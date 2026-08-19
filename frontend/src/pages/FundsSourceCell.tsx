/** Inline editor for a holding's source attribution.
 *
 * The derived source is a heuristic over which import supplied a position's
 * transactions, and it genuinely cannot tell "held under an AMC folio" from
 * "bought through a platform that also reports to the RTAs" -- so the user
 * needs a way to correct it.
 */
import { useState } from "react";
import { setHoldingSource } from "../api/client";
import type { FundRow } from "./fundsShared";

const DATALIST_ID = "known-holding-sources";

export function SourceDatalist({ sources }: { sources: string[] }) {
  return (
    <datalist id={DATALIST_ID}>
      {sources.map((s) => (
        <option key={s} value={s} />
      ))}
    </datalist>
  );
}

export function SourceCell({
  row,
  schemeKey,
  onSaved,
}: {
  row: FundRow;
  schemeKey: string | undefined;
  onSaved: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(row.metrics?.source ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Without a scheme_key there's nothing to key an override on -- show the
  // value read-only rather than offering an edit that can't be saved.
  if (schemeKey === undefined) {
    return <span className="muted">{row.metrics?.source ?? "—"}</span>;
  }

  async function save(next: string | null) {
    setSaving(true);
    setError(null);
    try {
      await setHoldingSource(row.folio, schemeKey!, next);
      setEditing(false);
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setSaving(false);
    }
  }

  if (!editing) {
    return (
      <span>
        {row.metrics?.source ?? "—"}
        {row.metrics?.source_is_override && (
          <span className="muted" title="Manually set — click edit to change or reset">
            {" "}
            (edited)
          </span>
        )}{" "}
        <button
          style={{ padding: "2px 8px", fontSize: 12 }}
          onClick={() => {
            setValue(row.metrics?.source ?? "");
            setEditing(true);
          }}
        >
          Edit
        </button>
      </span>
    );
  }

  return (
    <span style={{ display: "inline-flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
      <input
        list={DATALIST_ID}
        value={value}
        autoFocus
        disabled={saving}
        style={{ width: 150 }}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") void save(value);
          if (e.key === "Escape") setEditing(false);
        }}
      />
      <button style={{ padding: "2px 8px", fontSize: 12 }} disabled={saving} onClick={() => void save(value)}>
        {saving ? "…" : "Save"}
      </button>
      {row.metrics?.source_is_override && (
        <button
          style={{ padding: "2px 8px", fontSize: 12 }}
          disabled={saving}
          title="Go back to the source derived from your imports"
          onClick={() => void save(null)}
        >
          Reset
        </button>
      )}
      <button
        style={{ padding: "2px 8px", fontSize: 12 }}
        disabled={saving}
        onClick={() => setEditing(false)}
      >
        Cancel
      </button>
      {error && <span style={{ color: "var(--negative)", fontSize: 12 }}>{error}</span>}
    </span>
  );
}
