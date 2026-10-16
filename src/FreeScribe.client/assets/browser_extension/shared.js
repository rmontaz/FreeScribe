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

  // The chart's patient number: in the address, or else the one the page links to most
  let id = (location.href.match(/[?&](?:demographicNo|demographic_no|demographicId)=(\d+)/i) || [])[1];
  if (!id) {
    const counts = {};
    for (const m of document.documentElement.innerHTML.slice(0, 2000000)
      .matchAll(/demographic(?:_no|No|Id)=(\d+)/gi)) counts[m[1]] = (counts[m[1]] || 0) + 1;
    id = Object.keys(counts).sort((a, b) => counts[b] - counts[a])[0];
  }
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

// The patient of the OSCAR chart in the active tab, or null.
async function fsPatientFromActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!tab || !/^https?:/.test(tab.url || "")) return null;
  let results;
  try {
    results = await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames: true }, func: fsReadOscarPatient });
  } catch (_error) {
    return null;
  }
  // The top page first, then frames
  results.sort((a, b) => (a.frameId === 0 ? -1 : 0) - (b.frameId === 0 ? -1 : 0));
  const hit = results.find((r) => r.result && r.result.found);
  return hit ? hit.result.patient : null;
}

// Sends the chart's patient to FreeScribe. Returns the name FreeScribe linked, or null.
async function fsLinkPatient() {
  const patient = await fsPatientFromActiveTab();
  if (!patient) return null;
  const reply = await fsCall("/api/patient", { patient });
  return reply.patient || null;
}
