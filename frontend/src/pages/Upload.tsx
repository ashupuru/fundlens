import { useState } from "react";
import { postIngestCas, postIngestCsv, postRebuildLots } from "../api/client";
import type { components } from "../api/schema";

type IngestResult = components["schemas"]["IngestResponse"];
type RebuildResult = components["schemas"]["RebuildResponse"];

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
    </div>
  );
}
