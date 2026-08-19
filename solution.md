# Solution overview

The scheduling agent uses an LLM for one narrow job: report what the patient said in a
strict schema. Application code validates those observations, resolves raw phrases against
the clinic catalog, evaluates policy, ranks candidates, checks availability, and writes a
booking. The full catalog is never placed in the extraction prompt.

## Shared turn path

Typed and spoken messages both call `ConversationService`:

```text
patient text
  -> strict extraction (OpenAI or local test implementation)
  -> semantic validation
  -> patient-request update
  -> deterministic resolution and rule results
  -> pending offer / availability / booking action
  -> grounded response
```

Pipecat provides WebRTC audio transport. ElevenLabs produces the final transcript and
speaks the checked response. It cannot bypass the scheduling service.

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
