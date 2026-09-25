"""Natural language -> Cypher with a hard read-only guard, one automatic repair round, and
execution inside a read transaction (so even a guard miss cannot write)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from neo4j.exceptions import Neo4jError

from ..config import Settings, settings as default_settings
from ..fusion.resolve import find_mentions
from ..graph import GraphStore, to_native
from .features import enabled
from .llm import BaseLLM, LLMError
from .schema_check import check_cypher
from .schema_prompt import SCHEMA_TEXT, build_system

_KW = r"(?<![.\w`])"  # keyword not preceded by '.', a word char or a backtick (so n.set / `set` are fine)
_FORBIDDEN = re.compile(
    _KW + r"(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|FOREACH|SHOW|TERMINATE|USE|ALTER|GRANT|DENY|REVOKE|START|STOP|ENABLE|DEALLOCATE|REALLOCATE|RENAME|INSERT|FINISH)\b"
    r"|\bLOAD\s+CSV\b|\bCALL\s*\{|\bdbms\.|\bdb\.create|\bapoc\.|\bPERIODIC\s+COMMIT\b",
    re.IGNORECASE,
)
_LEADING_OK = re.compile(r"^(?:MATCH|OPTIONAL\s+MATCH|WITH|UNWIND|RETURN|CALL\s+db\.index\.fulltext\.query)", re.IGNORECASE)
_CALL = re.compile(r"\bCALL\s+([A-Za-z_.]+)", re.IGNORECASE)
_ALLOWED_PROCEDURES = {"db.index.fulltext.querynodes", "db.index.fulltext.queryrelationships"}
# a literal bound to a fact-bearing column name = the model inventing a fact ('open' AS status)
_LITERAL_FACT = re.compile(
    r"(?:'[^']*'|\"[^\"]*\"|(?<![\w.])\d+(?:\.\d+)?|\btrue\b|\bfalse\b)\s+AS\s+`?(status|source|sources|confidence|claim|conflict\w*|raw_evidence_ref|evidence_refs?|applied|action|status_since|timestamp|last_confirmed|since|removed\w*|deleted\w*|updated\w*|created\w*)`?\b",
    re.IGNORECASE)
_WRITE_ALIAS = re.compile(r"\bAS\s+`?(?:removed|deleted|updated|created|modified|written|changed|merged)\w*", re.IGNORECASE)
_COMMENTS = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
_FENCE = re.compile(r"```(?:cypher)?\s*(.*?)```", re.S | re.I)


class UnsafeCypher(ValueError):
    pass


def extract_cypher(text: str) -> str:
    m = _FENCE.search(text)
    cy = (m.group(1) if m else text).strip()
    return cy.rstrip(";").strip()


def validate_cypher(cypher: str, default_limit: int = 25, max_limit: int = 100) -> str:
    """Reject anything that could mutate the graph; make sure the query returns rows and is bounded."""
    if not cypher:
        raise UnsafeCypher("empty query")
    cypher = _COMMENTS.sub(" ", cypher).strip()
    if ";" in cypher.rstrip(";"):
        raise UnsafeCypher("multiple statements are not allowed")
    if not _LEADING_OK.match(cypher):
        raise UnsafeCypher("query must start with MATCH, OPTIONAL MATCH, WITH, UNWIND, RETURN or a fulltext CALL")
    bad = _FORBIDDEN.search(cypher)
    if bad:
        raise UnsafeCypher(f"write/admin clause not allowed: {bad.group(0)}")
    for proc in _CALL.findall(cypher):
        if proc.lower() not in _ALLOWED_PROCEDURES:
            raise UnsafeCypher(f"procedure call not allowed: {proc}")
    if not re.search(r"\bRETURN\b", re.sub(r"'[^']*'|\"[^\"]*\"", "", cypher), re.I):
        raise UnsafeCypher("query has no RETURN clause")
    wa = _WRITE_ALIAS.search(cypher)
    if wa:
        raise UnsafeCypher(f"column alias implies a write ({wa.group(0)!r}); the graph is read-only")
    lit = _LITERAL_FACT.search(cypher)
    if lit:
        raise UnsafeCypher(f"a literal value is projected as a fact column ({lit.group(0)!r}); return real node/relationship properties")
    m = re.search(r"\bLIMIT\s+(\d+)\s*$", cypher, re.I)
    if not m:
        cypher = f"{cypher}\nLIMIT {default_limit}"
    elif int(m.group(1)) > max_limit:
        cypher = cypher[: m.start()] + f"LIMIT {max_limit}"
    return cypher


@dataclass
class Text2CypherResult:
    cypher: str
    rows: list[dict[str, Any]]
    attempts: list[dict[str, str]] = field(default_factory=list)
    error: str | None = None


class Text2Cypher:
    def __init__(self, llm: BaseLLM, graph: GraphStore, max_repairs: int = 2, cfg: Settings | None = None):
        self.llm, self.g, self.max_repairs = llm, graph, max_repairs
        self.cfg = cfg or default_settings
        self._known_ids: set[str] | None = None

    def known_ids(self) -> set[str]:
        if self._known_ids is None:
            self._known_ids = {r["id"] for r in self.g.all_entity_names()}
        return self._known_ids

    def _entity_preamble(self, question: str) -> tuple[str, dict[str, str]]:
        """entity_link: 'Gas Sensor 3' -> Sensor-Gas3 so the model filters on ids instead of spoken names."""
        ents = find_mentions(self.g, question)
        if not ents:
            return "", {}
        lines = [f"  \"{e['mention']}\" = {e['id']} ({e.get('kind')}, status {e.get('status')})" for e in ents[:6]]
        n2i = {}
        for e in ents:
            n2i[e["mention"].lower()] = e["id"]; n2i[(e.get("name") or "").lower()] = e["id"]
            for a in e.get("aliases") or []:
                n2i[a.lower()] = e["id"]
        return "Entities named in the question (use these ids):\n" + "\n".join(lines) + "\n", n2i

    def _prompt(self, question: str, now: datetime, prior_error: str | None = None, prior_cypher: str | None = None, preamble: str = "") -> str:
        p = f"Scenario clock $now = {now.isoformat()} (scenario date {now.date().isoformat()})\n{preamble}Q: {question}"
        if prior_error:
            p += f"\n\nYour previous query failed. Fix it and return only the corrected query.\nPrevious query:\n```cypher\n{prior_cypher}\n```\nError: {prior_error}"
        return p

    def run(self, question: str, now: datetime) -> Text2CypherResult:
        attempts: list[dict[str, str]] = []
        error, cypher = None, None
        zero_row_retry_done, prev_zero = False, None
        feats = self.cfg.qa_features
        system = build_system(feats, question)
        preamble, name_to_id = self._entity_preamble(question) if enabled(self.cfg, "entity_link") else ("", {})
        for attempt in range(self.max_repairs + 1):
            try:
                raw = self.llm.complete(system, self._prompt(question, now, error, cypher, preamble), max_tokens=320, temperature=0.0, thinking=False)  # deterministic and fast; 320 tokens ~ 8 lines of Cypher
            except LLMError as e:
                return Text2CypherResult(cypher or "", [], attempts, error=str(e))
            cypher = extract_cypher(raw)
            try:
                cypher = validate_cypher(cypher)
                if enabled(self.cfg, "schema_check"):
                    chk = check_cypher(cypher, self.known_ids(), name_to_id)
                    if not chk.ok:
                        error = "schema check: " + "; ".join(chk.problems[:4])
                        attempts.append({"cypher": cypher, "outcome": error})
                        continue
                rows = self.g.read_dicts(cypher, now=now)
                attempts.append({"cypher": cypher, "outcome": f"ok ({len(rows)} rows)"})
                if not rows and attempt < self.max_repairs and not zero_row_retry_done:
                    # zero rows is often a wrong property/label/direction, not a true negative: ask once more
                    zero_row_retry_done = True
                    error = ("the query ran but returned 0 rows. First REMOVE every WHERE condition the question did not ask for "
                             "(confidence, source, conflict, $now/time filters), then check each label, property name, property value and "
                             "relationship direction against the schema (e.g. (:Event)-[:ABOUT]->(:Entity), status values, numeric levels). "
                             "Return the shortest corrected query. If it is already minimal and correct, return it unchanged.")
                    prev_zero = cypher
                    continue
                if not rows and prev_zero is not None and cypher.strip() != prev_zero.strip():
                    pass  # second attempt also empty: accept as a genuine empty result
                return Text2CypherResult(cypher, [to_native(r) for r in rows], attempts)
            except UnsafeCypher as e:
                error = f"rejected by read-only guard: {e}"
            except Neo4jError as e:
                error = f"{e.code}: {e.message}"
            except Exception as e:  # driver/type errors
                error = f"{type(e).__name__}: {e}"
            attempts.append({"cypher": cypher, "outcome": error})
        if zero_row_retry_done and attempts and attempts[-1]["outcome"].startswith("ok"):
            return Text2CypherResult(cypher or "", [], attempts)
        return Text2CypherResult(cypher or "", [], attempts, error=error)
