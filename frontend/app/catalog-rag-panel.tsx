"use client";

import { useEffect, useState } from "react";

type CatalogSummary = {
  catalog_version: string;
  locations: number;
  providers: number;
  appointment_types: number;
  retrieval: string;
};

type RetrievalResult = {
  id: string;
  name: string;
  retrieval_score: number;
  specialty?: string;
  address?: string;
  city?: string;
};

type RetrievalResponse = {
  candidate_count: number;
  index_size: number;
  latency_ms: number;
  results: RetrievalResult[];
};

export function CatalogRagPanel({ apiUrl }: { apiUrl: string }) {
  const base = apiUrl.replace(/\/$/, "");
  const [summary, setSummary] = useState<CatalogSummary | null>(null);
  const [query, setQuery] = useState("knee MRI");
  const [entityType, setEntityType] = useState("appointment_type");
  const [search, setSearch] = useState<RetrievalResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("Choose a catalog JSON file to activate it.");

  useEffect(() => {
    void fetch(`${base}/api/catalog`)
      .then((response) => response.json())
      .then(setSummary)
      .catch(() => setMessage("The catalog API is unavailable."));
  }, [base]);

  async function upload(file: File) {
    setBusy(true);
    setMessage(`Reading ${file.name}…`);
    try {
      const catalog = JSON.parse(await file.text());
      const response = await fetch(`${base}/api/catalog/upload`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ catalog }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? "The catalog was rejected.");
      setSummary(payload);
      setMessage("Catalog validated, indexed, and activated for new scheduling work.");
      setSearch(null);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "The catalog could not be uploaded.");
    } finally {
      setBusy(false);
    }
  }

  async function retrieve() {
    setBusy(true);
    try {
      const response = await fetch(`${base}/api/catalog/search`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entity_type: entityType, query, limit: 8 }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? "Retrieval failed.");
      setSearch(payload);
      setMessage("Retrieved candidates. Deterministic resolution and policy checks still decide the result.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Retrieval failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="page catalog-rag-page" aria-labelledby="catalog-rag-title">
      <header className="catalog-rag-header">
        <div>
          <h2 id="catalog-rag-title">Catalog retrieval</h2>
          <p>Upload a large catalog, build a local index, and inspect what retrieval finds.</p>
        </div>
        <label className="catalog-upload-button">
          <input type="file" accept="application/json,.json" disabled={busy} onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void upload(file);
          }} />
          {busy ? "Working…" : "Upload catalog JSON"}
        </label>
      </header>

      {summary && (
        <div className="catalog-rag-metrics">
          <div><span>Locations</span><strong>{summary.locations}</strong></div>
          <div><span>Providers</span><strong>{summary.providers}</strong></div>
          <div><span>Appointment types</span><strong>{summary.appointment_types}</strong></div>
          <div><span>Index</span><strong>{summary.retrieval}</strong></div>
        </div>
      )}

      <div className="catalog-rag-search">
        <select aria-label="Catalog entity type" value={entityType} onChange={(event) => setEntityType(event.target.value)}>
          <option value="appointment_type">Appointment type</option>
          <option value="provider">Provider</option>
          <option value="location">Location</option>
        </select>
        <input aria-label="Catalog search" value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void retrieve(); }} />
        <button type="button" onClick={() => void retrieve()} disabled={busy || !query.trim()}>Retrieve</button>
      </div>

      <p className="catalog-rag-message">{message}</p>

      {search && (
        <section className="catalog-rag-results" aria-label="Retrieved catalog candidates">
          <header><strong>{search.candidate_count} candidates</strong><span>{search.latency_ms} ms · indexed {search.index_size} records</span></header>
          {search.results.map((result) => (
            <div className="catalog-rag-result" key={result.id}>
              <div><strong>{result.name}</strong><small>{result.id}{result.specialty ? ` · ${result.specialty}` : ""}{result.city ? ` · ${result.city}` : ""}</small></div>
              <b>{result.retrieval_score}</b>
            </div>
          ))}
          {search.results.length === 0 && <p>No matching records were retrieved.</p>}
        </section>
      )}
    </section>
  );
}
