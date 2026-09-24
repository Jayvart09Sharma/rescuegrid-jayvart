import json
import time
from pathlib import Path

reports_path = Path(__file__).parent / "field_reports.json"

with open(reports_path, "r") as file:
    reports = json.load(file)

print("RescueGrid Field Report Replay Started")

start_time = time.time()

for report in reports:

    while time.time() - start_time < report["time"]:
        time.sleep(0.1)

    print(
        f"[{report['time']}s] "
        f"{report['report_id']} -> "
        f"{report['text']}"
    )

print("Field report replay finished.")