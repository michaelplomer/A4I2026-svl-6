"""Frontend ("UI") tools: things the agent does to the PORTAL, not to data.

These functions never touch the map themselves. When the agent calls one, the portal backend
(portal/backend/chat.py) sees the function call in ADK's event stream and forwards it to the
browser as a `ui_action` event; portal/frontend/js/ui_tools.js runs it (fly to a cell, switch
layer, ...). The Python body only validates the arguments and returns an acknowledgement, so
the model gets a sensible tool result and keeps talking.

Contract: the function NAME and ARGUMENTS here must match HANDLERS in
portal/frontend/js/ui_tools.js. Add a tool in both places. GET /api/chat/contract lists them.

Keep them fire-and-forget: the agent learns the outcome from the next turn's `ui_context`
(current layer, selected cell, view), not from the return value.
"""
from __future__ import annotations

# Map layers the browser knows (portal/frontend/js/layers.js). Keep in sync.
MAP_LAYERS = {
    "offset_best_f": "Heat offset, measured or predicted (°F vs campaign average). Default.",
    "forecast_high_f": "Estimated block high on the chosen forecast day / scenario.",
    "measured_offset_f": "Measured offset only (driven blocks).",
    "predicted_offset_f": "Model prediction for every land block.",
    "confidence": "Measured / near measured streets / extrapolated.",
    "heat_index_offset_f": "Measured heat-index offset (driven blocks).",
    "est_days_ge_95_per_year": "Estimated days per year at or above 95°F.",
    "trend_days_ge_95_per_year": "7-year trend in days >= 95°F (weak evidence).",
    "trees_100m": "Street trees within 100 m.",
    "trees_250m": "Street trees within 250 m.",
    "tree_dbh_sum_100m": "Summed trunk diameter within 100 m.",
    "dist_open_space_m": "Distance to large open space.",
    "tract_water_share": "Water share of the tract (waterfront proxy).",
    "bg_housing_units_per_km2": "Housing density.",
    "epa_walk_index": "EPA National Walkability Index (1-20).",
    "epa_intersection_density": "EPA street intersection density.",
    "epa_activity_density": "EPA jobs + homes per acre.",
    "epa_landuse_mix": "EPA land-use mix (0-1).",
    "epa_auto_road_density": "EPA auto-oriented road density.",
    "epa_transit_dist_m": "EPA distance to nearest transit stop.",
    "bg_share_65_plus": "Share of residents aged 65+.",
    "est_residents_65_plus": "Estimated residents 65+ in the block.",
    "tract_no_vehicle_rate": "Households without a car (tract).",
    "tract_poverty_rate": "Poverty rate (tract).",
    "bg_median_income": "Median household income.",
    "bg_severe_rent_burden_rate": "Severely rent-burdened renters.",
    "bg_pop_per_km2": "Population density.",
    "dist_to_driven_m": "Distance to nearest measured block.",
}

PORTAL_VIEWS = ("map", "today", "decade", "equity", "history")

MAX_HIGHLIGHT = 500


def _ok(**shown) -> dict:
    return {"status": "sent_to_portal", **shown}


def _err(msg: str) -> dict:
    return {"status": "error", "error": msg}


def show_cell_on_map(cell_i: int, cell_j: int, zoom: float = 16.0) -> dict:
    """Fly the planner's map to one grid block, select it and open its details panel.

    Use after you mention a specific block, so the planner sees it. Blocks are 0.001-degree
    cells: cell_i = floor(lat / 0.001), cell_j = floor(lon / 0.001)
    (e.g. lat 40.6782, lon -73.9442 -> cell_i 40678, cell_j -73945).

    Args:
        cell_i: Latitude index of the block (about 40570 to 40750 in Brooklyn).
        cell_j: Longitude index of the block (negative, about -74045 to -73830).
        zoom: Map zoom level, 12 (borough) to 17 (street). Default 16.
    """
    if not (40000 <= cell_i <= 41000 and -75000 <= cell_j <= -73000):
        return _err("cell_i/cell_j are outside New York; compute them as floor(lat/0.001), floor(lon/0.001).")
    return _ok(cell=f"{cell_i},{cell_j}", zoom=zoom)


def show_location_on_map(lat: float, lon: float, zoom: float = 15.0) -> dict:
    """Pan and zoom the planner's map to a coordinate without selecting a block.

    Use for areas (a neighbourhood centre, the middle of a cluster of sites).

    Args:
        lat: Latitude in decimal degrees.
        lon: Longitude in decimal degrees (negative in New York).
        zoom: 11 (all of Brooklyn) to 17 (street). Default 15.
    """
    if not (40.4 <= lat <= 41.0 and -74.3 <= lon <= -73.6):
        return _err("That coordinate is outside New York City.")
    return _ok(center=[lat, lon], zoom=zoom)


def set_map_layer(layer_id: str) -> dict:
    """Change what the planner's map is coloured by.

    Args:
        layer_id: One of: offset_best_f, forecast_high_f, measured_offset_f, predicted_offset_f,
            confidence, heat_index_offset_f, est_days_ge_95_per_year, trend_days_ge_95_per_year,
            trees_100m, trees_250m, tree_dbh_sum_100m, dist_open_space_m, tract_water_share,
            bg_housing_units_per_km2, epa_walk_index, epa_intersection_density,
            epa_activity_density, epa_landuse_mix, epa_auto_road_density, epa_transit_dist_m,
            bg_share_65_plus, est_residents_65_plus, tract_no_vehicle_rate, tract_poverty_rate,
            bg_median_income, bg_severe_rent_burden_rate, bg_pop_per_km2, dist_to_driven_m.
    """
    if layer_id not in MAP_LAYERS:
        return _err(f"Unknown layer '{layer_id}'. Valid: {', '.join(MAP_LAYERS)}")
    return _ok(layer=layer_id, meaning=MAP_LAYERS[layer_id])


def highlight_cells_on_map(cells: list[str], label: str = "") -> dict:
    """Outline a set of blocks on the planner's map (e.g. your recommended sites) and fit the
    map to them. Replaces any previous highlight.

    Args:
        cells: Blocks as "cell_i,cell_j" strings, e.g. ["40678,-73945", "40681,-73950"].
            At most 500.
        label: Short caption shown with the highlight, e.g. "Top 10 cooling-center sites".
    """
    if not cells:
        return _err("Give at least one cell.")
    if len(cells) > MAX_HIGHLIGHT:
        return _err(f"At most {MAX_HIGHLIGHT} cells.")
    for c in cells:
        try:
            i, j = (int(x) for x in str(c).split(","))
        except ValueError:
            return _err(f"Bad cell '{c}': use 'cell_i,cell_j'.")
    return _ok(n_cells=len(cells), label=label)


def clear_map_highlights() -> dict:
    """Remove the outlined blocks from the planner's map."""
    return _ok()


def open_portal_view(view_id: str) -> dict:
    """Switch the portal to another page. The map stays loaded.

    Args:
        view_id: map (default), today (dispatch), decade (interventions), equity (audit),
            history (dangerous days per year).
    """
    if view_id not in PORTAL_VIEWS:
        return _err(f"Unknown view '{view_id}'. Valid: {', '.join(PORTAL_VIEWS)}")
    return _ok(view=view_id)


UI_TOOLS = [
    show_cell_on_map,
    show_location_on_map,
    set_map_layer,
    highlight_cells_on_map,
    clear_map_highlights,
    open_portal_view,
]
UI_TOOL_NAMES = {f.__name__ for f in UI_TOOLS}
