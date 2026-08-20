# Evaluation progress record

## Run snapshot

- Run: `eval_901e3bf9c516`
- Model: `gpt-5.4-mini`
- Dataset: 40 cases, version 1.0
- Result: **4 passed, 36 failed**
- Extraction passed: 28/40
- Validation passed: 38/40
- State update passed: 30/40
- Engine passed: 4/40
- Tokens: 79,341
- Estimated cost: $0.0897
- Runtime: 29.4 seconds

This file records the first failed stage for each case. Later stages may also
fail because an earlier stage gave them the wrong input.

Important: cases 001 and 002 were manually reviewed. The other 38 expected
results are still drafts. A failure can mean a product gap, an expected result
that needs correction, or a comparison that is too strict.

## Succeeded — 4

### Case 001 — Simple dental request

**Proof:** Extraction found the new-patient status and dental cleaning. The
validator accepted them, state was updated, and the engine correctly continued
to availability without asking about a referral.

### Case 002 — Remove a hard location constraint

**Proof:** “Any location is fine” cleared Richmond. The old selection stayed
cleared, and the engine returned valid dental candidates.

### Case 018 — Clear provider preference

**Proof:** The provider preference was removed without changing the other
patient facts. All four stages matched the expected result.

### Case 037 — Partial Mission location is ambiguous

**Proof:** The system kept “Mission” as patient text, found multiple location
matches, and asked for location clarification instead of guessing.

## Failed — 36

| Case | First failed stage | Short reason and proof |
|---|---|---|
| 003 — Required Richmond dental care | Engine | The decision type was mostly correct, but the blocker name differed and the engine returned two alternatives instead of the expected one. |
| 004 — Preferred Richmond | Engine | The engine reported a location-capability blocker when none was expected and returned an extra provider candidate. |
| 005 — Duplicate provider in information request | Extraction | The provider was not stored as a provider fact. It was returned only as an unclear reference. |
| 006 — Appointment context resolves provider | Engine | The correct MRI candidate was present, but the engine also returned a second candidate that did not match the expected provider and location. |
| 007 — Informal skin check | Engine | Expected two ambiguous appointment matches. The resolver returned no match and asked for a required field instead of clarification. |
| 008 — Exact skin cancer screening | Engine | The expected candidate was present, but the engine returned eight valid candidates instead of only the requested match. |
| 009 — Annual checkup | Engine | Expected an ambiguous choice between two appointment types. The resolver returned unresolved and did not show the two choices. |
| 010 — Relevant cardiology referral question | Engine | The engine asked for referral status, but the expected `REFERRAL_STATUS_REQUIRED` proof code was missing. This may be a proof-format mismatch. |
| 011 — Impossible provider makes referral irrelevant | Engine | The engine asked about referral status first. It should have detected that the required provider cannot accept the new patient and stopped asking irrelevant questions. |
| 012 — Referral not on file | State | Extraction and validation passed, but the patient status, referral status, and MRI appointment were not applied to state. |
| 013 — MRI at impossible location | Engine | The engine used different blocker names and returned an extra relaxed candidate. |
| 014 — MRI body area missing | Engine | Expected MRI choices for different body areas. The resolver returned no match instead of an ambiguous MRI result. |
| 015 — Provider works at several locations | Engine | The engine started availability immediately. It should have asked which location the patient wanted. |
| 016 — Ask whether provider is flexible | Engine | The engine offered alternatives immediately. The expected behavior was to first ask whether the named provider was flexible. |
| 017 — Replace selected provider | Engine | The requested replacement provider was returned, but the old/other provider was still included as valid. |
| 019 — Replace impossible location | Engine | The correct location candidate was present, but an additional provider candidate was returned. |
| 020 — Change Friday to Monday | Extraction | The new Monday time was understood, but the extractor did not mark the pending Friday offer as rejected. |
| 021 — Accept location alternative | Extraction | The model correctly understood acceptance but also returned selection text. The strict expected result rejected that extra detail. This comparison should be reviewed. |
| 022 — Reject location alternative | Extraction | “No” was marked unclear instead of rejected. The expected explicit location `KEEP` was also absent. |
| 023 — Yes without a pending question | Engine | Extraction correctly marked “Yes” as unclear, but the engine asked for appointment type instead of asking what the patient was agreeing to. |
| 024 — No answers referral question | Extraction | The model understood the negative answer but also returned selection text. The strict expected result rejected the extra detail. This comparison should be reviewed. |
| 025 — Long message with many facts | Extraction | The time preference kept only “Fridays before noon” and lost “otherwise earliest morning.” |
| 026 — Correction after long history | Engine | The expected provider candidate was present, but an extra provider candidate was also returned. |
| 027 — Prompt injection referral claim | Extraction | The system safely did not invent referral status and state stayed correct, but the test expected an explicit `KEEP`. Missing `KEEP` should probably count as equivalent and the grader should be reviewed. |
| 028 — Symptoms are not a diagnosis | Extraction | The extractor did not preserve the patient’s requested visit description and added a provider clear. The validator correctly avoided inventing a diagnosis. |
| 029 — Information plus booking request | Extraction | The meaning was close, but extracted location text was “Mission District clinic” instead of the expected “Mission District.” This comparison may be too strict. |
| 030 — Existing-patient follow-up | Engine | “Follow-up visit” resolved to two appointment types. The engine asked for clarification instead of selecting the expected follow-up type. |
| 031 — New patient requests existing-only follow-up | Engine | The engine blocked the request correctly, but its blocker name differed from the expected blocker name. This is likely a proof-format mismatch. |
| 032 — Pre-operative evaluation referral | Engine | The engine asked for referral status, but the expected referral-required proof code was missing. |
| 033 — Physical therapy has no provider | Engine | The engine reached no match, but did not return the expected `NO_PROVIDER_OFFERS_APPOINTMENT` blocker proof. |
| 034 — Urology has no provider | Extraction | The appointment was extracted, but its `UNSPECIFIED` requirement was missing. |
| 035 — Required Richmond without provider | Engine | The engine found the location capability failure, but used a positive rule name where the expected result used a negative blocker name. |
| 036 — Required provider at wrong location | Engine | The blocker names differed and the engine returned an extra alternative provider. |
| 038 — Correct new patient to existing | Engine | The expected candidate was present, but the engine returned ten candidates instead of only the requested provider/location match. |
| 039 — Conflicting referral statements | Extraction | Expected `CONFLICTING`; the extractor returned `UNKNOWN`, so the conflict was not preserved explicitly. |
| 040 — Speech error “M R I spy” | Extraction | The text was safely not changed into “MRI spine,” but the extractor failed to mark the phrase as an unclear appointment reference. |

## Main patterns discovered

1. **Candidate filtering is too broad.** Many engine failures contain the right
   candidate plus several extra candidates.
2. **Ambiguous appointment phrases need better resolution.** Examples include
   “skin check,” “annual checkup,” “MRI,” and “follow-up visit.”
3. **Pending yes/no handling needs work.** Cases 020–024 show inconsistent
   acceptance, rejection, and clarification behavior.
4. **Blocker proof names are inconsistent.** Several decisions are correct, but
   their blocker codes do not match the expected contract.
5. **Some evaluator expectations are too strict.** Explicit `KEEP`, harmless
   selection text, and small raw-text differences should be reviewed before
   treating them as product failures.

## Reliability note

Evaluation jobs are currently stored in backend process memory. With multiple
backend workers, one worker can create a run while another worker cannot find
it. Before using this at larger scale, store run status and results in the
database and execute runs through a shared job queue.
