"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";

type Tab = "workbench" | "graph" | "catalog" | "evaluations";

const tabs: { id: Tab; label: string; glyph: string }[] = [
  { id: "workbench", label: "Workbench", glyph: "◫" },
  { id: "graph", label: "Agent graph", glyph: "⌘" },
  { id: "catalog", label: "Catalog", glyph: "▦" },
  { id: "evaluations", label: "Evaluations", glyph: "✓" },
];

const API_BASE = process.env.NEXT_PUBLIC_SCHEDULING_API_URL ?? "http://127.0.0.1:8000";
const GOLDEN_UTTERANCE = "I'm a new patient looking for the earliest dental cleaning with Dr. Wei Lee. Richmond is a must.";

type ApiStatus = "connecting" | "connected" | "offline";
type Message = { id: string; role: "assistant" | "patient"; text: string; grounded?: boolean };
type EntityState = { raw_text: string; requirement: "REQUIRED" | "PREFERRED"; priority: number | null } | null;
type SchedulingStateDto = {
  conversation_id: string;
  version: number;
  active_intents: string[];
  patient_status: string;
  referral_status: string;
  appointment_type: EntityState;
  provider: EntityState;
  location: EntityState;
  time: { objective: string; priority: number | null; timezone: string } | null;
  selected_candidate_id: string | null;
  selected_slot_id: string | null;
  confirmed_state_version: number | null;
};
type TraceEvent = { stage: string; latency_ms: number; title: string; detail: string; tone: string };
type TurnResponse = {
  conversation_id: string;
  turn_id: string;
  turn_number: number;
  assistant_message: string;
  state: SchedulingStateDto;
  state_patch: Record<string, unknown>;
  trace: TraceEvent[];
  total_latency_ms: number;
  extractor_mode: string;
  offered_slots: { slot_id: string; start: string; duration_min: number }[];
  booking: { booking_id: string; status: string } | null;
  engine_result: {
    decision: { status: string };
    blockers: { code?: string; kind?: string }[];
    relaxation_candidates: { location_name: string; requires_patient_permission: boolean }[];
    next_action: { type: string };
  };
};

const graphNodes = [
  {
    id: "greeting",
    eyebrow: "ENTRY NODE",
    title: "Welcome caller",
    body: "Identify the scheduling assistant and understand the caller’s goal.",
    position: "node-one",
  },
  {
    id: "collect_request",
    eyebrow: "CONVERSATION",
    title: "Collect request",
    body: "Gather the service, patient status, and hard or soft preferences.",
    position: "node-two",
  },
  {
    id: "schedule",
    eyebrow: "TOOL NODE",
    title: "Run scheduler",
    body: "Resolve entities, enforce policy, rank candidates, and find slots.",
    position: "node-three",
  },
  {
    id: "confirm",
    eyebrow: "TERMINAL NODE",
    title: "Confirm booking",
    body: "Read back checked details and request explicit confirmation.",
    position: "node-four",
  },
];

const catalogRows = [
  { type: "Location", name: "Richmond Care Center", detail: "San Francisco · no dental" },
  { type: "Location", name: "Mission District Clinic", detail: "San Francisco · dental" },
  { type: "Provider", name: "Dr. Wei Lee", detail: "Dentistry · 2 locations" },
  { type: "Provider", name: "Dr. Linda Ramirez", detail: "Duplicate name · needs context" },
  { type: "Visit type", name: "Dental Cleaning", detail: "45 minutes · new patients allowed" },
  { type: "Visit type", name: "Physical Therapy Evaluation", detail: "No eligible provider" },
];

function Mark() {
  return (
    <span className="brand-mark" aria-hidden="true">
      <span />
      <span />
      <span />
    </span>
  );
}

function stateFacts(state: SchedulingStateDto | null): [string, string][] {
  if (!state) return [
    ["Intent", "Unknown"],
    ["Patient", "Unknown"],
    ["Visit", "Not provided"],
    ["Provider", "No preference"],
    ["Location", "No preference"],
    ["Time", "Not provided"],
  ];
  const entity = (value: EntityState, empty: string) => value
    ? `${value.raw_text} · ${value.requirement.toLocaleLowerCase()}`
    : empty;
  return [
    ["Intent", state.active_intents[0]?.replaceAll("_", " ").toLocaleLowerCase() ?? "Unknown"],
    ["Patient", state.patient_status.toLocaleLowerCase()],
    ["Visit", entity(state.appointment_type, "Not provided")],
    ["Provider", entity(state.provider, "No preference")],
    ["Location", entity(state.location, "No preference")],
    ["Time", state.time?.objective.replaceAll("_", " ").toLocaleLowerCase() ?? "Not provided"],
  ];
}

export default function Home() {
  const [activeTab, setActiveTab] = useState<Tab>("workbench");
  const [callActive, setCallActive] = useState(false);
  const [composer, setComposer] = useState("");
  const [apiStatus, setApiStatus] = useState<ApiStatus>("connecting");
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [currentTurn, setCurrentTurn] = useState<TurnResponse | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);
  const [showJson, setShowJson] = useState(false);
  const [selectedNode, setSelectedNode] = useState("schedule");
  const [nodePrompt, setNodePrompt] = useState(
    "Resolve the caller’s request against the clinic catalog. Never claim eligibility, availability, or booking success unless the scheduling tool returns it.",
  );
  const [saved, setSaved] = useState(true);
  const [catalogQuery, setCatalogQuery] = useState("");

  const selectedGraphNode = graphNodes.find((node) => node.id === selectedNode)!;
  const filteredCatalog = useMemo(() => {
    const query = catalogQuery.trim().toLocaleLowerCase();
    if (!query) return catalogRows;
    return catalogRows.filter((row) =>
      `${row.type} ${row.name} ${row.detail}`.toLocaleLowerCase().includes(query),
    );
  }, [catalogQuery]);

  useEffect(() => {
    void createConversation();
    // The initial connection runs once; later reconnects are explicit user actions.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function createConversation(initialUtterance?: string) {
    setApiStatus("connecting");
    setApiError(null);
    setCurrentTurn(null);
    setSubmitting(Boolean(initialUtterance));
    try {
      const response = await fetch(`${API_BASE}/api/conversations`, { method: "POST" });
      if (!response.ok) throw new Error(`API returned ${response.status}`);
      const created = await response.json() as { conversation_id: string; assistant_message: string };
      const greeting: Message = { id: `greeting-${created.conversation_id}`, role: "assistant", text: created.assistant_message };
      setConversationId(created.conversation_id);
      setMessages([greeting]);
      setApiStatus("connected");
      if (initialUtterance) await submitTurn(created.conversation_id, initialUtterance, [greeting]);
    } catch {
      setApiStatus("offline");
      setConversationId(null);
      setMessages([]);
      setApiError("The local scheduling API is unavailable. Start it with “make api”, then reconnect.");
      setSubmitting(false);
    }
  }

  async function submitTurn(id: string, utterance: string, baseMessages?: Message[]) {
    const patientMessage: Message = { id: `patient-${Date.now()}`, role: "patient", text: utterance };
    if (baseMessages) setMessages([...baseMessages, patientMessage]);
    else setMessages((existing) => [...existing, patientMessage]);
    setSubmitting(true);
    setApiError(null);
    try {
      const response = await fetch(`${API_BASE}/api/conversations/${id}/turns`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ utterance }),
      });
      if (!response.ok) throw new Error(`API returned ${response.status}`);
      const turn = await response.json() as TurnResponse;
      const assistantMessage: Message = {
        id: turn.turn_id,
        role: "assistant",
        text: turn.assistant_message,
        grounded: true,
      };
      if (baseMessages) setMessages([...baseMessages, patientMessage, assistantMessage]);
      else setMessages((existing) => [...existing, assistantMessage]);
      setCurrentTurn(turn);
      setApiStatus("connected");
    } catch {
      setApiStatus("offline");
      setApiError("That turn could not reach the scheduling API. Your message was not processed.");
    } finally {
      setSubmitting(false);
    }
  }

  function sendMessage(event: FormEvent) {
    event.preventDefault();
    const utterance = composer.trim();
    if (!utterance || !conversationId || submitting) return;
    setComposer("");
    void submitTurn(conversationId, utterance);
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <Mark />
          <div>
            <strong>Prosper</strong>
            <span>Agent Studio</span>
          </div>
        </div>

        <nav className="primary-nav" aria-label="Product areas">
          <p className="nav-label">BUILD</p>
          {tabs.map((tab) => (
            <button
              className={activeTab === tab.id ? "nav-item active" : "nav-item"}
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              type="button"
            >
              <span className="nav-glyph" aria-hidden="true">{tab.glyph}</span>
              {tab.label}
              {tab.id === "catalog" && <span className="nav-count">140</span>}
            </button>
          ))}
        </nav>

        <div className="sidebar-spacer" />

        <div className="agent-card">
          <div className="agent-avatar">PS</div>
          <div>
            <strong>Prosper Scheduler</strong>
            <span><i /> Draft · v0.1</span>
          </div>
          <button aria-label="Open agent menu" type="button">•••</button>
        </div>

        <div className="sidebar-footer">
          <button type="button"><span>?</span> Documentation</button>
          <button type="button"><span>⌘</span> Command menu <kbd>⌘K</kbd></button>
        </div>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <span className="mobile-mark"><Mark /></span>
            <div>
              <h1>{tabs.find((tab) => tab.id === activeTab)?.label}</h1>
              <p>Prosper Scheduler <span>/</span> Development</p>
            </div>
          </div>
          <div className="top-actions">
            <span className={`demo-badge api-${apiStatus}`}><i /> {apiStatus === "connected" ? "Local API connected" : apiStatus === "offline" ? "API offline" : "Connecting"}</span>
            <button className="secondary-button" type="button" onClick={() => setShowJson(true)} disabled={!currentTurn}>Preview JSON</button>
            <button className="primary-button" type="button" onClick={() => setCallActive(true)}>
              <span aria-hidden="true">◉</span> Test agent
            </button>
          </div>
        </header>

        {activeTab === "workbench" && (
          <Workbench
            callActive={callActive}
            composer={composer}
            apiError={apiError}
            apiStatus={apiStatus}
            currentTurn={currentTurn}
            messages={messages}
            onComposerChange={setComposer}
            onEndCall={() => setCallActive(false)}
            onReconnect={() => void createConversation()}
            onRunDemo={() => void createConversation(GOLDEN_UTTERANCE)}
            onSend={sendMessage}
            onStartCall={() => setCallActive(true)}
            submitting={submitting}
          />
        )}
        {activeTab === "graph" && (
          <GraphEditor
            nodePrompt={nodePrompt}
            onPromptChange={(value) => {
              setNodePrompt(value);
              setSaved(false);
            }}
            onSave={() => setSaved(true)}
            onSelectNode={setSelectedNode}
            saved={saved}
            selectedNode={selectedNode}
            selectedGraphNode={selectedGraphNode}
          />
        )}
        {activeTab === "catalog" && (
          <Catalog
            query={catalogQuery}
            rows={filteredCatalog}
            onQueryChange={setCatalogQuery}
          />
        )}
        {activeTab === "evaluations" && <Evaluations />}
      </section>

      {callActive && (
        <div className="call-dock" role="status" aria-live="polite">
          <div className="call-pulse"><span /></div>
          <div>
            <strong>Test call in progress</strong>
            <span>Voice transport remains mocked in this slice</span>
          </div>
          <div className="waveform" aria-hidden="true">
            {Array.from({ length: 12 }).map((_, index) => <i key={index} />)}
          </div>
          <button type="button" onClick={() => setCallActive(false)}>End call</button>
        </div>
      )}
      {showJson && currentTurn && (
        <div className="modal-backdrop">
          <button className="modal-dismiss" aria-label="Close JSON preview" type="button" onClick={() => setShowJson(false)} />
          <section className="json-modal" role="dialog" aria-modal="true" aria-labelledby="json-title">
            <header><div><span>TURN PAYLOAD</span><h2 id="json-title">Checked backend response</h2></div><button aria-label="Close JSON preview" type="button" onClick={() => setShowJson(false)}>×</button></header>
            <pre>{JSON.stringify(currentTurn, null, 2)}</pre>
          </section>
        </div>
      )}
    </main>
  );
}

function Workbench({
  apiError,
  apiStatus,
  callActive,
  composer,
  currentTurn,
  messages,
  onComposerChange,
  onEndCall,
  onReconnect,
  onRunDemo,
  onSend,
  onStartCall,
  submitting,
}: {
  apiError: string | null;
  apiStatus: ApiStatus;
  callActive: boolean;
  composer: string;
  currentTurn: TurnResponse | null;
  messages: Message[];
  onComposerChange: (value: string) => void;
  onEndCall: () => void;
  onReconnect: () => void;
  onRunDemo: () => void;
  onSend: (event: FormEvent) => void;
  onStartCall: () => void;
  submitting: boolean;
}) {
  const facts = stateFacts(currentTurn?.state ?? null);
  const trace = currentTurn?.trace ?? [];
  const decision = currentTurn?.engine_result.decision.status;
  return (
    <div className="page workbench-page">
      <section className="page-intro">
        <div>
          <span className="eyebrow">LIVE DEBUGGING</span>
          <h2>See every scheduling decision.</h2>
          <p>Send real text turns through the local scheduler, inspect what it understood, and verify every policy before a booking is made.</p>
        </div>
        <button className="scenario-button" type="button" onClick={onRunDemo} disabled={submitting}>
          <span>▶</span>
          <div><strong>{submitting ? "Running scenario…" : "Run golden scenario"}</strong><small>Dental · hard location constraint</small></div>
        </button>
      </section>

      <div className="workbench-grid">
        <section className="panel conversation-panel">
          <div className="panel-header">
            <div>
              <span className="panel-icon">◌</span>
              <div><h3>Conversation</h3><p>Live text API · voice integration pending</p></div>
            </div>
            <span className={`mode-pill api-${apiStatus}`}><i /> {apiStatus === "connected" ? "LIVE LOCAL API" : apiStatus === "offline" ? "BACKEND OFFLINE" : "CONNECTING"}</span>
          </div>

          <div className="transcript">
            <div className="time-divider"><span>{apiStatus === "connected" ? "CURRENT SESSION" : "LOCAL DEVELOPMENT"}</span></div>
            {apiError && (
              <div className="api-error" role="alert">
                <div><strong>Scheduling API unavailable</strong><p>{apiError}</p></div>
                <button type="button" onClick={onReconnect}>Reconnect</button>
              </div>
            )}
            {!messages.length && apiStatus === "connecting" && (
              <div className="conversation-empty"><span>◌</span><strong>Opening a local conversation…</strong></div>
            )}
            {messages.map((message) => message.role === "assistant" ? (
              <div className={`message assistant-message new-message ${message.grounded ? "highlighted" : ""}`} key={message.id}>
                <div className="message-avatar"><Mark /></div>
                <div>
                  <span className="speaker">PROSPER SCHEDULER</span>
                  <p>{message.text}</p>
                  {message.grounded && <span className="grounded-label">✓ Grounded in checked backend result</span>}
                </div>
              </div>
            ) : (
              <div className="message patient-message new-message" key={message.id}>
                <div><span className="speaker">PATIENT</span><p>{message.text}</p></div>
                <div className="patient-avatar">AM</div>
              </div>
            ))}
            {submitting && <div className="processing-row"><i /><i /><i /><span>Running deterministic checks</span></div>}
          </div>

          <form className="composer" onSubmit={onSend}>
            <button aria-label={callActive ? "End mocked voice call" : "Open mocked voice call"} className={callActive ? "mic-button listening" : "mic-button"} type="button" onClick={callActive ? onEndCall : onStartCall}>◉</button>
            <input aria-label="Patient message" value={composer} onChange={(event) => onComposerChange(event.target.value)} placeholder={apiStatus === "connected" ? "Type a patient message…" : "Connect the local API to send messages"} disabled={apiStatus !== "connected" || submitting} />
            <span>{submitting ? "Checking…" : "Local rules"}</span>
            <button aria-label="Send message" className="send-button" type="submit" disabled={apiStatus !== "connected" || submitting || !composer.trim()}>↑</button>
          </form>
        </section>

        <div className="inspector-stack">
          <section className="panel decision-panel">
            <div className="panel-header compact">
              <div><span className="panel-icon">↳</span><div><h3>Decision trace</h3><p>{currentTurn ? `Turn ${currentTurn.turn_number} · ${currentTurn.total_latency_ms} ms total` : "Waiting for a processed turn"}</p></div></div>
              <button aria-label="More decision options" type="button">•••</button>
            </div>
            <div className="trace-list">
              {trace.map((item, index) => (
                <div className={`trace-row ${item.tone}`} key={`${item.stage}-${index}`}>
                  <div className="trace-rail"><span>{item.tone === "warning" ? "!" : "✓"}</span>{index < trace.length - 1 && <i />}</div>
                  <div className="trace-copy">
                    <div><b>{item.stage}</b><time>{item.latency_ms} ms</time></div>
                    <strong>{item.title}</strong>
                    <p>{item.detail}</p>
                  </div>
                </div>
              ))}
              {!trace.length && <div className="trace-empty"><span>↳</span><p>Send a scheduling request to see extraction, resolution, policy, and decision stages.</p></div>}
            </div>
            {decision === "NO_EXACT_MATCH" && <div className="decision-callout"><div><span>!</span><strong>Hard constraint preserved</strong></div><p>The alternative remains separate until the patient explicitly agrees.</p></div>}
            {currentTurn?.booking && <div className="booking-callout"><div><span>✓</span><strong>Booking confirmed</strong></div><p>{currentTurn.booking.booking_id} · idempotent mock write</p></div>}
          </section>

          <section className="panel state-panel">
            <div className="panel-title-row"><div><span className="panel-icon">◇</span><h3>Canonical state</h3></div><span>VERSION {currentTurn?.state.version ?? 0}</span></div>
            <div className="fact-grid">
              {facts.map(([label, value]) => <div className="fact" key={label}><span>{label}</span><strong>{value}</strong></div>)}
            </div>
          </section>

          <section className="panel usage-panel">
            <div className="panel-title-row"><div><span className="panel-icon">↗</span><h3>Context usage</h3></div><span>{currentTurn?.extractor_mode.replaceAll("_", " ") ?? "NO TURN"}</span></div>
            <div className="usage-comparison">
              <div><span>Current extraction</span><strong>0 <small>LLM tokens</small></strong><i><b style={{ width: currentTurn ? "4%" : "0%" }} /></i></div>
              <div className="baseline"><span>Full catalog baseline</span><strong>— <small>not measured</small></strong><i><b style={{ width: "0%" }} /></i></div>
            </div>
            <p className="estimate-note">This slice uses the deterministic local extractor. Model cost telemetry will appear when structured LLM extraction is added.</p>
          </section>
        </div>
      </div>
    </div>
  );
}

function GraphEditor({
  nodePrompt,
  onPromptChange,
  onSave,
  onSelectNode,
  saved,
  selectedNode,
  selectedGraphNode,
}: {
  nodePrompt: string;
  onPromptChange: (value: string) => void;
  onSave: () => void;
  onSelectNode: (id: string) => void;
  saved: boolean;
  selectedNode: string;
  selectedGraphNode: (typeof graphNodes)[number];
}) {
  return (
    <div className="page graph-page">
      <section className="page-intro graph-intro">
        <div><span className="eyebrow">AGENT DESIGN</span><h2>Conversation graph</h2><p>Shape how the voice agent moves through a scheduling call.</p></div>
        <div className="graph-actions"><span className={saved ? "save-state saved" : "save-state"}>{saved ? "✓ All changes saved" : "• Unsaved changes"}</span><button className="secondary-button" type="button">Validate graph</button><button className="primary-button" type="button" onClick={onSave}>Save agent</button></div>
      </section>
      <div className="graph-layout">
        <section className="panel graph-canvas" aria-label="Agent node graph">
          <div className="canvas-toolbar"><div><button type="button">−</button><span>100%</span><button type="button">+</button></div><button type="button">Center graph</button></div>
          <div className="graph-wire wire-one" /><div className="graph-wire wire-two" /><div className="graph-wire wire-three" />
          {graphNodes.map((node) => (
            <button className={`graph-node ${node.position} ${selectedNode === node.id ? "selected" : ""}`} key={node.id} type="button" onClick={() => onSelectNode(node.id)}>
              <span className="node-dot" /><span className="node-eyebrow">{node.eyebrow}</span><strong>{node.title}</strong><p>{node.body}</p><small>{node.id}</small>
            </button>
          ))}
          <button className="add-node" type="button">＋ Add node</button>
          <div className="minimap"><i /><i /><i /><i /></div>
        </section>
        <aside className="panel node-inspector">
          <div className="inspector-heading"><div><span className="node-type-icon">↳</span><div><span>SELECTED NODE</span><h3>{selectedGraphNode.title}</h3></div></div><button type="button">•••</button></div>
          <label>Node ID<input value={selectedGraphNode.id} readOnly /></label>
          <label>Instruction<textarea value={nodePrompt} onChange={(event) => onPromptChange(event.target.value)} /></label>
          <div className="field-group"><div><span>TRANSITIONS</span><button type="button">＋ Add</button></div><div className="transition-card"><span>ON SUCCESS</span><strong>confirm</strong><i>→</i></div></div>
          <div className="field-group"><div><span>TOOLS</span><button type="button">＋ Add</button></div><div className="tool-card"><span>⚙</span><div><strong>run_scheduler</strong><small>Deterministic policy engine</small></div></div></div>
          <p className="mock-note">This editor is interactive frontend state. JSON loading and persistence will be connected in the backend API part.</p>
        </aside>
      </div>
    </div>
  );
}

function Catalog({ query, rows, onQueryChange }: { query: string; rows: typeof catalogRows; onQueryChange: (value: string) => void }) {
  return (
    <div className="page catalog-page">
      <section className="page-intro"><div><span className="eyebrow">CLINIC RESOURCES</span><h2>Catalog explorer</h2><p>Inspect the records that constrain every scheduling decision.</p></div><button className="primary-button" type="button">＋ Add resource</button></section>
      <div className="stat-strip"><div><strong>8</strong><span>Locations</span></div><div><strong>50</strong><span>Providers</span></div><div><strong>82</strong><span>Appointment types</span></div><div className="warning-stat"><strong>8</strong><span>Types without providers</span></div></div>
      <section className="panel catalog-table-panel">
        <div className="catalog-toolbar"><label><span>⌕</span><input value={query} onChange={(event) => onQueryChange(event.target.value)} placeholder="Search names, specialties, capabilities…" /></label><button type="button">All resources⌄</button><span className="mode-pill"><i /> SAMPLE VIEW</span></div>
        <div className="catalog-table"><div className="catalog-row table-head"><span>RESOURCE</span><span>NAME</span><span>DETAILS</span><span>STATUS</span></div>{rows.map((row) => <div className="catalog-row" key={`${row.type}-${row.name}`}><span><i className={`resource-icon ${row.type.replace(" ", "-").toLowerCase()}`}>{row.type === "Location" ? "⌖" : row.type === "Provider" ? "+" : "◷"}</i>{row.type}</span><strong>{row.name}</strong><span>{row.detail}</span><span className={row.detail.includes("No eligible") || row.detail.includes("needs context") ? "status-chip warning" : "status-chip"}>{row.detail.includes("No eligible") ? "Needs attention" : row.detail.includes("needs context") ? "Ambiguous" : "Ready"}</span></div>)}</div>
        {!rows.length && <div className="empty-search">No sample records match “{query}”.</div>}
      </section>
      <p className="section-footnote">Sample frontend rows only. The next backend slice will expose all 140 catalog records through search and filters.</p>
    </div>
  );
}

function Evaluations() {
  return (
    <div className="page evaluations-page">
      <section className="page-intro"><div><span className="eyebrow">RELIABILITY LAB</span><h2>Evaluation runs</h2><p>Measure each failure boundary instead of hiding errors behind one score.</p></div><button className="primary-button" type="button">▶ Run evaluation</button></section>
      <div className="evaluation-grid">
        {[
          ["Extraction", "Utterance → state patch", "24 fixtures"],
          ["Policy engine", "Canonical state → safe action", "32 fixtures"],
          ["Conversations", "Multi-turn task completion", "12 scenarios"],
        ].map(([title, copy, count], index) => <section className="panel evaluation-card" key={title}><div><span className="evaluation-number">0{index + 1}</span><span className="not-run">NOT RUN</span></div><h3>{title}</h3><p>{copy}</p><footer><span>{count} planned</span><strong>—</strong></footer></section>)}
      </div>
      <section className="panel evaluation-empty"><div className="evaluation-orbit"><span>✓</span></div><h3>No evaluation results yet</h3><p>The frontend contract is ready. Results will appear here after the evaluation API and fixtures are implemented.</p><div><span>Entity resolution accuracy</span><span>Invalid booking rate</span><span>Tokens per booking</span><span>Task success</span></div></section>
    </div>
  );
}
