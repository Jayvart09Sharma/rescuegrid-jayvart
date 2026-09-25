"""Deterministic, template-based rendering of query rows into prose (template NLG, Reiter & Dale style).
Used INSTEAD of the answer-composition LLM call when the rows have a shape the templates know how to
say; otherwise the caller falls back to the model. Every value in the output is copied from the rows, so
the grounding fact-check passes by construction. Flag: QA_FEATURES=det_render."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

from ..graph import to_native

_ENTITY_KEYS = ("id", "name", "status", "status_since", "last_confirmed", "source", "confidence", "raw_evidence_ref", "conflict",
                "conflict_claim", "conflict_source", "conflict_confidence", "conflict_since", "kind", "occupancy_est", "reading", "unit",
                "threshold", "level", "beds_available", "capacity", "lat", "lon", "position_since", "unit_type", "facility_type", "hazard_type", "sensor_type")
_EVENT_KEYS = ("timestamp", "claim", "source", "confidence", "raw_evidence_ref", "applied", "action", "id", "name", "entity_id", "event_id", "note")
_AGG_KEYS = {"count", "n", "total", "sum", "people", "occupants", "capacity", "beds", "avg", "average", "number"}


def hhmm(v: Any) -> Optional[str]:
    v = to_native(v)
    if hasattr(v, "strftime"):
        return v.strftime("%H:%M:%SZ")
    if isinstance(v, str):
        m = re.search(r"\d{4}-\d{2}-\d{2}[T ](\d{2}:\d{2}:\d{2})", v)
        return m.group(1) + "Z" if m else None
    return None


def _conf(v: Any) -> str:
    return f"{v:.2f}" if isinstance(v, (int, float)) else ""


def _label(r: dict) -> str:
    return str(r.get("name") or r.get("id") or "it")


def _prov(r: dict) -> str:
    bits = [b for b in [str(r["source"]) if r.get("source") else "", _conf(r.get("confidence"))] if b]
    return f" ({', '.join(bits)})" if bits else ""


def _status_sentence(r: dict) -> str:
    s = f"{_label(r)} is {str(r.get('status', '')).replace('_', ' ')}"
    since = hhmm(r.get("status_since"))
    if since:
        s += f" since {since}"
    s += _prov(r)
    extras = []
    if r.get("occupancy_est") is not None:
        extras.append(f"est. {r['occupancy_est']} occupants")
    if r.get("reading") is not None:
        extras.append(f"reading {r['reading']} {r.get('unit') or ''}".strip())
    if r.get("level") is not None and r.get("reading") is None:
        extras.append(f"level {r['level']} {r.get('unit') or ''}".strip())
    if r.get("beds_available") is not None:
        extras.append(f"{r['beds_available']} beds available")
    if r.get("capacity") is not None and r.get("beds_available") is None:
        extras.append(f"capacity {r['capacity']}")
    if extras:
        s += ", " + ", ".join(extras)
    if r.get("conflict"):
        s += f". CONFLICTING: {r.get('conflict_source')} reports {r.get('conflict_claim')}" + (f" ({_conf(r.get('conflict_confidence'))})" if r.get("conflict_confidence") is not None else "")
    return s + "."


def _event_sentence(r: dict) -> str:
    t = hhmm(r.get("timestamp")) or "?"
    who = str(r.get("name") or r.get("entity_id") or r.get("id") or "")
    claim = str(r.get("claim") or "").replace("_", " ")
    src = str(r.get("source") or "")
    return f"{t} {who} {claim} ({src} {_conf(r.get('confidence'))})".replace("( ", "(").replace(" )", ")")


def shape_of(rows: list[dict]) -> Optional[str]:
    """'entity_status' | 'event_list' | 'aggregate' | None (unknown -> let the model write)."""
    if not rows or not all(isinstance(r, dict) for r in rows):
        return None
    keys = set().union(*(set(r) for r in rows))
    if len(rows) == 1 and len(keys) <= 3 and any(k.lower() in _AGG_KEYS for k in keys) and all(isinstance(to_native(v), (int, float)) or v is None or k == "id" for r in rows for k, v in r.items()):
        return "aggregate"
    if "timestamp" in keys and "claim" in keys and keys <= set(_EVENT_KEYS):
        return "event_list"
    if "status" in keys and ("id" in keys or "name" in keys) and keys <= set(_ENTITY_KEYS) and not any(isinstance(v, (list, dict)) for r in rows for v in r.values()):
        return "entity_status"
    return None


def render(question: str, rows: list[dict], max_items: int = 8) -> Optional[str]:
    shape = shape_of(rows)
    if shape is None:
        return None
    if shape == "aggregate":
        r = rows[0]
        k, v = next((k, to_native(v)) for k, v in r.items() if k != "id")
        label = k.replace("_", " ")
        return f"{label.capitalize()}: {v}." if v is not None else f"No {label} is recorded."
    if shape == "entity_status":
        sents = [_status_sentence(r) for r in rows[:max_items]]
        more = f" (+{len(rows) - max_items} more)" if len(rows) > max_items else ""
        return " ".join(sents) + more
    if shape == "event_list":
        items = [_event_sentence(r) for r in rows[:max_items]]
        more = f" (+{len(rows) - max_items} more)" if len(rows) > max_items else ""
        return f"{len(rows)} event(s): " + "; ".join(items) + "." + more
    return None
