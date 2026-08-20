# Reviewed failure matrix

## What the verdicts mean

- `PRODUCT_BUG` — the documented product behavior is clear, and the product did
  something different.
- `EXPECTED_RESULT_WRONG` — the product behaved correctly, but the test expected
  the wrong behavior.
- `GRADER_TOO_STRICT` — the useful behavior was correct, but the grader failed on
  formatting, an internal code, or another difference that did not change the
  patient outcome.
- `NEEDS_PRODUCT_DECISION` — more than one behavior is reasonable and the product
  contract does not yet choose one.

## Current result

The 36 red cases are **not** 36 product bugs.

| Verdict | Count |
|---|---:|
| `PRODUCT_BUG` | 24 |
| `GRADER_TOO_STRICT` | 8 |
| `NEEDS_PRODUCT_DECISION` | 3 |
| `EXPECTED_RESULT_WRONG` | 1 |
| **Total reviewed failures** | **36** |

These are version-one verdicts based on the current catalog, `solution.md`, and
the deterministic scheduling boundary. We should review them one at a time and
change a verdict when we make a different product decision.

## Matrix

| Case | Verdict | Short proof |
|---|---|---|
| 003 — Required Richmond dental care | `PRODUCT_BUG` | The engine found the expected alternative but also allowed another provider. With earliest-time ranking, that extra provider could be offered even though the patient named Dr. Wei Lee. |
| 004 — Preferred Richmond | `PRODUCT_BUG` | The requested provider had a valid alternative location, but the engine also included a different provider and reported a blocker on a ready result. |
| 005 — Duplicate provider information request | `PRODUCT_BUG` | “Dr. Linda Ramirez” was not kept as a provider fact, so the catalog could not use the information request to disambiguate the two matching providers. |
| 006 — Appointment context resolves duplicate provider | `PRODUCT_BUG` | MRI context correctly resolved the radiologist, but availability candidates still included a different provider/location combination. |
| 007 — Informal skin check | `PRODUCT_BUG` | The catalog contains relevant skin appointment types, but “skin check” returned no match instead of safe choices to clarify. |
| 008 — Exact skin cancer screening | `PRODUCT_BUG` | The named provider/location candidate was present, but seven unrelated candidates remained eligible for slot selection. |
| 009 — Annual checkup | `PRODUCT_BUG` | The catalog contains Annual Physical and Annual Wellness Visit, but the resolver returned no match instead of asking the patient to choose between them. |
| 010 — Cardiology referral question | `GRADER_TOO_STRICT` | The engine correctly asked for referral status. The only mismatch was the missing internal `REFERRAL_STATUS_REQUIRED` proof code. |
| 011 — Impossible provider makes referral irrelevant | `PRODUCT_BUG` | The engine asked about a referral before noticing that the required provider does not accept new patients. The referral answer cannot change that result. |
| 012 — Referral not on file | `PRODUCT_BUG` | Extraction and validation succeeded, but the trusted patient status, referral status, and MRI request were not saved into conversation state. |
| 013 — MRI at impossible location | `PRODUCT_BUG` | The correct relaxed candidate was present, but the engine also offered a candidate that changed more of the patient’s request than necessary. |
| 014 — MRI without body area | `PRODUCT_BUG` | The catalog contains several MRI types, but the resolver returned no match rather than asking which MRI the patient needs. |
| 015 — Provider works at several locations | `NEEDS_PRODUCT_DECISION` | We have not decided whether missing location means “search all of this provider’s locations” or “ask the patient to choose a location first.” Both are reasonable. |
| 016 — Named provider is unavailable for new patients | `NEEDS_PRODUCT_DECISION` | We have not decided whether to ask “is another provider okay?” before showing alternatives, or to show checked alternatives immediately and request permission there. |
| 017 — Replace selected provider | `PRODUCT_BUG` | The replacement provider was found, but another provider remained in the availability candidates and could win because of earliest-time ranking. |
| 019 — Replace impossible location | `PRODUCT_BUG` | The corrected location worked, but the engine kept an additional provider candidate instead of honoring the existing named provider first. |
| 020 — Change Friday to Monday | `PRODUCT_BUG` | The new Monday preference was extracted, but the old pending Friday offer was not explicitly rejected. A changed request must invalidate the old offer. |
| 021 — Accept location alternative | `PRODUCT_BUG` | The patient clearly accepted. The model added harmless selection text, but semantic validation rejected the whole answer, so the accepted alternative was not applied. |
| 022 — Reject location alternative | `PRODUCT_BUG` | “No, I can only go to Richmond” was classified as unclear instead of a clear rejection, so the pending alternative was not handled correctly. |
| 023 — “Yes” without a pending question | `PRODUCT_BUG` | Extraction correctly marked the answer unclear, but the engine asked for appointment type instead of asking what the patient meant by “Yes.” |
| 024 — “No” answers referral question | `PRODUCT_BUG` | The answer clearly meant no referral. Extra selection text caused validation to reject the whole answer instead of safely applying `NOT_ON_FILE`. |
| 025 — Long message with two time preferences | `PRODUCT_BUG` | The extractor kept “Fridays before noon” but dropped the fallback “earliest morning,” which can change the slots offered. |
| 026 — Correction after long history | `PRODUCT_BUG` | The corrected provider/location candidate was present, but another provider remained eligible and could be selected by availability ranking. |
| 027 — Prompt injection referral claim | `GRADER_TOO_STRICT` | The system safely left referral status unknown and asked for it. The test failed because it expected an explicit `KEEP`, although an omitted field already means keep. |
| 028 — Symptoms without appointment type | `NEEDS_PRODUCT_DECISION` | We need to decide whether symptoms belong in a separate reason-for-visit field, trigger staff triage, or are stored as raw appointment text for later clarification. The current schema does not define this. |
| 029 — Information plus booking request | `PRODUCT_BUG` | The raw location difference was harmless, but the larger product gap is that the service does not yet support answering an information question while preserving and continuing the booking request in the same turn. |
| 030 — Existing-patient “follow-up” | `EXPECTED_RESULT_WRONG` | The catalog has both Follow-up Visit and Follow-up Consultation for the named provider/location. Choosing Follow-up Visit without clarification would be a guess, so the engine was right to ask. |
| 031 — New patient requests existing-only follow-up | `GRADER_TOO_STRICT` | The engine correctly blocked the booking. Only the internal blocker name differed from the expected name. |
| 032 — Pre-operative referral question | `GRADER_TOO_STRICT` | The engine correctly asked for referral status. The failure was only the missing expected proof code. |
| 033 — Appointment type has no provider | `GRADER_TOO_STRICT` | The engine correctly returned no match and did not offer a slot. It failed only because `NO_PROVIDER_OFFERS_APPOINTMENT` was not included as a blocker code. |
| 034 — Urology requirement field | `GRADER_TOO_STRICT` | Urology was extracted and validation succeeded. The failed detail was a missing `UNSPECIFIED` value that did not change the no-provider scheduling outcome. |
| 035 — Required Richmond capability | `GRADER_TOO_STRICT` | The engine detected the correct capability failure and did not treat Richmond as valid. The grader expected a negative blocker name while the engine used the failed positive rule name. |
| 036 — Required provider at wrong location | `PRODUCT_BUG` | Besides blocker-name differences, the engine returned an alternative that changed the provider even though a smaller location-only relaxation existed. |
| 038 — Correct patient status to existing | `PRODUCT_BUG` | The named provider/location candidate was present, but nine unrelated candidates remained available and could override the patient’s named choices. |
| 039 — Conflicting referral statements | `PRODUCT_BUG` | The extraction schema cannot directly represent two conflicting referral claims in one turn. The conflict became `UNKNOWN` instead of an explicit clarification state. |
| 040 — Speech error “M R I spy” | `GRADER_TOO_STRICT` | The system did not convert the phrase into MRI spine or offer a booking. It safely asked for the appointment type again; the failure was the missing unclear-reference marker and action-label difference. |

## Suggested review order

Review one case at a time in this order:

1. Case 003 — establish how candidate filtering should honor named preferences.
2. Case 010 — decide whether proof-code mismatches should fail an evaluation.
3. Case 015 — decide when the system should ask for a location.
4. Case 030 — confirm that ambiguous catalog matches must never be guessed.

After these four examples, the same decisions will resolve many other rows.
