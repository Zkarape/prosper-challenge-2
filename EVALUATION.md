# Scheduling evaluation

## The simple explanation

We keep 40 patient examples with expected results. Cases 001 and 002 are
manually reviewed. The other 38 expected results are useful drafts, but should
not be called a gold set until a person reviews them. Each example goes through
the same code path as a real patient turn:

1. **Extraction** — understand the latest patient words as typed facts.
2. **Validation** — reject facts that are unsupported or unsafe.
3. **State update** — apply only validated changes to the scheduling request.
4. **Engine** — resolve the catalog and apply deterministic scheduling rules.

A case passes only when all four stages pass. The grader uses normal code
comparisons. It does not ask another LLM whether the answer looks good.

In the frontend, open **Evaluations**. Run one case first so every stage can be
inspected. Then run all 40. Failed cases stay red and show the exact expected and
actual values.

The **Structured LLM** option measures the configured production extractor and
reports its tokens and estimated cost. The **Local smoke test** makes no model
calls. It checks that the pipeline is connected, but it is not evidence of LLM
quality.

The evaluation API is:

```text
GET  /api/evaluations/dataset
POST /api/evaluations/runs
GET  /api/evaluations/runs/latest
GET  /api/evaluations/runs/{run_id}
```

Example request for one case:

```json
{
  "case_ids": ["case_001"],
  "extractor": "configured"
}
```

Use `case_ids: null` to run all 40. `POST` returns a background job immediately;
the frontend polls its `run_id` until it finishes. The latest ten runs are kept
in backend memory for inspection and can be exported as JSON from the frontend.
They are cleared when the backend restarts. A multi-worker deployment should
move these jobs and results to a shared queue and database.

## Conversation efficiency

The unit of efficiency is a completed conversation, not an individual request.
Failed and abandoned conversations remain in the token numerator because they
still consumed model calls.

## Context strategy comparison

The Evaluations page also contains a paired experiment for the extractor. It runs
the same five multi-turn conversations with the same model, initial state, schema,
catalog and deterministic engine. Only the history window changes:

1. `compact` sends the latest utterance, structured request and pending offer.
2. `bounded_recent` adds the last patient/assistant exchange.
3. `full_history` adds every prior patient and assistant message.

Each conversation is repeated three times. Tokens come from the provider response;
local token estimates cannot produce a proof run. Accuracy is graded from the final
typed request, engine decision, next action and booking status. Failed trials stay
in the token totals. The runner also reports a 95% Wilson interval and a paired
exact p-value so a small sample cannot masquerade as statistical certainty.

The final-code run `context_eval_c079bf2575a5` on `gpt-5.4-mini` measured:

| Strategy | Passed trials | Input tokens | Total tokens | Estimated cost |
| --- | ---: | ---: | ---: | ---: |
| State only | 15/15 | 158,891 | 173,981 | $0.15960 |
| Recent exchange | 13/15 | 167,419 | 182,910 | $0.17246 |
| Full history | 13/15 | 173,288 | 188,658 | $0.16889 |

Against full history, the selected recent-exchange strategy used 5,869 fewer input
tokens (3.39%) and 5,748 fewer total tokens (3.05%), with the same observed 13/15
accuracy. This proves token reduction for this run. It does not prove lower cost:
different cached-token totals made recent context about $0.0036 more expensive.
State only used 8.31% fewer input tokens than full history and passed 15/15 in this
run, but it failed trials in both preceding repeated runs.

The honest result is that full history costs more input tokens without showing an
accuracy advantage here. The benchmark is too small and variable to prove that
either compact alternative preserves accuracy universally. Bounded recent context
remains the conservative production default because it retains short references
at a fixed cost, but that choice is provisional. The next evidence step is a
larger, independently reviewed set of long conversations and production traces.
Cost comparisons must also be repeated because provider prompt caching varies.

The comparison API is:

```text
GET  /api/evaluations/context-comparison/dataset
POST /api/evaluations/context-comparison/runs
GET  /api/evaluations/context-comparison/runs/latest
GET  /api/evaluations/context-comparison/runs/{run_id}
```

## Where to inspect the data

In Supabase, open **Table Editor**:

- `conversations` — lifecycle, intent, final outcome and safety result;
- `conversation_turns` — each committed patient turn and its complete diagnostic
  response;
- `usage_events` — one row per actual model call;
- `conversation_evaluations` — a view that totals calls, tokens, cost and latency
  for each conversation;
- `bookings` — authoritative confirmed booking records.

The same information is available from the backend:

```text
GET /api/conversations/{conversation_id}/evaluation
GET /api/evaluations/summary
POST /api/conversations/{conversation_id}/end
```

Voice calls are finalized automatically when they disconnect. Confirmed bookings
and staff handoffs are finalized immediately. The text API exposes `/end` so a test
runner can explicitly close an incomplete or information-only conversation.

## Useful Supabase SQL

Run these in **SQL Editor**:

```sql
select *
from conversation_evaluations
order by started_at desc;
```

```sql
select conversation_id, turn_id, stage, model,
       input_tokens, cached_input_tokens, output_tokens, total_tokens,
       estimated_cost_usd, latency_ms
from usage_events
order by created_at desc;
```

```sql
select conversation_id, status, outcome, safe, intent,
       message_number, created_at, ended_at
from conversations
order by created_at desc;
```

## Current stage coverage

Production turns can contain two measured LLM stages. `EXTRACTION` proposes a
typed state change. After validation and deterministic scheduling,
`RESPONSE_WRITING` phrases the checked response plan for the patient. Each real
provider call records a separate `UsageEvent` under the same conversation and
turn. If response writing is disabled or fails, the checked deterministic wording
is used and no model usage is invented.

## Cost snapshot

Token counts come from the provider response and are authoritative. Cost uses the
price snapshot in `backend/conversation/telemetry.py`. Unknown models retain their
token usage but report `estimated_cost_usd = null` instead of using a guessed rate.
