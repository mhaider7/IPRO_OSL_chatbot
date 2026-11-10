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
 *                    "hawk:close"  minimize button or Escape pressed inside the panel
 *                                  (minimizes; the conversation is kept)
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
      height: var(--launcher);
      min-width: var(--launcher);
      padding: 0 20px 0 15px;
      border-radius: 999px;
      background: var(--crimson);
      color: #fff;
      font: 600 15px/1 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: normal;
      cursor: pointer;
      box-shadow: 0 10px 28px -12px rgba(166, 25, 46, .75), 0 2px 6px rgba(0, 0, 0, .15);
      transition: background .15s, bottom .25s ease, padding .24s ease;
    }
    .launcher:hover { background: var(--crimson-dark); }
    /* While following a banner that's sliding in or out (data-avoid), move with it
       frame by frame instead of gliding after it, so the two never overlap. */
    .widget.following .launcher { transition: background .15s, padding .24s ease; }
    .widget.following .panel { transition: none; }
    .launcher:focus-visible {
      outline: 3px solid var(--crimson);
      outline-offset: 3px;
      box-shadow: 0 0 0 3px #fff;
    }

    /* Pill <-> circle: the label collapses (its width is measured into --label-w so
       the collapse starts at once), the padding evens out to a 52px circle, and the
       chat icon cross-fades into a chevron that turns into place. */
    .icons { position: relative; flex: none; width: 22px; height: 22px; }
    .icons svg {
      position: absolute; inset: 0;
      display: block; width: 22px; height: 22px;
      transition: opacity .18s ease, transform .24s ease;
    }
    .icon-minimize { opacity: 0; transform: rotate(-90deg) scale(.7); }
    .label {
      display: inline-block;
      max-width: var(--label-w, 8em);
      margin-left: 8px;
      overflow: hidden;
      white-space: nowrap;
      transition: max-width .24s ease, margin-left .24s ease, opacity .14s ease;
    }

    .widget[data-state="open"] .launcher { padding: 0 15px; }
    .widget[data-state="open"] .label { max-width: 0; margin-left: 0; opacity: 0; }
    .widget[data-state="open"] .icon-chat { opacity: 0; transform: scale(.6); }
    .widget[data-state="open"] .icon-minimize { opacity: 1; transform: none; }

    /* Open: the panel grows out of the point just above the launcher's centre (its
       box sits 12px above the launcher with the same right edge, so it never covers
       it). Close: a quick fade in place. While the exit plays the panel has class
       "leaving" (see hidePanel). */
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
      transform-origin: calc(100% - var(--launcher) / 2) 100%;
      animation: hawk-grow-in .3s cubic-bezier(.2, .9, .3, 1) both;
      transition: bottom .25s ease, height .25s ease;
    }
    .panel.leaving { animation: hawk-fade-out .15s ease-in both; }
    .panel iframe { display: block; width: 100%; height: 100%; border: 0; }

    /* First open: the chat app is still loading. The panel is laid out (so the app
       loads and draws at full size) but invisible and doesn't animate yet; the open
       animation starts once the app is ready (see revealPanel). */
    .panel.waiting { animation: none !important; opacity: 0; pointer-events: none; }

    @keyframes hawk-grow-in {
      from { opacity: 0; transform: scale(.12); }
      40%  { opacity: 1; }
      to   { opacity: 1; transform: none; }
    }
    @keyframes hawk-fade-out { to { opacity: 0; } }

    /* Phones and very short windows: the open panel fills the viewport and carries
       its own minimize button, so the launcher steps aside. The panel slides up from
       the bottom edge and back down; the launcher comes back (fading in) only once
       the panel is gone. */
    @media (max-width: 520px), (max-height: 480px) {
      .widget[data-state="open"] .panel,
      .widget .panel.leaving {   /* keep the full-screen shape while it slides away */
        inset: 0;
        width: 100%;
        height: 100%;
        border: 0;
        border-radius: 0;
      }
      .widget[data-state="open"] .panel { animation: hawk-sheet-in .34s cubic-bezier(.2, .9, .3, 1) both; }
      .widget .panel.leaving { animation: hawk-sheet-out .24s cubic-bezier(.4, 0, 1, 1) both; }
      .widget[data-state="open"] .launcher,
      .widget:has(.panel.leaving) .launcher { display: none !important; }
      .launcher { animation: hawk-launcher-in .18s ease-out; }
    }
    @keyframes hawk-sheet-in    { from { transform: translateY(100%); } }
    @keyframes hawk-sheet-out   { to   { transform: translateY(100%); } }
    @keyframes hawk-launcher-in { from { opacity: 0; } }

    /* Reduced motion: no movement; the panel cross-fades in and out (0.12s) */
    @media (prefers-reduced-motion: reduce) {
      .panel, .widget[data-state="open"] .panel { animation: hawk-fade-in .12s linear both; transition: none; }
      .widget .panel.leaving { animation: hawk-fade-out .12s linear both; }
      .launcher { animation: none; }
      .launcher, .label, .icons svg { transition: none; }
    }
    @keyframes hawk-fade-in { from { opacity: 0; } }
  `;

  const CHAT_ICON = `<svg class="icon-chat" viewBox="0 0 24 24" fill="none" aria-hidden="true">
    <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v8a2.5 2.5 0 0 1-2.5 2.5H10l-4.2 3.6c-.5.4-1.3.1-1.3-.6V16A2.5 2.5 0 0 1 4 13.5v-8Z"
      stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>`;
  // Chevron-down while open: "tuck the chat away", not "end it".
  const MINIMIZE_ICON = `<svg class="icon-minimize" viewBox="0 0 24 24" fill="none" aria-hidden="true">
    <path d="M6 9.5l6 6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

  function mount() {
    const host = document.createElement(TAG);
    const root = host.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>${CSS}</style>
      <div class="widget" data-state="closed">
        <button class="launcher" type="button" aria-expanded="false" aria-controls="hawk-panel">
          <span class="icons">${CHAT_ICON}${MINIMIZE_ICON}</span><span class="label">Ask Hawk</span>
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

    // Closing with an exit animation keeps the panel on screen but inert (no clicks,
    // no Tab) until the animation ends; reopening during the exit cancels it.
    let leaveTimer = 0;
    let onLeaveEnd = null;
    let focusLauncherAfterExit = false;   // the launcher was still hidden when asked for focus
    function showPanel() {
      focusLauncherAfterExit = false;
      clearTimeout(leaveTimer);
      if (onLeaveEnd) panel.removeEventListener("animationend", onLeaveEnd);
      onLeaveEnd = null;
      panel.classList.remove("leaving");
      panel.inert = false;
      panel.hidden = false;
      if (!ready) waitForApp();
    }

    // First open of the page: the chat app starts loading at the click and would
    // otherwise load and draw during the open animation (a visible stutter). Hold the
    // animation until the app says "hawk:ready", plus two frames for it to paint;
    // never longer than REVEAL_CAP_MS. The launcher still reacts at once.
    const REVEAL_CAP_MS = 300;
    let waitingForApp = false;
    let revealTimer = 0;
    function waitForApp() {
      if (waitingForApp) return;
      waitingForApp = true;
      panel.classList.add("waiting");
      revealTimer = setTimeout(revealPanel, REVEAL_CAP_MS);
    }
    function revealPanel() {
      if (!waitingForApp) return;
      waitingForApp = false;
      clearTimeout(revealTimer);
      panel.classList.remove("waiting");   // the open animation starts now
    }
    function cancelWait() {
      waitingForApp = false;
      clearTimeout(revealTimer);
      panel.classList.remove("waiting");
    }

    function hidePanel() {
      if (waitingForApp) { cancelWait(); panel.hidden = true; return; }   // closed before it appeared
      if (panel.hidden) return;
      panel.inert = true;
      panel.classList.add("leaving");
      const done = () => {
        clearTimeout(leaveTimer);
        panel.removeEventListener("animationend", onLeaveEnd);
        onLeaveEnd = null;
        panel.classList.remove("leaving");
        panel.inert = false;
        panel.hidden = true;
        if (focusLauncherAfterExit) { focusLauncherAfterExit = false; launcher.focus(); }
      };
      onLeaveEnd = (e) => { if (e.target === panel) done(); };
      panel.addEventListener("animationend", onLeaveEnd);
      leaveTimer = setTimeout(done, 1000);   // in case animationend never arrives
    }

    function setState(next) {
      state = next;
      widget.dataset.state = next;
      const isOpen = next !== "closed";
      if (isOpen) showPanel(); else hidePanel();
      launcher.setAttribute("aria-expanded", String(isOpen));
      if (isOpen) {
        launcher.setAttribute("aria-label", "Minimize Hawk chat");
        launcher.title = "Minimize (your conversation is saved)";
      } else {
        launcher.removeAttribute("aria-label");   // name falls back to "Ask Hawk"
        launcher.removeAttribute("title");
      }
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
      // Full-screen: the launcher stays hidden until the panel has slid away, so it
      // can't take focus yet; hidePanel's exit gives it focus when the slide ends.
      if (root.activeElement !== launcher && panel.classList.contains("leaving")) focusLauncherAfterExit = true;
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
        if (waitingForApp) requestAnimationFrame(() => requestAnimationFrame(revealPanel));
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
        // Any part on screen in the launcher's column counts (not only once it reaches
        // the launcher), so a banner sliding up keeps the full GAP below the launcher.
        const coversCorner = r.top < vh && r.bottom > top && r.left < right && r.right > left;
        if (coversCorner) offset = Math.max(offset, vh - r.top);
      }
      return Math.min(offset, Math.max(0, vh - LAUNCHER - 2 * GAP));   // keep the launcher on screen
    }

    // Is an avoided element moving right now? Banners usually slide in or out with a
    // CSS animation or transition on themselves or a container, which getAnimations()
    // reports (animations inside the banner, like a spinner, don't move it).
    function avoidedIsMoving() {
      let elements;
      try { elements = document.querySelectorAll(AVOID); } catch { return false; }
      for (const el of elements) {
        for (let node = el; node && node !== document.documentElement; node = node.parentElement) {
          if (typeof node.getAnimations !== "function") return false;
          if (node.getAnimations().some((a) => a.playState === "running")) return true;
        }
      }
      return false;
    }

    // Measure once per frame. While a banner is moving, keep measuring every frame
    // (up to FOLLOW_MAX_MS after the last page change) and move the launcher and panel
    // with it instead of gliding after it ("following" turns their glide off).
    const FOLLOW_MAX_MS = 2000;
    let followUntil = 0;
    let measureQueued = false;
    function measure() {
      if (measureQueued) return;
      measureQueued = true;
      requestAnimationFrame(() => {
        measureQueued = false;
        const offset = Math.round(avoidOffset());   // also brings new animations up to date
        const moving = performance.now() < followUntil && avoidedIsMoving();
        widget.classList.toggle("following", moving);
        widget.style.setProperty("--avoid", `${offset}px`);
        if (moving) measure();
      });
    }

    let settleTimer = 0;
    function measureNowAndAfterAnimations() {
      followUntil = performance.now() + FOLLOW_MAX_MS;
      measure();
      clearTimeout(settleTimer);
      settleTimer = setTimeout(measure, 400);   // safety net for banners that move some other way
    }

    if (AVOID) {
      new MutationObserver(measureNowAndAfterAnimations)
        .observe(document.body, { childList: true, subtree: true, attributes: true,
                                  attributeFilter: ["class", "style", "hidden", "open"] });
      window.addEventListener("resize", measure);
      // The launcher changes width as it morphs; re-check the banner once it settles.
      launcher.addEventListener("transitionend", (e) => {
        if (e.target === launcher && e.propertyName === "padding-left") measure();
      });
      measure();
    }

    document.body.append(host);

    // Reopen (without taking focus) if the panel was open on the previous page.
    let wasOpen = false;
    try { wasOpen = sessionStorage.getItem(OPEN_KEY) === "1"; } catch { /* storage blocked */ }
    if (wasOpen) open(false);

    // Exact label width, so the pill-to-circle collapse has no dead time. Measured
    // after any reopen above, so a page that loads with the panel open doesn't
    // play the morph. (scrollWidth is the full text width even while collapsed.)
    const label = root.querySelector(".label");
    widget.style.setProperty("--label-w", `${Math.ceil(label.scrollWidth)}px`);
  }

  if (document.body) mount();
  else document.addEventListener("DOMContentLoaded", mount, { once: true });
})();
