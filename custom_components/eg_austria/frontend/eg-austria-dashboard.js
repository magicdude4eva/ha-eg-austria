// EG Austria (unofficial): "Energy community" dashboard strategy (#52, #61).
//
// Served by the integration and loaded on every frontend start. Registers
// `custom:eg-austria` for Settings -> Dashboards -> Add dashboard. On each
// load it asks `eg_austria/dashboard_config` which statistics, comparison
// sources and sensors exist, and builds core cards only from that: cards for
// sources that aren't set are left out. Read-only; nothing is written.
//
// No date picker: community data lags about two weeks, so a picker that
// starts on today shows empty cards. Totals use fixed periods (the last
// complete month and the month so far), charts fixed ranges.

const STRATEGY_TYPE = "eg-austria";
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
    months: [
      "January", "February", "March", "April", "May", "June",
      "July", "August", "September", "October", "November", "December",
    ],
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
    monthSoFar: "month so far",
    lastMonth: "last month",
    monthSoFarNote: "Month so far · last month {value}",
    lastMonthNote: "Last month · month so far not complete yet",
    feedInToCommunities: "Feed-in to communities",
    importFromCommunities: "Import covered by communities",
    pvSelfConsumed: "PV self-consumed",
    totalsIn: "Totals, {period}",
    totalsLastMonth: "Totals, last month",
    totalsMonthSoFar: "Totals, month so far",
    toCommunities: "Fed into communities",
    toSurplus: "To surplus buyer",
    fromCommunities: "Drawn from communities",
    fromSupplier: "Drawn from supplier",
    communities: "Communities",
    feedInShareMonthSoFar: "Feed-in share, month so far",
    feedInShareLastMonth: "Feed-in share, last month",
    importShareMonthSoFar: "Import share, month so far",
    importShareLastMonth: "Import share, last month",
    charts: "Feed-in and import",
    checks: "Your own sources",
    feedInSplit: "Where the feed-in went",
    importSplit: "Where the import came from",
    byMonth: "by month",
    byDay: "by day, 45 days",
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
  },
  de: {
    name: "EG Austria Energiegemeinschaft",
    title: "Energiegemeinschaft",
    description:
      "Gemeinschaftsanteile, Aufteilung von Einspeisung und Bezug und der Importstatus der EG-Austria-Integration.",
    none: "Kein EG-Austria-Eintrag ist geladen. Richte zuerst die Integration ein.",
    dateFormat: "%-d.%-m.%Y",
    months: [
      "Jänner", "Februar", "März", "April", "Mai", "Juni",
      "Juli", "August", "September", "Oktober", "November", "Dezember",
    ],
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
    monthSoFar: "Monat bisher",
    lastMonth: "Vormonat",
    monthSoFarNote: "Monat bisher · Vormonat {value}",
    lastMonthNote: "Vormonat · Monat bisher noch nicht vollständig",
    feedInToCommunities: "Einspeisung an Gemeinschaften",
    importFromCommunities: "Bezug durch Gemeinschaften gedeckt",
    pvSelfConsumed: "PV selbst verbraucht",
    totalsIn: "Summen, {period}",
    totalsLastMonth: "Summen, Vormonat",
    totalsMonthSoFar: "Summen, Monat bisher",
    toCommunities: "An Gemeinschaften eingespeist",
    toSurplus: "An Überschussabnehmer",
    fromCommunities: "Aus Gemeinschaften bezogen",
    fromSupplier: "Vom Lieferanten bezogen",
    communities: "Gemeinschaften",
    feedInShareMonthSoFar: "Einspeiseanteil, Monat bisher",
    feedInShareLastMonth: "Einspeiseanteil, Vormonat",
    importShareMonthSoFar: "Bezugsanteil, Monat bisher",
    importShareLastMonth: "Bezugsanteil, Vormonat",
    charts: "Einspeisung und Bezug",
    checks: "Deine eigenen Quellen",
    feedInSplit: "Wohin die Einspeisung ging",
    importSplit: "Woher der Bezug kam",
    byMonth: "pro Monat",
    byDay: "pro Tag, 45 Tage",
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
const QUARTER = { columns: 12 };
const FULL = { columns: "full" };
const UNKNOWN = ["unknown", "unavailable"];

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

// --- Share gauges and status ----------------------------------------------

const gauge = (entity, name, color) => ({
  type: "gauge",
  entity,
  name,
  min: 0,
  max: 100,
  segments: [{ from: 0, color }],
});

// A quarter-width stack: the month so far in the gauge, last month in a
// note under it. Early in a month the month so far has no complete day yet
// (unknown), so the gauge shows last month then instead of a warning.
const shareGauge = (entry, t, key, name, color) => {
  const soFar = entry.entities[`${key}_month_so_far`];
  const last = entry.entities[`${key}_last_month`];
  if (!soFar && !last) return null;
  if (!soFar || !last) {
    const label = soFar ? t.monthSoFar : t.lastMonth;
    return { ...gauge(soFar || last, `${name}, ${label}`, color), grid_options: QUARTER };
  }
  const value = `{{ states('${last}', with_unit=True) }}`;
  const note =
    `{% if is_number(states('${soFar}')) %}` +
    `${t.monthSoFarNote.replace("{value}", value)}` +
    `{% else %}${t.lastMonthNote}{% endif %}`;
  return {
    type: "vertical-stack",
    grid_options: QUARTER,
    cards: [
      {
        type: "conditional",
        conditions: [{ condition: "state", entity: soFar, state_not: UNKNOWN }],
        card: gauge(soFar, name, color),
      },
      {
        type: "conditional",
        conditions: [{ condition: "state", entity: soFar, state: UNKNOWN }],
        card: gauge(last, name, color),
      },
      { type: "markdown", text_only: true, content: note },
    ],
  };
};

const statusCard = (entry, t) => {
  const e = entry.entities;
  const rows = [
    ["data_complete_until", t.dataCompleteUntil],
    ["meter_data_until", t.meterDataUntil],
    ["last_run_status", t.lastRun],
    ["last_import", t.lastImport],
    ["token_expires", t.tokenExpires],
  ]
    .filter(([key]) => e[key])
    .map(([key, name]) => ({ entity: e[key], name }));
  rows.push({
    type: "weblink",
    name: t.energyDashboard,
    icon: "mdi:lightning-bolt",
    url: "/energy",
  });
  return { type: "entities", title: t.status, grid_options: QUARTER, entities: rows };
};

const overviewSection = (entry, t) =>
  section(
    [
      delayBanner(entry, t),
      shareGauge(
        entry,
        t,
        "feed_in_to_communities",
        t.feedInToCommunities,
        COLORS.communities[0]
      ),
      shareGauge(
        entry,
        t,
        "import_from_communities",
        t.importFromCommunities,
        COLORS.communities[1]
      ),
      shareGauge(entry, t, "pv_self_consumed", t.pvSelfConsumed, COLORS.pv),
      statusCard(entry, t),
    ].filter(Boolean)
  );

// --- Totals ---------------------------------------------------------------

const pad = (n) => String(n).padStart(2, "0");

// The latest calendar month whose days are all complete, from the state of
// *Data complete until* (YYYY-MM-DD), as in the summary sensors.
export const lastCompleteMonth = (hass, entity) => {
  const state = entity && hass && hass.states && hass.states[entity];
  const match = state && /^(\d{4})-(\d{2})-(\d{2})$/.exec(state.state);
  if (!match) return null;
  let year = Number(match[1]);
  let month = Number(match[2]);
  const day = Number(match[3]);
  if (day !== new Date(Date.UTC(year, month, 0)).getUTCDate()) {
    month -= 1;
    if (month === 0) [year, month] = [year - 1, 12];
  }
  const [nextYear, nextMonth] = month === 12 ? [year + 1, 1] : [year, month + 1];
  return {
    label: `${text(hass).months[month - 1]} ${year}`,
    // Naive times: the backend reads them in the HA time zone.
    period: {
      fixed_period: {
        start: `${year}-${pad(month)}-01T00:00:00`,
        end: `${nextYear}-${pad(nextMonth)}-01T00:00:00`,
      },
    },
  };
};

const TOTALS = [
  ["generation_to_community", "toCommunities"],
  ["generation_to_surplus", "toSurplus"],
  ["consumption_from_community", "fromCommunities"],
  ["consumption_from_supplier", "fromSupplier"],
];

// Half-width sections side by side; inside, four in a row on a wide screen
// and two per row on a phone.
const totalsSection = (entry, t, title, period) => {
  const s = entry.statistics;
  const cards = TOTALS.filter(([key]) => s[key]).map(([key, name]) => ({
    type: "statistic",
    entity: s[key],
    name: t[name],
    stat_type: "change",
    period,
    grid_options: { columns: 6 },
  }));
  return cards.length ? section([heading(title), ...cards], 2) : null;
};

const totalsSections = (entry, t, hass) => {
  const last = lastCompleteMonth(hass, entry.entities.data_complete_until);
  return [
    totalsSection(
      entry,
      t,
      last ? t.totalsIn.replace("{period}", last.label) : t.totalsLastMonth,
      last ? last.period : { calendar: { period: "month", offset: -1 } }
    ),
    totalsSection(entry, t, t.totalsMonthSoFar, {
      calendar: { period: "month", offset: 0 },
    }),
  ];
};

// --- Communities ----------------------------------------------------------

const COMMUNITY_ROWS = [
  ["feed_in_share_month_so_far", "feedInShareMonthSoFar"],
  ["feed_in_share_last_month", "feedInShareLastMonth"],
  ["import_share_month_so_far", "importShareMonthSoFar"],
  ["import_share_last_month", "importShareLastMonth"],
];

const communitiesSection = (entry, t) => {
  const e = entry.entities;
  const cards = entry.communities
    .map((c) => {
      const rows = COMMUNITY_ROWS.filter(([key]) => e[`${c.slug}_${key}`]).map(
        ([key, name]) => ({ entity: e[`${c.slug}_${key}`], name: t[name] })
      );
      return rows.length ? { type: "entities", title: c.name, entities: rows } : null;
    })
    .filter(Boolean);
  // Side by side: half each for two, a third for three, a quarter from four.
  const width = Math.max(12, Math.floor(48 / Math.max(cards.length, 1)));
  for (const card of cards) {
    card.grid_options = width >= 48 ? FULL : { columns: width };
  }
  return cards.length ? section([heading(t.communities), ...cards]) : null;
};

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
  grid_options: FULL,
  ...options,
});

const MONTHS = { period: "month", days_to_show: 365 };
const DAYS = { period: "day", days_to_show: 45 };

const splitSeries = (entry, quantity, rest, restName, restColor) =>
  series([
    ...entry.communities.map((c, i) => [
      (entry.community_statistics[c.slug] || {})[quantity],
      c.name,
      COLORS.communities[i % COLORS.communities.length],
    ]),
    [entry.statistics[rest], restName, restColor],
  ]);

const chartsSection = (entry, t) => {
  const feedIn = splitSeries(
    entry,
    "generation_to_community",
    "generation_to_surplus",
    t.surplusBuyer,
    COLORS.surplus
  );
  const imports = splitSeries(
    entry,
    "consumption_from_community",
    "consumption_from_supplier",
    t.supplier,
    COLORS.supplier
  );
  const cards = [];
  for (const [range, label] of [
    [MONTHS, t.byMonth],
    [DAYS, t.byDay],
  ]) {
    for (const [title, items] of [
      [t.feedInSplit, feedIn],
      [t.importSplit, imports],
    ]) {
      if (items.length) {
        cards.push(
          graph(`${title}, ${label}`, items, { chart_type: "bar-stack", ...range })
        );
      }
    }
  }
  return cards.length ? section([heading(t.charts), ...cards]) : null;
};

const checksSection = (entry, t) => {
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
          { chart_type: "bar", ...DAYS }
        )
      );
    }
  }
  return cards.length ? section([heading(t.checks), ...cards]) : null;
};

// --- Dashboard ------------------------------------------------------------

export const buildView = (entry, hass) => {
  const t = text(hass);
  const sections = [
    overviewSection(entry, t),
    ...totalsSections(entry, t, hass),
    communitiesSection(entry, t),
    chartsSection(entry, t),
    checksSection(entry, t),
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
  return buildDashboard(result.entries || [], config, hass);
};

// Browser only: register the strategy element and the "Add dashboard" entry.
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
