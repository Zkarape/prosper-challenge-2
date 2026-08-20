# Catalog data

`catalog.json` is a synthetic clinic catalog for the **Phase 2 context-management**
work: a scheduling agent has to navigate it reliably and cost-effectively. It is
deliberately **large** (so dumping it all into the prompt is expensive/inaccurate) and
deliberately **messy** (so naive matching fails).

Stress profile: **250 locations · 2,500 providers · 500 appointment types**.

The first 8 locations, 50 providers, and 82 appointment types are the original
evaluation catalog. Records with `stress_*` IDs expand it without changing those
original relationships. Regenerate the same dataset with:

```bash
python backend/data/generate_large_catalog.py
```

This size tests whether extraction and deterministic catalog resolution stay
accurate without sending the full catalog to the LLM. The generated data includes
duplicate provider names, shared aliases, multiple time zones, multi-location
providers, referrals, new-patient restrictions, and capability-gated services.

## Schema

```jsonc
{
  "policies": [ "...human-readable rules a correct agent must honor..." ],

  "locations": [{
    "id", "name", "address", "city", "phone", "hours",
    "capabilities": ["imaging" | "lab" | "physical_therapy" | "dental" | "surgery"]
  }],

  "providers": [{
    "id", "name", "title",            // MD / DO / NP / PA
    "specialty",
    "location_ids": [...],            // where they practice
    "accepting_new_patients": bool,
    "languages": [...],
    "appointment_type_ids": [...]     // what they can be booked for
  }],

  "appointment_types": [{
    "id", "name", "specialty", "duration_min",
    "requires_referral": bool,
    "new_patients_allowed": bool,
    "required_capability": "imaging" | ... | (absent)
  }]
}
```

## Rules to honor (also in `policies`)

- A provider can only be booked at a location in their `location_ids`.
- A provider can only be booked for an appointment type in their `appointment_type_ids`.
- An appointment type with a `required_capability` is only bookable at a location whose
  `capabilities` include it (e.g. MRI / X-Ray / CT need `imaging`).
- `requires_referral` types need a referral on file first.
- New patients can only book `new_patients_allowed` types, and only with providers who are
  `accepting_new_patients`.

## Intentional messiness (you must handle this)

- **Duplicate provider names** — multiple `Dr. Chen` and several `Dr. Maria Garcia` across
  different specialties / locations / roles. A name alone is not a unique key.
- **Near-duplicate appointment types** — e.g. *Annual Physical* vs *Annual Wellness Visit*,
  *Skin Cancer Screening* vs *Full Body Skin Exam*, the *MRI - Brain/Spine/Knee* family.
- **Location-gated services** — only a few locations have `imaging`, so a provider may
  practice at several sites but offer an MRI at only one.
- **Multi-location providers** — "book with Dr. X" still needs a location to disambiguate.
