"""Pure decision logic for applying a claim to an entity's current status. No I/O, fully
unit-testable. See docs/DATA_MODEL.md 'Temporal semantics'."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from ..contracts import Event

Action = str  # 'create' | 'override' | 'confirm' | 'stale' | 'conflict' | 'confirm_competing' | 'resolve_conflict'

# A unit's status moves along a progression during a response. Two sources reporting successive steps of it
# (radio says en_route, then the crew's report says on_scene) are not disagreeing about the same fact, so these
# transitions never open a conflict: the later step overrides. Backward steps (on_scene -> en_route) and
# out_of_service are still judged by the normal conflict rule. Added 2026-09-25 (Shresth, agreed with Kenil's design).
TEAM_PROGRESSION = {("available", "en_route"), ("en_route", "on_scene"), ("available", "on_scene"), ("on_scene", "available"), ("en_route", "available")}


@dataclass
class Decision:
    action: Action
    note: str = ""
    applied: bool = True   # did the event change (or confirm) state at all
    adopted: bool = True   # did the event's own claim become the entity's status (False for the losing side of a conflict)


def _dt(v: Any) -> Optional[datetime]:
    if v is None:
        return None
    return v.to_native() if hasattr(v, "to_native") else v


def decide(current: Optional[dict[str, Any]], ev: Event, *, conflict_window_s: float, confidence_margin: float) -> Decision:
    if current is None:
        return Decision("create", "entity unknown - created from event")
    status = current.get("status")
    since = _dt(current.get("status_since"))
    last_seen = _dt(current.get("last_confirmed")) or since   # freshest evidence for the current status
    cur_src = current.get("source")
    cur_conf = float(current.get("confidence") or 0.0)
    in_conflict = bool(current.get("conflict"))
    competing = current.get("conflict_claim")
    conflict_src = current.get("conflict_source")
    placeholder = status in (None, "", "unknown") or cur_src == "seed_dataset"   # a prior, not evidence

    # ---- same claim -> confirmation (and possibly resolution of an open conflict)
    if status == ev.claim:
        if in_conflict and ev.source == conflict_src:
            return Decision("resolve_conflict", f"dissenting source {ev.source} now agrees with '{ev.claim}'")
        if in_conflict and ev.source not in (cur_src, conflict_src):
            return Decision("resolve_conflict", f"third source {ev.source} confirms '{ev.claim}'")
        if last_seen is not None and ev.timestamp < last_seen and not placeholder:
            return Decision("stale", f"confirmation at {ev.timestamp.isoformat()} is older than the latest evidence ({last_seen.isoformat()})", applied=False)
        return Decision("confirm", f"{ev.source} re-confirms '{ev.claim}'")

    # ---- out-of-order: older than the freshest evidence we already hold
    if last_seen is not None and ev.timestamp < last_seen and not placeholder:
        return Decision("stale", f"event {ev.timestamp.isoformat()} older than latest evidence {last_seen.isoformat()}", applied=False)

    # ---- placeholders and the static seed never generate conflicts
    if placeholder:
        return Decision("override", f"'{status}' -> '{ev.claim}' by {ev.source}")

    # ---- a new source agreeing with the competing side of an open conflict -> flip
    if in_conflict and competing == ev.claim and ev.source not in (cur_src, conflict_src):
        return Decision("confirm_competing", f"third source {ev.source} confirms competing claim '{ev.claim}' - status flips")

    # ---- a unit reporting the next step of its response is an update, not a contradiction
    if current.get("kind") == "team" and (status, ev.claim) in TEAM_PROGRESSION:
        return Decision("override", f"unit progression '{status}' -> '{ev.claim}' by {ev.source}")

    # ---- contradiction: someone other than this source stands behind the current status, recently, with comparable confidence
    backers = {cur_src, *(current.get("confirmed_sources") or [])} - {ev.source, None}
    if backers and last_seen is not None:
        age = (ev.timestamp - last_seen).total_seconds()
        if 0 <= age <= conflict_window_s and abs(ev.confidence - cur_conf) <= confidence_margin:
            who = cur_src if cur_src != ev.source else ", ".join(sorted(backers))
            return Decision("conflict", f"{ev.source} says '{ev.claim}' ({ev.confidence:.2f}) vs {who} '{status}' ({cur_conf:.2f}) reported {age:.0f}s earlier")

    return Decision("override", f"'{status}' -> '{ev.claim}' by {ev.source}")
