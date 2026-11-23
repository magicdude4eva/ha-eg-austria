// The two cards that follow the date picker (#61). Synthetic numbers only.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  clipPeriod,
  communityCardIds,
  completeCutoff,
  currentMonth,
  fetchDaily,
  fetchTotals,
  applySelection,
  previousPeriod,
  segmentDetail,
  settlementState,
  infoIcon,
  periodCardIds,
  periodTitle,
  pickerCard,
  pickerCollection,
  periodMonths,
  renderCommunityCard,
  renderPeriodCard,
  renderStatusCard,
  statusElement,
} from "../../custom_components/eg_austria/frontend/eg-austria-dashboard.js";

const TEXT = {
  info: "Totals & more.",
  best: "Best day {date}: {kwh}",
  total: "Total",
  until: "so far, until {date}",
  noData: "No community data for this period yet.",
};
const PERIOD = {
  collection_key: "energy_eg_austria_home_dashboard",
  complete_until: "sensor.complete",
  text: TEXT,
  donuts: [
    {
      title: "Import",
      info: "Import: from each community.",
      say: "Communities covered {pct} of your import",
      parts: [
        { name: "A & B", statistic: "s:a_in", color: "#2f7fd1", share: true },
        { name: "Supplier", statistic: "s:supplier", color: "#9aa3ad" },
      ],
    },
    {
      title: "PV",
      say: "You used {pct} of your PV yourself",
      parts: [
        { name: "Self-consumed", statistic: "s:pv", subtract: "s:gen", color: "#f2c94c", share: true },
        { name: "Fed in", statistic: "s:gen", color: "#6c757f" },
      ],
    },
  ],
};
const COMMUNITY = {
  collection_key: "energy_eg_austria_home_dashboard",
  complete_until: "sensor.complete",
  name: "Example <BEG>",
  color: "#2f7fd1",
  text: TEXT,
  rows: [
    { statistic: "s:a_out", name: "Fed in", total: "s:gen", of: "{pct} of your feed-in" },
    { statistic: "s:a_in", name: "Drawn", total: null, of: "{pct} of your import" },
  ],
};
const TOTALS = { "s:a_in": 25, "s:supplier": 75, "s:pv": 200, "s:gen": 150, "s:a_out": 30 };

const october = { start: new Date(2030, 9, 1), end: new Date(2030, 9, 31, 23, 59, 59, 999) };

test("period title always has the year", () => {
  assert.equal(periodTitle(october.start, october.end, "en"), "October 2030");
  assert.equal(
    periodTitle(new Date(2030, 0, 1), new Date(2030, 11, 31, 23, 59, 59, 999), "en"),
    "2030"
  );
  assert.equal(
    periodTitle(new Date(2030, 9, 5), new Date(2030, 9, 5, 23, 59, 59, 999), "en"),
    "Oct 5, 2030"
  );
  assert.equal(
    periodTitle(new Date(2030, 9, 5), new Date(2030, 9, 11, 23, 59, 59, 999), "en"),
    "Oct 5, 2030 – Oct 11, 2030"
  );
});

test("cutoff: the day after Data complete until, cut only inside the period", () => {
  const hass = { states: { "sensor.complete": { state: "2030-10-06" } } };
  const cutoff = completeCutoff(hass, "sensor.complete");
  assert.deepEqual(cutoff, new Date(2030, 9, 7));
  assert.equal(completeCutoff({ states: { x: { state: "unknown" } } }, "x"), null);
  assert.equal(completeCutoff(hass, null), null);
  const cut = clipPeriod(october, cutoff);
  assert.deepEqual([cut.until, cut.clipped, cut.empty], [cutoff, true, false]);
  const done = clipPeriod({ start: new Date(2030, 8, 1), end: new Date(2030, 9, 1) }, cutoff);
  assert.deepEqual([done.until, done.clipped], [new Date(2030, 9, 1), false]);
  assert.equal(clipPeriod({ start: new Date(2030, 10, 1), end: new Date(2030, 11, 1) }, cutoff).empty, true);
  // An open end counts to tomorrow.
  const open = clipPeriod({ start: october.start }, null, new Date(2030, 9, 20, 15));
  assert.deepEqual(open.until, new Date(2030, 9, 21));
  assert.deepEqual(currentMonth(new Date(2030, 1, 14)), {
    start: new Date(2030, 1, 1),
    end: new Date(2030, 2, 1),
  });
});

test("period card: totals, shares, PV minus feed-in, names escaped", () => {
  const period = clipPeriod(october, new Date(2030, 9, 7));
  const html = renderPeriodCard(PERIOD, TOTALS, period, "en");
  // No title (it sits under the picker), only how far the data goes.
  assert.doesNotMatch(html, /<h2>|October 2030/);
  assert.match(html, /So far, until Oct 6/);
  assert.doesNotMatch(renderPeriodCard(PERIOD, TOTALS, clipPeriod(october, null), "en"), /until/);
  assert.match(html, /Communities covered 25\.0 % of your import/);
  assert.match(html, /A &amp; B/);
  assert.match(html, /100\.0 kWh/);
  // PV 200, fed in 150: 50 self-consumed, 25 %.
  assert.match(html, /You used 25\.0 % of your PV yourself/);
  assert.match(html, /50\.0 kWh/);
  // A missing statistic leaves its part out instead of breaking the card.
  const partial = renderPeriodCard(PERIOD, { ...TOTALS, "s:supplier": null }, period, "en");
  assert.match(partial, /Communities covered 100\.0 % of your import/);
  const empty = renderPeriodCard(PERIOD, {}, { ...period, empty: true }, "en");
  assert.match(empty, /No community data for this period yet\./);
  assert.deepEqual(periodCardIds(PERIOD), ["s:a_in", "s:supplier", "s:pv", "s:gen", "s:gen"]);
});

test("info icon: native tooltip, or ha-tooltip once the frontend has it", () => {
  const html = renderPeriodCard(PERIOD, TOTALS, clipPeriod(october, null), "en");
  assert.match(html, /<ha-svg-icon id="info" path="M11,9H13V7H11/);
  assert.match(html, /title="Totals &amp; more\.\n\nImport: from each community\."/);
  assert.match(renderCommunityCard(COMMUNITY, TOTALS, clipPeriod(october, null), "en"), /title="Totals &amp; more\."/);
  assert.equal(infoIcon([null, ""]), "");
  globalThis.customElements = { get: (tag) => (tag === "ha-tooltip" ? class {} : undefined) };
  try {
    const rich = infoIcon(["A & B", "C"]);
    assert.match(rich, /<ha-tooltip for="info" placement="left">A &amp; B<br><br>C<\/ha-tooltip>/);
    assert.doesNotMatch(rich, /title=/);
  } finally {
    delete globalThis.customElements;
  }
});

test("community card: kWh, share of the total, bar width", () => {
  const period = clipPeriod(october, null);
  const html = renderCommunityCard(COMMUNITY, TOTALS, period, "en");
  assert.match(html, /<h2>Example &lt;BEG&gt;<\/h2>/);
  assert.match(html, /October 2030/);
  assert.doesNotMatch(html, /so far/);
  assert.match(html, /30\.0 kWh/);
  assert.match(html, /20\.0 % of your feed-in/);
  assert.match(html, /width:20\.0%;background:#2f7fd1/);
  // Without a total there is no share.
  assert.doesNotMatch(html, /of your import/);
  assert.deepEqual(communityCardIds(COMMUNITY), ["s:a_out", "s:gen", "s:a_in"]);
});

test("totals: change over the period in kWh, failures become null", async () => {
  const calls = [];
  const hass = {
    callWS: async (msg) => {
      calls.push(msg);
      if (msg.statistic_id === "s:bad") throw new Error("unknown");
      return msg.statistic_id === "s:none" ? {} : { change: 12.5 };
    },
  };
  const start = new Date(Date.UTC(2030, 9, 1));
  const end = new Date(Date.UTC(2030, 9, 7));
  const totals = await fetchTotals(hass, ["s:a", "s:a", "s:bad", "s:none"], start, end);
  assert.deepEqual(totals, { "s:a": 12.5, "s:bad": null, "s:none": null });
  assert.deepEqual(calls[0], {
    type: "recorder/statistic_during_period",
    statistic_id: "s:a",
    fixed_period: { start_time: "2030-10-01T00:00:00.000Z", end_time: "2030-10-07T00:00:00.000Z" },
    types: ["change"],
    units: { energy: "kWh" },
  });
});

// A stand-in for HTMLElement with a shadow root.
class FakeElement {
  attachShadow() {
    this.shadowRoot = { innerHTML: "" };
    return this.shadowRoot;
  }
}
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

test("card follows the picker's collection and stops when removed", async () => {
  let notify = null;
  let unsubscribed = false;
  const collection = {
    start: october.start,
    end: october.end,
    subscribe(cb) {
      notify = cb;
      return () => {
        unsubscribed = true;
      };
    },
  };
  const asked = [];
  const hass = {
    locale: { language: "en" },
    states: {},
    connection: { _energy_eg_austria_home_dashboard: collection },
    callWS: async (msg) => {
      asked.push(msg.fixed_period.start_time);
      return { change: TOTALS[msg.statistic_id] };
    },
  };
  assert.equal(pickerCollection(hass, "energy_eg_austria_home_dashboard"), collection);
  const Card = pickerCard(FakeElement, renderCommunityCard, communityCardIds, 2);
  const card = new Card();
  card.setConfig(COMMUNITY);
  card.hass = hass;
  card.connectedCallback();
  await settle();
  assert.match(card.shadowRoot.innerHTML, /October 2030/);
  assert.equal(card.getCardSize(), 2);
  // Same period again: no new requests.
  const before = asked.length;
  card.hass = { ...hass };
  await settle();
  assert.equal(asked.length, before);
  // The picker steps back a month.
  collection.start = new Date(2030, 8, 1);
  collection.end = new Date(2030, 8, 30, 23, 59, 59, 999);
  notify();
  await settle();
  assert.match(card.shadowRoot.innerHTML, /September 2030/);
  card.disconnectedCallback();
  assert.equal(unsubscribed, true);
});

test("card without a picker collection shows the current month", async () => {
  const hass = {
    locale: { language: "en" },
    states: {},
    connection: {},
    callWS: async () => ({ change: 1 }),
  };
  const Card = pickerCard(FakeElement, renderCommunityCard, communityCardIds, 2);
  const card = new Card();
  card.setConfig(COMMUNITY);
  card.hass = hass;
  card.connectedCallback();
  await settle();
  const now = new Date();
  const month = now.toLocaleDateString("en", { month: "long", year: "numeric" });
  assert.match(card.shadowRoot.innerHTML, new RegExp(`<span class="meta">${month}</span>`));
  card.disconnectedCallback();
});

const STATUS = {
  title: "Import status",
  items: [
    { entity: "sensor.complete", name: "Data complete until" },
    { entity: "sensor.run", name: "Last run", kind: "run" },
    { entity: "sensor.token", name: "Token expires", kind: "token" },
    { entity: "sensor.missing", name: "Meter data until" },
  ],
  link: { name: "Energy dashboard", url: "/energy" },
};
const statusHass = (run, token) => ({
  states: {
    "sensor.complete": { state: "2030-10-06" },
    "sensor.run": { state: run },
    "sensor.token": { state: token },
  },
  formatEntityState: (s) => (s.state === "ok" ? "OK" : `fmt:${s.state}`),
});
const NOW = new Date(Date.UTC(2030, 9, 20));

test("status row: values formatted by HA, Last run and token in colour", () => {
  const html = renderStatusCard(STATUS, statusHass("ok", "2031-10-20T10:00:00+00:00"), NOW);
  assert.match(html, /<span class="title">Import status<\/span>/);
  assert.match(html, /Data complete until<\/span><span class="value">fmt:2030-10-06/);
  assert.match(html, /background:var\(--success-color, #43a047\)"><\/span>OK/);
  // Token expires as HA formats the timestamp (a date), no colour while far off.
  assert.match(html, /<span class="value">fmt:2031-10-20T10:00:00\+00:00/);
  assert.match(html, /data-entity="sensor\.missing"><span class="label">Meter data until<\/span><span class="value">–/);
  assert.match(html, /<a class="link" href="\/energy">Energy dashboard ›<\/a>/);
  const bad = renderStatusCard(STATUS, statusHass("auth_failed", "2030-10-25T00:00:00+00:00"), NOW);
  assert.match(bad, /background:var\(--error-color, #db4437\)"><\/span>fmt:auth_failed/);
  assert.match(bad, /style="color:var\(--warning-color, #ffa600\)">fmt:2030-10-25/);
  const late = renderStatusCard(STATUS, statusHass("partial", "2030-10-01T00:00:00+00:00"), NOW);
  assert.match(late, /background:var\(--warning-color, #ffa600\)"><\/span>fmt:partial/);
  assert.match(late, /style="color:var\(--error-color, #db4437\)">fmt:2030-10-01/);
  // Without formatEntityState: the raw state.
  assert.match(renderStatusCard(STATUS, { states: { "sensor.run": { state: "ok" } } }, NOW), /<\/span>ok<\/span>/);
});

test("status element renders only when its entities change, opens more-info", () => {
  const events = [];
  class Base extends FakeElement {
    dispatchEvent(ev) {
      events.push(ev);
    }
  }
  const Card = statusElement(Base);
  const card = new Card();
  card.setConfig(STATUS);
  const hass = statusHass("ok", "2031-10-20T10:00:00+00:00");
  card.hass = hass;
  const first = card.shadowRoot.innerHTML;
  assert.match(first, /Import status/);
  card.shadowRoot.innerHTML = "kept";
  card.hass = { ...hass, states: { ...hass.states, "sensor.other": { state: "x" } } };
  assert.equal(card.shadowRoot.innerHTML, "kept");
  card.hass = statusHass("partial", "2031-10-20T10:00:00+00:00");
  assert.match(card.shadowRoot.innerHTML, /fmt:partial/);
  assert.equal(card.getCardSize(), 1);
  card._click({ target: { closest: (sel) => (sel === ".item" ? { dataset: { entity: "sensor.run" } } : null) } });
  assert.equal(events[0].type, "hass-more-info");
  assert.deepEqual(events[0].detail, { entityId: "sensor.run" });
});

test("previous period: month, year, cut month, days", () => {
  const month = previousPeriod(clipPeriod(october, null), "en");
  assert.deepEqual([month.start, month.until, month.label], [new Date(2030, 8, 1), new Date(2030, 9, 1), "September 2030"]);
  const year = previousPeriod(
    clipPeriod({ start: new Date(2030, 0, 1), end: new Date(2030, 11, 31, 23, 59, 59, 999) }, null),
    "en"
  );
  assert.equal(year.label, "2029");
  // Month so far until 6 Oct: the same days of September.
  const cut = previousPeriod(clipPeriod(october, new Date(2030, 9, 7)), "en");
  assert.deepEqual([cut.start, cut.until], [new Date(2030, 8, 1), new Date(2030, 8, 7)]);
  assert.equal(cut.label, "Sep 1, 2030 – Sep 6, 2030");
  // 31 March cut: 28 February at most.
  const march = previousPeriod(
    clipPeriod({ start: new Date(2030, 2, 1), end: new Date(2030, 2, 31, 23, 59, 59, 999) }, new Date(2030, 2, 31)),
    "en"
  );
  assert.deepEqual(march.until, new Date(2030, 1, 28));
  // A week: the seven days before.
  const week = previousPeriod(
    clipPeriod({ start: new Date(2030, 9, 7), end: new Date(2030, 9, 13, 23, 59, 59, 999) }, null),
    "en"
  );
  assert.deepEqual([week.start, week.until], [new Date(2030, 8, 30), new Date(2030, 9, 6, 23, 59, 59, 999)]);
});

test("daily values: one request for all ids, failures become empty", async () => {
  const calls = [];
  const hass = {
    callWS: async (msg) => {
      calls.push(msg);
      return { "s:a": [] };
    },
  };
  const start = new Date(Date.UTC(2030, 9, 1));
  const end = new Date(Date.UTC(2030, 9, 7));
  assert.deepEqual(await fetchDaily(hass, ["s:a", "s:a", "s:b"], start, end), { "s:a": [] });
  assert.deepEqual(calls[0], {
    type: "recorder/statistics_during_period",
    start_time: "2030-10-01T00:00:00.000Z",
    end_time: "2030-10-07T00:00:00.000Z",
    statistic_ids: ["s:a", "s:b"],
    period: "day",
    types: ["change"],
    units: { energy: "kWh" },
  });
  assert.deepEqual(await fetchDaily({ callWS: async () => { throw new Error("x"); } }, ["s:a"], start, end), {});
});

const day = (d) => +new Date(2030, 9, d);
const EXTRA = {
  previous: { totals: { "s:a_in": 50, "s:pv": 180, "s:gen": 100 }, label: "September 2030" },
  daily: {
    "s:a_in": [
      { start: day(1), change: 3 },
      { start: day(2), change: 9.25 },
      { start: day(7), change: 99 },
    ],
    "s:pv": [
      { start: day(1), change: 40 },
      { start: day(2), change: 30 },
    ],
    "s:gen": [
      { start: day(1), change: 25 },
      { start: day(2), change: 28 },
    ],
  },
  until: new Date(2030, 9, 7),
};

test("segment detail: previous period with change, best day before the cut", () => {
  const part = PERIOD.donuts[0].parts[0];
  assert.equal(
    segmentDetail(part, 25, EXTRA, "en", TEXT),
    "September 2030: 50.0 kWh (-50 %) · Best day Oct 2: 9.3 kWh"
  );
  // PV self-consumed per day: PV minus feed-in (15 on the 1st, 2 on the 2nd).
  const pv = PERIOD.donuts[1].parts[0];
  assert.equal(segmentDetail(pv, 50, EXTRA, "en", TEXT), "September 2030: 80.0 kWh (-38 %) · Best day Oct 1: 15.0 kWh");
  // Nothing before: no change; nothing at all: empty.
  assert.equal(
    segmentDetail(part, 25, { previous: { totals: { "s:a_in": 0 }, label: "Sep" } }, "en", TEXT),
    "Sep: 0.0 kWh"
  );
  assert.equal(segmentDetail(part, 25, {}, "en", TEXT), "");
});

test("period card: segments and legend rows carry share, kWh and detail", () => {
  const period = clipPeriod(october, new Date(2030, 9, 7));
  const html = renderPeriodCard(PERIOD, TOTALS, period, "en", EXTRA);
  assert.match(html, /<circle data-seg="0-0" /);
  assert.match(
    html,
    /<button type="button" class="part" data-seg="0-0" data-pct="25\.0 %" data-kwh="25\.0 kWh" data-detail="September 2030: 50\.0 kWh \(-50 %\) · Best day Oct 2: 9\.3 kWh">/
  );
  assert.match(html, /<span class="say" data-key="detail" data-default="Communities covered 25\.0 % of your import">/);
  assert.match(html, /<text data-key="kwh" data-default="100\.0 kWh"/);
  // Without details yet: empty detail, the sentence stays.
  assert.match(renderPeriodCard(PERIOD, TOTALS, period, "en"), /data-detail=""/);
});

// A minimal DOM for applySelection.
const node = (attrs) => {
  const classes = new Set();
  return {
    dataset: { ...attrs },
    textContent: attrs.default || "",
    classList: {
      toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)),
      contains: (c) => classes.has(c),
    },
  };
};

test("selection: highlights one segment and swaps the texts, null restores", () => {
  const row = node({ seg: "0-1", pct: "75.0 %", kwh: "75.0 kWh", detail: "Sep: 1 kWh" });
  const other = node({ seg: "0-0", pct: "25.0 %", kwh: "25.0 kWh", detail: "" });
  const ring = node({ seg: "0-1" });
  const keyed = [
    node({ key: "pct", default: "Total" }),
    node({ key: "kwh", default: "100.0 kWh" }),
    node({ key: "detail", default: "Communities covered 25.0 %" }),
  ];
  const donut = {
    ...node({}),
    querySelector: (sel) => [row, other].find((r) => sel.includes(`"${r.dataset.seg}"`)) || null,
    querySelectorAll: (sel) => (sel === "[data-seg]" ? [row, other, ring] : keyed),
  };
  const root = { querySelectorAll: () => [donut] };
  applySelection(root, "0-1");
  assert.equal(donut.classList.contains("focus"), true);
  assert.deepEqual([row, other, ring].map((n) => n.classList.contains("sel")), [true, false, true]);
  assert.deepEqual(keyed.map((n) => n.textContent), ["75.0 %", "75.0 kWh", "Sep: 1 kWh"]);
  applySelection(root, "0-0");
  // An empty detail keeps the sentence.
  assert.equal(keyed[2].textContent, "Communities covered 25.0 %");
  applySelection(root, null);
  assert.equal(donut.classList.contains("focus"), false);
  assert.deepEqual(keyed.map((n) => n.textContent), ["Total", "100.0 kWh", "Communities covered 25.0 %"]);
});

test("period card element: second render with previous period and best day", async () => {
  const asked = [];
  const hass = {
    locale: { language: "en" },
    states: {},
    connection: {
      _energy_eg_austria_home_dashboard: { start: october.start, end: october.end, subscribe: () => () => {} },
    },
    callWS: async (msg) => {
      asked.push(msg.type);
      if (msg.type === "recorder/statistics_during_period") return EXTRA.daily;
      const sept = msg.fixed_period.start_time.startsWith("2030-08-31") || msg.fixed_period.start_time.startsWith("2030-09-01");
      return { change: sept ? EXTRA.previous.totals[msg.statistic_id] ?? 0 : TOTALS[msg.statistic_id] };
    },
  };
  const Card = pickerCard(FakeElement, renderPeriodCard, periodCardIds, 4, true);
  const card = new Card();
  card.setConfig(PERIOD);
  card.hass = hass;
  card.connectedCallback();
  await settle();
  await settle();
  assert.ok(asked.includes("recorder/statistics_during_period"));
  assert.match(card.shadowRoot.innerHTML, /data-detail="September 2030: 50\.0 kWh/);
  card.disconnectedCallback();
});

// --- € (#75) ---------------------------------------------------------------

const MONEY_TEXT = {
  ...TEXT,
  settled: "settled",
  provisional: "provisional",
  partlySettled: "partly settled",
  saved: "Saved",
  moneyInfo: "€: provisional until settled.",
};
const SETTLED = {
  a: { generation: ["2030-09", "2030-10"], consumption: ["2030-09"] },
  b: { generation: ["2030-09"], consumption: [] },
};
const MONEY_PERIOD = {
  ...PERIOD,
  settled: SETTLED,
  text: MONEY_TEXT,
  donuts: [
    {
      title: "Import",
      say: "Communities covered {pct} of your import",
      money: [
        { name: "Cost", statistic: "s:cost", settle: [{ slug: "a", direction: "consumption" }] },
        { name: "Saved", statistic: "s:saved", settle: [{ slug: "a", direction: "consumption" }] },
      ],
      parts: [
        {
          name: "A",
          statistic: "s:a_in",
          color: "#2f7fd1",
          share: true,
          money: "s:a_cost",
          settle: [{ slug: "a", direction: "consumption" }],
        },
        { name: "Supplier", statistic: "s:supplier", color: "#9aa3ad" },
      ],
    },
    {
      title: "Feed-in",
      say: "Communities took {pct} of your feed-in",
      money: [
        {
          name: "Income",
          statistic: "s:income",
          settle: [
            { slug: "a", direction: "generation" },
            { slug: "b", direction: "generation" },
          ],
        },
      ],
      parts: [
        {
          name: "A",
          statistic: "s:a_out",
          color: "#2f7fd1",
          share: true,
          money: "s:a_income",
          settle: [{ slug: "a", direction: "generation" }],
        },
        { name: "Surplus buyer", statistic: "s:surplus", color: "#e0a030", money: "s:surplus_eur" },
      ],
    },
  ],
};
const MONEY_TOTALS = {
  ...TOTALS,
  "s:surplus": 120,
  "s:cost": 2.5,
  "s:saved": 0.56,
  "s:a_cost": 2.5,
  "s:income": 4.1,
  "s:a_income": 1.2,
  "s:surplus_eur": 2.9,
};

test("settlement state: all, some or none of the community months", () => {
  const oct = clipPeriod(october, null);
  assert.deepEqual(periodMonths(oct), ["2030-10"]);
  const year = clipPeriod({ start: new Date(2030, 0, 1), end: new Date(2031, 0, 1) }, new Date(2030, 10, 7));
  assert.equal(periodMonths(year).length, 11);
  const a = [{ slug: "a", direction: "generation" }];
  const ab = [...a, { slug: "b", direction: "generation" }];
  assert.equal(settlementState(a, SETTLED, oct), "settled");
  assert.equal(settlementState(ab, SETTLED, oct), "partly");
  assert.equal(settlementState([{ slug: "b", direction: "consumption" }], SETTLED, oct), "provisional");
  // A cut month 1-6 Oct is still October.
  assert.equal(settlementState(a, SETTLED, clipPeriod(october, new Date(2030, 9, 7))), "settled");
  assert.equal(settlementState(undefined, SETTLED, oct), null);
  assert.equal(settlementState(a, undefined, oct), null);
  assert.equal(settlementState(a, {}, oct), "provisional");
});

test("period card: € per segment and per donut, settled or provisional", () => {
  const period = clipPeriod(october, null);
  const html = renderPeriodCard(MONEY_PERIOD, MONEY_TOTALS, period, "en");
  // Import, October: A's consumption not settled → provisional, in italics.
  assert.match(html, /<span class="money provisional">Cost €2\.50 · Saved €0\.56 · provisional<\/span>/);
  assert.match(html, /<span class="value">25\.0 kWh<\/span><span class="eur provisional">€2\.50<\/span>/);
  assert.match(html, /data-detail="€2\.50 provisional"/);
  // Feed-in: A settled, B not → partly settled for the donut, A settled.
  assert.match(html, /<span class="money provisional">Income €4\.10 · partly settled<\/span>/);
  assert.match(html, /<span class="eur">€1\.20<\/span>/);
  assert.match(html, /data-detail="€1\.20 settled"/);
  // Surplus income: € without a marker.
  assert.match(html, /<span class="eur">€2\.90<\/span>/);
  assert.match(html, /data-detail="€2\.90"/);
  // Supplier part has no €.
  assert.match(html, /<span class="value">75\.0 kWh<\/span><\/button>/);
  assert.match(html, /€: provisional until settled\./);
  // No € totals yet: no money line, no € cells.
  const kwhOnly = renderPeriodCard(MONEY_PERIOD, TOTALS, period, "en").split("</style>")[1];
  assert.doesNotMatch(kwhOnly, /class="money|class="eur/);
  assert.deepEqual(periodCardIds(MONEY_PERIOD), [
    "s:a_in",
    "s:a_cost",
    "s:supplier",
    "s:cost",
    "s:saved",
    "s:a_out",
    "s:a_income",
    "s:surplus",
    "s:surplus_eur",
    "s:income",
  ]);
  // German number format.
  assert.match(renderPeriodCard(MONEY_PERIOD, MONEY_TOTALS, period, "de"), /Cost 2,50\s€/);
});

test("community card: € per row, savings, marker next to the period", () => {
  const config = {
    ...COMMUNITY,
    name: "A",
    settled: SETTLED,
    text: MONEY_TEXT,
    rows: [
      { ...COMMUNITY.rows[0], money: "s:a_income", settle: [{ slug: "a", direction: "generation" }] },
      {
        ...COMMUNITY.rows[1],
        money: "s:a_cost",
        saved: "s:saved",
        settle: [{ slug: "a", direction: "consumption" }],
      },
    ],
  };
  const period = clipPeriod(october, null);
  const html = renderCommunityCard(config, MONEY_TOTALS, period, "en");
  assert.match(html, /October 2030 · partly settled/);
  assert.match(html, /30\.0 kWh<\/span><span class="eur">€1\.20<\/span>/);
  assert.match(html, /25\.0 kWh<\/span><span class="eur provisional">€2\.50<\/span>/);
  assert.match(html, /<span class="money">Saved €0\.56<\/span>/);
  const sept = renderCommunityCard(
    config,
    MONEY_TOTALS,
    clipPeriod({ start: new Date(2030, 8, 1), end: new Date(2030, 9, 1) }, null),
    "en"
  );
  assert.match(sept, /September 2030 · settled/);
  assert.deepEqual(communityCardIds(config), ["s:a_out", "s:gen", "s:a_income", "s:a_in", "s:a_cost", "s:saved"]);
  // Without € statistics nothing changes.
  const body = renderCommunityCard(COMMUNITY, TOTALS, period, "en").split("</style>")[1];
  assert.doesNotMatch(body, /class="eur|settled|provisional/);
});
