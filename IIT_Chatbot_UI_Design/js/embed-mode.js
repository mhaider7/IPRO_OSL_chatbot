/**
 * embed-mode.js — runs only when the app is loaded as the widget panel (/chat?embed=1).
 * The page around it is controlled by /embed.js; see that file for the message list.
 */

import { scroll } from "./ui.js";

export const EMBEDDED =
  new URLSearchParams(location.search).get("embed") === "1" && window.parent !== window;

// The host site's origin isn't known in advance, so messages go to "*".
// They carry only a type string, never conversation data.
function tellParent(type) {
  window.parent.postMessage({ type }, "*");
}

export function initEmbedMode() {
  if (!EMBEDDED) return;

  document.documentElement.classList.add("embed");

  const closeBtn = document.getElementById("close-panel");
  closeBtn.hidden = false;
  closeBtn.addEventListener("click", () => tellParent("hawk:close"));

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") tellParent("hawk:close");
  });

  window.addEventListener("message", (e) => {
    if (e.source !== window.parent || e.data?.type !== "hawk:focus") return;
    scroll();   // replies that streamed in while the panel was hidden
    const input = document.getElementById("message");
    (input.disabled ? closeBtn : input).focus();
  });

  tellParent("hawk:ready");
}
