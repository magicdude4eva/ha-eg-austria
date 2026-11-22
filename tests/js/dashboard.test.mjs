// Dashboard strategy (issue #52): `node --test "tests/js/*.test.mjs"` (Node 22+).
// Synthetic entry data only.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  buildDashboard,
  buildView,
  generate,
} from "../../custom_components/eg_austria/frontend/eg-austria-dashboard.js";

const CORE_CARDS = new Set([
  "button",
  "energy-date-selection",
  "entities",
  "gauge",
  "heading",
  "markdown",
  "statistic",
  "statistics-graph",
  "tile",
]);

const S = (q) => `eg_austria:home_${q}`;

const entry = (overrides = {}) => ({
  entry_id: "entry1",
  title: "EG Austria Home",
  site: "home",
  communities: [
    { slug: "beg", name: "Example BEG" },
    { slug: "eeg", name: "Example EEG" },
  ],
  statistics: Object.fromEntries(
    [
      "consumption",
      "generation",
      "consumption_from_community",
      "generation_to_community",
      "consumption_from_supplier",
      "generation_to_surplus",
    ].map((q) => [q, S(q)])
  ),
  community_statistics: {
    beg: {
      consumption_from_community: S("beg_consumption_from_community"),
      generation_to_community: S("beg_generation_to_community"),
    },
    eeg: { generation_to_community: S("eeg_generation_to_community") },
  },
  sources: { feed_in: null, grid_consumption: null, pv: null },
  entities: {
    data_complete_until: "sensor.eg_austria_home_data_complete_until",
    meter_data_until: "sensor.eg_austria_home_meter_data_until",
    last_run_status: "sensor.eg_austria_home_last_run_status",
    last_import: "sensor.eg_austria_home_last_import",
    feed_in_to_communities_month_so_far: "sensor.a",
    feed_in_to_communities_last_month: "sensor.b",
    import_from_communities_month_so_far: "sensor.c",
    import_from_communities_last_month: "sensor.d",
    beg_feed_in_share_month_so_far: "sensor.e",
    beg_import_share_last_month: "sensor.f",
  },
  ...overrides,
});

const EN = { locale: { language: "en" } };
const DE = { locale: { language: "de-AT" } };

const cards = (view) => view.sections.flatMap((s) => s.cards);
const graphs = (view) => cards(view).filter((c) => c.type === "statistics-graph");
const titles = (view) => graphs(view).map((c) => c.title);

test("only core cards, sections view per entry", () => {
  const view = buildView(entry(), EN);
  assert.equal(view.type, "sections");
  assert.equal(view.path, "home");
  assert.equal(view.title, "EG Austria Home");
  for (const card of cards(view)) {
    assert.ok(CORE_CARDS.has(card.type), card.type);
    if (card.collection_key) assert.match(card.collection_key, /^energy_/);
  }
});

test("feed-in split: one series per community plus the surplus buyer", () => {
  const view = buildView(entry(), EN);
  const monthly = graphs(view).find((c) => c.title === "Where the feed-in went, by month");
  assert.equal(monthly.chart_type, "bar-stack");
  assert.equal(monthly.period, "month");
  assert.deepEqual(monthly.entities, [
    { entity: S("beg_generation_to_community"), name: "Example BEG" },
    { entity: S("eeg_generation_to_community"), name: "Example EEG" },
    { entity: S("generation_to_surplus"), name: "Surplus buyer" },
  ]);
  // The meter total is not stacked with its parts.
  assert.ok(!monthly.entities.some((e) => e.entity === S("generation")));
  const imports = graphs(view).find((c) => c.title === "Where the import came from, by month");
  // A community without the statistic is left out.
  assert.deepEqual(
    imports.entities.map((e) => e.name),
    ["Example BEG", "Supplier"]
  );
});

test("source cards only for sources that are set", () => {
  assert.ok(!titles(buildView(entry(), EN)).some((t) => t.startsWith("PV")));
  const view = buildView(
    entry({
      sources: { feed_in: "sensor.inverter_feed_in", grid_consumption: null, pv: "sensor.pv" },
      entities: { ...entry().entities, pv_self_consumed_month_so_far: "sensor.pv_self" },
    }),
    EN
  );
  assert.ok(titles(view).includes("PV production, feed-in and communities"));
  const check = graphs(view).find((c) => c.title === "Metered vs your feed-in source");
  assert.deepEqual(
    check.entities.map((e) => e.entity),
    [S("generation"), "sensor.inverter_feed_in"]
  );
  assert.ok(!titles(view).includes("Metered vs your grid consumption source"));
  assert.ok(cards(view).some((c) => c.type === "gauge" && c.entity === "sensor.pv_self"));
});

test("status explains both delays with templates", () => {
  const view = buildView(entry(), EN);
  const note = cards(view).find((c) => c.type === "markdown");
  assert.match(note.content, /states\('sensor\.eg_austria_home_data_complete_until'\)/);
  assert.match(note.content, /states\('sensor\.eg_austria_home_meter_data_until'\)/);
  const button = cards(view).find((c) => c.type === "button");
  assert.equal(button.tap_action.navigation_path, "/energy");
});

test("communities: entities card per community with its sensors", () => {
  const view = buildView(entry(), EN);
  const communities = cards(view).filter((c) => c.type === "entities");
  assert.deepEqual(
    communities.map((c) => [c.title, c.entities]),
    [["Example BEG", ["sensor.e", "sensor.f"]]]
  );
});

test("German labels", () => {
  const view = buildView(entry(), DE);
  assert.ok(titles(view).includes("Wohin die Einspeisung ging, pro Monat"));
  const gauge = cards(view).find((c) => c.type === "gauge");
  assert.equal(gauge.name, "Einspeisung an Gemeinschaften, Monat bisher");
});

test("sections without content are left out", () => {
  const view = buildView(
    entry({ statistics: {}, community_statistics: {}, entities: {} }),
    EN
  );
  assert.deepEqual(
    view.sections.map((s) => s.cards[0].heading),
    ["Import status", "Totals in the selected period"]
  );
});

test("one view per entry, entry_id filter, empty state", () => {
  const two = [entry(), entry({ entry_id: "entry2", site: "cabin", title: "Cabin" })];
  assert.deepEqual(
    buildDashboard(two, {}, EN).views.map((v) => v.path),
    ["home", "cabin"]
  );
  assert.deepEqual(
    buildDashboard(two, { entry_id: "entry2" }, EN).views.map((v) => v.path),
    ["cabin"]
  );
  const empty = buildDashboard([], {}, EN);
  assert.equal(empty.views[0].cards[0].type, "markdown");
});

test("generate asks the websocket command", async () => {
  const calls = [];
  const hass = {
    ...EN,
    callWS: async (msg) => {
      calls.push(msg);
      return { entries: [entry()] };
    },
  };
  const dashboard = await generate({}, hass);
  assert.deepEqual(calls, [{ type: "eg_austria/dashboard_config" }]);
  assert.equal(dashboard.views.length, 1);
});
