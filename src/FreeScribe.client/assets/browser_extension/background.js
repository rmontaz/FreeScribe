/*
 * background.js: keyboard shortcuts, the right-click menu and the toolbar badge.
 * Part of FreeScribe, released under the AGPL-3.0 license.
 */

importScripts("shared.js");

const MENU_ID = "freescribe-add-context";

chrome.runtime.onInstalled.addListener((details) => {
  chrome.contextMenus.create({
    id: MENU_ID,
    title: "Add to FreeScribe visit context",
    contexts: ["selection"],
  });
  chrome.alarms.create("freescribe-status", { periodInMinutes: 0.5 });
  fsSyncSites();
  if (details.reason === "install") chrome.runtime.openOptionsPage();
});

chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create("freescribe-status", { periodInMinutes: 0.5 });
  fsSyncSites();
});

// Tell every floating panel to check FreeScribe now (after a shortcut, say)
async function refreshPanels() {
  for (const origin of await fsSites()) {
    try {
      for (const tab of await chrome.tabs.query({ url: origin + "/*" })) {
        chrome.tabs.sendMessage(tab.id, { fs: "refresh" }).catch(() => {});
      }
    } catch (_error) { /* permission removed */ }
  }
}

// Starts recording; from a patient's chart, that patient is linked to the session first.
// ``choice`` answers a session that belongs to someone else: "new" or "switch". Without it,
// that case is refused with code "other_patient" so the clinician can choose.
async function startRecording(tabId, choice) {
  let name = null;
  try {
    name = await fsLinkPatient(tabId, choice || "link");
  } catch (error) {
    if (error.kind === "refused") throw error;
  }
  await fsRecord("start");
  return name;
}

/* ---------------------------------------------- requests from the floating panel */
const panelActions = {
  async call(msg) {
    return fsCall(msg.path, msg.body);
  },
  async record(msg, tab) {
    if (msg.action === "start") return { patient: await startRecording(tab.id, msg.choice) };
    return fsRecord(msg.action);
  },
  async preview(_msg, tab) {
    // Who is on this page, without linking anyone (the panel asks first)
    return { patient: await fsPatientFromTab(tab.id) };
  },
  async unlink() {
    return fsUnlinkPatient();
  },
  async insert(msg, tab) {
    return fsInsertIntoTab(tab.id, msg.text);
  },
  async link(msg, tab) {
    const name = await fsLinkPatient(tab.id, msg.mode || "link");
    if (!name) throw new FsError("No single patient is open on this page. Open the patient's chart first.", "refused");
    return { patient: name };
  },
  // Dictation: type a piece where the cursor is (the box may be in any frame of the page)
  async "dict-insert"(msg, tab) {
    let probes;
    try {
      probes = await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames: true }, func: fsProbeFocus });
    } catch (_error) {
      return { done: false };
    }
    const fields = probes.filter((p) => p.result && p.result.field);
    if (!fields.length) return { done: false };
    fields.sort((a, b) => (b.result.focused - a.result.focused) || (b.result.at - a.result.at) ||
      (b.result.big - a.result.big));
    const [typed] = await chrome.scripting.executeScript({
      target: { tabId: tab.id, frameIds: [fields[0].frameId] }, func: fsDictInsert, args: [msg.text, msg.sid],
    });
    return Object.assign({ frameId: fields[0].frameId }, (typed && typed.result) || { done: false });
  },
  // Dictation: swap the typed text for the AI-tidied version (only where it is unchanged)
  async "dict-replace"(msg, tab) {
    const frames = [...new Set(msg.frameIds || [])];
    if (frames.length !== 1) return { done: false, reason: frames.length ? "several" : "none" };
    const [result] = await chrome.scripting.executeScript({
      target: { tabId: tab.id, frameIds: frames }, func: fsDictReplace, args: [msg.sid, msg.text],
    });
    return (result && result.result) || { done: false, reason: "changed" };
  },
  // Talking through a form: read the boxes of the form on this page (the frame with the most)
  async "form-read"(_msg, tab) {
    let found;
    try {
      found = await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames: true }, func: fsReadForm });
    } catch (_error) {
      return { count: 0 };
    }
    const best = found.filter((f) => f.result && f.result.count).sort((a, b) => b.result.count - a.result.count)[0];
    return best ? Object.assign({ frameId: best.frameId }, best.result) : { count: 0 };
  },
  async "form-write"(msg, tab) {
    const [result] = await chrome.scripting.executeScript({
      target: { tabId: tab.id, frameIds: [msg.frameId] }, func: fsWriteForm, args: [msg.values],
    });
    return (result && result.result) || { done: false, written: 0 };
  },
  // The keyboard shortcuts as Chrome has them now (the clinician may have changed them, and Chrome
  // leaves one unset when another extension already uses its keys)
  async shortcuts() {
    const all = await chrome.commands.getAll();
    return Object.fromEntries(all.filter((c) => c.name !== "_execute_action").map((c) => [c.name, c.shortcut || ""]));
  },
  async "open-shortcuts"() {
    // Chrome's own page for changing an extension's shortcuts (an extension can't change them itself)
    await fsOpenInFront("chrome://extensions/shortcuts", 900, 700);
    return {};
  },
  // Load the extension's files again (FreeScribe keeps its folder up to date): the panel's "Update now"
  async "self-reload"() {
    // The notification stays after the reload, in case Chrome does not bring the extension back by itself
    notify("Updating the FreeScribe extension", "The panel comes back in a few seconds. If it doesn't, open " +
      "chrome://extensions and press the reload arrow on FreeScribe.");
    setTimeout(() => chrome.runtime.reload(), 400);
    return {};
  },
  async options() {
    // OSCAR's chart is a pop-up window above the main Chrome window, so the settings open in
    // their own window in front of it (a tab would open behind the chart, out of sight)
    await fsOpenInFront(chrome.runtime.getURL("options.html"), 600, 720);
    return {};
  },
  async "hide-site"(_msg, tab) {
    const origin = fsOriginOf(tab.url || "");
    if (!origin) throw new FsError("This page's address could not be read.", "refused");
    await fsDisableSite(origin);
    notify("FreeScribe panel hidden", `It won't show on ${origin.replace(/^https?:\/\//, "")} any more. ` +
      "To bring it back, click the FreeScribe button in Chrome's toolbar and switch on “Floating panel on this site”.");
    return { origin };
  },
};

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  const action = msg && panelActions[msg.fs];
  if (!action || !sender.tab) return false;
  action(msg, sender.tab)
    .then((data) => {
      reply({ ok: true, data });
      if (msg.fs === "record" || msg.fs === "link") { refreshBadge(); refreshPanels(); }
    })
    .catch((error) => reply({ ok: false, kind: error.kind || "refused", error: error.message,
                              code: error.code || "", details: error.details || {} }));
  return true;   // the reply comes later
});

function notify(title, message) {
  chrome.notifications.create({
    type: "basic", iconUrl: "icons/icon128.png", title, message, priority: 1,
  });
}

async function refreshBadge() {
  try {
    const status = await fsStatus();
    await fsShowBadge(status);
    return status;
  } catch (_error) {
    await fsShowBadge(null);
    return null;
  }
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "freescribe-status") refreshBadge();
});

chrome.contextMenus.onClicked.addListener(async (info) => {
  if (info.menuItemId !== MENU_ID || !info.selectionText) return;
  try {
    await fsAddContext(info.selectionText);
    notify("Added to FreeScribe", "The selected text was added to this visit’s Context.");
  } catch (error) {
    notify("FreeScribe", error.message);
  }
});

chrome.commands.onCommand.addListener(async (command) => {
  try {
    if (command === "toggle-recording") {
      const status = await fsStatus();
      if (status.recording) {
        await fsRecord("stop");
        notify("FreeScribe", status.writes_note ? "Stopped. Writing the note…" : "Stopped.");
      } else {
        // Starting from a patient's chart links that patient to the session
        const tab = await fsActiveTab();
        const name = await startRecording(tab && /^https?:/.test(tab.url || "") ? tab.id : undefined);
        // System notifications can show on the lock screen: no patient names in them
        notify("FreeScribe", name ? "Recording. The chart's patient is linked to this session." : "Recording.");
      }
    } else if (command === "pause-resume") {
      const status = await fsStatus();
      await fsRecord(status.paused ? "resume" : "pause");
    } else if (command === "dictate") {
      // The floating panel on the page does the dictating (it types into the page as you talk)
      const tab = await fsActiveTab();
      let shown = false;
      if (tab && /^https?:/.test(tab.url || "")) {
        try {
          const reply = await chrome.tabs.sendMessage(tab.id, { fs: "voice-toggle" }, { frameId: 0 });
          shown = !!(reply && reply.ok);
        } catch (_error) { shown = false; }
      }
      if (!shown) {
        notify("FreeScribe", "Dictation works on pages where the FreeScribe panel is on. Click the FreeScribe button " +
          "and switch on “Floating panel on this site”.");
      }
    } else if (command === "insert-note") {
      // Only the note of the patient whose chart is open here (as the panel's Insert does)
      const tab = await fsActiveTab();
      const chart = tab && /^https?:/.test(tab.url || "") ? await fsPatientFromTab(tab.id) : null;
      let note;
      if (chart) {
        const found = await fsCall("/api/note_for", { patient: chart });
        if (found.match === "none") {
          // System notifications can show on the lock screen: no patient names in them
          notify("FreeScribe", "Not inserted: this chart is a different patient from FreeScribe's note. " +
            "Open the FreeScribe panel on the chart to see whose note it is.");
          return;
        }
        note = found;
      } else {
        note = await fsNote();
      }
      if (!note.plain) {
        notify("FreeScribe", note.busy ? `Not ready yet (${note.busy}).` : "This session has no note yet.");
        return;
      }
      const result = await fsInsertIntoActiveTab(note.plain);
      if (!result.done) {
        notify("FreeScribe", "Click in the note box in your EMR first (where the note should go), then press the shortcut again.");
      }
    }
  } catch (error) {
    notify("FreeScribe", error.code === "other_patient"
      ? "Not started: this FreeScribe session belongs to another patient. Use the FreeScribe panel on the chart " +
        "to start a new session for this patient or move this one."
      : error.message);
  }
  setTimeout(() => { refreshBadge(); refreshPanels(); }, 700);
});
