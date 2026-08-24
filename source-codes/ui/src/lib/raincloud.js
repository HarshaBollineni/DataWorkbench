const finite = (input) => {
  if (input == null || input === "") return null;
  const number = Number(input);
  return Number.isFinite(number) ? number : null;
};

export function buildRaincloudModel(rows = [], maximumDots = 10) {
  const bins = rows.map((row) => ({
    ...row,
    start: finite(row.start),
    end: finite(row.end),
    count: Math.max(0, finite(row.count) || 0),
  }));
  const total = bins.reduce((sum, row) => sum + row.count, 0);
  const maximum = Math.max(...bins.map((row) => row.count), 0);
  const dotUnit = maximum > 0 ? Math.max(1, Math.ceil(maximum / maximumDots)) : 1;

  return {
    bins: bins.map((row) => {
      const fullDots = Math.floor(row.count / dotUnit);
      const remainder = row.count - fullDots * dotUnit;
      return {
        ...row,
        share: total ? row.count / total : 0,
        dots: [
          ...Array.from({ length: fullDots }, () => 1),
          ...(remainder > 0 ? [remainder / dotUnit] : []),
        ],
      };
    }),
    dotUnit,
    maximum,
    total,
  };
}

export function scaleRaincloudValue(input, minimum, maximum) {
  const value = finite(input);
  const start = finite(minimum);
  const end = finite(maximum);
  if (value == null || start == null || end == null || end <= start) return null;
  return Math.min(100, Math.max(0, (value - start) / (end - start) * 100));
}
