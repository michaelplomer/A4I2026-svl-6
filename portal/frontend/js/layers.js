// Map layer catalogue. One entry per thing the map can be coloured by.
//
//   source      "cell"  -> a column of a4i_grid.cell_profile_full (0.001° squares)
//               "tract" -> a column of a4i_grid.tract_exposure (census tract polygons)
//   domain      fixed [min, (mid,) max], or omitted to use the data's 2nd-98th percentile
//   categories  {value: colour} for a categorical column (instead of palette/domain)
//   status      omitted = ready; anything else greys the layer out and shows its note
//
// To add a feature: add the column to CELL_COLUMNS / TRACT_COLUMNS in backend/data.py,
// refresh the snapshot, and add an entry here.

const HEAT = ["#2166ac", "#67a9cf", "#d1e5f0", "#f7f7f7", "#fddbc7", "#ef8a62", "#b2182b"];
const GREENS = ["#f7fcf5", "#c7e9c0", "#74c476", "#238b45", "#00441b"];
const BLUES = ["#f7fbff", "#c6dbef", "#6baed6", "#2171b5", "#08306b"];
const PURPLES = ["#fcfbfd", "#dadaeb", "#9e9ac8", "#6a51a3", "#3f007d"];
const ORANGES = ["#fff5eb", "#fdd0a2", "#fd8d3c", "#d94801", "#7f2704"];
const GREYS = ["#ffffff", "#bdbdbd", "#737373", "#252525"];

export const LAYER_GROUPS = [
  {
    label: "Heat",
    layers: [
      {
        id: "offset_best_f", source: "cell", label: "Heat offset (measured or predicted)", unit: "°F",
        palette: HEAT, domain: [-3, 0, 3], format: (v) => `${v > 0 ? "+" : ""}${v.toFixed(1)}°F`, model: true,
        note: "Degrees hotter (+) or cooler (−) than the city average on the measured afternoon. Measured where a car drove, predicted by heat_offset_v1 elsewhere. Not a temperature: add it to the day's forecast high.",
      },
      {
        // Derived in the browser: base high (NWS forecast day or a what-if scenario) + offset_best_f.
        id: "forecast_high_f", source: "cell", label: "Estimated high: forecast or scenario day", unit: "°F",
        derived: "forecast",
        bands: [
          { min: null, color: "#91bfdb", label: "below 80°F" },
          { min: 80, color: "#fee090", label: "80–89°F" },
          { min: 90, color: "#fdae61", label: "90–94°F: caution" },
          { min: 95, color: "#f46d43", label: "95–99°F: danger" },
          { min: 100, color: "#d73027", label: "100–104°F: extreme" },
          { min: 105, color: "#67001f", label: "105°F+: extreme, hottest blocks" },
        ],
        note: "Block high = the day's high + this block's offset. An afternoon estimate, not hourly: the offset was measured relative to the city average on one hot afternoon.",
      },
      {
        id: "measured_offset_f", source: "cell", label: "Measured heat offset", unit: "°F",
        palette: HEAT, domain: [-3, 0, 3], format: (v) => `${v > 0 ? "+" : ""}${v.toFixed(1)}°F`,
        note: "Driven blocks only (5+ readings), one afternoon.",
      },
      {
        id: "predicted_offset_f", source: "cell", label: "Predicted heat offset (model only)", unit: "°F",
        palette: HEAT, domain: [-3, 0, 3], format: (v) => `${v > 0 ? "+" : ""}${v.toFixed(1)}°F`, model: true,
        note: "The model's prediction everywhere, including driven blocks, so you can compare it with the measurement.",
      },
      {
        id: "confidence", source: "cell", label: "How much to trust the number",
        categories: { measured: "#1b7837", near_data: "#7fbf7b", extrapolated: "#c2a5cf" },
        categoryLabels: { measured: "Measured", near_data: "Predicted, near measured streets",
          extrapolated: "Extrapolated (nothing measured within 1 km)" },
        note: "Extrapolated blocks rely on built-environment features alone, which barely beat the city average. Treat them as unknown.",
      },
      {
        id: "heat_index_offset_f", source: "cell", label: "Measured heat-index offset", unit: "°F",
        palette: HEAT, domain: [-3, 0, 3], format: (v) => `${v > 0 ? "+" : ""}${v.toFixed(1)}°F`,
        note: "Same as above, for how hot it feels (temperature + humidity).",
      },
      {
        id: "est_days_ge_95_per_year", source: "cell", label: "Estimated days ≥ 95°F per year", unit: "days",
        palette: ORANGES, format: (v) => v.toFixed(1),
        note: "City reference high + this block's offset (measured or predicted), averaged over 2019–2025. An estimate: assumes the one-afternoon offset holds on every hot day.",
      },
      {
        id: "trend_days_ge_95_per_year", source: "cell", label: "Trend in days ≥ 95°F (measured blocks)", unit: "days/yr",
        palette: HEAT, domain: [-2, 0, 2], format: (v) => `${v > 0 ? "+" : ""}${v.toFixed(1)}/yr`,
        note: "Slope over 7 years. Too short a record to call a real trend; show with care.",
      },
    ],
  },
  {
    label: "Built environment (model features)",
    layers: [
      { id: "trees_100m", source: "cell", label: "Street trees within 100 m", unit: "trees",
        palette: GREENS, format: (v) => v.toFixed(0) },
      { id: "trees_250m", source: "cell", label: "Street trees within 250 m", unit: "trees",
        palette: GREENS, format: (v) => v.toFixed(0) },
      { id: "tree_dbh_sum_100m", source: "cell", label: "Trunk diameter within 100 m", unit: "in",
        palette: GREENS, format: (v) => v.toFixed(0),
        note: "Summed diameter at breast height: a rough stand-in for canopy size." },
      { id: "dist_open_space_m", source: "cell", label: "Distance to large open space", unit: "m",
        palette: ORANGES, format: (v) => `${(v / 1000).toFixed(1)} km`,
        note: "Parks, cemeteries and airfields (near-empty tracts). A proxy, not a park layer." },
      { id: "tract_water_share", source: "cell", label: "Water share of tract", unit: "",
        palette: BLUES, format: (v) => `${(v * 100).toFixed(0)}%`,
        note: "Waterfront proxy. Strongest single correlate of the offset so far (r = −0.29)." },
      { id: "bg_housing_units_per_km2", source: "cell", label: "Housing density (block group)", unit: "units/km²",
        palette: PURPLES, format: (v) => Math.round(v).toLocaleString() },
    ],
  },
  {
    // EPA Smart Location Database v3 (2021, CC0), per 2010 block group. All are model features.
    label: "Walkability & urban form (EPA Smart Location)",
    layers: [
      { id: "epa_walk_index", source: "cell", label: "National Walkability Index", unit: "1–20",
        palette: PURPLES, domain: [1, 20], format: (v) => v.toFixed(1),
        note: "EPA composite of intersection density, transit proximity and land-use mix." },
      { id: "epa_intersection_density", source: "cell", label: "Street intersection density", unit: "per sq mi",
        palette: PURPLES, format: (v) => Math.round(v).toLocaleString() },
      { id: "epa_activity_density", source: "cell", label: "Activity density", unit: "jobs + homes / acre",
        palette: PURPLES, format: (v) => v.toFixed(0) },
      { id: "epa_landuse_mix", source: "cell", label: "Land-use mix", unit: "0–1",
        palette: PURPLES, domain: [0, 1], format: (v) => v.toFixed(2),
        note: "0 = a single use (all homes or all jobs), 1 = an even mix." },
      { id: "epa_auto_road_density", source: "cell", label: "Auto-oriented road density", unit: "mi / sq mi",
        palette: GREYS, format: (v) => v.toFixed(1),
        note: "Highways and arterials: wide asphalt, few trees." },
      { id: "epa_transit_dist_m", source: "cell", label: "Distance to nearest transit stop", unit: "m",
        palette: ORANGES, format: (v) => `${Math.round(v)} m`,
        note: "Also an access question: how far a resident without a car walks to a bus or train." },
    ],
  },
  {
    // Block-group columns (bg_*) are ACS 2018; car ownership and poverty only exist per tract (ACS 2019).
    label: "Who lives there (not model inputs)",
    layers: [
      { id: "bg_share_65_plus", source: "cell", label: "Residents 65+", palette: ORANGES,
        format: (v) => `${(v * 100).toFixed(0)}%`, note: "Block group, ACS 2018." },
      { id: "est_residents_65_plus", source: "cell", label: "Estimated residents 65+ in this block", unit: "people",
        palette: ORANGES, format: (v) => v.toFixed(0),
        note: "Block group's 65+ population spread evenly over its land cells. Adds up across cells without double counting." },
      { id: "tract_no_vehicle_rate", source: "cell", label: "Households without a car", palette: ORANGES,
        format: (v) => `${(v * 100).toFixed(0)}%`, note: "Tract, ACS 2019 (not published per block group)." },
      { id: "tract_poverty_rate", source: "cell", label: "Poverty rate", palette: ORANGES,
        format: (v) => `${(v * 100).toFixed(0)}%`, note: "Tract, ACS 2019." },
      { id: "bg_median_income", source: "cell", label: "Median household income", palette: PURPLES,
        format: (v) => `$${Math.round(v / 1000)}k`, note: "Block group, ACS 2018." },
      { id: "bg_severe_rent_burden_rate", source: "cell", label: "Severely rent-burdened renters", palette: ORANGES,
        format: (v) => `${(v * 100).toFixed(0)}%`,
        note: "Renters paying 50%+ of income on rent: less room for AC and power bills. Block group, ACS 2018." },
      { id: "bg_pop_per_km2", source: "cell", label: "Population density", unit: "people/km²",
        palette: PURPLES, format: (v) => Math.round(v).toLocaleString(), note: "Block group, ACS 2018." },
    ],
  },
  {
    label: "Model coverage",
    layers: [
      { id: "dist_to_driven_m", source: "cell", label: "Distance to nearest measured block", unit: "m",
        palette: GREYS, domain: [0, 3000], format: (v) => `${Math.round(v)} m`,
        note: "Beyond ~1 km the model is extrapolating. Say so when you show a prediction there." },
    ],
  },
];

export const OVERLAYS = [
  { id: "tract-lines", label: "Census tract boundaries", on: true },
  { id: "driven-outline", label: "Outline measured blocks", on: false },
  { id: "out-of-range", label: "Grey out extrapolated blocks (nothing measured within 1 km)", on: true },
  { id: "holdout", label: "Highlight held-out test blocks", on: false },
  { id: "non-land", label: "Show water / non-residential cells", on: false },
];

// Public-domain basemaps (US Government works) -- avoids the ODbL question that
// OpenStreetMap-based tiles would raise. Max native zoom is 16.
export const BASEMAPS = [
  { id: "topo", label: "Streets",
    url: "https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}" },
  { id: "imagery", label: "Imagery",
    url: "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}" },
  { id: "none", label: "None", url: null },
];

// Plain-language names for the model's inputs, used in the "why" breakdown.
export const FEATURE_LABELS = {
  nbr_offset_1km: "Nearby measured streets",
  tract_water_share: "Waterfront (water share of tract)",
  tree_dbh_sum_100m: "Tree size within 100 m",
  trees_100m: "Street-tree count within 100 m",
  bg_housing_units_per_km2: "Housing density",
};

export function findLayer(id) {
  for (const g of LAYER_GROUPS) for (const l of g.layers) if (l.id === id) return l;
  return null;
}
