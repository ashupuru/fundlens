import { useEffect, useState } from "react";
import { getDataQualitySummary, postRefreshBenchmarks, postRefreshGroww } from "../api/client";
import type { components } from "../api/schema";

type Summary = components["schemas"]["DataQualitySummaryResponse"];

type LoadState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; summary: Summary };

type GrowwRefreshState =
  | { status: "idle" }
  | { status: "refreshing" }
  | { status: "error"; message: string }
  | { status: "done"; rowsUpserted: number };

type BenchmarkRefreshState =
  | { status: "idle" }
  | { status: "refreshing" }
  | { status: "error"; message: string }
  | { status: "done"; inserted: number; failedBenchmarks: string[] };

export default function DataQuality() {
  const [state, setState] = useState<LoadState>({ status: "loading" });
  const [growwRefresh, setGrowwRefresh] = useState<GrowwRefreshState>({ status: "idle" });
  const [benchmarkRefresh, setBenchmarkRefresh] = useState<BenchmarkRefreshState>({
    status: "idle",
  });

  function load() {
    getDataQualitySummary()
      .then((summary) => setState({ status: "ready", summary }))
      .catch((err: unknown) =>
        setState({
          status: "error",
          message: err instanceof Error ? err.message : "Unknown error",
        }),
      );
  }

  useEffect(load, []);

  async function refreshGroww() {
    setGrowwRefresh({ status: "refreshing" });
    try {
      const result = await postRefreshGroww();
      if (result.ok) {
        setGrowwRefresh({ status: "done", rowsUpserted: result.rows_upserted });
      } else {
        setGrowwRefresh({ status: "error", message: result.error ?? "Unknown error" });
      }
      load(); // refresh the freshness table below
    } catch (err) {
      setGrowwRefresh({
        status: "error",
        message: err instanceof Error ? err.message : "Unknown error",
      });
    }
  }

  async function refreshBenchmarks() {
    setBenchmarkRefresh({ status: "refreshing" });
    try {
      const results = await postRefreshBenchmarks();
      const inserted = results.reduce((sum, r) => sum + r.inserted, 0);
      const failedBenchmarks = results.filter((r) => r.error).map((r) => r.benchmark_id);
      setBenchmarkRefresh({ status: "done", inserted, failedBenchmarks });
      load(); // refresh the freshness table below
    } catch (err) {
      setBenchmarkRefresh({
        status: "error",
        message: err instanceof Error ? err.message : "Unknown error",
      });
    }
  }

  if (state.status === "loading") {
    return (
      <div>
        <h1>Data Quality</h1>
        <p className="muted">Loading…</p>
      </div>
    );
  }

  if (state.status === "error") {
    return (
      <div>
        <h1>Data Quality</h1>
        <div className="card">
          <p>Failed to load: {state.message}</p>
        </div>
      </div>
    );
  }

  const { summary } = state;

  return (
    <div>
      <h1>Data Quality</h1>

      <div className="card">
        <h2>Folios found ({summary.folios.length})</h2>
        {summary.folios.length === 0 ? (
          <p className="muted">No folios ingested yet.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Folio</th>
                <th>AMC</th>
                <th>PAN</th>
                <th>Schemes</th>
                <th>Transactions</th>
                <th>First txn</th>
                <th>Last txn</th>
              </tr>
            </thead>
            <tbody>
              {summary.folios.map((f) => (
                <tr key={f.folio}>
                  <td>{f.folio}</td>
                  <td>{f.amc}</td>
                  <td>{f.pan ?? "—"}</td>
                  <td>{f.scheme_count}</td>
                  <td>{f.txn_count}</td>
                  <td>{f.first_txn_date}</td>
                  <td>{f.last_txn_date}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="card">
        <h2>Coverage gaps ({summary.coverage_gaps.length})</h2>
        {summary.coverage_gaps.length === 0 ? (
          <p className="muted">
            No coverage gaps detected — every ingested scheme's history looks complete.
          </p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Folio</th>
                <th>Scheme</th>
                <th>Opening balance (units)</th>
                <th>Source file</th>
                <th>Message</th>
              </tr>
            </thead>
            <tbody>
              {summary.coverage_gaps.map((g, i) => (
                <tr key={i}>
                  <td>{g.folio}</td>
                  <td>{g.scheme_name}</td>
                  <td>{g.opening_balance_units}</td>
                  <td>{g.source_filename ?? "—"}</td>
                  <td>{g.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="card">
        <h2>Unmatched schemes ({summary.unmatched_schemes.length})</h2>
        {summary.unmatched_schemes.length === 0 ? (
          <p className="muted">Every currently-held scheme has a resolved ISIN and AMFI code.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Folio</th>
                <th>Scheme</th>
                <th>AMC</th>
                <th>ISIN</th>
                <th>AMFI code</th>
              </tr>
            </thead>
            <tbody>
              {summary.unmatched_schemes.map((h, i) => (
                <tr key={i}>
                  <td>{h.folio}</td>
                  <td>{h.scheme_name}</td>
                  <td>{h.amc}</td>
                  <td>{h.isin ?? "missing"}</td>
                  <td>{h.amfi_code ?? "missing"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="card">
        <h2>Cross-source duplicates ({summary.cross_source_duplicates.length})</h2>
        {summary.cross_source_duplicates.length === 0 ? (
          <p className="muted">
            No transaction appears under more than one folio with identical units.
          </p>
        ) : (
          <>
            <p className="muted" style={{ marginBottom: 12 }}>
              The same trade ingested twice — once from your CAS, once from a broker export. The
              two sources report amounts a few paise apart (a CAS is net of stamp duty, a broker
              reports the gross order), so only the identical unit count gives them away. Resolving
              keeps the CAS record and removes the broker copy, preserving broker attribution.
            </p>
            <table>
              <thead>
                <tr>
                  <th>Scheme</th>
                  <th>Date</th>
                  <th>Units</th>
                  <th>Recorded under</th>
                  <th>Resolvable</th>
                </tr>
              </thead>
              <tbody>
                {summary.cross_source_duplicates.slice(0, 50).map((d, i) => (
                  <tr key={i}>
                    <td>{d.scheme_name}</td>
                    <td>{d.txn_date}</td>
                    <td>{d.units}</td>
                    <td>
                      {d.rows
                        .map((r) => `${r.source} (${(r.amount_paise ?? 0) / 100})`)
                        .join(" vs ")}
                    </td>
                    <td className={d.resolvable ? "positive" : ""}>
                      {d.resolvable ? "yes" : "needs review"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {summary.cross_source_duplicates.length > 50 && (
              <p className="muted" style={{ marginTop: 8 }}>
                Showing the first 50 of {summary.cross_source_duplicates.length}.
              </p>
            )}
          </>
        )}
      </div>

      <div className="card">
        <h2>Failed XIRR computations ({summary.failed_xirr_positions.length})</h2>
        {summary.failed_xirr_positions.length === 0 ? (
          <p className="muted">XIRR computed successfully for every position.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Folio</th>
                <th>Scheme</th>
                <th>AMC</th>
                <th>Reason</th>
                <th>Fully exited</th>
                <th>&lt; 1yr</th>
              </tr>
            </thead>
            <tbody>
              {summary.failed_xirr_positions.map((p, i) => (
                <tr key={i}>
                  <td>{p.folio}</td>
                  <td>{p.scheme_name}</td>
                  <td>{p.amc}</td>
                  <td>{p.xirr_reason ?? "—"}</td>
                  <td>{p.is_fully_exited ? "yes" : "no"}</td>
                  <td>{p.less_than_one_year ? "yes" : "no"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="card">
        <h2>Market data freshness</h2>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
          <button onClick={() => void refreshGroww()} disabled={growwRefresh.status === "refreshing"}>
            {growwRefresh.status === "refreshing" ? "Refreshing…" : "Refresh fund metadata (Groww)"}
          </button>
          {growwRefresh.status === "refreshing" && (
            <span className="muted">Fetching the full fund universe — usually a few seconds.</span>
          )}
          {growwRefresh.status === "done" && (
            <span className="muted">Updated {growwRefresh.rowsUpserted} funds.</span>
          )}
          {growwRefresh.status === "error" && (
            <span className="muted">Failed: {growwRefresh.message}</span>
          )}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
          <button
            onClick={() => void refreshBenchmarks()}
            disabled={benchmarkRefresh.status === "refreshing"}
          >
            {benchmarkRefresh.status === "refreshing" ? "Refreshing…" : "Refresh benchmark data"}
          </button>
          {benchmarkRefresh.status === "refreshing" && (
            <span className="muted">
              Fetching TRI-proxy history for all four benchmarks — usually a few seconds.
            </span>
          )}
          {benchmarkRefresh.status === "done" && (
            <span className="muted">
              Inserted {benchmarkRefresh.inserted} row{benchmarkRefresh.inserted === 1 ? "" : "s"}.
              {benchmarkRefresh.failedBenchmarks.length > 0 &&
                ` Failed: ${benchmarkRefresh.failedBenchmarks.join(", ")}.`}
            </span>
          )}
          {benchmarkRefresh.status === "error" && (
            <span className="muted">Failed: {benchmarkRefresh.message}</span>
          )}
        </div>
        {summary.data_freshness.length === 0 ? (
          <p className="muted">No market-data refresh has been run yet.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Source</th>
                <th>Status</th>
                <th>Last success</th>
                <th>Rows updated</th>
                <th>Error</th>
              </tr>
            </thead>
            <tbody>
              {summary.data_freshness.map((fr) => (
                <tr key={fr.source}>
                  <td>{fr.source}</td>
                  <td>{fr.status}</td>
                  <td>{fr.last_success_at ?? "never"}</td>
                  <td>{fr.rows_updated ?? "—"}</td>
                  <td>{fr.error_message ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
