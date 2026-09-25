"""QAEngine: question in -> QAResponse out. Modes: 'auto' (pre-baked intent if one matches, else
LLM text->Cypher), 'llm' (force the model path), 'fallback' (never touch the model).
Guarantees regardless of mode: read-only (write/dispatch requests are refused before any model
call), answers grounded in graph rows (the LLM draft is fact-checked against them), conflicts on
anything highlighted are surfaced, and reroutes/tasking are suggestions needing commander approval."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, Field

from ..config import Settings, settings as default_settings
from ..contracts import Conflict, Highlight, Provenance, QAResponse
from ..fusion.resolve import AmbiguousEntity, EntityResolver
from ..graph import GraphStore, to_native
from .fallback import KIND_TO_TYPE, Fallback
from .llm import BaseLLM, LLMError, make_llm
from .schema_prompt import ANSWER_SYSTEM
from .text2cypher import Text2Cypher

Mode = Literal["auto", "llm", "fallback"]
MODES = ("auto", "llm", "fallback")
SOURCES = ("drone_vision", "radio_asr", "gps", "sensor", "field_report", "seed_dataset")

_MUTATION = re.compile(
    r"^\s*(?:please\s+|now\s+|go\s+ahead\s+and\s+)?(?:set|update|change|mark|delete|remove|create|add|insert|merge|drop|clear|reset|wipe|erase|"
    r"dispatch|send|assign|reassign|task|move|reroute|redirect|order|deploy|evacuate|close|open)\b"
    r"|\bignore\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|earlier)?\s*(?:rules|instructions|prompts?)\b"
    r"|\b(?:write|admin|developer|god)\s+mode\b|\bsystem\s+override\b|\bdetach\s+delete\b|\bdrop\s+(?:the\s+)?(?:database|graph|index)\b",
    re.IGNORECASE)
_DISPATCH = re.compile(r"\b(dispatch|send|assign|reassign|task|move|reroute|redirect|deploy|evacuate|order)\b", re.IGNORECASE)


# Hard length limits are part of the JSON schema, so the grammar-constrained decoder (xgrammar in vLLM) must close every
# string in time: the model cannot run away inside `answer` until max_tokens and return unterminated JSON. Budget:
# 600 + 240 chars of text (~210 tokens) + 6 ids (~50) + keys fit inside COMPOSE_MAX_TOKENS.
ANSWER_MAX_CHARS, SUGGESTION_MAX_CHARS, ID_MAX_CHARS, COMPOSE_MAX_TOKENS = 600, 240, 64, 320


class AnswerDraft(BaseModel):
    answer: str = Field(max_length=ANSWER_MAX_CHARS, description="plain-language answer for the watch officer, grounded only in the rows")
    highlight_type: Literal["building", "road", "team", "hazard", "sensor", "facility", "event", "none"] = "none"
    highlight_id: Optional[str] = Field(default=None, max_length=ID_MAX_CHARS, description="entity id from the rows to fly the camera to")
    highlight_ids: list[Annotated[str, Field(max_length=ID_MAX_CHARS)]] = Field(default_factory=list, max_length=5, description="other entity ids from the rows to highlight")
    confidence: float = Field(ge=0.0, le=1.0, default=0.6)
    suggestion: Optional[str] = Field(default=None, max_length=SUGGESTION_MAX_CHARS, description="one-sentence suggested reroute/tasking, only if the rows support it")


def _json_default(v):
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


def _walk_records(v: Any, out: list[dict]) -> list[dict]:
    """Every dict (row or nested) in the rows; used for provenance and fact-checking."""
    if isinstance(v, dict):
        if v:
            out.append(v)
        for x in v.values():
            _walk_records(x, out)
    elif isinstance(v, list):
        for x in v:
            _walk_records(x, out)
    return out


def _times(rec: dict) -> set[str]:
    out = set()
    for val in rec.values():
        val = to_native(val)
        if hasattr(val, "strftime"):
            out.add(val.strftime("%H:%M:%S"))
        elif isinstance(val, str):
            out.update(re.findall(r"\d{4}-\d{2}-\d{2}[T ](\d{2}:\d{2}:\d{2})", val))
    return out


def _confs(rec: dict) -> set[float]:
    return {round(float(v), 2) for k, v in rec.items() if "confidence" in k and isinstance(v, (int, float))}


def _srcs(rec: dict) -> set[str]:
    out = set()
    for k, v in rec.items():
        if "source" not in k:
            continue
        if isinstance(v, str):
            out.add(v)
        elif isinstance(v, list):
            out.update(x for x in v if isinstance(x, str))
    return out


def fact_check(answer: str, rows: list[dict]) -> list[str]:
    """Return problems where the answer states a source/time/confidence that no single row supports."""
    recs = _walk_records(rows, [])
    all_times = set().union(*(_times(r) for r in recs)) if recs else set()
    all_confs = set().union(*(_confs(r) for r in recs)) if recs else set()
    any_source = any(_srcs(r) for r in recs)
    problems = []
    for seg in re.split(r"(?<=[.;])\s+|\n|\s+and\s+|,\s+while\s+|\s+but\s+", answer):
        srcs = [s for s in SOURCES if s in seg]
        times = re.findall(r"\b(\d{2}:\d{2}:\d{2})Z?\b", seg)
        confs = [round(float(c), 2) for c in re.findall(r"(?<![\d.])(0\.\d{1,3}|1\.00?)(?![\d])", seg)]
        for t in times:
            if t not in all_times:
                problems.append(f"time {t} is not in the rows")
        for c in confs:
            if c not in all_confs:
                problems.append(f"confidence {c:.2f} is not in the rows")
        if len(srcs) == 1 and (times or confs) and any_source:
            s = srcs[0]
            if not any(s in _srcs(r) and all(t in _times(r) for t in times) and all(c in _confs(r) for c in confs) for r in recs):
                problems.append(f"no row has {s} with {', '.join(times + [f'{c:.2f}' for c in confs])}")
        if (times or confs) and not recs:
            problems.append("answer quotes times/confidences but the rows carry none")
    return list(dict.fromkeys(problems))


class QAEngine:
    def __init__(self, graph: GraphStore, llm: Optional[BaseLLM] = None, cfg: Optional[Settings] = None, auto_llm: bool = True):
        self.g = graph
        self.cfg = cfg or default_settings
        self.llm = llm if llm is not None else (make_llm(self.cfg) if auto_llm else None)
        self.fallback = Fallback(graph, self.cfg)
        self.resolver = EntityResolver(graph)
        self.t2c = Text2Cypher(self.llm, graph, cfg=self.cfg) if self.llm else None

    def now(self) -> datetime:
        if self.cfg.clock == "wall":
            return datetime.now(timezone.utc)
        return self.g.replay_now() or datetime.now(timezone.utc)

    def answer(self, question: str, mode: Mode = "auto") -> QAResponse:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question must be a non-empty string")
        question = question.strip()
        now = self.now()
        if _MUTATION.search(question):
            return self._finish(self._read_only(question), question, now)
        if mode in ("auto", "fallback"):
            resp = self.fallback.answer(question, now)
            if resp and (mode == "fallback" or resp.confidence >= 0.6 or not self.t2c):
                return self._finish(resp, question, now)
            if mode == "fallback":
                return self._finish(QAResponse(answer="No pre-baked query matches that question. Try one of: " + "; ".join(self.fallback.examples()),
                                               mode="fallback", confidence=0.0), question, now)
            if resp:  # weak pre-baked answer (entity not found, ambiguous): let the model try, keep the pre-baked one if it fails
                llm_resp = self._llm_answer(question, now, allow_fallback=False)
                if llm_resp.mode == "llm" and llm_resp.evidence:
                    llm_resp.warnings.append(f"pre-baked '{resp.intent}' could not answer ({resp.answer}); answered by the LLM path")
                    return self._finish(llm_resp, question, now)
                return self._finish(resp, question, now)
        if not self.t2c:
            return self._finish(QAResponse(answer="No LLM is configured (LLM_PROVIDER=none) and no pre-baked query matches.", mode="error", confidence=0.0), question, now)
        return self._finish(self._llm_answer(question, now), question, now)

    @staticmethod
    def _read_only(question: str) -> QAResponse:
        if _DISPATCH.search(question):
            msg = ("RescueGrid never dispatches or tasks units - the commander does. I can show where units are, what they are near "
                   "and which routes are open, so the IC can decide. No changes were made.")
        else:
            msg = "RescueGrid is read-only: I answer questions about the live graph but never change it. No changes were made."
        return QAResponse(answer=msg, mode="fallback", intent="read_only_refusal", confidence=1.0)

    # ------------------------------------------------------------------ LLM path
    def _llm_answer(self, question: str, now: datetime, allow_fallback: bool = True) -> QAResponse:
        t2c = self.t2c.run(question, now)
        attempts_warn = [a["outcome"] for a in t2c.attempts if not a["outcome"].startswith("ok")]
        if t2c.error and not t2c.rows:
            fb = self.fallback.answer(question, now) if allow_fallback else None
            if fb and not self.fallback.covers(question, fb.intent):
                fb = None
            if fb:
                fb.mode = "llm+fallback"; fb.warnings.append("LLM query failed; answered from the pre-baked query")
                return fb
            return QAResponse(answer="I could not build a valid graph query for that question. Try rephrasing, or ask one of the standard questions.",
                              mode="error", cypher=t2c.cypher, confidence=0.0, warnings=attempts_warn[-2:])
        all_zero = bool(t2c.rows) and all(all(v in (0, None, [], "") for v in r.values()) for r in t2c.rows)
        if not t2c.rows or all_zero:
            fb = self.fallback.answer(question, now) if allow_fallback else None
            if fb and not self.fallback.covers(question, fb.intent):
                fb = None  # e.g. "what did RADIO report" must not be answered by an unfiltered pre-baked query
            if fb and fb.confidence >= 0.6:
                fb.mode = "llm+fallback"; fb.warnings.append("LLM query returned no rows; answered from the pre-baked query")
                return fb
            return QAResponse(answer="The graph query found no matching records. That is not proof that none exist - the question may use terms the graph does not track.",
                              mode="llm", cypher=t2c.cypher, evidence=t2c.rows, confidence=0.3,
                              warnings=["query returned " + ("only zero counts" if all_zero else "0 rows") + "; not evidence of absence"] + attempts_warn)
        rows_json = json.dumps(t2c.rows[:25], default=_json_default)[:12000]
        user = f"Question: {question}\nScenario clock: {now.isoformat()}\nCypher run:\n{t2c.cypher}\nRows ({len(t2c.rows)}):\n{rows_json}"
        try:
            draft = self._compose(user)
            problems = fact_check(draft.answer, t2c.rows)
            if problems:  # one regeneration with the mismatches spelled out
                retry = user + "\n\nYour previous answer stated facts not in the rows: " + "; ".join(problems) + ". Rewrite it using only values present in the rows."
                draft2 = self._compose(retry)
                problems2 = fact_check(draft2.answer, t2c.rows)
                if len(problems2) < len(problems):
                    draft, problems = draft2, problems2
        except LLMError as e:  # model could not write the answer: list the rows deterministically instead
            ids = self._row_entity_ids(t2c.rows)[:6]
            kind = (self.g.get_entity(ids[0]) or {}).get("kind") if ids else None
            return QAResponse(answer=self._rows_summary(t2c.rows), mode="llm", cypher=t2c.cypher, evidence=t2c.rows, confidence=0.5,
                              highlight=Highlight(type=KIND_TO_TYPE.get(kind or "", "none"), id=ids[0] if ids else None, ids=ids[1:],
                                                  action="fly_to" if ids else "none"),
                              provenance=self._provenance(t2c.rows), warnings=["answer composed from rows without the model: " + str(e)[:200]])
        known = self._known_ids(t2c.rows)
        cand = [self._to_id(draft.highlight_id, known)] + [self._to_id(i, known) for i in draft.highlight_ids]
        cand = [c for c in cand if c]
        if not cand:
            cand = [i for i in self._row_entity_ids(t2c.rows)][:6]
        hid = cand[0] if cand else None
        extra = [c for c in dict.fromkeys(cand[1:]) if c != hid][:5]
        kind = (self.g.get_entity(hid) or {}).get("kind") if hid else None
        hl = Highlight(type=KIND_TO_TYPE.get(kind or "", "none"), id=hid, ids=extra, action="fly_to" if hid else "none")
        suggestion = draft.suggestion.strip() if draft.suggestion and draft.suggestion.strip() else None
        if suggestion and "commander approval" not in suggestion.lower():
            suggestion = suggestion.rstrip(".") + " - suggested, commander approval required."
        conf = min(draft.confidence, 0.4) if problems else draft.confidence
        return QAResponse(answer=draft.answer, highlight=hl, confidence=conf, mode="llm", cypher=t2c.cypher, evidence=t2c.rows,
                          provenance=self._provenance(t2c.rows), suggestion=suggestion, requires_commander_approval=bool(suggestion),
                          warnings=attempts_warn + [f"answer fact-check: {p}" for p in problems])

    def _compose(self, user: str) -> AnswerDraft:
        try:
            return self.llm.complete_json(ANSWER_SYSTEM, user, AnswerDraft, max_tokens=COMPOSE_MAX_TOKENS, thinking=self.cfg.llm_thinking)
        except LLMError:  # one retry (bounded: at most ~2 x 320 tokens); after that the rows are listed without the model
            return self.llm.complete_json(ANSWER_SYSTEM, user + "\n\nKeep `answer` under 60 words.", AnswerDraft, max_tokens=COMPOSE_MAX_TOKENS, thinking=self.cfg.llm_thinking)

    @staticmethod
    def _rows_summary(rows: list[dict]) -> str:
        """Deterministic, model-free rendering of result rows (used when composition fails)."""
        keys = ("name", "id", "claim", "status", "source", "confidence", "timestamp", "status_since", "distance_m", "reading", "people", "count")
        parts = []
        for r in rows[:8]:
            bits = []
            for k in keys:
                v = to_native(r.get(k))
                if v is None or (k == "id" and r.get("name")):
                    continue
                bits.append(v.strftime("%H:%M:%SZ") if hasattr(v, "strftime") else (f"{v:.2f}" if isinstance(v, float) and k == "confidence" else str(v)))
            if bits:
                parts.append(" ".join(bits))
        more = f" (+{len(rows) - 8} more)" if len(rows) > 8 else ""
        return f"{len(rows)} matching record(s): " + "; ".join(parts) + more + "."

    # ------------------------------------------------------------------ helpers
    def _known_ids(self, rows: list[dict]) -> set[str]:
        out: set[str] = set()
        def walk(v):
            if isinstance(v, dict):
                for x in v.values(): walk(x)
            elif isinstance(v, list):
                for x in v: walk(x)
            elif isinstance(v, str) and 2 < len(v) < 64:
                out.add(v)
        walk(rows)
        return out

    def _row_entity_ids(self, rows: list[dict]) -> list[str]:
        ids = [v for v in self._known_ids(rows) if re.fullmatch(r"[A-Z][A-Za-z]+-[\w-]+", v) and not v.startswith("evt-")]
        order = {v: i for i, v in enumerate(json.dumps(rows, default=_json_default).split('"'))}
        return [i for i in sorted(ids, key=lambda x: order.get(x, 1 << 30)) if self.g.get_entity(i)]

    def _to_id(self, text: Optional[str], known: set[str]) -> Optional[str]:
        """Map whatever the model wrote (an id, a name, an alias) to an entity id that the rows actually contain."""
        if not text:
            return None
        try:
            ent = self.g.get_entity(text) or self.resolver.resolve(text)
        except AmbiguousEntity:
            return None
        return ent["id"] if ent and ent["id"] in known else None

    @staticmethod
    def _provenance(rows: list[dict]) -> list[Provenance]:
        out, seen = [], set()
        for r in _walk_records(rows, []):
            src = r.get("source") or r.get("e_source") or (r.get("sources") or [None])[0]
            ts = r.get("status_since") or r.get("timestamp") or r.get("since")
            ref = r.get("raw_evidence_ref") or (r.get("evidence_refs") or [None])[-1]
            if not isinstance(src, str) or (ts is None and ref is None):
                continue
            key = (src, str(ts), ref)
            if key in seen:
                continue
            seen.add(key)
            out.append(Provenance(source=src, timestamp=to_native(ts) if hasattr(to_native(ts), "tzinfo") else None,
                                  confidence=r.get("confidence") if isinstance(r.get("confidence"), (int, float)) else None, raw_evidence_ref=ref))
        return out[:20]

    CONFLICTS_FOR = ("MATCH (n:Entity) WHERE n.id IN $ids AND n.conflict = true RETURN n.id AS id, n.name AS name, n.kind AS kind, n.status AS status, "
                     "n.source AS source, n.confidence AS confidence, n.raw_evidence_ref AS raw_evidence_ref, n.status_since AS status_since, "
                     "n.conflict_claim AS competing_claim, n.conflict_source AS competing_source, n.conflict_confidence AS competing_confidence, "
                     "n.conflict_evidence_ref AS competing_evidence_ref, n.conflict_since AS competing_since")

    # ------------------------------------------------------------------ common post-processing
    def _finish(self, resp: QAResponse, question: str, now: datetime) -> QAResponse:
        resp.question, resp.as_of = question, now
        ids = [i for i in [resp.highlight.id] + resp.highlight.ids if i]
        resp.conflicts = [Conflict(**to_native(r)) for r in self.g.read_dicts(self.CONFLICTS_FOR, ids=ids)] if ids else []
        if resp.conflicts and not any("conflicting reports" in w for w in resp.warnings):
            resp.warnings.append("highlighted entity has conflicting reports: " + "; ".join(
                f"{c.name} {c.status} ({c.source}) vs {c.competing_claim} ({c.competing_source})" for c in resp.conflicts))
        if resp.suggestion and not resp.requires_commander_approval:
            resp.requires_commander_approval = True
        if resp.highlight.id and resp.highlight.lat is None:
            pos = self.g.read_dicts("MATCH (n:Entity {id:$id}) RETURN n.lat AS lat, n.lon AS lon, n.kind AS kind", id=resp.highlight.id)
            if pos:
                resp.highlight.lat, resp.highlight.lon = pos[0]["lat"], pos[0]["lon"]
                if resp.highlight.type == "none":
                    resp.highlight.type = KIND_TO_TYPE.get(pos[0]["kind"] or "", "none")
        return resp
