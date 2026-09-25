"""The fusion / ingestion agent: one Event in -> timestamped, provenance-tagged graph writes out.
Pipeline per event (inside ONE transaction): dedupe -> resolve entity -> temporal/status policy ->
correlation rules -> audit Event node. Anything that fails rolls back and is recorded as an
audit-only Event with applied=false, so the stream never stops and the graph never half-applies."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from pydantic import ValidationError

from ..config import Settings, settings as default_settings
from ..contracts import Event
from ..graph import GraphStore, to_native
from .policy import Decision, decide
from .resolve import AmbiguousEntity, EntityResolver
from .rules import DEFAULT_RULES, Context, Rule

NON_STATUS_CLAIMS = {"position_update", "near"}


@dataclass
class IngestResult:
    event_id: str
    entity_id: Optional[str]
    action: str
    applied: bool
    created_entity: bool = False
    notes: list[str] = field(default_factory=list)
    relationships: list[str] = field(default_factory=list)
    error: Optional[str] = None

    def summary(self) -> str:
        head = f"[{self.event_id}] -> {self.entity_id or '?'} | {self.action}"
        if self.error:
            return f"{head} | ERROR {self.error}"
        parts = [head] + self.notes + [f"rel: {r}" for r in self.relationships]
        return " | ".join(parts)


class FusionAgent:
    def __init__(self, graph: GraphStore, cfg: Settings | None = None, rules: Optional[list[Rule]] = None):
        self.g = graph
        self.cfg = cfg or default_settings
        self.rules = rules if rules is not None else list(DEFAULT_RULES)
        self.resolver = EntityResolver(graph)

    # ---------------------------------------------------------------- public
    def handle(self, event: Event | dict[str, Any]) -> IngestResult:
        try:
            ev = event if isinstance(event, Event) else Event.model_validate(event)
        except ValidationError as e:
            eid = str(event.get("event_id") or "<no id>") if isinstance(event, dict) else "<invalid>"
            return IngestResult(eid, None, "invalid", False, error=f"payload rejected: {e.errors()[0]['loc']} {e.errors()[0]['msg']}")
        if self.g.event_exists(ev.event_id):
            return IngestResult(ev.event_id, None, "duplicate", False, notes=["event id already ingested - skipped"])
        try:
            with self.g.transaction():
                return self._handle(ev)
        except AmbiguousEntity as e:
            note = f"ambiguous entity {e.text!r}: candidates {e.candidates} - recorded, not applied"
            self._record_only(ev, None, "ambiguous", note)
            return IngestResult(ev.event_id, None, "ambiguous", False, notes=[note])
        except Exception as e:  # rolled back above; never let one bad event stop the stream
            err = f"{type(e).__name__}: {e}"
            self._record_only(ev, None, "error", err)
            return IngestResult(ev.event_id, None, "error", False, error=err)

    def run(self, source: Iterable[Event], on_result: Optional[Callable[[IngestResult], None]] = None) -> list[IngestResult]:
        results = []
        for ev in source:
            r = self.handle(ev)
            results.append(r)
            if on_result:
                on_result(r)
        return results

    # ---------------------------------------------------------------- internals
    def _record_only(self, ev: Event, entity_id: Optional[str], action: str, note: str) -> None:
        try:
            self.g.record_event(ev, entity_id, False, note[:500], action)
        except Exception:
            pass  # the audit write itself failed; the IngestResult still carries the error

    def _handle(self, ev: Event) -> IngestResult:
        entity, created = self.resolver.resolve_or_create(ev)
        ctx = Context(self.g, self.cfg, self.resolver, ev, entity, created)
        if created:
            ctx.note(f"unknown entity '{ev.entity}' -> created {entity['id']} ({entity['kind']})")
        elif entity["id"] != ev.entity:
            ctx.note(f"'{ev.entity}' resolved to {entity['id']}")

        if ev.claim in NON_STATUS_CLAIMS:
            decision = self._positional_decision(entity, ev)
        else:
            decision = decide(entity, ev, conflict_window_s=self.cfg.conflict_window_s, confidence_margin=self.cfg.conflict_confidence_margin)
            self._apply_decision(entity, ev, decision)
        ctx.note(decision.note)

        if decision.applied and decision.adopted:
            for rule in self.rules:
                if rule.matches(ctx):
                    rule.apply(ctx)

        self.g.record_event(ev, entity["id"], decision.applied, decision.note, decision.action)
        if decision.action == "conflict":  # both Event nodes exist now -> link them
            self.g.link_conflict(ev.event_id, entity.get("status_event_id"))
        return IngestResult(ev.event_id, entity["id"], decision.action, decision.applied, created, ctx.notes, ctx.relationships)

    @staticmethod
    def _positional_decision(entity: dict[str, Any], ev: Event) -> Decision:
        """Positional claims never touch status, but they are still time-gated against the unit's
        newest known position so a late GPS fix or vision frame cannot rewind it."""
        ps = to_native(entity.get("position_since"))
        if ps is not None and ev.timestamp < ps and entity.get("position_source") != "seed_dataset":
            return Decision("stale", f"positional event {ev.timestamp.isoformat()} older than position_since {ps.isoformat()}", applied=False)
        return Decision(ev.claim, "positional claim - status untouched")

    def _apply_decision(self, entity: dict[str, Any], ev: Event, d: Decision) -> None:
        g, eid = self.g, entity["id"]
        if d.action in ("override", "create"):
            g.override_status(eid, ev)
        elif d.action == "confirm":
            g.confirm_status(eid, ev)
        elif d.action == "resolve_conflict":
            g.confirm_status(eid, ev)
            g.clear_conflict(eid, ev.timestamp, d.note)
        elif d.action == "confirm_competing":
            prior_sources = [s for s in (entity.get("conflict_source"),) if s]
            g.override_status(eid, ev)
            g.set_props(eid, {"confirmed_sources": prior_sources + [ev.source], "confirmations": 1, "conflict_resolution": d.note})
        elif d.action == "conflict":
            if ev.confidence > float(entity.get("confidence") or 0.0):
                # the new report is more credible: it becomes the status, the old one is kept as the competing claim
                old = entity
                g.override_status(eid, ev)
                g.set_conflict(eid, claim=old["status"], source=old["source"], confidence=float(old.get("confidence") or 0.0),
                               ref=old.get("raw_evidence_ref") or "", since=old.get("last_confirmed") or old["status_since"], event_id=old.get("status_event_id"))
                g.set_props(eid, {"conflict_note": f"status follows the higher-confidence report ({ev.source} {ev.confidence:.2f}); {old['source']} reported '{old['status']}' ({float(old.get('confidence') or 0):.2f})"})
                d.adopted = True
            else:
                g.set_conflict(eid, claim=ev.claim, source=ev.source, confidence=ev.confidence, ref=ev.raw_evidence_ref,
                               since=ev.timestamp, event_id=ev.event_id)
                g.set_props(eid, {"conflict_note": f"status keeps the higher-confidence report ({entity['source']} {float(entity.get('confidence') or 0):.2f}); {ev.source} reported '{ev.claim}' ({ev.confidence:.2f})"})
                d.adopted = False  # the losing claim must not drive correlation rules (assignments etc.)
        # 'stale' -> nothing to change; the Event node records it with applied=false
