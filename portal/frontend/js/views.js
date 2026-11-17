// Non-map views. Each is generated from the persona catalogue (/api/personas): the questions
// it will answer, how far the data gets us today, and what's left to build.
import { api } from "./api.js";
import { BODIES } from "./views_data.js";

export const STATUS = {
  ready: "Ready now",
  needs_model: "Needs model",
  needs_agent: "Needs agent",
  needs_external: "Needs external data",
};

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// How each view's numbers are made. Shown above the view's tables.
const METHOD = {
  today: {
    title: "How this list is made",
    body: "Take the hottest quarter of populated blocks (offset vs. the city average: measured where a Heat Watch car drove, predicted elsewhere), then rank them by the people least able to cope: residents 65+ plus households without a car. Estimated high = the city high you pick + the block's offset. A ranked list, not a truck route.",
  },
  decade: {
    title: "How these lists are made",
    body: "Planting priority: hot blocks (hottest quarter) with fewer street trees than the Brooklyn median, ranked by heat × residents. Ready planting sites: dead trees and stumps from the 2015 street tree census inside hot blocks. The model's tree effect is small and is an association from one afternoon, not a measured cooling effect.",
  },
  equity: {
    title: "How the audit works",
    body: "Every number is resident-weighted over grid blocks (ACS 2019 5-year, block group and tract). The recommendation columns are exactly the lists on the Today and Decade pages. Demographics are never model inputs, so this check isn't circular. Race isn't in the snapshot yet.",
  },
  history: {
    title: "How these numbers are made",
    body: "City: days the average high of Central Park, LaGuardia and JFK crossed each threshold. Per block: that daily high + the block's offset, assuming the offset holds on every hot day. Seven years is too short to call a trend.",
  },
};

function coverageChips(questions) {
  const counts = {};
  questions.forEach((q) => { counts[q.status] = (counts[q.status] || 0) + 1; });
  return `<div class="coverage">${Object.keys(STATUS).filter((s) => counts[s])
    .map((s) => `<span class="chip ${s}">${counts[s]} · ${STATUS[s]}</span>`).join("")}</div>`;
}

function questionCards(questions) {
  if (!questions.length) return "";
  return `<div class="cards">${questions.map((q) => `
    <article class="card">
      <div class="card-who">${q.track === "today" ? "⚡ Today" : "🌳 Decade"} #${q.n} · ${esc(q.who)}</div>
      <div class="card-ask">“${esc(q.ask)}”</div>
      <span class="chip ${q.status}">${STATUS[q.status]}</span>
      <div class="card-gap">${esc(q.gap)}</div>
      ${q.layers.length ? `<div class="card-actions">
        <button type="button" class="link-btn" data-layer="${q.layers[0]}">Show on map →</button></div>` : ""}
    </article>`).join("")}</div>`;
}

function todoBlock(id) {
  const t = METHOD[id];
  return t ? `<div class="todo"><h3>${t.title}</h3><div class="muted">${t.body}</div></div>` : "";
}

// View-specific bodies live in views_data.js.

function askBody(prefill) {
  return `
    <div class="chat">
      <textarea id="ask-input" placeholder="e.g. We can fund three cooling centers this summer. Where should they go?">${esc(prefill || "")}</textarea>
      <div><button type="button" class="link-btn" id="ask-send">Send</button></div>
      <div id="ask-reply"></div>
    </div>`;
}

function wireAsk(root) {
  const send = root.querySelector("#ask-send");
  if (!send) return;
  send.addEventListener("click", async () => {
    const msg = root.querySelector("#ask-input").value.trim();
    const out = root.querySelector("#ask-reply");
    if (!msg) return;
    out.innerHTML = `<div class="chat-reply muted">Sending…</div>`;
    const r = await api.ask(msg);
    const body = await r.json().catch(() => ({}));
    out.innerHTML = `<div class="chat-reply">${r.ok ? esc(body.reply ?? JSON.stringify(body))
      : `<span class="muted">${esc(body.detail || `HTTP ${r.status}`)}</span>`}</div>`;
  });
}

// ---------------------------------------------------------------- public
export async function renderView(root, view, questions, opts = {}) {
  const { onShowLayer, prefill } = opts;
  const mine = questions.filter((q) => q.view === view.id);
  const B = BODIES[view.id];
  let body = "";
  if (B) {
    try { body = await B.html(); }
    catch (e) { body = `<div class="flag warn">Couldn't load the data for this view: ${esc(e.message)}</div>`; }
  }
  if (view.id === "ask") body = askBody(prefill);

  root.innerHTML = `
    <div class="view-head">
      <h1>${view.icon} ${esc(view.label)}</h1>
      <p>${esc(view.blurb)}</p>
      ${mine.length ? coverageChips(mine) : ""}
    </div>
    ${todoBlock(view.id)}
    ${body ? `<div class="view-body">${body}</div>` : ""}
    ${mine.length ? `<h2>Questions this view answers</h2>${questionCards(mine)}` : ""}`;

  root.querySelectorAll("[data-layer]").forEach((b) =>
    b.addEventListener("click", () => onShowLayer?.(b.dataset.layer)));
  wireAsk(root);
  B?.wire?.(root, opts);
}
