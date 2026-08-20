# Phase 1 voice workflow

Prosper Agent Studio treats the voice agent as versioned JSON that can be loaded,
edited, validated, and saved from the **Agent graph** tab. The format keeps Pipecat
Flows fields such as `task_messages`, callable edge functions, and transition actions,
while adding stable node types and canvas positions for the editor.

The vocabulary draws on three production builders:

- [Pipecat Flows nodes and messages](https://docs.pipecat.ai/pipecat-flows/guides/nodes-and-messages)
  define focused nodes with role/task messages, functions, and pre/post actions.
- [ElevenLabs Workflows](https://elevenlabs.io/docs/eleven-agents/customization/agent-workflows)
  distinguish conversational subagents, guaranteed tool dispatch, transfers, terminal
  nodes, and tool success/failure paths.
- [Retell conversation flows](https://docs.retellai.com/build/conversation-flow/overview)
  distinguish conversation, subagent, function, logic, transfer, and end nodes with
  explicit conditional and fallback transitions.

## Node types

`conversation` speaks or collects information and has no privileged capability.
`subagent` is conversational but receives only the tools needed for one narrow task.
`tool` represents a guaranteed backend operation. `decision` routes silently from
application-owned state or a tool result. `handoff` transfers responsibility to clinic
staff. `end` is terminal and has no outgoing edges.

These types are editor-level guardrails. The Pipecat compiler still emits standard
`NodeConfig` objects, keeping the stored graph portable and close to the runtime library.

## Edge types

`condition` describes an observable patient or state condition. `success` and `failure`
route explicit tool outcomes. `default` is the single fallback path when no specific
condition applies. Every edge has a stable ID, human label, destination, and Pipecat
function name. Conditional edges require a written condition; end nodes cannot have
outgoing edges; all destinations and tool references must exist before saving.

Clinic eligibility, referral requirements, availability, and booking are not LLM edge
conditions. Those decisions remain in `SchedulingEngine`, `MockAvailability`, and
`MockBookingService`. The graph orchestrates when those capabilities run and what the
assistant may say about their checked outputs.

## Tool definitions

Tools are defined once at the top of `backend/example_flow.json`. Each definition names
the implementation seam, JSON-style parameters, and possible outcomes. Nodes reference
tools by name, which makes capability scope visible in the inspector.

The demo exposes five tools: observation-only extraction, deterministic catalog and rule
evaluation, availability lookup, idempotent booking, and staff handoff. The saved voice
ID and first spoken message are loaded when a new Pipecat call starts. Changes to graph
layout and prompts are saved immediately to the JSON but require a new call—and a bot
restart for long-running development workers—to affect runtime configuration.

## Demo walkthrough

Start the API, voice runner, and frontend with `make api`, `make run`, and
`make frontend`. In **Agent graph**, drag nodes to organize the canvas, select a node,
edit its identity and instructions, scope its tools, add or remove paths, and press
**Save workflow**. Invalid references, duplicate identifiers, missing conditions, or
illegal end-node paths receive a validation error and are not written.

Then open **Scheduling agent** and start a call. A completed scheduling turn highlights the
workflow nodes whose `runtime_stages` match the real extraction, rules, decision, and
booking trace. This connects the editable design view to observable runtime evidence
without pretending that visual prompts control deterministic clinic policy.
