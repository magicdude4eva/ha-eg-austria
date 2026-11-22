// The two cards that follow the date picker (#61). Synthetic numbers only.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  clipPeriod,
  communityCardIds,
  completeCutoff,
  currentMonth,
  fetchTotals,
  periodCardIds,
  periodTitle,
  pickerCard,
  pickerCollection,
  renderCommunityCard,
  renderPeriodCard,
} from "../../custom_components/eg_austria/frontend/eg-austria-dashboard.js";

const TEXT = {
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
  assert.match(html, /<h2>October 2030<\/h2>/);
  assert.match(html, /so far, until Oct 6/);
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
  const Card = pickerCard(FakeElement, renderPeriodCard, periodCardIds, 4);
  const card = new Card();
  card.setConfig(PERIOD);
  card.hass = hass;
  card.connectedCallback();
  await settle();
  const now = new Date();
  const month = now.toLocaleDateString("en", { month: "long", year: "numeric" });
  assert.match(card.shadowRoot.innerHTML, new RegExp(`<h2>${month}</h2>`));
  card.disconnectedCallback();
});
