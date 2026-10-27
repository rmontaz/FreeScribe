/*
 * panel.js: the floating FreeScribe panel shown on the EMR pages it is turned on for,
 * including OSCAR's pop-up chart windows (which have no toolbar for the extension button).
 *
 * It floats above the page, can be dragged anywhere, snaps to the left or right edge when
 * dropped near one, and shrinks to a small button. Its place is remembered per site.
 * It lives in a shadow root, so the EMR's styles and the panel's never mix.
 * All talking to FreeScribe goes through the extension's background worker.
 *
 * Part of FreeScribe, released under the AGPL-3.0 license.
 */
(() => {
  if (window.top !== window || !document.body) return;
  const FS_VERSION = (() => { try { return chrome.runtime.getManifest().version; } catch (_e) { return ""; } })();
  const old = document.getElementById("freescribe-panel-host");
  if (old) {
    // The same version is already here; an older one (before an update) gives way to this one
    if (old.dataset.version === FS_VERSION) return;
    old.remove();
  }

  /* ------------------------------------------------------------------ icons */
  const svg = (paths, size = 16) =>
    `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" ` +
    `stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
  const ICON = {
    mic: svg('<rect x="9" y="2" width="6" height="12" rx="3"/><path d="M19 10v1a7 7 0 0 1-14 0v-1"/><path d="M12 18v4"/>'),
    stop: svg('<rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" stroke="none"/>'),
    pause: svg('<rect x="6" y="5" width="4" height="14" rx="1" fill="currentColor" stroke="none"/><rect x="14" y="5" width="4" height="14" rx="1" fill="currentColor" stroke="none"/>'),
    play: svg('<path d="M7 5l12 7-12 7z" fill="currentColor" stroke="none"/>'),
    insert: svg('<path d="M12 3v12"/><path d="M7 10l5 5 5-5"/><path d="M4 21h16"/>'),
    copy: svg('<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>'),
    link: svg('<path d="M10 13a5 5 0 0 0 7.1 0l3-3a5 5 0 0 0-7.1-7.1l-1.2 1.2"/><path d="M14 11a5 5 0 0 0-7.1 0l-3 3a5 5 0 0 0 7.1 7.1l1.2-1.2"/>', 14),
    minimize: svg('<path d="M5 12h14"/>'),
    left: svg('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>'),
    right: svg('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/>'),
    more: svg('<circle cx="12" cy="5" r="1.4" fill="currentColor"/><circle cx="12" cy="12" r="1.4" fill="currentColor"/><circle cx="12" cy="19" r="1.4" fill="currentColor"/>'),
    check: svg('<path d="M5 12.5l4.5 4.5L19 7.5"/>', 12),
    user: svg('<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>', 14),
    dictate: svg('<rect x="3" y="3" width="8" height="12" rx="4"/><path d="M14 6h7M14 10h7M14 14h5M3 19h18"/>'),
    mark: '<svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true"><rect width="24" height="24" rx="7" fill="#1f8a4c"/>' +
      '<g stroke="#fff" stroke-width="2.2" stroke-linecap="round"><path d="M7 10v4"/><path d="M10.3 7.5v9"/><path d="M13.7 9v6"/><path d="M17 11v2"/></g></svg>',
  };

  /* ------------------------------------------------------------------ styles */
  const CSS = `
:host { all: initial; }
* { box-sizing: border-box; }
.root {
  --bg: rgba(255,255,255,.96); --surface: #f5f6f7; --line: rgba(15,23,42,.10); --text: #16181d;
  --muted: #5f6672; --faint: #8b919c; --accent: #1f8a4c; --accent-hover: #19733f; --accent-soft: #e4f4ea;
  --rec: #e5484d; --rec-hover: #cf3b40; --rec-soft: #fdebec; --amber: #b45309; --amber-soft: #fdf1e2;
  --blue: #2f6fdf; --blue-soft: #e8f0fd; --shadow: 0 12px 32px rgba(15,23,42,.18), 0 2px 6px rgba(15,23,42,.08);
  font: 13px/1.4 system-ui, -apple-system, "Segoe UI", Roboto, Ubuntu, sans-serif; color: var(--text);
  -webkit-font-smoothing: antialiased; color-scheme: light dark;
}
@media (prefers-color-scheme: dark) {
  .root {
    --bg: rgba(28,29,33,.96); --surface: #25262b; --line: rgba(255,255,255,.10); --text: #eceef1;
    --muted: #a5abb6; --faint: #7d838e; --accent: #34a866; --accent-hover: #3cba72; --accent-soft: rgba(52,168,102,.16);
    --rec: #f2555a; --rec-hover: #ff6a6e; --rec-soft: rgba(242,85,90,.16); --amber: #f0a43a; --amber-soft: rgba(240,164,58,.15);
    --blue: #6aa1ff; --blue-soft: rgba(106,161,255,.15); --shadow: 0 12px 32px rgba(0,0,0,.45), 0 2px 6px rgba(0,0,0,.3);
  }
}
button { font: inherit; color: inherit; border: 0; background: none; cursor: pointer; padding: 0; }
button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
button:disabled { cursor: default; opacity: .55; }

/* ---- the card */
.card {
  width: 316px; background: var(--bg); border: 1px solid var(--line); border-radius: 16px;
  box-shadow: var(--shadow); overflow: hidden; backdrop-filter: blur(14px) saturate(1.4);
  -webkit-backdrop-filter: blur(14px) saturate(1.4);
}
.card.opening { animation: pop .16s ease-out; }
@keyframes pop { from { transform: scale(.97); opacity: 0; } to { transform: none; opacity: 1; } }
.docked-left .card { border-radius: 0 16px 16px 0; border-left: 0; }
.docked-right .card { border-radius: 16px 0 0 16px; border-right: 0; }
.head {
  display: flex; align-items: center; gap: 8px; padding: 10px 8px 10px 12px; cursor: grab; user-select: none;
  border-bottom: 1px solid var(--line);
}
.dragging .head { cursor: grabbing; }
.brand { display: grid; place-items: center; }
.chip {
  display: inline-flex; align-items: center; gap: 6px; padding: 2px 9px 2px 8px; border-radius: 999px;
  font-size: 11.5px; font-weight: 600; background: var(--surface); color: var(--muted); white-space: nowrap;
  max-width: 170px; overflow: hidden; text-overflow: ellipsis;
}
.chip i { width: 7px; height: 7px; border-radius: 50%; background: currentColor; flex: none; }
.chip.recording { background: var(--rec-soft); color: var(--rec); }
.chip.recording i { animation: blink 1.2s infinite; }
.chip.paused { background: var(--amber-soft); color: var(--amber); }
.chip.working { background: var(--blue-soft); color: var(--blue); }
.chip.ready { background: var(--accent-soft); color: var(--accent); }
@keyframes blink { 50% { opacity: .2; } }
.spacer { flex: 1; }
.tool { width: 28px; height: 28px; border-radius: 8px; display: grid; place-items: center; color: var(--muted); }
.tool:hover { background: var(--surface); color: var(--text); }
.tool.on { color: var(--accent); background: var(--accent-soft); }

.body { padding: 12px; display: grid; gap: 10px; }
.who { display: flex; align-items: center; gap: 8px; min-width: 0; }
.who .avatar { width: 28px; height: 28px; border-radius: 50%; display: grid; place-items: center; flex: none;
  background: var(--surface); color: var(--muted); }
.who .names { min-width: 0; flex: 1; }
.who .name { font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.who .sub { color: var(--faint); font-size: 11.5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.linkbtn { display: inline-flex; align-items: center; gap: 5px; font-size: 11.5px; font-weight: 600;
  color: var(--accent); padding: 4px 8px; border-radius: 7px; flex: none; }
.linkbtn:hover { background: var(--accent-soft); }
.linkbtn.muted-btn { color: var(--muted); }
.who-btns { display: flex; flex-direction: column; align-items: flex-end; gap: 0; flex: none; }
.linkbtn.muted-btn:hover { background: var(--surface); color: var(--text); }
.body > * { min-width: 0; }
.tpl { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--muted); margin-top: -2px; min-width: 0; }
.tpl .pick { flex: none; }
.tpl .sub { flex: 1; min-width: 0; }
.tpl .pick { font-weight: 650; color: var(--text); padding: 3px 8px; border-radius: 7px; border: 1px solid var(--line);
  background: var(--bg); max-width: 190px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tpl .pick:hover { background: var(--surface); }
.tpl .sub { color: var(--faint); font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tpl-list { border: 1px solid var(--line); border-radius: 12px; padding: 4px; background: var(--bg); display: grid; }
.tpl-list button { text-align: left; padding: 7px 10px; border-radius: 8px; display: flex; gap: 8px; align-items: center; }
.tpl-list button:hover { background: var(--surface); }
.tpl-list .mark { width: 14px; color: var(--accent); }
.tpl-list .dflt { color: var(--faint); font-size: 11px; margin-left: auto; }
.tpl-list .make { color: var(--accent); font-weight: 600; border-top: 1px solid var(--line); border-radius: 0 0 8px 8px; }
.avatar.linked { background: var(--accent-soft) !important; color: var(--accent) !important; }
.ask { border: 1px solid var(--line); border-radius: 12px; padding: 10px; background: var(--surface); display: grid; gap: 6px; }
.ask.warn { background: var(--amber-soft); border-color: transparent; }
.ask-title { font-weight: 650; }
.ask-name { font-size: 14px; font-weight: 600; }
.ask-line { font-size: 12.5px; }
.ask-note { font-size: 11.5px; color: var(--amber); }
.ask .sub { color: var(--faint); font-weight: 400; }
.row2 { display: flex; gap: 8px; }
.col { display: grid; gap: 6px; margin-top: 2px; }
.small-primary { height: 34px; border-radius: 10px; padding: 0 12px; font-weight: 650; color: #fff; background: var(--accent); }
.small-primary:hover { background: var(--accent-hover); }
.row2 .small-primary { flex: 1; }
.col .ghost { justify-content: center; }

.timer { display: flex; align-items: center; gap: 10px; padding: 2px 2px 0; }
.time { font-size: 26px; font-weight: 650; letter-spacing: -.02em; font-variant-numeric: tabular-nums; }
.bars { display: flex; align-items: center; gap: 3px; height: 22px; }
.bars span { width: 3px; border-radius: 2px; background: var(--rec); height: 30%; animation: wave 1s ease-in-out infinite; }
.bars span:nth-child(2) { animation-delay: .15s; } .bars span:nth-child(3) { animation-delay: .3s; }
.bars span:nth-child(4) { animation-delay: .45s; } .bars span:nth-child(5) { animation-delay: .6s; }
.paused .bars span { animation: none; background: var(--amber); height: 30%; }
@keyframes wave { 50% { height: 100%; } }

.controls { display: flex; gap: 8px; }
.big {
  flex: 1; height: 44px; border-radius: 12px; display: inline-flex; align-items: center; justify-content: center;
  gap: 8px; font-weight: 650; font-size: 14px; color: #fff; background: var(--accent);
  transition: background .12s, transform .06s;
}
.big:hover:not(:disabled) { background: var(--accent-hover); }
.big:active:not(:disabled) { transform: scale(.985); }
.big.stop { background: var(--rec); }
.big.stop:hover:not(:disabled) { background: var(--rec-hover); }
.square { width: 44px; height: 44px; border-radius: 12px; display: grid; place-items: center;
  background: var(--surface); color: var(--text); border: 1px solid var(--line); }
.square:hover { filter: brightness(.97); }
.square.on { color: var(--rec); border-color: var(--rec); background: var(--rec-soft); }

.progress { height: 4px; border-radius: 4px; background: var(--blue-soft); overflow: hidden; }
.progress span { display: block; width: 40%; height: 100%; border-radius: 4px; background: var(--blue);
  animation: slide 1.1s ease-in-out infinite; }
@keyframes slide { from { transform: translateX(-100%); } to { transform: translateX(250%); } }
.working-text { color: var(--muted); font-size: 12px; }

.note { border: 1px solid var(--line); border-radius: 12px; background: var(--surface); overflow: hidden; }
.note-head { display: flex; justify-content: space-between; align-items: center; padding: 8px 10px 0;
  font-weight: 600; font-size: 12px; color: var(--muted); }
.note pre { margin: 6px 10px 0; max-height: 168px; overflow: auto; white-space: pre-wrap; word-wrap: break-word;
  font: 12.5px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; color: var(--text); }
.note .row { display: flex; gap: 8px; padding: 10px; }
.insert { flex: 1; height: 36px; border-radius: 10px; display: inline-flex; align-items: center; justify-content: center;
  gap: 7px; font-weight: 650; color: #fff; background: var(--text); }
.insert:hover { filter: brightness(1.15); }
@media (prefers-color-scheme: dark) { .insert { color: #16181d; } }
.ghost { height: 36px; padding: 0 12px; border-radius: 10px; display: inline-flex; align-items: center; gap: 6px;
  font-weight: 600; color: var(--text); border: 1px solid var(--line); background: var(--bg); }
.ghost:hover { background: var(--surface); }
.hint { color: var(--faint); font-size: 11.5px; padding: 0 10px 10px; margin-top: -4px; }

.msg { font-size: 12px; padding: 8px 10px; border-radius: 10px; background: var(--blue-soft); color: var(--blue); }
.msg.ok { background: var(--accent-soft); color: var(--accent); }
.msg.warn { background: var(--amber-soft); color: var(--amber); }
.offline { display: grid; gap: 10px; }
.offline p { margin: 0; color: var(--muted); }

.foot { padding: 8px 12px; border-top: 1px solid var(--line); color: var(--faint); font-size: 11px;
  display: flex; align-items: center; gap: 10px; white-space: nowrap; }
.foot { overflow: hidden; }
.foot-link { font-size: 11px; color: var(--accent); text-decoration: underline; padding: 0; }
kbd { font: 600 10.5px system-ui, sans-serif; padding: 1px 5px; border-radius: 5px; background: var(--surface);
  border: 1px solid var(--line); color: var(--muted); }

.menu { position: absolute; top: 44px; right: 8px; min-width: 190px; padding: 6px; border-radius: 12px;
  background: var(--bg); border: 1px solid var(--line); box-shadow: var(--shadow); z-index: 2; }
.menu button { display: block; width: 100%; text-align: left; padding: 8px 10px; border-radius: 8px; }
.menu button:hover { background: var(--surface); }

/* ---- the small button */
.bubble {
  position: relative; width: 52px; height: 52px; border-radius: 50%; background: var(--bg); border: 1px solid var(--line);
  box-shadow: var(--shadow); display: grid; place-items: center; color: var(--accent); cursor: grab; user-select: none;
  touch-action: none;
}
.bubble:hover { transform: scale(1.04); }
.bubble.recording { color: var(--rec); box-shadow: 0 0 0 0 var(--rec), var(--shadow); animation: ring 1.6s infinite; }
.bubble.paused { color: var(--amber); }
.bubble.working { color: var(--blue); }
.bubble.working::after { content: ""; position: absolute; inset: -3px; border-radius: 50%;
  border: 2.5px solid transparent; border-top-color: var(--blue); animation: spin 1s linear infinite; }
.bubble.offline { color: var(--faint); }
.bubble .badge.warn { background: var(--amber); font: 700 11px system-ui, sans-serif; }
.bubble .badge { position: absolute; top: -2px; right: -2px; width: 18px; height: 18px; border-radius: 50%;
  background: var(--accent); color: #fff; display: grid; place-items: center; border: 2px solid var(--bg); }
@keyframes ring { 0% { box-shadow: 0 0 0 0 rgba(229,72,77,.45), var(--shadow); }
  70% { box-shadow: 0 0 0 12px rgba(229,72,77,0), var(--shadow); } 100% { box-shadow: 0 0 0 0 rgba(229,72,77,0), var(--shadow); } }
@keyframes spin { to { transform: rotate(360deg); } }
.docked-left .bubble { border-radius: 0 16px 16px 0; width: 44px; height: 56px; border-left: 0; }
.docked-right .bubble { border-radius: 16px 0 0 16px; width: 44px; height: 56px; border-right: 0; }
.mini-wrap { display: flex; align-items: center; gap: 8px; }
.docked-right .mini-wrap { flex-direction: row-reverse; }
.mini-time { padding: 4px 10px; border-radius: 999px; background: var(--bg); border: 1px solid var(--line);
  box-shadow: var(--shadow); font-weight: 650; font-variant-numeric: tabular-nums; color: var(--rec); }
.mini-time.paused { color: var(--amber); }
`;

  /* ------------------------------------------------------------------ setup */
  const host = document.createElement("div");
  host.id = "freescribe-panel-host";
  host.dataset.version = FS_VERSION;
  host.style.cssText = "position:fixed;z-index:2147483647;top:0;left:0;margin:0;padding:0;border:0;";
  const shadow = host.attachShadow({ mode: "open" });
  const sheet = new CSSStyleSheet();
  sheet.replaceSync(CSS);
  shadow.adoptedStyleSheets = [sheet];
  const root = document.createElement("div");
  root.className = "root";
  shadow.appendChild(root);
  document.documentElement.appendChild(host);

  const KEY = "fsPanel:" + location.origin;
  let place = { side: "right", y: Math.round(window.innerHeight * 0.3), x: 0, collapsed: true };
  let status = null;          // last status from FreeScribe
  let problem = null;         // {kind, message} when FreeScribe can't be reached
  let noteText = "";
  let noteKey = "";
  let message = null;         // {text, kind}
  let messageTimer = null;
  let menuOpen = false;
  let choice = null;          // a question about the patient: {kind: "link"|"start", chart, chartDob, current, currentDob, hasContent}
  let busyAction = false;
  let seconds = 0;
  let shownAt = Date.now();
  let pollTimer = null;
  let alive = true;
  // The shortcuts as Chrome has them: {command name: "Alt+Shift+R"}, "" when unset
  let keys = { "toggle-recording": "", "pause-resume": "", "insert-note": "", dictate: "" };
  let keysRead = false;
  let seenQuestion = "";
  let templateOpen = false;
  let pageChart = null;          // the patient of the chart on this page (read from the page)
  let pageChartRead = false;
  let guard = null;
  let chartNote = null;          // FreeScribe's answer for this page's chart (see note_for)
  let noteOwner = "";            // whose note the panel shows              // Insert was stopped: the chart is another patient's {chart, session}      // the note template list is open
  const tplName = (n) => String(n || "").replace(/ - Default$/, "");
  // "1.10.0" is newer than "1.9.2"
  const newer = (a, b) => {
    const x = String(a).split(".").map(Number), y = String(b).split(".").map(Number);
    for (let i = 0; i < Math.max(x.length, y.length); i++) {
      if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) > (y[i] || 0);
    }
    return false;
  };

  const send = (msg) => new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage(msg, (reply) => {
        if (chrome.runtime.lastError) resolve({ ok: false, kind: "offline", error: "The extension was updated. Reload this page." });
        else resolve(reply || { ok: false, error: "No reply" });
      });
    } catch (_error) {
      // The extension was reloaded or removed: this copy of the panel is orphaned
      resolve({ ok: false, kind: "gone", error: "The extension was updated. Reload this page." });
    }
  });

  async function readKeys() {
    const reply = await send({ fs: "shortcuts" });
    if (!reply.ok) return;
    keys = Object.assign(keys, reply.data);
    keysRead = true;
    window.__fsKeys = keys;          // for the dictation bar (voice.js)
    render();
  }
  const keyHint = (name, text) => keys[name] ? `${text} (${keys[name]})` : text;

  /* ------------------------------------------------------------------ helpers */
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const clock = (s) => {
    s = Math.max(0, Math.floor(s));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return (h ? `${h}:${String(m).padStart(2, "0")}` : `${m}`) + ":" + String(sec).padStart(2, "0");
  };
  const isMac = /Mac/i.test(navigator.platform || navigator.userAgent);

  function say(text, kind = "", ms = 4500) {
    message = text ? { text, kind } : null;
    clearTimeout(messageTimer);
    if (text && ms) messageTimer = setTimeout(() => { message = null; render(); }, ms);
    render();
  }

  function phase() {
    if (problem) return "offline";
    return status ? status.phase : "idle";
  }

  const PHASE = {
    idle: ["Ready", ""], recording: ["Recording", "recording"], paused: ["Paused", "paused"],
    working: ["Working", "working"], note_ready: ["Note ready", "ready"], offline: ["Not connected", ""],
    question: ["Needs your answer", "paused"],
  };

  /* ------------------------------------------------------------------ placement */
  function applyPlace() {
    const w = host.offsetWidth || 52, h = host.offsetHeight || 52;
    const maxY = Math.max(8, window.innerHeight - h - 8);
    place.y = Math.min(Math.max(8, place.y), maxY);
    root.classList.toggle("docked-left", place.side === "left");
    root.classList.toggle("docked-right", place.side === "right");
    host.style.top = place.y + "px";
    if (place.side === "left") {
      host.style.left = "0px"; host.style.right = "auto";
    } else if (place.side === "right") {
      host.style.left = "auto"; host.style.right = "0px";
    } else {
      place.x = Math.min(Math.max(8, place.x), Math.max(8, window.innerWidth - w - 8));
      host.style.left = place.x + "px"; host.style.right = "auto";
    }
  }

  function savePlace() {
    try { chrome.storage.local.set({ [KEY]: place }); } catch (_error) { /* orphaned */ }
  }

  // Drag by the card's header or the small button; drop near an edge to dock there
  function startDrag(event, onClick) {
    if (event.button !== 0) return;
    const startX = event.clientX, startY = event.clientY;
    const rect = host.getBoundingClientRect();
    let moved = false;
    const move = (e) => {
      const dx = e.clientX - startX, dy = e.clientY - startY;
      if (!moved && Math.hypot(dx, dy) < 5) return;
      if (!moved) { moved = true; root.classList.add("dragging"); place.side = null; }
      place.x = rect.left + dx;
      place.y = rect.top + dy;
      applyPlace();
    };
    const up = (e) => {
      window.removeEventListener("pointermove", move, true);
      window.removeEventListener("pointerup", up, true);
      root.classList.remove("dragging");
      if (!moved) { if (onClick) onClick(e); return; }
      const r = host.getBoundingClientRect();
      if (r.left < 40) place.side = "left";
      else if (window.innerWidth - r.right < 40) place.side = "right";
      applyPlace();
      savePlace();
    };
    window.addEventListener("pointermove", move, true);
    window.addEventListener("pointerup", up, true);
    event.preventDefault();
  }

  /* ------------------------------------------------------------------ talking to FreeScribe */
  async function refresh() {
    if (!alive) return;
    if (!host.isConnected) { stop(); return; }     // replaced by an updated panel
    const reply = await send({ fs: "call", path: "/api/status" });
    if (reply.kind === "gone") { stop(); return; }
    if (reply.ok) {
      status = reply.data;
      problem = null;
      seconds = status.seconds || 0;
      shownAt = Date.now();
      const s = status.session;
      if (pageChart) {
        // A patient's chart is open here: show that patient's own note (from the open session or an
        // earlier one), never another patient's
        const r = await send({ fs: "call", path: "/api/note_for", body: { patient: pageChart } });
        chartNote = r.ok ? r.data : null;
        const own = chartNote && chartNote.match !== "none" && !(chartNote.match === "current" && status.recording);
        noteText = own ? (chartNote.plain || "") : "";
        noteOwner = own ? chartNote.patient + (chartNote.match === "other" ? " · earlier session" : "") : "";
        noteKey = "";
      } else {
        chartNote = null;
        const showNote = s && s.has_note && !status.recording;
        const k = s ? `${s.id}:${status.phase}:${status.busy}` : "";
        if (showNote && k !== noteKey) {
          noteKey = k;
          const note = await send({ fs: "call", path: "/api/note" });
          noteText = note.ok ? (note.data.plain || "") : "";
        }
        if (!showNote) { noteText = ""; noteKey = ""; }
        noteOwner = s && s.patient ? s.patient : "";
      }
      // FreeScribe is waiting on a question: open the panel once for it, so nobody waits on a spinner
      const q = status.question;
      if (q && q.id !== seenQuestion) {
        seenQuestion = q.id;
        if (place.collapsed) { place.collapsed = false; opening = true; }
      }
    } else {
      status = null;
      problem = { kind: reply.kind, message: reply.error };
    }
    try { render(); } catch (error) { console.warn("FreeScribe panel:", error); }
    schedule();
  }

  function schedule() {
    clearTimeout(pollTimer);
    if (!alive) return;
    const active = status && (status.recording || status.phase === "working" || status.phase === "question");
    const ms = document.hidden ? 8000 : (active || !place.collapsed ? 1500 : 4000);
    pollTimer = setTimeout(refresh, ms);
  }

  async function act(action) {
    if (busyAction) return;
    busyAction = true;
    render();
    const reply = await send({ fs: "record", action });
    busyAction = false;
    if (!reply.ok && reply.code === "other_patient"){
      // The session belongs to someone else: ask whether to start a new one or move it
      const d = reply.details || {};
      choice = { kind: "start", chart: d.chart, chartDob: d.chart_dob, current: d.current,
                 currentDob: d.current_dob, hasContent: !!d.has_content };
      message = null;
      render();
      return;
    }
    if (!reply.ok) say(reply.error, "warn");
    else if (action === "stop") say(status && status.writes_note ? "Stopped. FreeScribe is writing the note…" : "Stopped.", "ok");
    else if (action === "start") {
      // Read the status again: a new session starts with the default template
      await new Promise((r) => setTimeout(r, 600));
      await refresh();
      const t = status && status.template && status.writes_note ? ` The note will be ${tplName(status.template.current)}.` : "";
      if (reply.patient || t) say(`${reply.patient ? `Recording for ${reply.patient}.` : "Recording."}${t}`, "ok", 6000);
    }
    setTimeout(refresh, 350);
  }

  // Insert puts in the note of the patient whose chart is open here, never another patient's:
  // the chart's patient is read from the page and FreeScribe finds that patient's note (from the
  // open session or an earlier one). A different patient is refused unless the clinician insists.
  async function insert(force = false) {
    guard = null;
    const chart = await readChart();
    let text = "", done = "Inserted into the chart. Check it before you sign.";
    if (chart && !force) {
      const r = await send({ fs: "call", path: "/api/note_for", body: { patient: chart } });
      if (!r.ok) return say(r.error, "warn");
      const d = r.data;
      if (d.match === "none") {
        guard = { chart: d.chart, session: d.session_patient, hasNote: d.has_note };
        render();
        return;
      }
      if (!d.plain) return say(d.busy ? `${d.patient}'s note isn't ready yet (${d.busy}).` : `There's no note for ${d.patient} yet.`, "warn");
      text = d.plain;
      if (d.match === "other") done = `Inserted ${d.patient}'s note (from an earlier FreeScribe session). Check it before you sign.`;
    } else {
      if (!noteText) {
        const note = await send({ fs: "call", path: "/api/note" });
        noteText = note.ok ? (note.data.plain || "") : "";
      }
      text = noteText;
      const s = status && status.session;
      if (!chart && s && s.patient) done = `Inserted ${s.patient}'s note. This page didn't say whose chart it is: check it's the right one.`;
    }
    if (!text) return say("There's no note yet.", "warn");
    const reply = await send({ fs: "insert", text });
    if (reply.ok && reply.data.done) say(done, chart || !(status && status.session && status.session.patient) ? "ok" : "warn", 9000);
    else say("Click in the chart's note box where the note should go, then press Insert again.", "warn", 7000);
  }

  // The patient of the chart open on this page (null when the page shows no single patient)
  async function readChart() {
    const reply = await send({ fs: "preview" });
    pageChart = reply.ok ? reply.data.patient || null : null;
    pageChartRead = true;
    return pageChart;
  }

  // The chart on this page is not the FreeScribe session's patient
  function chartMismatch() {
    const s = status && status.session;
    if (!pageChart || !s || !s.patient) return false;
    const name = [pageChart.last_name, pageChart.first_name].filter(Boolean).join(", ").toLowerCase();
    if (name !== s.patient.toLowerCase()) return true;
    return !!(pageChart.dob && s.patient_dob && pageChart.dob !== s.patient_dob);
  }

  async function copy() {
    if (!noteText) return;
    let ok = false;
    try { await navigator.clipboard.writeText(noteText); ok = true; } catch (_error) { /* not allowed here */ }
    if (!ok) {
      const area = document.createElement("textarea");
      area.value = noteText;
      area.style.cssText = "position:fixed;opacity:0;top:0;left:0;";
      shadow.appendChild(area);
      area.select();
      try { ok = document.execCommand("copy"); } catch (_error) { ok = false; }
      area.remove();
    }
    say(ok ? `Copied. Paste with ${isMac ? "⌘V" : "Ctrl+V"}.` : "Copying was blocked here. Use Insert instead.", ok ? "ok" : "warn");
  }

  // "Link chart": find out who is on this page, then ask before linking anyone
  async function link() {
    const reply = await send({ fs: "preview" });
    if (!reply.ok) return say(reply.error, "warn");
    const p = reply.data.patient;
    if (!p) return say("No single patient is open on this page. Open the patient's chart, then link it.", "warn", 7000);
    const s = status && status.session;
    const name = [p.last_name, p.first_name].filter(Boolean).join(", ");
    if (s && s.patient && s.patient.toLowerCase() === name.toLowerCase() &&
        (!s.patient_dob || !p.dob || s.patient_dob === p.dob)) return say(`${name} is already linked.`, "ok");
    choice = { kind: "link", chart: name, chartDob: p.dob || "", current: s && s.patient || "",
               currentDob: s && s.patient_dob || "", hasContent: !!(s && (s.has_transcript || s.has_note)) };
    message = null;
    render();
  }

  async function unlink() {
    const name = status && status.session && status.session.patient;
    const reply = await send({ fs: "unlink" });
    if (reply.ok) say(name ? `Unlinked ${name}. Link the right chart when it's open.` : "No patient was linked.", "ok", 7000);
    else say(reply.error, "warn");
    refresh();
  }

  // The answer to a choice: "link" (no one was linked), "switch" (move this session) or "new"
  async function answer(mode) {
    const c = choice;
    choice = null;
    if (!c) return;
    if (c.kind === "start"){
      busyAction = true; render();
      const reply = await send({ fs: "record", action: "start", choice: mode === "link" ? "" : mode });
      busyAction = false;
      if (reply.ok) say(mode === "new" ? `New session for ${c.chart}. Recording.` : `Recording for ${c.chart}.`, "ok");
      else say(reply.error, "warn", 8000);
    } else {
      const reply = await send({ fs: "link", mode });
      if (reply.ok) say(mode === "new" ? `New session for ${reply.data.patient}.` :
                        mode === "switch" ? `This session is now for ${reply.data.patient}.` :
                        `Linked ${reply.data.patient} from this chart.`, "ok");
      else say(reply.error, "warn", 8000);
    }
    setTimeout(refresh, 300);
  }

  function choiceHtml() {
    const c = choice;
    const born = (d) => d ? ` <span class="sub">· born ${esc(d)}</span>` : "";
    if (!c.current){
      return `<div class="ask"><div class="ask-title">Link this chart to the session?</div>
        <div class="ask-name">${esc(c.chart)}${born(c.chartDob)}</div>
        <div class="row2"><button class="small-primary" data-do="answer-link">Link</button>
        <button class="ghost" data-do="answer-cancel">Cancel</button></div></div>`;
    }
    return `<div class="ask warn"><div class="ask-title">This session is for someone else</div>
      <div class="ask-line">Session: <b>${esc(c.current)}</b>${born(c.currentDob)}</div>
      <div class="ask-line">This chart: <b>${esc(c.chart)}</b>${born(c.chartDob)}</div>
      <div class="col"><button class="small-primary" data-do="answer-new">New session for ${esc(c.chart)}</button>
      <button class="ghost" data-do="answer-switch">Move this session to ${esc(c.chart)}</button>
      <button class="ghost" data-do="answer-cancel">Cancel</button></div>
      ${c.hasContent ? `<div class="ask-note">Moving it means the recording and note already in this session will belong to ${esc(c.chart)}.</div>` : ""}</div>`;
  }

  /* ------------------------------------------------------------------ rendering */
  let painted = "";
  let opening = true;     // the card is about to appear: play its opening animation once
  let pressing = false;
  let pending = false;

  // Replaces the panel's content only when it changed, and never under a pressed button
  // (a click would be lost). Returns true when the content was replaced.
  function paint(html) {
    if (html === painted) { updateTime(); applyPlace(); return false; }
    if (pressing) { pending = true; return false; }
    painted = html;
    root.innerHTML = html;
    updateTime();
    return true;
  }

  function updateTime() {
    const live = status && status.recording && !status.paused;
    const shown = seconds + (live ? (Date.now() - shownAt) / 1000 : 0);
    for (const el of root.querySelectorAll("[data-time]")) el.textContent = clock(shown);
  }

  root.addEventListener("pointerdown", () => { pressing = true; }, true);
  window.addEventListener("pointerup", () => {
    pressing = false;
    if (pending) { pending = false; setTimeout(render, 0); }
  }, true);

  function render() {
    if (!alive) return;
    const ph = phase();
    const [label, cls] = PHASE[ph] || PHASE.idle;

    if (place.collapsed) {
      const attention = status && (status.question || (status.error && !status.recording) || chartMismatch());
      const badge = attention ? `<span class="badge warn">!</span>`
        : ph === "note_ready" ? `<span class="badge">${ICON.check}</span>` : "";
      const time = status && status.recording
        ? `<span class="mini-time ${status.paused ? "paused" : ""}" data-time></span>` : "";
      const html = `<div class="mini-wrap"><div class="bubble ${cls || (ph === "offline" ? "offline" : "")}"
        role="button" tabindex="0" aria-label="FreeScribe: ${esc(label)}. Click to open." title="FreeScribe · ${esc(label)}">
        ${ICON.mic}${badge}</div>${time}</div>`;
      if (!paint(html)) return;
      const bubble = root.querySelector(".bubble");
      bubble.addEventListener("pointerdown", (e) => startDrag(e, () => {
        place.collapsed = false; opening = true; savePlace(); render(); refresh();
      }));
      bubble.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { place.collapsed = false; opening = true; savePlace(); render(); }
      });
      applyPlace();
      return;
    }

    const s = status && status.session;
    const chipText = ph === "working" && status.busy ? status.busy : label;
    let body = "";
    if (!status && !problem) {
      // Not heard from FreeScribe yet (the panel opens on a new page before the first reply)
      body = `<div class="working-text">Connecting to FreeScribe…</div>`;
    } else if (ph === "offline") {
      const unpaired = problem && problem.kind === "unpaired";
      body = `<div class="offline"><p>${esc(problem ? problem.message : "")}</p>
        <div class="controls">${unpaired
          ? `<button class="big" data-do="options">Pair with FreeScribe</button>`
          : `<button class="big" data-do="retry">Try again</button>`}</div></div>`;
    } else {
      const who = s && (s.patient || s.title) ? esc(s.patient || s.title) : "No session open";
      const contents = s ? [s.has_transcript ? "Transcript" : "", s.has_note ? "Note" : ""].filter(Boolean).join(" · ") ||
        (status.writes_note ? "Nothing recorded yet" : "Transcribe-only mode") : "";
      const sub = s && s.patient ? `Linked from the chart${s.patient_dob ? " · born " + s.patient_dob : ""}` : contents;
      body += `<div class="who"><div class="avatar ${s && s.patient ? "linked" : ""}">${ICON.user}</div>
        <div class="names"><div class="name" title="${who}">${who}</div><div class="sub">${esc(sub)}</div></div>
        ${s && s.patient
          ? `<span class="who-btns"><button class="linkbtn" data-do="link" title="Link the chart open on this page instead">Change</button>
             <button class="linkbtn muted-btn" data-do="unlink" title="Unlink this patient from the session" ${status.recording ? "disabled" : ""}>Unlink</button></span>`
          : `<button class="linkbtn" data-do="link" title="Link the patient whose chart is open on this page">${ICON.link}Link chart</button>`}</div>`;
      const shipped = status.extension_version || "";
      if (shipped && newer(shipped, FS_VERSION)) {
        body += `<div class="ask warn"><div class="ask-title">Update the extension</div>
          <div class="ask-line">FreeScribe comes with version ${esc(shipped)}; Chrome is running ${esc(FS_VERSION || "an older one")}.</div>
          <div class="row2"><button class="small-primary" data-do="self-update">Update now</button></div>
          <div class="ask-note">Takes a second. Open FreeScribe pages reload their panel.</div></div>`;
      }
      if (guard) {
        body += `<div class="ask warn"><div class="ask-title">Not inserted: different patient</div>
          <div class="ask-line">This chart: <b>${esc(guard.chart)}</b></div>
          <div class="ask-line">${guard.session ? `FreeScribe's note: <b>${esc(guard.session)}</b>` : "FreeScribe has no note for this patient."}</div>
          <div class="col"><button class="ghost" data-do="guard-cancel">Cancel</button>
          ${guard.hasNote ? `<button class="ghost" data-do="guard-force">Insert ${guard.session ? esc(guard.session) + "'s" : "the open"} note here anyway</button>` : ""}</div>
          <div class="ask-note">Open ${esc(guard.session || "the right patient")}'s chart to insert that note.</div></div>`;
      } else if (chartMismatch()) {
        const own = chartNote && chartNote.match !== "none";
        body += `<div class="ask warn"><div class="ask-title">Different patient on this page</div>
          <div class="ask-line">This chart: <b>${esc([pageChart.last_name, pageChart.first_name].filter(Boolean).join(", "))}</b></div>
          <div class="ask-line">FreeScribe session: <b>${esc(s.patient)}</b>${status.recording ? " (recording)" : ""}</div>
          <div class="ask-note">${own ? "The note below is this chart's own patient's." :
            `${esc(s.patient)}'s note is not offered here; open ${esc(s.patient)}'s chart to insert it.`}</div></div>`;
      }
      const t = status.template;
      if (t && t.current) {
        const other = t.current !== t.default;
        body += `<div class="tpl"><span>Note</span><button class="pick" data-do="tpl-open" title="The template the note is written with">${esc(tplName(t.current))} ▾</button>
          ${other ? `<span class="sub" title="New sessions start with the default template">new sessions: ${esc(tplName(t.default))}</span>` : ""}</div>`;
        if (templateOpen) {
          body += `<div class="tpl-list">${t.options.map((n) => `<button data-do="tpl-pick" data-opt="${esc(n)}">
              <span class="mark">${n === t.current ? "✓" : ""}</span>${esc(tplName(n))}${n === t.default ? `<span class="dflt">default</span>` : ""}</button>`).join("")}
            ${other ? `<button class="make" data-do="tpl-default">Use ${esc(tplName(t.current))} for new sessions too</button>` : ""}</div>`;
        }
      }
      if (choice) body += choiceHtml();
      if (status.question) {
        const q = status.question;
        body += `<div class="ask warn"><div class="ask-title">FreeScribe is asking</div><div class="ask-line">${esc(q.text)}</div>
          <div class="col">${q.options.map((o, i) => `<button class="${i === q.options.length - 1 ? "small-primary" : "ghost"}"
            data-do="q-answer" data-opt="${esc(o.id)}">${esc(o.label)}</button>`).join("")}</div>
          <div class="ask-note">You can also answer in FreeScribe.</div></div>`;
      } else if (status.error && !status.recording && ph !== "working") {
        body += `<div class="ask warn"><div class="ask-title">FreeScribe says</div><div class="ask-line">${esc(status.error)}</div>
          <div class="row2"><button class="ghost" data-do="dismiss-error">Dismiss</button></div></div>`;
      }
      if (status.recording) {
        body += `<div class="timer ${status.paused ? "paused" : ""}"><span class="time" data-time></span>
          <span class="bars"><span></span><span></span><span></span><span></span><span></span></span></div>
          <div class="controls">
            <button class="big stop" data-do="stop" ${busyAction ? "disabled" : ""}>${ICON.stop}${status.writes_note ? "Stop &amp; write note" : "Stop"}</button>
            <button class="square" data-do="${status.paused ? "resume" : "pause"}" title="${esc(keyHint("pause-resume", status.paused ? "Resume" : "Pause"))}"
              aria-label="${status.paused ? "Resume" : "Pause"}">${status.paused ? ICON.play : ICON.pause}</button>
          </div>`;
      } else if (ph === "question") {
        // Answer FreeScribe's question first (above)
      } else if (ph === "working") {
        body += `<div class="progress"><span></span></div><div class="working-text">${esc(status.busy || "Working")}…</div>`;
      } else {
        const dictating = window.__fsVoice && window.__fsVoice.active();
        body += `<div class="controls"><button class="big" data-do="start" ${busyAction || dictating ? "disabled" : ""}>${ICON.mic}Start recording</button>
          <button class="square ${dictating ? "on" : ""}" data-do="dictate" title="${esc(keyHint("dictate", "Dictate where the cursor is in the chart"))}"
            aria-label="Dictate into the chart">${ICON.dictate}</button></div>`;
      }
      if (noteText && (!status.recording || (chartNote && chartNote.match === "other"))) {
        const words = noteText.split(/\s+/).filter(Boolean).length;
        body += `<div class="note"><div class="note-head"><span>${noteOwner ? `Note · ${esc(noteOwner)}` : "Note"}</span><span>${words} words</span></div>
          <pre>${esc(noteText)}</pre>
          <div class="row"><button class="insert" data-do="insert">${ICON.insert}Insert into chart</button>
          <button class="ghost" data-do="copy" title="Copy the note">${ICON.copy}Copy</button></div>
          <div class="hint">Click in the chart's note box first, then Insert.</div></div>`;
      }
    }
    if (message) body += `<div class="msg ${message.kind}">${esc(message.text)}</div>`;

    const html = `<div class="card" role="dialog" aria-label="FreeScribe">
      <div class="head">
        <span class="brand" title="FreeScribe">${ICON.mark}</span>
        <span class="chip ${cls}"><i></i>${esc(chipText)}</span>
        <span class="spacer"></span>
        <button class="tool ${place.side === "left" ? "on" : ""}" data-do="dock-left" title="Stick to the left side" aria-label="Stick to the left side">${ICON.left}</button>
        <button class="tool ${place.side === "right" ? "on" : ""}" data-do="dock-right" title="Stick to the right side" aria-label="Stick to the right side">${ICON.right}</button>
        <button class="tool" data-do="menu" title="More" aria-label="More">${ICON.more}</button>
        <button class="tool" data-do="collapse" title="Shrink to a button" aria-label="Shrink to a button">${ICON.minimize}</button>
      </div>
      ${menuOpen ? `<div class="menu">${window.__fsVoice
          ? `<button data-do="talk-form">Fill this form by talking</button><button data-do="dictate">Dictate into the chart</button>` : ""}<button data-do="options">Pairing with FreeScribe</button><button data-do="shortcuts">Change keyboard shortcuts</button>
        <button data-do="hide-site">Don't show on this site</button></div>` : ""}
      <div class="body">${body}</div>
      ${footHtml()}
    </div>`;
    if (!paint(html)) return;
    if (opening) { root.querySelector(".card").classList.add("opening"); opening = false; }

    root.querySelector(".head").addEventListener("pointerdown", (e) => {
      if (e.target.closest("button")) return;
      startDrag(e);
    });
    for (const button of root.querySelectorAll("button[data-do]")) {
      // Keep the cursor in the chart's note box: buttons don't take the focus on click
      button.addEventListener("mousedown", (e) => e.preventDefault());
      button.addEventListener("click", () => handle(button.dataset.do, button.dataset.opt));
    }
    applyPlace();
  }

  // The shortcuts in the footer, as Chrome has them. When they share their modifier keys
  // ("Alt+Shift+") that is written once; an unset one is left out, with a pointer to set it.
  function footHtml() {
    if (!keysRead) return "";
    const names = [["toggle-recording", "record"], ["pause-resume", "pause"], ["insert-note", "insert"], ["dictate", "dictate"]];
    const set = names.filter(([n]) => keys[n]);
    const split = (k) => { const m = /^(.*?)([^+⌥⇧⌘⌃]+)$/.exec(k); return m ? [m[1], m[2]] : ["", k]; };
    const prefixes = new Set(set.map(([n]) => split(keys[n])[0]));
    let html;
    if (!set.length) {
      html = `<button class="foot-link" data-do="shortcuts">Set keyboard shortcuts</button>`;
    } else if (prefixes.size === 1) {
      const [p] = prefixes;
      html = (p ? `<span>${esc(p.replace(/\+$/, ""))}</span>` : "") +
        set.map(([n, word]) => `<span><kbd>${esc(split(keys[n])[1])}</kbd> ${word}</span>`).join("");
    } else {
      html = set.map(([n, word]) => `<span><kbd>${esc(keys[n])}</kbd> ${word}</span>`).join("");
    }
    const missing = names.length - set.length;
    if (set.length && missing) {
      html += `<button class="foot-link" data-do="shortcuts" title="${missing} shortcut${missing > 1 ? "s are" : " is"} not set (Chrome leaves one unset when another extension uses its keys)">+ set</button>`;
    }
    return `<div class="foot">${html}</div>`;
  }

  async function chooseTemplate(name, makeDefault) {
    const reply = await send({ fs: "call", path: "/api/template", body: { name, make_default: makeDefault } });
    if (!reply.ok) { say(reply.error, "warn"); return; }
    if (status) status.template = reply.data.template;
    const writing = status && status.session && status.session.has_note && !status.recording;
    say(makeDefault ? `New sessions will use ${tplName(name)}.`
      : writing ? `Template set to ${tplName(name)}. Regenerate in FreeScribe to rewrite the note.`
      : `The note will be written as ${tplName(name)}.`, "ok", 5000);
    refresh();
  }

  async function attention(body) {
    const reply = await send({ fs: "call", path: "/api/attention", body });
    if (!reply.ok) say(reply.error, "warn");
    else if (body.action === "answer") say(body.option === "anyway" ? "Writing the note…" : "Done.", "ok", 3000);
    setTimeout(refresh, 300);
  }

  function handle(what, option) {
    if (what !== "menu") menuOpen = false;
    switch (what) {
      case "start": case "stop": case "pause": case "resume": act(what); break;
      case "insert": insert(); break;
      case "guard-cancel": guard = null; render(); break;
      case "self-update":
        say("Updating the extension…", "", 0);
        send({ fs: "self-reload" });
        break;
      case "guard-force": guard = null; insert(true); break;
      case "q-answer":
        if (status && status.question) attention({ action: "answer", id: status.question.id, option });
        break;
      case "dismiss-error": attention({ action: "dismiss" }); break;
      case "tpl-open": templateOpen = !templateOpen; render(); break;
      case "tpl-pick": templateOpen = false; chooseTemplate(option, false); break;
      case "tpl-default":
        templateOpen = false;
        if (status && status.template) chooseTemplate(status.template.current, true);
        break;
      case "copy": copy(); break;
      case "link": link(); break;
      case "unlink": unlink(); break;
      case "answer-link": answer("link"); break;
      case "answer-new": answer("new"); break;
      case "answer-switch": answer("switch"); break;
      case "answer-cancel": choice = null; render(); break;
      case "retry": refresh(); break;
      case "options": send({ fs: "options" }); break;
      case "shortcuts":
        send({ fs: "open-shortcuts" });
        say("Change the keys in Chrome's page, then come back. The panel shows the new ones.", "", 8000);
        break;
      case "dictate":
        if (window.__fsVoice) window.__fsVoice.toggle().then(() => setTimeout(render, 300));
        break;
      case "talk-form":
        // The form window takes the screen; the panel waits as its small button
        place.collapsed = true; savePlace(); render();
        if (window.__fsVoice) window.__fsVoice.openForm();
        break;
      case "menu": menuOpen = !menuOpen; render(); break;
      case "collapse": place.collapsed = true; savePlace(); render(); break;
      case "dock-left": case "dock-right": {
        const side = what === "dock-left" ? "left" : "right";
        if (place.side === side) {
          // Already stuck there: let it float a little way in
          const r = host.getBoundingClientRect();
          place.side = null;
          place.x = side === "left" ? 16 : window.innerWidth - r.width - 16;
        } else {
          place.side = side;
        }
        savePlace(); render(); break;
      }
      case "hide-site": hideHere(); break;
      default: break;
    }
  }

  async function hideHere() {
    say("Hiding the panel on this site…", "", 0);
    const reply = await send({ fs: "hide-site" });
    if (!reply.ok) { say(`The panel couldn't be turned off here (${reply.error}).`, "warn", 8000); return; }
    // Gone from this page now; the background removes it from the site's other open pages
    stop();
  }

  function stop() {
    alive = false;
    clearTimeout(pollTimer);
    clearInterval(tick);
    host.remove();
  }

  // The timer ticks between status checks
  const tick = setInterval(() => {
    if (status && status.recording && !status.paused && !document.hidden) updateTime();
  }, 1000);

  window.addEventListener("resize", () => applyPlace());
  // Coming back from Chrome's shortcut page (or any other window): read the keys again
  document.addEventListener("visibilitychange", () => { if (!document.hidden) { refresh(); readKeys(); readChart().then(refresh); } });
  window.addEventListener("focus", () => readKeys());
  try {
    chrome.runtime.onMessage.addListener((msg) => {
      if (msg && msg.fs === "remove-panel") stop();
      if (msg && msg.fs === "refresh") refresh();
    });
  } catch (_error) { /* orphaned */ }

  chrome.storage.local.get(KEY, (stored) => {
    if (stored && stored[KEY]) place = Object.assign(place, stored[KEY]);
    refresh();
    readKeys();
    readChart().then(refresh);
    try { render(); } catch (error) { console.warn("FreeScribe panel:", error); }
  });
})();
