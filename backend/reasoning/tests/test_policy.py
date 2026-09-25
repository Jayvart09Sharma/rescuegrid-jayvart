"""Pure unit tests for the status/conflict policy - no database needed."""
from datetime import datetime, timedelta, timezone

from rescuegrid.contracts import Event
from rescuegrid.fusion.policy import decide

T0 = datetime(2026, 9, 25, 14, 0, tzinfo=timezone.utc)
KW = dict(conflict_window_s=300, confidence_margin=0.25)


def ev(claim, source="drone_vision", conf=0.9, dt=0):
    return Event(source=source, timestamp=T0 + timedelta(seconds=dt), confidence=conf, entity="X", claim=claim, raw_evidence_ref="r")


def cur(status="open", source="radio_asr", conf=0.7, since=T0, **extra):
    return {"status": status, "source": source, "confidence": conf, "status_since": since, "conflict": False, **extra}


def test_unknown_entity_is_created():
    assert decide(None, ev("collapsed"), **KW).action == "create"


def test_same_claim_confirms():
    d = decide(cur("blocked"), ev("blocked", source="drone_vision", dt=60), **KW)
    assert d.action == "confirm" and d.applied


def test_older_event_is_stale_and_not_applied():
    d = decide(cur("collapsed", since=T0 + timedelta(seconds=15)), ev("intact", dt=5), **KW)
    assert d.action == "stale" and not d.applied


def test_seed_status_is_simply_overridden():
    d = decide(cur("intact", source="seed_dataset", conf=1.0, since=T0 - timedelta(hours=1)), ev("collapsed", conf=0.91, dt=15), **KW)
    assert d.action == "override"


def test_contradiction_from_other_source_in_window_is_conflict():
    d = decide(cur("open", source="radio_asr", conf=0.70), ev("blocked", source="drone_vision", conf=0.85, dt=15), **KW)
    assert d.action == "conflict"


def test_same_source_correcting_itself_overrides():
    d = decide(cur("open", source="drone_vision", conf=0.70), ev("blocked", source="drone_vision", conf=0.85, dt=15), **KW)
    assert d.action == "override"


def test_much_higher_confidence_overrides_without_conflict():
    d = decide(cur("open", source="radio_asr", conf=0.40), ev("blocked", source="drone_vision", conf=0.95, dt=15), **KW)
    assert d.action == "override"


def test_outside_window_overrides():
    d = decide(cur("open", source="radio_asr", conf=0.70), ev("blocked", source="drone_vision", conf=0.75, dt=3600), **KW)
    assert d.action == "override"


def test_third_source_resolves_conflict():
    c = cur("blocked", source="drone_vision", conf=0.85, conflict=True, conflict_claim="open", conflict_source="radio_asr")
    assert decide(c, ev("blocked", source="field_report", dt=60), **KW).action == "resolve_conflict"
    assert decide(c, ev("open", source="field_report", dt=60), **KW).action == "confirm_competing"
    # one of the two original sources repeating itself does not resolve anything
    assert decide(c, ev("blocked", source="drone_vision", dt=60), **KW).action == "confirm"


def test_window_is_measured_from_last_confirmation():
    old_status = cur("open", source="radio_asr", conf=0.70, since=T0 - timedelta(hours=2), last_confirmed=T0 + timedelta(seconds=150))
    d = decide(old_status, ev("blocked", source="drone_vision", conf=0.85, dt=165), **KW)
    assert d.action == "conflict"


def test_placeholder_status_never_conflicts():
    d = decide(cur("unknown", source="drone_vision", conf=0.8), ev("blocked", source="radio_asr", conf=0.8, dt=10), **KW)
    assert d.action == "override"


def test_dissenting_source_recanting_resolves():
    c = cur("blocked", source="drone_vision", conf=0.85, conflict=True, conflict_claim="open", conflict_source="radio_asr")
    assert decide(c, ev("blocked", source="radio_asr", dt=60), **KW).action == "resolve_conflict"


def test_self_correction_is_a_conflict_when_another_source_backs_the_status():
    c = cur("blocked", source="drone_vision", conf=0.85, last_confirmed=T0 + timedelta(seconds=60), confirmed_sources=["drone_vision", "field_report"])
    assert decide(c, ev("open", source="drone_vision", conf=0.85, dt=90), **KW).action == "conflict"
    alone = cur("blocked", source="drone_vision", conf=0.85, last_confirmed=T0 + timedelta(seconds=60), confirmed_sources=["drone_vision"])
    assert decide(alone, ev("open", source="drone_vision", conf=0.85, dt=90), **KW).action == "override"


def test_confirmation_older_than_latest_evidence_is_stale():
    c = cur("blocked", source="drone_vision", conf=0.85, last_confirmed=T0 + timedelta(seconds=120))
    assert decide(c, ev("blocked", source="field_report", dt=60), **KW).action == "stale"
