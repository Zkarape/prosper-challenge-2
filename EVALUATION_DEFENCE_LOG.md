# Evaluation defence log

## Progress

- Start: 4/40.
- `eval_54e9d18aa1fc`: 17/40.
- `eval_baeee8ff17c9`: 26/40.
- `eval_7b7be1f758be`: 35/40.
- `eval_28a5e2838f61`: 39/40.
- Final `eval_51558bad91de`: 40/40.

## Fixes

- 003–004: smallest safe alternative; grade best candidate group.
- 005: preserve names in information questions; clarify duplicate providers.
- 007, 009, 014, 030: catalog aliases preserve ambiguity.
- 010, 032: unknown referral proof codes.
- 011, 033–034: stop before irrelevant questions.
- 013, 031, 036: stable blocker names.
- 015: ask location for multi-location providers.
- 016: ask permission before changing provider.
- 020–024: safe answer, correction, acceptance, and rejection handling.
- 025, 028–029, 039–040: long time, symptoms, mixed intent, conflict, and speech errors.
- Grader: compare meaning, not harmless wording or `KEEP` differences.
- PostgreSQL: runs survive restart.

## Proof

- Extraction: 40/40.
- Validation: 40/40.
- State: 40/40.
- Engine: 40/40.
- Backend: 81 passed, 2 skipped, 0 failed.
- Frontend: build passed; 2/2 tests passed.

## Context trade-off experiment

The first repeated live run rejected the original state-only claim: it saved
tokens, but passed 13/15 trials. Recent context passed 14/15 and full history
passed 9/15. The failures exposed two validation gaps: unrelated wording could
clear patient status, and explicit “any doctor/location” corrections relied on
the model choosing the exact patch operation.

After grounding status changes and moving explicit no-preference language into
deterministic validation, `context_eval_b4dad45befaf` produced:

- state only: 14/15;
- state plus the last exchange: 15/15;
- full history: 12/15;
- selected versus full: 8.86% fewer input tokens and 8.44% fewer total tokens.

The final-code run `context_eval_c079bf2575a5` then produced 15/15 for state only,
13/15 for recent context and 13/15 for full history. Recent context used 3.39%
fewer input tokens than full history, but caching made it slightly more expensive.

Decision: keep bounded recent context as a provisional safety margin for short
references. Claim measured input-token reduction, not proven lower cost or proven
accuracy non-inferiority. The changing results are evidence that the next step is
a larger human-reviewed benchmark, not more tuning against these five scenarios.
