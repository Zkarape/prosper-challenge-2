"use client";

import { type ComponentType, useEffect, useState } from "react";
import { AgentGraphEditor } from "./agent-graph-editor";
import { ClientObservability } from "./client-observability";
import { EngineLogicPanel } from "./engine-logic-panel";
import { EvaluationsPanel } from "./evaluations-panel";
import { SystemLogsPanel } from "./system-logs-panel";
import { ThemeControl } from "./theme-control";

type Tab = "test" | "graph" | "engine" | "evaluations" | "logs";
type VoiceCallPanelComponent = ComponentType<{
  endpoint: string;
  onSchedulingTurn: (turn: unknown) => void;
}>;

const tabs: { id: Tab; label: string }[] = [
  { id: "test", label: "Scheduling agent" },
  { id: "graph", label: "Agent graph" },
  { id: "engine", label: "Engine logic" },
  { id: "evaluations", label: "Evaluations" },
  { id: "logs", label: "System logs" },
];

const VOICE_AGENT_URL = process.env.NEXT_PUBLIC_VOICE_AGENT_URL ?? "http://127.0.0.1:7860";
const SCHEDULING_API_URL = process.env.NEXT_PUBLIC_SCHEDULING_API_URL ?? "http://127.0.0.1:8000";

type EntityState = { raw_text: string; requirement: "REQUIRED" | "PREFERRED" | "UNSPECIFIED" } | null;
type SchedulingStateDto = {
  current_goal: string | null;
  patient_status: string;
  referral_status: string;
  appointment_type: EntityState;
  provider: EntityState;
  location: EntityState;
  time: { raw_text: string; objective: string } | null;
};
type TraceEvent = {
  stage: string;
  latency_ms: number;
  title: string;
  detail: string;
  tone: string;
  data?: Record<string, unknown>;
};
type TurnResponse = {
  message_number: number;
  assistant_message: string;
  state: SchedulingStateDto;
  state_patch: Record<string, unknown>;
  trace: TraceEvent[];
  total_latency_ms: number;
  extractor_mode: string;
  usage: {
    model: string | null;
    input_tokens: number;
    cached_input_tokens: number;
    output_tokens: number;
  };
  booking: { booking_id: string; status: string } | null;
  engine_result: {
    decision: { status: string };
    next_action: { type: string };
  };
};

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
  const [VoiceCallPanel, setVoiceCallPanel] = useState<VoiceCallPanelComponent | null>(null);
  const [activeTab, setActiveTab] = useState<Tab>("test");
  const [currentTurn, setCurrentTurn] = useState<TurnResponse | null>(null);
  const [turnHistory, setTurnHistory] = useState<TurnResponse[]>([]);

  useEffect(() => {
    let mounted = true;
    void import("./voice-call-panel").then(({ VoiceCallPanel: LoadedVoiceCallPanel }) => {
      if (mounted) setVoiceCallPanel(() => LoadedVoiceCallPanel);
    });
    return () => {
      mounted = false;
    };
  }, []);

  return (
    <main className="app-shell">
      <ClientObservability apiUrl={SCHEDULING_API_URL} />
      <aside className="sidebar">
        <div className="brand">
          <Mark />
          <strong>Prosper</strong>
        </div>

        <nav className="primary-nav" aria-label="Agent workspace">
          {tabs.map((tab) => (
            <button
              className={activeTab === tab.id ? "nav-item active" : "nav-item"}
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              type="button"
            >
              {tab.label}
            </button>
          ))}
        </nav>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <h1>{tabs.find((tab) => tab.id === activeTab)?.label}</h1>
          <div className="topbar-actions">
            {activeTab === "test" && currentTurn && (
              <button className="inspect-turn-button" type="button" onClick={() => setActiveTab("graph")}>
                Inspect last turn →
              </button>
            )}
            <ThemeControl />
          </div>
        </header>

        {activeTab === "test" && (
          <div className="page test-page">
            {VoiceCallPanel ? (
              <VoiceCallPanel
                endpoint={VOICE_AGENT_URL}
                onSchedulingTurn={(value) => {
                  const turn = value as TurnResponse;
                  setCurrentTurn(turn);
                  setTurnHistory((history) => {
                    if (turn.message_number === 1) return [turn];
                    const previous = history.findIndex((item) => item.message_number === turn.message_number);
                    if (previous === -1) return [...history, turn];
                    return history.map((item, index) => index === previous ? turn : item);
                  });
                }}
              />
            ) : (
              <div className="voice-panel-loading">Loading voice test…</div>
            )}
            <UsageHistory turns={turnHistory} />
          </div>
        )}

        {activeTab === "graph" && (
          <AgentGraphEditor apiUrl={SCHEDULING_API_URL} currentTurn={currentTurn} />
        )}

        {activeTab === "evaluations" && (
          <EvaluationsPanel apiUrl={SCHEDULING_API_URL} />
        )}

        {activeTab === "engine" && <EngineLogicPanel />}

        {activeTab === "logs" && <SystemLogsPanel apiUrl={SCHEDULING_API_URL} />}

      </section>
    </main>
  );
}

function UsageHistory({ turns }: { turns: TurnResponse[] }) {
  const latest = turns.at(-1);
  const sessionTokens = turns.reduce(
    (total, turn) => total + turn.usage.input_tokens + turn.usage.output_tokens,
    0,
  );
  const largestInput = Math.max(...turns.map((turn) => turn.usage.input_tokens), 1);

  return (
    <section className="usage-history" aria-labelledby="usage-title">
      <header>
        <div>
          <h2 id="usage-title">Context usage</h2>
          <p>Input context for each patient request.</p>
        </div>
        <div className="usage-totals">
          <span><small>Latest input</small><strong>{latest?.usage.input_tokens.toLocaleString() ?? "—"}</strong></span>
          <span><small>Session total</small><strong>{sessionTokens ? sessionTokens.toLocaleString() : "—"}</strong></span>
        </div>
      </header>

      {turns.length === 0 ? (
        <p className="usage-empty">Token usage appears after the first completed request.</p>
      ) : (
        <div className="usage-table">
          <div className="usage-row usage-head">
            <span>Request</span><span>Input</span><span>Cached</span><span>Output</span><span>Total</span>
          </div>
          {turns.map((turn) => (
            <div className="usage-row" key={turn.message_number}>
              <span>Turn {turn.message_number}<i><b style={{ width: `${Math.max(3, (turn.usage.input_tokens / largestInput) * 100)}%` }} /></i></span>
              <strong>{turn.usage.input_tokens.toLocaleString()}</strong>
              <span>{turn.usage.cached_input_tokens.toLocaleString()}</span>
              <span>{turn.usage.output_tokens.toLocaleString()}</span>
              <strong>{(turn.usage.input_tokens + turn.usage.output_tokens).toLocaleString()}</strong>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
