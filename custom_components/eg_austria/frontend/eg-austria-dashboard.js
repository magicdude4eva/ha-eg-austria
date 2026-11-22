// EG Austria (unofficial): "Energy community" dashboard strategy (issue #52).
//
// Served by the integration and loaded on every frontend start. Registers
// `custom:eg-austria` for Settings -> Dashboards -> Add dashboard. On each
// load it asks `eg_austria/dashboard_config` which statistics, comparison
// sources and sensors exist, and builds core cards only from that: cards for
// sources that aren't set are left out. Read-only; nothing is written.

const STRATEGY_TYPE = "eg-austria";
const DOCS_URL =
  "https://github.com/magicdude4eva/ha-eg-austria#energy-community-dashboard";

const TEXT = {
  en: {
    name: "EG Austria energy community",
    description:
      "Community shares, feed-in and import split, and the import status of the EG Austria integration.",
    none: "No EG Austria entry is loaded. Set up the integration first.",
    status: "Import status",
    delays:
      "Community shares are complete until **{community}**, meter values until **{meter}**. Days after the first date show feed-in and import without the community split yet.",
    shares: "Shares",
    monthSoFar: "month so far",
    lastMonth: "last month",
    feedInToCommunities: "Feed-in to communities",
    importFromCommunities: "Import from communities",
    pvSelfConsumed: "PV self-consumed",
    totals: "Totals in the selected period",
    toCommunities: "Fed into communities",
    toSurplus: "To surplus buyer",
    fromCommunities: "Drawn from communities",
    fromSupplier: "Drawn from supplier",
    feedInSplit: "Where the feed-in went",
    importSplit: "Where the import came from",
    byMonth: "by month",
    byDay: "by day",
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
    communities: "Communities",
    energyDashboard: "Energy dashboard",
  },
  de: {
    name: "EG Austria Energiegemeinschaft",
    description:
      "Gemeinschaftsanteile, Aufteilung von Einspeisung und Bezug und der Importstatus der EG-Austria-Integration.",
    none: "Kein EG-Austria-Eintrag ist geladen. Richte zuerst die Integration ein.",
    status: "Importstatus",
    delays:
      "Gemeinschaftsanteile sind vollständig bis **{community}**, Zählerwerte bis **{meter}**. Tage nach dem ersten Datum zeigen Einspeisung und Bezug noch ohne Gemeinschaftsaufteilung.",
    shares: "Anteile",
    monthSoFar: "Monat bisher",
    lastMonth: "Vormonat",
    feedInToCommunities: "Einspeisung an Gemeinschaften",
    importFromCommunities: "Bezug aus Gemeinschaften",
    pvSelfConsumed: "PV selbst verbraucht",
    totals: "Summen im gewählten Zeitraum",
    toCommunities: "An Gemeinschaften eingespeist",
    toSurplus: "An Überschussabnehmer",
    fromCommunities: "Aus Gemeinschaften bezogen",
    fromSupplier: "Vom Lieferanten bezogen",
    feedInSplit: "Wohin die Einspeisung ging",
    importSplit: "Woher der Bezug kam",
    byMonth: "pro Monat",
    byDay: "pro Tag",
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
    communities: "Gemeinschaften",
    energyDashboard: "Energie-Dashboard",
  },
};

const text = (hass) => {
  const lang = (hass && hass.locale && hass.locale.language) || "en";
  return TEXT[lang.split("-")[0]] || TEXT.en;
};

const heading = (title) => ({ type: "heading", heading: title });
const full = { columns: "full" };

// One statistics-graph series per existing statistic id.
const series = (items) =>
  items.filter(([id]) => Boolean(id)).map(([entity, name]) => ({ entity, name }));

const statistic = (entity, name, collection) => ({
  type: "statistic",
  entity,
  name,
  stat_type: "change",
  period: { calendar: { period: "month" } },
  energy_date_selection: true,
  collection_key: collection,
});

const graph = (title, entities, options) => ({
  type: "statistics-graph",
  title,
  entities,
  stat_types: ["change"],
  grid_options: full,
  ...options,
});

const statusSection = (entry, t) => {
  const e = entry.entities;
  const cards = [heading(t.status)];
  for (const key of [
    "data_complete_until",
    "meter_data_until",
    "last_run_status",
    "last_import",
  ]) {
    if (e[key]) cards.push({ type: "tile", entity: e[key] });
  }
  if (e.data_complete_until && e.meter_data_until) {
    cards.push({
      type: "markdown",
      grid_options: full,
      content: t.delays
        .replace("{community}", `{{ states('${e.data_complete_until}') }}`)
        .replace("{meter}", `{{ states('${e.meter_data_until}') }}`),
    });
  }
  cards.push({
    type: "button",
    name: t.energyDashboard,
    icon: "mdi:lightning-bolt",
    show_state: false,
    tap_action: { action: "navigate", navigation_path: "/energy" },
  });
  return { type: "grid", cards };
};

const sharesSection = (entry, t) => {
  const e = entry.entities;
  const cards = [heading(t.shares)];
  const values = [
    ["feed_in_to_communities", t.feedInToCommunities],
    ["import_from_communities", t.importFromCommunities],
    ["pv_self_consumed", t.pvSelfConsumed],
  ];
  for (const [key, name] of values) {
    for (const [period, label] of [
      ["month_so_far", t.monthSoFar],
      ["last_month", t.lastMonth],
    ]) {
      const entity = e[`${key}_${period}`];
      if (entity) {
        cards.push({
          type: "gauge",
          entity,
          name: `${name}, ${label}`,
          min: 0,
          max: 100,
        });
      }
    }
  }
  return cards.length > 1 ? { type: "grid", cards } : null;
};

const totalsSection = (entry, t, collection) => {
  const s = entry.statistics;
  const cards = [
    heading(t.totals),
    { type: "energy-date-selection", collection_key: collection, grid_options: full },
  ];
  for (const [key, name] of [
    ["generation_to_community", t.toCommunities],
    ["generation_to_surplus", t.toSurplus],
    ["consumption_from_community", t.fromCommunities],
    ["consumption_from_supplier", t.fromSupplier],
  ]) {
    if (s[key]) cards.push(statistic(s[key], name, collection));
  }
  return { type: "grid", cards };
};

const splitSeries = (entry, quantity, rest, restName) =>
  series([
    ...entry.communities.map((c) => [
      (entry.community_statistics[c.slug] || {})[quantity],
      c.name,
    ]),
    [entry.statistics[rest], restName],
  ]);

const chartsSection = (entry, t, collection) => {
  const s = entry.statistics;
  const src = entry.sources;
  const cards = [];
  const feedIn = splitSeries(
    entry,
    "generation_to_community",
    "generation_to_surplus",
    t.surplusBuyer
  );
  const imports = splitSeries(
    entry,
    "consumption_from_community",
    "consumption_from_supplier",
    t.supplier
  );
  if (feedIn.length) {
    cards.push(
      graph(`${t.feedInSplit}, ${t.byMonth}`, feedIn, {
        chart_type: "bar-stack",
        period: "month",
        days_to_show: 365,
      }),
      graph(`${t.feedInSplit}, ${t.byDay}`, feedIn, {
        chart_type: "bar-stack",
        energy_date_selection: true,
        collection_key: collection,
      })
    );
  }
  if (imports.length) {
    cards.push(
      graph(`${t.importSplit}, ${t.byMonth}`, imports, {
        chart_type: "bar-stack",
        period: "month",
        days_to_show: 365,
      }),
      graph(`${t.importSplit}, ${t.byDay}`, imports, {
        chart_type: "bar-stack",
        energy_date_selection: true,
        collection_key: collection,
      })
    );
  }
  if (src.pv) {
    cards.push(
      graph(
        t.pvComparison,
        series([
          [src.pv, t.pv],
          [s.generation, t.meteredFeedIn],
          [s.generation_to_community, t.toCommunities],
        ]),
        { chart_type: "bar", period: "month", days_to_show: 365 }
      )
    );
  }
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
            [metered, theirs],
            [source, mine],
          ]),
          { chart_type: "line", period: "day", days_to_show: 45 }
        )
      );
    }
  }
  return cards.length ? { type: "grid", column_span: 4, cards } : null;
};

const communitiesSection = (entry, t) => {
  const e = entry.entities;
  const cards = [heading(t.communities)];
  for (const c of entry.communities) {
    const rows = [
      `${c.slug}_feed_in_share_month_so_far`,
      `${c.slug}_feed_in_share_last_month`,
      `${c.slug}_import_share_month_so_far`,
      `${c.slug}_import_share_last_month`,
    ]
      .map((key) => e[key])
      .filter(Boolean);
    if (rows.length) cards.push({ type: "entities", title: c.name, entities: rows });
  }
  return cards.length > 1 ? { type: "grid", cards } : null;
};

export const buildView = (entry, hass) => {
  const t = text(hass);
  const collection = `energy_eg_austria_${entry.site}`;
  const sections = [
    statusSection(entry, t),
    sharesSection(entry, t),
    totalsSection(entry, t, collection),
    chartsSection(entry, t, collection),
    communitiesSection(entry, t),
  ].filter(Boolean);
  return {
    title: entry.title,
    path: entry.site,
    type: "sections",
    max_columns: 4,
    sections,
  };
};

export const buildDashboard = (entries, config, hass) => {
  const t = text(hass);
  const picked = config && config.entry_id
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
