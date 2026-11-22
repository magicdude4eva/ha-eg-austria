// EG Austria (unofficial): "Energy community" dashboard strategy (#52, #61).
//
// Served by the integration and loaded on every frontend start. Registers
// `custom:eg-austria` for Settings -> Dashboards -> Add dashboard. On each
// load it asks `eg_austria/dashboard_config` which statistics, comparison
// sources and sensors exist, and builds core cards only from that: cards for
// sources that aren't set are left out. Read-only; nothing is written.
//
// Layout (#61): the delay banner, then a date picker that opens on this
// month. Two small cards of our own follow the picker: the period card
// (import, feed-in and PV donuts) and one card per community. The by-day
// split and the meter check follow it too; the 12-month charts don't. Charts
// sit in pairs on a wide screen, the import status at the end.

const STRATEGY_TYPE = "eg-austria";
const PERIOD_TAG = "eg-austria-period-card";
const COMMUNITY_TAG = "eg-austria-community-card";
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
  return { type: "entities", title: t.status, grid_options: HALF, entities: rows };
};

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
      i === cards.length - 1 && cards.length % 2 ? FULL : { columns: 24 },
  }));

const communityColor = (i) => COLORS.communities[i % COLORS.communities.length];

// Labels the cards of our own need; the strategy passes them in the config.
const cardText = (t) => ({ total: t.total, until: t.until, noData: t.noData });

const communityParts = (entry, quantity) =>
  entry.communities
    .map((c, i) => ({
      name: c.name,
      statistic: (entry.community_statistics[c.slug] || {})[quantity],
      color: communityColor(i),
      share: true,
    }))
    .filter((p) => p.statistic);

// Each donut: the parts that make up a total; `share` parts give the % in
// the sentence. PV self-consumed is PV production minus metered feed-in.
const donuts = (entry, t) => {
  const s = entry.statistics;
  const list = [
    {
      title: t.importTitle,
      say: t.importSay,
      parts: [
        ...communityParts(entry, "consumption_from_community"),
        s.consumption_from_supplier && {
          name: t.supplier,
          statistic: s.consumption_from_supplier,
          color: COLORS.supplier,
        },
      ],
    },
    {
      title: t.feedInTitle,
      say: t.feedInSay,
      parts: [
        ...communityParts(entry, "generation_to_community"),
        s.generation_to_surplus && {
          name: t.surplusBuyer,
          statistic: s.generation_to_surplus,
          color: COLORS.surplus,
        },
      ],
    },
  ];
  if (entry.sources.pv && s.generation) {
    list.push({
      title: t.pvTitle,
      say: t.pvSay,
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
    .map((d) => ({ ...d, parts: d.parts.filter(Boolean) }))
    .filter((d) => d.parts.length > 1 && d.parts.some((p) => p.share));
};

const periodCard = (entry, t, collection) => {
  const list = donuts(entry, t);
  return list.length
    ? {
        type: `custom:${PERIOD_TAG}`,
        collection_key: collection,
        complete_until: entry.entities.data_complete_until || null,
        donuts: list,
        text: cardText(t),
        grid_options: FULL,
      }
    : null;
};

// One small card per community (a meter can be in up to five): kWh fed in
// and drawn for the picked period, as a share of all feed-in and import.
const communityCards = (entry, t, collection) =>
  entry.communities
    .map((c, i) => {
      const stats = entry.community_statistics[c.slug] || {};
      const s = entry.statistics;
      const rows = [
        [stats.generation_to_community, t.fedIn, s.generation, t.ofFeedIn],
        [stats.consumption_from_community, t.drawn, s.consumption, t.ofImport],
      ]
        .filter(([statistic]) => statistic)
        .map(([statistic, name, total, of]) => ({ statistic, name, total: total || null, of }));
      return rows.length
        ? {
            type: `custom:${COMMUNITY_TAG}`,
            collection_key: collection,
            complete_until: entry.entities.data_complete_until || null,
            name: c.name,
            color: communityColor(i),
            rows,
            text: cardText(t),
          }
        : null;
    })
    .filter(Boolean);

const pickerSection = (entry, t, collection) =>
  section(
    [
      delayBanner(entry, t),
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

// "October 2026", "2026" or a date range; the year is always shown.
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

const partValue = (part, totals) => {
  const value = totals[part.statistic];
  if (value === null || value === undefined) return null;
  const minus = part.subtract ? totals[part.subtract] : 0;
  return Math.max(value - (minus || 0), 0);
};

const STYLE = `<style>
ha-card{padding:16px;display:flex;flex-direction:column;gap:14px;height:100%;box-sizing:border-box}
.head{display:flex;align-items:baseline;gap:6px 10px;flex-wrap:wrap}
h2{margin:0;font-size:1.2em;font-weight:500;color:var(--primary-text-color)}
.meta,.say,.empty,.of{color:var(--secondary-text-color);font-size:.875em}
.donuts{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:16px 24px}
.donut{display:flex;gap:14px;align-items:center;min-width:0}
svg{flex:none;width:120px;height:120px}
.text{display:flex;flex-direction:column;gap:4px;min-width:0;flex:1}
h3{margin:0;font-size:1em;font-weight:500}
.row{display:flex;align-items:center;gap:8px;font-variant-numeric:tabular-nums}
.dot{width:10px;height:10px;border-radius:3px;flex:none}
.value{margin-left:auto;padding-left:8px;font-weight:500;white-space:nowrap}
.name{min-width:0;overflow-wrap:anywhere}
.bar{display:flex;flex-direction:column;gap:4px}
.track{height:8px;border-radius:4px;background:var(--divider-color);overflow:hidden}
.track i{display:block;height:100%;border-radius:4px}
</style>`;

const donutSvg = (parts, total, lang, label) => {
  const r = 48;
  const c = 2 * Math.PI * r;
  let offset = 0;
  const rings = parts
    .map((p) => {
      const len = total > 0 ? (c * p.value) / total : 0;
      const ring = `<circle cx="60" cy="60" r="${r}" fill="none" stroke="${p.color}" stroke-width="16" stroke-dasharray="${len} ${c - len}" stroke-dashoffset="${-offset}" transform="rotate(-90 60 60)"></circle>`;
      offset += len;
      return ring;
    })
    .join("");
  return `<svg viewBox="0 0 120 120" role="img" aria-label="${escapeHtml(label)} ${kwh(lang, total)}"><circle cx="60" cy="60" r="${r}" fill="none" stroke="var(--divider-color)" stroke-width="16"></circle>${rings}<text x="60" y="56" text-anchor="middle" font-size="11" fill="var(--secondary-text-color)">${escapeHtml(label)}</text><text x="60" y="73" text-anchor="middle" font-size="13" font-weight="500" fill="var(--primary-text-color)">${kwh(lang, total)}</text></svg>`;
};

const header = (config, period, lang, name) => {
  const title = periodTitle(period.start, period.end, lang);
  const until = period.clipped
    ? config.text.until.replace(
        "{date}",
        new Date(period.until - 1).toLocaleDateString(lang, { day: "numeric", month: "short" })
      )
    : "";
  const meta = [name ? title : "", until].filter(Boolean).join(" · ");
  return `<div class="head"><h2>${escapeHtml(name || title)}</h2>${meta ? `<span class="meta">${escapeHtml(meta)}</span>` : ""}</div>`;
};

export const renderPeriodCard = (config, totals, period, lang) => {
  let body;
  if (period.empty) {
    body = `<div class="empty">${escapeHtml(config.text.noData)}</div>`;
  } else {
    body = `<div class="donuts">${config.donuts
      .map((d) => {
        const parts = d.parts
          .map((p) => ({ ...p, value: partValue(p, totals) }))
          .filter((p) => p.value !== null);
        const total = parts.reduce((sum, p) => sum + p.value, 0);
        const share = parts.filter((p) => p.share).reduce((sum, p) => sum + p.value, 0);
        const rows = parts
          .map(
            (p) =>
              `<div class="row"><span class="dot" style="background:${p.color}"></span><span class="name">${escapeHtml(p.name)}</span><span class="value">${kwh(lang, p.value)}</span></div>`
          )
          .join("");
        return `<div class="donut">${donutSvg(parts, total, lang, config.text.total)}<div class="text"><h3>${escapeHtml(d.title)}</h3><span class="say">${escapeHtml(d.say.replace("{pct}", percent(lang, share, total)))}</span>${rows}</div></div>`;
      })
      .join("")}</div>`;
  }
  return `${STYLE}<ha-card>${header(config, period, lang)}${body}</ha-card>`;
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
        return `<div class="bar"><div class="row"><span class="name">${escapeHtml(row.name)}</span><span class="value">${kwh(lang, value)}</span>${of ? `<span class="of">${escapeHtml(of)}</span>` : ""}</div><div class="track"><i style="width:${width.toFixed(1)}%;background:${config.color}"></i></div></div>`;
      })
      .join("");
  }
  return `${STYLE}<ha-card>${header(config, period, lang, config.name)}${body}</ha-card>`;
};

export const periodCardIds = (config) =>
  config.donuts.flatMap((d) => d.parts.flatMap((p) => [p.statistic, p.subtract].filter(Boolean)));
export const communityCardIds = (config) =>
  config.rows.flatMap((r) => [r.statistic, r.total].filter(Boolean));

// The element: finds the picker's collection (it may appear a moment after
// the card), follows its period and renders when period or cutoff change.
export const pickerCard = (Base, render, ids, size) =>
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
      fetchTotals(this._hass, ids(this._config), period.start, period.until).then((totals) => {
        if (this._key === key) this._show(render(this._config, totals, period, lang));
      });
    }

    _show(html) {
      if (!this.shadowRoot) this.attachShadow({ mode: "open" });
      this.shadowRoot.innerHTML = html;
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
    section([statusCard(entry, t)]),
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
  for (const [tag, render, ids, size] of [
    [PERIOD_TAG, renderPeriodCard, periodCardIds, 4],
    [COMMUNITY_TAG, renderCommunityCard, communityCardIds, 2],
  ]) {
    if (!customElements.get(tag)) {
      customElements.define(tag, pickerCard(HTMLElement, render, ids, size));
    }
  }
}
