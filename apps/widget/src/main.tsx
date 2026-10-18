import { render } from "preact";

import { App } from "./App";
import css from "./styles.css?inline";

/**
 * Embed with:
 *   <script src="https://YOUR-API/widget.js" defer></script>
 * Options (data attributes on the script tag):
 *   data-api="https://YOUR-API"   API origin (default: where widget.js was loaded from)
 *   data-open="true"              open the panel on page load
 */
function mount(script: HTMLScriptElement | null) {
  if (document.getElementById("ask-olake-root")) return; // loaded twice: keep one
  const fromScript = script?.src ? new URL(script.src).origin : window.location.origin;
  const apiBase = (script?.dataset.api || fromScript).replace(/\/$/, "");
  const startOpen = script?.dataset.open === "true";

  const host = document.createElement("div");
  host.id = "ask-olake-root";
  document.body.appendChild(host);
  const shadow = host.attachShadow({ mode: "open" });
  const style = document.createElement("style");
  style.textContent = css;
  shadow.appendChild(style);
  const container = document.createElement("div");
  shadow.appendChild(container);
  render(<App apiBase={apiBase} startOpen={startOpen} />, container);
}

// document.currentScript is only set while this file first runs, so capture it now.
const script = document.currentScript as HTMLScriptElement | null;
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => mount(script));
} else {
  mount(script);
}
