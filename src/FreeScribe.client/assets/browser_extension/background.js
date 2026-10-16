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
  if (details.reason === "install") chrome.runtime.openOptionsPage();
});

chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create("freescribe-status", { periodInMinutes: 0.5 });
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
        let name = null;
        try {
          name = await fsLinkPatient();
        } catch (error) {
          if (error.kind === "refused") {
            notify("FreeScribe", error.message);
            return;
          }
        }
        await fsRecord("start");
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
  setTimeout(refreshBadge, 700);
});
