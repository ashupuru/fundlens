/** Click-to-sort column header for the funds table. */
import type { SortDirection, SortKey } from "./fundsShared";

export function SortableHeader({
  label,
  sortKey,
  activeSort,
  onSort,
}: {
  label: string;
  sortKey: SortKey;
  activeSort: { key: SortKey; direction: SortDirection };
  onSort: (key: SortKey) => void;
}) {
  const isActive = activeSort.key === sortKey;
  return (
    <th
      onClick={() => onSort(sortKey)}
      style={{ cursor: "pointer", userSelect: "none" }}
      title="Click to sort"
    >
      {label} {isActive ? (activeSort.direction === "asc" ? "▲" : "▼") : ""}
    </th>
  );
}
