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
- Backend: 62 passed, 2 skipped, 0 failed.
- Frontend: build passed; 2/2 tests passed.
