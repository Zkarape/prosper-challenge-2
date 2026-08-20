"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

type LogEvent = {
  timestamp: string | null;
  level: string;
  process: string;
  component: string;
  event: string;
  message: string;
  source_file: string;
  exception: string | null;
  fields: Record<string, unknown>;
};

type LoggingStatus = {
  directory: string;
  level: string;
  transcripts_in_logs: boolean;
  files: { name: string; size_bytes: number }[];
};

type LogsResponse = {
  events: LogEvent[];
  logging: LoggingStatus;
};

const levels = ["ALL", "INFO", "WARNING", "ERROR"];
const processes = ["ALL", "api", "voice"];

function formatTime(value: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    fractionalSecondDigits: 3,
  });
}

function compactFields(fields: Record<string, unknown>) {
  const preferred = [
    "request_id",
    "conversation_id",
    "turn_id",
    "status_code",
    "duration_ms",
    "next_action",
    "outcome",
  ];
  return preferred
    .filter((key) => fields[key] !== undefined && fields[key] !== null)
    .map((key) => `${key}=${String(fields[key])}`);
}

export function SystemLogsPanel({ apiUrl }: { apiUrl: string }) {
  const [events, setEvents] = useState<LogEvent[]>([]);
  const [logging, setLogging] = useState<LoggingStatus | null>(null);
  const [process, setProcess] = useState("ALL");
  const [level, setLevel] = useState("ALL");
  const [search, setSearch] = useState("");
  const [live, setLive] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const query = useMemo(() => {
    const params = new URLSearchParams({ limit: "300" });
    if (process !== "ALL") params.set("process", process);
    if (level !== "ALL") params.set("level", level);
    if (search.trim()) params.set("search", search.trim());
    return params.toString();
  }, [level, process, search]);

  const refresh = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const response = await fetch(`${apiUrl}/api/system/logs?${query}`, {
        cache: "no-store",
      });
      if (!response.ok) {
        throw new Error(response.status === 404
          ? "The local log viewer is disabled in this environment."
          : `Log API returned ${response.status}.`);
      }
      const payload = (await response.json()) as LogsResponse;
      setEvents(payload.events);
      setLogging(payload.logging);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load system logs.");
    } finally {
      if (!quiet) setLoading(false);
    }
  }, [apiUrl, query]);

  useEffect(() => {
    const timeout = window.setTimeout(() => void refresh(), 0);
    return () => window.clearTimeout(timeout);
  }, [refresh]);

  useEffect(() => {
    if (!live) return;
    const interval = window.setInterval(() => void refresh(true), 2000);
    return () => window.clearInterval(interval);
  }, [live, refresh]);

  return (
    <div className="page system-logs-page">
      <header className="system-logs-header">
        <div>
          <span className="logs-eyebrow">Local observability</span>
          <h2>Follow every request and scheduling turn</h2>
          <p>Search structured events by request, conversation, turn, error, or component.</p>
        </div>
        <div className="logs-health">
          <i className={error ? "offline" : live ? "live" : ""} />
          <span>{error ? "Unavailable" : live ? "Live · 2s" : "Paused"}</span>
        </div>
      </header>

      <section className="logs-toolbar" aria-label="Log filters">
        <label>
          <span>Search</span>
          <input
            aria-label="Search logs"
            onChange={(event) => setSearch(event.target.value)}
            placeholder="conversation ID, event, exception…"
            value={search}
          />
        </label>
        <label>
          <span>Process</span>
          <select value={process} onChange={(event) => setProcess(event.target.value)}>
            {processes.map((value) => <option key={value}>{value}</option>)}
          </select>
        </label>
        <label>
          <span>Level</span>
          <select value={level} onChange={(event) => setLevel(event.target.value)}>
            {levels.map((value) => <option key={value}>{value}</option>)}
          </select>
        </label>
        <button className={live ? "logs-live active" : "logs-live"} onClick={() => setLive((value) => !value)} type="button">
          <i /> Live
        </button>
        <button className="logs-refresh" onClick={() => void refresh()} type="button">Refresh</button>
      </section>

      {logging && (
        <aside className="logs-safety">
          <strong>{logging.transcripts_in_logs ? "Transcript logging is ON" : "Privacy-safe mode"}</strong>
          <span>{logging.transcripts_in_logs
            ? "Patient wording may be present. Disable LOG_INCLUDE_TRANSCRIPTS before deployment."
            : "Patient wording is excluded; only character and word counts are recorded."}</span>
          <code>{logging.directory}</code>
        </aside>
      )}

      <section className="logs-console" aria-live="polite">
        <div className="logs-console-head">
          <span>Time</span><span>Level</span><span>Source</span><span>Event</span><span>Context</span>
        </div>
        {error ? (
          <div className="logs-state error"><strong>Logs unavailable</strong><span>{error}</span></div>
        ) : loading ? (
          <div className="logs-state"><span>Loading structured events…</span></div>
        ) : events.length === 0 ? (
          <div className="logs-state"><strong>No matching events</strong><span>Run a scheduling turn or clear the filters.</span></div>
        ) : events.map((item, index) => {
          const context = compactFields(item.fields);
          return (
            <details className={`log-row level-${item.level.toLowerCase()}`} key={`${item.timestamp}-${item.source_file}-${index}`}>
              <summary>
                <time>{formatTime(item.timestamp)}</time>
                <span className="log-level">{item.level}</span>
                <span className="log-source"><b>{item.process}</b>/{item.component}</span>
                <span className="log-event"><b>{item.event}</b><small>{item.message}</small></span>
                <span className="log-context">{context.length ? context.map((value) => <code key={value}>{value}</code>) : "—"}</span>
              </summary>
              <div className="log-detail">
                <pre>{JSON.stringify(item.fields, null, 2)}</pre>
                {item.exception && <pre className="log-exception">{item.exception}</pre>}
              </div>
            </details>
          );
        })}
      </section>
    </div>
  );
}
