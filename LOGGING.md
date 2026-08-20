# Prosper logging and debugging

Prosper emits structured operational events for the API, scheduling engine,
booking adapter, Pipecat voice process, agent graph, and uncaught browser errors.
Every event can carry a `request_id`, `conversation_id`, and `turn_id`, making it
possible to follow one request across layers without searching for patient text.

## Where logs are stored locally

The default files are:

```text
backend/.logs/api.jsonl
backend/.logs/voice.jsonl
```

They are newline-delimited JSON, rotate at 10 MB, retain seven days, and compress
rotated files with gzip. The current files can be inspected in three ways:

```bash
make logs          # follow all events
make logs-once     # print the latest 200 events and exit
make logs-errors   # follow ERROR events only
```

The **System logs** tab in Agent Studio offers the same current-file view with
live refresh, full-text search, level filtering, and process filtering. It reads
`GET /api/system/logs`; this local inspection API is disabled automatically when
`APP_ENV=production`.

The terminals running `make api` and `make run` also show a compact stream. Each
row has this shape:

```text
12:08:41.318 INFO api/scheduling Scheduling turn completed
```

Expand the JSON event to see safe diagnostic fields such as duration, next
action, token counts, trace stages, and correlation IDs.

## Correlating a failed turn

1. Copy the `x-request-id` response header, conversation ID, or turn/message ID.
2. Paste it into the System logs search box, or run:

   ```bash
   backend/.venv/bin/python backend/show_logs.py --search conv_123
   ```

3. Follow the event sequence: `http_request_completed`, `turn_started`, extraction
   events, booking events, and `turn_completed` or `turn_failed`.
4. For browser-only failures, search for the `frontend/browser_error` or
   `frontend/unhandled_promise_rejection` event.

Operational logs are separate from the durable product audit data in PostgreSQL.
Conversation turns, usage events, booking attempts, and evaluations remain in
their existing tables; JSONL logs explain runtime behavior and exceptions.

## Privacy and secrets

Patient wording is excluded from operational logs by default. Voice and text
events record only transcript character and word counts. Common API keys,
authorization headers, passwords, secrets, and PostgreSQL credentials are
redacted before display.

Pipecat and provider-library DEBUG/TRACE records are also filtered in this mode,
because some upstream diagnostics contain raw STT or TTS text. Prosper's own
structured DEBUG events remain available and contain counts rather than wording.

`LOG_INCLUDE_TRANSCRIPTS=true` is available only for a consented local debugging
session. Never enable it in production. This setting affects operational logs,
not the application data intentionally stored in `conversation_turns`.

## Configuration

Copy values from `backend/.env.example` as needed:

| Variable | Default | Purpose |
| --- | --- | --- |
| `LOG_LEVEL` | `INFO` | Minimum emitted level. Use `DEBUG` for STT segment timing. |
| `LOG_CONSOLE` | `true` | Write events to stderr. |
| `LOG_CONSOLE_JSON` | production only | Emit machine-readable console JSON. |
| `LOG_JSON_FILES` | `true` | Write local rotated JSONL files. |
| `LOG_DIR` | `backend/.logs` | Override the shared local log directory. |
| `LOG_ROTATION` | `10 MB` | Loguru rotation threshold. |
| `LOG_RETENTION` | `7 days` | Rotated-file retention. |
| `LOG_INCLUDE_TRANSCRIPTS` | `false` | Include patient wording; local consented debugging only. |
| `ENABLE_DEBUG_LOG_API` | off in production | Enable the local log viewer endpoints. |

## Deployment settings

For containers or a managed host, prefer stdout JSON and let the platform ship it
to its centralized log service:

```dotenv
APP_ENV=production
LOG_LEVEL=INFO
LOG_CONSOLE=true
LOG_CONSOLE_JSON=true
LOG_JSON_FILES=false
LOG_INCLUDE_TRANSCRIPTS=false
ENABLE_DEBUG_LOG_API=false
```

The System logs tab intentionally does not replace a production log backend:
separate API and voice containers may not share a filesystem, and local files are
ephemeral on many hosts. Filter centralized logs by the same `request_id`,
`conversation_id`, `turn_id`, `event`, `process`, and `component` fields.
