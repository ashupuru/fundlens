import { useEffect, useState } from "react";
import { getDataQualitySummary } from "../api/client";
import type { components } from "../api/schema";

type Summary = components["schemas"]["DataQualitySummaryResponse"];

type LoadState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; summary: Summary };

export default function DataQuality() {
  const [state, setState] = useState<LoadState>({ status: "loading" });

  useEffect(() => {
    getDataQualitySummary()
      .then((summary) => setState({ status: "ready", summary }))
      .catch((err: unknown) =>
        setState({
          status: "error",
          message: err instanceof Error ? err.message : "Unknown error",
        }),
      );
  }, []);

  if (state.status === "loading") {
    return (
      <main>
        <h1>Data Quality</h1>
        <p>Loading…</p>
      </main>
    );
  }

  if (state.status === "error") {
    return (
      <main>
        <h1>Data Quality</h1>
        <p>Failed to load: {state.message}</p>
      </main>
    );
  }

  const { summary } = state;

  return (
    <main style={{ textAlign: "left", padding: "0 24px" }}>
      <h1>Data Quality</h1>

      <h2>Folios found ({summary.folios.length})</h2>
      {summary.folios.length === 0 ? (
        <p>No folios ingested yet.</p>
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

      <h2>Coverage gaps ({summary.coverage_gaps.length})</h2>
      {summary.coverage_gaps.length === 0 ? (
        <p>No coverage gaps detected — every ingested scheme's history looks complete.</p>
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

      <h2>Unmatched schemes ({summary.unmatched_schemes.length})</h2>
      {summary.unmatched_schemes.length === 0 ? (
        <p>Every currently-held scheme has a resolved ISIN and AMFI code.</p>
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

      <h2>Failed XIRR computations ({summary.failed_xirr_positions.length})</h2>
      {summary.failed_xirr_positions.length === 0 ? (
        <p>XIRR computed successfully for every position.</p>
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

      <h2>Market data freshness</h2>
      {summary.data_freshness.length === 0 ? (
        <p>No market-data refresh has been run yet.</p>
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
    </main>
  );
}
