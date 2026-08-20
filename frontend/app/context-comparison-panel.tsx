"use client";

import { useEffect, useState } from "react";

type Dataset = {
  scenario_count: number;
  turn_count: number;
  repetitions: number;
  scenario_trial_count: number;
  turn_count_per_strategy: number;
  total_patient_turns: number;
  review_status: string;
  methodology: string;
  available: boolean;
};

type Scenario = {
  scenario_id: string;
  title: string;
  status: "PASS" | "FAIL";
  differences: string[];
  trial: number;
};

type Strategy = {
  strategy: "compact" | "bounded_recent" | "full_history";
  scenario_count: number;
  repetitions: number;
  scenario_trial_count: number;
  passed_scenario_trials: number;
  passed_scenarios: number;
  accuracy_percent: number;
  patient_turn_count: number;
  model_call_count: number;
  input_tokens: number;
  cached_input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  estimated_cost_usd: number | null;
  model_latency_ms: number;
  average_first_turn_input_tokens: number;
  average_last_turn_input_tokens: number;
  tokens_per_passed_scenario: number | null;
  scenarios: Scenario[];
};

type ComparisonRun = {
  run_id: string;
  status: "RUNNING" | "COMPLETED" | "ERROR";
  model: string | null;
  started_at: string;
  duration_ms: number | null;
  dataset_review_status?: string;
  strategies: { compact: Strategy; bounded_recent: Strategy; full_history: Strategy } | null;
  comparison: {
    conclusion: string;
    input_tokens_saved: number;
    input_tokens_saved_percent: number;
    total_tokens_saved: number;
    total_tokens_saved_percent: number;
    estimated_cost_saved_usd: number | null;
    accuracy_delta_percentage_points: number;
    same_accuracy: boolean;
    both_strategies_passed_every_scenario: boolean;
  } | null;
  error?: string | null;
};

function number(value: number | null | undefined, digits = 0) {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function delay(milliseconds: number) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function conclusionText(value: string | undefined) {
  if (value === "SUPPORTED_WITHIN_BENCHMARK") return "Supported within this benchmark";
  if (value === "TRADEOFF_REQUIRES_REVIEW") return "Recent context lost accuracy";
  if (value === "NOT_SUPPORTED_TOKEN_SAVINGS") return "No token saving measured";
  if (value === "INCONCLUSIVE_ACCURACY") return "Accuracy evidence is inconclusive";
  return "Run the comparison to see the result";
}

export function ContextComparisonPanel({ apiUrl }: { apiUrl: string }) {
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [run, setRun] = useState<ComparisonRun | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const base = apiUrl.replace(/\/$/, "");
    void Promise.all([
      fetch(`${base}/api/evaluations/context-comparison/dataset`).then(async (response) => {
        if (!response.ok) throw new Error("The context comparison dataset could not be loaded.");
        return response.json() as Promise<Dataset>;
      }),
      fetch(`${base}/api/evaluations/context-comparison/runs/latest`).then(async (response) => (
        response.ok ? response.json() as Promise<ComparisonRun> : null
      )).catch(() => null),
    ]).then(([loadedDataset, latest]) => {
      if (!active) return;
      setDataset(loadedDataset);
      setRun(latest);
      if (latest?.status === "RUNNING") void poll(latest.run_id);
    }).catch((reason: unknown) => {
      if (active) setError(reason instanceof Error ? reason.message : "Context comparison is unavailable.");
    });
    return () => { active = false; };

    async function poll(runId: string) {
      setRunning(true);
      try {
        let current: ComparisonRun | null = null;
        for (let attempt = 0; attempt < 900; attempt += 1) {
          await delay(1000);
          const response = await fetch(`${base}/api/evaluations/context-comparison/runs/${runId}`);
          if (!response.ok) throw new Error("The context comparison run could not be read.");
          current = await response.json() as ComparisonRun;
          if (active) setRun(current);
          if (current.status !== "RUNNING") break;
        }
        if (current?.status === "ERROR") throw new Error(current.error ?? "The comparison failed.");
      } catch (reason) {
        if (active) setError(reason instanceof Error ? reason.message : "The comparison failed.");
      } finally {
        if (active) setRunning(false);
      }
    }
  }, [apiUrl]);

  async function execute() {
    setRunning(true);
    setError(null);
    const base = apiUrl.replace(/\/$/, "");
    try {
      const response = await fetch(`${base}/api/evaluations/context-comparison/runs`, { method: "POST" });
      const payload = await response.json() as ComparisonRun & { detail?: string };
      if (!response.ok) throw new Error(payload.detail ?? "The comparison could not start.");
      setRun(payload);
      let current = payload;
      for (let attempt = 0; attempt < 900 && current.status === "RUNNING"; attempt += 1) {
        await delay(1000);
        const status = await fetch(`${base}/api/evaluations/context-comparison/runs/${current.run_id}`);
        if (!status.ok) throw new Error("The comparison result could not be read.");
        current = await status.json() as ComparisonRun;
        setRun(current);
      }
      if (current.status === "ERROR") throw new Error(current.error ?? "The comparison failed.");
      if (current.status === "RUNNING") throw new Error("The comparison timed out.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The comparison failed.");
    } finally {
      setRunning(false);
    }
  }

  const compact = run?.strategies?.compact;
  const bounded = run?.strategies?.bounded_recent;
  const full = run?.strategies?.full_history;
  const comparison = run?.comparison;

  return (
    <section className="context-proof" aria-labelledby="context-proof-title">
      <header>
        <div>
          <span>Paired experiment</span>
          <h3 id="context-proof-title">How much conversation history does the extractor need?</h3>
          <p>Same model, patient messages, initial state, schema, and engine. Only the history window changes.</p>
        </div>
        <button
          className="primary"
          disabled={running || !dataset?.available}
          onClick={() => void execute()}
          type="button"
        >
          {running ? "Running paired trials…" : `Run ${dataset?.total_patient_turns ?? 0} patient turns`}
        </button>
      </header>

      {!dataset?.available && (
        <p className="context-proof-warning">Start the API with the structured LLM extractor. A local token estimate is not accepted as proof.</p>
      )}
      {error && <p className="context-proof-warning" role="alert">{error}</p>}

      <div className="context-proof-method">
        <span>{dataset?.scenario_count ?? "—"} multi-turn conversations</span>
        <span>{dataset?.repetitions ?? "—"} paired repetitions</span>
        <span>{dataset?.turn_count_per_strategy ?? "—"} patient turns per strategy</span>
        <span>Provider-reported token counts</span>
        <span>Draft expected outcomes</span>
      </div>

      <div className="context-proof-grid">
        <StrategyCard title="State only" detail="Latest message + patient request + pending offer" value={compact} />
        <StrategyCard title="Selected: recent context" detail="State only + the last patient/assistant exchange" value={bounded} />
        <StrategyCard title="Full history" detail="State only + every earlier patient and assistant message" value={full} />
      </div>

      <div className="context-proof-result">
        <div>
          <small>Measured result</small>
          <strong>{comparison ? `${number(comparison.input_tokens_saved_percent, 1)}%` : "—"}</strong>
          <span>input tokens saved vs full history</span>
        </div>
        <div>
          <b className={comparison?.conclusion === "SUPPORTED_WITHIN_BENCHMARK" ? "supported" : ""}>
            {conclusionText(comparison?.conclusion)}
          </b>
          {comparison && (
            <dl>
              <div><dt>Tokens avoided</dt><dd>{number(comparison.input_tokens_saved)}</dd></div>
              <div><dt>Accuracy change</dt><dd>{comparison.accuracy_delta_percentage_points > 0 ? "+" : ""}{number(comparison.accuracy_delta_percentage_points, 1)} pp</dd></div>
              <div><dt>Estimated cost avoided</dt><dd>{comparison.estimated_cost_saved_usd == null ? "—" : `$${number(comparison.estimated_cost_saved_usd, 5)}`}</dd></div>
            </dl>
          )}
        </div>
      </div>

      {compact && bounded && full && (
        <div className="context-scenario-table" role="table" aria-label="Context comparison scenarios">
          <div role="row"><strong role="columnheader">Conversation</strong><strong role="columnheader">State only</strong><strong role="columnheader">Recent</strong><strong role="columnheader">Full</strong></div>
          {scenarioRows(compact, bounded, full).map((scenario) => (
            <div role="row" key={scenario.scenarioId}>
              <span role="cell">{scenario.title}</span>
              <b className={scenario.compact.passed === scenario.compact.total ? "pass" : "fail"} role="cell">{scenario.compact.passed}/{scenario.compact.total}</b>
              <b className={scenario.bounded.passed === scenario.bounded.total ? "pass" : "fail"} role="cell">{scenario.bounded.passed}/{scenario.bounded.total}</b>
              <b className={scenario.full.passed === scenario.full.total ? "pass" : "fail"} role="cell">{scenario.full.passed}/{scenario.full.total}</b>
            </div>
          ))}
        </div>
      )}

      <footer>
        <strong>What this can prove</strong>
        <p>A successful run supports recent context for these conversations and this model. It does not prove universal accuracy; the draft expectations still need human review and broader coverage.</p>
      </footer>
    </section>
  );
}

function StrategyCard({ title, detail, value }: { title: string; detail: string; value?: Strategy }) {
  return (
    <article className="context-strategy-card">
      <header><h4>{title}</h4><p>{detail}</p></header>
      <dl>
        <div><dt>Passed trials</dt><dd>{value ? `${value.passed_scenario_trials ?? value.passed_scenarios}/${value.scenario_trial_count ?? value.scenario_count}` : "—"}</dd></div>
        <div><dt>Input tokens</dt><dd>{number(value?.input_tokens)}</dd></div>
        <div><dt>Cached input</dt><dd>{number(value?.cached_input_tokens)}</dd></div>
        <div><dt>Total tokens</dt><dd>{number(value?.total_tokens)}</dd></div>
        <div><dt>Average first turn</dt><dd>{number(value?.average_first_turn_input_tokens)}</dd></div>
        <div><dt>Average last turn</dt><dd>{number(value?.average_last_turn_input_tokens)}</dd></div>
      </dl>
    </article>
  );
}

function scenarioRows(compact: Strategy, bounded: Strategy, full: Strategy) {
  const summarize = (strategy: Strategy, scenarioId: string) => {
    const trials = strategy.scenarios.filter((item) => item.scenario_id === scenarioId);
    return { passed: trials.filter((item) => item.status === "PASS").length, total: trials.length };
  };
  const unique = Array.from(new Map(compact.scenarios.map((item) => [item.scenario_id, item])).values());
  return unique.map((item) => ({
    scenarioId: item.scenario_id,
    title: item.title,
    compact: summarize(compact, item.scenario_id),
    bounded: summarize(bounded, item.scenario_id),
    full: summarize(full, item.scenario_id),
  }));
}
