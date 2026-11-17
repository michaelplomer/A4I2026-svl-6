// Shared numbers for the Today, Decade, Equity and History views. Everything is computed in
// the browser from the cell snapshot (/api/cells), so the views need no live queries.
//
// The lists here are deliberately simple and explainable, so the Equity audit can check them:
//   Today  = the hottest quarter of populated blocks, ranked by people least able to cope
//            (residents 65+ plus households without a car).
//   Decade = hot blocks with few street trees, ranked by heat x residents; plus the dead trees
//            and stumps in hot blocks, which are ready planting sites.
import { api } from "./api.js";

let cached = null;

/** Brooklyn land cells that have a heat estimate, as plain objects. */
export async function brooklynCells() {
  if (cached) return cached;
  const d = await api.cells();
  const idx = Object.fromEntries(d.columns.map((c, k) => [c, k]));
  cached = d.rows
    .filter((r) => r[idx.borough] === "Brooklyn" && r[idx.is_land] && r[idx.offset_best_f] != null)
    .map((r) => Object.fromEntries(d.columns.map((c, k) => [c, r[k]])));
  return cached;
}

const num = (v) => (typeof v === "number" && Number.isFinite(v) ? v : 0);

export function quantile(values, p) {
  const v = values.filter((x) => typeof x === "number").sort((a, b) => a - b);
  return v.length ? v[Math.min(v.length - 1, Math.floor(p * v.length))] : null;
}

/** Offset at or above which a populated block is in the hottest `share` of blocks. */
export function hotCut(cells, share) {
  return quantile(cells.filter((c) => num(c.est_residents) > 0).map((c) => c.offset_best_f), 1 - share);
}

export const atRisk = (c) => num(c.est_residents_65_plus) + num(c.est_households_no_car);

// ---------------------------------------------------------------- the two recommendation lists
export function todayList(cells, n = 25) {
  const cut = hotCut(cells, 0.25);
  return cells
    .filter((c) => c.offset_best_f >= cut && num(c.est_residents) > 0)
    .sort((a, b) => atRisk(b) - atRisk(a) || b.offset_best_f - a.offset_best_f)
    .slice(0, n);
}

export function decadeList(cells, n = 25) {
  const cut = hotCut(cells, 0.25);
  const fewTrees = quantile(cells.map((c) => c.trees_100m), 0.5);
  return cells
    .filter((c) => c.offset_best_f >= cut && num(c.trees_100m) <= fewTrees && num(c.est_residents) >= 50)
    .sort((a, b) => b.offset_best_f * num(b.est_residents) - a.offset_best_f * num(a.est_residents))
    .slice(0, n);
}

/** Hot blocks with dead trees or stumps, most planting sites first. */
export function plantingSites(cells, n = 25) {
  const cut = hotCut(cells, 0.25);
  const hot = cells.filter((c) => c.offset_best_f >= cut);
  const sites = (c) => num(c.trees_dead) + num(c.stumps);
  return {
    hot,
    totalDead: hot.reduce((s, c) => s + num(c.trees_dead), 0),
    totalStumps: hot.reduce((s, c) => s + num(c.stumps), 0),
    list: hot.filter((c) => sites(c) > 0)
      .sort((a, b) => sites(b) - sites(a) || b.offset_best_f - a.offset_best_f).slice(0, n),
  };
}

// ---------------------------------------------------------------- summaries
/** Resident-weighted profile of a set of cells (for the equity comparison). */
export function profile(cells) {
  let res = 0, r65 = 0, noCar = 0, pov = 0, povW = 0, inc = 0, incW = 0, off = 0;
  for (const c of cells) {
    const w = num(c.est_residents);
    res += w;
    r65 += num(c.est_residents_65_plus);
    noCar += num(c.tract_no_vehicle_rate) * w;
    off += c.offset_best_f * w;
    if (typeof c.tract_poverty_rate === "number") { pov += c.tract_poverty_rate * w; povW += w; }
    if (typeof c.bg_median_income === "number") { inc += c.bg_median_income * w; incW += w; }
  }
  return {
    blocks: cells.length,
    residents: res,
    share65: res ? r65 / res : null,
    noCarRate: res ? noCar / res : null,
    poverty: povW ? pov / povW : null,
    income: incW ? inc / incW : null,
    offset: res ? off / res : null,
  };
}

/** Split residents into `k` equal groups by `key`; per group: range, residents, heat. */
export function byResidentQuantile(cells, key, k = 5, hotShare = 0.1) {
  const cut = hotCut(cells, hotShare);
  const pts = cells.filter((c) => typeof c[key] === "number" && num(c.est_residents) > 0)
    .sort((a, b) => a[key] - b[key]);
  const total = pts.reduce((s, c) => s + c.est_residents, 0);
  const groups = Array.from({ length: k }, () => ({ cells: [], residents: 0, hot: 0, off: 0 }));
  let acc = 0;
  for (const c of pts) {
    const g = groups[Math.min(k - 1, Math.floor((acc / total) * k))];
    acc += c.est_residents;
    g.cells.push(c); g.residents += c.est_residents; g.off += c.offset_best_f * c.est_residents;
    if (c.offset_best_f >= cut) g.hot += c.est_residents;
  }
  return groups.map((g) => ({
    lo: g.cells[0]?.[key], hi: g.cells[g.cells.length - 1]?.[key],
    residents: g.residents, offset: g.off / g.residents, hotShare: g.hot / g.residents,
  }));
}

/** Group cells by a key (council_district, neighborhood); resident-weighted means of `fields`. */
export function groupBy(cells, key, fields) {
  const m = new Map();
  for (const c of cells) {
    const k = c[key];
    if (k == null) continue;
    if (!m.has(k)) m.set(k, { key: k, blocks: 0, residents: 0, r65: 0, noCar: 0, sums: {}, max: {} });
    const g = m.get(k), w = num(c.est_residents);
    g.blocks += 1; g.residents += w; g.r65 += num(c.est_residents_65_plus);
    g.noCar += num(c.est_households_no_car);
    for (const f of fields) {
      g.sums[f] = (g.sums[f] || 0) + num(c[f]) * w;
      g.max[f] = Math.max(g.max[f] ?? -Infinity, num(c[f]));
    }
  }
  return [...m.values()].map((g) => ({
    ...g, mean: Object.fromEntries(fields.map((f) => [f, g.residents ? g.sums[f] / g.residents : null])),
  }));
}

/** Mean contribution of each model input across a set of cells, in degrees F. */
export function meanAttributions(cells) {
  const s = {};
  let n = 0;
  for (const c of cells) {
    if (!c.attributions) continue;
    n += 1;
    for (const [f, v] of c.attributions) s[f] = (s[f] || 0) + v;
  }
  return Object.entries(s).map(([f, v]) => [f, v / n]).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
}

// ---------------------------------------------------------------- formatting
export const place = (c) => [c.neighborhood, c.council_district ? `District ${c.council_district}` : null]
  .filter(Boolean).join(" · ") || "—";
export const near = (c) => (c.street_address ? `near ${titleCase(c.street_address)}` : "—");
export const signed = (v, d = 1) => (v == null ? "—" : `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(d)}`);
export const pct = (v, d = 0) => (v == null ? "—" : `${(v * 100).toFixed(d)}%`);
export const int = (v) => (v == null ? "—" : Math.round(v).toLocaleString());
export const money = (v) => (v == null ? "—" : `$${Math.round(v / 1000)}k`);

function titleCase(s) {
  return String(s).toLowerCase().replace(/\b([a-z])/g, (m) => m.toUpperCase())
    .replace(/\b(\d+)(St|Nd|Rd|Th)\b/g, (m, a, b) => a + b.toLowerCase());
}
