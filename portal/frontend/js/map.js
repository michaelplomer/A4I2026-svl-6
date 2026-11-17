// Map view: grid cells and tracts over a public-domain basemap, a layer picker,
// overlays, a legend and a click-to-inspect panel.
import { api } from "./api.js";
import { LAYER_GROUPS, OVERLAYS, BASEMAPS, FEATURE_LABELS, findLayer } from "./layers.js";

const TRANSPARENT = "rgba(0,0,0,0)";
const state = { map: null, layer: "offset_best_f", overlays: {}, basemap: "topo", cells: null, tracts: null,
  tractById: new Map(), domains: {}, onAsk: null, model: null,
  // Base high for the forecast layer: an NWS daytime period, or a what-if scenario.
  forecast: null, forecastError: null, baseIdx: "scenario", scenarioHigh: 102, selected: null,
  cellDeg: 0.001 };
const signed = (v, d = 1) => `${v > 0 ? "+" : ""}${v.toFixed(d)}°F`;

function baseHigh() {
  if (state.baseIdx === "scenario" || !state.forecast) {
    return { high: state.scenarioHigh, label: `scenario (${state.scenarioHigh}°F)`, scenario: true };
  }
  const d = state.forecast.days[state.baseIdx];
  return { high: d.high_f, label: `${d.name} (NWS ${d.high_f}°F)`, scenario: false };
}

// Value expression shared by colouring and the hover popup: base high + the block's offset.
const blockHigh = (p) => (typeof p.offset_best_f === "number" ? baseHigh().high + p.offset_best_f : null);

// ------------------------------------------------------------------ data → GeoJSON
function cellsToGeoJSON({ cell_deg: d, columns, rows }) {
  const features = rows.map((row, idx) => {
    const p = {};
    columns.forEach((c, k) => { p[c] = row[k]; });
    const x0 = p.cell_j * d, y0 = p.cell_i * d, x1 = x0 + d, y1 = y0 + d;
    return {
      type: "Feature", id: idx, properties: p,
      geometry: { type: "Polygon", coordinates: [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]] },
    };
  });
  return { type: "FeatureCollection", features };
}

function percentile(sorted, q) {
  if (!sorted.length) return null;
  const pos = (sorted.length - 1) * q, lo = Math.floor(pos), hi = Math.ceil(pos);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
}

function dataDomain(layer) {
  if (layer.domain) return layer.domain;
  if (state.domains[layer.id]) return state.domains[layer.id];
  const feats = layer.source === "tract" ? state.tracts.features
    : state.cells.features.filter((f) => f.properties.is_land);
  const vals = feats.map((f) => f.properties[layer.id]).filter((v) => typeof v === "number").sort((a, b) => a - b);
  let lo = percentile(vals, 0.02) ?? 0, hi = percentile(vals, 0.98) ?? 1;
  if (hi <= lo) hi = lo + 1;
  return (state.domains[layer.id] = [lo, hi]);
}

// Evenly spread the palette over the domain; a 3-value domain is diverging around its middle.
function stopValues(domain, n) {
  return Array.from({ length: n }, (_, i) => {
    const t = i / (n - 1);
    if (domain.length === 3) {
      const [lo, mid, hi] = domain;
      return t <= 0.5 ? lo + (t / 0.5) * (mid - lo) : mid + ((t - 0.5) / 0.5) * (hi - mid);
    }
    return domain[0] + t * (domain[1] - domain[0]);
  });
}

function colorExpr(layer) {
  if (layer.derived === "forecast") {
    const value = ["+", baseHigh().high, ["get", "offset_best_f"]];
    const [first, ...rest] = layer.bands;
    return ["case",
      ["==", ["typeof", ["get", "offset_best_f"]], "number"],
      ["step", value, first.color, ...rest.flatMap((b) => [b.min, b.color])],
      TRANSPARENT];
  }
  if (layer.categories) {
    return ["match", ["get", layer.id], ...Object.entries(layer.categories).flat(), TRANSPARENT];
  }
  const domain = dataDomain(layer);
  const values = stopValues(domain, layer.palette.length);
  const stops = values.flatMap((v, i) => [v, layer.palette[i]]);
  return ["case",
    ["==", ["typeof", ["get", layer.id]], "number"],
    ["interpolate", ["linear"], ["get", layer.id], ...stops],
    TRANSPARENT];
}

// ------------------------------------------------------------------ map setup
function baseStyle() {
  const sources = {}, layers = [{ id: "bg", type: "background", paint: { "background-color": "#f1f3f5" } }];
  for (const b of BASEMAPS.filter((b) => b.url)) {
    sources[`base-${b.id}`] = { type: "raster", tiles: [b.url], tileSize: 256, maxzoom: 16,
      attribution: "Basemap: USGS The National Map" };
    // Faint and grey, so the data colours carry the map.
    layers.push({ id: `base-${b.id}`, type: "raster", source: `base-${b.id}`,
      layout: { visibility: b.id === state.basemap ? "visible" : "none" },
      paint: { "raster-saturation": -0.85, "raster-opacity": 0.48 } });
  }
  return { version: 8, sources, layers };
}

// Layers whose values are actual Heat Watch measurements get a dot on each measured block, so
// the measured streets stand out from the surrounding cells.
const MEASURED_LAYERS = ["measured_offset_f", "heat_index_offset_f"];

// Which cells to outline for a layer: those that have a value for it.
function hasValueFilter(layer) {
  if (layer.derived === "forecast") return ["==", ["typeof", ["get", "offset_best_f"]], "number"];
  if (layer.categories) return ["in", ["get", layer.id], ["literal", Object.keys(layer.categories)]];
  return ["==", ["typeof", ["get", layer.id]], "number"];
}

// Numbered marker for ranked results (1 = top recommendation), drawn once per rank.
function rankedMarkerImage(rank) {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 48;
  const ctx = canvas.getContext("2d");
  ctx.beginPath();
  ctx.arc(24, 24, 20, 0, Math.PI * 2);
  ctx.fillStyle = "#facc15";
  ctx.fill();
  ctx.lineWidth = 4;
  ctx.strokeStyle = "#78350f";
  ctx.stroke();
  ctx.fillStyle = "#422006";
  ctx.font = `bold ${rank > 9 ? 18 : 22}px sans-serif`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(String(rank), 24, 25);
  return ctx.getImageData(0, 0, 48, 48);
}
const MAX_RANKED = 20;

function addDataLayers(map) {
  const d = state.cellDeg;
  map.addSource("cells", { type: "geojson", data: state.cells,
    attribution: "Heat: NOAA Heat Watch · Trees: NYC 2015 Street Tree Census · People: ACS 2018 (block groups), 2019 (tracts)" });
  map.addSource("heat-points", { type: "geojson", data: {
    type: "FeatureCollection",
    features: state.cells.features
      .filter((f) => MEASURED_LAYERS.some((k) => typeof f.properties[k] === "number"))
      .map((f) => ({ type: "Feature", id: f.id, properties: f.properties,
        geometry: { type: "Point", coordinates: [(f.properties.cell_j + 0.5) * d, (f.properties.cell_i + 0.5) * d] } })),
  } });
  map.addSource("tracts", { type: "geojson", data: state.tracts });
  map.addSource("selected", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  map.addSource("selected-marker", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  // Ranked results get their own source, so clicking through them doesn't erase the numbers.
  map.addSource("ranked-markers", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  for (let rank = 1; rank <= MAX_RANKED; rank += 1) {
    map.addImage(`ranked-marker-${rank}`, rankedMarkerImage(rank), { pixelRatio: 2 });
  }

  map.addLayer({ id: "tract-fill", type: "fill", source: "tracts",
    paint: { "fill-color": TRANSPARENT, "fill-opacity": 0.7 } });
  map.addLayer({ id: "cells-fill", type: "fill", source: "cells",
    paint: { "fill-color": TRANSPARENT, "fill-opacity": 0.9 } });
  map.addLayer({ id: "cells-outline", type: "line", source: "cells",
    filter: hasValueFilter(findLayer(state.layer)),
    paint: {
      "line-color": "#17212b",
      "line-width": ["interpolate", ["linear"], ["zoom"], 9, 0.15, 12, 0.5, 15, 1],
      "line-opacity": 0.35,
    } });
  map.addLayer({ id: "heat-points", type: "circle", source: "heat-points",
    layout: { visibility: "none" },
    paint: {
      "circle-color": TRANSPARENT,
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 3.5, 12, 5.5, 15, 7.5],
      "circle-stroke-color": "#ffffff",
      "circle-stroke-width": 1.25,
      "circle-opacity": 0.98,
    } });
  map.addLayer({ id: "out-of-range", type: "fill", source: "cells",
    filter: ["==", ["get", "confidence"], "extrapolated"],
    paint: { "fill-color": "#6b7280", "fill-opacity": 0.55 } });
  map.addLayer({ id: "driven-outline", type: "line", source: "cells",
    filter: ["==", ["get", "is_driven"], true],
    paint: { "line-color": "#111827", "line-width": 0.6, "line-opacity": 0.7 } });
  map.addLayer({ id: "holdout", type: "line", source: "cells",
    filter: ["==", ["get", "holdout"], true],
    paint: { "line-color": "#c026d3", "line-width": 1.6 } });
  map.addLayer({ id: "tract-lines", type: "line", source: "tracts",
    paint: { "line-color": "#374151", "line-width": 0.6, "line-opacity": 0.45 } });
  map.addLayer({ id: "selected-fill", type: "fill", source: "selected",
    paint: { "fill-color": "#f59e0b", "fill-opacity": 0.28 } });
  map.addLayer({ id: "selected-line", type: "line", source: "selected",
    paint: { "line-color": "#b45309", "line-width": 2.4 } });
  // Blocks the agent outlines (highlight_cells_on_map).
  map.addSource("highlights", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  map.addLayer({ id: "highlights-fill", type: "fill", source: "highlights",
    paint: { "fill-color": "#22d3ee", "fill-opacity": 0.25 } });
  map.addLayer({ id: "highlights-line", type: "line", source: "highlights",
    paint: { "line-color": "#0891b2", "line-width": 2.5 } });
  // Markers on top: a halo for the selected block, numbered pins for ranked results.
  map.addLayer({ id: "selected-marker-halo", type: "circle", source: "selected-marker",
    paint: { "circle-radius": 20, "circle-color": "#f59e0b", "circle-opacity": 0.3 } });
  map.addLayer({ id: "ranked-marker-halo", type: "circle", source: "ranked-markers",
    paint: { "circle-radius": 20, "circle-color": "#f59e0b", "circle-opacity": 0.3 } });
  map.addLayer({ id: "ranked-marker-rank", type: "symbol", source: "ranked-markers",
    layout: {
      "icon-image": ["concat", "ranked-marker-", ["to-string", ["get", "rank"]]],
      "icon-allow-overlap": true,
      "icon-ignore-placement": true,
    } });
}

// The apply* functions only touch the map once its style and data layers exist. Before
// that, controls just update `state`, and the map's load handler applies it all at once.
function applyLayer() {
  const { map } = state, layer = findLayer(state.layer);
  if (!state.ready) return;
  const isCell = layer.source === "cell";
  map.setPaintProperty("cells-fill", "fill-color", isCell ? colorExpr(layer) : TRANSPARENT);
  map.setPaintProperty("tract-fill", "fill-color", isCell ? TRANSPARENT : colorExpr(layer));
  map.setLayoutProperty("cells-outline", "visibility", isCell ? "visible" : "none");
  if (isCell) map.setFilter("cells-outline", hasValueFilter(layer));
  const measured = MEASURED_LAYERS.includes(layer.id);
  map.setLayoutProperty("heat-points", "visibility", measured ? "visible" : "none");
  if (measured) {
    map.setFilter("heat-points", ["==", ["typeof", ["get", layer.id]], "number"]);
    map.setPaintProperty("heat-points", "circle-color", colorExpr(layer));
  }
  renderLegend(layer);
}

function applyOverlays() {
  const { map, overlays } = state;
  if (!state.ready) return;
  for (const id of ["tract-lines", "driven-outline", "out-of-range", "holdout"]) {
    map.setLayoutProperty(id, "visibility", overlays[id] ? "visible" : "none");
  }
  // Cells that are water / non-residential stay hidden unless asked for.
  map.setFilter("cells-fill", overlays["non-land"] ? null : ["==", ["get", "is_land"], true]);
}

function applyBasemap() {
  if (!state.ready) return;
  for (const b of BASEMAPS.filter((b) => b.url)) {
    state.map.setLayoutProperty(`base-${b.id}`, "visibility", b.id === state.basemap ? "visible" : "none");
  }
}

// ------------------------------------------------------------------ controls
function renderLayerPicker() {
  const el = document.getElementById("layer-picker");
  if (!el) return;
  el.innerHTML = LAYER_GROUPS.map((g) => `
    <div class="layer-group-label">${g.label}</div>
    ${g.layers.map((l) => {
      const disabled = l.status && l.status !== "ready";
      return `<label class="opt ${disabled ? "disabled" : ""}">
        <input type="radio" name="layer" value="${l.id}" ${l.id === state.layer ? "checked" : ""} ${disabled ? "disabled" : ""}>
        <span><span class="opt-label">${l.label}</span>
        ${disabled ? `<span class="opt-note">${l.note}</span>` : ""}</span>
      </label>`;
    }).join("")}`).join("");
  el.addEventListener("change", (e) => { state.layer = e.target.value; applyLayer(); });
}

function renderOverlayPicker() {
  const el = document.getElementById("overlay-picker");
  if (!el) return;
  el.innerHTML = OVERLAYS.map((o) => `
    <label class="opt"><input type="checkbox" value="${o.id}" ${state.overlays[o.id] ? "checked" : ""}>
      <span class="opt-label">${o.label}</span></label>`).join("");
  el.addEventListener("change", (e) => { state.overlays[e.target.value] = e.target.checked; applyOverlays(); });
}

function renderBasemapPicker() {
  const el = document.getElementById("basemap-picker");
  if (!el) return;
  const draw = () => {
    el.innerHTML = BASEMAPS.map((b) =>
      `<button type="button" data-id="${b.id}" class="${b.id === state.basemap ? "on" : ""}">${b.label}</button>`).join("");
  };
  draw();
  el.addEventListener("click", (e) => {
    const id = e.target.dataset.id;
    if (!id) return;
    state.basemap = id; applyBasemap(); draw();
  });
}

function modelCard() {
  const m = state.model;
  if (!m) return "";
  return `<div class="legend-note model-card">Model <b>${m.model}</b>: typical error ${m.cv.mae_f.toFixed(2)}°F
    vs ${m.cv.baseline_mae_f.toFixed(2)}°F for "every block is average" (5-fold block cross-validation,
    R² ${m.cv.r2_mean.toFixed(2)}). Strongest input: nearby measured streets.</div>`;
}

// Exposure on the chosen day: blocks and people at or above each threshold. Extrapolated blocks
// are left out -- the model can't tell them apart from "average".
function exposureSummary() {
  const base = baseHigh().high;
  // 105 matters on a 102°F day: offsets span only about ±4°F, so 95 and 100 catch nearly everything.
  const rows = [95, 100, 105].map((t) => {
    let blocks = 0, res65 = 0, noCar = 0;
    for (const f of state.cells.features) {
      const p = f.properties;
      if (!p.is_land || p.confidence === "extrapolated" || typeof p.offset_best_f !== "number") continue;
      if (base + p.offset_best_f >= t) {
        blocks += 1; res65 += p.est_residents_65_plus || 0; noCar += p.est_households_no_car || 0;
      }
    }
    return { t, blocks, res65, noCar };
  });
  return `<table class="exposure">
    <tr><th></th><th>blocks</th><th>65+</th><th>no car</th></tr>
    ${rows.map((r) => `<tr><td>≥ ${r.t}°F</td><td>${r.blocks.toLocaleString()}</td>
      <td>${Math.round(r.res65).toLocaleString()}</td><td>${Math.round(r.noCar).toLocaleString()}</td></tr>`).join("")}
  </table>
  <div class="legend-note">Measured and predicted blocks only; 65+ = estimated residents, no car = estimated households.</div>`;
}

function renderForecastLegend(el, layer) {
  const days = state.forecast?.days ?? [];
  const b = baseHigh();
  el.innerHTML = `
    <div class="legend-title">${layer.label}</div>
    <label class="base-pick">Day
      <select id="base-select">
        ${days.map((d, i) => `<option value="${i}" ${String(state.baseIdx) === String(i) ? "selected" : ""}>
          ${d.name}: ${d.high_f}°F, ${d.short_forecast}</option>`).join("")}
        <option value="scenario" ${state.baseIdx === "scenario" ? "selected" : ""}>What-if scenario…</option>
      </select>
    </label>
    ${b.scenario ? `<label class="base-pick">City high
      <input id="scenario-input" type="number" min="60" max="115" step="1" value="${state.scenarioHigh}"> °F</label>` : ""}
    ${state.forecastError ? `<div class="flag warn">NWS forecast unavailable (${state.forecastError}); scenario only.</div>` : ""}
    ${layer.bands.map((x) => `<div class="legend-cat"><span class="swatch" style="background:${x.color}"></span>${x.label}</div>`).join("")}
    ${exposureSummary()}
    <div class="legend-note">${layer.note}</div>
    <div class="legend-note">${b.scenario ? "Scenario: a what-if city high, not a forecast."
      : `Forecast: ${state.forecast.source}, grid ${state.forecast.office} ${state.forecast.grid_x},${state.forecast.grid_y}, issued ${new Date(state.forecast.generated_at).toLocaleString()}.`}</div>`;
}

function renderLegend(layer) {
  const el = document.getElementById("legend");
  el.classList.toggle("legend-wide", Boolean(layer.derived || layer.categories));
  if (layer.derived === "forecast") { renderForecastLegend(el, layer); return; }
  if (layer.categories) {
    el.innerHTML = `
      <div class="legend-title">${layer.label}</div>
      ${Object.entries(layer.categories).map(([k, c]) => `
        <div class="legend-cat"><span class="swatch" style="background:${c}"></span>${layer.categoryLabels?.[k] ?? k}</div>`).join("")}
      ${layer.note ? `<div class="legend-note">${layer.note}</div>` : ""}
      ${modelCard()}`;
    return;
  }
  // Continuous layers: a compact bar, with the explanation folded away under "About this layer".
  const domain = dataDomain(layer);
  const fmt = layer.format || ((v) => v.toFixed(1));
  const ticks = domain.length === 3 ? domain : [domain[0], (domain[0] + domain[1]) / 2, domain[1]];
  // Diverging heat differences read better as cooler / average / warmer than as bare numbers.
  const tickLabels = layer.diverging
    ? [`${fmt(ticks[0])} cooler`, "avg", `${fmt(ticks[2])} warmer`]
    : ticks.map(fmt);
  const notes = [layer.note, layer.domain ? "" : "Scale: 2nd–98th percentile across Brooklyn."].filter(Boolean);
  el.innerHTML = `
    <div class="legend-title">${layer.label}${layer.unit ? ` <span class="muted">(${layer.unit})</span>` : ""}</div>
    <div class="legend-bar" style="background:linear-gradient(to right, ${layer.palette.join(",")})"></div>
    <div class="legend-ticks">${tickLabels.map((t) => `<span>${t}</span>`).join("")}</div>
    ${notes.length || layer.model ? `<details class="legend-info"><summary>About this layer</summary>
      ${notes.map((n) => `<div>${n}</div>`).join("")}${layer.model ? modelCard() : ""}</details>` : ""}`;
}

// ------------------------------------------------------------------ inspector
const fmtPct = (v) => (typeof v === "number" ? `${(v * 100).toFixed(0)}%` : "—");
const fmtNum = (v, d = 0) => (typeof v === "number" ? v.toLocaleString(undefined, { maximumFractionDigits: d }) : "—");

// "Why": each model input's contribution in °F relative to an average block, largest first.
function renderWhy(p) {
  const attr = p.attributions;
  if (!Array.isArray(attr) || !attr.length) return "";
  const max = Math.max(...attr.map(([, v]) => Math.abs(v)), 0.01);
  return `
    <h3>Why <span class="muted">(model, vs. an average block)</span></h3>
    <div class="why">
      ${attr.map(([f, v]) => `
        <div class="why-row" title="${f}">
          <span class="why-label">${FEATURE_LABELS[f] ?? f}</span>
          <span class="why-bar"><span class="${v >= 0 ? "pos" : "neg"}"
            style="width:${(Math.abs(v) / max) * 50}%;${v >= 0 ? "left:50%" : `right:50%`}"></span></span>
          <span class="why-val">${signed(v, 2)}</span>
        </div>`).join("")}
    </div>
    <div class="muted why-note">Associations learned from one afternoon, not proven causes.
      "Nearby measured streets" means this block inherits the pattern of measured blocks around it.</div>`;
}

const CONFIDENCE_FLAG = {
  measured: `<div class="flag ok">Measured: a Heat Watch car drove here.</div>`,
  near_data: `<div class="flag info">Predicted by the model from measured streets within 1 km. Typical error ≈ 0.8°F.</div>`,
  extrapolated: `<div class="flag warn">Extrapolated: nothing measured within 1 km. The model barely beats "average" here; treat the number as unknown.</div>`,
};

function renderInspector(p) {
  const el = document.getElementById("inspector");
  if (!el) return;
  const t = state.tractById.get(p.tract_geo_id) || {};
  const lat = (p.cell_i + 0.5) * 0.001, lon = (p.cell_j + 0.5) * 0.001;
  const num = (v) => typeof v === "number";
  el.innerHTML = `
    <h2>Block details</h2>
    <div class="muted">Cell ${p.cell_i}, ${p.cell_j} · ${lat.toFixed(4)}, ${lon.toFixed(4)}</div>
    ${CONFIDENCE_FLAG[p.confidence] ?? `<div class="flag warn">Water or non-residential cell: not modelled.</div>`}
    <h3>Heat</h3>
    <dl>
      <dt>Heat offset</dt><dd><b>${num(p.offset_best_f) ? signed(p.offset_best_f, 2) : "—"}</b></dd>
      <dt>Measured</dt><dd>${num(p.measured_offset_f) ? `${signed(p.measured_offset_f, 2)} <span class="muted">(${fmtNum(p.n_readings)} readings)</span>` : "—"}</dd>
      <dt>Model prediction</dt><dd>${num(p.predicted_offset_f) ? signed(p.predicted_offset_f, 2) : "—"}</dd>
      ${num(p.heat_index_offset_f) ? `<dt>Heat-index offset</dt><dd>${signed(p.heat_index_offset_f, 2)}</dd>` : ""}
      ${p.holdout ? `<dt>Test set</dt><dd>held out (model never saw it)</dd>` : ""}
      <dt>Est. days ≥ 95°F / yr</dt><dd>${fmtNum(p.est_days_ge_95_per_year, 1)}</dd>
      <dt>Est. days ≥ 100°F / yr</dt><dd>${fmtNum(p.est_days_ge_100_per_year, 1)}</dd>
      <dt>Est. hottest day 2019–25</dt><dd>${num(p.est_peak_f) ? `${p.est_peak_f.toFixed(0)}°F` : "—"}</dd>
      ${num(p.offset_best_f) ? `<dt>High, ${baseHigh().label}</dt>
        <dd><b>${blockHigh(p).toFixed(0)}°F</b></dd>` : ""}
    </dl>
    ${renderWhy(p)}
    <h3>Built environment</h3>
    <dl>
      <dt>Street trees ≤100 m</dt><dd>${fmtNum(p.trees_100m)}</dd>
      <dt>Trunk diameter ≤100 m</dt><dd>${fmtNum(p.tree_dbh_sum_100m)} in</dd>
      <dt>Tract water share</dt><dd>${fmtPct(p.tract_water_share)}</dd>
      <dt>Housing density</dt><dd>${fmtNum(p.bg_housing_units_per_km2)}/km²</dd>
      <dt>Walkability index <span class="muted">(EPA)</span></dt><dd>${fmtNum(p.epa_walk_index, 1)} / 20</dd>
      <dt>Intersections / sq mi <span class="muted">(EPA)</span></dt><dd>${fmtNum(p.epa_intersection_density)}</dd>
      <dt>Arterial road density <span class="muted">(EPA)</span></dt><dd>${fmtNum(p.epa_auto_road_density, 1)} mi/sq mi</dd>
      <dt>To transit <span class="muted">(EPA)</span></dt><dd>${num(p.epa_transit_dist_m) ? `${fmtNum(p.epa_transit_dist_m)} m` : "—"}</dd>
    </dl>
    <h3>Who lives here <span class="muted">(block group ${p.bg_geo_id ?? "—"}, ${p.borough ?? ""})</span></h3>
    <dl>
      <dt>Est. residents in this block</dt><dd>${fmtNum(p.est_residents)}</dd>
      <dt>Est. aged 65+ in this block</dt><dd>${fmtNum(p.est_residents_65_plus)}</dd>
      <dt>Block group residents</dt><dd>${fmtNum(p.bg_total_pop)}</dd>
      <dt>Aged 65+</dt><dd>${fmtPct(p.bg_share_65_plus)}</dd>
      <dt>Median income</dt><dd>${typeof p.bg_median_income === "number" ? `$${fmtNum(p.bg_median_income)}` : "—"}</dd>
      <dt>Severely rent-burdened</dt><dd>${fmtPct(p.bg_severe_rent_burden_rate)}</dd>
      <dt>Households without a car <span class="muted">(tract)</span></dt><dd>${fmtPct(p.tract_no_vehicle_rate)}</dd>
      <dt>Poverty rate <span class="muted">(tract)</span></dt><dd>${fmtPct(p.tract_poverty_rate)}</dd>
    </dl>
    <div class="muted" style="font-size:.75rem;margin-bottom:8px">Tract ${t.tract_name ?? "—"} · block group ACS 2018, tract ACS 2019</div>
    <button type="button" class="link-btn" id="ask-about">Ask the agent about this block →</button>`;
  el.querySelector("#ask-about").addEventListener("click", () =>
    state.onAsk?.(`Why is the block at ${lat.toFixed(4)}, ${lon.toFixed(4)} as hot as it is, and who lives there?`));
}

const cellCenter = (p) => [(p.cell_j + 0.5) * state.cellDeg, (p.cell_i + 0.5) * state.cellDeg];

function selectCell(feature) {
  state.selected = feature;
  state.map.getSource("selected").setData({ type: "FeatureCollection", features: [feature] });
  state.map.getSource("selected-marker").setData({ type: "FeatureCollection", features: [
    { type: "Feature", properties: {}, geometry: { type: "Point", coordinates: cellCenter(feature.properties) } },
  ] });
  renderInspector(feature.properties);
}

// The forecast/scenario controls live in the legend, which re-renders; listen once on the parent.
function bindForecastControls() {
  const el = document.getElementById("legend");
  const changed = () => {
    applyLayer();
    if (state.selected) renderInspector(state.selected.properties);
  };
  el.addEventListener("change", (e) => {
    if (e.target.id === "base-select") {
      state.baseIdx = e.target.value === "scenario" ? "scenario" : Number(e.target.value);
      changed();
    } else if (e.target.id === "scenario-input") {
      const v = Number(e.target.value);
      if (Number.isFinite(v)) { state.scenarioHigh = Math.min(115, Math.max(60, Math.round(v))); changed(); }
    }
  });
}

// ------------------------------------------------------------------ public
export async function initMapView({ onAsk, onStatus }) {
  state.onAsk = onAsk;
  OVERLAYS.forEach((o) => { state.overlays[o.id] = o.on; });

  const [cells, tracts, model, fc] = await Promise.all([
    api.cells(), api.tracts(), api.model().catch(() => null),
    api.forecast().catch((e) => { state.forecastError = e.message; return null; }),
  ]);
  state.cellDeg = cells.cell_deg;
  state.cells = cellsToGeoJSON(cells);
  state.tracts = tracts;
  state.model = model;
  if (fc?.days?.length) {
    state.forecast = fc;
    // Open on the hottest forecast day; if none is warm enough to matter, open on the scenario.
    const hottest = fc.days.reduce((best, d, i) => (d.high_f > fc.days[best].high_f ? i : best), 0);
    state.baseIdx = fc.days[hottest].high_f >= 85 ? hottest : "scenario";
  }
  bindForecastControls();
  tracts.features.forEach((f) => state.tractById.set(f.properties.geo_id, f.properties));
  const count = (c) => state.cells.features.filter((f) => f.properties.confidence === c).length;
  onStatus?.(`${count("measured").toLocaleString()} measured · ${count("near_data").toLocaleString()} predicted · ` +
    `${count("extrapolated").toLocaleString()} extrapolated blocks · ${tracts.features.length} tracts`);

  const map = new maplibregl.Map({
    container: "map", style: baseStyle(), center: [-73.95, 40.65], zoom: 11, maxZoom: 17,
    attributionControl: { compact: true },
  });
  state.map = map;
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  map.addControl(new maplibregl.ScaleControl({ unit: "imperial" }), "bottom-right");

  renderLayerPicker();
  renderOverlayPicker();
  renderBasemapPicker();

  state.cellIndex = new Map(state.cells.features.map((f, k) =>
    [`${f.properties.cell_i},${f.properties.cell_j}`, k]));

  map.on("load", () => {
    addDataLayers(map);
    state.ready = true;
    readyResolve();
    applyLayer();
    applyOverlays();
    applyBasemap();
    map.fitBounds([[-74.045, 40.57], [-73.83, 40.74]], { padding: 20, duration: 0 });
  });

  const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 8 });
  map.on("mousemove", (e) => {
    if (!map.getLayer("cells-fill")) return;
    const layer = findLayer(state.layer);
    const target = layer.source === "cell" ? "cells-fill" : "tract-fill";
    const f = map.queryRenderedFeatures(e.point, { layers: [target] })[0];
    if (!f) { popup.remove(); map.getCanvas().style.cursor = ""; return; }
    const v = f.properties[layer.id];
    map.getCanvas().style.cursor = "pointer";
    const high = layer.derived === "forecast" ? blockHigh(f.properties) : null;
    const shown = layer.derived === "forecast" ? (high === null ? "no data" : `${high.toFixed(0)}°F`)
      : layer.categories ? (layer.categoryLabels?.[v] ?? v ?? "no data")
      : typeof v === "number" ? (layer.format || ((x) => x.toFixed(1)))(v) : "no data";
    popup.setLngLat(e.lngLat).setHTML(`${layer.label}: <b>${shown}</b>`)
      .addTo(map);
  });
  map.on("mouseout", () => popup.remove());
  map.on("click", (e) => {
    const f = map.queryRenderedFeatures(e.point, { layers: ["cells-fill"] })[0];
    if (!f) return;
    // queryRenderedFeatures returns a tiled copy; use the source feature for exact geometry.
    selectCell(state.cells.features[f.id]);
    // The planner chose this block: let the shell show its details (app.js opens the Block tab).
    window.dispatchEvent(new CustomEvent("heat:cell-clicked", { detail: f.properties }));
  });
  return state;
}

export function showLayer(id) {
  if (!findLayer(id) || !state.map) return;
  state.layer = id;
  const radio = document.querySelector(`#layer-picker input[value="${id}"]`);
  if (radio) radio.checked = true;
  if (state.map.getLayer("cells-fill")) applyLayer();
}

export function resizeMap() {
  state.map?.resize();
}

// ------------------------------------------------------------------ agent-facing API
// Used by js/ui_tools.js to carry out the agent's UI tool calls. Each waits for the map.
let readyResolve;
const ready = new Promise((r) => { readyResolve = r; });
export const whenMapReady = () => ready;

const cellFeature = (i, j) => {
  const k = state.cellIndex?.get(`${i},${j}`);
  return k === undefined ? null : state.cells.features[k];
};

/** Fly to a block and select it (opens the details panel). Returns false if it isn't in the grid. */
export async function focusCell(cellI, cellJ, zoom = 16) {
  await ready;
  const f = cellFeature(cellI, cellJ);
  const lon = (cellJ + 0.5) * 0.001, lat = (cellI + 0.5) * 0.001;
  state.map.flyTo({ center: [lon, lat], zoom, essential: true });
  if (!f) return false;
  selectCell(f);
  return true;
}

export async function flyTo(lat, lon, zoom = 15) {
  await ready;
  state.map.flyTo({ center: [lon, lat], zoom, essential: true });
}

/** Outline blocks given as "cell_i,cell_j" strings and fit the map to them. */
export async function highlightCells(cells, label = "") {
  await ready;
  const d = 0.001;
  const features = cells.map((c) => String(c).split(",").map(Number))
    .filter(([i, j]) => Number.isFinite(i) && Number.isFinite(j))
    .map(([i, j]) => ({
      type: "Feature", properties: { cell_i: i, cell_j: j, label },
      geometry: { type: "Polygon", coordinates: [[[j * d, i * d], [(j + 1) * d, i * d],
        [(j + 1) * d, (i + 1) * d], [j * d, (i + 1) * d], [j * d, i * d]]] },
    }));
  state.map.getSource("highlights").setData({ type: "FeatureCollection", features });
  state.highlight = { label, count: features.length };
  if (features.length) {
    const xs = features.flatMap((f) => f.geometry.coordinates[0].map((p) => p[0]));
    const ys = features.flatMap((f) => f.geometry.coordinates[0].map((p) => p[1]));
    state.map.fitBounds([[Math.min(...xs), Math.min(...ys)], [Math.max(...xs), Math.max(...ys)]],
      { padding: 80, maxZoom: 16 });
  }
  return features.length;
}

export async function clearHighlights() {
  await ready;
  state.map.getSource("highlights").setData({ type: "FeatureCollection", features: [] });
  state.map.getSource("ranked-markers").setData({ type: "FeatureCollection", features: [] });
  state.highlight = null;
}

/**
 * Number a ranked list of places on the map (1 = top) and fit the map to them.
 * Each location gives a block as cell_i/cell_j, or a point as lat/lon; the block it falls in is
 * filled. If a location names a map layer, the map switches to it. Returns
 * {count, matched}: how many were shown, and how many fell on a grid block.
 */
export async function showLocations(locations, label = "") {
  await ready;
  const d = state.cellDeg;
  const pts = locations.slice(0, MAX_RANKED).map((loc, k) => {
    const hasCell = Number.isFinite(Number(loc.cell_i)) && Number.isFinite(Number(loc.cell_j));
    const i = hasCell ? Number(loc.cell_i) : Math.floor(Number(loc.lat) / d);
    const j = hasCell ? Number(loc.cell_j) : Math.floor(Number(loc.lon) / d);
    const lat = hasCell ? (i + 0.5) * d : Number(loc.lat), lon = hasCell ? (j + 0.5) * d : Number(loc.lon);
    return { rank: Number(loc.rank) || k + 1, label: loc.label || "", i, j, lat, lon };
  }).filter((p) => Number.isFinite(p.lat) && Number.isFinite(p.lon));

  const layerId = locations.map((l) => l.layer).find((id) => findLayer(id)?.source === "cell");
  if (layerId) showLayer(layerId);

  const blocks = pts.map((p) => cellFeature(p.i, p.j)).filter(Boolean);
  state.map.getSource("selected").setData({ type: "FeatureCollection", features: blocks });
  state.map.getSource("selected-marker").setData({ type: "FeatureCollection", features: [] });
  state.map.getSource("ranked-markers").setData({ type: "FeatureCollection", features: pts.map((p) => ({
    type: "Feature", properties: { rank: Math.min(p.rank, MAX_RANKED), label: p.label },
    geometry: { type: "Point", coordinates: [p.lon, p.lat] },
  })) });
  state.highlight = { label: label || "ranked locations", count: pts.length,
    ranked: pts.map((p) => `${p.rank}: ${p.i},${p.j}`) };

  if (pts.length === 1) {
    state.map.flyTo({ center: [pts[0].lon, pts[0].lat], zoom: 16, duration: 900, essential: true });
  } else if (pts.length > 1) {
    const lons = pts.map((p) => p.lon), lats = pts.map((p) => p.lat);
    state.map.fitBounds([[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]],
      { padding: 60, maxZoom: 15, duration: 700 });
  }
  return { count: pts.length, matched: blocks.length };
}

/** What the planner is looking at, sent with every chat message as the agent's ui_context. */
export function getMapContext() {
  if (!state.map) return { map_loaded: false };
  const c = state.map.getCenter();
  const sel = state.selected?.properties;
  const selectedCenter = sel ? cellCenter(sel) : null;
  return {
    layer: state.layer,
    center: { lat: +c.lat.toFixed(4), lon: +c.lng.toFixed(4) },
    zoom: +state.map.getZoom().toFixed(1),
    selected_block: sel ? {
      cell_i: sel.cell_i, cell_j: sel.cell_j,
      lat: +selectedCenter[1].toFixed(5), lon: +selectedCenter[0].toFixed(5),
      offset_best_f: sel.offset_best_f, confidence: sel.confidence,
    } : null,
    highlighted: state.highlight,
    base_high: baseHigh().label,
  };
}
