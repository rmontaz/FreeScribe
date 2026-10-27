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
  constructor(message, kind, code = "", details = {}) {
    super(message);
    this.kind = kind; // "offline" | "unpaired" | "refused"
    this.code = code; // FreeScribe's reason, e.g. "other_patient"
    this.details = details || {};
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
  if (!response.ok) throw new FsError(data.error || `FreeScribe answered ${response.status}.`, "refused", data.code, data.details);
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
// Runs inside the EMR page (every frame): where is the cursor? When the focus has moved
// to the floating panel, the note box clicked last (remembered by frame.js) counts.
function fsProbeFocus() {
  const editable = (el) => !!el && !el.disabled && !el.readOnly && (el.tagName === "TEXTAREA" ||
    (el.tagName === "INPUT" && /^(text|search|)$/i.test(el.type || "")) || el.isContentEditable);
  let el = document.activeElement;
  let at = Date.now();
  if (!editable(el)) {
    el = window.__fsLastField;
    at = window.__fsLastAt || 0;
    if (!el || !el.isConnected || !editable(el)) return { field: false };
  }
  return {
    field: true,
    focused: document.hasFocus() && document.activeElement === el,
    at,
    name: el.getAttribute("aria-label") || el.name || el.id || el.tagName.toLowerCase(),
    length: el.isContentEditable ? (el.innerText || "").length : (el.value || "").length,
    big: el.tagName === "TEXTAREA" || el.isContentEditable,
  };
}

// Runs inside the chosen frame: put the text at the cursor (replacing any selection)
function fsInsertAtCursor(text) {
  const editable = (e) => !!e && !e.disabled && !e.readOnly && (e.tagName === "TEXTAREA" ||
    (e.tagName === "INPUT" && /^(text|search|)$/i.test(e.type || "")) || e.isContentEditable);
  let el = document.activeElement;
  if (!editable(el)) el = window.__fsLastField;
  if (!editable(el) || !el.isConnected) return { done: false };
  if (el.isContentEditable) {
    el.focus();
    const ok = document.execCommand("insertText", false, text);
    if (!ok) el.innerText += text;
  } else {
    // A text box keeps its cursor position after it loses the focus
    const value = el.value || "";
    const start = el.selectionStart ?? value.length;
    const end = el.selectionEnd ?? start;
    el.focus();
    const before = value.slice(0, start);
    // Start on a new line when the cursor is right after other text
    const lead = before && !/\n\s*$/.test(before) ? "\n" : "";
    el.setRangeText(lead + text, start, end, "end");
    for (const type of ["input", "change", "keyup"]) {
      el.dispatchEvent(new Event(type, { bubbles: true }));
    }
  }
  return { done: true, name: el.name || el.id || el.tagName.toLowerCase() };
}

// Opens ``url`` in a small window of its own, in front of everything (also of a pop-up window)
async function fsOpenInFront(url, width, height) {
  const win = await chrome.windows.create({ url, type: "popup", width, height, focused: true });
  try { await chrome.windows.update(win.id, { focused: true, drawAttention: true }); } catch (_error) { /* shown */ }
  return win;
}

async function fsActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  return tab || null;
}

// Insert ``text`` where the cursor is in tab ``tabId``. Returns {done, reason, name}.
async function fsInsertIntoTab(tabId, text) {
  let probes;
  try {
    probes = await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, func: fsProbeFocus });
  } catch (_error) {
    return { done: false, reason: "page" };
  }
  const fields = probes.filter((p) => p.result && p.result.field);
  if (!fields.length) return { done: false, reason: "nofield" };
  // The box with the keyboard focus wins, then the one clicked last, then a big note box
  fields.sort((a, b) => (b.result.focused - a.result.focused) || (b.result.at - a.result.at) ||
    (b.result.big - a.result.big));
  const target = fields[0];
  const [inserted] = await chrome.scripting.executeScript({
    target: { tabId, frameIds: [target.frameId] }, func: fsInsertAtCursor, args: [text],
  });
  return inserted && inserted.result && inserted.result.done
    ? { done: true, name: inserted.result.name }
    : { done: false, reason: "nofield" };
}

// Insert ``text`` where the cursor is in the active tab.
async function fsInsertIntoActiveTab(text) {
  const tab = await fsActiveTab();
  if (!tab || !/^https?:/.test(tab.url || "")) return { done: false, reason: "page" };
  return fsInsertIntoTab(tab.id, text);
}

/* ----------------------------------------- the floating panel on EMR sites */
// The panel is shown on sites the clinician turned it on for (their OSCAR address),
// including OSCAR's pop-up chart windows, which have no toolbar for the extension button.
async function fsSites() {
  const stored = await chrome.storage.local.get("sites");
  return Array.isArray(stored.sites) ? stored.sites : [];
}

function fsSiteId(origin) {
  return "fs-" + origin.replace(/[^a-z0-9]+/gi, "_");
}

function fsOriginOf(url) {
  try {
    const u = new URL(url);
    return /^https?:$/.test(u.protocol) ? u.origin : "";
  } catch (_error) {
    return "";
  }
}

// Registers the panel's scripts for ``origin`` (the site permission must be granted already)
// and shows the panel in the tabs already open there.
// The panel's scripts in the top window: dictation first, then the panel that offers it
const FS_PANEL_FILES = ["voice.js", "panel.js"];

async function fsRegisterSite(origin) {
  const id = fsSiteId(origin);
  const existing = await chrome.scripting.getRegisteredContentScripts({ ids: [id + "-frames", id + "-panel"] });
  const panel = existing.find((s) => s.id === id + "-panel");
  // Registered by an older version with other files: register again
  const current = existing.length === 2 && panel && JSON.stringify(panel.js) === JSON.stringify(FS_PANEL_FILES);
  if (!current) {
    if (existing.length) await chrome.scripting.unregisterContentScripts({ ids: existing.map((s) => s.id) });
    await chrome.scripting.registerContentScripts([
      { id: id + "-frames", matches: [origin + "/*"], js: ["frame.js"], allFrames: true,
        runAt: "document_start", persistAcrossSessions: true },
      { id: id + "-panel", matches: [origin + "/*"], js: FS_PANEL_FILES, allFrames: false,
        runAt: "document_idle", persistAcrossSessions: true },
    ]);
  }
  const tabs = await chrome.tabs.query({ url: origin + "/*" });
  for (const tab of tabs) {
    try {
      await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames: true }, files: ["frame.js"] });
      await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: FS_PANEL_FILES });
    } catch (_error) { /* a page Chrome does not let extensions into */ }
  }
}

async function fsEnableSite(origin) {
  const sites = await fsSites();
  if (!sites.includes(origin)) await chrome.storage.local.set({ sites: [...sites, origin] });
  await fsRegisterSite(origin);
}

async function fsDisableSite(origin) {
  const sites = (await fsSites()).filter((s) => s !== origin);
  await chrome.storage.local.set({ sites });
  const id = fsSiteId(origin);
  const existing = await chrome.scripting.getRegisteredContentScripts({ ids: [id + "-frames", id + "-panel"] });
  if (existing.length) await chrome.scripting.unregisterContentScripts({ ids: existing.map((s) => s.id) });
  const tabs = await chrome.tabs.query({ url: origin + "/*" });
  for (const tab of tabs) {
    try { await chrome.tabs.sendMessage(tab.id, { fs: "remove-panel" }); } catch (_error) { /* not shown */ }
  }
  try { await chrome.permissions.remove({ origins: [origin + "/*"] }); } catch (_error) { /* kept */ }
}

// After an update or a restart: make sure every turned-on site still has its scripts
async function fsSyncSites() {
  for (const origin of await fsSites()) {
    const granted = await chrome.permissions.contains({ origins: [origin + "/*"] });
    if (granted) {
      try { await fsRegisterSite(origin); } catch (_error) { /* try again next start */ }
    }
  }
}

/* ------------------------------------------------------ patient from OSCAR */
// Runs inside the OSCAR page (each frame). Finds the chart's patient number and reads the
// patient's details with the clinician's own OSCAR session: OSCAR's JSON service
// (/ws/rs/demographics/{id}) when it has one, otherwise the patient's edit page.
// Self-contained: Chrome copies this function into the page.
async function fsReadOscarPatient() {
  const pad = (v) => String(v || "").padStart(2, "0");
  const fromDoc = (doc) => {
    const val = (name) => {
      const el = doc.querySelector(`[name="${name}"]`);
      if (!el) return "";
      if (el.tagName === "SELECT") return el.value || (el.selectedOptions[0] && el.selectedOptions[0].value) || "";
      return el.value || el.getAttribute("value") || "";
    };
    const last = val("last_name"), first = val("first_name");
    if (!last && !first) return null;
    const y = val("year_of_birth"), m = val("month_of_birth"), d = val("date_of_birth");
    return {
      first_name: first, last_name: last,
      dob: y && m && d ? `${y}-${pad(m)}-${pad(d)}` : "",
      sex: val("sex"), hin: val("hin"), hin_version: val("ver"), hc_province: val("hc_type"),
      sin: val("sin"), street: val("address"), city: val("city"), province: val("province"),
      postal_code: val("postal"), phone: val("phone"), work_phone: val("phone2"),
      cell: val("demo_cell"), email: val("email"), chart_no: val("chart_no"),
    };
  };
  const fromJson = (data) => {
    if (data && !data.firstName && !data.lastName) {
      const inner = Object.values(data).find((v) => v && typeof v === "object" && (v.firstName || v.lastName));
      if (inner) data = inner;
    }
    if (!data || !(data.firstName || data.lastName)) return null;
    const address = data.address || {};
    const extras = Array.isArray(data.extras) ? data.extras : [];
    const extra = (key) => {
      const hit = extras.find((e) => e && e.key === key);
      return hit ? String(hit.value || "") : "";
    };
    return {
      first_name: data.firstName || "", last_name: data.lastName || "",
      dob: data.dobYear && data.dobMonth && data.dobDay ? `${data.dobYear}-${pad(data.dobMonth)}-${pad(data.dobDay)}` : "",
      sex: data.sex || "", hin: data.hin || "", hin_version: data.ver || "", hc_province: data.hcType || "",
      sin: data.sin || "", street: address.address || "", city: address.city || "",
      province: address.province || "", postal_code: address.postal || "", phone: data.phone || "",
      work_phone: data.alternativePhone || "", cell: extra("demo_cell"), email: data.email || "",
      chart_no: data.chartNo || "",
    };
  };

  // The patient's own page is open: read it directly
  const here = fromDoc(document);
  if (here) return { found: true, patient: here, via: "page" };

  // The chart's patient number, from the page's own address only. A page that merely links to
  // patients (the day sheet, a search list) is not one patient's chart: nothing is guessed.
  const id = (location.href.match(/[?&](?:demographicNo|demographic_no|demographicId)=(\d+)/i) || [])[1];
  if (!id) return { found: false };

  const first = location.pathname.split("/")[1];
  const bases = [...new Set([first ? `${location.origin}/${first}` : null, location.origin].filter(Boolean))];
  for (const base of bases) {
    try {
      const r = await fetch(`${base}/ws/rs/demographics/${id}`,
        { credentials: "include", headers: { Accept: "application/json" } });
      if (r.ok && /json/i.test(r.headers.get("content-type") || "")) {
        const patient = fromJson(await r.json());
        if (patient) return { found: true, patient, via: "service", id };
      }
    } catch (_e) { /* try the next way */ }
    try {
      const r = await fetch(`${base}/demographic/demographiccontrol.jsp?demographic_no=${id}` +
        "&displaymode=edit&dboperation=search_detail", { credentials: "include" });
      if (r.ok) {
        const doc = new DOMParser().parseFromString(await r.text(), "text/html");
        const patient = fromDoc(doc);
        if (patient) return { found: true, patient, via: "edit page", id };
      }
    } catch (_e) { /* not OSCAR here */ }
  }
  return { found: false, id };
}

// The patient of the OSCAR chart in tab ``tabId`` (the active tab when not given), or null.
async function fsPatientFromTab(tabId) {
  if (tabId === undefined) {
    const tab = await fsActiveTab();
    if (!tab || !/^https?:/.test(tab.url || "")) return null;
    tabId = tab.id;
  }
  let results;
  try {
    results = await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, func: fsReadOscarPatient });
  } catch (_error) {
    return null;
  }
  // The top page first, then frames. Frames that show different patients: no patient.
  results.sort((a, b) => (a.frameId === 0 ? -1 : 0) - (b.frameId === 0 ? -1 : 0));
  const hits = results.filter((r) => r.result && r.result.found);
  const who = (p) => [p.last_name, p.first_name, p.dob].map((v) => String(v || "").toLowerCase()).join("|");
  if (new Set(hits.map((h) => who(h.result.patient))).size > 1) return null;
  return hits.length ? hits[0].result.patient : null;
}

// "Raman, Priya", as OSCAR and FreeScribe show patients
function fsPatientName(p) {
  return [p && p.last_name, p && p.first_name].filter(Boolean).join(", ");
}

// Sends the chart's patient to FreeScribe. ``mode``: "link" (refused with code "other_patient"
// when the session belongs to someone else), "switch" (move the session to this patient) or
// "new" (a fresh session for this patient). Returns the name FreeScribe linked, or null when the
// page shows no single patient.
async function fsLinkPatient(tabId, mode = "link") {
  const patient = await fsPatientFromTab(tabId);
  if (!patient) return null;
  if (mode === "new") await fsCall("/api/session", { action: "new" });
  const reply = await fsCall("/api/patient", { patient, action: mode === "switch" ? "switch" : "link" });
  return reply.patient || null;
}

const fsUnlinkPatient = () => fsCall("/api/patient", { action: "clear" });

/* ------------------------------------------------------ dictation into the page */
// These run inside a frame of the EMR page (Chrome copies each function in), so each is
// self-contained. The box being typed into is the one with the cursor, or the one clicked
// last (frame.js remembers it), so the clinician can click from box to box while talking.

// Types one piece of dictation at the cursor. ``sid`` is the dictation; the text typed for it
// so far is remembered (in the page only) so the AI-tidied version can replace it at the end.
function fsDictInsert(text, sid) {
  const editable = (e) => !!e && !e.disabled && !e.readOnly && (e.tagName === "TEXTAREA" ||
    (e.tagName === "INPUT" && /^(text|search|)$/i.test(e.type || "")) || e.isContentEditable);
  let el = document.activeElement;
  if (!editable(el)) el = window.__fsLastField;
  if (!editable(el) || !el.isConnected) return { done: false };
  let st = window.__fsDict;
  if (!st || st.sid !== sid) st = window.__fsDict = { sid, spans: [] };
  let span = st.spans[st.spans.length - 1];
  if (el.isContentEditable) {
    el.focus();
    const sel = window.getSelection();
    if (!sel.rangeCount || !el.contains(sel.getRangeAt(0).startContainer)) return { done: false };
    // Positions are kept as character offsets in the box: the browser may merge the typed
    // text into the text before it, which moves node-based positions
    const offset = () => {
      const pre = document.createRange();
      pre.selectNodeContents(el);
      pre.setEnd(sel.getRangeAt(0).endContainer, sel.getRangeAt(0).endOffset);
      return pre.toString().length;
    };
    const pre = document.createRange();
    pre.selectNodeContents(el);
    pre.setEnd(sel.getRangeAt(0).startContainer, sel.getRangeAt(0).startOffset);
    const before = pre.toString();
    if (!before || /\s$/.test(before)) text = text.replace(/^ +/, "");
    else if (!/^[\s,.;:?!]/.test(text)) text = " " + text;
    const at = before.length;
    if (!span || span.el !== el || span.end !== at) {
      span = { el, text: "", start: at, end: at };
      st.spans.push(span);
    }
    if (!document.execCommand("insertText", false, text)) return { done: false };
    span.text += text;
    span.end = offset();
  } else {
    const value = el.value || "";
    const at = el.selectionStart ?? value.length;
    const end = el.selectionEnd ?? at;
    const before = value.slice(0, at);
    if (!before || /\s$/.test(before)) text = text.replace(/^ +/, "");
    else if (!/^[\s,.;:?!]/.test(text)) text = " " + text;
    if (el.maxLength > 0) text = text.slice(0, Math.max(0, el.maxLength - (value.length - (end - at))));
    if (!text) return { done: true, full: true, name: el.name || el.id || el.tagName.toLowerCase() };
    if (!span || span.el !== el || span.start + span.text.length !== at) {
      span = { el, text: "", start: at };
      st.spans.push(span);
    }
    el.focus();
    el.setRangeText(text, at, end, "end");
    span.text += text;
    for (const type of ["input", "change", "keyup"]) el.dispatchEvent(new Event(type, { bubbles: true }));
  }
  // What to call the box in the dictation bar: its label, else its name in words, else what it is
  const words = (n) => {
    const t = String(n || "").replace(/([a-z])([A-Z])/g, "$1 $2").replace(/[_\-.\[\]]+/g, " ").replace(/\s+/g, " ").trim();
    return /[A-Za-z]{3}/.test(t) && !/^(text|input|field|box|txt|t|f)\s*\d*$/i.test(t) ? t[0].toUpperCase() + t.slice(1).toLowerCase() : "";
  };
  const big = el.isContentEditable || el.tagName === "TEXTAREA";
  const named = el.getAttribute("aria-label") || (el.labels && el.labels[0] && el.labels[0].textContent.trim()) ||
    el.getAttribute("title") || el.getAttribute("placeholder") || "";
  const label = named || (big && /note/i.test((el.name || "") + (el.id || "")) ? "the note" : words(el.name || el.id)) ||
    (big ? "the text box" : "the box");
  return { done: true, name: String(label).slice(0, 60), spans: st.spans.length };
}

// Replaces the dictation ``sid`` typed in this frame with ``tidied`` when it was typed in one
// place and nobody has changed it since. Returns {done, reason}.
function fsDictReplace(sid, tidied) {
  const st = window.__fsDict;
  if (!st || st.sid !== sid || !st.spans.length) return { done: false, reason: "none" };
  if (st.spans.length > 1) return { done: false, reason: "several" };
  const span = st.spans[0];
  const el = span.el;
  if (!el.isConnected) return { done: false, reason: "changed" };
  const same = (a, b) => a.replace(/\s+/g, " ").trim() === b.replace(/\s+/g, " ").trim();
  const lead = (span.text.match(/^\s*/) || [""])[0];
  if (el.isContentEditable) {
    // Character offsets back to places in the box
    const place = (n) => {
      const walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
      let last = null;
      for (let t = walk.nextNode(); t; t = walk.nextNode()) {
        if (n <= t.data.length) return [t, n];
        n -= t.data.length;
        last = t;
      }
      return last ? [last, last.data.length] : [el, el.childNodes.length];
    };
    const range = document.createRange();
    try {
      range.setStart(...place(span.start));
      range.setEnd(...place(span.end));
    } catch (_error) { return { done: false, reason: "changed" }; }
    if (!same(range.toString(), span.text)) return { done: false, reason: "changed" };
    el.focus();
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    if (!document.execCommand("insertText", false, lead + tidied)) return { done: false, reason: "changed" };
  } else {
    const value = el.value || "";
    if (value.slice(span.start, span.start + span.text.length) !== span.text) return { done: false, reason: "changed" };
    el.setRangeText(lead + tidied, span.start, span.start + span.text.length, "end");
    for (const type of ["input", "change", "keyup"]) el.dispatchEvent(new Event(type, { bubbles: true }));
  }
  window.__fsDict = null;
  return { done: true };
}

// The fillable boxes of the form in this frame, with what each is called. Boxes drawn over a
// picture of the paper form (many OSCAR eForms) get the picture and their place on it, so
// FreeScribe can read the label from the picture. Remembers the boxes for fsWriteForm.
async function fsReadForm() {
  const skip = /^(hidden|submit|button|reset|image|file|password|color|range)$/i;
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 2 && r.height > 2 && s.visibility !== "hidden" && s.display !== "none";
  };
  const inPanel = (el) => { const h = el.getRootNode && el.getRootNode().host; return !!(h && /^freescribe-/.test(h.id)); };
  const all = [...document.querySelectorAll("input, textarea, select")].filter((el) =>
    !el.disabled && !(el.tagName === "INPUT" && skip.test(el.type || "")) && !inPanel(el) && visible(el) &&
    !(el.readOnly && el.tagName !== "SELECT"));
  if (all.length < 2) return { count: 0 };
  const clean = (t) => String(t || "").replace(/\s+/g, " ").trim();

  // Text on the page with its place, for labels that are neither <label> nor attributes
  const texts = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n && texts.length < 4000; n = walker.nextNode()) {
    const t = clean(n.data);
    if (!t || !/[A-Za-z]/.test(t) || !n.parentElement || /^(SCRIPT|STYLE|OPTION|TEXTAREA)$/.test(n.parentElement.tagName)) continue;
    const range = document.createRange();
    range.selectNodeContents(n);
    const r = range.getBoundingClientRect();
    if (r.width && r.height) texts.push({ t, r });
  }
  const nearText = (r, after) => {
    const mid = (r.top + r.bottom) / 2;
    let best = null, bestD = 1e9;
    for (const x of texts) {
      const xm = (x.r.top + x.r.bottom) / 2;
      let d = 1e9;
      if (after && Math.abs(xm - mid) < Math.max(8, r.height) && x.r.left >= r.right - 2 && x.r.left - r.right < 80) d = x.r.left - r.right;
      else if (!after && Math.abs(xm - mid) < Math.max(8, r.height / 2 + 4) && x.r.right <= r.left + 2 && r.left - x.r.right < 260) d = r.left - x.r.right;
      else if (!after && r.top - x.r.bottom >= -2 && r.top - x.r.bottom < 26 && x.r.left < r.right && x.r.right > r.left - 40) d = 300 + (r.top - x.r.bottom);
      if (d < bestD) { bestD = d; best = x.t; }
    }
    return best || "";
  };
  const ownLabel = (el) => clean(el.getAttribute("aria-label") || (el.labels && [...el.labels].map((l) => l.textContent).join(" ")) ||
    el.getAttribute("title") || el.getAttribute("placeholder") || "");

  // Pictures of the paper form
  const pictures = [], picEls = [];
  for (const img of document.querySelectorAll("img")) {
    const r = img.getBoundingClientRect();
    if (img.naturalWidth >= 400 && r.width >= 300 && r.height >= 300) picEls.push({ img, r });
  }
  const onPicture = (r) => {
    const i = picEls.findIndex((p) => r.left >= p.r.left - 2 && r.right <= p.r.right + 2 && r.top >= p.r.top - 2 && r.bottom <= p.r.bottom + 2);
    if (i < 0) return null;
    const p = picEls[i], k = p.img.naturalWidth / p.r.width;
    return { picture: i, rect: [(r.left - p.r.left) * k, (r.top - p.r.top) * k, (r.right - p.r.left) * k, (r.bottom - p.r.top) * k] };
  };

  const fields = [], els = [];
  const radios = new Map();
  for (const el of all) {
    const r = el.getBoundingClientRect();
    if (el.type === "radio") {
      const key = el.name || ("radio" + els.length);
      if (!radios.has(key)) {
        radios.set(key, { id: String(els.length), kind: "radio", name: el.name || "", options: [], option_labels: [],
          option_rects: [], label: "", top: r.top + scrollY });
        els.push([]);
        fields.push(radios.get(key));
      }
      const f = radios.get(key);
      els[Number(f.id)].push(el);
      f.options.push(el.value || String(f.options.length + 1));
      f.option_labels.push(ownLabel(el) || nearText(r, true));
      const pic = onPicture(r);
      f.option_rects.push(pic ? pic.rect : null);
      if (pic) f.picture = pic.picture;
      if (!f.label) {
        const group = el.closest("fieldset");
        f.label = clean(group && group.querySelector("legend") && group.querySelector("legend").textContent) || "";
      }
      continue;
    }
    const kind = el.tagName === "SELECT" ? "choice" : el.type === "checkbox" ? "checkbox" : "text";
    const f = { id: String(els.length), kind, name: el.name || el.id || "", top: r.top + scrollY,
      label: ownLabel(el) || nearText(r, kind === "checkbox") || (kind === "checkbox" ? nearText(r, false) : ""),
      multiline: el.tagName === "TEXTAREA", max_length: el.maxLength > 0 ? el.maxLength : 0,
      existing: kind === "text" ? (el.value || "") : "" };
    if (kind === "choice") {
      f.options = [...el.options].map((o) => o.value).filter((v) => v !== "");
      f.option_labels = [...el.options].filter((o) => o.value !== "").map((o) => clean(o.textContent));
    }
    const pic = onPicture(r);
    if (pic) Object.assign(f, pic);
    els.push([el]);
    fields.push(f);
  }
  for (const f of radios.values()) {
    if (!f.label && f.option_rects.length) f.label = "";
    if (f.option_labels.some((l) => !l)) f.option_labels = [];
  }
  window.__fsFormEls = els;

  // Send the pictures that unlabelled boxes are on (read on this computer by FreeScribe)
  const needed = new Set(fields.filter((f) => typeof f.picture === "number" &&
    (!f.label || (f.kind === "radio" && !f.option_labels.length))).map((f) => f.picture));
  for (let i = 0; i < picEls.length; i++) {
    let data = "";
    if (needed.has(i)) {
      try {
        const blob = await (await fetch(picEls[i].img.currentSrc || picEls[i].img.src, { credentials: "same-origin" })).blob();
        if (blob.size < 15 * 1024 * 1024) {
          data = await new Promise((resolve) => {
            const reader = new FileReader();
            reader.onload = () => resolve(String(reader.result).split(",")[1] || "");
            reader.onerror = () => resolve("");
            reader.readAsDataURL(blob);
          });
        }
      } catch (_error) { data = ""; }
    }
    pictures.push({ data, width: picEls[i].img.naturalWidth });
  }
  const title = clean((document.querySelector("h1, h2, .formTitle") || {}).textContent) || clean(document.title);
  return { count: fields.length, fields, pictures, title: title.slice(0, 120) };
}

// Writes the reviewed answers into the boxes fsReadForm found. Never submits the form.
function fsWriteForm(values) {
  const els = window.__fsFormEls;
  if (!els) return { done: false, written: 0 };
  let written = 0;
  const fire = (el) => { for (const t of ["input", "change", "blur"]) el.dispatchEvent(new Event(t, { bubbles: true })); };
  for (const [id, value] of Object.entries(values || {})) {
    const group = els[Number(id)];
    if (!group || !group.length) continue;
    const el = group[0];
    if (el.type === "radio") {
      const pick = group.find((r) => r.value === value);
      if (pick) { pick.checked = true; fire(pick); written++; }
    } else if (el.type === "checkbox") {
      const on = value === "yes";
      if (el.checked !== on) { el.checked = on; fire(el); }
      written++;
    } else if (el.tagName === "SELECT") {
      if ([...el.options].some((o) => o.value === value)) { el.value = value; fire(el); written++; }
    } else {
      el.value = el.maxLength > 0 ? String(value).slice(0, el.maxLength) : String(value);
      fire(el);
      written++;
    }
  }
  return { done: true, written };
}
