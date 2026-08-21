# AI Software Engineer Challenge

Prosper builds voice AI for healthcare phone calls across several use cases. Our largest is **appointment scheduling**. This challenge has two phases. Plan to spend roughly 8–12 hours on it.

## Phase 1 — Voice Agent Builder

Build a UI for creating and editing voice agents, where an agent is a **graph of nodes** — each node a step in the conversation, each edge a transition the agent can take. A user should be able to edit the node graph and place a test call from the UI, similar to existing products like [ElevenLabs Agents](https://elevenlabs.io/) or [Retell AI](https://www.retellai.com/) (see the reference screenshot below).

### What's already built

The backend is already in place: a [Pipecat](https://github.com/pipecat-ai/pipecat) voice pipeline (WebRTC + ElevenLabs STT/TTS + OpenAI LLM) whose conversation is a **node graph** built with Pipecat Flows. An agent is defined declaratively as JSON and compiled into a runnable flow at runtime by an `AgentBuilder`, and can be exercised through a browser test call. The repo ships an example agent (`backend/example_flow.json`) purely to illustrate the format — it's a sample, not the agent you're expected to use; build whatever agent fits your solution. Phase 1 therefore comes down to **building the UI to edit those nodes** — the agent representation and the voice runtime already exist, though you're welcome to change either (the node format, the backend) if you think you can do better. The starter code lives in the [prosper-challenge-context-management](https://github.com/Prosper-Technologies/prosper-challenge-context-management) repository — create a fork and start building your solution.

## Phase 2 — Context Management

This is the more open-ended — and, to us, the more interesting — part of the challenge. 

One of our biggest challenges today is efficiently handling a large number of locations, doctors, and appointment types when scheduling. The naive approach is to dump all of them as plain text into the age

nt's context. This poses two main issues (the latter being the more important):

1. **Cost** — large contexts are expensive, especially at scale.
2. **Accuracy** — the agent struggles to pick the right appointment type, location, or doctor from a long, undifferentiated list.

Now that you have a platform for building agents, design a context-management approach that lets a scheduling agent reliably and cost-effectively navigate a large catalog of locations, doctors, and appointment types. Treat this as an open design problem — retrieval, structured tool calls, hierarchical lookups, self-correcting systems or anything else — and propose the best solution you can.

### Provided data

The starter repo includes a sample catalog at `backend/data/catalog.json` — a deliberately large and messy set of locations, providers, and appointment types, along with the booking policies that constrain them (referrals, new-patient rules, capability-gated services, providers practising at multiple locations). This simulates the data we could receive from a multi-specialty clinic; the goal is to integrate all these resources and policies into an agent that can handle scheduling reliably and cost-effectively.

Availability/scheduling data isn't included — mock it however you like; the focus is navigating the catalog, not a real calendar. You're also free to add a section to the builder UI for managing these resources (providers, locations, appointment types).

## Requirements

- A minimal UI where a user can create an agent and place a test call.
- A scheduling agent capable of:
    - Creating appointments for patients efficiently
    - Offering available appointment slots
    - Matching the patient's request to the right appointment type, location, and provider — including disambiguating between similar options and honoring preferences (provider, location, soonest available)
    - Answering questions about locations, doctors, and appointment types
- We'll provide API keys for OpenAI and ElevenLabs separately, but feel free to use other providers if you prefer.

## What we'll value

We care less about polish and breadth of features, and more about your engineering judgment. In particular, we'll be looking for:

- **Originality and rigor of your context-management approach (Phase 2)** — this is the heart of the challenge. We're interested in how you reason about cost and accuracy.
- **System design and scalability** — would your approach hold up with a large number of locations, providers, and appointment types?
- **Understanding of your trade-offs** — explain why you chose your approach over the alternatives (cost, latency, accuracy, complexity).
- **Pragmatic scoping** — what you chose to build, mock, or leave out, and the reasoning behind it. Knowing what *not* to build is a signal.
- **A flashy demo** — during the challenge review we'll ask you to walk us through a live, end-to-end demo of what you built, so make sure it's something you can show off.

## Hints

We highly encourage you to use AI tools (Claude Code, Cursor, etc.) to help with this challenge. We don't mind if you "vibe code" most of it — that usually signals strong prompting skills. What matters is that you can explain and defend the decisions and trade-offs behind your solution.

## Submission

Submit a link to a repository containing your code and a `solution.md` file with an overview of your solution and the key architectural decisions you made.
