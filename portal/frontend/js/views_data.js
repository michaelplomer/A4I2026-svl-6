// Data bodies for the Today, Decade, Equity and History views. Each has html() -> string and
// wire(root, opts) for its buttons. Lists and summaries come from analysis.js.
import { api } from "./api.js";
import { FEATURE_LABELS } from "./layers.js";
import {
  brooklynCells, todayList, decadeList, plantingSites, profile, byResidentQuantile, groupBy,
  meanAttributions, hotCut, atRisk, place, near, signed, pct, int, money,
} from "./analysis.js";

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// Lists currently on screen, by id, for the "Show on map" buttons.
const lists = {};

const mapButton = (id, label = "Show these on the map →") =>
  `<button type="button" class="link-btn" data-show-list="${id}">${label}</button>`;
const cellButton = (c) =>
  `<button type="button" class="link-btn small" data-cell="${c.cell_i},${c.cell_j}" title="Fly to this block">Map</button>`;
// Block name on one line, "near <address>" under it.
const blockCell = (c) =>
  `<td class="block"><div>${esc(place(c))}</div><div class="muted small">${esc(near(c))}</div></td>`;
const CONF = { measured: "Measured", near_data: "Near data", extrapolated: "Extrapolated" };

/** Shared wiring: "Show on map" for a whole list, "Map" for one block. */
function wireCommon(root, { onShowLocations, onFocusCell }) {
  const body = root.querySelector(".view-body");
  if (!body) return;
  body.addEventListener("click", (e) => {
    const all = e.target.closest("[data-show-list]");
    if (all) {
      const { cells, label, layer } = lists[all.dataset.showList];
      onShowLocations?.(cells.map((c, k) => ({
        cell_i: c.cell_i, cell_j: c.cell_j, rank: k + 1, label: c.neighborhood || "", layer,
      })), label);
      return;
    }
    const one = e.target.closest("[data-cell]");
    if (one) {
      const [i, j] = one.dataset.cell.split(",").map(Number);
      onFocusCell?.(i, j);
    }
  });
}

// ============================================================== Today: dispatch
const today = { base: null, n: 25 };

async function todayForecast() {
  try { return await api.forecast(); } catch { return null; }
}

function baseOptions(fc) {
  const opts = (fc?.days || []).map((d, k) => ({ value: `d${k}`, high: d.high_f, label: `${d.name}: NWS ${d.high_f}°F` }));
  for (const h of [95, 100, 102]) opts.push({ value: `s${h}`, high: h, label: `What-if: city high ${h}°F` });
  return opts;
}

async function todayInner() {
  const [cells, fc] = await Promise.all([brooklynCells(), todayForecast()]);
  const opts = baseOptions(fc);
  const hottest = (fc?.days || []).reduce((m, d) => Math.max(m, d.high_f), -Infinity);
  if (!today.base) {
    const k = (fc?.days || []).findIndex((d) => d.high_f === hottest);
    today.base = hottest >= 90 ? `d${k}` : "s100";
  }
  const base = opts.find((o) => o.value === today.base) || opts[opts.length - 2];
  const list = todayList(cells, today.n);
  lists.today = { cells: list, label: `Today: top ${list.length} blocks to check`, layer: "forecast_high_f" };

  const hi = (c) => base.high + c.offset_best_f;
  // Blocks running at least 1 F hotter than the city: a threshold that means something at any base.
  const hotter = cells.filter((c) => c.offset_best_f >= 1);
  const hotterOld = hotter.reduce((s, c) => s + (c.est_residents_65_plus || 0), 0);

  const districts = groupBy(list, "council_district", [])
    .sort((a, b) => b.r65 + b.noCar - (a.r65 + a.noCar));

  const strip = fc?.days?.length ? `
    <div class="fc-strip">${fc.days.map((d) => `
      <div class="fc-day ${d.high_f >= 90 ? "hot" : ""}"><div class="fc-name">${esc(d.name)}</div>
        <div class="fc-high">${d.high_f}°F</div><div class="fc-desc">${esc(d.short_forecast)}</div></div>`).join("")}
    </div>` : `<div class="flag warn">NWS forecast unavailable right now; what-if scenarios only.</div>`;

  return `
    <h3>Next 7 days in Brooklyn</h3>
    ${strip}
    ${fc && hottest < 90 ? `<div class="flag info">No day in the forecast reaches 90°F (hottest: ${hottest}°F).
      The ranking below doesn't depend on the day, so it is ready for the next heat wave; pick a
      what-if city high to see the block-level highs.</div>` : ""}

    <div class="controls">
      <label>City high <select id="today-base">${opts.map((o) =>
        `<option value="${o.value}" ${o.value === base.value ? "selected" : ""}>${esc(o.label)}</option>`).join("")}</select></label>
      <label>Blocks <select id="today-n">${[10, 25, 50].map((n) =>
        `<option ${n === today.n ? "selected" : ""}>${n}</option>`).join("")}</select></label>
      ${mapButton("today")}
    </div>

    <div class="stats">
      <div class="stat"><div class="stat-v">${int(hotter.length)}</div><div class="stat-l">blocks reaching ${base.high + 1}°F or more at a ${base.high}°F city high (1°F+ hotter than the city)</div></div>
      <div class="stat"><div class="stat-v">${int(hotterOld)}</div><div class="stat-l">residents 65+ living in them</div></div>
      <div class="stat"><div class="stat-v">${int(list.reduce((s, c) => s + atRisk(c), 0))}</div><div class="stat-l">people 65+ and car-free households on this list</div></div>
    </div>

    <h3>Where to send checks first</h3>
    <div class="table-scroll"><table class="data">
      <thead><tr><th>#</th><th>Block</th><th>Est. high</th><th>vs city</th>
        <th>Residents 65+</th><th>Households, no car</th><th>Confidence</th><th></th></tr></thead>
      <tbody>${list.map((c, k) => `<tr>
        <td>${k + 1}</td>${blockCell(c)}
        <td>${hi(c).toFixed(1)}°F</td><td>${signed(c.offset_best_f)}°F</td>
        <td>${int(c.est_residents_65_plus)}</td><td>${int(c.est_households_no_car)}</td>
        <td class="left">${CONF[c.confidence] || "—"}</td><td>${cellButton(c)}</td></tr>`).join("")}</tbody>
    </table></div>

    <h3>By council district</h3>
    <table class="data">
      <thead><tr><th>District</th><th>Blocks on the list</th><th>Residents 65+</th><th>Households, no car</th></tr></thead>
      <tbody>${districts.map((d) => `<tr><td>District ${esc(d.key)}</td><td>${d.blocks}</td>
        <td>${int(d.r65)}</td><td>${int(d.noCar)}</td></tr>`).join("")}</tbody>
    </table>
    <p class="muted small">Addresses are the nearest street tree in the 2015 census, so read them as "near".
      Council districts and neighbourhoods use the 2015 boundaries.</p>`;
}

const todayBody = {
  html: async () => `<div id="today-inner">${await todayInner()}</div>`,
  wire(root, opts) {
    wireCommon(root, opts);
    root.querySelector(".view-body").addEventListener("change", async (e) => {
      if (e.target.id === "today-base") today.base = e.target.value;
      else if (e.target.id === "today-n") today.n = Number(e.target.value);
      else return;
      root.querySelector("#today-inner").innerHTML = await todayInner();
    });
  },
};

// ============================================================== Decade: interventions
async function decadeHtml() {
  const [cells, model] = await Promise.all([brooklynCells(), api.model().catch(() => null)]);
  const list = decadeList(cells, 25);
  const plant = plantingSites(cells, 25);
  lists.decade = { cells: list, label: "Decade: planting priority", layer: "trees_100m" };
  lists.plant = { cells: plant.list, label: "Decade: dead trees and stumps in hot blocks", layer: "offset_best_f" };

  // The model's tree terms: per extra tree, and per cm of trunk diameter within 100 m.
  const w = model?.weights || {};
  const perTree = (w.trees_100m ?? 0) + (w.tree_dbh_sum_100m ?? 0) * 50;  // a mature ~50 cm tree
  const tenTrees = perTree * 10;

  const cut10 = hotCut(cells, 0.1);
  const hottest = cells.filter((c) => c.offset_best_f >= cut10);
  const attr = meanAttributions(hottest);
  const max = Math.max(...attr.map(([, v]) => Math.abs(v)), 0.01);

  const byDistrict = groupBy(plant.hot.filter((c) => (c.trees_dead || 0) + (c.stumps || 0) > 0),
    "council_district", ["trees_dead", "stumps"])
    .map((d) => ({ ...d, sites: d.blocks }));
  const siteCount = new Map();
  for (const c of plant.hot) {
    if (!c.council_district) continue;
    siteCount.set(c.council_district, (siteCount.get(c.council_district) || 0) + (c.trees_dead || 0) + (c.stumps || 0));
  }
  byDistrict.forEach((d) => { d.sites = siteCount.get(d.key) || 0; });
  byDistrict.sort((a, b) => b.sites - a.sites);

  return `
    <div class="stats">
      <div class="stat"><div class="stat-v">${int(plant.totalDead + plant.totalStumps)}</div>
        <div class="stat-l">dead trees and stumps in the hottest quarter of blocks (${int(plant.totalDead)} dead, ${int(plant.totalStumps)} stumps): ready planting sites</div></div>
      <div class="stat"><div class="stat-v">${model ? `${signed(tenTrees, 2)}°F` : "—"}</div>
        <div class="stat-l">the model's estimate for 10 more mature street trees within 100 m. Small, and an association, not a measured effect</div></div>
    </div>

    <h3>What makes the hottest blocks hot</h3>
    <p class="muted small">Mean contribution of each model input across the hottest 10% of blocks, in °F vs. an average block.</p>
    <div class="why wide">${attr.map(([f, v]) => `
      <div class="why-row"><span class="why-label">${esc(FEATURE_LABELS[f] ?? f)}</span>
        <span class="why-bar"><span class="${v >= 0 ? "pos" : "neg"}"
          style="width:${(Math.abs(v) / max) * 50}%;${v >= 0 ? "left:50%" : "right:50%"}"></span></span>
        <span class="why-val">${signed(v, 2)}</span></div>`).join("")}</div>
    <p class="muted small">"Nearby measured streets" dominates: hot blocks sit in hot neighbourhoods. Being away
      from the water adds a little (waterfront tracts run cooler). Trees matter, but the model can't separate them well from everything else about a street.</p>

    <h3>Planting priority: hot blocks with few street trees</h3>
    <div class="controls">${mapButton("decade")}</div>
    <div class="table-scroll"><table class="data">
      <thead><tr><th>#</th><th>Block</th><th>vs city</th><th>Trees within 100 m</th>
        <th>Residents</th><th>Residents 65+</th><th>Planting sites in block</th><th></th></tr></thead>
      <tbody>${list.map((c, k) => `<tr><td>${k + 1}</td>${blockCell(c)}<td>${signed(c.offset_best_f)}°F</td><td>${int(c.trees_100m)}</td>
        <td>${int(c.est_residents)}</td><td>${int(c.est_residents_65_plus)}</td>
        <td>${(c.trees_dead || 0) + (c.stumps || 0)}</td><td>${cellButton(c)}</td></tr>`).join("")}</tbody>
    </table></div>

    <h3>Ready planting sites: dead trees and stumps in hot blocks</h3>
    <div class="controls">${mapButton("plant")}</div>
    <div class="two-col">
      <div class="table-scroll"><table class="data">
        <thead><tr><th>#</th><th>Block</th><th>Dead</th><th>Stumps</th><th>vs city</th><th></th></tr></thead>
        <tbody>${plant.list.map((c, k) => `<tr><td>${k + 1}</td>${blockCell(c)}<td>${c.trees_dead || 0}</td><td>${c.stumps || 0}</td>
          <td>${signed(c.offset_best_f)}°F</td><td>${cellButton(c)}</td></tr>`).join("")}</tbody>
      </table></div>
      <div class="table-scroll"><table class="data">
        <thead><tr><th>District</th><th>Planting sites</th><th>Blocks</th></tr></thead>
        <tbody>${byDistrict.map((d) => `<tr><td>District ${esc(d.key)}</td><td>${d.sites}</td><td>${d.blocks}</td></tr>`).join("")}</tbody>
      </table></div>
    </div>
    <p class="muted small">Tree census from 2015: some sites will have been replanted since. Cool pavement and
      green roofs need surface data we don't have yet.</p>`;
}

// ============================================================== Equity audit
async function equityHtml() {
  const cells = await brooklynCells();
  const populated = cells.filter((c) => (c.est_residents || 0) > 0);
  const cut10 = hotCut(cells, 0.1);
  const hot10 = populated.filter((c) => c.offset_best_f >= cut10);
  const cols = [
    ["Brooklyn", profile(populated)],
    ["Hottest 10% of blocks", profile(hot10)],
    ["Today list (top 25)", profile(todayList(cells, 25))],
    ["Decade list (top 25)", profile(decadeList(cells, 25))],
  ];
  const rows = [
    ["Blocks", (p) => int(p.blocks)],
    ["Residents", (p) => int(p.residents)],
    ["Heat vs city (resident-weighted)", (p) => `${signed(p.offset, 2)}°F`],
    ["Aged 65+", (p) => pct(p.share65, 1)],
    ["Households without a car (tract rate)", (p) => pct(p.noCarRate, 1)],
    ["Poverty rate (tract)", (p) => pct(p.poverty, 1)],
    ["Median household income (block group)", (p) => money(p.income)],
  ];
  const pov = byResidentQuantile(cells, "tract_poverty_rate");
  const inc = byResidentQuantile(cells, "bg_median_income");
  const quint = (g, fmt) => g.map((q, k) => `<tr><td>${k + 1} · ${fmt(q.lo)}–${fmt(q.hi)}</td>
    <td>${int(q.residents)}</td><td>${signed(q.offset, 2)}°F</td><td>${pct(q.hotShare, 1)}</td></tr>`).join("");

  return `
    <h3>Who the recommendations reach</h3>
    <div class="table-scroll"><table class="data">
      <thead><tr><th>Measure</th>${cols.map(([h]) => `<th>${h}</th>`).join("")}</tr></thead>
      <tbody>${rows.map(([label, f]) => `<tr><td>${label}</td>${cols.map(([, p]) => `<td>${f(p)}</td>`).join("")}</tr>`).join("")}</tbody>
    </table></div>
    <p class="muted small">Read across: if the list columns show more older, poorer or car-free residents than Brooklyn
      as a whole, the lists reach the people who need them most.</p>

    <h3>Heat by neighbourhood poverty</h3>
    <p class="muted small">Brooklyn residents split into five equal groups by their tract's poverty rate.</p>
    <table class="data"><thead><tr><th>Group · poverty rate</th><th>Residents</th><th>Heat vs city</th><th>Living in hottest 10% of blocks</th></tr></thead>
      <tbody>${quint(pov, (v) => pct(v))}</tbody></table>

    <h3>Heat by income</h3>
    <p class="muted small">Five equal groups by block-group median household income.</p>
    <table class="data"><thead><tr><th>Group · median income</th><th>Residents</th><th>Heat vs city</th><th>Living in hottest 10% of blocks</th></tr></thead>
      <tbody>${quint(inc, money)}</tbody></table>
    <p class="muted small">If every group lived in equally hot blocks, each would have 10% of its residents in the hottest 10%.</p>`;
}

// ============================================================== Heat history
function historyChart(years) {
  const W = 560, H = 180, pad = 28, maxV = Math.max(...years.map((y) => y.days_90), 1);
  const bw = (W - pad * 2) / years.length;
  const y = (v) => H - pad - (v / maxV) * (H - pad * 2);
  return `<svg class="hist-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Days per year at or above 90 and 95 degrees">
    ${years.map((r, k) => {
      const x = pad + k * bw;
      return `<rect x="${x + bw * 0.12}" y="${y(r.days_90)}" width="${bw * 0.36}" height="${H - pad - y(r.days_90)}" class="b90"><title>${r.year}: ${r.days_90} days ≥ 90°F</title></rect>
        <rect x="${x + bw * 0.52}" y="${y(r.days_95)}" width="${bw * 0.36}" height="${H - pad - y(r.days_95)}" class="b95"><title>${r.year}: ${r.days_95} days ≥ 95°F</title></rect>
        <text x="${x + bw / 2}" y="${y(r.days_90) - 4}" class="v">${r.days_90}</text>
        <text x="${x + bw / 2}" y="${H - 10}" class="t">${r.year}</text>`;
    }).join("")}
    <line x1="${pad}" x2="${W - pad}" y1="${H - pad}" y2="${H - pad}" class="axis"/>
  </svg>
  <div class="chart-key"><span class="k90"></span> days ≥ 90°F <span class="k95"></span> days ≥ 95°F</div>`;
}

async function historyHtml() {
  const [{ station, years }, cells] = await Promise.all([api.heatDays(), brooklynCells()]);
  const fields = ["est_days_ge_90_per_year", "est_days_ge_95_per_year"];
  const districts = groupBy(cells, "council_district", fields).filter((g) => g.residents >= 1000)
    .sort((a, b) => b.mean.est_days_ge_95_per_year - a.mean.est_days_ge_95_per_year);
  const hoods = groupBy(cells, "neighborhood", fields).filter((g) => g.residents >= 5000)
    .sort((a, b) => b.mean.est_days_ge_95_per_year - a.mean.est_days_ge_95_per_year);
  const avg = (k) => years.reduce((s, y) => s + y[k], 0) / years.length;
  const groupRows = (gs, name) => gs.map((g) => `<tr><td>${name(g.key)}</td><td>${int(g.residents)}</td>
    <td>${g.mean.est_days_ge_90_per_year.toFixed(1)}</td><td>${g.mean.est_days_ge_95_per_year.toFixed(1)}</td>
    <td>${g.max.est_days_ge_95_per_year.toFixed(1)}</td></tr>`).join("");
  const head = (first) => `<thead><tr><th>${first}</th><th>Residents</th><th>Days ≥ 90°F / yr</th>
    <th>Days ≥ 95°F / yr</th><th>Hottest block, ≥ 95°F / yr</th></tr></thead>`;

  return `
    <h3>City-wide: days per year at or above each threshold</h3>
    <p class="muted small">${esc(station)}, 2019–2025. Average: ${avg("days_90").toFixed(0)} days ≥ 90°F and
      ${avg("days_95").toFixed(1)} days ≥ 95°F a year.</p>
    ${historyChart(years)}
    <table class="data">
      <thead><tr><th>Year</th><th>≥ 90°F</th><th>≥ 95°F</th><th>≥ 100°F</th><th>Peak</th></tr></thead>
      <tbody>${years.map((y) => `<tr><td>${y.year}</td><td>${y.days_90}</td><td>${y.days_95}</td>
        <td>${y.days_100}</td><td>${y.peak_f.toFixed(0)}°F</td></tr>`).join("")}</tbody>
    </table>

    <h3>By council district (estimated, per block, 2019–2025 average)</h3>
    <div class="table-scroll"><table class="data">${head("District")}
      <tbody>${groupRows(districts, (k) => `District ${esc(k)}`)}</tbody></table></div>

    <h3>By neighbourhood (5,000+ residents in the grid)</h3>
    <div class="table-scroll"><table class="data">${head("Neighbourhood")}
      <tbody>${groupRows(hoods, esc)}</tbody></table></div>
    <p class="muted small">Resident-weighted averages. Differences between districts are a day or two a year: the
      city high decides most of it, the street adds the rest.</p>`;
}

export const BODIES = {
  today: todayBody,
  decade: { html: decadeHtml, wire: wireCommon },
  equity: { html: equityHtml },
  history: { html: historyHtml },
};
