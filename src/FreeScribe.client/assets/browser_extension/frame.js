/*
 * frame.js: runs in every frame of the EMR pages the floating panel is turned on for.
 * It remembers the text box the clinician clicked last, so "Insert into chart" puts
 * the note there even after a click on the panel has moved the focus.
 * Nothing is read from or sent anywhere: the box is only remembered in the page.
 * Part of FreeScribe, released under the AGPL-3.0 license.
 */
(() => {
  if (window.__fsFrame) return;
  window.__fsFrame = true;

  const editable = (el) => !!el && !el.disabled && !el.readOnly && (el.tagName === "TEXTAREA" ||
    (el.tagName === "INPUT" && /^(text|search|)$/i.test(el.type || "")) || el.isContentEditable);

  document.addEventListener("focusin", (event) => {
    const el = event.composedPath ? event.composedPath()[0] : event.target;
    // The panel's own boxes don't count
    const root = el && el.getRootNode && el.getRootNode();
    if (root && root.host && root.host.id === "freescribe-panel-host") return;
    if (editable(el)) {
      window.__fsLastField = el;
      window.__fsLastAt = Date.now();
    }
  }, true);
})();
