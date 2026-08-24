import assert from "node:assert/strict";
import test from "node:test";

import { buildRaincloudModel, scaleRaincloudValue } from "../src/lib/raincloud.js";

test("raincloud dots share one unit and never exceed the configured maximum", () => {
  const model = buildRaincloudModel([
    { start: 0, end: 10, count: 7575 },
    { start: 10, end: 20, count: 3790 },
    { start: 20, end: 30, count: 0 },
  ]);

  assert.equal(model.dotUnit, 758);
  assert.equal(model.bins[0].dots.length, 10);
  assert.equal(model.bins[1].dots.length, 5);
  assert.equal(model.bins[2].dots.length, 0);
  assert.ok(model.bins[0].dots.at(-1) < 1);
});

test("raincloud shares use exact retained bin counts", () => {
  const model = buildRaincloudModel([
    { start: 0, end: 1, count: 25 },
    { start: 1, end: 2, count: 75 },
  ]);

  assert.equal(model.total, 100);
  assert.equal(model.bins[0].share, 0.25);
  assert.equal(model.bins[1].share, 0.75);
});

test("raincloud values scale onto the retained histogram domain", () => {
  assert.equal(scaleRaincloudValue(15, 10, 20), 50);
  assert.equal(scaleRaincloudValue(5, 10, 20), 0);
  assert.equal(scaleRaincloudValue(25, 10, 20), 100);
  assert.equal(scaleRaincloudValue(10, 10, 10), null);
  assert.equal(scaleRaincloudValue(null, 10, 20), null);
});
