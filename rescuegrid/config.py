"""Environment-driven settings. Everything defaults to Kenil's local instances; point the env
vars at the shared Neo4j / shared LLM later and nothing else changes."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    neo4j_uri: str = field(default_factory=lambda: _env("NEO4J_URI", "bolt://127.0.0.1:7687"))
    neo4j_user: str = field(default_factory=lambda: _env("NEO4J_USER", "neo4j"))
    neo4j_password: str = field(default_factory=lambda: _env("NEO4J_PASSWORD", "rescuegrid"))
    neo4j_database: str = field(default_factory=lambda: _env("NEO4J_DATABASE", "neo4j"))

    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "openai_compatible"))
    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL", "http://127.0.0.1:8080/v1"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "llm"))
    llm_api_key: str = field(default_factory=lambda: _env("LLM_API_KEY", "not-needed"))
    llm_thinking: bool = field(default_factory=lambda: _env("LLM_THINKING", "false").lower() == "true")
    llm_timeout_s: float = field(default_factory=lambda: float(_env("LLM_TIMEOUT_S", "60")))
    qa_max_repairs: int = field(default_factory=lambda: int(_env("QA_MAX_REPAIRS", "2")))

    near_radius_m: float = field(default_factory=lambda: float(_env("NEAR_RADIUS_M", "75")))
    conflict_window_s: float = field(default_factory=lambda: float(_env("CONFLICT_WINDOW_S", "300")))
    conflict_confidence_margin: float = field(default_factory=lambda: float(_env("CONFLICT_CONFIDENCE_MARGIN", "0.25")))
    clock: str = field(default_factory=lambda: _env("RESCUEGRID_CLOCK", "replay"))  # replay | wall
    # Q&A pipeline experiments (comma-separated, see rescuegrid/qa/features.py). Empty = baseline.
    qa_features: frozenset = field(default_factory=lambda: frozenset(f.strip() for f in _env("QA_FEATURES", "").split(",") if f.strip()))

    schema_file: Path = ROOT / "schema" / "schema.cypher"
    seed_file: Path = ROOT / "schema" / "seed_static.cypher"


settings = Settings()
