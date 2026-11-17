// Chat panel: talks to POST /api/chat (server-sent events) and runs the agent's UI tools.
//
// Stream events (see portal/backend/chat.py):
//   session {session_id, mode} · text {delta} · tool_call {id, name, args, ui}
//   tool_result {id, name, response} · ui_action {id, name, args} · error {message} · done {}
//
// Every message carries `context` (what the planner sees: view, layer, selected block, map
// centre) so the agent can resolve "this block" or "here".
import { runUiAction } from "./ui_tools.js";

const SESSION_KEY = "heat-chat-session";
const SUGGESTIONS = [
  "How hot is the block I've selected, and why?",
  "Which blocks have the most residents 65+ in the hottest spots?",
  "Show me where street trees are sparse",
  "How often does Brooklyn reach 95°F?",
];

const state = { sessionId: null, busy: false, getContext: () => ({}), els: {} };
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
      body: JSON.stringify({ message: text, session_id: state.sessionId, context: state.getContext() }),
    });
    if (!r.ok || !r.body) throw new Error(`HTTP ${r.status}`);
    for await (const { event, data } of sseEvents(r)) {
      if (event === "session") {
        state.sessionId = data.session_id;
        store(SESSION_KEY, data.session_id);
        state.els.mode.textContent = data.mode === "mock" ? "mock agent" : "";
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

// ------------------------------------------------------------------ public
export function initChat({ getContext } = {}) {
  if (getContext) state.getContext = getContext;
  state.sessionId = load(SESSION_KEY);
  const panel = document.getElementById("chat-panel");
  panel.innerHTML = `
    <div class="chat-head">
      <h2>Ask the agent <span class="chat-mode" id="chat-mode"></span></h2>
      <div class="chat-head-actions">
        <button type="button" class="link-btn" id="chat-new" title="Start a new conversation">New</button>
        <button type="button" class="icon-btn" id="chat-close" aria-label="Close chat">✕</button>
      </div>
    </div>
    <div class="chat-log" id="chat-log" aria-live="polite">
      <div class="chat-empty" id="chat-empty">
        <p class="muted">Ask about heat, who's exposed, or where help should go. The agent can move the map.</p>
        ${SUGGESTIONS.map((s) => `<button type="button" class="suggestion">${esc(s)}</button>`).join("")}
      </div>
    </div>
    <form class="chat-form" id="chat-form">
      <textarea id="chat-input" rows="2" placeholder="Ask a question…  (Enter to send, Shift+Enter for a new line)"></textarea>
      <button type="submit" class="send-btn" id="chat-send">Send</button>
    </form>`;
  state.els = {
    panel, log: panel.querySelector("#chat-log"), empty: panel.querySelector("#chat-empty"),
    input: panel.querySelector("#chat-input"), send: panel.querySelector("#chat-send"),
    mode: panel.querySelector("#chat-mode"),
  };
  panel.querySelector("#chat-form").addEventListener("submit", (e) => { e.preventDefault(); send(state.els.input.value); });
  state.els.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(state.els.input.value); }
  });
  panel.querySelector("#chat-new").addEventListener("click", newChat);
  panel.querySelector("#chat-close").addEventListener("click", () => toggleChat(false));
  panel.querySelectorAll(".suggestion").forEach((b) => b.addEventListener("click", () => send(b.textContent)));
  document.getElementById("chat-toggle")?.addEventListener("click", () => toggleChat());
}

export function toggleChat(open) {
  const panel = state.els.panel;
  const show = open ?? panel.hidden;
  panel.hidden = !show;
  document.body.classList.toggle("chat-open", show);
  document.getElementById("chat-toggle")?.setAttribute("aria-expanded", String(show));
  window.dispatchEvent(new Event("resize")); // let the map re-measure
  if (show) state.els.input.focus();
}

/** Open the panel and send a question (e.g. from "Ask the agent about this block"). */
export function askAgent(text, { sendNow = true } = {}) {
  toggleChat(true);
  if (sendNow) send(text);
  else state.els.input.value = text;
}
