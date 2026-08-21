# Prosper Challenge — Context Management

Voice AI for healthcare scheduling with an embedded live-call tester, observation-only LLM extraction, and a deterministic scheduling engine.

- **Phase 1** — a UI to edit the node graph and place a test call.
- **Phase 2** — a context-management approach so a scheduling agent can reliably and cost-effectively navigate a large catalog of locations, doctors, and appointment types.

```
browser mic -> Pipecat WebRTC -> ElevenLabs STT fragments
    -> local VAD + semantic turn detection -> completed patient turn
    -> structured LLM extraction -> deterministic scheduler
    -> checked response plan -> LLM response writer
    -> ElevenLabs TTS -> browser audio
```

The live call is rendered inside Prosper Agent Studio. The user is never redirected to Pipecat's prebuilt client.

Voice and text use the same `ConversationService`. The extraction LLM reports only
patient-stated facts; application code applies defaults, resolves the catalog,
checks policy and availability, owns pending offers, and confirms a booking only
after the mock booking system succeeds. A separate response-writing LLM turns the
checked result into natural patient-facing text. That exact text is shown in the
conversation and pronounced by ElevenLabs.

The **Agent graph** tab is a real workflow editor backed by
`backend/example_flow.json`: nodes can be added, positioned, configured, connected,
validated, and saved. See [PHASE1_WORKFLOW.md](PHASE1_WORKFLOW.md) for the node, edge,
and tool contracts and a short demo walkthrough.

## Quickstart

Requires **Python 3.11+**, Node 22+, `OPENAI_API_KEY`, and `ELEVENLABS_API_KEY`. Copy `backend/.env.example` to `backend/.env`, fill in the keys, then run these in separate terminals from the repo root:

```bash
make install
make run
```

```bash
make api
```

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:3001`, press the recording button under **Scheduling agent**, and allow microphone access. Speak naturally and pause when finished. Pipecat combines STT fragments until the patient’s thought is complete, then sends one authoritative turn to the scheduling pipeline. Both sides of that committed conversation appear on the same screen while the assistant audio plays there.

After a processed turn, open **Agent graph** to see which stages ran and inspect their validated inputs, decisions, token usage, and booking result.

Open **System logs** to follow correlated API, scheduling, booking, voice, and
browser-error events. Local JSONL files live in `backend/.logs`; `make logs`
follows them in a terminal. See [LOGGING.md](LOGGING.md) for filters, privacy
defaults, and production stdout settings.

By default the demo uses process-local memory. For durable conversations and safe
multi-worker booking, follow [SCALING.md](SCALING.md) to start PostgreSQL and set
`DATABASE_URL`.

Conversation-level token, cost, latency and outcome evaluation is documented in
[EVALUATION.md](EVALUATION.md). The ledger records both extraction and response-
writing calls under the same conversation and turn.

## Scheduling API

The deterministic text scheduling loop runs without OpenAI or ElevenLabs keys:

```bash
make install
make api
```

The text API remains available at `http://127.0.0.1:8000` for automated tests and direct API debugging. The product UI connects directly to the Pipecat runner at `http://127.0.0.1:7860`; it never opens another page or window.

`EXTRACTOR_MODE=auto` uses OpenAI Structured Outputs when `OPENAI_API_KEY` is
available and the local structured fallback otherwise. The extraction model is
configured by `EXTRACTION_MODEL`; `RESPONSE_MODEL` controls the model that phrases
the checked response. When OpenAI is unavailable, the checked deterministic text
remains a safe fallback.

## Large-catalog retrieval demo

Open **Catalog retrieval** in the UI to upload a catalog JSON file and search it.
The app validates the catalog, builds a small in-memory lexical index, and shows
the matching candidates plus retrieval time. This keeps the request sent to the
LLM small: retrieval suggests records, while the deterministic scheduler still
checks relationships, policy, availability, and booking safety.

The demo upload is process-local, so it resets when the API restarts. A production
version should store catalog versions in PostgreSQL/object storage and pin each
conversation to one version.

The same operations are available through `POST /api/catalog/upload` and
`POST /api/catalog/search`.

## Layout

| Path | Responsibility |
| --- | --- |
| `backend/bot.py` | Pipecat WebRTC, local VAD and semantic turn detection, STT-fragment aggregation, the shared scheduling turn service, live diagnostics, and ElevenLabs STT/TTS. |
| `backend/observability.py` | Structured logging, correlation fields, secret redaction, rotated JSONL sinks, and local log inspection. |
| `frontend/app/voice-call-panel.tsx` | Embedded call controls, microphone connection, browser audio, and live patient/assistant transcript. |
| `backend/extraction/` | Strict Pydantic extraction schema, prompt, OpenAI Responses adapter, telemetry, and semantic validator. |
| `backend/scheduling/` | Patient request reducer, content-hashed catalog resolution, deterministic rules, availability, booking, checked response writer, and shared turn service. |
| `backend/conversation/` | Server-owned pending offers and option IDs used for selections and confirmation. |
| `backend/agent_builder/` | All agent-building code. `schema.py` = the declarative `AgentConfig` / `Node` / `Edge` contract; `builder.py` = `AgentBuilder`, which loads + validates the JSON and compiles it into a Pipecat Flows graph. |
| `backend/example_flow.json` | The example agent **as data** — a clinic scheduler. The starting point for the Phase 2 context-management work. |
| `backend/data/catalog.json` | The fast runtime and 40-case evaluation catalog. |
| `backend/data/large-catalog.json` | The 250-location, 2,500-provider, 500-appointment stress catalog. See [`backend/data/README.md`](backend/data/README.md). |

The original graph builder remains available for the Phase 1 editor, but booking execution no longer depends on an LLM-authored graph transition.
