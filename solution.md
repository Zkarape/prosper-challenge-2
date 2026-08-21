# Prosper healthcare scheduling agent

## Overview

I built a voice scheduling agent and a small studio for testing, editing, and
understanding it. A clinic operator can edit the agent as a graph, start a voice
call from the same screen, watch each scheduling decision, inspect token use and
logs, and run repeatable evaluations.

The main problem was not speech. It was deciding how much authority to give the
language model. A patient speaks in flexible language, but a clinic has exact
appointment types, provider relationships, locations, and booking rules. Asking
one model to understand the patient, search the catalog, apply policy, and confirm
a booking would be simple to prototype, but difficult to trust.

My solution separates those jobs:

```text
microphone
  -> Pipecat voice pipeline
  -> final patient utterance
  -> LLM extracts patient-stated facts
  -> application validates those facts
  -> deterministic code resolves the catalog and applies clinic rules
  -> availability is searched
  -> patient confirms an exact offer
  -> booking system confirms the booking
  -> LLM phrases the checked response plan
  -> ElevenLabs speaks that exact text
```

The LLM understands language. It does not decide what the clinic allows. This is
the central architectural decision in the project.

## A request moving through the system

Suppose a caller says:

> “I am a new patient and need the earliest knee MRI.”

First, Pipecat receives the browser microphone over WebRTC. ElevenLabs produces
transcript fragments, while local voice activity detection decides when the
patient has stopped speaking. Only the completed utterance enters the scheduling
system. Interim words may appear while the patient speaks, but they cannot change
the saved request.

Next, the extractor receives the latest utterance, the current structured patient
request, the current pending offer if one exists, and one recent patient/assistant
exchange. It does not receive the full conversation, clinic catalog, or policy
list. OpenAI Structured Outputs turns the sentence into a typed proposal such as
`patient_status = NEW`, `appointment_type = "knee MRI"`, and
`time = EARLIEST_AVAILABLE`. Each changed fact includes evidence from the latest
utterance.

That proposal is still untrusted. Application code checks its evidence, rejects
unsupported changes and internal IDs, and makes one bounded correction attempt
when the structured extraction is semantically invalid. Only the validated patch
can update the patient request.

The catalog resolver then matches the patient's wording to official clinic
records. It can return exactly one match, several possible matches, or no match.
It never silently chooses between ambiguous records. The scheduling engine applies
eligibility, referral, provider, location, and capability rules in a fixed order.
It asks only for an unknown fact that can change the result. If the request is
impossible, it keeps the exact reason and can offer the smallest safe alternative
instead of continuing with irrelevant questions.

If the request is eligible, the availability adapter returns slots and the server
creates a pending offer. The offer contains server-owned option IDs and is tied to
a fingerprint of the current request and a hash of the current catalog. This is
what gives short replies such as “yes” or “the first one” a precise meaning. If the
patient changes a material fact, the fingerprint changes and the old offer can no
longer be confirmed.

Finally, an appointment is considered booked only when the booking adapter returns
`confirmed` with the matching offer, candidate, and slot. An assistant message is
never treated as proof of success. The booking path is idempotent, so retrying the
same confirmed request returns the same booking rather than creating a duplicate.

The engine then creates the authoritative response plan. A second, small LLM call
turns that plan into one or two natural sentences. It has no booking tools and is
not allowed to change the decision, slot, provider, location, policy result, or
booking state. Application code rejects unsafe output, such as a false booking
claim, and falls back to the checked deterministic sentence if the call fails or
breaks the contract. The accepted text is both displayed in the studio and sent
unchanged to ElevenLabs text-to-speech.

## Why I did not use RAG for the clinic catalog

This catalog is structured operational data, not a collection of documents. Its
relationships and rules must be applied exactly. Semantic retrieval could be
useful for finding a small set of likely records in a very large catalog, but it
should not decide eligibility or silently remove alternatives.

For this challenge I use deterministic normalization, exact names, aliases,
prefixes, token matching, and relationship filtering. The model preserves raw
patient wording; application code maps that wording to IDs. This gives three useful
properties:

- the catalog and policies are not repeated in every model prompt;
- the same request produces the same candidates and rule results;
- every ambiguity or rejection can be explained from clinic data.

If the catalog became too large for the current in-memory indexes, I would move
candidate retrieval to indexed PostgreSQL queries or a search service. That would
change how candidate IDs are found, but not the trust boundary: deterministic code
would still verify every relationship and policy before scheduling.

## The patient request is the primary context

I chose a compact, typed patient request plus one recent exchange instead of
replaying the full transcript on every turn. The request remembers the current
goal, patient and referral status, raw appointment wording, provider and location
requirements, and time preference. Corrections are explicit operations: set,
replace, clear, or keep.

This keeps prompt growth bounded by useful scheduling facts and one exchange,
rather than the full length of the call. The server-authored pending offer gives
short answers such as “yes” a precise meaning. It also makes the logic portable:
voice calls, text API requests, and evaluation cases all call the same
`ConversationService` instead of maintaining separate versions of the scheduling
behavior.

The trade-off is measurable but not settled. In the final repeated run, bounded
recent context used 3.39% fewer input tokens than full history and both passed
13/15 trials. State alone used 8.31% fewer input tokens and passed 15/15, but it
failed cases in the preceding runs. Prompt caching also meant fewer input tokens
did not produce lower estimated cost every time. I keep the bounded middle option
as a provisional safety margin for short references. The experiment proves token
reduction for these calls; five draft conversations are not enough to prove
universal accuracy or cost savings.

## The deterministic engine

The engine follows a first-action-wins order:

1. Resolve the requested appointment, provider, and location.
2. Ask for clarification if an identity is missing or ambiguous.
3. Check decisive patient restrictions before asking more questions.
4. Ask for one unknown fact only when it can change eligibility.
5. Apply referral rules.
6. Build valid appointment, provider, and location combinations.
7. Search availability for exact matches.
8. Ask permission before relaxing a named preference or requirement.
9. Block or hand off when no safe path remains.

The engine returns typed decisions, candidates, rule results, blocker codes, and
one next action. The response-writing LLM phrases that checked result but cannot
change it. This keeps the conversation natural while making the business outcome
repeatable and testable.

## Voice and turn handling

The voice layer uses Pipecat with WebRTC, ElevenLabs speech-to-text and
text-to-speech, and local Silero voice activity detection. ElevenLabs transcription
is committed from the local speech-stop event so the system does not wait for a
second provider-side end-of-turn decision. Completed turns enter a sequential
background queue. The model and database work therefore do not block audio, VAD,
or transcript frames, while patient turns still remain ordered.

Voice is an adapter around the scheduling core. Replacing WebRTC, STT, or TTS does
not require rewriting extraction, policy, availability, or booking logic.

The response writer makes the latency trade-off visible. In a three-turn live
sample, extraction took a median 2.19 seconds, response writing 0.99 seconds, and
the complete scheduling service 2.88 seconds. A separate cold ElevenLabs streaming
request produced its first audio in 0.69 seconds. With the configured 0.45-second
speech-stop window, the estimated median time from the patient's final word to
first assistant audio is therefore about 4.0 seconds. These are development
measurements, not a production SLA; provider load, network location, warm
connections, and response length will move them. Streaming the checked response
writer into TTS is the clearest next latency optimization.

## Why Pipecat does not own the scheduling context

Pipecat also provides `LLMContext`, context aggregators, system instructions and
function calling. I deliberately do not use its default context loop in the
current runtime. That loop is designed for a conversational LLM placed directly
inside the Pipecat pipeline: transcriptions are appended as user messages, model
output is appended as assistant messages, and function calls and their results
become part of the same history.

This application has a different boundary. Pipecat completes the spoken turn and
passes one utterance to `ConversationService`. The service calls a structured
extractor, validates its proposal, updates the durable patient request, runs policy
code, and asks a bounded response writer to phrase the checked result. The writer
is an application service, not a free-running conversational model inside the
Pipecat pipeline. Adding the default context aggregator would therefore keep a
second transcript-shaped context beside the PostgreSQL conversation and typed
scheduling request. It would not replace validation or deterministic policy, and
the two copies could disagree after retries, restarts or non-voice API calls.

For the same reason, the extraction instruction lives with the extractor rather
than in Pipecat's `system_instruction`, and the model is not given Pipecat booking
functions to choose from. Booking is an application decision reached only after
eligibility, availability and pending-offer checks. Pipecat function calling would
improve orchestration ergonomics, but it would not make an LLM-selected booking
action authoritative.

This decision has a cost. I maintain the recent-context window, model-call
telemetry and tool boundary myself, and the two model calls are sequential. I also
do not automatically receive Pipecat's cross-provider message conversion,
token-streamed LLM-to-TTS handoff, spoken-response aggregation, context
summarization or interruption-aware function handling.

If first-audio latency becomes the dominant problem, I would stream the bounded
response writer through Pipecat and let TTS begin at the first complete sentence.
I would also adopt more of Pipecat's context features when the voice layer gains
multiple LLM-driven conversation nodes or long-running informational tools.
Pipecat would then own the short-lived spoken conversation:
the system prompt would use the LLM service's `system_instruction`, aggregators
would record what the patient said and what TTS actually spoke, and registered
functions would delegate to the existing checked application services. The typed
patient request and PostgreSQL records would remain the durable source of truth.
In other words, Pipecat context can become the conversational layer without
becoming the clinic policy engine.

## Agent graph and studio

The Agent graph tab edits a declarative JSON workflow. It supports conversation,
subagent, tool, decision, handoff, and end nodes; typed edges; node positioning;
scoped tools; and validation before saving. `AgentBuilder` can compile this format
into Pipecat Flows nodes.

For the current live scheduling demo, the saved graph supplies agent metadata such
as the first message and visually maps real runtime stages. The scheduling loop
itself is owned by `ConversationService` and `SchedulingEngine`, not by
LLM-selected graph transitions. I chose this boundary because clinic eligibility
and booking must not change when someone edits a conversational prompt. A fuller
product would compile graph tool nodes into calls to the same deterministic
services, while keeping policy nodes read-only or separately permissioned.

The rest of the studio exists to make the system inspectable rather than merely
flashy:

- **Scheduling agent** runs the complete voice pipeline in the application and
  shows the live conversation and per-turn context usage.
- **Agent graph** edits the workflow and highlights stages used by the latest
  request.
- **Engine logic** explains each trust boundary and the exact decision order.
- **Evaluations** runs individual cases or the complete dataset through the real
  pipeline and shows the first failing stage.
- **System logs** follows correlated voice, API, scheduling, and booking events
  without logging patient wording by default.

The UI also supports light, dark, and system themes so the diagnostic information
remains readable in different environments.

## Storage, concurrency, and scaling

The zero-setup mode keeps state in memory. When `DATABASE_URL` is configured, the
same storage interface uses PostgreSQL; I used Supabase as the managed deployment
option. PostgreSQL stores conversations, the current patient request, turns,
pending offers, catalog snapshots, agent publications, bookings, model usage, and
evaluation runs.

This makes API workers replaceable: any worker can continue a conversation, so the
HTTP scheduling API does not require sticky sessions. A short database processing
claim prevents two workers from processing the same conversation turn at once.
The claim releases its connection before the LLM call, which avoids tying the
database pool size to model concurrency. Unique constraints on message IDs, offer
IDs, and slot IDs protect retries and duplicate bookings at the final storage
boundary.

A WebRTC call remains attached to one voice worker for the life of that connection,
which is expected. More workers can sit behind a load balancer, while Supabase's
transaction pooler controls database connections. The catalog remains cached in
memory because it is small and immutable; every conversation stores its content
hash so decisions can be reproduced after the catalog changes.

I did not add Redis, Kafka, Kubernetes, or a custom cache. PostgreSQL already solves
the immediate shared-state and concurrency problems. Those components should be
added only after production measurements show a specific bottleneck. Similarly,
the repository contains an application-path concurrency runner, but it is not
presented as proof of 100 simultaneous voice calls because it does not include
provider quotas, audio connections, or production network behavior.

## Evaluation and observability

I built evaluation into the product because an architecture is not useful if its
decisions cannot be checked. Forty cases exercise extraction, semantic validation,
state updates, and engine decisions through the same service used by real calls.
The grader uses ordinary comparisons rather than another model, so failures are
repeatable and show the first broken boundary.

The first full run passed 4 of 40 cases. That result exposed broad candidate
filtering, weak ambiguous-name handling, inconsistent yes/no behavior, irrelevant
questions, and unstable blocker codes. I converted those patterns into catalog
aliases, engine ordering rules, validation rules, pending-offer behavior, and
meaning-based grader comparisons. The final regression run passes all 40 cases.
The current backend suite runs 88 tests: 86 pass and 2 PostgreSQL integration
tests are skipped unless a test database URL is supplied.

This is a regression suite, not a claim of perfect real-world accuracy. Only the
first two expected cases were manually reviewed; the other expected results began
as drafts and were refined while debugging. Before production, clinic and safety
reviewers should approve a larger frozen test set, including noisy speech,
adversarial inputs, interruptions, provider failures, and real catalog changes.

Every actual model call records its stage, model, provider token counts, cached
tokens, estimated cost, and latency under one conversation ID. The final
conversation records whether it ended in a confirmed booking, correct block,
answered question, handoff, abandonment, or system error. This allows the product
to measure total tokens per safely completed conversation rather than optimizing
one cheap request while accidentally asking many extra questions.

Structured logs carry request, conversation, and turn IDs across the browser,
voice worker, API, scheduler, and booking adapter. Patient transcript text is
excluded from operational logs by default; durable conversation data and
operational diagnostics remain separate.

## Scope and production seams

Availability is deterministic mock data because the challenge provides no real
calendar. The booking adapter has a production-shaped contract—stable slots,
request and catalog checks, idempotency, and a confirmed response—but it does not
connect to an EHR or practice-management system. Replacing the mock requires a new
adapter, not a new scheduling engine.

Authentication is also intentionally absent. Patient calls use opaque conversation
IDs, and the local studio is a development tool. Before exposing agent or catalog
editing, I would add staff authentication and authorization. Before storing real
patient health information, I would also add the required consent, retention,
encryption, audit, vendor agreements, and healthcare compliance controls.

These omissions are deliberate. The project focuses on the hard part of the
challenge: turning uncertain patient language into a small trusted request, then
navigating a large, messy clinic catalog with decisions that are cheap,
explainable, and safe to repeat.

## Running the demo

After configuring `OPENAI_API_KEY` and `ELEVENLABS_API_KEY`, install the
dependencies once with `make install`. `DATABASE_URL` is optional; when it is
set, apply the schema once with `make db-migrate`. Then run the three services in
separate terminals:

```bash
make api
make run
make frontend
```

Open `http://localhost:3001`, start a call in **Scheduling agent**, and inspect the
same request in **Agent graph**, **Engine logic**, **Evaluations**, and **System
logs**.
