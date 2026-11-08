/**
 * embed.js — Hawk chat widget loader.
 *
 * Add to any page with:
 *   <script src="https://HAWK_SERVER/embed.js" defer></script>
 *
 * Optional: data-avoid="<CSS selector>" names elements the launcher should stay
 * above while they're on screen, such as a cookie banner. For iit.edu (Klaro):
 *   <script src="https://HAWK_SERVER/embed.js" data-avoid=".klaro .cm-modal" defer></script>
 *
 * Runs on the host page. It draws the "Ask Hawk" launcher inside a Shadow DOM
 * (so the page's CSS can't reach it) and opens the chat in an iframe pointing
 * at this server's /chat?embed=1. The chat app itself never runs on the host page.
 *
 * Messages between this script and the iframe (all { type } objects):
 *   iframe -> page   "hawk:ready"  app finished loading
 *                    "hawk:close"  × button or Escape pressed inside the panel
 *   page -> iframe   "hawk:focus"  panel opened; move focus to the message box
 */
(() => {
  "use strict";

  const TAG = "hawk-chat-widget";
  if (document.querySelector(TAG)) return;   // snippet included twice

  const script =
    document.currentScript || document.querySelector('script[src$="/embed.js"]');
  if (!script) return;
  const HAWK_ORIGIN = new URL(script.src, location.href).origin;
  const PANEL_URL = `${HAWK_ORIGIN}/chat?embed=1`;
  const AVOID = (script.dataset.avoid || "").trim();
  const OPEN_KEY = "hawk-widget:open";   // host-page sessionStorage: reopen after navigating
  const GAP = 20;        // px between the launcher and the viewport edges
  const LAUNCHER = 52;   // px launcher height

  // All panel/launcher geometry lives here and keys off .widget[data-state].
  // States: "closed" | "open". A future "expanded" state only needs new rules.
  const CSS = `
    :host { all: initial !important; }
    *, *::before, *::after { box-sizing: border-box; }
    [hidden] { display: none !important; }

    .widget {
      --crimson: #a6192e;
      --crimson-dark: #8b1427;
      --gap: ${GAP}px;
      --launcher: ${LAUNCHER}px;
      --avoid: 0px;   /* height of a banner covering the bottom corner (data-avoid) */
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }

    .launcher {
      all: unset;
      box-sizing: border-box;
      position: fixed;
      right: var(--gap);
      bottom: calc(var(--gap) + var(--avoid));
      z-index: 2147483001;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      height: var(--launcher);
      min-width: var(--launcher);
      padding: 0 20px 0 16px;
      border-radius: 999px;
      background: var(--crimson);
      color: #fff;
      font: 600 15px/1 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: normal;
      cursor: pointer;
      box-shadow: 0 10px 28px -12px rgba(166, 25, 46, .75), 0 2px 6px rgba(0, 0, 0, .15);
      transition: background .15s, bottom .25s ease;
    }
    .launcher:hover { background: var(--crimson-dark); }
    .launcher:focus-visible {
      outline: 3px solid var(--crimson);
      outline-offset: 3px;
      box-shadow: 0 0 0 3px #fff;
    }
    .launcher svg { display: block; width: 22px; height: 22px; flex: none; }
    .icon-close { display: none !important; }

    .widget[data-state="open"] .launcher { padding: 0; }
    .widget[data-state="open"] .label,
    .widget[data-state="open"] .icon-chat { display: none !important; }
    .widget[data-state="open"] .icon-close { display: block !important; }

    .panel {
      position: fixed;
      z-index: 2147483000;
      right: var(--gap);
      bottom: calc(var(--gap) + var(--launcher) + 12px + var(--avoid));
      width: min(400px, calc(100vw - 32px));
      height: min(600px, calc(100vh - var(--launcher) - 52px - var(--avoid)));
      height: min(600px, calc(100dvh - var(--launcher) - 52px - var(--avoid)));
      background: #fff;
      border: 1px solid #e5e3df;
      border-radius: 14px;
      overflow: hidden;
      box-shadow: 0 24px 60px -20px rgba(26, 25, 22, .45), 0 4px 14px -6px rgba(26, 25, 22, .15);
      animation: hawk-pop .18s ease-out both;
      transition: bottom .25s ease, height .25s ease;
    }
    .panel iframe { display: block; width: 100%; height: 100%; border: 0; }

    @keyframes hawk-pop {
      from { opacity: 0; transform: translateY(10px) scale(.98); }
      to   { opacity: 1; transform: none; }
    }

    /* Phones and very short windows: the open panel fills the viewport and
       carries its own close button, so the launcher steps aside. */
    @media (max-width: 520px), (max-height: 480px) {
      .widget[data-state="open"] .panel {
        inset: 0;
        width: 100%;
        height: 100%;
        border: 0;
        border-radius: 0;
      }
      .widget[data-state="open"] .launcher { display: none !important; }
    }

    @media (prefers-reduced-motion: reduce) {
      .panel { animation: none; transition: none; }
      .launcher { transition: none; }
    }
  `;

  const CHAT_ICON = `<svg class="icon-chat" viewBox="0 0 24 24" fill="none" aria-hidden="true">
    <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v8a2.5 2.5 0 0 1-2.5 2.5H10l-4.2 3.6c-.5.4-1.3.1-1.3-.6V16A2.5 2.5 0 0 1 4 13.5v-8Z"
      stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>`;
  const CLOSE_ICON = `<svg class="icon-close" viewBox="0 0 24 24" fill="none" aria-hidden="true">
    <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>`;

  function mount() {
    const host = document.createElement(TAG);
    const root = host.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>${CSS}</style>
      <div class="widget" data-state="closed">
        <button class="launcher" type="button" aria-expanded="false" aria-controls="hawk-panel">
          ${CHAT_ICON}<span class="label">Ask Hawk</span>${CLOSE_ICON}
        </button>
        <div class="panel" id="hawk-panel" role="dialog" aria-label="Hawk, OSL chat assistant" hidden></div>
      </div>`;

    const widget   = root.querySelector(".widget");
    const launcher = root.querySelector(".launcher");
    const panel    = root.querySelector(".panel");

    let state = "closed";
    let iframe = null;        // created on first open so pages that never use Hawk don't load it
    let ready = false;        // iframe app has said "hawk:ready"
    let focusPending = false; // panel opened before the app was ready

    function setState(next) {
      state = next;
      widget.dataset.state = next;
      const isOpen = next !== "closed";
      panel.hidden = !isOpen;
      launcher.setAttribute("aria-expanded", String(isOpen));
      if (isOpen) launcher.setAttribute("aria-label", "Close Hawk chat");
      else launcher.removeAttribute("aria-label");   // name falls back to "Ask Hawk"
      try { sessionStorage.setItem(OPEN_KEY, isOpen ? "1" : "0"); } catch { /* storage blocked */ }
    }

    function post(type) {
      iframe?.contentWindow?.postMessage({ type }, HAWK_ORIGIN);
    }

    function focusPanel() {
      if (!ready) { focusPending = true; return; }
      iframe.focus();
      post("hawk:focus");
    }

    // moveFocus is false when reopening after a page navigation, so the page
    // doesn't yank keyboard focus into the chat as it loads.
    function open(moveFocus = true) {
      if (!iframe) {
        iframe = document.createElement("iframe");
        iframe.src = PANEL_URL;
        iframe.title = "Hawk chat";
        iframe.allow = "clipboard-write";
        panel.append(iframe);
      }
      setState("open");
      if (moveFocus) focusPanel();
    }

    function close() {
      focusPending = false;
      setState("closed");
      launcher.focus();
    }

    launcher.addEventListener("click", () => (state === "closed" ? open() : close()));

    // Escape while focus is on the launcher. Escape inside the iframe arrives as "hawk:close".
    root.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && state !== "closed") { e.stopPropagation(); close(); }
    });

    window.addEventListener("message", (e) => {
      if (!iframe || e.source !== iframe.contentWindow || e.origin !== HAWK_ORIGIN) return;
      const type = e.data && e.data.type;
      if (type === "hawk:ready") {
        ready = true;
        if (focusPending && state !== "closed") { focusPending = false; focusPanel(); }
      } else if (type === "hawk:close" && state !== "closed") {
        close();
      }
    });

    // ── Stay above banners named in data-avoid (e.g. a cookie notice) ──
    // Re-measured whenever the page changes, since banners usually appear
    // after load and are removed or hidden when dismissed.
    function avoidOffset() {
      let elements;
      try { elements = document.querySelectorAll(AVOID); }
      catch { console.warn("Hawk: invalid data-avoid selector", AVOID); return 0; }
      const vh = window.innerHeight;
      const right = window.innerWidth - GAP;   // the launcher's corner, before any shift
      const left = right - launcher.offsetWidth;
      const top = vh - GAP - LAUNCHER;
      let offset = 0;
      for (const el of elements) {
        const r = el.getBoundingClientRect();
        if (!r.width || !r.height || getComputedStyle(el).visibility === "hidden") continue;
        const coversCorner = r.top < vh - GAP && r.bottom > top && r.left < right && r.right > left;
        if (coversCorner) offset = Math.max(offset, vh - r.top);
      }
      return Math.min(offset, Math.max(0, vh - LAUNCHER - 2 * GAP));   // keep the launcher on screen
    }

    let measureQueued = false;
    function measure() {
      if (measureQueued) return;
      measureQueued = true;
      requestAnimationFrame(() => {
        measureQueued = false;
        widget.style.setProperty("--avoid", `${Math.round(avoidOffset())}px`);
      });
    }

    let settleTimer = 0;
    function measureNowAndAfterAnimations() {
      measure();
      clearTimeout(settleTimer);
      settleTimer = setTimeout(measure, 400);   // banners often slide in or out
    }

    if (AVOID) {
      new MutationObserver(measureNowAndAfterAnimations)
        .observe(document.body, { childList: true, subtree: true, attributes: true,
                                  attributeFilter: ["class", "style", "hidden", "open"] });
      window.addEventListener("resize", measure);
      measure();
    }

    document.body.append(host);

    // Reopen (without taking focus) if the panel was open on the previous page.
    let wasOpen = false;
    try { wasOpen = sessionStorage.getItem(OPEN_KEY) === "1"; } catch { /* storage blocked */ }
    if (wasOpen) open(false);
  }

  if (document.body) mount();
  else document.addEventListener("DOMContentLoaded", mount, { once: true });
})();
