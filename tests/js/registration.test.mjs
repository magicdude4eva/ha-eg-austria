// Strategy registration in the browser (#52, #61), with stand-ins for
// `customElements` and `window`. Own file: node runs each test file in its
// own process, so the module is loaded fresh with these globals.

import assert from "node:assert/strict";
import { test } from "node:test";

const defined = new Map();
globalThis.HTMLElement = class {};
globalThis.customElements = {
  get: (tag) => defined.get(tag),
  define: (tag, cls) => defined.set(tag, cls),
};
globalThis.window = globalThis;

await import("../../custom_components/eg_austria/frontend/eg-austria-dashboard.js");

test("registers the element and the Add dashboard entry", () => {
  const cls = defined.get("ll-strategy-dashboard-eg-austria");
  assert.equal(typeof cls.generate, "function");
  assert.deepEqual(
    window.customStrategies.map((s) => [s.type, s.strategyType]),
    [["eg-austria", "dashboard"]]
  );
});

test("getCreateSuggestions pre-fills title and icon", async () => {
  const cls = defined.get("ll-strategy-dashboard-eg-austria");
  assert.deepEqual(await cls.getCreateSuggestions({ locale: { language: "de" } }), {
    title: "Energiegemeinschaft",
    icon: "mdi:home-group",
  });
});
