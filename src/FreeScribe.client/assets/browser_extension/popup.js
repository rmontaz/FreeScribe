/*
 * popup.js: the FreeScribe button in Chrome's toolbar.
 * Part of FreeScribe, released under the AGPL-3.0 license.
 */

const $ = (id) => document.getElementById(id);
let current = null;          // last status
let noteText = "";           // plain-text note of the open session
let noteFor = "";            // session id + phase the note was fetched for
let timer = null;
let stopMessage = false;     // "writing the note" message is showing

const PHASES = {
  idle: ["Ready", ""],
  recording: ["Recording", "recording"],
  paused: ["Paused", "paused"],
  working: ["Working", "working"],
  note_ready: ["Note ready", "ready"],
};

function show(section) {
  $("setup").hidden = section !== "setup";
  $("main").hidden = section !== "main";
}

function message(text, kind = "") {
  const el = $("message");
  el.textContent = text || "";
  el.className = "message" + (kind ? " " + kind : "");
  el.hidden = !text;
}

function setPill(text, cls) {
  const pill = $("pill");
  pill.textContent = text;
  pill.className = "pill" + (cls ? " " + cls : "");
}

async function showSetup(text, error) {
  show("setup");
  setPill(error && error.kind === "offline" ? "Offline" : "Not paired", "");
  $("setup-text").textContent = text;
  const { code } = await fsSettings();
  if (!$("code").value) $("code").value = code;
  $("setup-error").hidden = true;
  fsShowBadge(null);
}

async function refresh() {
  let status;
  try {
    status = await fsStatus();
  } catch (error) {
    current = null;
    if (error.kind === "unpaired") {
      await showSetup("Connect this extension to FreeScribe on this computer.", error);
      if (error.message && (await fsSettings()).code) {
        $("setup-error").textContent = error.message;
        $("setup-error").hidden = false;
      }
    } else {
      await showSetup(error.message, error);
    }
    return;
  }
  current = status;
  show("main");
  fsShowBadge(status);
  render(status);
}

function render(status) {
  if (stopMessage && !status.recording && status.phase !== "working") {
    stopMessage = false;
    message(status.phase === "note_ready" ? "The note is ready." : "", "ok");
  }
  const [label, cls] = PHASES[status.phase] || ["", ""];
  setPill(status.phase === "working" && status.busy ? status.busy : label, cls);

  const session = status.session;
  $("title").textContent = session ? session.title : "No session open";
  const parts = [];
  if (session) {
    parts.push(session.has_transcript ? "Transcript ✓" : "No transcript yet");
    if (status.writes_note) parts.push(session.has_note ? "Note ✓" : "No note yet");
  }
  if (!status.writes_note) parts.push("Transcribe-only mode");
  $("detail").textContent = parts.join(" · ");
  const patient = session && session.patient;
  $("patient").textContent = patient ? `Patient: ${patient}${session.patient_dob ? " · born " + session.patient_dob : ""}` : "";
  $("link").textContent = patient ? "Change" : "Link patient from chart";
  $("link").hidden = !session;
  $("unlink").hidden = !patient;
  $("unlink").disabled = !!status.recording;

  const record = $("record");
  if (status.recording) {
    record.textContent = status.writes_note ? "Stop and write note" : "Stop";
    record.className = "danger wide big";
    record.disabled = false;
  } else {
    record.textContent = status.phase === "working" ? (status.busy || "Working") + "…" : "Start recording";
    record.className = "primary wide big";
    record.disabled = status.phase === "working";
  }
  const pause = $("pause");
  pause.hidden = !status.recording;
  pause.textContent = status.paused ? "Resume" : "Pause";

  const hasNote = !!(session && session.has_note) && status.phase !== "recording" && status.phase !== "paused";
  $("note-card").hidden = !hasNote;
  const key = session ? `${session.id}:${status.phase}:${status.busy}` : "";
  if (hasNote && key !== noteFor) {
    noteFor = key;
    loadNote();
  }
}

async function loadNote() {
  try {
    const note = await fsNote();
    noteText = note.plain || "";
    $("note").textContent = noteText;
    $("note-size").textContent = noteText ? `${noteText.split(/\s+/).filter(Boolean).length} words` : "";
  } catch (error) {
    message(error.message, "warn");
  }
}

// Asks a question about the patient with buttons: [{label, primary, run}]
function ask(title, lines, buttons) {
  const box = $("choice");
  box.innerHTML = "";
  const t = document.createElement("div"); t.className = "choice-title"; t.textContent = title; box.appendChild(t);
  for (const line of lines) {
    const p = document.createElement("div"); p.className = "small"; p.textContent = line; box.appendChild(p);
  }
  const row = document.createElement("div"); row.className = "choice-buttons";
  for (const b of buttons) {
    const el = document.createElement("button");
    el.className = b.primary ? "primary" : "secondary";
    el.textContent = b.label;
    el.addEventListener("click", async () => { box.hidden = true; if (b.run) await b.run(); setTimeout(refresh, 300); });
    row.appendChild(el);
  }
  box.appendChild(row);
  box.hidden = false;
}

const born = (dob) => dob ? ` (born ${dob})` : "";

// "Link patient from chart": who is on this page? Ask before linking anyone.
async function linkPatient() {
  const p = await fsPatientFromTab();
  if (!p) return message("No single patient is open in this tab. Open the patient’s chart, then link it.", "warn");
  const name = fsPatientName(p);
  const session = current && current.session;
  const run = (mode) => async () => {
    try {
      const linked = await fsLinkPatient(undefined, mode);
      message(mode === "new" ? `New session for ${linked}.` : mode === "switch" ? `This session is now for ${linked}.`
        : `Linked ${linked} from the chart. Forms will use the chart’s details.`, "ok");
    } catch (error) { message(error.message, "warn"); }
  };
  if (session && session.patient && session.patient.toLowerCase() !== name.toLowerCase()) {
    ask("This session is for someone else", [`Session: ${session.patient}${born(session.patient_dob)}`,
      `This chart: ${name}${born(p.dob)}`], [
      { label: `New session for ${name}`, primary: true, run: run("new") },
      { label: `Move this session to ${name}`, run: run("switch") },
      { label: "Cancel" }]);
  } else if (session && session.patient) {
    message(`${name} is already linked.`, "ok");
  } else {
    ask("Link this chart to the session?", [`${name}${born(p.dob)}`],
      [{ label: "Link", primary: true, run: run("link") }, { label: "Cancel" }]);
  }
}

async function unlinkPatient() {
  try {
    const name = current && current.session && current.session.patient;
    await fsUnlinkPatient();
    message(name ? `Unlinked ${name}.` : "No patient was linked.", "ok");
  } catch (error) { message(error.message, "warn"); }
}

async function act(action) {
  message("");
  // Starting from a patient's chart links that patient to the session; a session that
  // belongs to another patient is not recorded into
  if (action === "start" && current && current.session) {
    try {
      await fsLinkPatient();
    } catch (error) {
      if (error.code === "other_patient") {
        const d = error.details || {};
        const go = (mode) => async () => {
          try { await fsLinkPatient(undefined, mode); await fsRecord("start"); message(`Recording for ${d.chart}.`, "ok"); }
          catch (e) { message(e.message, "warn"); }
        };
        ask("This session is for someone else", [`Session: ${d.current}${born(d.current_dob)}`,
          `This chart: ${d.chart}${born(d.chart_dob)}`].concat(d.has_content
            ? ["Moving it means the recording and note already in this session will belong to " + d.chart + "."] : []), [
          { label: `New session for ${d.chart}`, primary: true, run: go("new") },
          { label: `Move this session to ${d.chart}`, run: go("switch") },
          { label: "Cancel" }]);
        return;
      }
      message(error.message, "warn");
      if (error.kind === "refused") return;
    }
  }
  try {
    await fsRecord(action);
    if (action === "stop") {
      message(current && current.writes_note ? "Stopped. FreeScribe is writing the note…" : "Stopped.", "ok");
      stopMessage = true;
    }
  } catch (error) {
    message(error.message, "warn");
  }
  setTimeout(refresh, 400);
}

$("link").addEventListener("click", async () => {
  message("");
  await linkPatient();
  setTimeout(refresh, 300);
});
$("unlink").addEventListener("click", async () => {
  message("");
  await unlinkPatient();
  setTimeout(refresh, 300);
});
$("record").addEventListener("click", () => act(current && current.recording ? "stop" : "start"));
$("pause").addEventListener("click", () => act(current && current.paused ? "resume" : "pause"));

$("insert").addEventListener("click", async () => {
  message("");
  if (!noteText) await loadNote();
  if (!noteText) return message("This session has no note yet.", "warn");
  const result = await fsInsertIntoActiveTab(noteText);
  if (result.done) {
    message("Inserted into the EMR. Check it before you sign.", "ok");
  } else if (result.reason === "page") {
    message("Open the patient’s chart in this tab first (Chrome pages can’t be written to).", "warn");
  } else {
    message("Click in the note box in your EMR first (where the note should go), then press Insert again.", "warn");
  }
});

$("copy").addEventListener("click", async () => {
  if (!noteText) await loadNote();
  try {
    await navigator.clipboard.writeText(noteText);
    message("Copied. Paste it into the EMR with Ctrl+V (⌘V on a Mac).", "ok");
  } catch (_error) {
    message("Copying was blocked by Chrome. Select the note text and copy it instead.", "warn");
  }
});

$("pair").addEventListener("click", async () => {
  const code = $("code").value.trim();
  if (!code) return;
  const { port } = await fsSettings();
  await fsSaveSettings(code, port);
  await refresh();
});
$("code").addEventListener("keydown", (event) => { if (event.key === "Enter") $("pair").click(); });
$("settings").addEventListener("click", () => chrome.runtime.openOptionsPage());

chrome.commands.getAll((commands) => {
  const keys = commands.filter((c) => c.shortcut && c.name !== "_execute_action")
    .map((c) => `${c.shortcut} ${{ "toggle-recording": "start/stop", "pause-resume": "pause", "insert-note": "insert note" }[c.name] || c.name}`);
  $("keys").textContent = keys.length ? "Shortcuts: " + keys.join(" · ") : "Set keyboard shortcuts in chrome://extensions/shortcuts";
});

/* ---------------------------------------------- the floating panel on this site */
let siteOrigin = "";

async function showSite() {
  const tab = await fsActiveTab();
  siteOrigin = tab ? fsOriginOf(tab.url || "") : "";
  $("site").hidden = !siteOrigin;
  if (!siteOrigin) return;
  $("site-origin").textContent = siteOrigin.replace(/^https?:\/\//, "");
  const sites = await fsSites();
  const granted = await chrome.permissions.contains({ origins: [siteOrigin + "/*"] });
  $("site-toggle").checked = sites.includes(siteOrigin) && granted;
}

$("site-toggle").addEventListener("change", (event) => {
  const on = event.target.checked;
  if (!on) {
    fsDisableSite(siteOrigin).then(() => message("The floating panel is off for this site.", "ok"));
    return;
  }
  // Chrome asks the clinician to allow the extension on this site (it must be asked
  // straight from the click, before anything else is awaited)
  chrome.permissions.request({ origins: [siteOrigin + "/*"] }, async (granted) => {
    if (!granted) {
      event.target.checked = false;
      message("The panel needs permission to show on this site.", "warn");
      return;
    }
    try {
      await fsEnableSite(siteOrigin);
      message("The floating panel is on. It also appears in OSCAR's pop-up chart windows.", "ok");
    } catch (error) {
      event.target.checked = false;
      message(`The panel could not be turned on here (${error.message}).`, "warn");
    }
  });
});

showSite();
refresh();
timer = setInterval(refresh, 1500);
