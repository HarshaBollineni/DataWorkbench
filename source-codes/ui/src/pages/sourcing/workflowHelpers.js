export const highConfidenceHeaderMapping = (inspection) => Object.fromEntries(
  (inspection?.fields || [])
    .filter((field) => field.confidence === "high" && field.source_column)
    .map((field) => [field.name, field.source_column]),
);

const normalizeSearchText = (value) => String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, "");

export function inferTaxonomyValue(options, corpus, aliases = []) {
  const normalized = normalizeSearchText(corpus);
  return options.find((option) => {
    const candidates = [option.label, option.key, ...(aliases.filter(([key]) => key === option.key || key === option.label).flatMap(([, values]) => values))];
    return candidates.some((candidate) => candidate && normalized.includes(normalizeSearchText(candidate)));
  })?.label || "";
}

export function periodDate(value, end = false, quarterLike = false) {
  const text = String(value || "").trim();
  const quarter = text.match(/(\d{4})\D*Q([1-4])/i);
  if (quarter) {
    const year = Number(quarter[1]);
    const quarterNumber = Number(quarter[2]);
    const date = end
      ? new Date(Date.UTC(year, quarterNumber * 3, 0))
      : new Date(Date.UTC(year, (quarterNumber - 1) * 3, 1));
    return date.toISOString().slice(0, 10);
  }
  if (/^\d{4}$/.test(text)) return `${text}-${end ? "12-31" : "01-01"}`;
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) return "";
  if (quarterLike && end) date.setUTCMonth(Math.floor(date.getUTCMonth() / 3) * 3 + 3, 0);
  return date.toISOString().slice(0, 10);
}
