"use client";

import { useState } from "react";

type JourneyStep = {
  id: string;
  number: string;
  title: string;
  owner: string;
  childExplanation: string;
  receives: string;
  produces: string;
  reason: string;
  forbidden: string;
  example: string;
};

const exampleRequest = "I’m a new patient and need the earliest dental cleaning.";

const journey: JourneyStep[] = [
  {
    id: "listen",
    number: "1",
    title: "Know when the patient is done",
    owner: "bot.py · Pipecat",
    childExplanation: "Like waiting for someone to finish a sentence before answering.",
    receives: "Live microphone audio over WebRTC",
    produces: "One final patient utterance",
    reason: "Scheduling runs on UserStoppedSpeakingFrame, so partial speech cannot create several conflicting turns.",
    forbidden: "Interim transcription cannot change the patient request.",
    example: `final_transcript = "${exampleRequest}"`,
  },
  {
    id: "extract",
    number: "2",
    title: "Translate words into facts",
    owner: "llm_extractor.py · OpenAI",
    childExplanation: "The model acts like a highlighter: it marks only the useful facts in the latest sentence.",
    receives: "Latest utterance + compact request + pending offer",
    produces: "A typed observation patch with quoted evidence",
    reason: "Natural language is fuzzy; a model is useful for interpreting synonyms, corrections and references.",
    forbidden: "The model cannot invent clinic IDs, decide eligibility, choose slots or book.",
    example: `{
  "current_goal": "BOOK_APPOINTMENT",
  "patient_status": "NEW",
  "appointment_type": { "raw_text": "dental cleaning" },
  "time": { "objective": "EARLIEST" }
}`,
  },
  {
    id: "validate",
    number: "3",
    title: "Check that every fact was really said",
    owner: "validator.py · application code",
    childExplanation: "A second reader checks that every highlighted fact exists in the sentence.",
    receives: "Structured observations and their evidence",
    produces: "Trusted patch, or one corrected extraction retry",
    reason: "A valid JSON shape is not enough; the meaning must also be supported by the patient’s words.",
    forbidden: "Unsupported, contradictory or stale facts never reach durable state.",
    example: `patient_status = NEW       ✓ “new patient”
appointment = dental     ✓ “dental cleaning”
time = EARLIEST          ✓ “earliest”`,
  },
  {
    id: "remember",
    number: "4",
    title: "Update one trusted request",
    owner: "state.py + storage.py · PostgreSQL",
    childExplanation: "The system keeps one clean worksheet instead of rereading the whole conversation.",
    receives: "Previously trusted request + validated patch",
    produces: "Canonical SchedulingRequest and a new fingerprint",
    reason: "Compact state lowers context cost and lets another server continue the same conversation.",
    forbidden: "If a material fact changes, old offers and confirmations are invalidated.",
    example: `SchedulingRequest(
  patient_status = NEW,
  appointment_type = "dental cleaning",
  time.objective = EARLIEST
)
request_fingerprint = "72b…"`,
  },
  {
    id: "resolve",
    number: "5",
    title: "Match words to the clinic catalog",
    owner: "catalog.py · deterministic code",
    childExplanation: "It looks up the clinic’s official name, like finding a book in a library index.",
    receives: "Raw patient wording + versioned catalog snapshot",
    produces: "Exactly one record, an ambiguity, or no match",
    reason: "Rules must run against real clinic records, never a name guessed by the model.",
    forbidden: "Multiple possible records are never silently reduced to one.",
    example: `"dental cleaning"
        ↓ exact/alias resolution
appointment_type_id = "appt_dental_cleaning"
catalog_version = "sha256:91c…"`,
  },
  {
    id: "decide",
    number: "6",
    title: "Prove what is allowed",
    owner: "engine.py · SchedulingEngine.evaluate",
    childExplanation: "A rulebook checks the request in the same order every time.",
    receives: "Resolved request + clinic policies",
    produces: "Rule results, candidates and one typed next action",
    reason: "The same facts must always produce the same decision, with a concrete reason for every blocker.",
    forbidden: "The engine asks only for an unknown fact that can change the outcome.",
    example: `patient eligibility  PASS
referral requirement  NOT_APPLICABLE
exact candidates       3
next_action            QUERY_AVAILABILITY`,
  },
  {
    id: "offer",
    number: "7",
    title: "Create options the server can trust",
    owner: "service.py + availability.py",
    childExplanation: "The server writes the choices on a numbered ticket, so “the first one” has one meaning.",
    receives: "Valid candidates + EARLIEST objective",
    produces: "Server-owned slot offer with offer ID and request fingerprint",
    reason: "A pending offer gives short replies such as “yes” or “that one” a precise reference.",
    forbidden: "A stale, expired, mismatched or already-used slot cannot be selected.",
    example: `offer_id = "offer_123"
request_fingerprint = "72b…"
slots = [
  { "slot_id": "slot_1", "starts_at": "09:00" }
]`,
  },
  {
    id: "book",
    number: "8",
    title: "Book only after an explicit yes",
    owner: "booking adapter + PostgreSQL transaction",
    childExplanation: "Nothing is written in the appointment book until the patient confirms the exact choice.",
    receives: "Confirmed offer ID + exact slot ID",
    produces: "A booking ID with status CONFIRMED, or a safe failure",
    reason: "The assistant’s words are not proof of success; the booking system’s confirmed response is.",
    forbidden: "The assistant cannot say “booked” before a matching confirmed backend response.",
    example: `{
  "status": "CONFIRMED",
  "booking_id": "booking_789",
  "offer_id": "offer_123",
  "slot_id": "slot_1"
}`,
  },
];

const algorithm = [
  ["Resolve requested names", "Unclear? Ask for clarification."],
  ["Check decisive patient eligibility", "Fails? Block and offer a useful next path."],
  ["Find an unknown fact that changes the result", "Exists? Ask exactly one question."],
  ["Check referral policy", "Fails? Block and explain the required recovery."],
  ["Build and validate exact candidates", "Any? Search availability."],
  ["Try safe relaxations", "Any? Ask permission before changing the request."],
  ["No safe candidate", "Stop or hand off; never invent availability."],
];

const authorityRows = [
  ["Patient speech", "Browser + Pipecat", "Temporary audio and transcript", "Not trusted as structured facts"],
  ["Extracted observations", "LLM", "Typed proposal", "Must pass evidence validation"],
  ["Patient request", "State reducer", "PostgreSQL", "Canonical conversation truth"],
  ["Eligibility + candidates", "Deterministic engine", "Recomputed from catalog and policy", "LLM cannot override it"],
  ["Pending offer", "Scheduling service", "PostgreSQL", "Bound to request fingerprint"],
  ["Confirmed booking", "Booking adapter", "Transactional booking record", "Only confirmed response means success"],
];

export function EngineLogicPanel() {
  const [selectedId, setSelectedId] = useState("listen");
  const selected = journey.find((step) => step.id === selectedId) ?? journey[0];

  return (
    <div className="page engine-system-page">
      <header className="engine-system-header">
        <div>
          <h2>One request through the real system</h2>
          <p>Select a handoff to see its owner, data, reason and safety boundary.</p>
        </div>
        <code>{exampleRequest}</code>
      </header>

      <section className="system-map" aria-labelledby="system-map-title">
        <header>
          <div><h3 id="system-map-title">Runtime map</h3><p>Voice moves left to right. Durable truth lives below the scheduler.</p></div>
          <span>Actual implementation</span>
        </header>
        <div className="system-map-flow" aria-label="Runtime component flow">
          <article><small>Patient device</small><strong>Browser</strong><span>Microphone · transcript · audio</span></article>
          <i aria-hidden="true">↔</i>
          <article><small>Voice worker</small><strong>Pipecat</strong><span>VAD · STT · turn boundary · TTS</span></article>
          <i aria-hidden="true">→</i>
          <article className="map-core"><small>Application core</small><strong>ConversationService</strong><span>Coordinates one safe scheduling turn</span></article>
          <i aria-hidden="true">→</i>
          <article><small>Business logic</small><strong>Catalog + Engine</strong><span>Resolve · validate rules · choose action</span></article>
          <i aria-hidden="true">→</i>
          <article><small>Clinic boundary</small><strong>Availability + Booking</strong><span>Offer slots · confirm transaction</span></article>
        </div>
        <div className="system-map-services">
          <article><strong>OpenAI</strong><span>Observations only</span><i>↕</i><small>ConversationService</small></article>
          <article><strong>PostgreSQL / Supabase</strong><span>Request · turns · offers · bookings · usage</span><i>↕</i><small>ConversationService</small></article>
          <article><strong>Evaluation runner</strong><span>Calls the production pipeline with isolated test conversations</span><i>→</i><small>ConversationService</small></article>
        </div>
      </section>

      <section className="request-journey" aria-label="Request walkthrough">
        <div className="journey-list">
          <header><h3>Follow the request</h3><span>{journey.length} handoffs</span></header>
          {journey.map((step) => (
            <button
              aria-current={selected.id === step.id ? "step" : undefined}
              className={selected.id === step.id ? "active" : ""}
              key={step.id}
              onClick={() => setSelectedId(step.id)}
              type="button"
            >
              <span>{step.number}</span>
              <div><strong>{step.title}</strong><small>{step.owner}</small></div>
              <i aria-hidden="true">›</i>
            </button>
          ))}
        </div>

        <article className="journey-detail">
          <span className="journey-owner">{selected.owner}</span>
          <h3>{selected.title}</h3>
          <p className="journey-simple">{selected.childExplanation}</p>
          <div className="journey-io">
            <div><span>Receives</span><strong>{selected.receives}</strong></div>
            <i aria-hidden="true">→</i>
            <div><span>Produces</span><strong>{selected.produces}</strong></div>
          </div>
          <dl className="journey-reasons">
            <div><dt>Why it exists</dt><dd>{selected.reason}</dd></div>
            <div><dt>Safety boundary</dt><dd>{selected.forbidden}</dd></div>
          </dl>
          <div className="journey-example"><span>Example at this boundary</span><pre>{selected.example}</pre></div>
        </article>
      </section>

      <section className="engine-algorithm">
        <header><h3>The decision order</h3><p>The first condition that needs action wins. Later steps do not run.</p></header>
        <ol>
          {algorithm.map(([check, result]) => (
            <li key={check}><strong>{check}</strong><span>{result}</span></li>
          ))}
        </ol>
      </section>

      <section className="authority-table">
        <header><h3>Who is allowed to decide what?</h3><p>This is the trust model behind the architecture.</p></header>
        <div role="table" aria-label="System data authority">
          <div className="authority-row authority-head" role="row">
            <span role="columnheader">Data</span><span role="columnheader">Owner</span><span role="columnheader">Where it lives</span><span role="columnheader">Rule</span>
          </div>
          {authorityRows.map((row) => (
            <div className="authority-row" role="row" key={row[0]}>
              {row.map((cell) => <span role="cell" key={cell}>{cell}</span>)}
            </div>
          ))}
        </div>
      </section>

      <section className="engine-invariants">
        <header><h3>Four claims to defend</h3></header>
        <div>
          <article><span>01</span><strong>The LLM has no booking authority.</strong><p>It proposes observations; deterministic code owns identity, policy and action.</p></article>
          <article><span>02</span><strong>Every offer is tied to one request.</strong><p>A material correction changes the fingerprint and invalidates stale choices.</p></article>
          <article><span>03</span><strong>A booking is successful only when the backend confirms it.</strong><p>The returned offer, slot and booking IDs must match.</p></article>
          <article><span>04</span><strong>Durable state makes API workers replaceable.</strong><p>PostgreSQL stores turns, offers, bookings and usage; a short lease prevents two workers processing one turn.</p></article>
        </div>
      </section>

      <aside className="implementation-truth">
        <strong>Current boundary</strong>
        <p>The Agent graph edits the greeting, voice and workflow metadata. The scheduling algorithm itself is still code-owned by ConversationService and SchedulingEngine; the graph does not yet compile into those rules.</p>
      </aside>
    </div>
  );
}
