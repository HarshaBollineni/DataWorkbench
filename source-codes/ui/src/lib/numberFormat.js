const DEFAULT_MAXIMUM_FRACTION_DIGITS = 3;

export function formatDisplayNumber(value, options = {}) {
  const {
    fallback = "—",
    maximumFractionDigits = DEFAULT_MAXIMUM_FRACTION_DIGITS,
    minimumFractionDigits = 0,
    preserveSmallValues = true,
  } = options;
  if (value == null || value === "" || typeof value === "boolean") return fallback;

  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return fallback;

  const digits = Math.min(DEFAULT_MAXIMUM_FRACTION_DIGITS, Math.max(0, maximumFractionDigits));
  const minimumDigits = Math.min(digits, Math.max(0, minimumFractionDigits));
  const threshold = 10 ** -digits;
  if (preserveSmallValues && numeric !== 0 && Math.abs(numeric) < threshold) {
    const boundary = threshold.toLocaleString(undefined, {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
    return numeric > 0 ? `<${boundary}` : `>-${boundary}`;
  }

  return numeric.toLocaleString(undefined, {
    minimumFractionDigits: minimumDigits,
    maximumFractionDigits: digits,
  });
}
