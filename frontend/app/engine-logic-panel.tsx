"use client";

import { useState } from "react";

type EngineStage = {
  id: string;
  number: string;
  title: string;
  owner: string;
  short: string;
  input: string;
  output: string;
  guarantee: string;
};

const stages: EngineStage[] = [
  { id: "transcript", number: "01", title: "Final transcript", owner: "Pipecat", short: "Wait until the patient finishes speaking.", input: "Voice or typed message", output: "One final patient utterance", guarantee: "Interim speech never changes the patient request." },
  { id: "extract", number: "02", title: "Extract observations", owner: "LLM", short: "Report only what changed in the latest words.", input: "Compact request, pending offer and latest utterance", output: "Strict patch with evidence", guarantee: "The LLM cannot return catalog IDs or make policy decisions." },
  { id: "validate", number: "03", title: "Validate evidence", owner: "Application code", short: "Reject unsupported or contradictory changes.", input: "Structured extraction", output: "Trusted patch or safe retry", guarantee: "Every changed fact must be supported by the latest utterance." },
  { id: "remember", number: "04", title: "Update patient request", owner: "Application code", short: "Apply corrections once and clear stale offers.", input: "Trusted patch", output: "Canonical patient request", guarantee: "Old slots and confirmations cannot survive a material request change." },
  { id: "resolve", number: "05", title: "Resolve catalog", owner: "Deterministic engine", short: "Match raw phrases to clinic records.", input: "Patient wording and catalog snapshot", output: "Resolved, ambiguous or unresolved identities", guarantee: "Names are never guessed when multiple records match." },
  { id: "rules", number: "06", title: "Evaluate rules", owner: "Deterministic engine", short: "Prove eligibility before searching availability.", input: "Resolved request and clinic policies", output: "Rule results and valid candidates", guarantee: "Every blocker has a concrete rule and reason." },
  { id: "decide", number: "07", title: "Choose one action", owner: "Deterministic engine", short: "Ask, search, offer, block or hand off.", input: "Blockers, candidates and pending offer", output: "One typed next action", guarantee: "Only questions that can change the outcome are asked." },
  { id: "book", number: "08", title: "Confirm and book", owner: "Booking system", short: "Write only after explicit confirmation.", input: "Exact server-owned offer ID", output: "Confirmed booking ID or failure", guarantee: "The assistant says booked only after the booking system returns confirmed." },
];

const decisions = [
  ["Identity unresolved", "Ask for clarification"],
  ["Patient is ineligible", "Block and offer recovery"],
  ["A relevant fact is unknown", "Ask one question"],
  ["An exact candidate is valid", "Query availability"],
  ["Only a safe alternative exists", "Ask permission"],
  ["A slot is selected", "Request confirmation"],
  ["Booking system says confirmed", "Report success"],
];

export function EngineLogicPanel() {
  const [selectedId, setSelectedId] = useState("resolve");
  const selected = stages.find((stage) => stage.id === selectedId) ?? stages[0];

  return (
    <div className="page engine-page">
      <header className="engine-header">
        <div>
          <h2>The deterministic scheduling engine</h2>
          <p>The LLM understands words. Application code owns every scheduling decision.</p>
        </div>
        <span>8 checked stages</span>
      </header>

      <section className="engine-boundary">
        <div>
          <span>LLM responsibility</span>
          <strong>Observe what the patient said</strong>
          <p>Intent, facts, preferences, corrections and evidence.</p>
        </div>
        <div className="engine-boundary-rule"><span>Trust boundary</span></div>
        <div>
          <span>Application responsibility</span>
          <strong>Decide what the clinic can do</strong>
          <p>Identity, policy, relevance, candidates, availability and booking.</p>
        </div>
      </section>

      <section className="engine-workflow">
        <div className="engine-stage-list" aria-label="Engine stages">
          {stages.map((stage) => (
            <button className={selected.id === stage.id ? "active" : ""} key={stage.id} type="button" onClick={() => setSelectedId(stage.id)}>
              <span>{stage.number}</span>
              <div><strong>{stage.title}</strong><small>{stage.short}</small></div>
              <i aria-hidden="true">→</i>
            </button>
          ))}
        </div>
        <article className="engine-stage-detail">
          <span>{selected.owner}</span>
          <h3>{selected.title}</h3>
          <p>{selected.short}</p>
          <dl>
            <div><dt>Receives</dt><dd>{selected.input}</dd></div>
            <div><dt>Produces</dt><dd>{selected.output}</dd></div>
          </dl>
          <div className="engine-guarantee"><span>Safety guarantee</span><strong>{selected.guarantee}</strong></div>
        </article>
      </section>

      <section className="engine-context">
        <header><h3>Small context, durable memory</h3><p>The model sees only what it needs for the latest sentence.</p></header>
        <div>
          <article><span>1</span><strong>Patient request</strong><p>Validated facts and preferences owned by the server.</p></article>
          <article><span>2</span><strong>Pending offer</strong><p>The exact question or option that “yes” and “that one” refer to.</p></article>
          <article><span>3</span><strong>Recent context</strong><p>Only the latest relevant exchange—not the full transcript or catalog.</p></article>
        </div>
        <footer>Catalog records, policy lists, hidden candidates and booking authority never enter the model context.</footer>
      </section>

      <section className="engine-decisions">
        <header><h3>How the next action is chosen</h3><p>The first true condition wins.</p></header>
        <div>
          {decisions.map(([condition, action], index) => (
            <div key={condition}><span>{index + 1}</span><strong>{condition}</strong><i>→</i><p>{action}</p></div>
          ))}
        </div>
      </section>
    </div>
  );
}
