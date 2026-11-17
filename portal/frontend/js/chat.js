// Chat panel: talks to POST /api/chat (server-sent events) and runs the agent's UI tools.
//
// Stream events (see portal/backend/chat.py):
//   session {session_id, mode} · text {delta} · tool_call {id, name, args, ui}
//   tool_result {id, name, response} · ui_action {id, name, args} · error {message} · done {}
//
// Every message carries `context` (what the planner sees: view, layer, selected block, map
// centre, and the persona they're planning as) so the agent can resolve "this block" or "here"
// and pitch the answer at the right person.
import { runUiAction } from "./ui_tools.js";

const SESSION_KEY = "heat-chat-session";
const PERSONA_KEY = "heat-chat-persona";
// Shown when the persona has no starter questions in the planning sheet.
const GENERAL_STARTERS = [
  "How hot is the block I've selected, and why?",
  "Which blocks have the most residents 65+ in the hottest spots?",
  "Show me where street trees are sparse",
  "How often does Brooklyn reach 95°F?",
];

const state = { sessionId: null, busy: false, getContext: () => ({}), els: {}, questions: [], persona: "" };
let uiQueue = Promise.resolve(); // UI actions run one after another, across messages

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// Minimal, safe markdown: escape first, then **bold**, `code`, bullets and line breaks.
function md(text) {
  const lines = esc(text).split("\n");
  let html = "", inList = false;
  for (const raw of lines) {
    const line = raw.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>");
    const bullet = line.match(/^\s*[*-]\s+(.*)$/);
    if (bullet) {
      if (!inList) { html += "<ul>"; inList = true; }
      html += `<li>${bullet[1]}</li>`;
    } else {
      if (inList) { html += "</ul>"; inList = false; }
      html += line.trim() ? `<p>${line}</p>` : "";
    }
  }
  return html + (inList ? "</ul>" : "");
}

function store(key, value) {
  try { value === null ? sessionStorage.removeItem(key) : sessionStorage.setItem(key, value); } catch { /* private mode */ }
}
function load(key) {
  try { return sessionStorage.getItem(key); } catch { return null; }
}

// ------------------------------------------------------------------ rendering
function scrollDown() {
  const log = state.els.log;
  log.scrollTop = log.scrollHeight;
}

function addMessage(role, html = "") {
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  el.innerHTML = `<div class="msg-body">${html}</div><div class="msg-tools"></div>`;
  state.els.log.appendChild(el);
  state.els.empty.hidden = true;
  scrollDown();
  return el;
}

function addChip(msgEl, id, text, kind) {
  const chip = document.createElement("span");
  chip.className = `tool-chip ${kind}`;
  if (id) chip.dataset.id = id;
  chip.textContent = text;
  msgEl.querySelector(".msg-tools").appendChild(chip);
  scrollDown();
  return chip;
}

function setBusy(busy) {
  state.busy = busy;
  state.els.send.disabled = busy;
  state.els.input.disabled = busy;
  state.els.panel.classList.toggle("busy", busy);
}

// ------------------------------------------------------------------ SSE over fetch
async function* sseEvents(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let cut;
    while ((cut = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, cut);
      buf = buf.slice(cut + 2);
      let event = "message", data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      try { yield { event, data: data ? JSON.parse(data) : {} }; } catch { /* malformed chunk */ }
    }
  }
}

async function send(text) {
  text = text.trim();
  if (!text || state.busy) return;
  state.els.input.value = "";
  addMessage("user", md(text));
  const reply = addMessage("assistant", `<span class="typing">Thinking…</span>`);
  const body = reply.querySelector(".msg-body");
  let answer = "";
  setBusy(true);
  try {
    const r = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, session_id: state.sessionId,
        context: { ...state.getContext(), persona: state.persona || null } }),
    });
    if (!r.ok || !r.body) throw new Error(`HTTP ${r.status}`);
    for await (const { event, data } of sseEvents(r)) {
      if (event === "session") {
        state.sessionId = data.session_id;
        store(SESSION_KEY, data.session_id);
        state.els.mode.textContent = data.mode === "mock" ? "Mock agent" : "Gemini agent";
        state.els.mode.classList.toggle("mock", data.mode === "mock");
      } else if (event === "text") {
        answer += data.delta;
        body.innerHTML = md(answer);
        scrollDown();
      } else if (event === "tool_call" && !data.ui) {
        addChip(reply, data.id, `🔎 ${data.name}`, "data pending");
      } else if (event === "tool_result") {
        reply.querySelector(`.tool-chip[data-id="${CSS.escape(data.id || "")}"]`)?.classList.remove("pending");
      } else if (event === "ui_action") {
        // Run map actions in order, but don't hold up the reply while the map animates or loads.
        const chip = addChip(reply, data.id, `🗺️ ${data.name}…`, "ui pending");
        uiQueue = uiQueue.then(() => runUiAction(data)).then((res) => {
          chip.textContent = `🗺️ ${res.note}`;
          chip.classList.remove("pending");
          chip.classList.toggle("failed", !res.ok);
        });
      } else if (event === "error") {
        addChip(reply, null, `⚠️ ${data.message}`, "failed");
      }
    }
    if (!answer) body.innerHTML = `<span class="muted">(no text reply)</span>`;
  } catch (e) {
    body.innerHTML = `<span class="muted">Couldn't reach the agent: ${esc(e.message)}</span>`;
  } finally {
    setBusy(false);
    state.els.input.focus();
  }
}

function newChat() {
  state.sessionId = null;
  store(SESSION_KEY, null);
  state.els.log.querySelectorAll(".msg").forEach((m) => m.remove());
  state.els.empty.hidden = false;
}

// ------------------------------------------------------------------ persona + starters
// The persona picker and the starter questions come from the team's planning sheet
// (backend/personas.py QUESTIONS): pick who you are, get that role's questions as one-click starts.
function personaList() {
  return [...new Set(state.questions.map((q) => q.who))];
}

function renderStarters() {
  const mine = state.questions.filter((q) => q.who === state.persona).map((q) => q.ask);
  const starters = mine.length ? mine : GENERAL_STARTERS;
  state.els.starters.innerHTML = starters.map((s) =>
    `<button type="button" class="chat-starter" data-prompt="${esc(s)}">${esc(s)}</button>`).join("");
}

// ------------------------------------------------------------------ public
export function initChat({ getContext, questions = [] } = {}) {
  if (getContext) state.getContext = getContext;
  state.questions = questions;
  state.sessionId = load(SESSION_KEY);
  const personas = personaList();
  const saved = load(PERSONA_KEY);
  state.persona = personas.includes(saved) ? saved : (personas[0] || "");

  const panel = document.getElementById("chat-panel");
  panel.innerHTML = `
    <div class="chat-head">
      <h2>Planning assistant <span class="chat-mode" id="chat-mode"></span></h2>
      <div class="chat-head-actions">
        <button type="button" class="link-btn" id="chat-new" title="Start a new conversation">New</button>
      </div>
    </div>
    ${personas.length ? `<div class="chat-toolbar">
      <label for="chat-persona">I am planning as</label>
      <select id="chat-persona">${personas.map((p) =>
        `<option value="${esc(p)}" ${p === state.persona ? "selected" : ""}>${esc(p)}</option>`).join("")}</select>
    </div>` : ""}
    <div class="chat-log" id="chat-log" aria-live="polite">
      <div class="chat-empty" id="chat-empty">
        <p class="muted">Ask about heat, who's exposed, or where help should go. The assistant can move the map,
          outline blocks and number its recommendations.</p>
        <div class="chat-starters" id="chat-starters" aria-label="Suggested questions"></div>
      </div>
    </div>
    <form class="chat-form" id="chat-form">
      <label class="sr-only" for="chat-input">Your message</label>
      <textarea id="chat-input" rows="2" placeholder="Ask a question…  (Enter to send, Shift+Enter for a new line)"></textarea>
      <button type="submit" class="send-btn" id="chat-send">Send</button>
    </form>`;
  state.els = {
    panel, log: panel.querySelector("#chat-log"), empty: panel.querySelector("#chat-empty"),
    input: panel.querySelector("#chat-input"), send: panel.querySelector("#chat-send"),
    mode: panel.querySelector("#chat-mode"), starters: panel.querySelector("#chat-starters"),
  };
  renderStarters();
  panel.querySelector("#chat-form").addEventListener("submit", (e) => { e.preventDefault(); send(state.els.input.value); });
  state.els.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(state.els.input.value); }
  });
  panel.querySelector("#chat-new").addEventListener("click", newChat);
  panel.querySelector("#chat-persona")?.addEventListener("change", (e) => {
    state.persona = e.target.value;
    store(PERSONA_KEY, state.persona);
    renderStarters();
  });
  // Starters fill the box rather than sending, so the planner can adjust the wording first.
  state.els.starters.addEventListener("click", (e) => {
    const b = e.target.closest("[data-prompt]");
    if (!b) return;
    state.els.input.value = b.dataset.prompt;
    state.els.input.focus();
  });
}

/** Send a question (e.g. from "Ask the agent about this block"). The caller shows the tab. */
export function askAgent(text, { sendNow = true } = {}) {
  if (sendNow) send(text);
  else { state.els.input.value = text; state.els.input.focus(); }
}
