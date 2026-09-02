export function primaryTreePerformance(leaves = []) {
  const usable = leaves.filter((row) => Number.isFinite(Number(row.score))
    && Number.isFinite(Number(row.rows)) && Number.isFinite(Number(row.target_events)));
  const totalRows = usable.reduce((sum, row) => sum + Number(row.rows), 0);
  const totalEvents = usable.reduce((sum, row) => sum + Number(row.target_events), 0);
  const totalNonEvents = totalRows - totalEvents;
  if (!totalRows || !totalEvents || !totalNonEvents) return [];

  const grouped = new Map();
  usable.forEach((row) => {
    const score = Number(row.score);
    const current = grouped.get(score) || { score, rows: 0, events: 0 };
    current.rows += Number(row.rows);
    current.events += Number(row.target_events);
    grouped.set(score, current);
  });

  let cumulativeRows = 0;
  let cumulativeEvents = 0;
  const baselineRate = totalEvents / totalRows;
  const rows = [{ threshold: null, rows: 0, events: 0, cumulative_rows: 0,
    cumulative_events: 0, population_share: 0, tpr: 0, fpr: 0,
    event_rate: null, cumulative_lift: null }];
  [...grouped.values()].sort((left, right) => right.score - left.score).forEach((row) => {
    cumulativeRows += row.rows;
    cumulativeEvents += row.events;
    const cumulativeNonEvents = cumulativeRows - cumulativeEvents;
    rows.push({
      threshold: row.score,
      rows: row.rows,
      events: row.events,
      cumulative_rows: cumulativeRows,
      cumulative_events: cumulativeEvents,
      population_share: cumulativeRows / totalRows,
      tpr: cumulativeEvents / totalEvents,
      fpr: cumulativeNonEvents / totalNonEvents,
      event_rate: cumulativeRows ? cumulativeEvents / cumulativeRows : null,
      cumulative_lift: cumulativeRows ? (cumulativeEvents / cumulativeRows) / baselineRate : null,
    });
  });
  return rows;
}

export function profilePercentiles(profile = {}) {
  const values = profile.percentiles || {};
  const candidates = [
    ["P1", values.p01 ?? values.p1],
    ["P5", values.p05 ?? values.p5],
    ["P25", values.p25 ?? profile.q1],
    ["P50", values.p50 ?? profile.median],
    ["P75", values.p75 ?? profile.q3],
    ["P95", values.p95],
    ["P99", values.p99],
  ];
  return candidates.filter(([, value]) => value != null);
}

export function promotionRecommendation(metrics = {}) {
  const candidateType = metrics.candidate?.candidate_type;
  if (candidateType === "target_leakage") return "recommended";
  if (candidateType) return "review";
  return "not_recommended";
}

export function formatProfileNumber(value) {
  if (value == null || value === "") return "—";
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "—";
  return numeric.toLocaleString(undefined, {
    maximumFractionDigits: Math.abs(numeric) > 100 ? 0 : 2,
  });
}
