"use client";

import { useEffect, useMemo, useState } from "react";
import { ContextComparisonPanel } from "./context-comparison-panel";

type EvaluationCase = {
  test_case_id: string;
  title: string;
  tags: string[];
  definition_complete: boolean;
  input: {
    state_before: Record<string, unknown>;
    pending_action_before: unknown;
    patient_utterance: string;
  };
  expected: {
    engine: { decision_status: string; action_type: string };
  };
};

type EvaluationDataset = {
  dataset_version: string;
  case_count: number;
  defined_case_count: number;
  manual_authored_case_count: number;
  cases: EvaluationCase[];
};

type StageResult = {
  id: "extraction" | "validation" | "state" | "engine";
  label: string;
  status: "PASS" | "FAIL" | "ERROR" | "SKIPPED";
  expected: unknown;
  actual: unknown;
  differences: string[];
};

type CaseResult = {
  test_case_id: string;
  title: string;
  overall_status: "PASS" | "FAIL" | "ERROR";
  duration_ms: number;
  stages: StageResult[];
  usage: {
    model: string | null;
    input_tokens: number;
    cached_input_tokens: number;
    output_tokens: number;
    total_tokens: number;
    estimated_cost_usd: number | null;
  };
  error: string | null;
};

type EvaluationRun = {
  run_id: string;
  status: "RUNNING" | "COMPLETED" | "ERROR";
  dataset_version: string;
  extractor_mode: string;
  model: string | null;
  started_at: string;
  duration_ms: number | null;
  summary: {
    case_count: number;
    passed_case_count: number;
    failed_case_count: number;
    error_case_count: number;
    extraction_passed: number;
    validation_passed: number;
    state_passed: number;
    engine_passed: number;
    total_tokens: number;
    estimated_cost_usd: number | null;
  } | null;
  cases: CaseResult[];
  error?: string | null;
};

type Health = { extractor_mode: string };
type ResultFilter = "all" | "passed" | "failed" | "not_run";
type ExtractorChoice = "configured" | "local";

const pipeline = [
  ["1", "Extraction", "Turn the latest patient words into typed facts."],
  ["2", "Validation", "Reject facts that are unsupported or unsafe."],
  ["3", "State update", "Apply only the validated changes."],
  ["4", "Engine", "Run catalog and scheduling rules without an LLM."],
] as const;

function words(value: string | null | undefined) {
  return value ? value.replaceAll("_", " ").toLocaleLowerCase() : "not available";
}

function formatNumber(value: number | null | undefined, digits = 0) {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function stateValue(value: unknown): string {
  if (value === null || value === undefined) return "None";
  if (Array.isArray(value)) return value.length ? value.map(String).join(", ") : "None";
  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    return String(record.raw_text ?? record.value ?? JSON.stringify(record));
  }
  return String(value).replaceAll("_", " ").toLocaleLowerCase();
}

function delay(milliseconds: number) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

export function EvaluationsPanel({ apiUrl }: { apiUrl: string }) {
  const [dataset, setDataset] = useState<EvaluationDataset | null>(null);
  const [run, setRun] = useState<EvaluationRun | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState<"selected" | "all" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedCaseId, setSelectedCaseId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [resultFilter, setResultFilter] = useState<ResultFilter>("all");
  const [extractor, setExtractor] = useState<ExtractorChoice>("configured");

  useEffect(() => {
    let active = true;
    const base = apiUrl.replace(/\/$/, "");
    void Promise.all([
      fetch(`${base}/api/evaluations/dataset`).then(async (response) => {
        if (!response.ok) throw new Error("The 40 test cases could not be loaded.");
        return response.json() as Promise<EvaluationDataset>;
      }),
      fetch(`${base}/health`).then(async (response) => (
        response.ok ? response.json() as Promise<Health> : null
      )).catch(() => null),
      fetch(`${base}/api/evaluations/runs/latest`).then(async (response) => (
        response.ok ? response.json() as Promise<EvaluationRun> : null
      )).catch(() => null),
    ]).then(([loadedDataset, loadedHealth, latestRun]) => {
      if (!active) return;
      setDataset(loadedDataset);
      setHealth(loadedHealth);
      setRun(latestRun);
      setSelectedCaseId(loadedDataset.cases[0]?.test_case_id ?? null);
      setLoading(false);
      if (latestRun?.status === "RUNNING") {
        setRunning("all");
        void (async () => {
          let current = latestRun;
          for (let attempt = 0; attempt < 600 && current.status === "RUNNING"; attempt += 1) {
            await delay(1000);
            const response = await fetch(`${base}/api/evaluations/runs/${current.run_id}`);
            if (!response.ok) throw new Error("The evaluation job could not be read.");
            current = await response.json() as EvaluationRun;
            if (active) setRun(current);
          }
          if (current.status === "ERROR") throw new Error(current.error ?? "The evaluation job failed.");
        })().catch((reason: unknown) => {
          if (active) setError(reason instanceof Error ? reason.message : "The evaluation job failed.");
        }).finally(() => {
          if (active) setRunning(null);
        });
      }
    }).catch((reason: unknown) => {
      if (!active) return;
      setError(reason instanceof Error ? reason.message : "The evaluation API is unavailable.");
      setLoading(false);
    });
    return () => { active = false; };
  }, [apiUrl]);

  const resultsByCase = useMemo(() => new Map(
    (run?.cases ?? []).map((item) => [item.test_case_id, item]),
  ), [run]);

  const filteredCases = useMemo(() => {
    if (!dataset) return [];
    const normalized = query.trim().toLocaleLowerCase();
    return dataset.cases.filter((item) => {
      const result = resultsByCase.get(item.test_case_id);
      const filterMatches = resultFilter === "all"
        || (resultFilter === "passed" && result?.overall_status === "PASS")
        || (resultFilter === "failed" && result && result.overall_status !== "PASS")
        || (resultFilter === "not_run" && !result);
      const searchMatches = !normalized || [
        item.test_case_id,
        item.title,
        item.input.patient_utterance,
        ...item.tags,
      ].join(" ").toLocaleLowerCase().includes(normalized);
      return filterMatches && searchMatches;
    });
  }, [dataset, query, resultFilter, resultsByCase]);

  const selectedCase = dataset?.cases.find((item) => item.test_case_id === selectedCaseId)
    ?? filteredCases[0]
    ?? null;
  const selectedResult = selectedCase ? resultsByCase.get(selectedCase.test_case_id) ?? null : null;

  async function execute(scope: "selected" | "all") {
    if (!dataset || (scope === "selected" && !selectedCase)) return;
    setRunning(scope);
    setError(null);
    try {
      const response = await fetch(`${apiUrl.replace(/\/$/, "")}/api/evaluations/runs`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          extractor,
          case_ids: scope === "selected" ? [selectedCase?.test_case_id] : null,
        }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => null) as { detail?: string } | null;
        throw new Error(payload?.detail ?? "The evaluation run failed.");
      }
      let current = await response.json() as EvaluationRun;
      setRun(current);
      for (let attempt = 0; attempt < 600 && current.status === "RUNNING"; attempt += 1) {
        await delay(1000);
        const statusResponse = await fetch(
          `${apiUrl.replace(/\/$/, "")}/api/evaluations/runs/${current.run_id}`,
        );
        if (!statusResponse.ok) throw new Error("The evaluation job could not be read.");
        current = await statusResponse.json() as EvaluationRun;
        setRun(current);
      }
      if (current.status === "ERROR") {
        throw new Error(current.error ?? "The evaluation job failed.");
      }
      if (current.status !== "COMPLETED") {
        throw new Error("The evaluation job timed out.");
      }
      setResultFilter("all");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The evaluation run failed.");
    } finally {
      setRunning(null);
    }
  }

  function exportRun() {
    if (!run || run.status !== "COMPLETED") return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(run, null, 2)], {
      type: "application/json",
    }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `${run.run_id}.json`;
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="page evaluations-page">
      <header className="evaluations-header">
        <div>
          <h2>Prove the scheduling pipeline</h2>
          <p>A case passes only when all four stages match the expected result.</p>
        </div>
        {run?.status === "COMPLETED" && <button type="button" onClick={exportRun}>Export run</button>}
      </header>

      {error && <div className="evaluation-notice" role="alert">{error}</div>}

      <ContextComparisonPanel apiUrl={apiUrl} />

      <section className="evaluation-how" aria-label="How evaluation works">
        <header>
          <div>
            <strong>Start small, then build confidence</strong>
            <span>Run one case, inspect each stage, then run all 40.</span>
          </div>
          <div className="evaluation-actions">
            <label>
              Extractor
              <select value={extractor} onChange={(event) => setExtractor(event.target.value as ExtractorChoice)}>
                <option value="configured">
                  {health?.extractor_mode === "OPENAI_STRUCTURED" ? "Structured LLM" : "Configured extractor"}
                </option>
                <option value="local">Local smoke test</option>
              </select>
            </label>
            <button disabled={Boolean(running) || !selectedCase} type="button" onClick={() => void execute("selected")}>
              {running === "selected" ? "Running…" : "Run selected case"}
            </button>
            <button className="primary" disabled={Boolean(running) || !dataset} type="button" onClick={() => void execute("all")}>
              {running === "all" ? "Running 40 cases…" : "Run all 40"}
            </button>
          </div>
        </header>
        <div className="evaluation-pipeline">
          {pipeline.map(([number, title, description]) => (
            <div key={title}><span>{number}</span><strong>{title}</strong><small>{description}</small></div>
          ))}
        </div>
        <p className="evaluation-mode-note">
          {extractor === "local"
            ? "Local smoke test checks the wiring with no model calls or token cost. It does not measure LLM quality."
            : health?.extractor_mode === "OPENAI_STRUCTURED"
              ? "Structured LLM uses the same extractor configured for the product. The grades themselves stay deterministic."
              : "The backend is currently configured with the local extractor. Set EXTRACTOR_MODE=openai to measure the structured LLM."}
        </p>
      </section>

      <section className="evaluation-metrics" aria-label="Latest evaluation result">
        <Metric label="Cases passed" value={run?.summary ? `${run.summary.passed_case_count} / ${run.summary.case_count}` : run?.status === "RUNNING" ? "Running…" : "Not run"} />
        <Metric label="Extraction" value={run?.summary ? `${run.summary.extraction_passed} / ${run.summary.case_count}` : "—"} />
        <Metric label="Validation" value={run?.summary ? `${run.summary.validation_passed} / ${run.summary.case_count}` : "—"} />
        <Metric label="State update" value={run?.summary ? `${run.summary.state_passed} / ${run.summary.case_count}` : "—"} />
        <Metric label="Engine" value={run?.summary ? `${run.summary.engine_passed} / ${run.summary.case_count}` : "—"} />
      </section>

      {run?.summary && (
        <div className="evaluation-run-meta">
          <span>{words(run.extractor_mode)}{run.model ? ` · ${run.model}` : ""}</span>
          <span>{formatNumber(run.summary.total_tokens)} tokens</span>
          <span>{run.summary.estimated_cost_usd == null ? "No priced model calls" : `$${formatNumber(run.summary.estimated_cost_usd, 4)}`}</span>
          {run.summary.error_case_count > 0 && <span>{run.summary.error_case_count} system error{run.summary.error_case_count === 1 ? "" : "s"}</span>}
          <span>{formatNumber(run.duration_ms)} ms</span>
          <span>{new Date(run.started_at).toLocaleString()}</span>
        </div>
      )}

      <section className="evaluation-suite">
        <header className="suite-header">
          <div>
            <h3>40-case test dataset</h3>
            <p>
              {dataset
                ? `${dataset.manual_authored_case_count} expected results are manually reviewed; ${dataset.defined_case_count - dataset.manual_authored_case_count} are draft expectations to review.`
                : loading ? "Loading cases…" : "Dataset unavailable."}
            </p>
          </div>
          {dataset && <span>Dataset {dataset.dataset_version}</span>}
        </header>

        <div className="evaluation-browser">
          <aside className="evaluation-case-list">
            <div className="evaluation-filters">
              <input
                aria-label="Search evaluation cases"
                placeholder="Search cases"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
              <div>
                {(["all", "passed", "failed", "not_run"] as const).map((filter) => (
                  <button
                    className={resultFilter === filter ? "active" : ""}
                    key={filter}
                    type="button"
                    onClick={() => setResultFilter(filter)}
                  >
                    {filter === "not_run" ? "Not run" : filter[0].toUpperCase() + filter.slice(1)}
                  </button>
                ))}
              </div>
            </div>
            <div className="evaluation-case-scroll">
              {filteredCases.map((item) => {
                const result = resultsByCase.get(item.test_case_id);
                return (
                  <button
                    className={selectedCase?.test_case_id === item.test_case_id ? "evaluation-case active" : "evaluation-case"}
                    key={item.test_case_id}
                    type="button"
                    onClick={() => setSelectedCaseId(item.test_case_id)}
                  >
                    <span>{item.test_case_id.replace("case_", "")}</span>
                    <strong>{item.title}</strong>
                    <small className={result ? `result-${result.overall_status.toLocaleLowerCase()}` : ""}>
                      {result ? result.overall_status : "Not run"}
                    </small>
                  </button>
                );
              })}
              {!loading && filteredCases.length === 0 && <p className="evaluation-no-cases">No cases match this filter.</p>}
            </div>
          </aside>

          <div className="evaluation-case-detail">
            {selectedCase ? (
              <CaseDetail
                item={selectedCase}
                result={selectedResult}
                running={running === "selected"}
                onRun={() => void execute("selected")}
              />
            ) : <div className="evaluation-detail-empty">Select a case to inspect it.</div>}
          </div>
        </div>
      </section>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="evaluation-metric"><span>{label}</span><strong>{value}</strong></div>;
}

function CaseDetail({
  item,
  result,
  running,
  onRun,
}: {
  item: EvaluationCase;
  result: CaseResult | null;
  running: boolean;
  onRun: () => void;
}) {
  const usefulState = Object.entries(item.input.state_before).filter(([key, value]) => (
    value !== null && !["version", "selected_candidate_id", "selected_slot_id", "confirmed_state_version"].includes(key)
  ));

  return (
    <article>
      <header className="case-detail-header">
        <div>
          <span>{item.test_case_id}</span>
          <h3>{item.title}</h3>
        </div>
        <div className="case-detail-actions">
          <span className={`case-definition ${result ? result.overall_status.toLocaleLowerCase() : "not-run"}`}>
            {result?.overall_status ?? "Not run"}
          </span>
          <button disabled={running} type="button" onClick={onRun}>{running ? "Running…" : "Run this case"}</button>
        </div>
      </header>

      <section className="case-patient-input">
        <h4>Patient says</h4>
        <blockquote>{item.input.patient_utterance}</blockquote>
      </section>

      <section className="case-starting-facts">
        <h4>Facts before this turn</h4>
        {usefulState.length || item.input.pending_action_before ? (
          <dl>
            {usefulState.map(([key, value]) => (
              <div key={key}><dt>{words(key)}</dt><dd>{stateValue(value)}</dd></div>
            ))}
            {Boolean(item.input.pending_action_before) && <div><dt>pending question</dt><dd>Yes</dd></div>}
          </dl>
        ) : <p>No prior scheduling facts.</p>}
      </section>

      {result ? (
        <>
          <section className="case-result-summary">
            <span>{formatNumber(result.usage.total_tokens)} tokens</span>
            <span>{formatNumber(result.duration_ms)} ms</span>
            {result.usage.estimated_cost_usd != null && <span>${formatNumber(result.usage.estimated_cost_usd, 5)}</span>}
          </section>
          <section className="stage-results">
            <h4>What happened</h4>
            {result.stages.map((stage, index) => (
              <StageCard key={stage.id} stage={stage} number={index + 1} />
            ))}
          </section>
        </>
      ) : (
        <section className="case-not-run">
          <strong>Run this case first.</strong>
          <p>It will use the real extraction, validation, state, and engine code. Nothing here is marked as passed before execution.</p>
          <p>Expected finish: {words(item.expected.engine.decision_status)} → {words(item.expected.engine.action_type)}</p>
        </section>
      )}
    </article>
  );
}

function StageCard({ stage, number }: { stage: StageResult; number: number }) {
  return (
    <div className={`stage-result ${stage.status.toLocaleLowerCase()}`}>
      <header>
        <span>{number}</span>
        <strong>{stage.label}</strong>
        <small>{stage.status}</small>
      </header>
      {stage.differences.length ? (
        <ul>{stage.differences.map((difference) => <li key={difference}>{difference}</li>)}</ul>
      ) : <p>Actual result matched the expected result.</p>}
      <details>
        <summary>Compare actual and expected</summary>
        <div>
          <section><span>Expected</span><pre>{JSON.stringify(stage.expected, null, 2)}</pre></section>
          <section><span>Actual</span><pre>{JSON.stringify(stage.actual, null, 2)}</pre></section>
        </div>
      </details>
    </div>
  );
}
