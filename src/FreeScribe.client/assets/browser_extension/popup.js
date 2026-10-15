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

async function act(action) {
  message("");
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

refresh();
timer = setInterval(refresh, 1500);
