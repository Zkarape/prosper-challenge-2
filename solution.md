# Solution overview

## Voice agent builder

The Phase 1 editor loads the versioned workflow in `backend/example_flow.json` through
the scheduling API. It provides a draggable canvas, six explicit node types, four edge
types, scoped tool assignment, a focused node inspector, validation, and atomic saving.
The example now describes the actual safe scheduling architecture rather than a generic
four-step appointment script. A new Pipecat call loads the saved first message and
ElevenLabs voice, while runtime-stage metadata connects live call traces back to nodes.

The graph orchestrates conversation and capability scope; it cannot override the
deterministic policy and booking boundary described below.

The scheduling agent uses an LLM for one narrow job: report what the patient said in a
strict schema. Application code validates those observations, resolves raw phrases against
the clinic catalog, evaluates policy, ranks candidates, checks availability, and writes a
booking. The full catalog is never placed in the extraction prompt.

## Deterministic text path

Typed messages call `ConversationService`:

```text
patient text
  -> strict extraction (OpenAI or local test implementation)
  -> semantic validation
  -> patient-request update
  -> deterministic resolution and rule results
  -> pending offer / availability / booking action
  -> grounded response
```

## Embedded live-voice path

Pipecat provides WebRTC audio transport, local Silero VAD, and semantic turn detection.
ElevenLabs may commit multiple STT fragments, but the voice adapter buffers them until
Pipecat declares the patient’s thought complete. That one authoritative turn calls the same `ConversationService`
as typed messages: OpenAI performs observation-only extraction, deterministic code
applies defaults and scheduling rules, and ElevenLabs speaks only the checked response.
RTVI server messages stream the resulting state patch, rule trace, selected action,
latency, and extraction-token usage to the main Agent Studio Workbench. The recording
control is the primary action and does not open a modal, page, or browser window.

## Reliability choices

- A named provider or location is `UNSPECIFIED` unless the patient clearly says required
  or preferred.
- Requested combinations are validated before valid candidates are built, so an invalid
  request retains a precise proof and a useful alternative.
- Every selectable alternative, slot list, and confirmation is a server-owned pending
  offer. The model returns only accept, reject, or a one-based selection.
- Offers carry a patient-request fingerprint and catalog content hash. A changed request,
  catalog, eligibility result, or unavailable slot prevents booking.
- The booking service is idempotent on `offer_id`. The agent says confirmed only after it
  receives `status=confirmed`, a `booking_id`, and the matching `offer_id`.
- Timezone comes from the resolved clinic location, not the extractor.
- Traces expose operation summaries by default rather than model evidence or transcripts.

Structured Outputs ensure the response follows the supplied schema; semantic validation
is still required because schema adherence does not prove that extracted facts are true.
See the [official OpenAI Structured Outputs documentation](https://developers.openai.com/api/docs/guides/structured-outputs).

## Scope

Availability and booking are deterministic in-memory mocks because the challenge does not
include a real scheduling system. The interfaces and validation boundaries are explicit so
they can be replaced by production services without changing extraction authority.
