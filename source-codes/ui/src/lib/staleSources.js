export function staleSourceText(source) {
  const label = source?.label || "the selected snapshot";
  const date = source?.superseded_at
    ? new Date(source.superseded_at).toLocaleDateString()
    : "an earlier date";
  return `From snapshot ${label} (superseded on ${date})`;
}
