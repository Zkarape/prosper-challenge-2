"use client";

import { FormEvent, useMemo, useState } from "react";

type Tab = "workbench" | "graph" | "catalog" | "evaluations";

const tabs: { id: Tab; label: string; glyph: string }[] = [
  { id: "workbench", label: "Workbench", glyph: "◫" },
  { id: "graph", label: "Agent graph", glyph: "⌘" },
  { id: "catalog", label: "Catalog", glyph: "▦" },
  { id: "evaluations", label: "Evaluations", glyph: "✓" },
];

const facts = [
  ["Intent", "Book appointment"],
  ["Patient", "New"],
  ["Visit", "Dental Cleaning"],
  ["Provider", "Dr. Wei Lee · preferred"],
  ["Location", "Richmond · required"],
  ["Time", "Earliest available"],
];

const trace = [
  {
    label: "Extract",
    time: "312 ms",
    title: "6 facts captured",
    detail: "State patch validated · v1",
    tone: "success",
  },
  {
    label: "Resolve",
    time: "0.8 ms",
    title: "3 entities resolved",
    detail: "Exact and contextual catalog matches",
    tone: "success",
  },
  {
    label: "Policies",
    time: "0.2 ms",
    title: "1 constraint failed",
    detail: "Richmond lacks the dental capability",
    tone: "warning",
  },
  {
    label: "Decision",
    time: "0.3 ms",
    title: "Offer a safe alternative",
    detail: "Mission District · patient permission required",
    tone: "active",
  },
];

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

export default function Home() {
  const [activeTab, setActiveTab] = useState<Tab>("workbench");
  const [callActive, setCallActive] = useState(false);
  const [composer, setComposer] = useState("");
  const [demoTurn, setDemoTurn] = useState(0);
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

  function sendMessage(event: FormEvent) {
    event.preventDefault();
    if (!composer.trim()) return;
    setDemoTurn((turn) => Math.min(turn + 1, 2));
    setComposer("");
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
            <span className="demo-badge"><i /> Frontend demo</span>
            <button className="secondary-button" type="button">Preview JSON</button>
            <button className="primary-button" type="button" onClick={() => setCallActive(true)}>
              <span aria-hidden="true">◉</span> Test agent
            </button>
          </div>
        </header>

        {activeTab === "workbench" && (
          <Workbench
            callActive={callActive}
            composer={composer}
            demoTurn={demoTurn}
            onComposerChange={setComposer}
            onEndCall={() => setCallActive(false)}
            onRunDemo={() => setDemoTurn((turn) => (turn + 1) % 3)}
            onSend={sendMessage}
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
            <span>Listening through browser microphone</span>
          </div>
          <div className="waveform" aria-hidden="true">
            {Array.from({ length: 12 }).map((_, index) => <i key={index} />)}
          </div>
          <button type="button" onClick={() => setCallActive(false)}>End call</button>
        </div>
      )}
    </main>
  );
}

function Workbench({
  callActive,
  composer,
  demoTurn,
  onComposerChange,
  onEndCall,
  onRunDemo,
  onSend,
}: {
  callActive: boolean;
  composer: string;
  demoTurn: number;
  onComposerChange: (value: string) => void;
  onEndCall: () => void;
  onRunDemo: () => void;
  onSend: (event: FormEvent) => void;
}) {
  return (
    <div className="page workbench-page">
      <section className="page-intro">
        <div>
          <span className="eyebrow">LIVE DEBUGGING</span>
          <h2>See every scheduling decision.</h2>
          <p>Talk to the agent, inspect what it understood, and verify every policy before a booking is made.</p>
        </div>
        <button className="scenario-button" type="button" onClick={onRunDemo}>
          <span>▶</span>
          <div><strong>Run golden scenario</strong><small>Dental · hard location constraint</small></div>
        </button>
      </section>

      <div className="workbench-grid">
        <section className="panel conversation-panel">
          <div className="panel-header">
            <div>
              <span className="panel-icon">◌</span>
              <div><h3>Conversation</h3><p>Text fallback · browser voice ready</p></div>
            </div>
            <span className="mode-pill"><i /> DEMO DATA</span>
          </div>

          <div className="transcript">
            <div className="time-divider"><span>10:42 AM</span></div>
            <div className="message assistant-message">
              <div className="message-avatar"><Mark /></div>
              <div>
                <span className="speaker">PROSPER SCHEDULER</span>
                <p>Hi, I’m the clinic’s scheduling assistant. How can I help today?</p>
              </div>
            </div>
            <div className="message patient-message">
              <div>
                <span className="speaker">PATIENT</span>
                <p>I’m a new patient looking for the earliest dental cleaning with Dr. Wei Lee. Richmond is a must.</p>
              </div>
              <div className="patient-avatar">AM</div>
            </div>
            <div className="message assistant-message highlighted">
              <div className="message-avatar"><Mark /></div>
              <div>
                <span className="speaker">PROSPER SCHEDULER</span>
                <p>Dr. Lee can do dental cleanings, but the Richmond center isn’t equipped for dental care. I can check the Mission District clinic instead—would that work?</p>
                <span className="grounded-label">✓ Grounded in checked result</span>
              </div>
            </div>
            {demoTurn > 0 && (
              <div className="message patient-message new-message">
                <div><span className="speaker">PATIENT</span><p>Yes, Mission District works.</p></div>
                <div className="patient-avatar">AM</div>
              </div>
            )}
            {demoTurn > 1 && (
              <div className="message assistant-message new-message">
                <div className="message-avatar"><Mark /></div>
                <div><span className="speaker">PROSPER SCHEDULER</span><p>The earliest opening is Thursday, August 20 at 10:30 AM. Would you like me to reserve it?</p></div>
              </div>
            )}
          </div>

          <form className="composer" onSubmit={onSend}>
            <button aria-label={callActive ? "End voice call" : "Start voice call"} className={callActive ? "mic-button listening" : "mic-button"} type="button" onClick={callActive ? onEndCall : onRunDemo}>◉</button>
            <input aria-label="Patient message" value={composer} onChange={(event) => onComposerChange(event.target.value)} placeholder="Type a patient message…" />
            <span>Demo mode</span>
            <button aria-label="Send message" className="send-button" type="submit">↑</button>
          </form>
        </section>

        <div className="inspector-stack">
          <section className="panel decision-panel">
            <div className="panel-header compact">
              <div><span className="panel-icon">↳</span><div><h3>Decision trace</h3><p>Turn 1 · 314 ms total</p></div></div>
              <button aria-label="More decision options" type="button">•••</button>
            </div>
            <div className="trace-list">
              {trace.map((item, index) => (
                <div className={`trace-row ${item.tone}`} key={item.label}>
                  <div className="trace-rail"><span>{item.tone === "warning" ? "!" : "✓"}</span>{index < trace.length - 1 && <i />}</div>
                  <div className="trace-copy">
                    <div><b>{item.label}</b><time>{item.time}</time></div>
                    <strong>{item.title}</strong>
                    <p>{item.detail}</p>
                  </div>
                </div>
              ))}
            </div>
            <div className="decision-callout">
              <div><span>!</span><strong>Hard constraint preserved</strong></div>
              <p>Mission District is an alternative, not a valid match, until the patient agrees.</p>
            </div>
          </section>

          <section className="panel state-panel">
            <div className="panel-title-row"><div><span className="panel-icon">◇</span><h3>Canonical state</h3></div><span>VERSION 1</span></div>
            <div className="fact-grid">
              {facts.map(([label, value]) => <div className="fact" key={label}><span>{label}</span><strong>{value}</strong></div>)}
            </div>
          </section>

          <section className="panel usage-panel">
            <div className="panel-title-row"><div><span className="panel-icon">↗</span><h3>Context efficiency</h3></div><span>THIS TURN</span></div>
            <div className="usage-comparison">
              <div><span>Structured approach</span><strong>684 <small>tokens</small></strong><i><b style={{ width: "18%" }} /></i></div>
              <div className="baseline"><span>Full catalog baseline</span><strong>~11.4k <small>tokens</small></strong><i><b style={{ width: "100%" }} /></i></div>
            </div>
            <p className="estimate-note">Illustrative frontend values · real usage arrives with backend telemetry.</p>
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
