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

// Core cards plus the two cards this script defines.
const KNOWN_CARDS = new Set([
  "energy-date-selection",
  "custom:eg-austria-status-card",
  "heading",
  "markdown",
  "statistics-graph",
  "custom:eg-austria-period-card",
  "custom:eg-austria-community-card",
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
  },
  ...overrides,
});

const hass = (language, complete = "2026-11-06") => ({
  locale: { language },
  states: { [COMPLETE]: { state: complete } },
});
const EN = hass("en");
const DE = hass("de-AT");

const cards = (view) => view.sections.flatMap((s) => s.cards);
const graphs = (view) => cards(view).filter((c) => c.type === "statistics-graph");
const titles = (view) => graphs(view).map((c) => c.title);
const headings = (view) =>
  view.sections.map((s) => (s.cards[0].type === "heading" ? s.cards[0].heading : null));

const COLLECTION = "energy_eg_austria_home_dashboard";

test("known cards only, sections view per entry", () => {
  const view = buildView(entry(), EN);
  assert.equal(view.type, "sections");
  assert.equal(view.path, "home");
  assert.equal(view.title, "EG Austria Home");
  assert.equal(view.max_columns, 4);
  for (const card of cards(view)) {
    assert.ok(KNOWN_CARDS.has(card.type), card.type);
    if (card.collection_key) assert.equal(card.collection_key, COLLECTION);
  }
});

test("layout: status row and picker with own cards, picked charts, 12 months", () => {
  const view = buildView(entry(), EN);
  assert.deepEqual(
    view.sections.map((s) => s.column_span),
    [4, 4, 4]
  );
  assert.deepEqual(headings(view), [null, null, "Last 12 months"]);
  assert.deepEqual(
    view.sections[0].cards.map((c) => [c.type, c.grid_options.columns]),
    [
      ["markdown", "full"],
      ["custom:eg-austria-status-card", "full"],
      ["energy-date-selection", "full"],
      ["custom:eg-austria-period-card", "full"],
      ["custom:eg-austria-community-card", 24],
      ["custom:eg-austria-community-card", 24],
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

test("status row: short names, Last run and token marked, Energy dashboard link", () => {
  const view = buildView(entry(), EN);
  const status = view.sections[0].cards[1];
  assert.equal(status.title, "Import status");
  assert.deepEqual(
    status.items.map((i) => [i.name, i.kind || null]),
    [
      ["Data complete until", null],
      ["Meter data until", null],
      ["Last run", "run"],
      ["Last import", null],
      ["Token expires", "token"],
    ]
  );
  assert.equal(status.items[2].entity, "sensor.eg_austria_home_last_run_status");
  assert.deepEqual(status.link, { name: "Energy dashboard", url: "/energy" });
  assert.equal(buildView(entry(), DE).sections[0].cards[1].title, "Importstatus");
});

test("period card: donuts from the statistics, cut at Data complete until", () => {
  const view = buildView(entry(), EN);
  const card = view.sections[0].cards[3];
  assert.equal(card.collection_key, COLLECTION);
  assert.equal(card.complete_until, COMPLETE);
  assert.deepEqual(card.text, {
    total: "Total",
    until: "so far, until {date}",
    noData: "No community data for this period yet.",
    info: "Totals for the picked period, counted up to Data complete until, so all numbers cover the same days. Hover or tap a segment for the previous period and the best day.",
    best: "Best day {date}: {kwh}",
  });
  assert.deepEqual(
    card.donuts.map((d) => d.info.split(":")[0]),
    ["Import", "Feed-in"]
  );
  assert.deepEqual(
    card.donuts.map((d) => [d.title, d.say]),
    [
      ["Import", "Communities covered {pct} of your import"],
      ["Feed-in", "Communities took {pct} of your feed-in"],
    ]
  );
  const [imp, feed] = card.donuts;
  assert.deepEqual(imp.parts, [
    {
      name: "Example BEG",
      statistic: S("beg_consumption_from_community"),
      color: "#2f7fd1",
      share: true,
    },
    { name: "Supplier", statistic: S("consumption_from_supplier"), color: "#9aa3ad" },
  ]);
  assert.deepEqual(
    feed.parts.map((p) => [p.name, p.color, Boolean(p.share)]),
    [
      ["Example BEG", "#2f7fd1", true],
      ["Example EEG", "#2aa876", true],
      ["Surplus buyer", "#e0a030", false],
    ]
  );
  // PV donut only with a PV source: self-consumed = PV minus metered feed-in.
  const pv = buildView(
    entry({ sources: { feed_in: null, grid_consumption: null, pv: "sensor.pv" } }),
    EN
  ).sections[0].cards[3].donuts[2];
  assert.equal(pv.title, "PV");
  assert.deepEqual(pv.parts[0], {
    name: "Self-consumed",
    statistic: "sensor.pv",
    subtract: S("generation"),
    color: "#f2c94c",
    share: true,
  });
  // No community statistics: no donut has a share, so no card.
  const none = buildView(entry({ community_statistics: {} }), EN);
  assert.ok(!cards(none).some((c) => c.type === "custom:eg-austria-period-card"));
});

test("community cards: fed in and drawn as a share of all feed-in and import", () => {
  const view = buildView(entry(), EN);
  const [beg, eeg] = view.sections[0].cards.slice(4);
  assert.equal(beg.name, "Example BEG");
  assert.equal(beg.color, "#2f7fd1");
  assert.equal(beg.collection_key, COLLECTION);
  assert.equal(beg.complete_until, COMPLETE);
  assert.match(beg.text.info, /^kWh this community took from your feed-in/);
  assert.deepEqual(beg.rows, [
    {
      statistic: S("beg_generation_to_community"),
      name: "Fed in",
      total: S("generation"),
      of: "{pct} of your feed-in",
    },
    {
      statistic: S("beg_consumption_from_community"),
      name: "Drawn",
      total: S("consumption"),
      of: "{pct} of your import",
    },
  ]);
  // EEG has no import statistic here.
  assert.deepEqual(
    eeg.rows.map((r) => r.name),
    ["Fed in"]
  );
  assert.equal(eeg.color, "#2aa876");
  // Three communities: the odd last card takes the full row.
  const three = buildView(
    entry({
      communities: [...entry().communities, { slug: "x", name: "Example X" }],
      community_statistics: {
        ...entry().community_statistics,
        x: { generation_to_community: S("x_generation_to_community") },
      },
    }),
    EN
  );
  assert.deepEqual(
    three.sections[0].cards.slice(4).map((c) => c.grid_options.columns),
    [24, 24, "full"]
  );
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
});

test("German labels", () => {
  const view = buildView(entry(), DE);
  assert.ok(titles(view).includes("Wohin die Einspeisung ging, pro Monat"));
  const [, , , period, beg] = view.sections[0].cards;
  assert.equal(period.donuts[0].title, "Bezug");
  assert.equal(period.text.until, "bisher, bis {date}");
  assert.equal(headings(view)[2], "Letzte 12 Monate");
  assert.equal(beg.rows[0].name, "Eingespeist");
  assert.equal(beg.rows[0].of, "{pct} deiner Einspeisung");
});

test("sections without content are left out", () => {
  const view = buildView(
    entry({ statistics: {}, community_statistics: {}, entities: {} }),
    EN
  );
  // The status row (only the Energy dashboard link) and the picker are left.
  assert.deepEqual(
    view.sections.map((s) => s.cards.map((c) => c.type)),
    [["custom:eg-austria-status-card", "energy-date-selection"]]
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
  assert.deepEqual([...store], [["energy-default-period-_energy_eg_austria_home_dashboard", "this_month"]]);
  store.set("energy-default-period-_energy_eg_austria_home_dashboard", "this_year");
  seedDefaultPeriod(COLLECTION, storage);
  assert.equal(store.get("energy-default-period-_energy_eg_austria_home_dashboard"), "this_year");
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

// --- € (#75) ---------------------------------------------------------------

const moneyEntry = () => {
  const base = entry();
  return entry({
    statistics: {
      ...base.statistics,
      feed_in_eur: S("feed_in_eur"),
      consumption_eur: S("consumption_eur"),
      surplus_eur: S("surplus_eur"),
      feed_in_total_eur: S("feed_in_total_eur"),
      import_savings_eur: S("import_savings_eur"),
      supplier_eur: S("supplier_eur"),
    },
    community_statistics: {
      beg: {
        ...base.community_statistics.beg,
        feed_in_eur: S("beg_feed_in_eur"),
        consumption_eur: S("beg_consumption_eur"),
        import_savings_eur: S("beg_import_savings_eur"),
      },
      eeg: { ...base.community_statistics.eeg, feed_in_eur: S("eeg_feed_in_eur") },
    },
    settled: {
      beg: { generation: ["2026-09"], consumption: ["2026-09"] },
      eeg: { generation: [], consumption: [] },
    },
  });
};

test("€: parts, donut lines and settled months in the period card", () => {
  const card = buildView(moneyEntry(), EN).sections[0].cards[3];
  assert.deepEqual(card.settled, moneyEntry().settled);
  assert.equal(card.text.provisional, "provisional");
  assert.match(card.text.moneyInfo, /^€: community amounts are provisional/);
  assert.match(card.text.moneyInfo, /prices Zero kWh at the standard rate/);
  const [imp, feed] = card.donuts;
  assert.deepEqual(imp.money, [
    {
      name: "Cost",
      statistic: S("consumption_eur"),
      settle: [
        { slug: "beg", direction: "consumption" },
        { slug: "eeg", direction: "consumption" },
      ],
    },
    {
      name: "Saved",
      statistic: S("import_savings_eur"),
      settle: [
        { slug: "beg", direction: "consumption" },
        { slug: "eeg", direction: "consumption" },
      ],
    },
  ]);
  assert.equal(imp.parts[0].money, S("beg_consumption_eur"));
  assert.deepEqual(imp.parts[0].settle, [{ slug: "beg", direction: "consumption" }]);
  // Supplier: its energy cost at the supplier price, no settlement marker.
  assert.equal(imp.parts[1].money, S("supplier_eur"));
  assert.equal(imp.parts[1].settle, undefined);
  // Feed-in: compensation (community + surplus) when there is a surplus price.
  assert.equal(feed.money[0].statistic, S("feed_in_total_eur"));
  assert.deepEqual(
    feed.parts.map((p) => p.money),
    [S("beg_feed_in_eur"), S("eeg_feed_in_eur"), S("surplus_eur")]
  );
  assert.equal(feed.parts[2].settle, undefined);
  // Without a surplus price: community income only.
  const plain = moneyEntry();
  delete plain.statistics.feed_in_total_eur;
  delete plain.statistics.surplus_eur;
  const plainFeed = buildView(plain, EN).sections[0].cards[3].donuts[1];
  assert.equal(plainFeed.money[0].statistic, S("feed_in_eur"));
  assert.equal(plainFeed.parts[2].money, undefined);
});

test("€: community cards get € per row, savings and settled months", () => {
  const [beg, eeg] = buildView(moneyEntry(), DE).sections[0].cards.slice(4);
  assert.deepEqual(beg.rows[0].settle, [{ slug: "beg", direction: "generation" }]);
  assert.equal(beg.rows[0].money, S("beg_feed_in_eur"));
  assert.equal(beg.rows[1].money, S("beg_consumption_eur"));
  assert.equal(beg.rows[1].saved, S("beg_import_savings_eur"));
  assert.equal(beg.text.saved, "Gespart");
  assert.equal(beg.text.settled, "abgerechnet");
  assert.deepEqual(beg.settled, moneyEntry().settled);
  assert.equal(eeg.rows[0].money, S("eeg_feed_in_eur"));
  // No € statistics: cards as before (no settled map, no € texts).
  const [old] = buildView(entry(), EN).sections[0].cards.slice(4);
  assert.equal(old.settled, undefined);
  assert.equal(old.text.moneyInfo, undefined);
  assert.equal(buildView(entry(), EN).sections[0].cards[3].settled, undefined);
});
