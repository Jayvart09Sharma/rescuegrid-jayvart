from rescuegrid.qa.engine import fact_check
from rescuegrid.qa.render import render, shape_of


def test_entity_status_shape():
    rows = [{"id": "Building-14", "name": "Building 14", "status": "collapsed", "status_since": "2026-09-25 14:00:15+00:00", "source": "drone_vision",
             "confidence": 0.91, "raw_evidence_ref": "drone/cam1/frame_000450.jpg", "occupancy_est": 120, "conflict": False}]
    assert shape_of(rows) == "entity_status"
    out = render("What is the status of Building 14?", rows)
    assert out == "Building 14 is collapsed since 14:00:15Z (drone_vision, 0.91), est. 120 occupants."
    assert fact_check(out, rows) == []


def test_conflict_is_surfaced():
    rows = [{"id": "Road-Bridge", "name": "Bridge Street", "status": "blocked", "source": "drone_vision", "confidence": 0.85, "conflict": True,
             "conflict_claim": "open", "conflict_source": "radio_asr", "conflict_confidence": 0.7}]
    out = render("Is the bridge open?", rows)
    assert "CONFLICTING: radio_asr reports open (0.70)" in out and fact_check(out, rows) == []


def test_event_list_and_aggregate():
    rows = [{"id": "Road-Main", "name": "Main Street", "claim": "blocked", "timestamp": "2026-09-25 14:00:32+00:00", "source": "radio_asr", "confidence": 0.78},
            {"id": "Road-Bridge", "name": "Bridge Street", "claim": "open", "timestamp": "2026-09-25 14:02:30+00:00", "source": "radio_asr", "confidence": 0.7}]
    assert shape_of(rows) == "event_list"
    out = render("What did radio report?", rows)
    assert out.startswith("2 event(s): 14:00:32Z Main Street blocked (radio_asr 0.78); 14:02:30Z Bridge Street open (radio_asr 0.70).")
    assert fact_check(out, rows) == []
    assert render("How many roads are blocked?", [{"n": 2}]) == "N: 2."
    assert render("How many people?", [{"people": 120}]) == "People: 120."


def test_unknown_shapes_defer_to_the_model():
    assert render("q", [{"id": "Team-Rescue4", "near": [{"id": "Building-14"}]}]) is None     # nested list -> model
    assert render("q", [{"route": ["Road-Oak", "Road-River"], "hops": 1}]) is None
    assert render("q", []) is None
