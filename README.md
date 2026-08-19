# Prosper Challenge — Context Management

Voice AI for healthcare scheduling with observation-only LLM extraction and a deterministic scheduling engine.

- **Phase 1** — a UI to edit the node graph and place a test call.
- **Phase 2** — a context-management approach so a scheduling agent can reliably and cost-effectively navigate a large catalog of locations, doctors, and appointment types.

```
browser mic -> ElevenLabs STT -> shared ConversationService -> ElevenLabs TTS -> browser
                                      |
                        structured LLM extraction
                                      |
             catalog resolution + rules + availability + booking
```

Pipecat's dev runner ships a **prebuilt browser client**, so the test-call UI comes for free — no frontend code to write yet.

## Quickstart

Requires **Python 3.11+**. Run from the repo root:

```bash
make install
make run
```

Open the URL it prints (default `http://localhost:7860/client`), click **Connect**, allow mic access, and talk to the agent. `Ctrl+C` to stop. (`make help` lists all targets.)\
\
Remember to update the `.env` file accordingly.

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

The frontend sends text turns to `http://127.0.0.1:8000`, then renders the patient request, decision trace, alternatives, offered slots, and mock booking result. The **Test agent** button opens Pipecat's WebRTC client at `http://127.0.0.1:7860/client`. Text and voice call the same `ConversationService`.

`EXTRACTOR_MODE=auto` uses OpenAI Structured Outputs when `OPENAI_API_KEY` is available and the local structured fallback otherwise. The default extraction model is configured by `EXTRACTION_MODEL`.

## Layout

| Path | Responsibility |
| --- | --- |
| `backend/bot.py` | Pipecat WebRTC + ElevenLabs STT/TTS transport around the shared scheduling service. |
| `backend/extraction/` | Strict Pydantic extraction schema, prompt, OpenAI Responses adapter, telemetry, and semantic validator. |
| `backend/scheduling/` | Patient request reducer, content-hashed catalog resolution, deterministic rules, availability, booking, and shared turn service. |
| `backend/conversation/` | Server-owned pending offers and option IDs used for selections and confirmation. |
| `backend/agent_builder/` | All agent-building code. `schema.py` = the declarative `AgentConfig` / `Node` / `Edge` contract; `builder.py` = `AgentBuilder`, which loads + validates the JSON and compiles it into a Pipecat Flows graph. |
| `backend/example_flow.json` | The example agent **as data** — a clinic scheduler. The starting point for the Phase 2 context-management work. |
| `backend/data/catalog.json` | A deliberately large, deliberately messy clinic catalog (locations, providers, appointment types, booking rules) for the Phase 2 work. See [`backend/data/README.md`](backend/data/README.md). |

The original graph builder remains available for the Phase 1 editor, but booking execution no longer depends on an LLM-authored graph transition.
