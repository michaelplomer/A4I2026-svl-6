// App shell: top navigation, a tiny hash router (#/map, #/today, ...) and the right-hand
// side panel (Assistant | Layers | Block tabs).
import { api } from "./api.js";
import { initMapView, showLayer, resizeMap, getMapContext, showLocations, focusCell } from "./map.js";
import { renderView } from "./views.js";
import { initChat, askAgent } from "./chat.js";
import { configureUiTools } from "./ui_tools.js";

const els = {
  nav: document.getElementById("sidenav"),
  mapView: document.getElementById("view-map"),
  generic: document.getElementById("view-generic"),
  status: document.getElementById("data-status"),
  panel: document.getElementById("side-panel"),
};

let views = [], questions = [], mapReady = null, currentView = null;

function go(id) { location.hash = `#/${id}`; }

// ------------------------------------------------------------------ side panel tabs
export function selectTab(tab) {
  els.panel.querySelectorAll(".side-tab-btn").forEach((b) =>
    b.setAttribute("aria-selected", String(b.dataset.tab === tab)));
  els.panel.querySelectorAll(".side-tab").forEach((t) => { t.hidden = t.id !== `tab-${tab}`; });
  if (tab === "assistant") els.panel.querySelector("#chat-input")?.focus();
}

function initSidePanel() {
  els.panel.querySelector(".side-tabs").addEventListener("click", (e) => {
    const tab = e.target.closest("[data-tab]")?.dataset.tab;
    if (tab) selectTab(tab);
  });
  // A block the planner clicks on the map opens its details. Blocks the agent selects don't
  // switch tabs, so its reply stays in view.
  window.addEventListener("heat:cell-clicked", () => selectTab("block"));
}

// Layers and Block only mean something on the map view.
function setMapTabs(onMap) {
  els.panel.classList.toggle("on-map", onMap);
  const active = els.panel.querySelector('.side-tab-btn[aria-selected="true"]')?.dataset.tab;
  if (!onMap && active !== "assistant") selectTab("assistant");
}

// ------------------------------------------------------------------ views
async function showMap() {
  els.generic.hidden = true;
  els.mapView.hidden = false;
  setMapTabs(true);
  await ensureMap();
  resizeMap();
}

// For the agent's UI tools: switch to the map (if needed) before acting on it.
async function ensureMapVisible() {
  if (currentView !== "map") go("map");
  await showMap();
}

function renderNav(active) {
  els.nav.innerHTML = views.map((v) => `
    <a class="nav-item ${v.id === active ? "active" : ""}" href="#/${v.id}" title="${v.label}">
      <span aria-hidden="true">${v.icon}</span><span class="nav-label">${v.label}</span></a>`).join("");
}

function ensureMap() {
  if (!mapReady) {
    mapReady = initMapView({
      onAsk: (text) => { selectTab("assistant"); askAgent(text); },
      onStatus: (s) => { els.status.textContent = s; },
    }).catch((e) => {
      els.status.textContent = "Data unavailable";
      document.getElementById("legend").innerHTML =
        `<div class="legend-title">Couldn't load map data</div><div class="legend-note">${e.message}</div>`;
    });
  }
  return mapReady;
}

async function route() {
  const id = (location.hash.replace(/^#\//, "") || "map").split("?")[0];
  // "Ask the agent" is the Assistant tab beside the map, not a page of its own.
  if (id === "ask") {
    selectTab("assistant");
    go("map");
    return;
  }
  const view = views.find((v) => v.id === id) || views[0];
  currentView = view.id;
  renderNav(view.id);

  if (view.id === "map") {
    await showMap();
    return;
  }
  els.mapView.hidden = true;
  els.generic.hidden = false;
  setMapTabs(false);
  await renderView(els.generic, view, questions, {
    onShowLayer: async (layer) => { go("map"); await ensureMap(); showLayer(layer); },
    onShowLocations: async (locations, label) => { go("map"); await ensureMap(); showLocations(locations, label); },
    onFocusCell: async (i, j) => { go("map"); await ensureMap(); focusCell(i, j); },
  });
}

async function main() {
  ({ views, questions } = await api.personas());
  // The assistant is the side panel now, so it doesn't need a nav entry of its own.
  views = views.filter((v) => v.id !== "ask");
  initSidePanel();
  configureUiTools({ go, ensureMapVisible, selectTab });
  initChat({ questions, getContext: () => ({ view: currentView, ...getMapContext() }) });
  window.addEventListener("hashchange", route);
  await route();
  // Warm the data cache so the map opens instantly later. (Don't build the map itself
  // while its container is hidden: MapLibre would size it to 0x0.)
  api.cells().catch(() => {});
  api.tracts().catch(() => {});
}

main().catch((e) => { els.status.textContent = `Error: ${e.message}`; });
