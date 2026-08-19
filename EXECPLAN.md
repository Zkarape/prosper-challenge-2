# Build an observable, reliable scheduling agent

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds. This plan follows `PLANS.md` in the repository root.

## Purpose / Big Picture

After this work, a reviewer can type or speak a scheduling request, watch the system extract new facts, resolve catalog records, apply every booking rule, find or reject appointment combinations, offer mock time slots, and confirm a mock booking. The same screen shows token use, estimated cost, latency, and a comparison with the expensive approach of sending the entire catalog to the model. A second area lets the reviewer inspect and edit the voice agent's node graph.

The system must never rely on the language model to enforce booking rules. The language model turns natural language into a small state update and turns a checked next action into natural speech. Ordinary Python code owns the saved scheduling state, catalog lookup, policy checks, candidate ranking, availability, and booking.

## Progress

- [x] (2026-08-18) Read the challenge, catalog, current voice pipeline, graph schema, and repository instructions.
- [x] (2026-08-19) Create the `frontend/` dashboard project and complete the first independently reviewable product slice.
- [x] (2026-08-19) Implement the typed patient request, content-hashed catalog, entity resolution, per-rule results, exact invalid-request proof, relevant-question selection, explainable ranking, and hard-versus-soft alternatives.
- [x] (2026-08-19) Implement deterministic mock availability and idempotent mock booking, including catalog-derived timezones, duration validation, request/offer validation, and duplicate-request protection.
- [x] (2026-08-19) Add unit and end-to-end coverage for extraction validation, difficult catalog cases, pending offers, stale slots, idempotency, API contracts, and the voice adapter.
- [ ] Add backend endpoints for text turns, state inspection, trace inspection, evaluation runs, catalog browsing, and agent graph loading/saving. (Completed: conversation creation, conversation inspection, text turns, health check, deterministic local extraction, per-turn trace, slots, confirmation, and mock booking. Remaining: durable trace lookup, evaluation, catalog, graph, and structured LLM endpoints.)
- [x] (2026-08-19) Replace the frontend starter with the scheduling workbench, decision trace, live extraction usage, patient-request view, graph editor prototype, catalog sample, and an embedded Pipecat test call.
- [x] (2026-08-19) Connect committed voice transcripts to structured extraction and the deterministic scheduler; stream live transcripts, checked responses, usage, state, and decision traces into the Workbench.
- [x] (2026-08-19) Run backend tests, strict-schema validation, frontend tests/lint/build, Pipecat/OpenAI imports, and the representative golden scenario.
- [x] (2026-08-19) Write `solution.md` with architecture, safety boundaries, trade-offs, and intentionally mocked scope.

## Surprises & Discoveries

- Observation: The repository contains a working Pipecat voice runtime and declarative graph compiler but no custom frontend.
  Evidence: `backend/bot.py`, `backend/agent_builder/`, and `backend/example_flow.json` exist; no frontend existed before this work.
- Observation: The catalog is structurally valid but contains business-level traps.
  Evidence: duplicate provider names, providers working at locations that lack a service capability, and appointment types with no provider.
- Observation: Python 3.12 is installed and is a safer choice for the current voice dependencies than the default Python 3.14.
  Evidence: `/opt/homebrew/bin/python3.12` is available.
- Observation: The first deterministic engine slice passes all seven initial tests without API keys.
  Evidence: `/opt/homebrew/bin/python3.12 -m unittest discover -s backend/tests -v` reports `Ran 7 tests ... OK`.
- Observation: Availability and booking safety remain fully testable without external services.
  Evidence: the expanded suite reports `Ran 11 tests ... OK`, including deterministic 45-minute dental slots, stale-confirmation rejection, short-slot rejection, and idempotent booking retry.
- Observation: The frontend can be reviewed independently before backend integration without overstating product readiness.
  Evidence: every unconnected surface is labeled `Frontend demo`, `DEMO DATA`, `SAMPLE VIEW`, `Illustrative`, or `NOT RUN`; `npm test` reports two passing rendered-shell tests and `npm run lint` succeeds.
- Observation: The complete golden scheduling path is now testable without service keys or an LLM.
  Evidence: `make test-backend` reports 17 passing tests, including HTTP contract coverage and a four-turn conversation that preserves the Richmond constraint, accepts Mission District, offers duration-safe slots, asks for current-state confirmation, and performs one idempotent booking.
- Observation: A separate conversational voice LLM produced unnecessary questions that the deterministic engine would never select.
  Evidence: Replacing it with `ConversationService` removes improvised time questions, applies the server-owned earliest-time default, and sends the engine trace and extraction usage to the frontend after every committed voice turn.

## Decision Log

- Decision: Keep the LLM extraction result as a state patch rather than asking it to regenerate all state.
  Rationale: A patch prevents unrelated confirmed facts from being silently changed and makes every turn testable.
  Date/Author: 2026-08-18 / Codex and user.
- Decision: Keep hard constraints, soft preferences, valid candidates, and constraint-relaxation alternatives separate.
  Rationale: An alternative that violates a required location must receive the caller's permission and cannot be called valid.
  Date/Author: 2026-08-18 / Codex and user.
- Decision: Keep scheduling state, engine output, and usage telemetry as separate records joined by conversation and turn IDs.
  Rationale: This avoids sending debugging data back to the model and makes cost reporting honest.
  Date/Author: 2026-08-18 / Codex and user.
- Decision: Build the deterministic engine and text simulator before live voice integration.
  Rationale: Nearly all reliability tests can run without external API keys, while the text simulator provides a stable demo if voice services are unavailable.
  Date/Author: 2026-08-18 / Codex.
- Decision: Build the frontend in reviewable slices with explicit mock-data boundaries before wiring endpoints.
  Rationale: The user can evaluate information architecture, missing states, and graph-editing ergonomics now, while labels prevent scripted values from being mistaken for live scheduling behavior.
  Date/Author: 2026-08-19 / Codex and user.
- Decision: Introduce a deterministic local extractor behind the same turn contract planned for structured LLM extraction.
  Rationale: It makes the server-owned conversation, policy, trace, availability, and booking loop demonstrable without keys while allowing the extraction implementation to be replaced independently later.
  Date/Author: 2026-08-19 / Codex and user.
- Decision: Use `patient_request`, `pending_offer`, and `offer_id` instead of generic state/workflow version names.
  Rationale: These terms describe the domain directly. A stable request fingerprint invalidates an offer when scheduling facts change, while the offer ID is also the booking idempotency key.
  Date/Author: 2026-08-19 / Codex and user.
- Decision: Treat model intents as observations from the latest utterance and keep the conversation's current goal in deterministic code.
  Rationale: An informational question must not silently cancel an active booking task.
  Date/Author: 2026-08-19 / Codex and user.

## Outcomes & Retrospective

Implementation now includes one checked scheduling loop for voice and text. The embedded live call uses Pipecat end-of-speech detection, ElevenLabs transcription/speech, observation-only OpenAI extraction, and the deterministic scheduling engine. Each voice turn streams its validated patch, defaults, rule trace, next action, latency, and token usage to the Workbench. Semantic validation, exact requested-combination diagnostics, pending offers, earliest-time defaults, multi-candidate availability, confirmation rechecks, and idempotent booking are complete for the mocked scope. The graph editor remains a prototype, the frontend catalog is still a sample, evaluation reporting is not yet a product surface, and production persistence is intentionally out of scope.

## Context and Orientation

`backend/data/catalog.json` contains locations, providers, appointment types, and six booking policies. `backend/bot.py` creates the WebRTC, speech-to-text, language-model, and text-to-speech pipeline. `backend/agent_builder/schema.py` defines the JSON node graph, and `backend/agent_builder/builder.py` compiles it into Pipecat Flows nodes. `backend/example_flow.json` is the current demonstration graph.

The new scheduling package will live under `backend/scheduling/`. A scheduling state is the saved set of patient facts, requested service, provider and location requirements, time preferences, selected slot, and confirmation status. A state patch is only the information added, corrected, or cleared by one utterance. Entity resolution means matching text such as "Richmond" to a catalog ID such as `loc_007`. Eligibility means checking whether a complete appointment/provider/location combination obeys every catalog policy.

The new dashboard lives in `frontend/`. It will call small backend HTTP endpoints and display the structured results. The full trace is for the reviewer and is not placed into later model prompts.

## Scope and Priorities

The implementation is intentionally narrower than the production design. P0 is the reviewer-facing end-to-end proof; P1 is reliability coverage that may live primarily in tests; P2 is documented production follow-up. Do not delay a working P0 demo to implement P2 behavior.

**P0 — must work in the demo:** structured LLM extraction, a server-owned patient request and pending offers, entity resolution, deterministic policy checks, hard versus soft constraints, ambiguity, relaxation alternatives, deterministic mock availability, explicit confirmation, idempotent mock booking, per-message trace, token/latency display, browser voice call, text fallback, and a minimal valid graph editor.

**P1 — must be tested:** corrections and invalidation, duplicate names, unsupported services, slot-duration validation, idempotency, policy invariants, prompt-injection attempts, grounded response generation, and representative extraction failures.

**P2 — document as production follow-up unless time remains:** production concurrency and stale-catalog handling, clinic-approved urgency routing, sophisticated retry/failover behavior, repeated-action loop detection, broad conflicting-fact handling, and production persistence.

## LLM Context Contracts

The extraction model receives only the information required to understand the latest utterance: a compact system instruction, the exact structured-output schema, definitions for `UNKNOWN`, `REQUIRED`, `PREFERRED`, correction/clearing behavior, two to four representative few-shot examples, compact canonical scheduling state, the latest patient utterance, and only limited recent dialogue when needed for references such as “that doctor” or “instead.” It does **not** receive the full catalog, policy database, trace history, or the entire transcript.

Its responsibility is observation only. It returns `intent`, `state_patch`, and `corrections`; it never emits catalog IDs or authorizes eligibility, availability, or booking. The server validates the structured output before merging it.

The response-generation model receives a compact `ACTION_CONTEXT` containing the typed conversational action plus only the checked facts needed to say it naturally: relevant blocker explanations, approved candidate names, selected slots, or confirmed booking details. Its prompt explicitly forbids inventing providers, locations, policies, slots, or booking status and forbids changing the engine-selected action.

This gives the runtime boundary: probabilistic language understanding → deterministic request/resolution/policy/action selection → deterministic grounded response templates.

## Entity Resolution Strategy

Resolution uses a layered strategy. First use deterministic normalization, aliases, exact/prefix matching, and contextual filtering against the small catalog. Ambiguous matches remain ambiguous rather than being guessed. If a natural-language service description has no confident deterministic match, a semantic resolver may retrieve a small top-k set of catalog candidates; deterministic code still validates those IDs and owns all booking rules. The take-home does not require a vector database, but `solution.md` will explain how the retrieval stage can be replaced by indexed or embedding-based search as the catalog grows without putting the full catalog in the LLM prompt.

## Edge Case Coverage

The implementation and final documentation must account for all twenty failure cases discussed during design. The core demo will implement the cases that affect safe scheduling directly; lower-frequency operational cases may be demonstrated by tests or documented as production follow-up work, but none may be silently omitted.

1. Invalid LLM JSON is rejected by a schema validator, retried once, and then changed into a simple clarification or staff handoff.
2. Suspected speech-recognition mistakes produce a confirmation question rather than a weak automatic match. Only final transcripts update permanent state.
3. Duplicate provider or location names remain ambiguous until another fact selects one or the caller clarifies.
4. Unclear hard requirements versus preferences produce a focused question before alternatives are treated as acceptable.
5. Patient corrections support setting, clearing, and replacing facts. Changes clear incompatible candidates, slots, and confirmation.
6. Contradictory patient facts are marked as conflicting and require confirmation instead of silently choosing one.
7. Conflicting hard constraints return no valid candidate. Options that require relaxing a constraint are labeled separately and require permission.
8. Appointment types with no valid provider produce a truthful cannot-schedule result and optional staff handoff.
9. Symptom-only requests do not cause medical diagnosis. The agent asks for the known service or specialty and can hand off to staff.
10. Broken catalog records are found by startup integrity checks and excluded from booking.
11. Catalog decisions carry a catalog version and are checked again before booking so stale data cannot authorize a booking.
12. A slot that becomes unavailable is rechecked and replaced with new options; the agent does not claim success early.
13. Slot length is checked against appointment duration before the slot is offered.
14. Booking writes use an idempotency key so retries cannot create duplicate appointments.
15. Confirmation is tied to one state version. Material changes cancel old confirmation and require confirmation again.
16. LLM, speech, availability, and booking service failures use timeouts and safe retries, then fall back to text or staff help without claiming a booking.
17. Natural-language responses may use only facts supplied by the checked engine result; invented providers, policies, slots, and bookings are rejected in evaluation.
18. Long conversations use compact server-side state, limited recent dialogue, retry limits, and repeated-action detection to control cost and prevent loops.
19. Multiple intents can be recorded in one turn so an information answer does not erase an active booking task.
20. Caller instructions cannot bypass policy checks because only the deterministic engine can authorize eligibility and booking.

Healthcare urgency handling is an additional safety guard, separate from the twenty cases above. The production design requires a clinic-approved urgent-care or emergency script and staff escalation path; the scheduling model must not diagnose the caller.

## Evaluation Design

Evaluation is separated by failure boundary rather than reported only as one end-to-end score. Extraction fixtures map utterances plus compact prior state to expected state patches. Engine fixtures map canonical states to expected resolutions, checks, blockers, candidates, and typed actions. Conversation fixtures map multi-turn utterance sequences to expected final outcomes.

The report will show at least structured extraction accuracy, entity-resolution accuracy, deterministic policy/action correctness, end-to-end task success, invalid booking rate, unnecessary-question rate where practical, tokens per successful booking, estimated cost, and latency. Prompt-injection fixtures such as requests to “ignore the rules” or fabricate referral status must demonstrate that caller text cannot bypass deterministic policy checks. Response-grounding evaluation verifies that generated speech contains no provider, policy, slot, or booking fact absent from `ACTION_CONTEXT`.

## Golden Demo Path

The primary reviewer path is deliberately fixed so the architecture is understandable within the first minute. The caller asks for a Dental Cleaning as a new patient, requires Richmond, prefers Dr. Wei Lee, and wants the earliest appointment. The UI shows extraction, resolution, individual policy checks, the Richmond capability failure, zero exact candidates, and a Mission District relaxation alternative requiring consent. After the caller accepts Mission District, the same loop queries deterministic availability, offers a duration-compatible slot, requests explicit confirmation, executes one idempotent mock booking, and reports success. Duplicate-provider ambiguity and an unsupported service are secondary one-click scenarios.

## Baseline and Performance Measurement

The baseline is defined as a model call that receives the same system task, the entire `catalog.json`, relevant conversation history, and the latest utterance and is asked to determine the next scheduling action. The proposed system instead sends compact state plus the latest utterance to extraction, performs catalog lookup and policy evaluation outside the LLM, and sends only checked action context to verbalization. Both strategies will be measured on the same representative evaluation cases; no savings or accuracy numbers will be guessed.

Latency is recorded per stage. The implementation will optimize for sub-second extraction/response calls where the configured services permit it and near-negligible deterministic engine time; measured values, not aspirational numbers, will appear in the final report.

## Persistence and Ownership

Per conversation, the server owns the `SchedulingRequest`, current goal, pending offer, and confirmed booking. A pending offer carries an `offer_id`, patient-request fingerprint, catalog content hash, and server-owned options. Per message, the service returns the validated patch, engine result, safe trace summary, and extraction usage. Production persistence is intentionally out of scope.

## Plan of Work

First, add plain Python data models for known, unknown, and conflicting patient facts; required versus preferred entity requests; resolved, ambiguous, and unresolved entity matches; workflow status; selected candidates; and confirmation. Add a catalog repository that validates IDs and references when it loads `backend/data/catalog.json`.

Next, implement the engine as small pure functions. Entity resolution will normalize names and return multiple candidates rather than silently choosing duplicate names. Eligibility will check referral status, new-patient rules, provider appointment support, provider location membership, and required location capability. Ranking will first require all hard rules and then compare named preferences and earliest availability. When a required constraint prevents a match, the engine will return separate relaxation alternatives.

Then add a deterministic mock availability service. Given the same provider, location, appointment type, and date range, it will return the same slots so tests and demos are repeatable. Every slot must be at least as long as the appointment type. Mock booking will require a selected eligible candidate, a still-available slot, explicit confirmation of the current state version, and an idempotency key that prevents duplicate bookings.

Add tests before the HTTP interface. Table-driven cases will cover every policy plus duplicate names, similar location names, unsupported services, changes that invalidate old selections, and unavailable slots. Property-style checks will assert that every returned eligible candidate satisfies all rules. Evaluation fixtures will be split into extraction, engine, and multi-turn conversation cases so failures can be attributed to the correct layer without requiring exact response wording.

Add backend endpoints that accept a text utterance, apply either a real structured LLM extraction or a predictable local extractor used for tests, run the engine loop, and return a compact response plus a trace ID. Separate endpoints will return the full trace, current state, catalog summaries, agent graph, and evaluation report. Usage records will store model name, input, cached input, output and reasoning tokens when available, duration, versioned price, and estimated cost. Speech-to-text and text-to-speech usage will be shown separately.

Replace the frontend loading skeleton with a scheduling workbench. The main area will contain the transcript and a test-call control. The decision area will show the state change, entity matches, policy checks, candidate funnel, blockers, availability, and next action for each utterance. A usage area will show per-stage and cumulative tokens, latency, cost, and full-catalog baseline comparison. The graph area will load and edit the declarative agent nodes and edges while preserving JSON validation.

Finally, keep the shared voice adapter observable: every committed transcript calls the deterministic scheduling service, every checked response goes to TTS, and every state/trace/usage result is streamed to the dashboard. Voice failures must not prevent the text demo from working.

## Concrete Steps

Work from the repository root `/Users/karapetyans/Desktop/prosper-challenge-2`.

Create the backend environment with Python 3.12 and install requirements. Run backend tests with:

    make test-backend

Run the dashboard locally from `frontend/` with:

    npm run dev

Run the text scheduling API from the repository root with:

    make api

Build the dashboard with:

    npm run build

Validate the frontend shell with:

    npm test
    npm run lint

Run the voice agent only after `backend/.env` contains `OPENAI_API_KEY` and `ELEVENLABS_API_KEY`:

    make run

The plan will be updated with exact endpoint commands and expected outputs as those interfaces are added.

## Validation and Acceptance

The exact dental example must resolve Dental Cleaning, Dr. Wei Lee, and Richmond Care Center; pass the new-patient, referral, provider-service, and provider-location checks; fail the dental-capability check; return no valid candidate under the required Richmond constraint; and offer Mission District only as an alternative requiring patient permission.

Changing Richmond from required to preferred must make Mission District eligible. A duplicate name such as Dr. Linda Ramirez must produce an ambiguity until the specialty or service resolves it. Physical Therapy Evaluation must return no provider rather than inventing one. Every offered slot must match the appointment duration. Sending the same booking request twice with one idempotency key must create only one booking.

The dashboard must show these facts after each text turn without external keys. With keys present, the embedded browser call must show both transcript roles, play the checked assistant response, and update scheduling traces and extraction usage after every voice turn. The evaluation report must show structured extraction accuracy, task success, invalid booking rate, tokens per successful booking, estimated cost, and the full-catalog baseline.

## Idempotence and Recovery

Catalog loading, evaluation, and mock slot generation are repeatable. Any change to appointment type, provider, location, time, or primary preference changes the request fingerprint and invalidates the pending offer. Booking retries use the confirmed `offer_id` as an idempotency key. If a model or voice service is unavailable, local structured extraction and deterministic tests remain usable.

## Artifacts and Notes

The initial catalog has 8 locations, 50 providers, 82 appointment types, and 6 policy strings. The full JSON is approximately 45 KB, which is intentionally too large to send on every conversation turn. Exact token and price comparisons will be measured rather than guessed.

## Interfaces and Dependencies

The scheduling engine will expose one main operation equivalent to:

    process_turn(current_state, state_patch, catalog, availability) -> TurnResult

`TurnResult` will contain the new state version, resolution results, named policy checks, blockers, eligible candidates, relaxation alternatives, availability status, and one typed next action. Internal operations such as querying availability and executing a booking remain separate from conversational actions such as asking for a field, offering alternatives, offering slots, and requesting confirmation.

The implementation will prefer standard Python and the web packages already included by the Pipecat runner. Additional dependencies will be added only when they remove substantial custom code. Frontend dependencies will remain within `frontend/package.json`.

Plan revision note (2026-08-18): Initial plan created after repository and catalog analysis. It records the agreed state-patch, rule-engine, observable-loop, and real-time-cost design before product code is added.

Plan revision note (2026-08-18): Added explicit coverage for all twenty discussed failure cases plus separate healthcare urgency handling after the user requested confirmation that no gap was omitted.

Plan revision note (2026-08-18): Adopted the user-provided plan as the implementation source of truth, adding its explicit P0/P1/P2 scope, LLM context contracts, entity-resolution strategy, evaluation design, golden demo, baseline measurement, and persistence boundaries.

Plan revision note (2026-08-19): Completed the first frontend product slice, recorded its deliberate mock-data boundaries and validation evidence, and corrected the repository working path before backend integration begins.

Plan revision note (2026-08-19): Connected the frontend workbench to a server-owned deterministic text conversation loop, added the HTTP contract and full golden-path coverage, and kept voice, graph persistence, catalog APIs, and LLM extraction explicitly out of this slice.

Plan revision note (2026-08-19): Replaced the redirect to Pipecat's prebuilt client with an embedded WebRTC call panel and added a direct end-of-speech → LLM → TTS path. The deterministic scheduler remains separate and the voice prompt cannot claim booking success until tool integration is implemented.

Plan revision note (2026-08-19): Removed the separate conversational voice LLM after live testing exposed improvised follow-up questions. Committed voice turns now use the shared structured-extraction and deterministic scheduling service, default unstated time to earliest availability, and publish state, trace, action, latency, and token usage to the main Workbench.
