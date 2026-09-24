import json
import time
from pathlib import Path

# Path to the shared scenario file
scenario_path = Path(__file__).parent.parent / "config" / "scenario.json"

# Load scenario data
with open(scenario_path, "r") as file:
    scenario = json.load(file)

print("RescueGrid GPS Replay Started")

start_time = time.time()

# Go through all events in the scenario
for event in scenario["events"]:

    # Only use GPS events
    if event["type"] != "gps":
        continue

    # Wait until the event's scheduled time
    while time.time() - start_time < event["time"]:
        time.sleep(0.1)

    # Print the GPS update
    print(
        f"[{event['time']}s] "
        f"{event['entity']} -> "
        f"Latitude: {event['latitude']}, "
        f"Longitude: {event['longitude']}"
    )

print("GPS replay finished.")