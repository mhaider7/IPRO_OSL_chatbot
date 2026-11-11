/* Conversation survives a reload via sessionStorage (this tab only); no localStorage, cookies, or server-side storage. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const STORAGE_KEY = "hawk-session";
  let history = [], escalating = false, mode = "conversation", busy = false, pending = null, controller = null, epoch = 0, mailSending = false, completed = false;
  let draftNumber = 0;
  const greeting = "Hi, I'm Hawk — the OSL assistant. Ask me about student organizations, events, funding, space reservations, or any OSL policy. Please do not share passwords, verification codes, or student IDs.";

  function save() {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ history, escalating, mode, offerOptions: !$("options").hidden, completed }));
    } catch { /* Private browsing or storage disabled; the conversation just will not survive a reload. */ }
  }
  function restore() {
    let saved;
    try { saved = JSON.parse(sessionStorage.getItem(STORAGE_KEY)); } catch { return false; }
    if (!saved || !Array.isArray(saved.history) || saved.history.length === 0) return false;
    const validHistory = saved.history.every(m => m && (m.role === "user" || m.role === "bot") && typeof m.text === "string");
    if (!validHistory || (saved.mode !== "conversation" && saved.mode !== "diagnose") || typeof saved.escalating !== "boolean" || typeof saved.completed !== "boolean") return false;
    // A finished chat (advice given, or an email drafted) intentionally does not come back after a reload.
    if (saved.completed) return false;
    history = saved.history; escalating = saved.escalating; mode = saved.mode;
    $("messages").replaceChildren();
    message("bot", greeting);
    for (const turn of history) message(turn.role, turn.text);
    $("chips").hidden = true;
    $("options").hidden = !saved.offerOptions;
    controls();
    return true;
  }

  function message(role, text, sources = []) {
    const row = document.createElement("div");
    row.className = `message ${role}`;
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = text;
    for (const source of sources) {
      if (typeof source !== "string" || !source.trim()) continue;
      // Handbook cites are plain labels; only real https URLs become links.
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
    row.append(bubble);
    $("messages").append(row);
    scroll();
  }
  function scroll() { const b = document.getElementById("chat-body"); if (b) b.scrollTop = b.scrollHeight; }
  function controls() {
    for (const id of ["send", "message", "choose-email"]) { const el = $(id); if (el) el.disabled = busy || pending !== null || mailSending; }
    $("reset").disabled = mailSending;
    for (const chip of document.querySelectorAll("[data-question]")) chip.disabled = busy || pending !== null || mailSending;
    $("retry").disabled = busy;
    $("status").textContent = busy ? "Hawk is thinking…" : "";
  }
  function emailCard(email) {
    // Draft only — no sending. Show the draft for the student to copy or open in their mail app.
    document.querySelectorAll(".email-draft").forEach(c => c.remove());

    const n = ++draftNumber;
    const bodyId = `email-body-${n}`;
    const replyId = `reply-addr-${n}`;

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
      navigator.clipboard.writeText(text).then(() => { copyBtn.textContent = "Copied!"; setTimeout(() => { copyBtn.textContent = "Copy draft"; }, 2000); });
    });

    const mailtoLink = document.createElement("a");
    mailtoLink.className = "btn-mailto";
    mailtoLink.textContent = "Open in mail app";
    mailtoLink.setAttribute("role", "button");
    // Build mailto href dynamically on click so it picks up any body edits
    mailtoLink.addEventListener("click", () => {
      mailtoLink.href = `mailto:${email.to}?subject=${encodeURIComponent(email.subject)}&body=${encodeURIComponent(bodyEl.value)}`;
    });
    mailtoLink.href = "#";
    mailtoLink.target = "_blank";
    mailtoLink.rel = "noopener noreferrer";

    actions.append(copyBtn, mailtoLink);

    const note = document.createElement("p");
    note.className = "email-note";
    note.textContent = "This is a draft — Hawk does not send email. Edit the message above, then copy it or open your mail app to send.";

    card.append(heading, meta, bodyLabel, bodyEl, replyLabel, replyEl, actions, note);
    $("messages").append(card);
    scroll();
  }
  function validChat(data) {
    return typeof data.reply === "string" && Array.isArray(data.sources) && data.sources.every(s => typeof s === "string") &&
      typeof data.offer_options === "boolean" && typeof data.escalate === "boolean" && (data.escalate ? ["fixed_topic", "low_confidence", "user_request"].includes(data.escalation_reason) : data.escalation_reason === null);
  }
  function validEscalation(data) {
    return (data.email === null && typeof data.clarifying_question === "string" && data.clarifying_question.length > 0) ||
      (data.clarifying_question === null && data.email && ["to", "subject", "body"].every(key => typeof data.email[key] === "string"));
  }
  async function execute() {
    if (!pending || busy) return;
    const version = epoch;
    busy = true;
    $("error").hidden = true; $("retry").hidden = true; controls();
    controller = new AbortController();
    const timer = setTimeout(() => controller?.abort(), 15000);
    try {
      const path = pending;
      const request = { messages: history };
      const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request), signal: controller.signal });
      const data = await response.json();
      if (version !== epoch) return;
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Hawk could not respond. Please try again.");
      if (path === "/api/chat") {
        if (!validChat(data)) throw new Error("Hawk returned an unexpected reply. Please try again.");
        history.push({ role: "bot", text: data.reply });
        message("bot", data.reply, data.sources);
        pending = null;
        $("options").hidden = !data.offer_options;
        if (mode === "diagnose") completed = true;
        save();
      } else {
        if (!validEscalation(data)) throw new Error("Hawk returned an unexpected draft. Please try again.");
        if (data.clarifying_question) {
          history.push({ role: "bot", text: data.clarifying_question });
          message("bot", data.clarifying_question);
          save();
        } else {
          completed = true;
          save();
          emailCard(data.email);
        }
        pending = null;
      }
    } catch (error) {
      if (version !== epoch) return;
      $("error").textContent = error.name === "AbortError" ? "Hawk took too long to respond. Try again or start a new chat." : (error instanceof TypeError ? "Cannot reach Hawk. Check that the local server is running, then try again." : error.message);
      $("error").hidden = false; $("retry").hidden = false;
    } finally {
      clearTimeout(timer);
      if (version === epoch) {
        busy = false; controller = null; controls();
        if (pending && $("error").hidden) execute();
        else if (!pending) $("message").focus();
      }
    }
  }
  function send(text) {
    text = text.trim();
    if (!text || busy || pending || mailSending) return;
    // Leave room for the reply and an automatic escalation question.
    if (history.length >= 56 || history.reduce((n, m) => n + m.text.length, text.length) > 21000) {
      $("error").textContent = "This conversation is full. Finish reviewing any email, then start a new chat.";
      $("error").hidden = false; return;
    }
    history.push({ role: "user", text });
    message("user", text);
    save();
    $("message").value = ""; $("chips").hidden = true;
    pending = escalating ? "/api/escalate" : "/api/chat";
    execute();
  }
  function reset() {
    if (mailSending) return;
    epoch++; controller?.abort(); controller = null;
    history = []; escalating = false; mode = "conversation"; pending = null; busy = false; completed = false;
    try { sessionStorage.removeItem(STORAGE_KEY); } catch { /* Nothing to clear if storage is unavailable. */ }
    $("messages").replaceChildren(); $("message").value = "";
    $("error").hidden = true; $("retry").hidden = true; $("chips").hidden = false; $("options").hidden = true;
    message("bot", greeting); controls(); $("message").focus();
  }
  // Chat is always full-page — open() is a no-op kept for restore() compatibility.
  function open() { $("message").focus(); }

  // Auto-resize textarea as the user types.
  $("message").addEventListener("input", function () {
    this.style.height = "auto";
    this.style.height = Math.min(this.scrollHeight, 120) + "px";
  });

  $("reset").addEventListener("click", reset);
  $("retry").addEventListener("click", execute);
  $("composer").addEventListener("submit", event => { event.preventDefault(); send($("message").value); });
  $("choose-email").addEventListener("click", () => { if (busy || pending || mailSending) return; escalating = true; pending = "/api/escalate"; execute(); });
  document.querySelectorAll("[data-question]").forEach(button => button.addEventListener("click", () => send(button.dataset.question)));
  if (!restore()) reset();
})();
