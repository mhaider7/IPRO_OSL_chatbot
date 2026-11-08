/**
 * ui.js — all DOM rendering.
 * No fetch calls, no state. Pure functions that read/write the DOM.
 */

const $ = (id) => document.getElementById(id);
let draftNumber = 0;

export function scroll() {
  const body = $("chat-body");
  if (body) body.scrollTop = body.scrollHeight;
}

function attachSources(row, sources) {
  for (const source of sources) {
    try {
      const url = new URL(source.startsWith("https://") ? source : `https://${source}`);
      if (url.protocol !== "https:" || !url.hostname.includes(".")) continue;
      const link = document.createElement("a");
      link.className = "source";
      link.href = url.href;
      link.textContent = source;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      row.append(link);
    } catch { /* skip invalid URLs */ }
  }
}

export function renderMessage(role, text, sources = []) {
  const row = document.createElement("div");
  row.className = `message ${role}`;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  row.append(bubble);
  attachSources(row, sources);
  $("messages").append(row);
  scroll();
}

// A reply bubble that shows animated typing dots until the first token arrives.
// The dots are hidden from screen readers; #status announces "Hawk is thinking…".
export function startBotBubble() {
  const row = document.createElement("div");
  row.className = "message bot";
  const bubble = document.createElement("div");
  bubble.className = "bubble typing";
  const dots = document.createElement("span");
  dots.className = "dots";
  dots.setAttribute("aria-hidden", "true");
  dots.append(...[1, 2, 3].map(() => document.createElement("span")));
  bubble.append(dots);
  row.append(bubble);
  $("messages").append(row);
  scroll();
  return { row, bubble };
}

export function appendToken(bubble, token) {
  if (bubble.classList.contains("typing")) {
    bubble.classList.remove("typing");
    bubble.textContent = "";
  }
  bubble.textContent += token;
}

export function finishBotBubble(row, sources = []) {
  attachSources(row, sources);
  scroll();
}

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
