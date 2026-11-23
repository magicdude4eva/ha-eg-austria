// EG Austria (unofficial): "Energy community" dashboard strategy (#52, #61).
//
// Served by the integration and loaded on every frontend start. Registers
// `custom:eg-austria` for Settings -> Dashboards -> Add dashboard. On each
// load it asks `eg_austria/dashboard_config` which statistics, comparison
// sources and sensors exist, and builds core cards only from that: cards for
// sources that aren't set are left out. Read-only; nothing is written.
//
// Layout (#61): the delay banner, the import status in one row (#63), then a date picker that opens on this
// month. Two small cards of our own follow the picker: the period card
// (import, feed-in and PV donuts) and one card per community. The by-day
// split and the meter check follow it too; the 12-month charts don't. Charts
// sit in pairs on a wide screen. With money statistics (#75) both cards show
// € next to the kWh, marked settled or provisional.

const STRATEGY_TYPE = "eg-austria";
const PERIOD_TAG = "eg-austria-period-card";
const COMMUNITY_TAG = "eg-austria-community-card";
const STATUS_TAG = "eg-austria-status-card";
const DOCS_URL =
  "https://github.com/magicdude4eva/ha-eg-austria#energy-community-dashboard";
// A group of houses: members of an energy community sharing their energy.
const ICON = "mdi:home-group";

// Series colours, the same in every chart.
const COLORS = {
  communities: ["#2f7fd1", "#2aa876", "#8e6bd6", "#d1497a", "#16a2b8"],
  surplus: "#e0a030",
  supplier: "#9aa3ad",
  pv: "#f2c94c",
  metered: "#0b8fd6",
  source: "#7c5cd6",
  meterTotal: "#6c757f",
};

const TEXT = {
  en: {
    name: "EG Austria energy community",
    title: "Energy community",
    description:
      "Community shares, feed-in and import split, and the import status of the EG Austria integration.",
    none: "No EG Austria entry is loaded. Set up the integration first.",
    dateFormat: "%-d %b %Y",
    delaysCommunity: "Community shares are complete until <b>{date}</b>",
    delaysMeter: ", meter values until <b>{date}</b>",
    delaysTail:
      ". Later days show feed-in and import without the community split yet.",
    status: "Import status",
    dataCompleteUntil: "Data complete until",
    meterDataUntil: "Meter data until",
    lastRun: "Last run",
    lastImport: "Last import",
    tokenExpires: "Token expires",
    energyDashboard: "Energy dashboard",
    importTitle: "Import",
    feedInTitle: "Feed-in",
    pvTitle: "PV",
    importSay: "Communities covered {pct} of your import",
    feedInSay: "Communities took {pct} of your feed-in",
    pvSay: "You used {pct} of your PV yourself",
    selfConsumed: "Self-consumed",
    total: "Total",
    until: "so far, until {date}",
    noData: "No community data for this period yet.",
    best: "Best day {date}: {kwh}",
    infoTotals:
      "Totals for the picked period, counted up to Data complete until, so all numbers cover the same days. Hover or tap a segment for the previous period and the best day.",
    infoImport: "Import: what each community supplied; the rest came from your supplier.",
    infoFeedIn: "Feed-in: what each community took; the rest went to the surplus buyer.",
    infoPv: "PV: production minus metered feed-in is what you used yourself.",
    infoCommunity:
      "kWh this community took from your feed-in and supplied to you in the picked period, and their share of all your feed-in and import. Counted up to Data complete until.",
    ofFeedIn: "{pct} of your feed-in",
    ofImport: "{pct} of your import",
    toCommunities: "Fed into communities",
    fedIn: "Fed in",
    drawn: "Drawn",
    last12: "Last 12 months",
    checks: "Your own sources",
    feedInSplit: "Where the feed-in went",
    importSplit: "Where the import came from",
    byMonth: "by month",
    surplusBuyer: "Surplus buyer",
    supplier: "Supplier",
    pvComparison: "PV production, feed-in and communities",
    pv: "PV production",
    meteredFeedIn: "Metered feed-in",
    meteredConsumption: "Metered consumption",
    yourFeedIn: "Your feed-in source",
    yourConsumption: "Your grid consumption source",
    feedInCheck: "Metered vs your feed-in source",
    consumptionCheck: "Metered vs your grid consumption source",
    cost: "Cost",
    saved: "Saved",
    income: "Income",
    settled: "settled",
    provisional: "provisional",
    partlySettled: "partly settled",
    infoMoney:
      "€: community amounts are provisional until EG Austria settles the month, then they match the invoice. They differ from the portal's Energieanalyse, which prices Zero kWh at the standard rate. Surplus uses your surplus price; supplier cost (energy only, no grid fees) and savings use your supplier price (options).",
  },
  de: {
    name: "EG Austria Energiegemeinschaft",
    title: "Energiegemeinschaft",
    description:
      "Gemeinschaftsanteile, Aufteilung von Einspeisung und Bezug und der Importstatus der EG-Austria-Integration.",
    none: "Kein EG-Austria-Eintrag ist geladen. Richte zuerst die Integration ein.",
    dateFormat: "%-d.%-m.%Y",
    delaysCommunity: "Gemeinschaftsanteile sind vollständig bis <b>{date}</b>",
    delaysMeter: ", Zählerwerte bis <b>{date}</b>",
    delaysTail:
      ". Spätere Tage zeigen Einspeisung und Bezug noch ohne Gemeinschaftsaufteilung.",
    status: "Importstatus",
    dataCompleteUntil: "Daten vollständig bis",
    meterDataUntil: "Zählerdaten bis",
    lastRun: "Letzter Lauf",
    lastImport: "Letzter Import",
    tokenExpires: "Token läuft ab",
    energyDashboard: "Energie-Dashboard",
    importTitle: "Bezug",
    feedInTitle: "Einspeisung",
    pvTitle: "PV",
    importSay: "Gemeinschaften deckten {pct} deines Bezugs",
    feedInSay: "Gemeinschaften nahmen {pct} deiner Einspeisung",
    pvSay: "Du hast {pct} deiner PV selbst verbraucht",
    selfConsumed: "Selbst verbraucht",
    total: "Gesamt",
    until: "bisher, bis {date}",
    noData: "Für diesen Zeitraum gibt es noch keine Gemeinschaftsdaten.",
    best: "Bester Tag {date}: {kwh}",
    infoTotals:
      "Summen für den gewählten Zeitraum, gezählt bis Daten vollständig bis, damit alle Zahlen dieselben Tage abdecken. Fahre über ein Segment oder tippe darauf für den Vorzeitraum und den besten Tag.",
    infoImport: "Bezug: was jede Gemeinschaft geliefert hat; der Rest kam vom Lieferanten.",
    infoFeedIn:
      "Einspeisung: was jede Gemeinschaft abgenommen hat; der Rest ging an den Überschussabnehmer.",
    infoPv: "PV: Erzeugung minus gemessene Einspeisung ist, was du selbst verbraucht hast.",
    infoCommunity:
      "kWh, die diese Gemeinschaft im gewählten Zeitraum von deiner Einspeisung abgenommen und dir geliefert hat, und ihr Anteil an deiner gesamten Einspeisung und deinem Bezug. Gezählt bis Daten vollständig bis.",
    ofFeedIn: "{pct} deiner Einspeisung",
    ofImport: "{pct} deines Bezugs",
    toCommunities: "An Gemeinschaften eingespeist",
    fedIn: "Eingespeist",
    drawn: "Bezogen",
    last12: "Letzte 12 Monate",
    checks: "Deine eigenen Quellen",
    feedInSplit: "Wohin die Einspeisung ging",
    importSplit: "Woher der Bezug kam",
    byMonth: "pro Monat",
    surplusBuyer: "Überschussabnehmer",
    supplier: "Lieferant",
    pvComparison: "PV-Erzeugung, Einspeisung und Gemeinschaften",
    pv: "PV-Erzeugung",
    meteredFeedIn: "Gemessene Einspeisung",
    meteredConsumption: "Gemessener Bezug",
    yourFeedIn: "Deine Einspeisequelle",
    yourConsumption: "Deine Netzbezugsquelle",
    feedInCheck: "Gemessen vs. deine Einspeisequelle",
    consumptionCheck: "Gemessen vs. deine Netzbezugsquelle",
    cost: "Kosten",
    saved: "Gespart",
    income: "Ertrag",
    settled: "abgerechnet",
    provisional: "vorläufig",
    partlySettled: "teilweise abgerechnet",
    infoMoney:
      "€: Gemeinschaftsbeträge sind vorläufig, bis EG Austria den Monat abrechnet; danach entsprechen sie der Rechnung. Sie weichen von der Energieanalyse im Portal ab, die Zero-kWh zum Standardtarif bewertet. Der Überschuss nutzt deinen Überschusspreis, Lieferantenkosten (nur Energie, ohne Netzentgelte) und Ersparnis deinen Lieferantenpreis (Optionen).",
  },
};

const language = (hass) =>
  ((hass && hass.locale && hass.locale.language) || "en").split("-")[0];
const text = (hass) => TEXT[language(hass)] || TEXT.en;

// Shown pre-filled in the "Add dashboard" dialog (frontend 2026.11+).
export const createSuggestions = (hass) => ({ title: text(hass).title, icon: ICON });

// Sections view, 4 columns on a wide screen. A section spanning all 4 has a
// 48-column grid, so `columns: 12` is a quarter there and the full width on
// a phone (one 12-column section).
const MAX_COLUMNS = 4;
const FULL = { columns: "full" };
const HALF = { columns: 24 };

const section = (cards, span = MAX_COLUMNS) =>
  cards.length ? { type: "grid", column_span: span, cards } : null;
const heading = (title) => ({ type: "heading", heading: title });

// --- Banner ---------------------------------------------------------------

const delayBanner = (entry, t) => {
  const e = entry.entities;
  if (!e.data_complete_until) return null;
  const parse = (name, entity) =>
    `{%- set ${name} = as_datetime(states('${entity}'), None) -%}`;
  const show = (name, entity) =>
    `{{ ${name}.strftime('${t.dateFormat}') if ${name} else states('${entity}') }}`;
  let head = parse("c", e.data_complete_until);
  let body = t.delaysCommunity.replace("{date}", show("c", e.data_complete_until));
  if (e.meter_data_until) {
    head += parse("m", e.meter_data_until);
    body += `{%- if m %}${t.delaysMeter.replace(
      "{date}",
      show("m", e.meter_data_until)
    )}{% endif -%}`;
  }
  return {
    type: "markdown",
    text_only: true,
    grid_options: FULL,
    content: `${head}<ha-alert alert-type="info">${body}${t.delaysTail}</ha-alert>`,
  };
};

// --- Status ----------------------------------------------------------------

// One row above the picker (#63), drawn by the status card below: Last run
// in colour, timestamps as dates (Token expires shows when, not "in a year").
const STATUS_ITEMS = [
  ["data_complete_until", "dataCompleteUntil"],
  ["meter_data_until", "meterDataUntil"],
  ["last_run_status", "lastRun", "run"],
  ["last_import", "lastImport"],
  ["token_expires", "tokenExpires", "token"],
];

const statusCard = (entry, t) => ({
  type: `custom:${STATUS_TAG}`,
  title: t.status,
  items: STATUS_ITEMS.filter(([key]) => entry.entities[key]).map(([key, name, kind]) => ({
    entity: entry.entities[key],
    name: t[name],
    ...(kind ? { kind } : {}),
  })),
  link: { name: t.energyDashboard, url: "/energy" },
  grid_options: FULL,
});

// --- Picked period ---------------------------------------------------------

// The frontend remembers the last preset per date picker in the browser
// (`energy-default-period-_<collection key>`) and opens on "Today" without
// one, which has no community data yet. Seed "This month" once, only when
// nothing is stored: a browser preference, not Home Assistant config. If the
// frontend renames the key, the picker just opens on "Today" again.
// The key differs from 0.1.2's (`energy_eg_austria_<site>`), whose picker
// opened on "Today" and may have left a day preset stored in the browser.
export const collectionKey = (entry) => `energy_eg_austria_${entry.site}_dashboard`;
export const DEFAULT_PERIOD = "this_month";

export const seedDefaultPeriod = (key, storage) => {
  try {
    const store =
      storage || (typeof localStorage !== "undefined" ? localStorage : null);
    const name = `energy-default-period-_${key}`;
    if (store && !store.getItem(name)) store.setItem(name, DEFAULT_PERIOD);
  } catch (err) {
    // Storage blocked (private window, policy): the picker opens on today.
  }
};

const picked = (collection) => ({
  energy_date_selection: true,
  collection_key: collection,
});

// Half width each on a wide screen; an odd last card takes the full row.
const pairs = (cards) =>
  cards.map((card, i) => ({
    ...card,
    grid_options:
      i === cards.length - 1 && cards.length % 2 ? FULL : HALF,
  }));

const communityColor = (i) => COLORS.communities[i % COLORS.communities.length];

// Labels the cards of our own need; the strategy passes them in the config.
const cardText = (t, info) => ({ total: t.total, until: t.until, noData: t.noData, info });

// € (#75): `money` is the € statistic of a part or line, `settle` the
// community settlements ({ slug, direction }) that make it final. Without
// `settle` (surplus income) there is no settled / provisional marker.
const settlesOf = (entry, direction) =>
  entry.communities.map((c) => ({ slug: c.slug, direction }));

const communityParts = (entry, quantity, moneyQuantity, direction) =>
  entry.communities
    .map((c, i) => {
      const stats = entry.community_statistics[c.slug] || {};
      const part = {
        name: c.name,
        statistic: stats[quantity],
        color: communityColor(i),
        share: true,
      };
      if (stats[moneyQuantity]) {
        part.money = stats[moneyQuantity];
        part.settle = [{ slug: c.slug, direction }];
      }
      return part;
    })
    .filter((p) => p.statistic);

const moneyLines = (lines) => lines.filter((l) => l.statistic);
const hasMoney = (entry) =>
  Object.keys(entry.statistics).some((k) => k.endsWith("_eur")) ||
  Object.values(entry.community_statistics).some((stats) =>
    Object.keys(stats || {}).some((k) => k.endsWith("_eur"))
  );
const moneyText = (t) => ({
  settled: t.settled,
  provisional: t.provisional,
  partlySettled: t.partlySettled,
});

// Each donut: the parts that make up a total; `share` parts give the % in
// the sentence. PV self-consumed is PV production minus metered feed-in.
const donuts = (entry, t) => {
  const s = entry.statistics;
  const list = [
    {
      title: t.importTitle,
      say: t.importSay,
      info: t.infoImport,
      money: moneyLines([
        {
          name: t.cost,
          statistic: s.consumption_eur,
          settle: settlesOf(entry, "consumption"),
        },
        {
          name: t.saved,
          statistic: s.import_savings_eur,
          settle: settlesOf(entry, "consumption"),
        },
      ]),
      parts: [
        ...communityParts(entry, "consumption_from_community", "consumption_eur", "consumption"),
        s.consumption_from_supplier && {
          name: t.supplier,
          statistic: s.consumption_from_supplier,
          color: COLORS.supplier,
          ...(s.supplier_eur ? { money: s.supplier_eur } : {}),
        },
      ],
    },
    {
      title: t.feedInTitle,
      say: t.feedInSay,
      info: t.infoFeedIn,
      // With a surplus price: community income + surplus income.
      money: moneyLines([
        {
          name: t.income,
          statistic: s.feed_in_total_eur || s.feed_in_eur,
          settle: settlesOf(entry, "generation"),
        },
      ]),
      parts: [
        ...communityParts(entry, "generation_to_community", "feed_in_eur", "generation"),
        s.generation_to_surplus && {
          name: t.surplusBuyer,
          statistic: s.generation_to_surplus,
          color: COLORS.surplus,
          ...(s.surplus_eur ? { money: s.surplus_eur } : {}),
        },
      ],
    },
  ];
  if (entry.sources.pv && s.generation) {
    list.push({
      title: t.pvTitle,
      say: t.pvSay,
      info: t.infoPv,
      parts: [
        {
          name: t.selfConsumed,
          statistic: entry.sources.pv,
          subtract: s.generation,
          color: COLORS.pv,
          share: true,
        },
        { name: t.fedIn, statistic: s.generation, color: COLORS.meterTotal },
      ],
    });
  }
  return list
    .map(({ money, ...d }) => ({
      ...d,
      ...(money && money.length ? { money } : {}),
      parts: d.parts.filter(Boolean),
    }))
    .filter((d) => d.parts.length > 1 && d.parts.some((p) => p.share));
};

const periodCard = (entry, t, collection) => {
  const list = donuts(entry, t);
  if (!list.length) return null;
  const card = {
    type: `custom:${PERIOD_TAG}`,
    collection_key: collection,
    complete_until: entry.entities.data_complete_until || null,
    donuts: list,
    text: { ...cardText(t, t.infoTotals), best: t.best },
    grid_options: FULL,
  };
  if (hasMoney(entry)) {
    card.settled = entry.settled || {};
    card.text = { ...card.text, ...moneyText(t), moneyInfo: t.infoMoney };
  }
  return card;
};

// One small card per community (a meter can be in up to five): kWh fed in
// and drawn for the picked period, as a share of all feed-in and import.
const communityCards = (entry, t, collection) =>
  entry.communities
    .map((c, i) => {
      const stats = entry.community_statistics[c.slug] || {};
      const s = entry.statistics;
      const rows = [
        [
          stats.generation_to_community,
          t.fedIn,
          s.generation,
          t.ofFeedIn,
          { money: stats.feed_in_eur, direction: "generation" },
        ],
        [
          stats.consumption_from_community,
          t.drawn,
          s.consumption,
          t.ofImport,
          { money: stats.consumption_eur, saved: stats.import_savings_eur, direction: "consumption" },
        ],
      ]
        .filter(([statistic]) => statistic)
        .map(([statistic, name, total, of, eur]) => {
          const row = { statistic, name, total: total || null, of };
          if (eur.money) {
            row.money = eur.money;
            row.settle = [{ slug: c.slug, direction: eur.direction }];
            if (eur.saved) row.saved = eur.saved;
          }
          return row;
        });
      if (!rows.length) return null;
      const card = {
        type: `custom:${COMMUNITY_TAG}`,
        collection_key: collection,
        complete_until: entry.entities.data_complete_until || null,
        name: c.name,
        color: communityColor(i),
        rows,
        text: cardText(t, t.infoCommunity),
      };
      if (rows.some((r) => r.money)) {
        card.settled = entry.settled || {};
        card.text = { ...card.text, ...moneyText(t), saved: t.saved, moneyInfo: t.infoMoney };
      }
      return card;
    })
    .filter(Boolean);

const pickerSection = (entry, t, collection) =>
  section(
    [
      delayBanner(entry, t),
      statusCard(entry, t),
      { type: "energy-date-selection", collection_key: collection, grid_options: FULL },
      periodCard(entry, t, collection),
      ...pairs(communityCards(entry, t, collection)),
    ].filter(Boolean)
  );

// --- Charts ---------------------------------------------------------------

// One statistics-graph series per existing statistic id.
const series = (items) =>
  items
    .filter(([id]) => Boolean(id))
    .map(([entity, name, color]) => ({ entity, name, color }));

const graph = (title, entities, options) => ({
  type: "statistics-graph",
  title,
  entities,
  stat_types: ["change"],
  ...options,
});

const MONTHS = { period: "month", days_to_show: 365 };

const splitSeries = (entry, quantity, rest, restName, restColor) =>
  series([
    ...entry.communities.map((c, i) => [
      (entry.community_statistics[c.slug] || {})[quantity],
      c.name,
      communityColor(i),
    ]),
    [entry.statistics[rest], restName, restColor],
  ]);

const splits = (entry, t) =>
  [
    [
      t.feedInSplit,
      splitSeries(
        entry,
        "generation_to_community",
        "generation_to_surplus",
        t.surplusBuyer,
        COLORS.surplus
      ),
    ],
    [
      t.importSplit,
      splitSeries(
        entry,
        "consumption_from_community",
        "consumption_from_supplier",
        t.supplier,
        COLORS.supplier
      ),
    ],
  ].filter(([, items]) => items.length);

// The split for the picked period (by day for a month, by hour for a day).
const pickedChartsSection = (entry, t, collection) => {
  const cards = splits(entry, t).map(([title, items]) =>
    graph(title, items, { chart_type: "bar-stack", ...picked(collection) })
  );
  return section(pairs(cards));
};

const monthChartsSection = (entry, t) => {
  const cards = splits(entry, t).map(([title, items]) =>
    graph(`${title}, ${t.byMonth}`, items, { chart_type: "bar-stack", ...MONTHS })
  );
  return cards.length ? section([heading(t.last12), ...pairs(cards)]) : null;
};

const checksSection = (entry, t, collection) => {
  const s = entry.statistics;
  const src = entry.sources;
  const cards = [];
  if (src.pv) {
    cards.push(
      graph(
        t.pvComparison,
        series([
          [src.pv, t.pv, COLORS.pv],
          [s.generation, t.meteredFeedIn, COLORS.meterTotal],
          [s.generation_to_community, t.toCommunities, COLORS.communities[0]],
        ]),
        { chart_type: "bar", ...MONTHS }
      )
    );
  }
  // Metered and source values agree within about 1 %, so two lines lie on
  // top of each other: grouped bars in two colours keep both visible.
  for (const [source, metered, title, mine, theirs] of [
    [src.feed_in, s.generation, t.feedInCheck, t.yourFeedIn, t.meteredFeedIn],
    [
      src.grid_consumption,
      s.consumption,
      t.consumptionCheck,
      t.yourConsumption,
      t.meteredConsumption,
    ],
  ]) {
    if (source && metered) {
      cards.push(
        graph(
          title,
          series([
            [metered, theirs, COLORS.metered],
            [source, mine, COLORS.source],
          ]),
          { chart_type: "bar", ...picked(collection) }
        )
      );
    }
  }
  return cards.length ? section([heading(t.checks), ...pairs(cards)]) : null;
};

// --- Cards that follow the date picker -------------------------------------
//
// Core cards can't show a share for the picked period, so two small cards of
// our own do. They read the picker's period from the frontend's energy
// collection, kept on the connection as `_<collection key>`. That is not a
// public API: without it the cards show the current month. Totals come from
// `recorder/statistic_during_period` (change over the period, in kWh), cut
// at "Data complete until" so every number covers the same days.

const DAY = 864e5;

export const currentMonth = (now = new Date()) => ({
  start: new Date(now.getFullYear(), now.getMonth(), 1),
  end: new Date(now.getFullYear(), now.getMonth() + 1, 1),
});

export const pickerCollection = (hass, key) =>
  (hass && hass.connection && hass.connection[`_${key}`]) || null;

// The first day without complete data: "Data complete until" + 1 day.
export const completeCutoff = (hass, entity) => {
  const state = entity && hass && hass.states && hass.states[entity];
  const m = state && /^(\d{4})-(\d{2})-(\d{2})/.exec(state.state);
  return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]) + 1) : null;
};

// The picked period, cut at the cutoff. An open end counts to tomorrow.
export const clipPeriod = (picked, cutoff, now = new Date()) => {
  const start = picked.start;
  const end = picked.end || new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
  const clipped = Boolean(cutoff && cutoff < end);
  const until = clipped ? cutoff : end;
  return { start, end, until, clipped, empty: until <= start };
};

const sameTime = (a, b) => Math.abs(a - b) <= 1000;

// "October 2026", "2026" or a date range, for the community cards.
export const periodTitle = (start, end, lang) => {
  const at = (d, o) => d.toLocaleDateString(lang, o);
  const y = start.getFullYear();
  const m = start.getMonth();
  if (start.getDate() === 1 && start.getHours() === 0) {
    if (m === 0 && sameTime(end, new Date(y + 1, 0, 1))) return String(y);
    if (sameTime(end, new Date(y, m + 1, 1))) return at(start, { month: "long", year: "numeric" });
  }
  const last = new Date(end - 1);
  const day = { day: "numeric", month: "short", year: "numeric" };
  return last - start < DAY ? at(start, day) : `${at(start, day)} – ${at(last, day)}`;
};

export const fetchTotals = async (hass, ids, start, end) => {
  const totals = {};
  await Promise.all(
    [...new Set(ids)].map(async (id) => {
      try {
        const result = await hass.callWS({
          type: "recorder/statistic_during_period",
          statistic_id: id,
          fixed_period: { start_time: start.toISOString(), end_time: end.toISOString() },
          types: ["change"],
          units: { energy: "kWh" },
        });
        totals[id] = result && typeof result.change === "number" ? result.change : null;
      } catch (err) {
        totals[id] = null;
      }
    })
  );
  return totals;
};

// Daily changes for the best day: { id: [{ start (ms), change }] }. HA also
// returns the day that starts at the end time; callers filter on `until`.
export const fetchDaily = async (hass, ids, start, end) => {
  try {
    const result = await hass.callWS({
      type: "recorder/statistics_during_period",
      start_time: start.toISOString(),
      end_time: end.toISOString(),
      statistic_ids: [...new Set(ids)],
      period: "day",
      types: ["change"],
      units: { energy: "kWh" },
    });
    return result || {};
  } catch (err) {
    return {};
  }
};

const shiftMonths = (d, n) => {
  const t = new Date(d.getFullYear(), d.getMonth() - n, 1, d.getHours(), d.getMinutes());
  t.setDate(Math.min(d.getDate(), new Date(t.getFullYear(), t.getMonth() + 1, 0).getDate()));
  return t;
};
const shiftDays = (d, n) => {
  const t = new Date(+d);
  t.setDate(t.getDate() - n);
  return t;
};

// The period before, as long as this one: the previous month or year, else
// the same number of days back. A cut period compares the same days
// (1-6 Nov with 1-6 Oct).
export const previousPeriod = (period, lang) => {
  const { start, end, until, clipped } = period;
  const y = start.getFullYear();
  const m = start.getMonth();
  const monthStart = start.getDate() === 1 && start.getHours() === 0 && start.getMinutes() === 0;
  let months = 0;
  if (monthStart && sameTime(end, new Date(y, m + 1, 1))) months = 1;
  else if (monthStart && m === 0 && sameTime(end, new Date(y + 1, 0, 1))) months = 12;
  let prevStart;
  let prevUntil;
  if (months) {
    prevStart = shiftMonths(start, months);
    prevUntil = clipped ? shiftMonths(until, months) : new Date(+start);
  } else {
    const days = Math.max(1, Math.round((end - start) / DAY));
    prevStart = shiftDays(start, days);
    prevUntil = shiftDays(until, days);
  }
  return { start: prevStart, until: prevUntil, label: periodTitle(prevStart, prevUntil, lang) };
};

const escapeHtml = (value) =>
  String(value).replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );

const kwh = (lang, value) =>
  `${new Intl.NumberFormat(lang, { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(value)} kWh`;
const percent = (lang, part, total) =>
  total > 0
    ? `${new Intl.NumberFormat(lang, { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format((100 * part) / total)} %`
    : "–";

const eur = (lang, value) =>
  new Intl.NumberFormat(lang, { style: "currency", currency: "EUR" }).format(value);

const moneyValue = (statistic, totals) => {
  const value = statistic ? totals[statistic] : null;
  return typeof value === "number" ? value : null;
};

// "YYYY-MM" of every month the period touches (start up to the cut).
export const periodMonths = (period) => {
  const months = [];
  const last = new Date(period.until - 1);
  const d = new Date(period.start.getFullYear(), period.start.getMonth(), 1);
  while (d <= last) {
    months.push(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`);
    d.setMonth(d.getMonth() + 1);
  }
  return months;
};

// "settled" when every community month it depends on is settled,
// "provisional" when none is, else "partly"; null without dependencies.
export const settlementState = (settle, settled, period) => {
  if (!settle || !settle.length || !settled) return null;
  const months = periodMonths(period);
  let done = 0;
  let all = 0;
  for (const { slug, direction } of settle) {
    const list = (settled[slug] || {})[direction] || [];
    for (const month of months) {
      all += 1;
      if (list.includes(month)) done += 1;
    }
  }
  if (!all) return null;
  return done === all ? "settled" : done ? "partly" : "provisional";
};

const stateText = (text, state) =>
  state === "settled"
    ? text.settled
    : state === "partly"
      ? text.partlySettled
      : state === "provisional"
        ? text.provisional
        : "";

const partValue = (part, totals) => {
  const value = totals[part.statistic];
  if (value === null || value === undefined) return null;
  const minus = part.subtract ? totals[part.subtract] : 0;
  return Math.max(value - (minus || 0), 0);
};

// The day with the most kWh for a part (PV self-consumed: per day PV minus
// feed-in), only days before `until`.
const bestDay = (part, daily, until) => {
  const minus = new Map(
    (part.subtract ? daily[part.subtract] || [] : []).map((r) => [r.start, r.change || 0])
  );
  let best = null;
  for (const r of daily[part.statistic] || []) {
    if (r.start >= +until || typeof r.change !== "number") continue;
    const value = Math.max(r.change - (minus.get(r.start) || 0), 0);
    if (value > 0 && (!best || value > best.value)) best = { start: r.start, value };
  }
  return best;
};

const change = (lang, now, before) =>
  `${new Intl.NumberFormat(lang, { maximumFractionDigits: 0, signDisplay: "exceptZero" }).format((100 * (now - before)) / before)} %`;

// The line under the donut title while a segment is selected.
export const segmentDetail = (part, value, extra, lang, text) => {
  const bits = [];
  if (extra.previous) {
    const before = partValue(part, extra.previous.totals);
    if (before !== null) {
      const delta = before > 0 ? ` (${change(lang, value, before)})` : "";
      bits.push(`${extra.previous.label}: ${kwh(lang, before)}${delta}`);
    }
  }
  if (extra.daily && text.best) {
    const best = bestDay(part, extra.daily, extra.until);
    if (best) {
      const date = new Date(best.start).toLocaleDateString(lang, { day: "numeric", month: "short" });
      // Its own line (the detail line keeps line breaks).
      return [bits.join(" · "), text.best.replace("{date}", date).replace("{kwh}", kwh(lang, best.value))]
        .filter(Boolean)
        .join("\n");
    }
  }
  return bits.join(" · ");
};

// Highlights one segment (`d-p`: donut and part index) and shows its share,
// kWh and detail in place of the defaults; null restores them. Only classes
// and text change, so focus and hover stay where they are.
export const applySelection = (root, seg) => {
  for (const donut of root.querySelectorAll(".donut")) {
    const row = seg ? donut.querySelector(`.part[data-seg="${seg}"]`) : null;
    donut.classList.toggle("focus", Boolean(row));
    for (const el of donut.querySelectorAll("[data-seg]")) {
      el.classList.toggle("sel", Boolean(row) && el.dataset.seg === seg);
    }
    for (const el of donut.querySelectorAll("[data-key]")) {
      const value = row ? row.dataset[el.dataset.key] : "";
      el.textContent = value || el.dataset.default;
    }
  }
};

const STYLE = `<style>
ha-card{position:relative;padding:16px;display:flex;flex-direction:column;gap:14px;height:100%;box-sizing:border-box}
#info{position:absolute;top:4px;inset-inline-end:4px;color:var(--secondary-text-color)}
ha-tooltip::part(base__popup){margin-top:4px}
.head{display:flex;align-items:baseline;gap:6px 10px;flex-wrap:wrap}
h2{margin:0;font-size:1.2em;font-weight:500;color:var(--primary-text-color)}
.meta,.say,.empty,.of{color:var(--secondary-text-color);font-size:.875em}
.say{white-space:pre-line;min-height:2.5em}
.donuts{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:16px 24px}
.donut{display:flex;gap:14px;align-items:flex-start;min-width:0}
svg{flex:none;width:120px;height:120px}
.text{display:flex;flex-direction:column;gap:4px;min-width:0;flex:1}
h3{margin:0;font-size:1em;font-weight:500}
.row,.part{display:flex;align-items:center;gap:8px;font-variant-numeric:tabular-nums}
.part{all:unset;display:flex;align-items:center;gap:8px;cursor:pointer;border-radius:4px;font-variant-numeric:tabular-nums}
.part:focus-visible{outline:2px solid var(--primary-color)}
circle[data-seg]{cursor:pointer;transition:opacity .15s}
.part{transition:opacity .15s}
.focus [data-seg]:not(.sel){opacity:.35}
@media (prefers-reduced-motion:reduce){circle[data-seg],.part{transition:none}}
.dot{width:10px;height:10px;border-radius:3px;flex:none}
.value{margin-left:auto;padding-left:8px;font-weight:500;white-space:nowrap;text-align:right}
.name{min-width:0;overflow-wrap:anywhere}
.legend{display:grid;grid-template-columns:auto 1fr auto auto;row-gap:4px}
.legend .part{grid-column:1/-1;display:grid;grid-template-columns:subgrid;column-gap:8px}
.rows{display:grid;grid-template-columns:1fr auto auto auto;row-gap:14px}
.bar{grid-column:1/-1;display:grid;grid-template-columns:subgrid;row-gap:4px}
.bar .row{grid-column:1/-1;display:grid;grid-template-columns:subgrid;align-items:baseline}
.bar .track,.bar .money{grid-column:1/-1}
.of{padding-left:8px;text-align:right;white-space:nowrap}
.track{height:8px;border-radius:4px;background:var(--divider-color);overflow:hidden}
.track i{display:block;height:100%;border-radius:4px}
.eur{padding-left:8px;color:var(--secondary-text-color);white-space:nowrap;text-align:right}
.money{color:var(--secondary-text-color);font-size:.875em}
.provisional{font-style:italic}
</style>`;

const donutSvg = (parts, total, lang, label, d) => {
  const r = 48;
  const c = 2 * Math.PI * r;
  let offset = 0;
  const rings = parts
    .map((p) => {
      const len = total > 0 ? (c * p.value) / total : 0;
      const ring = `<circle data-seg="${d}-${p.index}" cx="60" cy="60" r="${r}" fill="none" stroke="${p.color}" stroke-width="16" stroke-dasharray="${len} ${c - len}" stroke-dashoffset="${-offset}" transform="rotate(-90 60 60)"></circle>`;
      offset += len;
      return ring;
    })
    .join("");
  return `<svg viewBox="0 0 120 120" role="img" aria-label="${escapeHtml(label)} ${kwh(lang, total)}"><circle cx="60" cy="60" r="${r}" fill="none" stroke="var(--divider-color)" stroke-width="16"></circle>${rings}<text data-key="pct" data-default="${escapeHtml(label)}" x="60" y="56" text-anchor="middle" font-size="11" fill="var(--secondary-text-color)">${escapeHtml(label)}</text><text data-key="kwh" data-default="${kwh(lang, total)}" x="60" y="73" text-anchor="middle" font-size="13" font-weight="500" fill="var(--primary-text-color)">${kwh(lang, total)}</text></svg>`;
};

const untilText = (config, period, lang) =>
  period.clipped
    ? config.text.until.replace(
        "{date}",
        new Date(period.until - 1).toLocaleDateString(lang, { day: "numeric", month: "short" })
      )
    : "";

// Community card: its name, then the period and how far the data goes.
const header = (config, period, lang) => {
  const meta = [
    periodTitle(period.start, period.end, lang),
    untilText(config, period, lang),
    cardMarker(config, period),
  ]
    .filter(Boolean)
    .join(" · ");
  return `<div class="head"><h2>${escapeHtml(config.name)}</h2><span class="meta">${escapeHtml(meta)}</span></div>`;
};

// Period card: no title, it sits right under the picker; only how far the
// data goes when the period is cut.
const untilLine = (config, period, lang) => {
  const until = untilText(config, period, lang);
  return until
    ? `<div class="meta">${escapeHtml(until.charAt(0).toUpperCase() + until.slice(1))}</div>`
    : "";
};

// The (i) icon in the top corner, like the core energy cards. Its text in
// `ha-tooltip` when the frontend has loaded it, else as a native tooltip.
const INFO_PATH =
  "M11,9H13V7H11M12,20C7.59,20 4,16.41 4,12C4,7.59 7.59,4 12,4C16.41,4 20,7.59 20,12C20,16.41 16.41,20 12,20M12,2A10,10 0 0,0 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12A10,10 0 0,0 12,2M11,17H13V11H11V17Z";

export const infoIcon = (lines) => {
  const text = lines.filter(Boolean);
  if (!text.length) return "";
  const tooltip = typeof customElements !== "undefined" && customElements.get("ha-tooltip");
  if (tooltip) {
    return `<ha-svg-icon id="info" path="${INFO_PATH}"></ha-svg-icon><ha-tooltip for="info" placement="left">${text.map(escapeHtml).join("<br><br>")}</ha-tooltip>`;
  }
  return `<ha-svg-icon id="info" path="${INFO_PATH}" title="${escapeHtml(text.join("\n\n"))}"></ha-svg-icon>`;
};

// The € line under a donut's sentence: "Cost 5,23 € · Saved 2,87 €" and the
// marker of its community months; nothing without € values.
const moneyLine = (donut, totals, config, period, lang) => {
  const bits = [];
  let state = null;
  for (const line of donut.money || []) {
    const value = moneyValue(line.statistic, totals);
    if (value === null) continue;
    bits.push(`${line.name} ${eur(lang, value)}`);
    state = state || settlementState(line.settle, config.settled, period);
  }
  if (!bits.length) return "";
  const marker = stateText(config.text, state);
  if (marker) bits.push(marker);
  const cls = state && state !== "settled" ? "money provisional" : "money";
  return `<span class="${cls}">${escapeHtml(bits.join(" · "))}</span>`;
};

// `extra` (after a second fetch): { previous: { totals, label }, daily,
// until } for the segment details.
export const renderPeriodCard = (config, totals, period, lang, extra = {}) => {
  let body;
  if (period.empty) {
    body = `<div class="empty">${escapeHtml(config.text.noData)}</div>`;
  } else {
    const details = { ...extra, until: period.until };
    body = `<div class="donuts">${config.donuts
      .map((d, di) => {
        const parts = d.parts
          .map((p, index) => ({ ...p, index, value: partValue(p, totals) }))
          .filter((p) => p.value !== null);
        const total = parts.reduce((sum, p) => sum + p.value, 0);
        const share = parts.filter((p) => p.share).reduce((sum, p) => sum + p.value, 0);
        const rows = parts
          .map((p) => {
            const money = moneyValue(p.money, totals);
            const state = settlementState(p.settle, config.settled, period);
            const marker = stateText(config.text, state);
            const eurText = money === null ? "" : eur(lang, money);
            const detail = [
              eurText && [eurText, marker].filter(Boolean).join(" "),
              segmentDetail(p, p.value, details, lang, config.text),
            ]
              .filter(Boolean)
              .join(" · ");
            const eurCell = eurText
              ? `<span class="eur${state && state !== "settled" ? " provisional" : ""}">${eurText}</span>`
              : "";
            return `<button type="button" class="part" data-seg="${di}-${p.index}" data-pct="${percent(lang, p.value, total)}" data-kwh="${kwh(lang, p.value)}" data-detail="${escapeHtml(detail)}"><span class="dot" style="background:${p.color}"></span><span class="name">${escapeHtml(p.name)}</span><span class="value">${kwh(lang, p.value)}</span>${eurCell}</button>`;
          })
          .join("");
        const say = escapeHtml(d.say.replace("{pct}", percent(lang, share, total)));
        return `<div class="donut">${donutSvg(parts, total, lang, config.text.total, di)}<div class="text"><h3>${escapeHtml(d.title)}</h3><span class="say" data-key="detail" data-default="${say}">${say}</span>${moneyLine(d, totals, config, period, lang)}<div class="legend">${rows}</div></div></div>`;
      })
      .join("")}</div>`;
  }
  const info = infoIcon([
    config.text.info,
    ...config.donuts.map((d) => d.info),
    config.text.moneyInfo,
  ]);
  return `${STYLE}<ha-card>${info}${untilLine(config, period, lang)}${body}</ha-card>`;
};

export const renderCommunityCard = (config, totals, period, lang) => {
  let body;
  if (period.empty) {
    body = `<div class="empty">${escapeHtml(config.text.noData)}</div>`;
  } else {
    body = config.rows
      .map((row) => {
        const value = totals[row.statistic] || 0;
        const total = row.total ? totals[row.total] || 0 : 0;
        const width = total > 0 ? Math.min(100, (100 * value) / total) : 0;
        const of = row.total ? row.of.replace("{pct}", percent(lang, value, total)) : "";
        const money = moneyValue(row.money, totals);
        const saved = moneyValue(row.saved, totals);
        const state = settlementState(row.settle, config.settled, period);
        const eurCell =
          money === null
            ? ""
            : `<span class="eur${state && state !== "settled" ? " provisional" : ""}">${eur(lang, money)}</span>`;
        const savedLine =
          saved === null
            ? ""
            : `<span class="money">${escapeHtml(`${config.text.saved} ${eur(lang, saved)}`)}</span>`;
        // Empty cells keep the columns: kWh, € and share line up across rows.
        return `<div class="bar"><div class="row"><span class="name">${escapeHtml(row.name)}</span><span class="value">${kwh(lang, value)}</span>${eurCell || "<span></span>"}<span class="of">${escapeHtml(of)}</span></div><div class="track"><i style="width:${width.toFixed(1)}%;background:${config.color}"></i></div>${savedLine}</div>`;
      })
      .join("");
    body = `<div class="rows">${body}</div>`;
  }
  const info = infoIcon([config.text.info, config.text.moneyInfo]);
  return `${STYLE}<ha-card>${info}${header(config, period, lang)}${body}</ha-card>`;
};

// The community card's marker next to the period: settled, partly settled
// or provisional over the rows that have € (both directions).
const cardMarker = (config, period) => {
  if (!config.settled || period.empty) return "";
  const settle = (config.rows || []).flatMap((r) => (r.money ? r.settle || [] : []));
  return stateText(config.text, settlementState(settle, config.settled, period));
};

export const periodCardIds = (config) =>
  config.donuts.flatMap((d) => [
    ...d.parts.flatMap((p) => [p.statistic, p.subtract, p.money].filter(Boolean)),
    ...(d.money || []).map((l) => l.statistic),
  ]);
export const communityCardIds = (config) =>
  config.rows.flatMap((r) => [r.statistic, r.total, r.money, r.saved].filter(Boolean));

// The element: finds the picker's collection (it may appear a moment after
// the card), follows its period and renders when period or cutoff change.
// With `details` (the period card): a second fetch for the previous period
// and the daily values, and segment selection by hover, focus or tap.
export const pickerCard = (Base, render, ids, size, details = false) =>
  class extends Base {
    setConfig(config) {
      this._config = config;
      this._key = null;
      this._sync();
    }

    set hass(hass) {
      this._hass = hass;
      this._sync();
    }

    connectedCallback() {
      this._connected = true;
      this._sync();
    }

    disconnectedCallback() {
      this._connected = false;
      if (this._unsub) this._unsub();
      this._unsub = null;
      this._collection = null;
      this._key = null;
      clearTimeout(this._retry);
      this._retry = null;
    }

    getCardSize() {
      return size;
    }

    _sync() {
      if (!this._hass || !this._config || !this._connected) return;
      if (!this._collection) {
        const collection = pickerCollection(this._hass, this._config.collection_key);
        if (collection) {
          this._collection = collection;
          try {
            const unsub = collection.subscribe(() => this._sync());
            this._unsub = typeof unsub === "function" ? unsub : null;
          } catch (err) {
            // Changed frontend: keep the period it has now.
          }
        } else if (!this._retry && (this._tries = (this._tries || 0) + 1) <= 20) {
          this._retry = setTimeout(() => {
            this._retry = null;
            this._sync();
          }, 500);
        }
      }
      const c = this._collection;
      const picked = c && c.start instanceof Date ? { start: c.start, end: c.end } : currentMonth();
      const period = clipPeriod(picked, completeCutoff(this._hass, this._config.complete_until));
      const lang = language(this._hass);
      const key = `${+period.start}|${+period.end}|${+period.until}|${lang}`;
      if (key === this._key) return;
      this._key = key;
      if (period.empty) {
        this._show(render(this._config, {}, period, lang));
        return;
      }
      const list = ids(this._config);
      fetchTotals(this._hass, list, period.start, period.until).then((totals) => {
        if (this._key !== key) return;
        this._show(render(this._config, totals, period, lang));
        if (!details) return;
        const prev = previousPeriod(period, lang);
        Promise.all([
          fetchTotals(this._hass, list, prev.start, prev.until),
          period.until - period.start > DAY
            ? fetchDaily(this._hass, list, period.start, period.until)
            : Promise.resolve(null),
        ]).then(([before, daily]) => {
          if (this._key !== key) return;
          const extra = { previous: { totals: before, label: prev.label }, daily };
          this._show(render(this._config, totals, period, lang, extra));
        });
      });
    }

    _show(html) {
      if (!this.shadowRoot) {
        this.attachShadow({ mode: "open" });
        if (details && this.shadowRoot.addEventListener) this._listen(this.shadowRoot);
      }
      this.shadowRoot.innerHTML = html;
      if (details && this._sel) applySelection(this.shadowRoot, this._sel);
    }

    // Hover or focus shows a segment; a click or tap keeps it until the same
    // segment or anything else in the card is clicked.
    _listen(root) {
      const seg = (el) => {
        const hit = el && el.closest ? el.closest("[data-seg]") : null;
        return hit ? hit.dataset.seg : null;
      };
      const hover = (s) => {
        if (!this._pinned) this._select(s);
      };
      root.addEventListener("pointerover", (ev) => hover(seg(ev.target)));
      root.addEventListener("pointerout", (ev) => {
        if (!seg(ev.relatedTarget)) hover(null);
      });
      root.addEventListener("focusin", (ev) => hover(seg(ev.target)));
      root.addEventListener("focusout", (ev) => {
        if (!seg(ev.relatedTarget)) hover(null);
      });
      root.addEventListener("click", (ev) => {
        const s = seg(ev.target);
        if (s && this._pinned !== s) {
          this._pinned = s;
          this._select(s);
        } else {
          this._pinned = null;
          if (!s) this._select(null);
        }
      });
    }

    _select(s) {
      if (s === this._sel) return;
      this._sel = s;
      applySelection(this.shadowRoot, s);
    }
  };

// --- Status card -------------------------------------------------------------

const OK_COLOR = "var(--success-color, #43a047)";
const WARN_COLOR = "var(--warning-color, #ffa600)";
const ERROR_COLOR = "var(--error-color, #db4437)";
// Last run: fine, passing (a later run catches up), or needs you.
const RUN_COLORS = {
  ok: OK_COLOR,
  partial: WARN_COLOR,
  skipped_maintenance: WARN_COLOR,
  rate_limited: WARN_COLOR,
  auth_failed: ERROR_COLOR,
  api_changed: ERROR_COLOR,
  unavailable: ERROR_COLOR,
};
// Token expires: amber in the last two weeks, red once expired.
const TOKEN_WARN_DAYS = 14;

const STATUS_STYLE = `<style>
ha-card{padding:10px 16px;display:flex;flex-wrap:wrap;align-items:center;gap:6px 24px;height:100%;box-sizing:border-box}
.title{font-weight:500;color:var(--primary-text-color)}
.item,.link{all:unset;cursor:pointer;display:flex;align-items:baseline;gap:6px;border-radius:6px}
.item:focus-visible,.link:focus-visible{outline:2px solid var(--primary-color)}
.label{color:var(--secondary-text-color);font-size:.875em}
.value{display:inline-flex;align-items:center;gap:6px;color:var(--primary-text-color);font-weight:500}
.dot{width:9px;height:9px;border-radius:50%;flex:none}
.link{margin-inline-start:auto;color:var(--primary-color)}
</style>`;

const statusColor = (item, state, now) => {
  if (!state) return "";
  if (item.kind === "run") return RUN_COLORS[state.state] || "";
  if (item.kind === "token") {
    const at = Date.parse(state.state);
    if (Number.isNaN(at)) return "";
    if (at <= +now) return ERROR_COLOR;
    if (at - now < TOKEN_WARN_DAYS * DAY) return WARN_COLOR;
  }
  return "";
};

export const renderStatusCard = (config, hass, now = new Date()) => {
  const items = config.items
    .map((item) => {
      const state = hass && hass.states ? hass.states[item.entity] : undefined;
      const value = !state
        ? "–"
        : typeof hass.formatEntityState === "function"
          ? hass.formatEntityState(state)
          : state.state;
      const color = statusColor(item, state, now);
      const dot =
        item.kind === "run"
          ? `<span class="dot" style="background:${color || "var(--disabled-color, #9e9e9e)"}"></span>`
          : "";
      const style = color && item.kind !== "run" ? ` style="color:${color}"` : "";
      return `<button class="item" data-entity="${escapeHtml(item.entity)}"><span class="label">${escapeHtml(item.name)}</span><span class="value"${style}>${dot}${escapeHtml(value)}</span></button>`;
    })
    .join("");
  const link = config.link
    ? `<a class="link" href="${escapeHtml(config.link.url)}">${escapeHtml(config.link.name)} ›</a>`
    : "";
  return `${STATUS_STYLE}<ha-card><span class="title">${escapeHtml(config.title)}</span>${items}${link}</ha-card>`;
};

// Re-renders only when one of its entities changes. A click on a value opens
// its more-info dialog; the link navigates inside the frontend.
export const statusElement = (Base) =>
  class extends Base {
    setConfig(config) {
      this._config = config;
      this._key = null;
      this._render();
    }

    set hass(hass) {
      this._hass = hass;
      this._render();
    }

    getCardSize() {
      return 1;
    }

    _render() {
      if (!this._hass || !this._config) return;
      const states = this._hass.states || {};
      const key = this._config.items
        .map((i) => (states[i.entity] ? states[i.entity].state : ""))
        .join("|");
      if (key === this._key) return;
      this._key = key;
      if (!this.shadowRoot) {
        this.attachShadow({ mode: "open" });
        if (this.shadowRoot.addEventListener) {
          this.shadowRoot.addEventListener("click", (ev) => this._click(ev));
        }
      }
      this.shadowRoot.innerHTML = renderStatusCard(this._config, this._hass);
    }

    _click(ev) {
      const item = ev.target.closest && ev.target.closest(".item");
      if (item) {
        this.dispatchEvent(
          new CustomEvent("hass-more-info", {
            detail: { entityId: item.dataset.entity },
            bubbles: true,
            composed: true,
          })
        );
        return;
      }
      const link = ev.target.closest && ev.target.closest(".link");
      if (link) {
        ev.preventDefault();
        history.pushState(null, "", link.getAttribute("href"));
        window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
      }
    }
  };

// --- Dashboard ------------------------------------------------------------

export const buildView = (entry, hass) => {
  const t = text(hass);
  const collection = collectionKey(entry);
  const sections = [
    pickerSection(entry, t, collection),
    pickedChartsSection(entry, t, collection),
    monthChartsSection(entry, t),
    checksSection(entry, t, collection),
  ].filter(Boolean);
  return {
    title: entry.title,
    path: entry.site,
    type: "sections",
    max_columns: MAX_COLUMNS,
    sections,
  };
};

export const buildDashboard = (entries, config, hass) => {
  const t = text(hass);
  const picked =
    config && config.entry_id
      ? entries.filter((e) => e.entry_id === config.entry_id)
      : entries;
  if (!picked.length) {
    return {
      views: [
        {
          title: t.name,
          cards: [{ type: "markdown", content: t.none }],
        },
      ],
    };
  }
  return { views: picked.map((entry) => buildView(entry, hass)) };
};

export const generate = async (config, hass) => {
  const result = await hass.callWS({ type: "eg_austria/dashboard_config" });
  const dashboard = buildDashboard(result.entries || [], config, hass);
  for (const entry of result.entries || []) seedDefaultPeriod(collectionKey(entry));
  return dashboard;
};

// Browser only: register the strategy element, the "Add dashboard" entry
// and the two cards.
const TAG = `ll-strategy-dashboard-${STRATEGY_TYPE}`;
if (typeof customElements !== "undefined" && !customElements.get(TAG)) {
  class EgAustriaDashboardStrategy extends HTMLElement {
    static generate(config, hass) {
      return generate(config, hass);
    }

    // Pre-fills title and icon in the "Add dashboard" dialog.
    static getCreateSuggestions(hass) {
      return createSuggestions(hass);
    }
  }
  customElements.define(TAG, EgAustriaDashboardStrategy);
  window.customStrategies = window.customStrategies || [];
  window.customStrategies.push({
    type: STRATEGY_TYPE,
    strategyType: "dashboard",
    name: TEXT.en.name,
    description: TEXT.en.description,
    documentationURL: DOCS_URL,
  });
}
if (typeof customElements !== "undefined") {
  for (const [tag, render, ids, size, details] of [
    [PERIOD_TAG, renderPeriodCard, periodCardIds, 4, true],
    [COMMUNITY_TAG, renderCommunityCard, communityCardIds, 2, false],
  ]) {
    if (!customElements.get(tag)) {
      customElements.define(tag, pickerCard(HTMLElement, render, ids, size, details));
    }
  }
  if (!customElements.get(STATUS_TAG)) customElements.define(STATUS_TAG, statusElement(HTMLElement));
}
