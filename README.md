# Prosper Agent Studio frontend

The reviewer-facing interface for building and testing the healthcare scheduling agent.

## Current slice

The frontend currently provides an interactive product shell with four areas:

- **Workbench** — a live text client for the local Python scheduling API, including the golden dental-scheduling scenario, canonical state, measured decision trace, slot selection, explicit confirmation, and mock booking result.
- **Agent graph** — selectable nodes and an editable node inspector held in browser state.
- **Catalog** — summary counts plus searchable sample records from the provided clinic catalog.
- **Evaluations** — the intended evaluation categories and empty result state.

The graph editor, catalog sample, evaluations, and voice control remain frontend-only and are labeled accordingly. The text conversation, state, trace, alternatives, slots, and booking result now come from the backend.

## Run locally

Requires Node.js 22.13 or newer.

```bash
npm install
npm run dev
```

From another terminal in the repository root, start the text API with `make api`. The frontend defaults to `http://127.0.0.1:8000`; copy `.env.example` to `.env.local` only when that address needs to change.

Use `npm test` for the production build and server-rendered shell checks.
