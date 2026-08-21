"""Generate a deterministic, isolated stress-test catalog.

The hand-maintained records keep their existing IDs and relationships. Generated
records use ``stress_*`` IDs and only reference other generated records, so the
existing evaluation cases keep the same correct answers. The normal runtime catalog
is read as input and is never overwritten.
"""

from __future__ import annotations

import json
from pathlib import Path


CATALOG_PATH = Path(__file__).with_name("catalog.json")
OUTPUT_PATH = Path(__file__).with_name("large-catalog.json")
TARGET_LOCATIONS = 250
TARGET_PROVIDERS = 2_500
TARGET_APPOINTMENT_TYPES = 500

CITIES = [
    ("San Francisco", "CA", "America/Los_Angeles", "415"),
    ("Oakland", "CA", "America/Los_Angeles", "510"),
    ("San Jose", "CA", "America/Los_Angeles", "408"),
    ("Sacramento", "CA", "America/Los_Angeles", "916"),
    ("Los Angeles", "CA", "America/Los_Angeles", "213"),
    ("Seattle", "WA", "America/Los_Angeles", "206"),
    ("Portland", "OR", "America/Los_Angeles", "503"),
    ("Phoenix", "AZ", "America/Phoenix", "602"),
    ("Denver", "CO", "America/Denver", "303"),
    ("Chicago", "IL", "America/Chicago", "312"),
    ("Dallas", "TX", "America/Chicago", "214"),
    ("Houston", "TX", "America/Chicago", "713"),
    ("Atlanta", "GA", "America/New_York", "404"),
    ("Boston", "MA", "America/New_York", "617"),
    ("New York", "NY", "America/New_York", "212"),
    ("Philadelphia", "PA", "America/New_York", "215"),
]

DISTRICTS = [
    "Northside",
    "Southside",
    "Eastgate",
    "Westgate",
    "Downtown",
    "Riverside",
    "Lakeside",
    "Hillcrest",
    "Greenwood",
    "Cedar Park",
    "Harbor Point",
    "University",
    "Central",
    "Valley",
    "Meadowbrook",
    "Highland",
]

FACILITY_KINDS = [
    "Medical Center",
    "Health Center",
    "Specialty Clinic",
    "Outpatient Pavilion",
]

STREETS = [
    "Market St",
    "Main St",
    "Broadway",
    "Oak Ave",
    "Pine St",
    "Cedar Ave",
    "Washington St",
    "Park Blvd",
]

CAPABILITY_SETS = [
    [],
    ["lab"],
    ["imaging"],
    ["physical_therapy"],
    ["dental"],
    ["surgery"],
    ["lab", "imaging"],
    ["lab", "physical_therapy"],
    ["imaging", "surgery"],
    ["dental", "surgery"],
    ["lab", "imaging", "surgery"],
]

SPECIALTIES = [
    "Cardiology",
    "Dermatology",
    "Family Medicine",
    "Internal Medicine",
    "Pediatrics",
    "Orthopedics",
    "Neurology",
    "Obstetrics and Gynecology",
    "Ophthalmology",
    "Otolaryngology",
    "Gastroenterology",
    "Endocrinology",
    "Psychiatry",
    "Radiology",
    "Physical Therapy",
    "Laboratory Medicine",
    "Dentistry",
    "Urology",
    "Pulmonology",
    "Allergy and Immunology",
    "Rheumatology",
    "Nephrology",
    "Oncology",
    "Sleep Medicine",
]

# name, duration, normally needs referral, allows a new patient
VISIT_FAMILIES = [
    ("New Patient Consultation", 45, False, True),
    ("Initial Evaluation", 40, False, True),
    ("Routine Follow-up", 20, False, False),
    ("Extended Follow-up", 40, False, False),
    ("Second Opinion", 45, True, True),
    ("Diagnostic Review", 30, True, False),
    ("Treatment Planning", 45, True, False),
    ("Medication Review", 20, False, False),
    ("Procedure Consultation", 40, True, False),
    ("Preoperative Evaluation", 45, True, False),
    ("Postoperative Follow-up", 30, False, False),
    ("Annual Review", 30, False, True),
    ("Urgent Visit", 25, False, True),
    ("Telehealth Consultation", 30, False, True),
    ("Care Coordination Visit", 30, False, False),
    ("Screening Consultation", 30, False, True),
    ("Chronic Care Review", 30, False, False),
    ("Results Review", 20, False, False),
    ("Preventive Visit", 30, False, True),
    ("Specialist Consultation", 40, True, True),
]

FIRST_NAMES = [
    "Avery", "Jordan", "Taylor", "Morgan", "Riley", "Cameron", "Quinn", "Parker",
    "Casey", "Reese", "Rowan", "Emerson", "Finley", "Hayden", "Skyler", "Dakota",
    "Amari", "Blake", "Drew", "Elliot", "Frankie", "Harper", "Jamie", "Kendall",
    "Lane", "Micah", "Noel", "Payton", "Robin", "Sage", "Shawn", "Tatum",
    "Alexis", "Bailey", "Corey", "Devin", "Ellis", "Gray", "Jules", "Kai",
]

LAST_NAMES = [
    "Abbott", "Bennett", "Callahan", "Dalton", "Ellison", "Foster", "Griffin",
    "Hawthorne", "Iverson", "Jennings", "Keaton", "Langley", "Monroe", "Nolan",
    "Owens", "Prescott", "Quincy", "Reynolds", "Sawyer", "Thatcher", "Underwood",
    "Vaughn", "Whitaker", "Xavier", "York", "Zimmerman", "Archer", "Brooks",
    "Collins", "Donovan", "Everett", "Fields", "Gibson", "Holland", "Ingram",
    "Jarvis", "Keller", "Lawson", "Mercer", "Nash",
]

LANGUAGES = [
    "English", "Spanish", "Mandarin", "Cantonese", "Vietnamese", "Arabic",
    "French", "Korean", "Tagalog", "Russian", "Armenian", "Portuguese",
]


def _base_records(data: dict, group: str) -> list[dict]:
    """Make the generator safe to rerun without duplicating stress records."""

    return [item for item in data[group] if not item["id"].startswith("stress_")]


def _required_capability(specialty: str, family_index: int) -> str | None:
    if specialty == "Radiology":
        return "imaging"
    if specialty == "Physical Therapy":
        return "physical_therapy"
    if specialty == "Laboratory Medicine":
        return "lab"
    if specialty == "Dentistry":
        return "dental"
    if specialty in {"Orthopedics", "Dermatology"} and family_index in {8, 9, 10}:
        return "surgery"
    return None


def _make_locations(count: int) -> list[dict]:
    locations = []
    for index in range(count):
        city, state, timezone, area_code = CITIES[index % len(CITIES)]
        district = DISTRICTS[(index // len(CITIES)) % len(DISTRICTS)]
        facility = FACILITY_KINDS[(index // (len(CITIES) * 2)) % len(FACILITY_KINDS)]
        canonical_name = f"{district} {facility} — {city} {index + 1:03d}"
        locations.append(
            {
                "id": f"stress_loc_{index + 1:04d}",
                "name": canonical_name,
                "aliases": [
                    f"{district} clinic {city} {index + 1:03d}",
                    # Repeated on purpose: short names must be disambiguated.
                    f"{district} {facility}",
                ],
                "address": f"{100 + ((index * 37) % 9800)} {STREETS[index % len(STREETS)]}",
                "city": city,
                "state": state,
                "phone": f"({area_code}) 555-{1000 + index:04d}",
                "hours": "Mon-Fri 8:00-17:00",
                "timezone": timezone,
                "capabilities": CAPABILITY_SETS[index % len(CAPABILITY_SETS)],
            }
        )
    return locations


def _make_appointment_types(count: int) -> list[dict]:
    appointment_types = []
    for index in range(count):
        specialty = SPECIALTIES[index % len(SPECIALTIES)]
        family_index = (index // len(SPECIALTIES)) % len(VISIT_FAMILIES)
        family, duration, needs_referral, allows_new = VISIT_FAMILIES[family_index]
        item = {
            "id": f"stress_appt_{index + 1:04d}",
            "name": f"{specialty} — {family}",
            "aliases": [
                f"{specialty} {family}",
                # The family-only alias is intentionally ambiguous across specialties.
                f"network {family}",
            ],
            "specialty": specialty,
            "duration_min": duration,
            "requires_referral": needs_referral,
            "new_patients_allowed": allows_new,
        }
        capability = _required_capability(specialty, family_index)
        if capability:
            item["required_capability"] = capability
        appointment_types.append(item)
    return appointment_types


def _make_providers(
    count: int,
    locations: list[dict],
    appointment_types: list[dict],
) -> list[dict]:
    providers = []
    types_by_specialty = {
        specialty: [item for item in appointment_types if item["specialty"] == specialty]
        for specialty in SPECIALTIES
    }

    for index in range(count):
        specialty = SPECIALTIES[index % len(SPECIALTIES)]
        type_pool = types_by_specialty[specialty]
        offered_types = [
            type_pool[(index // len(SPECIALTIES) + offset) % len(type_pool)]
            for offset in range(min(8, len(type_pool)))
        ]
        needed_capabilities = {
            item["required_capability"]
            for item in offered_types
            if item.get("required_capability")
        }

        start = (index * 17) % len(locations)
        selected_locations: list[dict] = []
        for offset in range(len(locations)):
            location = locations[(start + offset) % len(locations)]
            if location not in selected_locations:
                selected_locations.append(location)
            covered = {
                capability
                for selected in selected_locations
                for capability in selected["capabilities"]
            }
            if len(selected_locations) >= 3 and needed_capabilities <= covered:
                break
        selected_locations = selected_locations[:6]

        # The 1,600-name space deliberately creates duplicate full names at this scale.
        first_name = FIRST_NAMES[index % len(FIRST_NAMES)]
        last_name = LAST_NAMES[(index // len(FIRST_NAMES)) % len(LAST_NAMES)]
        languages = ["English"]
        second_language = LANGUAGES[(index * 7) % len(LANGUAGES)]
        if second_language != "English":
            languages.append(second_language)

        providers.append(
            {
                "id": f"stress_prov_{index + 1:05d}",
                "name": f"Dr. {first_name} {last_name}",
                "title": ("MD", "DO", "NP", "PA")[index % 4],
                "specialty": specialty,
                "location_ids": [item["id"] for item in selected_locations],
                "accepting_new_patients": index % 5 != 0,
                "languages": languages,
                "appointment_type_ids": [item["id"] for item in offered_types],
            }
        )
    return providers


def build() -> dict:
    data = json.loads(CATALOG_PATH.read_text())
    base_locations = _base_records(data, "locations")
    base_providers = _base_records(data, "providers")
    base_types = _base_records(data, "appointment_types")

    generated_locations = _make_locations(TARGET_LOCATIONS - len(base_locations))
    generated_types = _make_appointment_types(
        TARGET_APPOINTMENT_TYPES - len(base_types)
    )
    generated_providers = _make_providers(
        TARGET_PROVIDERS - len(base_providers),
        generated_locations,
        generated_types,
    )

    data["locations"] = base_locations + generated_locations
    data["providers"] = base_providers + generated_providers
    data["appointment_types"] = base_types + generated_types
    return data


def main() -> None:
    data = build()
    OUTPUT_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(
        f"Wrote {len(data['locations'])} locations, "
        f"{len(data['providers'])} providers, and "
        f"{len(data['appointment_types'])} appointment types to {OUTPUT_PATH}"
    )


if __name__ == "__main__":
    main()
