// Dashboard strategy (#52, #61): `node --test "tests/js/*.test.mjs"` (Node 22+).
// Synthetic entry data only.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  buildDashboard,
  buildView,
  createSuggestions,
  generate,
  lastCompleteMonth,
} from "../../custom_components/eg_austria/frontend/eg-austria-dashboard.js";

const CORE_CARDS = new Set([
  "conditional",
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

test("only core cards, sections view per entry, no date picker", () => {
  const view = buildView(entry(), EN);
  assert.equal(view.type, "sections");
  assert.equal(view.path, "home");
  assert.equal(view.title, "EG Austria Home");
  assert.equal(view.max_columns, 4);
  for (const card of cards(view)) {
    assert.ok(CORE_CARDS.has(card.type), card.type);
    assert.equal(card.energy_date_selection, undefined);
    assert.equal(card.collection_key, undefined);
  }
});

test("layout: overview, totals side by side, communities, charts", () => {
  const view = buildView(entry(), EN);
  assert.deepEqual(
    view.sections.map((s) => s.column_span),
    [4, 2, 2, 4, 4]
  );
  assert.deepEqual(headings(view), [
    null,
    "Totals, October 2026",
    "Totals, month so far",
    "Communities",
    "Feed-in and import",
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

test("totals: last complete month as fixed period, month so far by calendar", () => {
  const view = buildView(entry(), EN);
  const [last, soFar] = [view.sections[1], view.sections[2]];
  const stats = last.cards.filter((c) => c.type === "statistic");
  assert.deepEqual(
    stats.map((c) => [c.entity, c.name]),
    [
      [S("generation_to_community"), "Fed into communities"],
      [S("generation_to_surplus"), "To surplus buyer"],
      [S("consumption_from_community"), "Drawn from communities"],
      [S("consumption_from_supplier"), "Drawn from supplier"],
    ]
  );
  assert.deepEqual(stats[0].period, {
    fixed_period: { start: "2026-10-01T00:00:00", end: "2026-11-01T00:00:00" },
  });
  assert.deepEqual(stats[0].grid_options, { columns: 6 });
  assert.equal(stats[0].stat_type, "change");
  assert.deepEqual(soFar.cards[1].period, { calendar: { period: "month", offset: 0 } });
  // Unknown complete date: the previous calendar month.
  const unknown = buildView(entry(), hass("en", "unknown"));
  assert.equal(unknown.sections[1].cards[0].heading, "Totals, last month");
  assert.deepEqual(unknown.sections[1].cards[1].period, {
    calendar: { period: "month", offset: -1 },
  });
});

test("last complete month", () => {
  const month = (state) => lastCompleteMonth(hass("en", state), COMPLETE);
  assert.equal(month("2026-11-06").label, "October 2026");
  assert.deepEqual(month("2026-10-31").period.fixed_period, {
    start: "2026-10-01T00:00:00",
    end: "2026-11-01T00:00:00",
  });
  assert.deepEqual(month("2031-01-05").period.fixed_period, {
    start: "2030-12-01T00:00:00",
    end: "2031-01-01T00:00:00",
  });
  assert.deepEqual(month("2026-12-31").period.fixed_period, {
    start: "2026-12-01T00:00:00",
    end: "2027-01-01T00:00:00",
  });
  assert.equal(month("2028-02-28").label, "January 2028");
  assert.equal(month("2028-02-29").label, "February 2028");
  assert.equal(lastCompleteMonth(hass("de", "2031-01-06"), COMPLETE).label, "Dezember 2030");
  assert.equal(month("unknown"), null);
  assert.equal(lastCompleteMonth(EN, undefined), null);
  assert.equal(lastCompleteMonth({}, COMPLETE), null);
});

test("feed-in and import split: one series per community, fixed colours", () => {
  const view = buildView(entry(), EN);
  assert.deepEqual(titles(view), [
    "Where the feed-in went, by month",
    "Where the import came from, by month",
    "Where the feed-in went, by day, 45 days",
    "Where the import came from, by day, 45 days",
  ]);
  const monthly = graphs(view)[0];
  assert.equal(monthly.chart_type, "bar-stack");
  assert.equal(monthly.period, "month");
  assert.deepEqual(monthly.entities, [
    { entity: S("beg_generation_to_community"), name: "Example BEG", color: "#2f7fd1" },
    { entity: S("eeg_generation_to_community"), name: "Example EEG", color: "#2aa876" },
    { entity: S("generation_to_surplus"), name: "Surplus buyer", color: "#e0a030" },
  ]);
  // The meter total is not stacked with its parts.
  assert.ok(!monthly.entities.some((e) => e.entity === S("generation")));
  const daily = graphs(view)[2];
  assert.equal(daily.period, "day");
  assert.equal(daily.days_to_show, 45);
  assert.deepEqual(daily.grid_options, { columns: "full" });
  // A community without the statistic is left out.
  assert.deepEqual(
    graphs(view)[1].entities.map((e) => [e.name, e.color]),
    [
      ["Example BEG", "#2f7fd1"],
      ["Supplier", "#9aa3ad"],
    ]
  );
});

test("source cards only for sources that are set; metered vs source as bars", () => {
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
  assert.deepEqual(
    pv.entities.map((e) => e.color),
    ["#f2c94c", "#6c757f", "#2f7fd1"]
  );
  const check = graphs(view).find((c) => c.title === "Metered vs your feed-in source");
  assert.equal(check.chart_type, "bar");
  assert.equal(check.days_to_show, 45);
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

test("communities: entities cards side by side with short names", () => {
  const view = buildView(entry(), EN);
  const communities = view.sections[3].cards.filter((c) => c.type === "entities");
  assert.deepEqual(
    communities.map((c) => [c.title, c.grid_options, c.entities]),
    [
      [
        "Example BEG",
        { columns: 24 },
        [
          { entity: "sensor.e", name: "Feed-in share, month so far" },
          { entity: "sensor.f", name: "Import share, last month" },
        ],
      ],
      [
        "Example EEG",
        { columns: 24 },
        [{ entity: "sensor.g", name: "Feed-in share, last month" }],
      ],
    ]
  );
  const { eeg_feed_in_share_last_month: _, ...rest } = entry().entities;
  const one = buildView(entry({ entities: rest }), EN).sections[3].cards[1];
  assert.deepEqual(one.grid_options, { columns: "full" });
});

test("German labels", () => {
  const view = buildView(entry(), DE);
  assert.ok(titles(view).includes("Wohin die Einspeisung ging, pro Monat"));
  assert.equal(view.sections[0].cards[1].cards[0].card.name, "Einspeisung an Gemeinschaften");
  assert.equal(view.sections[1].cards[0].heading, "Summen, Oktober 2026");
});

test("sections without content are left out", () => {
  const view = buildView(
    entry({ statistics: {}, community_statistics: {}, entities: {} }),
    EN
  );
  // Only the status block with the Energy dashboard link is left.
  assert.equal(view.sections.length, 1);
  assert.deepEqual(
    view.sections[0].cards.map((c) => c.type),
    ["entities"]
  );
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
