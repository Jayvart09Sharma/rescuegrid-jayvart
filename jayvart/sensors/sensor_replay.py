import json
import time
from pathlib import Path


scenario_path = Path(__file__).parent.parent / "config" / "scenario.json"

with open(scenario_path, "r") as file:
    scenario = json.load(file)

print("RescueGrid Sensor Replay Started")

start_time = time.time()

for event in scenario["events"]:

    if event["type"] != "sensor":
        continue

    while time.time() - start_time < event["time"]:
        time.sleep(0.1)

    print(
        f"[{event['time']}s] "
        f"{event['sensor_type']} sensor "
        f"{event['sensor_id']} -> "
        f"{event['value']} {event['unit']}"
    )

print("Sensor replay finished.")