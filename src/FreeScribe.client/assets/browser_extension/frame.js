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

  // Never print FreeScribe's panel, dictation bar or windows with the chart
  const hidePrint = () => {
    if (document.getElementById("freescribe-print-style")) return;
    const style = document.createElement("style");
    style.id = "freescribe-print-style";
    style.textContent = '@media print { [id^="freescribe-"][id$="-host"] { display: none !important; } }';
    (document.head || document.documentElement).appendChild(style);
  };
  hidePrint();
  document.addEventListener("DOMContentLoaded", hidePrint);
  // Also for print paths that ignore print styles: hide them around the print itself
  window.addEventListener("beforeprint", () => {
    for (const el of document.querySelectorAll('[id^="freescribe-"][id$="-host"]')) {
      el.dataset.fsPrintDisplay = el.style.display || "";
      el.style.setProperty("display", "none", "important");
    }
  });
  window.addEventListener("afterprint", () => {
    for (const el of document.querySelectorAll('[id^="freescribe-"][id$="-host"]')) {
      el.style.display = el.dataset.fsPrintDisplay || "";
      delete el.dataset.fsPrintDisplay;
    }
  });

  document.addEventListener("focusin", (event) => {
    const el = event.composedPath ? event.composedPath()[0] : event.target;
    // The panel's own boxes don't count
    const root = el && el.getRootNode && el.getRootNode();
    if (root && root.host && /^freescribe-/.test(root.host.id || "")) return;
    if (editable(el)) {
      window.__fsLastField = el;
      window.__fsLastAt = Date.now();
    }
  }, true);
})();
