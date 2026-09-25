"""Entity resolution: turn whatever string an adapter sent ('Main Street', 'Rescue Team 4',
'GasSensor-3', 'Building-14') into a graph entity id, creating a node when nothing matches."""
from __future__ import annotations

import hashlib
import re
from typing import Any, Optional

from ..contracts import Event
from ..graph import GraphStore

_LABEL_HINTS = [
    ("Building", r"\b(building|bldg|b\d+|apartment|tower|house)\b"),
    ("Road", r"\b(street|st|ave|avenue|road|rd|bridge|highway|hwy|blvd|boulevard|lane|route)\b"),
    ("Team", r"\b(team|rescue|ambulance|ambo|medic|engine|unit|squad|truck|crew|police)\b"),
    ("Sensor", r"\b(sensor|gauge|seismometer|detector|meter)\b"),
    ("Facility", r"\b(hospital|shelter|school|clinic|station|depot|eoc)\b"),
    ("Hazard", r"\b(hazard|leak|fire|flood|plume|zone)\b"),
]
_VALID_LABELS = {"Building", "Road", "Team", "Hazard", "Sensor", "Facility"}


class AmbiguousEntity(LookupError):
    def __init__(self, text: str, candidates: list[str]):
        super().__init__(f"'{text}' is ambiguous: {candidates}")
        self.text, self.candidates = text, candidates


_LEADING = re.compile(r"^\s*(?:the|a|an|our|this|that|unit|callsign)\s+", re.I)
_TRAILING = re.compile(
    r"\s*(?:\b(?:right\s+now|now|currently|at\s+the\s+moment|at\s+present|still|staged|please|today|again|exactly)\b"
    r"|\b(?:in\s+relation\s+to|relative\s+to|given|with|and|compared\s+to)\b.*|[,;&].*|[?!.]+)\s*$", re.I)


def clean_mention(text: str) -> str:
    """'the gas sensor right now?' -> 'gas sensor'. Strips leading articles and trailing filler/clauses."""
    t = text.strip()
    for _ in range(4):
        new = _TRAILING.sub("", _LEADING.sub("", t)).strip()
        if new == t:
            break
        t = new
    return t


def normalize(s: str) -> str:
    return re.sub(r"[\W_]+", "", s.lower())


def infer_label(text: str, details: dict[str, Any]) -> str:
    hinted = str(details.get("entity_type") or details.get("kind") or "").strip().capitalize()
    if hinted in _VALID_LABELS:
        return hinted
    low = text.lower()
    for label, rx in _LABEL_HINTS:
        if re.search(rx, low):
            return label
    return "Entity"


def make_id(label: str, text: str) -> str:
    if re.fullmatch(r"[A-Za-z]+-[A-Za-z0-9]+", text):  # already id-like, e.g. Building-14
        return text
    slug = re.sub(r"[\W_]+", "", text.title())
    if not slug:  # punctuation-only or otherwise unslugable name: stable hash instead of an empty id
        slug = hashlib.sha1(text.encode()).hexdigest()[:10]
    return f"{label}-{slug}" if label != "Entity" else f"Entity-{slug}"


class EntityResolver:
    def __init__(self, graph: GraphStore):
        self.g = graph

    def resolve(self, text: str) -> Optional[dict[str, Any]]:
        """Return the entity dict (id, kind, name, ...) or None. Raises AmbiguousEntity when several
        entities match equally well. Tries the text as given, then a cleaned mention."""
        found = self._resolve_once(text)
        if found is None:
            cleaned = clean_mention(text)
            if cleaned and cleaned != text:
                found = self._resolve_once(cleaned)
        return found

    def _resolve_once(self, text: str) -> Optional[dict[str, Any]]:
        hit = self.g.get_entity(text)
        if hit:
            return hit
        target = normalize(text)
        for cand in self.g.all_entity_names():
            names = [cand["id"], cand.get("name") or ""] + list(cand.get("aliases") or [])
            if any(normalize(n) == target for n in names if n):
                return self.g.get_entity(cand["id"])
        hits = [h for h in self.g.fulltext_phrase(text) if not ({"Event"} & set(h.get("labels") or []))]
        wants_hazard = bool(re.search(r"\b(hazard|leak|plume|zone)\b", text, re.I))
        hits = [h for h in hits if wants_hazard or "Hazard" not in (h.get("labels") or [])]
        if not hits:
            hits = [h for h in self.g.fulltext_terms(text) if wants_hazard or "Hazard" not in (h.get("labels") or [])]
        if not hits:
            return None
        if len(hits) > 1 and hits[1]["score"] >= 0.8 * hits[0]["score"]:  # no clear winner -> do not guess
            raise AmbiguousEntity(text, [h["id"] for h in hits])
        return self.g.get_entity(hits[0]["id"])  # phrase query: 'the bridge' -> Road-Bridge

    def resolve_or_create(self, ev: Event, text: Optional[str] = None) -> tuple[dict[str, Any], bool]:
        text = text or ev.entity
        found = self.resolve(text)
        if found:
            return found, False
        label = infer_label(text, ev.details)
        eid = make_id(label, text)
        created = self.g.create_entity(
            eid, label, name=text, ts=ev.timestamp, source=ev.source, confidence=ev.confidence,
            ref=ev.raw_evidence_ref, lat=ev.details.get("lat"), lon=ev.details.get("lon"),
        )
        return created, True
