"""GPS replay adapter (Jayvant). Plays the responder position updates from the ONE team scenario
into the event bus on the shared clock. Entity names are spoken names ("Rescue Team 4"); the fusion
agent resolves them to graph ids (Team-Rescue4) and creates NEAR edges within 75 m.
    python3 gps/gps_replay.py            # real time
    RESCUEGRID_SPEED=10 python3 gps/gps_replay.py
"""
import sys
sys.path.insert(0, "/home/hp2/Shresth/rescuegrid/scenario")
from rescuegrid_events import ScenarioClock, log_line, post_event, scenario_events

print("RescueGrid GPS Replay Started")
clock = ScenarioClock()
for ev in scenario_events(sources=["gps"]):
    clock.wait_until(ev["_t"])
    reply = post_event(ev, raw_bytes=96)          # one GPS fix ~96 bytes on the wire
    print(log_line(ev, reply), flush=True)
print("GPS replay finished.")
