/*
 * shared.js: talking to FreeScribe and putting text into the EMR page.
 * Used by the popup, the options page and the background worker.
 *
 * FreeScribe listens on 127.0.0.1 only (this computer). Every request carries the
 * pairing code shown in FreeScribe's "Chrome extension" window. Notes are never
 * stored by the extension; they go straight from FreeScribe into the page.
 *
 * Part of FreeScribe, released under the AGPL-3.0 license.
 */

const FS_DEFAULT_PORT = 52617;

async function fsSettings() {
  const stored = await chrome.storage.local.get(["code", "port"]);
  return { code: stored.code || "", port: Number(stored.port) || FS_DEFAULT_PORT };
}

async function fsSaveSettings(code, port) {
  await chrome.storage.local.set({ code: String(code || "").trim(), port: Number(port) || FS_DEFAULT_PORT });
}

class FsError extends Error {
  constructor(message, kind) {
    super(message);
    this.kind = kind; // "offline" | "unpaired" | "refused"
  }
}

async function fsCall(path, body) {
  const { code, port } = await fsSettings();
  if (!code) throw new FsError("Pair the extension with FreeScribe first.", "unpaired");
  let response;
  try {
    response = await fetch(`http://127.0.0.1:${port}${path}`, {
      method: body === undefined ? "GET" : "POST",
      headers: { "X-FreeScribe-Code": code, "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
  } catch (_error) {
    throw new FsError(
      "FreeScribe isn’t reachable. Open FreeScribe and turn on “Chrome extension” in its sidebar.",
      "offline");
  }
  let data = {};
  try { data = await response.json(); } catch (_error) { /* empty reply */ }
  if (response.status === 401) throw new FsError(data.error || "The pairing code does not match.", "unpaired");
  if (!response.ok) throw new FsError(data.error || `FreeScribe answered ${response.status}.`, "refused");
  return data;
}

const fsStatus = () => fsCall("/api/status");
const fsRecord = (action) => fsCall("/api/record", { action });
const fsNote = () => fsCall("/api/note");
const fsAddContext = (text) => fsCall("/api/context", { text });

/* ----------------------------------------------------------------- the badge */
async function fsShowBadge(status) {
  let text = "", colour = "#5E5E62";
  if (!status) { text = "off"; colour = "#8E8E93"; }
  else if (status.phase === "recording") { text = "REC"; colour = "#D2312A"; }
  else if (status.phase === "paused") { text = "II"; colour = "#9A4A12"; }
  else if (status.phase === "working") { text = "…"; colour = "#2E6FC0"; }
  else if (status.phase === "note_ready") { text = "✓"; colour = "#2A8643"; }
  try {
    await chrome.action.setBadgeText({ text });
    await chrome.action.setBadgeBackgroundColor({ color: colour });
  } catch (_error) { /* not available in this context */ }
}

/* ------------------------------------------------- putting text into the page */
// Runs inside the EMR page (every frame): where is the cursor?
function fsProbeFocus() {
  const el = document.activeElement;
  const textual = el && (el.tagName === "TEXTAREA" ||
    (el.tagName === "INPUT" && /^(text|search|)$/i.test(el.type || "")) || el.isContentEditable);
  if (!textual || el.disabled || el.readOnly) return { field: false };
  return {
    field: true,
    focused: document.hasFocus(),
    name: el.getAttribute("aria-label") || el.name || el.id || el.tagName.toLowerCase(),
    length: el.isContentEditable ? (el.innerText || "").length : (el.value || "").length,
    big: el.tagName === "TEXTAREA" || el.isContentEditable,
  };
}

// Runs inside the chosen frame: put the text at the cursor (replacing any selection)
function fsInsertAtCursor(text) {
  const el = document.activeElement;
  if (!el) return { done: false };
  if (el.isContentEditable) {
    el.focus();
    const ok = document.execCommand("insertText", false, text);
    if (!ok) el.innerText += text;
  } else if ("value" in el) {
    el.focus();
    const value = el.value || "";
    const start = el.selectionStart ?? value.length;
    const end = el.selectionEnd ?? start;
    const before = value.slice(0, start);
    // Start on a new line when the cursor is right after other text
    const lead = before && !/\n\s*$/.test(before) ? "\n" : "";
    el.setRangeText(lead + text, start, end, "end");
    for (const type of ["input", "change", "keyup"]) {
      el.dispatchEvent(new Event(type, { bubbles: true }));
    }
  } else {
    return { done: false };
  }
  return { done: true, name: el.name || el.id || el.tagName.toLowerCase() };
}

// Insert ``text`` where the cursor is in the active tab. Returns {done, reason, name}.
async function fsInsertIntoActiveTab(text) {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!tab || !/^https?:/.test(tab.url || "")) {
    return { done: false, reason: "page" };
  }
  let probes;
  try {
    probes = await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames: true }, func: fsProbeFocus });
  } catch (_error) {
    return { done: false, reason: "page" };
  }
  const fields = probes.filter((p) => p.result && p.result.field);
  if (!fields.length) return { done: false, reason: "nofield" };
  // The frame that has the keyboard focus wins; otherwise the only (or largest) text box with a cursor
  fields.sort((a, b) => (b.result.focused - a.result.focused) || (b.result.big - a.result.big));
  const target = fields[0];
  const [inserted] = await chrome.scripting.executeScript({
    target: { tabId: tab.id, frameIds: [target.frameId] }, func: fsInsertAtCursor, args: [text],
  });
  return inserted && inserted.result && inserted.result.done
    ? { done: true, name: inserted.result.name }
    : { done: false, reason: "nofield" };
}
