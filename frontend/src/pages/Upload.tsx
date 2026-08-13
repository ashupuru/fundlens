import { useState } from "react";
import { postImportTri, postIngestCas, postIngestCsv, postRebuildLots } from "../api/client";
import type { components } from "../api/schema";

type IngestResult = components["schemas"]["IngestResponse"];
type RebuildResult = components["schemas"]["RebuildResponse"];
type TRIImportResult = components["schemas"]["TRIImportResponse"];

const BENCHMARK_IDS = [
  "NIFTY50_TRI",
  "BSE100_TRI",
  "NIFTY_MIDCAP150_TRI",
  "NIFTY_SMALLCAP250_TRI",
] as const;

type TRIStatus =
  | { state: "idle" }
  | { state: "working" }
  | { state: "error"; message: string }
  | { state: "done"; result: TRIImportResult };

type Status =
  | { state: "idle" }
  | { state: "working" }
  | { state: "error"; message: string }
  | { state: "done"; ingest: IngestResult; rebuild: RebuildResult | null };

function IngestSummary({ ingest, rebuild }: { ingest: IngestResult; rebuild: RebuildResult | null }) {
  return (
    <div style={{ marginTop: 12 }}>
      <p>
        Imported <strong>{ingest.inserted_count}</strong> new transaction
        {ingest.inserted_count === 1 ? "" : "s"}
        {ingest.duplicate_count > 0 && (
          <span className="muted"> ({ingest.duplicate_count} already had it, skipped)</span>
        )}
        .
      </p>

      {ingest.coverage_gaps.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <p className="muted">
            {ingest.coverage_gaps.length} scheme{ingest.coverage_gaps.length === 1 ? "" : "s"} had
            units already held at the start of this statement — full history for those isn't in
            this file:
          </p>
          <table>
            <thead>
              <tr>
                <th>Folio</th>
                <th>Scheme</th>
                <th>Opening balance (units)</th>
              </tr>
            </thead>
            <tbody>
              {ingest.coverage_gaps.map((g, i) => (
                <tr key={i}>
                  <td>{g.folio}</td>
                  <td>{g.scheme_name}</td>
                  <td>{g.opening_balance_units}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {rebuild && (
        <p className="muted" style={{ marginTop: 8 }}>
          Rebuilt lots: {rebuild.lots_created} lot{rebuild.lots_created === 1 ? "" : "s"} across{" "}
          {rebuild.schemes_processed} scheme{rebuild.schemes_processed === 1 ? "" : "s"}
          {rebuild.errors.length > 0 && (
            <span> — {rebuild.errors.length} scheme(s) had errors rebuilding.</span>
          )}
        </p>
      )}
    </div>
  );
}

export default function Upload() {
  const [casFile, setCasFile] = useState<File | null>(null);
  const [casPassword, setCasPassword] = useState("");
  const [casStatus, setCasStatus] = useState<Status>({ state: "idle" });

  const [csvFile, setCsvFile] = useState<File | null>(null);
  const [csvStatus, setCsvStatus] = useState<Status>({ state: "idle" });

  const [triFile, setTriFile] = useState<File | null>(null);
  const [triBenchmarkId, setTriBenchmarkId] = useState<(typeof BENCHMARK_IDS)[number]>(
    BENCHMARK_IDS[0],
  );
  const [triStatus, setTriStatus] = useState<TRIStatus>({ state: "idle" });

  async function submitCas(e: React.FormEvent) {
    e.preventDefault();
    if (!casFile || !casPassword) return;
    setCasStatus({ state: "working" });
    try {
      const ingest = await postIngestCas(casFile, casPassword);
      const rebuild = await postRebuildLots().catch(() => null);
      setCasStatus({ state: "done", ingest, rebuild });
      setCasPassword("");
      setCasFile(null);
    } catch (err) {
      setCasStatus({
        state: "error",
        message: err instanceof Error ? err.message : "Unknown error",
      });
    }
  }

  async function submitCsv(e: React.FormEvent) {
    e.preventDefault();
    if (!csvFile) return;
    setCsvStatus({ state: "working" });
    try {
      const ingest = await postIngestCsv(csvFile);
      const rebuild = await postRebuildLots().catch(() => null);
      setCsvStatus({ state: "done", ingest, rebuild });
      setCsvFile(null);
    } catch (err) {
      setCsvStatus({
        state: "error",
        message: err instanceof Error ? err.message : "Unknown error",
      });
    }
  }

  async function submitTri(e: React.FormEvent) {
    e.preventDefault();
    if (!triFile) return;
    setTriStatus({ state: "working" });
    try {
      const result = await postImportTri(triBenchmarkId, triFile);
      setTriStatus({ state: "done", result });
      setTriFile(null);
    } catch (err) {
      setTriStatus({
        state: "error",
        message: err instanceof Error ? err.message : "Unknown error",
      });
    }
  }

  return (
    <div>
      <h1>Upload</h1>

      <div className="card">
        <h2>CAS statement (PDF)</h2>
        <p className="muted" style={{ marginBottom: 12 }}>
          The password-protected Consolidated Account Statement from MFCentral or CAMS/KFintech.
          Re-uploading a newer statement is safe — transactions already on file are skipped.
        </p>
        <form onSubmit={(e) => void submitCas(e)}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <input
              type="file"
              accept="application/pdf"
              onChange={(e) => setCasFile(e.target.files?.[0] ?? null)}
              disabled={casStatus.state === "working"}
            />
            <input
              type="password"
              placeholder="PDF password"
              value={casPassword}
              onChange={(e) => setCasPassword(e.target.value)}
              disabled={casStatus.state === "working"}
              autoComplete="off"
            />
            <button type="submit" disabled={!casFile || !casPassword || casStatus.state === "working"}>
              {casStatus.state === "working" ? "Uploading…" : "Upload"}
            </button>
          </div>
        </form>
        {casStatus.state === "error" && (
          <p style={{ color: "var(--negative)", marginTop: 8 }}>{casStatus.message}</p>
        )}
        {casStatus.state === "done" && (
          <IngestSummary ingest={casStatus.ingest} rebuild={casStatus.rebuild} />
        )}
      </div>

      <div className="card">
        <h2>Transactions CSV (escape hatch)</h2>
        <p className="muted" style={{ marginBottom: 12 }}>
          Columns: folio, amc, scheme_name, txn_type, txn_date, amount, units, isin, amfi_code.
          For platforms that don't issue a CAS, or manual corrections.
        </p>
        <form onSubmit={(e) => void submitCsv(e)}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <input
              type="file"
              accept=".csv,text/csv"
              onChange={(e) => setCsvFile(e.target.files?.[0] ?? null)}
              disabled={csvStatus.state === "working"}
            />
            <button type="submit" disabled={!csvFile || csvStatus.state === "working"}>
              {csvStatus.state === "working" ? "Uploading…" : "Upload"}
            </button>
          </div>
        </form>
        {csvStatus.state === "error" && (
          <p style={{ color: "var(--negative)", marginTop: 8 }}>{csvStatus.message}</p>
        )}
        {csvStatus.state === "done" && (
          <IngestSummary ingest={csvStatus.ingest} rebuild={csvStatus.rebuild} />
        )}
      </div>

      <div className="card">
        <h2>Import benchmark TRI data (CSV)</h2>
        <p className="muted" style={{ marginBottom: 12 }}>
          FundLens's own two-column format — columns: date, value. Use this to upgrade a
          benchmark's history from a proxy (an index fund's NAV standing in for its TRI) to real
          TRI data you've downloaded and reformatted by hand. Existing proxy rows for the dates
          you import are overwritten; other dates are untouched.
        </p>
        <form onSubmit={(e) => void submitTri(e)}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <select
              value={triBenchmarkId}
              onChange={(e) => setTriBenchmarkId(e.target.value as (typeof BENCHMARK_IDS)[number])}
              disabled={triStatus.state === "working"}
            >
              {BENCHMARK_IDS.map((id) => (
                <option key={id} value={id}>
                  {id}
                </option>
              ))}
            </select>
            <input
              type="file"
              accept=".csv,text/csv"
              onChange={(e) => setTriFile(e.target.files?.[0] ?? null)}
              disabled={triStatus.state === "working"}
            />
            <button type="submit" disabled={!triFile || triStatus.state === "working"}>
              {triStatus.state === "working" ? "Uploading…" : "Upload"}
            </button>
          </div>
        </form>
        {triStatus.state === "error" && (
          <p style={{ color: "var(--negative)", marginTop: 8 }}>{triStatus.message}</p>
        )}
        {triStatus.state === "done" && (
          <p className="muted" style={{ marginTop: 8 }}>
            Wrote {triStatus.result.rows_written} row
            {triStatus.result.rows_written === 1 ? "" : "s"} for {triStatus.result.benchmark_id}.
          </p>
        )}
      </div>
    </div>
  );
}
