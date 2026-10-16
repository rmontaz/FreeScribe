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

// Starts recording; from a patient's chart, that patient is linked to the session first
async function startRecording(tabId) {
  let name = null;
  try {
    name = await fsLinkPatient(tabId);
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
    if (msg.action === "start") return { patient: await startRecording(tab.id) };
    return fsRecord(msg.action);
  },
  async insert(msg, tab) {
    return fsInsertIntoTab(tab.id, msg.text);
  },
  async link(_msg, tab) {
    return { patient: await fsLinkPatient(tab.id) };
  },
  async options() {
    await chrome.runtime.openOptionsPage();
    return {};
  },
  async "hide-site"(_msg, tab) {
    const origin = fsOriginOf(tab.url || "");
    if (origin) await fsDisableSite(origin);
    return {};
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
    .catch((error) => reply({ ok: false, kind: error.kind || "refused", error: error.message }));
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
        if (name) notify("FreeScribe", `Recording. Patient: ${name}`);
      }
    } else if (command === "pause-resume") {
      const status = await fsStatus();
      await fsRecord(status.paused ? "resume" : "pause");
    } else if (command === "insert-note") {
      const note = await fsNote();
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
    notify("FreeScribe", error.message);
  }
  setTimeout(() => { refreshBadge(); refreshPanels(); }, 700);
});
