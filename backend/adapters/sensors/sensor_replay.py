"""Sensor replay adapter (Jayvant). Reads the ONE team scenario (Shresth/rescuegrid/scenario/scenario.json)
and posts the sensor events to the event bus on the shared clock. A live adapter would replace
`scenario_events(...)` with an MQTT subscription and call post_event() the same way.
    python3 sensors/sensor_replay.py            # real time
    RESCUEGRID_SPEED=10 python3 sensors/sensor_replay.py
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scenario"))   # backend/scenario
from rescuegrid_events import ScenarioClock, log_line, post_event, scenario_events

print("RescueGrid Sensor Replay Started")
clock = ScenarioClock()
for ev in scenario_events(sources=["sensor"]):
    clock.wait_until(ev["_t"])
    reply = post_event(ev, raw_bytes=64)          # one MQTT sensor reading ~64 bytes on the wire
    print(log_line(ev, reply), flush=True)
print("Sensor replay finished.")
