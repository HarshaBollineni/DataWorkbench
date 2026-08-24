const NUMERIC_TYPES = new Set(["numerical"]);
const NULL_EXPLANATION = /^(?:blank|blanks|null|nulls|nan|n\/a|missing)(?:\s*\/\s*(?:blank|blanks|null|nulls|nan|n\/a|missing))*$/i;

export function specialValuesFor(row) {
  const value = row?.missing_value_codes_json || row?.missing_value_codes || [];
  if (Array.isArray(value)) return value.map((item) => String(item).trim()).filter(Boolean);
  if (typeof value === "string") {
    try {
      const parsed = JSON.parse(value);
      return (Array.isArray(parsed) ? parsed : [parsed]).map((item) => String(item).trim()).filter(Boolean);
    } catch {
      return value.trim() ? [value.trim()] : [];
    }
  }
  return value == null ? [] : [String(value).trim()].filter(Boolean);
}

const numericLiteral = (value) => value !== "" && Number.isFinite(Number(value));

export function parseSpecialValueInput(value) {
  return String(value || "").split(/[,;|]/).map((item) => item.trim()).filter(Boolean);
}

export function specialValueIssue(row) {
  const values = specialValuesFor(row);
  if (!values.length) return null;

  const numeric = NUMERIC_TYPES.has(String(row?.classification || "").toLowerCase());
  const annotated = values.filter((value) => /\s+=\s+/.test(value));
  if (annotated.length) {
    const suggestions = annotated
      .map((value) => value.split(/\s+=\s+/, 1)[0].trim())
      .filter((value) => value && !NULL_EXPLANATION.test(value))
      .filter((value) => !numeric || numericLiteral(value))
      .map((value) => numeric ? String(Number(value)) : value);
    const retained = values
      .filter((value) => !annotated.includes(value))
      .filter((value) => !numeric || numericLiteral(value))
      .map((value) => numeric ? String(Number(value)) : value);
    const suggestedValues = [...new Set([...retained, ...suggestions])];
    return {
      code: "descriptive_special_value",
      message: numeric
        ? `Enter literal numeric source values only${suggestedValues.length ? `, such as ${suggestedValues.join(", ")}` : ""}. Move explanations to Description. Blank and NaN values are already counted as null.`
        : `Enter literal source values only${suggestedValues.length ? `, such as ${suggestedValues.join(", ")}` : ""}. Move explanations to Description. Blank and null values are already counted as null.`,
      suggestedValues,
    };
  }

  if (numeric) {
    const invalid = values.filter((value) => !numericLiteral(value));
    if (invalid.length) return {
      code: "non_numeric_special_value",
      message: `This is a Numeric column. Enter numeric source values only, such as -999. Blank and NaN values are already counted as null. Invalid: ${invalid.join(", ")}.`,
      suggestedValues: [],
    };
  }
  return null;
}

export function specialValueIssues(rows) {
  return (rows || []).map((row, index) => ({ row, index, issue: specialValueIssue(row) }))
    .filter(({ issue }) => Boolean(issue));
}

export function specialValuePlaceholder(row) {
  return String(row?.classification || "").toLowerCase() === "numerical"
    ? "e.g. -999, -998"
    : "e.g. UNKNOWN, NOT_REPORTED";
}
