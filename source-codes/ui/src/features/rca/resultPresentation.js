const units = new Set(["ratio", "count", "number", "correlation"]);
const validValue = (value, unit) => typeof value === "number" && Number.isFinite(value)
  && (unit !== "ratio" || (value >= 0 && value <= 1))
  && (unit !== "correlation" || (value >= -1 && value <= 1))
  && (unit !== "count" || (value >= 0 && Number.isInteger(value)));

export function presentationMetrics(presentation) {
  if (presentation?.version !== 1 || !Array.isArray(presentation.metrics)) return [];
  return presentation.metrics.filter((metric) => metric && typeof metric.label === "string"
    && units.has(metric.unit) && validValue(metric.value, metric.unit)
    && (!metric.comparison || validValue(metric.comparison.value, metric.unit))).slice(0, 4);
}

export function metricValue(value, unit) {
  return unit === "ratio" ? `${(value * 100).toFixed(2)}%`
    : new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 }).format(value);
}

export function metricChange(metric) {
  const delta = metric.value - metric.comparison.value;
  return `${delta > 0 ? "+" : ""}${metric.unit === "ratio"
    ? `${(delta * 100).toFixed(2)} percentage points`
    : metricValue(delta, "number")}`;
}
