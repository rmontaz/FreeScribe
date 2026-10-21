/*
 * options.js: pairing with FreeScribe.
 * Part of FreeScribe, released under the AGPL-3.0 license.
 */

const $ = (id) => document.getElementById(id);

function result(text, kind) {
  const el = $("result");
  el.textContent = text;
  el.className = "message " + (kind || "");
  el.hidden = false;
}

(async () => {
  const { code, port } = await fsSettings();
  $("code").value = code;
  $("port").value = port;
  chrome.commands.getAll((commands) => {
    for (const c of commands) {
      if (c.name === "toggle-recording" && c.shortcut) $("k-toggle").textContent = c.shortcut;
      if (c.name === "insert-note" && c.shortcut) $("k-insert").textContent = c.shortcut;
    }
  });
})();

$("save").addEventListener("click", async () => {
  await fsSaveSettings($("code").value, $("port").value);
  try {
    const status = await fsStatus();
    await fsShowBadge(status);
    result(`Connected to FreeScribe ${status.version || ""}. You're all set.`, "ok");
  } catch (error) {
    result(error.message, "warn");
  }
});

$("shortcuts").addEventListener("click", (event) => {
  event.preventDefault();
  // A window of its own, so it isn't hidden behind OSCAR's pop-up chart
  chrome.windows.create({ url: "chrome://extensions/shortcuts", type: "normal", focused: true });
});
