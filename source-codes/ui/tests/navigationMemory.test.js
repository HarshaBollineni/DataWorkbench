import assert from "node:assert/strict";
import test from "node:test";

import {
  clearNavigationMemory, navigationSection, rememberedSectionLocation, rememberSectionLocation,
} from "../src/lib/navigationMemory.js";

function storageFixture() {
  const values = new Map();
  globalThis.sessionStorage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
  };
}

test("nested routes belong to their sidebar workspace", () => {
  assert.equal(navigationSection("/test-lab/artifacts"), "/test-lab");
  assert.equal(navigationSection("/issues/issue-123"), "/issues");
  assert.equal(navigationSection("/login"), null);
});

test("navigation locations are remembered independently per user and section", () => {
  storageFixture();
  const user = { tenant_id: "tenant-a", username: "reviewer" };
  rememberSectionLocation(user, {
    pathname: "/test-lab", search: "?item=snapshot-1&view=results", hash: "",
  });
  rememberSectionLocation(user, {
    pathname: "/knowledge-base", search: "?tab=rules", hash: "",
  });

  assert.equal(rememberedSectionLocation(user, "/test-lab"), "/test-lab?item=snapshot-1&view=results");
  assert.equal(rememberedSectionLocation(user, "/knowledge-base"), "/knowledge-base?tab=rules");
  assert.equal(rememberedSectionLocation({ ...user, username: "another" }, "/test-lab"), "/test-lab");

  clearNavigationMemory(user);
  assert.equal(rememberedSectionLocation(user, "/test-lab"), "/test-lab");
});
