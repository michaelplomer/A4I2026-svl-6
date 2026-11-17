// Browser-side handlers for the agent's UI tools.
//
// The agent (agent/heat_agent/ui_tools.py) declares these tools; when it calls one, the
// backend streams a `ui_action` {name, args} event and chat.js passes it here.
// NAMES AND ARGUMENTS MUST MATCH the Python functions. GET /api/chat/contract lists them.
//
// Each handler returns a short human-readable note for the chat ("Zoomed to block …"),
// or throws to report a failure in the chat.
import { focusCell, flyTo, showLayer, highlightCells, clearHighlights, getMapContext } from "./map.js";
import { findLayer } from "./layers.js";

let navigate = (view) => { location.hash = `#/${view}`; };
let ensureMap = async () => {};

/** app.js hands in its router so tools can switch views and make sure the map exists. */
export function configureUiTools({ go, ensureMapVisible }) {
  if (go) navigate = go;
  if (ensureMapVisible) ensureMap = ensureMapVisible;
}

export const HANDLERS = {
  async show_cell_on_map({ cell_i, cell_j, zoom = 16 }) {
    await ensureMap();
    const found = await focusCell(Number(cell_i), Number(cell_j), Number(zoom));
    return found ? `Zoomed to block ${cell_i},${cell_j}` : `Block ${cell_i},${cell_j} isn't in the grid; centred there`;
  },

  async show_location_on_map({ lat, lon, zoom = 15 }) {
    await ensureMap();
    await flyTo(Number(lat), Number(lon), Number(zoom));
    return `Moved map to ${Number(lat).toFixed(4)}, ${Number(lon).toFixed(4)}`;
  },

  async set_map_layer({ layer_id }) {
    const layer = findLayer(layer_id);
    if (!layer) throw new Error(`unknown layer ${layer_id}`);
    await ensureMap();
    showLayer(layer_id);
    return `Map layer: ${layer.label}`;
  },

  async highlight_cells_on_map({ cells = [], label = "" }) {
    await ensureMap();
    const n = await highlightCells(cells, label);
    return `Outlined ${n} block${n === 1 ? "" : "s"}${label ? `: ${label}` : ""}`;
  },

  async clear_map_highlights() {
    await clearHighlights();
    return "Cleared highlights";
  },

  async open_portal_view({ view_id }) {
    navigate(view_id);
    return `Opened ${view_id}`;
  },
};

/** Run one ui_action. Never throws: returns {ok, note}. */
export async function runUiAction({ name, args }) {
  const fn = HANDLERS[name];
  if (!fn) return { ok: false, note: `Portal can't do "${name}" yet` };
  try {
    return { ok: true, note: await fn(args || {}) };
  } catch (e) {
    return { ok: false, note: `${name} failed: ${e.message}` };
  }
}

export { getMapContext };
