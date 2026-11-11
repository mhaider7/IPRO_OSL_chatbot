/**
 * ui.js — all DOM rendering for the chat app.
 * No fetch calls and no conversation state (that lives in app.js). The only state
 * kept here is presentational: the text of a reply that is still streaming in
 * (`streams`) and a counter that gives each email draft unique element ids.
 *
 * Hawk's messages are formatted with format.js (markdown -> DOM nodes, never HTML);
 * the student's own messages are shown exactly as typed.
 */

import { formatInto } from "./format.js";

const $ = (id) => document.getElementById(id);
let draftNumber = 0;
const streams = new WeakMap();   // streaming bubble -> { text, frame }

const MIN_STAGE_MS = 600;           // shortest time a stage text stays up (never delays the reply)
const stageTimes = new WeakMap();   // stage element -> { shownAt, queued, timer }

export function scroll() {
  const body = $("chat-body");
  if (body) body.scrollTop = body.scrollHeight;
}

function attachSources(bubble, sources) {
  for (const source of sources) {
    if (typeof source !== "string" || !source.trim()) continue;
    // Handbook cites are plain labels like "(student org handbook, p. 28)".
    // Only real https URLs become links; never invent a link from a cite string.
    let href = null;
    if (/^https:\/\//i.test(source)) {
      try {
        const url = new URL(source);
        if (url.protocol === "https:" && url.hostname.includes(".")) href = url.href;
      } catch { /* plain text below */ }
    }
    if (href) {
      const link = document.createElement("a");
      link.className = "source";
      link.href = href;
      link.textContent = source;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      bubble.append(link);
    } else {
      const label = document.createElement("span");
      label.className = "source";
      label.textContent = source;
      bubble.append(label);
    }
  }
}

export function renderMessage(role, text, sources = []) {
  const row = document.createElement("div");
  row.className = `message ${role}`;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  if (role === "bot") formatInto(bubble, text);   // Hawk writes markdown; user text stays as typed
  else bubble.textContent = text;
  attachSources(bubble, sources);
  row.append(bubble);
  $("messages").append(row);
  scroll();
}

// The reply's placeholder until the first token arrives. While typing it is drawn as a
// small pill: a spinning ring plus what Hawk is doing ("Searching the handbook…"), set
// by setStage from the server's stage events. If no stage ever comes (the email-draft
// path), it shows typing dots instead. Ring, dots and text are hidden from screen
// readers; #status announces the same text. aria-busy asks screen readers to wait for
// the finished reply instead of re-reading the bubble every time it is re-rendered;
// finishBotBubble clears it.
export function startBotBubble() {
  const row = document.createElement("div");
  row.className = "message bot";
  row.setAttribute("aria-busy", "true");
  const bubble = document.createElement("div");
  bubble.className = "bubble typing";
  const dots = document.createElement("span");
  dots.className = "dots";
  dots.setAttribute("aria-hidden", "true");
  dots.append(...[1, 2, 3].map(() => document.createElement("span")));
  const stage = document.createElement("span");
  stage.className = "stage";
  stage.setAttribute("aria-hidden", "true");
  bubble.append(dots, stage);
  row.append(bubble);
  $("messages").append(row);
  scroll();
  return { row, bubble };
}

// Show what Hawk is doing in the pill and announce it in #status. Ignored once the
// reply has started (the first token replaces the pill).
export function setStage(bubble, text) {
  const stage = bubble.querySelector(".stage");
  if (!stage || streams.has(bubble)) return;
  setStatus(text);   // screen readers hear every stage right away
  holdThenShow(stage, text);
}

// The pill's text changes at most every MIN_STAGE_MS, so a stage that's over at once
// ("Searching…", ~0.05s) is still readable instead of a flicker. The reply itself is
// never held back: the first token replaces the pill whatever it is showing.
function holdThenShow(stage, text) {
  let t = stageTimes.get(stage);
  if (!t) stageTimes.set(stage, (t = { shownAt: -Infinity, queued: null, timer: 0 }));
  const wait = t.shownAt + MIN_STAGE_MS - performance.now();
  if (wait <= 0 && !t.timer) {
    stage.textContent = text;
    t.shownAt = performance.now();
    return;
  }
  t.queued = text;
  if (!t.timer) {
    t.timer = setTimeout(() => {
      t.timer = 0;
      stage.textContent = t.queued;
      t.shownAt = performance.now();
    }, wait);
  }
}

// Tokens are collected and the whole reply is re-formatted at most once per frame,
// so a half-written list or **bold** settles into place as more text arrives.
export function appendToken(bubble, token) {
  let stream = streams.get(bubble);
  if (!stream) streams.set(bubble, (stream = { text: "", frame: 0 }));
  stream.text += token;
  if (!stream.frame) {
    stream.frame = requestAnimationFrame(() => {
      stream.frame = 0;
      renderStream(bubble, stream.text);
    });
  }
}

function renderStream(bubble, text) {
  bubble.classList.remove("typing");
  formatInto(bubble, text);
  scroll();
}

export function finishBotBubble(row, sources = []) {
  const bubble = row.querySelector(".bubble");
  const stream = streams.get(bubble);
  if (stream) {   // render now: the last frame may not have run (or the tab is hidden)
    cancelAnimationFrame(stream.frame);
    streams.delete(bubble);
    renderStream(bubble, stream.text);
  }
  row.removeAttribute("aria-busy");
  if (bubble) attachSources(bubble, sources);
  scroll();
}

// Note: the To/Subject line below is the one place model text goes into innerHTML.
// The iframe keeps it away from the host page, but it should be built with
// textContent like everything else (open item in the widget notes' idea backlog).
export function renderEmailCard(email) {
  document.querySelectorAll(".email-draft").forEach(c => c.remove());

  const n = ++draftNumber;
  const bodyId  = `email-body-${n}`;
  const replyId = `email-reply-${n}`;

  const card = document.createElement("section");
  card.className = "email-draft";

  const heading = document.createElement("h3");
  heading.textContent = "Draft email to OSL";

  const meta = document.createElement("div");
  meta.className = "email-meta";
  meta.innerHTML = `<p>To: <span>${email.to}</span></p><p>Subject: <span>${email.subject}</span></p>`;

  const bodyLabel = document.createElement("label");
  bodyLabel.htmlFor = bodyId;
  bodyLabel.textContent = "Your message";

  const bodyEl = document.createElement("textarea");
  bodyEl.id = bodyId;
  bodyEl.value = email.body;
  bodyEl.maxLength = 24000;

  const replyLabel = document.createElement("label");
  replyLabel.htmlFor = replyId;
  replyLabel.textContent = "Your reply-to address";
  replyLabel.style.marginTop = "10px";

  const replyEl = document.createElement("input");
  replyEl.id = replyId;
  replyEl.type = "email";
  replyEl.placeholder = "you@hawk.iit.edu";
  replyEl.maxLength = 254;

  const actions = document.createElement("div");
  actions.className = "email-actions";

  const copyBtn = document.createElement("button");
  copyBtn.className = "btn-copy";
  copyBtn.type = "button";
  copyBtn.textContent = "Copy draft";
  copyBtn.addEventListener("click", () => {
    const text = `To: ${email.to}\nSubject: ${email.subject}\nReply-to: ${replyEl.value || "(add your address)"}\n\n${bodyEl.value}`;
    navigator.clipboard.writeText(text).then(() => {
      copyBtn.textContent = "Copied!";
      setTimeout(() => { copyBtn.textContent = "Copy draft"; }, 2000);
    });
  });

  const mailtoLink = document.createElement("a");
  mailtoLink.className = "btn-mailto";
  mailtoLink.textContent = "Open in mail app";
  mailtoLink.target = "_blank";
  mailtoLink.rel = "noopener noreferrer";
  mailtoLink.addEventListener("click", () => {
    mailtoLink.href = `mailto:${email.to}?subject=${encodeURIComponent(email.subject)}&body=${encodeURIComponent(bodyEl.value)}`;
  });
  mailtoLink.href = "#";

  actions.append(copyBtn, mailtoLink);

  const note = document.createElement("p");
  note.className = "email-note";
  note.textContent = "This is a draft — Hawk does not send email. Edit above, then copy it or open your mail app to send.";

  card.append(heading, meta, bodyLabel, bodyEl, replyLabel, replyEl, actions, note);
  $("messages").append(card);
  scroll();
}

export function setStatus(text) {
  $("status").textContent = text;
}

export function setError(text) {
  $("error").textContent = text;
  $("error").hidden = false;
  $("retry").hidden = false;
}

export function clearError() {
  $("error").hidden = true;
  $("retry").hidden = true;
}

export function setControls({ busy, pending, mailSending }) {
  const off = busy || pending || mailSending;
  for (const id of ["send", "message", "choose-email"]) {
    const el = $(id);
    if (el) el.disabled = off;
  }
  $("reset").disabled = mailSending;
  for (const chip of document.querySelectorAll("[data-question]")) chip.disabled = off;
  $("retry").disabled = busy;
  $("status").textContent = busy ? "Hawk is thinking…" : "";
}

export function showChips(visible) {
  $("chips").hidden = !visible;
}

export function showOptions(visible) {
  $("options").hidden = !visible;
}
