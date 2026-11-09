/**
 * app.js — state, event listeners, orchestration.
 * Imports api.js for fetch calls and ui.js for DOM updates.
 */

import { chatStreamRequest, escalateRequest, makeAbortController, warmUpRequest } from "./api.js";
import { EMBEDDED, initEmbedMode } from "./embed-mode.js";
import {
  renderMessage, renderEmailCard,
  startBotBubble, setStage, appendToken, finishBotBubble,
  setStatus, setError, clearError,
  setControls, showChips, showOptions, scroll,
} from "./ui.js";

// ── State ────────────────────────────────────────────────────
const STORAGE_KEY = "hawk-session";
const GREETING =
  "Hi, I'm Hawk — the OSL assistant. Ask me about student organizations, " +
  "events, funding, space reservations, or any OSL policy. " +
  "Please do not share passwords, verification codes, or student IDs.";

// Text for the stages the server reports while it works on a reply (see api.js).
const STAGES = {
  searching: "Searching the handbook…",
  loading:   "Starting up…",
  writing:   "Writing an answer…",
};

let history    = [];
let escalating = false;
let busy       = false;
let pending    = null;   // "/api/chat" | "/api/escalate" | null
let abortCtl   = null;   // current AbortController
let epoch      = 0;      // incremented on reset to cancel stale callbacks
let completed  = false;  // true once a full answer or email draft was shown

// ── Validation ───────────────────────────────────────────────
function validChat(data) {
  return (
    typeof data.reply === "string" &&
    Array.isArray(data.sources) &&
    data.sources.every((s) => typeof s === "string") &&
    typeof data.offer_options === "boolean" &&
    typeof data.escalate === "boolean" &&
    (data.escalate
      ? ["low_confidence", "user_request"].includes(data.escalation_reason)
      : data.escalation_reason === null)
  );
}

function validEscalation(data) {
  return (
    (data.email === null &&
      typeof data.clarifying_question === "string" &&
      data.clarifying_question.length > 0) ||
    (data.clarifying_question === null &&
      data.email &&
      ["to", "subject", "body"].every((k) => typeof data.email[k] === "string"))
  );
}

// ── Session persistence ───────────────────────────────────────
function save() {
  try {
    sessionStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        history,
        escalating,
        offerOptions: !document.getElementById("options").hidden,
        completed,
      })
    );
  } catch { /* private browsing — conversation won't survive a reload */ }
}

function restore() {
  let saved;
  try { saved = JSON.parse(sessionStorage.getItem(STORAGE_KEY)); } catch { return false; }
  if (!saved || !Array.isArray(saved.history) || saved.history.length === 0) return false;
  const validHistory = saved.history.every(
    (m) => m && (m.role === "user" || m.role === "bot") && typeof m.text === "string"
  );
  if (!validHistory || typeof saved.escalating !== "boolean" || typeof saved.completed !== "boolean") return false;
  if (saved.completed) return false;

  history    = saved.history;
  escalating = saved.escalating;

  renderMessage("bot", GREETING);
  for (const turn of history) renderMessage(turn.role, turn.text);
  showChips(false);
  showOptions(saved.offerOptions);
  controls();
  return true;
}

// ── Controls helper ───────────────────────────────────────────
function controls() {
  setControls({ busy, pending, mailSending: false });
}

// ── Core: execute a pending request ──────────────────────────
async function execute() {
  if (!pending || busy) return;
  const version = epoch;
  busy = true;
  clearError();
  controls();

  const { controller, clear } = makeAbortController();
  abortCtl = controller;

  let streamRow = null;

  try {
    if (pending === "/api/chat") {
      // ── Streaming chat ──────────────────────────────────────
      const { row, bubble } = startBotBubble();
      streamRow = row;
      let fullText = "";

      for await (const event of chatStreamRequest(history, controller.signal)) {
        if (version !== epoch) { row.remove(); return; }
        if (event.token !== undefined) {
          fullText += event.token;
          appendToken(bubble, event.token);
          scroll();
        } else if (event.stage !== undefined) {
          if (STAGES[event.stage]) setStage(bubble, STAGES[event.stage]);   // unknown stages: ignore
        } else if (event.done) {
          const text = fullText || event.reply || "";
          if (!text) throw new Error("Hawk returned an empty reply. Please try again.");
          // Low-confidence fallback ("I don't have information on that..."): the server
          // sends no tokens, only the whole reply in the done event. Show it the same way.
          if (!fullText) appendToken(bubble, text);
          history.push({ role: "bot", text: text });
          finishBotBubble(row, event.sources || []);
          showOptions(event.offer_options ?? true);
          pending = null;
          save();
        }
      }
    } else {
      // ── Non-streaming escalate ──────────────────────────────
      streamRow = startBotBubble().row;   // typing dots until the draft arrives
      const data = await escalateRequest(history, controller.signal);
      if (version !== epoch) return;
      streamRow.remove();
      if (!validEscalation(data)) throw new Error("Hawk returned an unexpected draft. Please try again.");
      if (data.clarifying_question) {
        history.push({ role: "bot", text: data.clarifying_question });
        renderMessage("bot", data.clarifying_question);
        save();
      } else {
        completed = true;
        save();
        renderEmailCard(data.email);
      }
      pending = null;
    }
  } catch (err) {
    if (version !== epoch) return;
    streamRow?.remove();
    setError(
      err.name === "AbortError"
        ? "Hawk took too long to respond. Try again or start a new chat."
        : err instanceof TypeError
          ? "Cannot reach Hawk. Check that the local server is running, then try again."
          : err.message
    );
  } finally {
    clear();
    if (version === epoch) {
      busy = false;
      abortCtl = null;
      controls();
      if (pending && document.getElementById("error").hidden) execute();
      else if (!pending) document.getElementById("message").focus();
    }
  }
}

// ── Send a user message ───────────────────────────────────────
function send(text) {
  text = text.trim();
  if (!text || busy || pending) return;

  if (
    history.length >= 56 ||
    history.reduce((n, m) => n + m.text.length, text.length) > 21000
  ) {
    setError("This conversation is full. Start a new chat.");
    return;
  }

  history.push({ role: "user", text });
  renderMessage("user", text);
  save();

  document.getElementById("message").value = "";
  document.getElementById("message").style.height = "auto";
  showChips(false);

  pending = escalating ? "/api/escalate" : "/api/chat";
  execute();
}

// ── Reset ─────────────────────────────────────────────────────
function reset({ focus = true } = {}) {
  epoch++;
  abortCtl?.abort();
  abortCtl = null;
  history = []; escalating = false; pending = null; busy = false; completed = false;

  try { sessionStorage.removeItem(STORAGE_KEY); } catch { /* ignore */ }

  document.getElementById("messages").replaceChildren();
  document.getElementById("message").value = "";
  document.getElementById("message").style.height = "auto";

  clearError();
  showChips(true);
  showOptions(false);
  renderMessage("bot", GREETING);
  controls();
  if (focus) document.getElementById("message").focus();
}

// ── Event listeners ───────────────────────────────────────────
document.getElementById("reset").addEventListener("click", () => reset());

document.getElementById("retry").addEventListener("click", execute);

document.getElementById("composer").addEventListener("submit", (e) => {
  e.preventDefault();
  send(document.getElementById("message").value);
});

document.getElementById("choose-email").addEventListener("click", () => {
  if (busy || pending) return;
  escalating = true;
  pending = "/api/escalate";
  execute();
});

document.querySelectorAll("[data-question]").forEach((btn) =>
  btn.addEventListener("click", () => send(btn.dataset.question))
);

// Enter sends, Shift+Enter adds a new line. On touch screens Enter always adds
// a new line (phone keyboards have no Shift+Enter) and the send button sends.
const touchScreen = window.matchMedia("(pointer: coarse)");
document.getElementById("message").addEventListener("keydown", (e) => {
  if (e.key !== "Enter" || e.shiftKey || touchScreen.matches) return;
  if (e.isComposing || e.keyCode === 229) return;   // confirming an IME candidate, not sending
  e.preventDefault();
  if (e.target.value.trim()) document.getElementById("composer").requestSubmit();
});

// Auto-resize textarea
document.getElementById("message").addEventListener("input", function () {
  this.style.height = "auto";
  this.style.height = Math.min(this.scrollHeight, 120) + "px";
});

// ── Init ──────────────────────────────────────────────────────

// In the widget panel, embed.js decides when focus moves into the chat.
if (!restore()) reset({ focus: !EMBEDDED });
initEmbedMode();
// The panel only loads when someone opens Hawk: get the model ready while they type.
if (EMBEDDED) warmUpRequest();
