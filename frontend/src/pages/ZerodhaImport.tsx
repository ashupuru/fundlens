import { useState } from "react";
import {
  postRebuildLots,
  postRefreshNav,
  postZerodhaCommit,
  postZerodhaPreview,
  type NavRefreshResponse,
  type ZerodhaCommitResult,
  type ZerodhaPreview,
} from "../api/client";
import type { components } from "../api/schema";

type RebuildLotsResult = components["schemas"]["RebuildResponse"];

type Mapping = Record<string, string>; // isin -> amfi_code, "" means skip

type Stage =
  | { step: "pick" }
  | { step: "previewing" }
  | { step: "preview"; file: File; preview: ZerodhaPreview; mapping: Mapping }
  | { step: "committing"; file: File; preview: ZerodhaPreview; mapping: Mapping }
  | {
      step: "done";
      commit: ZerodhaCommitResult;
      rebuild: RebuildLotsResult | null;
      refresh: NavRefreshResponse | null;
    }
  | { step: "error"; message: string };

function initialMapping(preview: ZerodhaPreview): Mapping {
  const mapping: Mapping = {};
  for (const scheme of preview.schemes) {
    mapping[scheme.isin] = scheme.matched_amfi_code ?? "";
  }
  return mapping;
}

export function ZerodhaImportCard() {
  const [stage, setStage] = useState<Stage>({ step: "pick" });

  async function handleFileSelected(file: File) {
    setStage({ step: "previewing" });
    try {
      const preview = await postZerodhaPreview(file);
      setStage({ step: "preview", file, preview, mapping: initialMapping(preview) });
    } catch (err: unknown) {
      setStage({ step: "error", message: err instanceof Error ? err.message : "Unknown error" });
    }
  }

  async function handleConfirm(file: File, preview: ZerodhaPreview, mapping: Mapping) {
    setStage({ step: "committing", file, preview, mapping });
    try {
      const asNullable: Record<string, string | null> = {};
      for (const [isin, code] of Object.entries(mapping)) {
        asNullable[isin] = code === "" ? null : code;
      }
      const commit = await postZerodhaCommit(file, asNullable);
      let rebuild: RebuildLotsResult | null = null;
      let refresh: NavRefreshResponse | null = null;
      if (commit.inserted_count > 0) {
        rebuild = await postRebuildLots();
        refresh = await postRefreshNav().catch(() => null);
      }
      setStage({ step: "done", commit, rebuild, refresh });
    } catch (err: unknown) {
      setStage({ step: "error", message: err instanceof Error ? err.message : "Unknown error" });
    }
  }

  return (
    <>
      <div className="card">
        <h3>Zerodha (Tradebook CSV)</h3>
        <p className="muted" style={{ marginBottom: 12 }}>
          Upload a Console Tradebook export filtered to the Mutual Fund segment (Console &gt;
          Reports &gt; Tradebook). Schemes are matched by ISIN against the current AMFI scheme
          list — review the proposed mapping below before anything is imported.
        </p>
        <input
          type="file"
          accept=".csv,text/csv"
          disabled={stage.step === "previewing" || stage.step === "committing"}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void handleFileSelected(file);
            e.target.value = "";
          }}
        />
        {stage.step === "previewing" && <p className="muted">Matching schemes against AMFI…</p>}
      </div>

      {stage.step === "error" && (
        <div className="card">
          <p>Failed: {stage.message}</p>
        </div>
      )}

      {(stage.step === "preview" || stage.step === "committing") && (
        <MappingReview stage={stage} onConfirm={handleConfirm} />
      )}

      {stage.step === "done" && (
        <ImportSummary commit={stage.commit} rebuild={stage.rebuild} refresh={stage.refresh} />
      )}
    </>
  );
}

function MappingReview({
  stage,
  onConfirm,
}: {
  stage:
    | { step: "preview"; file: File; preview: ZerodhaPreview; mapping: Mapping }
    | { step: "committing"; file: File; preview: ZerodhaPreview; mapping: Mapping };
  onConfirm: (file: File, preview: ZerodhaPreview, mapping: Mapping) => void;
}) {
  const [mapping, setMapping] = useState<Mapping>(stage.mapping);
  const committing = stage.step === "committing";
  const skippedCount = Object.values(mapping).filter((v) => v === "").length;

  return (
    <div className="card">
      <h3>
        Review proposed mapping ({stage.preview.total_mf_rows} transactions across{" "}
        {stage.preview.schemes.length} schemes)
      </h3>

      {stage.preview.rows_missing_isin > 0 && (
        <p className="muted" style={{ marginBottom: 12 }}>
          {stage.preview.rows_missing_isin} row{stage.preview.rows_missing_isin === 1 ? "" : "s"}{" "}
          had no ISIN and were dropped.
        </p>
      )}
      {stage.preview.unrecognized_trade_types.length > 0 && (
        <p className="muted" style={{ marginBottom: 12 }}>
          Skipped trade types not recognized by the importer (never guessed):{" "}
          {stage.preview.unrecognized_trade_types.join(", ")}
        </p>
      )}

      <table>
        <thead>
          <tr>
            <th>Fund (ISIN)</th>
            <th>Txns</th>
            <th>Match</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {stage.preview.schemes.map((scheme) => {
            const selected = mapping[scheme.isin] ?? "";
            const matched = scheme.matched_amfi_code !== null;
            return (
              <tr key={scheme.isin}>
                <td>
                  {scheme.symbol}
                  <span className="muted"> ({scheme.isin})</span>
                </td>
                <td>{scheme.txn_count}</td>
                <td>
                  <select
                    disabled={committing}
                    value={selected}
                    onChange={(e) =>
                      setMapping((prev) => ({
                        ...prev,
                        [scheme.isin]: e.target.value,
                      }))
                    }
                  >
                    <option value="">Skip (do not import)</option>
                    {matched && scheme.matched_amfi_code && (
                      <option value={scheme.matched_amfi_code}>{scheme.matched_scheme_name}</option>
                    )}
                  </select>
                </td>
                <td>
                  {matched ? (
                    <span className="badge badge-hold">matched</span>
                  ) : (
                    <span className="badge badge-sell">{scheme.reason ?? "needs review"}</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {skippedCount > 0 && (
        <p className="muted" style={{ marginTop: 12 }}>
          {skippedCount} scheme{skippedCount === 1 ? "" : "s"} will be skipped.
        </p>
      )}

      <button
        disabled={committing}
        style={{ marginTop: 16 }}
        onClick={() => onConfirm(stage.file, stage.preview, mapping)}
      >
        {committing ? "Importing…" : "Confirm & import"}
      </button>
    </div>
  );
}

function ImportSummary({
  commit,
  rebuild,
  refresh,
}: {
  commit: ZerodhaCommitResult;
  rebuild: RebuildLotsResult | null;
  refresh: NavRefreshResponse | null;
}) {
  const skippedReasons = Object.entries(commit.skipped_reasons);
  const crossSourceDuplicates = Object.entries(commit.cross_source_duplicates);
  return (
    <div className="card">
      <h3>Import complete</h3>
      <div
        className="card-grid"
        style={{
          marginBottom: skippedReasons.length > 0 || crossSourceDuplicates.length > 0 ? 16 : 0,
        }}
      >
        <div className="stat">
          <div className="stat-label">Inserted</div>
          <div className="stat-value positive">{commit.inserted_count}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Duplicates</div>
          <div className="stat-value">{commit.duplicate_count}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Already in portfolio</div>
          <div className="stat-value">{commit.cross_source_duplicate_count}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Skipped</div>
          <div className="stat-value">{commit.skipped_count}</div>
        </div>
      </div>

      {crossSourceDuplicates.length > 0 && (
        <div style={{ marginBottom: skippedReasons.length > 0 ? 16 : 0 }}>
          <p className="muted" style={{ marginBottom: 8 }}>
            These rows matched a transaction already in your portfolio from a different import
            (same scheme, date, and amount) and were not re-imported:
          </p>
          <table>
            <thead>
              <tr>
                <th>ISIN</th>
                <th>Rows matched to existing</th>
              </tr>
            </thead>
            <tbody>
              {crossSourceDuplicates.map(([isin, count]) => (
                <tr key={isin}>
                  <td>{isin}</td>
                  <td>{count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {skippedReasons.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>ISIN</th>
              <th>Rows skipped</th>
            </tr>
          </thead>
          <tbody>
            {skippedReasons.map(([isin, count]) => (
              <tr key={isin}>
                <td>{isin}</td>
                <td>{count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {rebuild && (
        <p className="muted" style={{ marginTop: 12 }}>
          Lots rebuilt: {rebuild.lots_created} created across {rebuild.schemes_processed} schemes.
          {rebuild.errors.length > 0 && ` ${rebuild.errors.length} scheme(s) had errors.`}
          {rebuild.gap_filled.length > 0 &&
            ` ${rebuild.gap_filled.length} scheme(s) had an assumed opening position ` +
              "(this export doesn't cover the scheme's full history; cost basis flagged " +
              "unknown for that portion)."}
        </p>
      )}
      {refresh && (
        <p className="muted" style={{ marginTop: 8 }}>
          NAV refresh: {refresh.backfilled_schemes} new scheme
          {refresh.backfilled_schemes === 1 ? "" : "s"} backfilled ({refresh.backfilled_rows} row
          {refresh.backfilled_rows === 1 ? "" : "s"}), {refresh.amfi_refresh_rows} latest-NAV row
          {refresh.amfi_refresh_rows === 1 ? "" : "s"} updated
          {!refresh.amfi_refresh_ok && ` — AMFI refresh failed: ${refresh.amfi_error}`}.
        </p>
      )}
    </div>
  );
}
