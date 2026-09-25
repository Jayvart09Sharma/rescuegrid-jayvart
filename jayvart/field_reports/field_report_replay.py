"""Field report replay adapter (Jayvant). Plays the pre-written field reports from the ONE team
scenario into the event bus. The report text travels in details.text; entity + claim are already
structured in the scenario so no LLM is needed on this path.
    python3 field_reports/field_report_replay.py
    RESCUEGRID_SPEED=10 python3 field_reports/field_report_replay.py
"""
import sys
sys.path.insert(0, "/home/hp2/Shresth/rescuegrid/scenario")
from rescuegrid_events import ScenarioClock, log_line, post_event, scenario_events

print("RescueGrid Field Report Replay Started")
clock = ScenarioClock()
for ev in scenario_events(sources=["field_report"]):
    clock.wait_until(ev["_t"])
    reply = post_event(ev, raw_bytes=len(ev.get("details", {}).get("text", "").encode()))
    print(log_line(ev, reply), flush=True)
print("Field report replay finished.")
