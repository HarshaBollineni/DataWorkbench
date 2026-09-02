import assert from "node:assert/strict";
import test from "node:test";

import { formatBinLabels, sortPsiBins } from "../../../../../src/features/test-lab/shared/binning/binLabelDisplay.js";

test("PSI numeric interval labels use two decimals without changing categorical labels", () => {
  assert.deepEqual(formatBinLabels([
    "(−∞, 5.515]", "(5.515, 5.9350000000000005]", "(5.9350000000000005, +∞]", "A: retail",
  ]), ["(−∞, 5.52]", "(5.52, 5.94]", "(5.94, +∞]", "A: retail"]);
});

test("PSI interval labels add precision only when boundaries would otherwise look identical", () => {
  assert.deepEqual(formatBinLabels([
    "(−∞, 1.2344]", "(1.2344, 1.2346]", "(1.2346, +∞]",
  ]), ["(−∞, 1.234]", "(1.234, 1.235]", "(1.235, +∞]"]);

  assert.deepEqual(formatBinLabels([
    "(−∞, 1.23444]", "(1.23444, 1.23446]", "(1.23446, +∞]",
  ]), ["(−∞, 1.2344]", "(1.2344, 1.2345]", "(1.2345, +∞]"]);
});

test("PSI numbered bins sort by interval number rather than lexical ID", () => {
  const rows = ["missing", "bin_1", "bin_10", "bin_2", "special:sentinel"]
    .map((bin) => ({ bin }));
  assert.deepEqual(sortPsiBins(rows).map((row) => row.bin), [
    "missing", "bin_1", "bin_2", "bin_10", "special:sentinel",
  ]);
});
