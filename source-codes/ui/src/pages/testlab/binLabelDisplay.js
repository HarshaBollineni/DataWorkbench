const numericToken = "[+-]?(?:\\d+(?:\\.\\d*)?|\\.\\d+)(?:e[+-]?\\d+)?";
const infinityToken = "(?:[+\u2212-]?(?:\u221e|Infinity))";
const intervalPattern = new RegExp(`^(\\s*[[(]\\s*)(${numericToken}|${infinityToken})(\\s*,\\s*)(${numericToken}|${infinityToken})(\\s*[\\])]\\s*)$`, "i");
const finiteNumberPattern = new RegExp(`^${numericToken}$`, "i");

function fixed(value, precision) {
  const numeric = Number(value);
  const direction = numeric < 0 ? -1 : 1;
  const factor = 10 ** precision;
  return (Math.round((numeric + direction * Number.EPSILON) * factor) / factor).toFixed(precision);
}

function parsedInterval(label) {
  if (typeof label !== "string") return null;
  const match = label.match(intervalPattern);
  if (!match) return null;
  return { match, finite: [match[2], match[4]].filter((value) => finiteNumberPattern.test(value)) };
}

function requiredPrecision(labels) {
  const values = [...new Set(labels.flatMap((label) => parsedInterval(label)?.finite || []).map(Number))];
  for (let precision = 2; precision <= 4; precision += 1) {
    if (new Set(values.map((value) => fixed(value, precision))).size === values.length) return precision;
  }
  return 4;
}

export function formatBinLabels(labels) {
  const precision = requiredPrecision(labels);
  return labels.map((label) => {
    const parsed = parsedInterval(label);
    if (!parsed) return label;
    const { match } = parsed;
    const lower = finiteNumberPattern.test(match[2]) ? fixed(match[2], precision) : match[2];
    const upper = finiteNumberPattern.test(match[4]) ? fixed(match[4], precision) : match[4];
    return `${match[1]}${lower}${match[3]}${upper}${match[5]}`;
  });
}

export function formatBinRows(rows, labelKey = "label") {
  const labels = formatBinLabels(rows.map((row) => row[labelKey]));
  return rows.map((row, index) => ({ ...row, [labelKey]: labels[index] }));
}

export function sortPsiBins(rows = []) {
  if (!rows.some((row) => /^bin_\d+$/.test(String(row.bin)))) return rows;
  return rows.map((row, index) => ({ row, index })).sort((left, right) => {
    const key = ({ row, index }) => {
      if (row.bin === "missing") return [0, 0];
      if (row.bin === "unseen") return [0, 1];
      const numbered = String(row.bin).match(/^bin_(\d+)$/);
      if (numbered) return [1, Number(numbered[1])];
      return [2, index];
    };
    const a = key(left), b = key(right);
    return a[0] - b[0] || a[1] - b[1];
  }).map(({ row }) => row);
}
