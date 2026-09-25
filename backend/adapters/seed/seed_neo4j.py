import json
from pathlib import Path

seed_folder = Path(__file__).parent

hospitals_path = seed_folder / "hospitals.json"
shelters_path = seed_folder / "shelters.json"

with open(hospitals_path, "r") as file:
    hospitals = json.load(file)

with open(shelters_path, "r") as file:
    shelters = json.load(file)

print("RescueGrid Seed Data Loader Started")

print("\nHospitals:")
for hospital in hospitals:
    print(
        f"{hospital['name']} -> "
        f"Available beds: {hospital['available_beds']} / "
        f"{hospital['capacity']}"
    )

print("\nShelters:")
for shelter in shelters:
    print(
        f"{shelter['name']} -> "
        f"Available spaces: {shelter['available_spaces']} / "
        f"{shelter['capacity']}"
    )

print("\nSeed data loaded successfully.")