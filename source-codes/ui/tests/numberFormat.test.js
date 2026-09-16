import assert from "node:assert/strict";
import test from "node:test";

import { formatDisplayNumber } from "../src/lib/numberFormat.js";

test("formatDisplayNumber keeps integers readable and limits ordinary decimals to three places", () => {
  assert.equal(formatDisplayNumber(15753), "15,753");
  assert.equal(formatDisplayNumber(2.4949889065185773), "2.495");
  assert.equal(formatDisplayNumber(0.8218), "0.822");
  assert.equal(formatDisplayNumber(4.4367), "4.437");
  assert.equal(formatDisplayNumber(2.5), "2.5");
});

test("formatDisplayNumber retains meaningful tiny values and handles unavailable input", () => {
  assert.equal(formatDisplayNumber(0), "0");
  assert.equal(formatDisplayNumber(0.0004), "<0.001");
  assert.equal(formatDisplayNumber(-0.0004), ">-0.001");
  assert.equal(formatDisplayNumber(null), "—");
  assert.equal(formatDisplayNumber(Number.NaN), "—");
});

test("formatDisplayNumber supports a stricter two-decimal presentation", () => {
  assert.equal(formatDisplayNumber(1.236, { maximumFractionDigits: 2 }), "1.24");
  assert.equal(formatDisplayNumber(0.004, { maximumFractionDigits: 2 }), "<0.01");
});
