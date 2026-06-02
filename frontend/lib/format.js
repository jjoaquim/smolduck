// Map a DuckDB logical type string to a coarse kind, icon, and cell alignment.

const NUMERIC = /\b(TINYINT|SMALLINT|INTEGER|BIGINT|HUGEINT|UTINYINT|USMALLINT|UINTEGER|UBIGINT|DECIMAL|NUMERIC|REAL|FLOAT|DOUBLE)\b/;
const TEMPORAL = /\b(DATE|TIME|TIMESTAMP|INTERVAL)\b/;
const BOOLEAN = /\bBOOLEAN\b/;
const TEXT = /\b(VARCHAR|CHAR|TEXT|STRING|UUID|BLOB|BIT)\b/;

export function typeKind(type) {
  const t = (type || "").toUpperCase();
  if (NUMERIC.test(t)) return "numeric";
  if (TEMPORAL.test(t)) return "temporal";
  if (BOOLEAN.test(t)) return "boolean";
  if (TEXT.test(t)) return "text";
  return "other";
}

const ICONS = { numeric: "#", temporal: "◷", boolean: "◉", text: "T", other: "•" };

export function typeIcon(type) {
  return ICONS[typeKind(type)];
}

export function isNumericKind(type) {
  return typeKind(type) === "numeric";
}

export function fmtCell(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
