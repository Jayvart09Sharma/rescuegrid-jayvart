"""Shared contracts: the inbound event payload (master-plan draft + optional fields) and the
outbound Q&A response (the handoff to Pranay's 3D twin)."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

Source = Literal["drone_vision", "radio_asr", "gps", "sensor", "field_report", "seed_dataset"]


class Event(BaseModel):
    """One raw perception from an upstream adapter. The six draft fields are required;
    event_id and details are optional extensions (see docs/EVENT_CONTRACT.md)."""

    source: str
    timestamp: datetime
    confidence: float = Field(ge=0.0, le=1.0)
    entity: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    raw_evidence_ref: str = Field(min_length=1)
    event_id: Optional[str] = None
    details: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}  # never drop fields a teammate adds later

    @field_validator("timestamp", mode="after")
    @classmethod
    def _ensure_tz(cls, v: datetime) -> datetime:
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)

    @field_validator("claim", "source", mode="before")
    @classmethod
    def _norm(cls, v: Any) -> Any:
        return re.sub(r"[\s\-]+", "_", v.strip().lower()) if isinstance(v, str) else v

    @field_validator("entity", mode="before")
    @classmethod
    def _strip(cls, v: Any) -> Any:
        return v.strip() if isinstance(v, str) else v

    @model_validator(mode="after")
    def _fill_id(self) -> "Event":
        if not self.event_id:
            raw = f"{self.source}|{self.timestamp.isoformat()}|{self.entity}|{self.claim}|{self.raw_evidence_ref}"
            self.event_id = "evt-" + hashlib.sha1(raw.encode()).hexdigest()[:16]
        return self

    @property
    def details_json(self) -> str:
        return json.dumps(self.details, sort_keys=True, default=str)


class Highlight(BaseModel):
    """What the 3D twin should fly to / highlight after an answer."""

    type: Literal["building", "road", "team", "hazard", "sensor", "facility", "event", "none"] = "none"
    id: Optional[str] = None
    ids: list[str] = Field(default_factory=list, description="additional entities to highlight (same type or mixed)")
    action: Literal["fly_to", "highlight", "none"] = "none"
    lat: Optional[float] = None
    lon: Optional[float] = None


class Provenance(BaseModel):
    source: str
    timestamp: Optional[datetime] = None
    confidence: Optional[float] = None
    raw_evidence_ref: Optional[str] = None


class Conflict(BaseModel):
    """Two sources disagree about one entity. Same shape in every response."""

    id: str
    name: Optional[str] = None
    kind: Optional[str] = None
    status: Optional[str] = None
    source: Optional[str] = None
    confidence: Optional[float] = None
    raw_evidence_ref: Optional[str] = None
    status_since: Optional[datetime] = None
    competing_claim: Optional[str] = None
    competing_source: Optional[str] = None
    competing_confidence: Optional[float] = None
    competing_evidence_ref: Optional[str] = None
    competing_since: Optional[datetime] = None


class QAResponse(BaseModel):
    """Output contract of the Q&A layer (v0.1). Backwards-compatible with the minimal shape
    {"answer": ..., "highlight": {"type": ..., "id": ...}} in Kenil's brief."""

    answer: str
    highlight: Highlight = Field(default_factory=Highlight)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    mode: Literal["llm", "fallback", "llm+fallback", "error"] = "fallback"
    intent: Optional[str] = None
    cypher: Optional[str] = None
    evidence: list[dict[str, Any]] = Field(default_factory=list, description="rows the answer was computed from")
    provenance: list[Provenance] = Field(default_factory=list, description="sources behind the highlighted facts")
    suggestion: Optional[str] = None
    requires_commander_approval: bool = False
    conflicts: list[Conflict] = Field(default_factory=list, description="conflicting reports on the highlighted entities")
    question: Optional[str] = None
    as_of: Optional[datetime] = None
    warnings: list[str] = Field(default_factory=list)
