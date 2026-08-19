# Prosper Challenge — Context Management

Voice AI for healthcare scheduling with an embedded live-call tester, observation-only LLM extraction, and a deterministic scheduling engine.

- **Phase 1** — a UI to edit the node graph and place a test call.
- **Phase 2** — a context-management approach so a scheduling agent can reliably and cost-effectively navigate a large catalog of locations, doctors, and appointment types.

```
browser mic -> Pipecat WebRTC -> ElevenLabs STT -> completed patient turn
    -> structured LLM extraction -> deterministic scheduler
    -> checked response -> ElevenLabs TTS -> browser audio
```

The live call is rendered inside Prosper Agent Studio. The user is never redirected to Pipecat's prebuilt client.

Voice and text now use the same `ConversationService`. The LLM reports only patient-stated facts; application code applies defaults, resolves the catalog, checks policy and availability, owns pending offers, and confirms a booking only after the mock booking system succeeds.

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

Open `http://localhost:3001`, press the large recording button on the Workbench, and allow microphone access. Speak naturally and pause when finished. The transcript is finalized after end-of-speech detection, sent to the LLM, and both sides of the conversation appear on the same screen while the assistant audio plays there.

## Text workbench

The deterministic text scheduling loop runs without OpenAI or ElevenLabs keys:

```bash
make install
make api
```

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

The frontend sends text turns to `http://127.0.0.1:8000`, then renders the patient request, decision trace, alternatives, offered slots, and mock booking result. The main voice surface connects directly to the Pipecat runner at `http://127.0.0.1:7860`; it never opens another page or window.

`EXTRACTOR_MODE=auto` uses OpenAI Structured Outputs when `OPENAI_API_KEY` is available and the local structured fallback otherwise. The default extraction model is configured by `EXTRACTION_MODEL`.

## Layout

| Path | Responsibility |
| --- | --- |
| `backend/bot.py` | Pipecat WebRTC, end-of-speech detection, the shared scheduling turn service, live diagnostics, and ElevenLabs STT/TTS. |
| `frontend/app/voice-call-panel.tsx` | Embedded call controls, microphone connection, browser audio, and live patient/assistant transcript. |
| `backend/extraction/` | Strict Pydantic extraction schema, prompt, OpenAI Responses adapter, telemetry, and semantic validator. |
| `backend/scheduling/` | Patient request reducer, content-hashed catalog resolution, deterministic rules, availability, booking, and shared turn service. |
| `backend/conversation/` | Server-owned pending offers and option IDs used for selections and confirmation. |
| `backend/agent_builder/` | All agent-building code. `schema.py` = the declarative `AgentConfig` / `Node` / `Edge` contract; `builder.py` = `AgentBuilder`, which loads + validates the JSON and compiles it into a Pipecat Flows graph. |
| `backend/example_flow.json` | The example agent **as data** — a clinic scheduler. The starting point for the Phase 2 context-management work. |
| `backend/data/catalog.json` | A deliberately large, deliberately messy clinic catalog (locations, providers, appointment types, booking rules) for the Phase 2 work. See [`backend/data/README.md`](backend/data/README.md). |

The original graph builder remains available for the Phase 1 editor, but booking execution no longer depends on an LLM-authored graph transition.
