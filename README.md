# Prosper Agent Studio frontend

The reviewer-facing interface for building and testing the healthcare scheduling agent.

## Current slice

The frontend currently provides an interactive product shell with four areas:

- **Workbench** — a scripted version of the golden dental-scheduling scenario, canonical state, decision trace, and illustrative context comparison.
- **Agent graph** — selectable nodes and an editable node inspector held in browser state.
- **Catalog** — summary counts plus searchable sample records from the provided clinic catalog.
- **Evaluations** — the intended evaluation categories and empty result state.

Every value that is not yet backed by the Python application is labeled as demo, sample, illustrative, or not run. Backend API integration and persistence are intentionally deferred to the next product slice.

## Run locally

Requires Node.js 22.13 or newer.

```bash
npm install
npm run dev
```

Use `npm test` for the production build and server-rendered shell checks.
