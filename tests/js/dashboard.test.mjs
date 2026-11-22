// Dashboard strategy (#52, #61): `node --test "tests/js/*.test.mjs"` (Node 22+).
// Synthetic entry data only.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  buildDashboard,
  buildView,
  collectionKey,
  createSuggestions,
  generate,
  seedDefaultPeriod,
} from "../../custom_components/eg_austria/frontend/eg-austria-dashboard.js";

const CORE_CARDS = new Set([
  "conditional",
  "energy-date-selection",
  "entities",
  "gauge",
  "heading",
  "markdown",
  "statistic",
  "statistics-graph",
  "vertical-stack",
]);

const S = (q) => `eg_austria:home_${q}`;
const COMPLETE = "sensor.eg_austria_home_data_complete_until";

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
    data_complete_until: COMPLETE,
    meter_data_until: "sensor.eg_austria_home_meter_data_until",
    last_run_status: "sensor.eg_austria_home_last_run_status",
    last_import: "sensor.eg_austria_home_last_import",
    token_expires: "sensor.eg_austria_home_token_expires",
    feed_in_to_communities_month_so_far: "sensor.a",
    feed_in_to_communities_last_month: "sensor.b",
    import_from_communities_month_so_far: "sensor.c",
    import_from_communities_last_month: "sensor.d",
    beg_feed_in_share_month_so_far: "sensor.e",
    beg_import_share_last_month: "sensor.f",
    eeg_feed_in_share_last_month: "sensor.g",
  },
  ...overrides,
});

const hass = (language, complete = "2026-11-06") => ({
  locale: { language },
  states: { [COMPLETE]: { state: complete } },
});
const EN = hass("en");
const DE = hass("de-AT");

// Every card, including the ones inside stacks and conditional cards.
const flat = (list) =>
  list.flatMap((c) => [c, ...flat(c.cards || []), ...(c.card ? flat([c.card]) : [])]);
const cards = (view) => flat(view.sections.flatMap((s) => s.cards));
const graphs = (view) => cards(view).filter((c) => c.type === "statistics-graph");
const titles = (view) => graphs(view).map((c) => c.title);
const headings = (view) =>
  view.sections.map((s) => (s.cards[0].type === "heading" ? s.cards[0].heading : null));

const COLLECTION = "energy_eg_austria_home";

test("only core cards, sections view per entry", () => {
  const view = buildView(entry(), EN);
  assert.equal(view.type, "sections");
  assert.equal(view.path, "home");
  assert.equal(view.title, "EG Austria Home");
  assert.equal(view.max_columns, 4);
  for (const card of cards(view)) {
    assert.ok(CORE_CARDS.has(card.type), card.type);
    if (card.collection_key) assert.equal(card.collection_key, COLLECTION);
  }
});

test("layout: overview, picker, communities, picked charts, 12 months", () => {
  const view = buildView(entry(), EN);
  assert.deepEqual(
    view.sections.map((s) => s.column_span),
    [4, 4, 2, 2, 4, 4]
  );
  assert.deepEqual(headings(view), [
    null,
    null,
    "Example BEG",
    "Example EEG",
    null,
    "Last 12 months",
  ]);
  const [banner, ...rest] = view.sections[0].cards;
  assert.equal(banner.type, "markdown");
  assert.deepEqual(banner.grid_options, { columns: "full" });
  // Two gauges (no PV source) and the status block, a quarter each.
  assert.deepEqual(
    rest.map((c) => [c.type, c.grid_options.columns]),
    [
      ["vertical-stack", 12],
      ["vertical-stack", 12],
      ["entities", 12],
    ]
  );
});

test("delay banner: both dates as templates, meter part optional", () => {
  const view = buildView(entry(), EN);
  const banner = view.sections[0].cards[0];
  assert.equal(banner.text_only, true);
  assert.match(banner.content, /<ha-alert alert-type="info">/);
  assert.match(banner.content, /states\('sensor\.eg_austria_home_data_complete_until'\)/);
  assert.match(banner.content, /states\('sensor\.eg_austria_home_meter_data_until'\)/);
  assert.match(banner.content, /strftime\('%-d %b %Y'\)/);
  const { meter_data_until: _, ...rest } = entry().entities;
  const noMeter = buildView(entry({ entities: rest }), DE).sections[0].cards[0];
  assert.doesNotMatch(noMeter.content, /meter_data_until/);
  assert.match(noMeter.content, /strftime\('%-d\.%-m\.%Y'\)/);
});

test("share gauge: month so far, last month as note and as fallback", () => {
  const view = buildView(entry(), EN);
  const stack = view.sections[0].cards[1];
  const [soFar, fallback, note] = stack.cards;
  assert.equal(soFar.card.entity, "sensor.a");
  assert.equal(soFar.card.name, "Feed-in to communities");
  assert.deepEqual(soFar.conditions, [
    { condition: "state", entity: "sensor.a", state_not: ["unknown", "unavailable"] },
  ]);
  assert.equal(fallback.card.entity, "sensor.b");
  assert.deepEqual(fallback.conditions[0].state, ["unknown", "unavailable"]);
  assert.deepEqual(soFar.card.segments, [{ from: 0, color: "#2f7fd1" }]);
  assert.equal(note.text_only, true);
  assert.match(note.content, /is_number\(states\('sensor\.a'\)\)/);
  assert.match(note.content, /states\('sensor\.b', with_unit=True\)/);
  // Only one period: a plain gauge that says which.
  const single = buildView(
    entry({ entities: { feed_in_to_communities_last_month: "sensor.b" } }),
    EN
  );
  const plain = single.sections[0].cards[0];
  assert.equal(plain.type, "gauge");
  assert.equal(plain.name, "Feed-in to communities, last month");
  assert.deepEqual(plain.grid_options, { columns: 12 });
});

test("status block: short names and a link to the Energy dashboard", () => {
  const view = buildView(entry(), EN);
  const status = cards(view).find((c) => c.type === "entities" && c.title === "Import status");
  assert.deepEqual(
    status.entities.map((r) => r.name),
    [
      "Data complete until",
      "Meter data until",
      "Last run",
      "Last import",
      "Token expires",
      "Energy dashboard",
    ]
  );
  const link = status.entities.at(-1);
  assert.equal(link.type, "weblink");
  assert.equal(link.url, "/energy");
});

test("picker section: date picker and four totals that follow it", () => {
  const view = buildView(entry(), EN);
  const [picker, ...stats] = view.sections[1].cards;
  assert.deepEqual(picker, {
    type: "energy-date-selection",
    collection_key: COLLECTION,
    grid_options: { columns: "full" },
  });
  assert.deepEqual(
    stats.map((c) => [c.type, c.entity, c.name, c.grid_options.columns]),
    [
      ["statistic", S("generation_to_community"), "Fed into communities", 12],
      ["statistic", S("generation_to_surplus"), "To surplus buyer", 12],
      ["statistic", S("consumption_from_community"), "Drawn from communities", 12],
      ["statistic", S("consumption_from_supplier"), "Drawn from supplier", 12],
    ]
  );
  assert.equal(stats[0].energy_date_selection, true);
  assert.equal(stats[0].collection_key, COLLECTION);
  assert.equal(stats[0].stat_type, "change");
});

test("communities: kWh for the picked period and the share sensors", () => {
  const view = buildView(entry(), EN);
  const [beg, eeg] = [view.sections[2], view.sections[3]];
  assert.deepEqual(
    beg.cards.slice(1).map((c) => [c.type, c.entity || null, c.name || null]),
    [
      ["statistic", S("beg_generation_to_community"), "Fed in"],
      ["statistic", S("beg_consumption_from_community"), "Drawn"],
      ["entities", null, null],
    ]
  );
  assert.equal(beg.cards[1].collection_key, COLLECTION);
  assert.deepEqual(beg.cards[3].entities, [
    { entity: "sensor.e", name: "Feed-in share, month so far" },
    { entity: "sensor.f", name: "Import share, last month" },
  ]);
  // EEG has no import statistic here.
  assert.deepEqual(
    eeg.cards.slice(1).map((c) => c.name || c.type),
    ["Fed in", "entities"]
  );
  // A community with nothing to show gets no section.
  const none = buildView(
    entry({ community_statistics: {}, entities: { data_complete_until: COMPLETE } }),
    EN
  );
  assert.ok(!headings(none).includes("Example BEG"));
});

test("charts: picked-period split in a pair, 12 months in a pair, fixed colours", () => {
  const view = buildView(entry(), EN);
  assert.deepEqual(titles(view), [
    "Where the feed-in went",
    "Where the import came from",
    "Where the feed-in went, by month",
    "Where the import came from, by month",
  ]);
  const [pickedFeedIn, pickedImport, monthly] = graphs(view);
  assert.equal(pickedFeedIn.chart_type, "bar-stack");
  assert.equal(pickedFeedIn.energy_date_selection, true);
  assert.equal(pickedFeedIn.collection_key, COLLECTION);
  assert.deepEqual(pickedFeedIn.grid_options, { columns: 24 });
  assert.equal(monthly.period, "month");
  assert.equal(monthly.days_to_show, 365);
  assert.equal(monthly.energy_date_selection, undefined);
  assert.deepEqual(monthly.grid_options, { columns: 24 });
  assert.deepEqual(monthly.entities, [
    { entity: S("beg_generation_to_community"), name: "Example BEG", color: "#2f7fd1" },
    { entity: S("eeg_generation_to_community"), name: "Example EEG", color: "#2aa876" },
    { entity: S("generation_to_surplus"), name: "Surplus buyer", color: "#e0a030" },
  ]);
  // The meter total is not stacked with its parts.
  assert.ok(!monthly.entities.some((e) => e.entity === S("generation")));
  // A community without the statistic is left out.
  assert.deepEqual(
    pickedImport.entities.map((e) => [e.name, e.color]),
    [
      ["Example BEG", "#2f7fd1"],
      ["Supplier", "#9aa3ad"],
    ]
  );
});

test("an odd last chart takes the full row", () => {
  const view = buildView(
    entry({
      statistics: { generation_to_surplus: S("generation_to_surplus") },
      community_statistics: {},
    }),
    EN
  );
  const only = graphs(view).filter((c) => c.title === "Where the feed-in went");
  assert.deepEqual(only[0].grid_options, { columns: "full" });
});

test("source cards only for sources that are set; meter check follows the picker", () => {
  assert.ok(!titles(buildView(entry(), EN)).some((t) => t.startsWith("PV")));
  const view = buildView(
    entry({
      sources: { feed_in: "sensor.inverter_feed_in", grid_consumption: null, pv: "sensor.pv" },
      entities: {
        ...entry().entities,
        pv_self_consumed_month_so_far: "sensor.pv_self",
        pv_self_consumed_last_month: "sensor.pv_self_last",
      },
    }),
    EN
  );
  assert.equal(headings(view).at(-1), "Your own sources");
  const pv = graphs(view).find((c) => c.title === "PV production, feed-in and communities");
  assert.equal(pv.chart_type, "bar");
  assert.equal(pv.period, "month");
  assert.deepEqual(pv.grid_options, { columns: 24 });
  assert.deepEqual(
    pv.entities.map((e) => e.color),
    ["#f2c94c", "#6c757f", "#2f7fd1"]
  );
  const check = graphs(view).find((c) => c.title === "Metered vs your feed-in source");
  assert.equal(check.chart_type, "bar");
  assert.equal(check.energy_date_selection, true);
  assert.deepEqual(check.grid_options, { columns: 24 });
  assert.deepEqual(
    check.entities.map((e) => [e.entity, e.color]),
    [
      [S("generation"), "#0b8fd6"],
      ["sensor.inverter_feed_in", "#7c5cd6"],
    ]
  );
  assert.ok(!titles(view).includes("Metered vs your grid consumption source"));
  // PV gauge in the overview row: three gauges and the status block.
  assert.equal(view.sections[0].cards.length, 5);
  assert.ok(cards(view).some((c) => c.type === "gauge" && c.entity === "sensor.pv_self"));
});

test("German labels", () => {
  const view = buildView(entry(), DE);
  assert.ok(titles(view).includes("Wohin die Einspeisung ging, pro Monat"));
  assert.equal(view.sections[0].cards[1].cards[0].card.name, "Einspeisung an Gemeinschaften");
  assert.equal(headings(view)[5], "Letzte 12 Monate");
  assert.equal(view.sections[2].cards[1].name, "Eingespeist");
});

test("sections without content are left out", () => {
  const view = buildView(
    entry({ statistics: {}, community_statistics: {}, entities: {} }),
    EN
  );
  // The status block with the Energy dashboard link and the picker are left.
  assert.deepEqual(
    view.sections.map((s) => s.cards.map((c) => c.type)),
    [["entities"], ["energy-date-selection"]]
  );
});

test("default period: This month is seeded once, never overwritten", () => {
  const store = new Map();
  const storage = {
    getItem: (k) => store.get(k) ?? null,
    setItem: (k, v) => store.set(k, v),
  };
  assert.equal(collectionKey(entry()), COLLECTION);
  seedDefaultPeriod(COLLECTION, storage);
  assert.deepEqual([...store], [["energy-default-period-_energy_eg_austria_home", "this_month"]]);
  store.set("energy-default-period-_energy_eg_austria_home", "this_year");
  seedDefaultPeriod(COLLECTION, storage);
  assert.equal(store.get("energy-default-period-_energy_eg_austria_home"), "this_year");
  // Blocked storage doesn't break the dashboard.
  seedDefaultPeriod(COLLECTION, {
    getItem: () => {
      throw new Error("blocked");
    },
  });
  // No storage at all (node): nothing happens.
  seedDefaultPeriod(COLLECTION);
});

test("Add dashboard suggestions follow the profile language", () => {
  assert.deepEqual(createSuggestions(EN), {
    title: "Energy community",
    icon: "mdi:home-group",
  });
  assert.equal(createSuggestions(DE).title, "Energiegemeinschaft");
  assert.equal(createSuggestions(hass("fr")).title, "Energy community");
  assert.equal(createSuggestions(undefined).title, "Energy community");
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
  const h = {
    ...EN,
    callWS: async (msg) => {
      calls.push(msg);
      return { entries: [entry()] };
    },
  };
  const dashboard = await generate({}, h);
  assert.deepEqual(calls, [{ type: "eg_austria/dashboard_config" }]);
  assert.equal(dashboard.views.length, 1);
});
