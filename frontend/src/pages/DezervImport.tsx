import { useState } from "react";
import {
  postDezervCommit,
  postDezervPreview,
  postRebuildLots,
  postRefreshNav,
  type DezervCommitResult,
  type DezervPreview,
  type NavRefreshResponse,
} from "../api/client";
import type { components } from "../api/schema";

type RebuildLotsResult = components["schemas"]["RebuildResponse"];

type Mapping = Record<string, string>; // security_name -> amfi_code, "" means skip

type Stage =
  | { step: "pick" }
  | { step: "previewing" }
  | { step: "preview"; file: File; preview: DezervPreview; mapping: Mapping }
  | { step: "committing"; file: File; preview: DezervPreview; mapping: Mapping }
  | {
      step: "done";
      commit: DezervCommitResult;
      rebuild: RebuildLotsResult | null;
      refresh: NavRefreshResponse | null;
    }
  | { step: "error"; message: string };

function initialMapping(preview: DezervPreview): Mapping {
  const mapping: Mapping = {};
  for (const scheme of preview.schemes) {
    mapping[scheme.security_name] = scheme.matched_amfi_code ?? "";
  }
  return mapping;
}

export default function DezervImport() {
  const [stage, setStage] = useState<Stage>({ step: "pick" });

  async function handleFileSelected(file: File) {
    setStage({ step: "previewing" });
    try {
      const preview = await postDezervPreview(file);
      setStage({ step: "preview", file, preview, mapping: initialMapping(preview) });
    } catch (err: unknown) {
      setStage({ step: "error", message: err instanceof Error ? err.message : "Unknown error" });
    }
  }

  async function handleConfirm(file: File, preview: DezervPreview, mapping: Mapping) {
    setStage({ step: "committing", file, preview, mapping });
    try {
      const asNullable: Record<string, string | null> = {};
      for (const [security, code] of Object.entries(mapping)) {
        asNullable[security] = code === "" ? null : code;
      }
      const commit = await postDezervCommit(file, asNullable);
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
    <div>
      <h1>Import Dezerv CSV</h1>
      <p className="muted" style={{ marginBottom: 20 }}>
        Upload a Dezerv "BANK BOOK" export. Scheme names are auto-matched against the current
        AMFI scheme list — review and correct the proposed mapping below before anything is
        imported.
      </p>

      <div className="card">
        <h2>1. Choose file</h2>
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
    </div>
  );
}

function MappingReview({
  stage,
  onConfirm,
}: {
  stage:
    | { step: "preview"; file: File; preview: DezervPreview; mapping: Mapping }
    | { step: "committing"; file: File; preview: DezervPreview; mapping: Mapping };
  onConfirm: (file: File, preview: DezervPreview, mapping: Mapping) => void;
}) {
  const [mapping, setMapping] = useState<Mapping>(stage.mapping);
  const committing = stage.step === "committing";
  const skippedCount = Object.values(mapping).filter((v) => v === "").length;

  return (
    <div className="card">
      <h2>
        2. Review proposed mapping ({stage.preview.total_mfdemat_rows} transactions across{" "}
        {stage.preview.schemes.length} schemes)
      </h2>

      {stage.preview.unrecognized_transaction_descriptions.length > 0 && (
        <p className="muted" style={{ marginBottom: 12 }}>
          Skipped transaction types not recognized by the importer (never guessed):{" "}
          {stage.preview.unrecognized_transaction_descriptions.join(", ")}
        </p>
      )}

      <table>
        <thead>
          <tr>
            <th>Dezerv security</th>
            <th>Txns</th>
            <th>Proposed match</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {stage.preview.schemes.map((scheme) => {
            const selected = mapping[scheme.security_name] ?? "";
            const matched = scheme.matched_amfi_code !== null;
            return (
              <tr key={scheme.security_name}>
                <td>{scheme.security_name}</td>
                <td>{scheme.txn_count}</td>
                <td>
                  <select
                    disabled={committing}
                    value={selected}
                    onChange={(e) =>
                      setMapping((prev) => ({
                        ...prev,
                        [scheme.security_name]: e.target.value,
                      }))
                    }
                  >
                    <option value="">Skip (do not import)</option>
                    {scheme.suggestions.map((s) => (
                      <option key={s.amfi_code} value={s.amfi_code}>
                        {s.scheme_name} ({(s.score * 100).toFixed(0)}%)
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  {matched ? (
                    <span className="badge badge-hold">auto-matched</span>
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
  commit: DezervCommitResult;
  rebuild: RebuildLotsResult | null;
  refresh: NavRefreshResponse | null;
}) {
  const skippedReasons = Object.entries(commit.skipped_reasons);
  const crossSourceDuplicates = Object.entries(commit.cross_source_duplicates);
  return (
    <div className="card">
      <h2>Import complete</h2>
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
                <th>Security</th>
                <th>Rows matched to existing</th>
              </tr>
            </thead>
            <tbody>
              {crossSourceDuplicates.map(([security, count]) => (
                <tr key={security}>
                  <td>{security}</td>
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
              <th>Security</th>
              <th>Rows skipped</th>
            </tr>
          </thead>
          <tbody>
            {skippedReasons.map(([security, count]) => (
              <tr key={security}>
                <td>{security}</td>
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
