/*
 * voice.js: dictating into the EMR, from the floating panel or its keyboard shortcut (Alt+Shift+D unless changed).
 *
 * Dictation: FreeScribe listens with its own microphone and speech-to-text (on this computer
 * or your speech server) and the text is typed where the cursor is in the chart, piece by
 * piece as you pause. Click another box while talking and the next words go there, so a
 * form can be dictated box by box. "Stop & tidy" has the AI fix punctuation and misheard
 * words in what was typed, without changing any number; it is only swapped in where the
 * typed text is still unchanged and in one box.
 *
 * Talking through a form: the boxes of the form open on the page (an OSCAR eForm or any web
 * form) are read, you say what goes in the form, and the AI fills the boxes from that and
 * the linked visit; the patient's and your own details come from the chart and your profile.
 * You review every answer before anything is written into the form. Nothing is submitted.
 *
 * Runs in the EMR page (the top window). Part of FreeScribe, released under the AGPL-3.0 license.
 */
(() => {
  const FS_VERSION = (() => { try { return chrome.runtime.getManifest().version; } catch (_e) { return ""; } })();
  if (window.top !== window || (window.__fsVoice && window.__fsVoice.version === FS_VERSION)) return;

  /* ------------------------------------------------------------- talking */
  const send = (msg) => new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage(msg, (reply) => {
        if (chrome.runtime.lastError) resolve({ ok: false, error: "The extension was updated. Reload this page." });
        else resolve(reply || { ok: false, error: "No reply" });
      });
    } catch (_error) {
      resolve({ ok: false, error: "The extension was updated. Reload this page." });
    }
  });
  const fs = async (path, body) => {
    const reply = await send({ fs: "call", path, body });
    if (!reply.ok) { const e = new Error(reply.error || "FreeScribe did not answer."); e.code = reply.code; throw e; }
    return reply.data;
  };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const clock = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  const isMac = /Mac/i.test(navigator.platform || navigator.userAgent);

  let tidyDefault = false;
  try { chrome.storage.local.get("fsDictTidy", (v) => { tidyDefault = !!(v && v.fsDictTidy); }); } catch (_error) { /* orphaned */ }
  const setTidyDefault = (on) => { tidyDefault = !!on; try { chrome.storage.local.set({ fsDictTidy: tidyDefault }); } catch (_error) { /* orphaned */ } };

  /* ------------------------------------------------------------- styles */
  const CSS = `
:host { all: initial; }
* { box-sizing: border-box; }
.root { --bg: rgba(255,255,255,.97); --surface: #f5f6f7; --line: rgba(15,23,42,.12); --text: #16181d; --muted: #5f6672;
  --faint: #8b919c; --accent: #1f8a4c; --accent-hover: #19733f; --accent-soft: #e4f4ea; --rec: #e5484d; --rec-soft: #fdebec;
  --amber: #b45309; --amber-soft: #fdf1e2; --blue: #2f6fdf; --blue-soft: #e8f0fd;
  --shadow: 0 12px 32px rgba(15,23,42,.2), 0 2px 6px rgba(15,23,42,.08);
  font: 13px/1.4 system-ui, -apple-system, "Segoe UI", Roboto, Ubuntu, sans-serif; color: var(--text); -webkit-font-smoothing: antialiased; }
@media (prefers-color-scheme: dark) { .root { --bg: rgba(28,29,33,.97); --surface: #25262b; --line: rgba(255,255,255,.12); --text: #eceef1;
  --muted: #a5abb6; --faint: #7d838e; --accent: #34a866; --accent-hover: #3cba72; --accent-soft: rgba(52,168,102,.16); --rec: #f2555a;
  --rec-soft: rgba(242,85,90,.16); --amber: #f0a43a; --amber-soft: rgba(240,164,58,.15); --blue: #6aa1ff; --blue-soft: rgba(106,161,255,.15);
  --shadow: 0 12px 32px rgba(0,0,0,.5), 0 2px 6px rgba(0,0,0,.3); } }
button { font: inherit; color: inherit; border: 0; background: none; cursor: pointer; padding: 0; }
button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
button:disabled { opacity: .5; cursor: default; }
.btn { height: 34px; padding: 0 14px; border-radius: 10px; font-weight: 650; display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
.primary { background: var(--accent); color: #fff; } .primary:hover:not(:disabled) { background: var(--accent-hover); }
.danger { background: var(--rec); color: #fff; }
.ghost { border: 1px solid var(--line); background: var(--bg); } .ghost:hover { background: var(--surface); }
.link { color: var(--muted); font-size: 12px; text-decoration: underline; }
.dot { width: 9px; height: 9px; border-radius: 50%; background: var(--rec); flex: none; animation: blink 1.2s infinite; }
@keyframes blink { 50% { opacity: .25; } }
.level { width: 46px; height: 6px; border-radius: 3px; background: var(--surface); overflow: hidden; flex: none; }
.level span { display: block; height: 100%; background: var(--rec); width: 0; transition: width .15s; }
.spin { width: 14px; height: 14px; border-radius: 50%; border: 2px solid var(--blue-soft); border-top-color: var(--blue); animation: rot 1s linear infinite; flex: none; }
@keyframes rot { to { transform: rotate(360deg); } }
.msg { font-size: 12.5px; padding: 8px 10px; border-radius: 10px; background: var(--blue-soft); color: var(--blue); }
.msg.ok { background: var(--accent-soft); color: var(--accent); } .msg.warn { background: var(--amber-soft); color: var(--amber); }

/* the dictation bar */
.bar { width: min(720px, calc(100vw - 24px)); background: var(--bg); border: 1px solid var(--line); border-radius: 14px; box-shadow: var(--shadow);
  padding: 10px 12px; display: grid; gap: 8px; backdrop-filter: blur(14px); }
.bar .top { display: flex; align-items: center; gap: 10px; }
.bar .what { font-weight: 650; white-space: nowrap; }
.bar .where { color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 1; min-width: 0; }
.bar .where.warn { color: var(--amber); font-weight: 600; }
.bar .text { font-size: 13px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; direction: rtl; text-align: left; }
.bar .text bdi { direction: ltr; unicode-bidi: plaintext; }
.bar .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.bar .row .spacer { flex: 1; }
.x { width: 30px; height: 30px; border-radius: 8px; display: grid; place-items: center; color: var(--muted); font-size: 18px; }
.x:hover { background: var(--surface); }

/* talking through a form */
.shade { position: fixed; inset: 0; background: rgba(15,23,42,.28); display: grid; place-items: center; padding: 16px; }
.card { width: min(760px, 100%); max-height: calc(100vh - 32px); background: var(--bg); border: 1px solid var(--line); border-radius: 16px;
  box-shadow: var(--shadow); display: flex; flex-direction: column; overflow: hidden; }
.card .head { display: flex; align-items: center; gap: 10px; padding: 14px 16px; border-bottom: 1px solid var(--line); }
.card h2 { font-size: 16px; margin: 0; flex: 1; }
.card .body { padding: 14px 16px; overflow: auto; display: grid; gap: 12px; }
.card .foot { padding: 12px 16px; border-top: 1px solid var(--line); display: flex; gap: 8px; align-items: center; justify-content: flex-end; flex-wrap: wrap; }
.card .foot .note { color: var(--faint); font-size: 12px; margin-right: auto; }
.facts { display: grid; gap: 4px; color: var(--muted); }
.facts b { color: var(--text); }
.said { min-height: 90px; max-height: 220px; overflow: auto; white-space: pre-wrap; border: 1px solid var(--line); border-radius: 12px;
  padding: 10px 12px; background: var(--surface); font-size: 13.5px; }
.said.empty { color: var(--faint); }
.listen { display: flex; align-items: center; gap: 10px; font-weight: 650; }
table { width: 100%; border-collapse: collapse; }
td, th { padding: 6px 6px; border-bottom: 1px solid var(--line); vertical-align: top; text-align: left; }
th { font-size: 11.5px; color: var(--faint); font-weight: 600; text-transform: uppercase; letter-spacing: .03em; }
td.l { width: 38%; color: var(--muted); }
td.c { width: 28px; }
td input[type=text], td textarea, td select { width: 100%; font: inherit; color: var(--text); background: var(--bg); border: 1px solid var(--line);
  border-radius: 8px; padding: 5px 7px; }
td textarea { min-height: 56px; resize: vertical; }
tr.off td { opacity: .45; }
.tag { font-size: 11px; color: var(--faint); }
`;

  function makeHost(id, style) {
    const host = document.createElement("div");
    host.id = id;
    host.style.cssText = "position:fixed;z-index:2147483646;margin:0;padding:0;border:0;" + style;
    const shadow = host.attachShadow({ mode: "open" });
    const sheet = new CSSStyleSheet();
    sheet.replaceSync(CSS);
    shadow.adoptedStyleSheets = [sheet];
    const root = document.createElement("div");
    root.className = "root";
    shadow.appendChild(root);
    document.documentElement.appendChild(host);
    // Clicks on our buttons must not take the cursor out of the chart's box
    root.addEventListener("mousedown", (e) => {
      if (e.target.closest("button")) e.preventDefault();
    });
    return { host, root };
  }

  /* ============================================================ dictation */
  // mode "chart": typed into the page; mode "form": shown in the form window only
  let dict = null;   // {id, mode, since, queue[], typed[], text, frames:Set, where, level, elapsed, phase, tidy, error, onPiece}
  let bar = null;
  let barMessage = null;
  let barTimer = null;

  async function startDictation(mode = "chart", onPiece = null) {
    if (dict) return dict;
    try {
      const reply = await fs("/api/dictate", { action: "start" });
      dict = { id: reply.id, mode, since: 0, queue: [], text: "", frames: new Set(), where: "", level: 0, elapsed: 0,
               phase: "live", tidy: null, error: "", onPiece, started: Date.now() };
    } catch (error) {
      if (mode === "chart") showBarMessage(error.message, "warn");
      throw error;
    }
    if (mode === "chart") renderBar();
    loop(dict);
    return dict;
  }

  async function stopDictation(tidy) {
    if (!dict || dict.phase !== "live") return;
    dict.phase = tidy ? "tidying" : "finishing";
    dict.wantTidy = !!tidy;
    renderBar();
    try { await fs("/api/dictate", { action: "stop", id: dict.id, tidy: !!tidy }); } catch (_error) { /* polled below */ }
  }

  async function cancelDictation() {
    if (!dict) return;
    const d = dict;
    try { await fs("/api/dictate", { action: "cancel", id: d.id }); } catch (_error) { /* ended */ }
    d.cancelled = true;
    dict = null;
    if (d.mode === "chart") showBarMessage(d.typed ? "Stopped. What was typed stays in the chart." : "Dictation cancelled.", "", 3500);
  }

  async function loop(d) {
    while (dict === d && !d.cancelled) {
      let r;
      try {
        r = await fs("/api/dictate", { action: "poll", id: d.id, since: d.since });
      } catch (error) {
        d.error = error.message;
        break;
      }
      for (const [n, text] of r.pieces) {
        d.since = n;
        d.text += text;
        d.queue.push(text);
        if (d.onPiece) d.onPiece(d.text);
      }
      d.level = r.level;
      d.elapsed = r.elapsed;
      if (r.error) d.error = r.error;
      if (d.mode === "chart") await typeQueued(d);
      if (!r.recording && d.phase === "live") d.phase = "finishing";    // stopped by FreeScribe (time limit, microphone)
      const tidy = r.tidy || {};
      if (r.finished && !["wanted", "working"].includes(tidy.state)) {
        d.tidy = tidy;
        break;
      }
      renderBar();
      await sleep(d.phase === "live" ? 350 : 500);
    }
    if (dict !== d || d.cancelled) return;
    if (d.mode === "chart") await finishChart(d);
    else if (d.onDone) d.onDone(d);
    if (dict === d) dict = null;
  }

  async function typeQueued(d) {
    while (d.queue.length) {
      const reply = await send({ fs: "dict-insert", text: d.queue[0], sid: d.id });
      if (!reply.ok || !reply.data.done) {
        d.where = "";
        return;
      }
      d.queue.shift();
      d.typed = true;
      d.frames.add(reply.data.frameId);
      d.where = reply.data.name || "the box";
    }
  }

  async function finishChart(d) {
    const left = d.queue.join("").trim();
    if (left) {
      // Nothing to type into: keep it for the clinician
      showBarMessage("No box to type into, so the text was not typed. Copy it and paste it where it belongs.", "warn", 0, d.text);
      return;
    }
    if (!d.text.trim()) {
      showBarMessage(d.error || "Nothing was heard. Check the microphone in FreeScribe's settings.", "warn", 6000);
      return;
    }
    const tidy = d.tidy || {};
    if (d.wantTidy && tidy.state === "done") {
      if (tidy.text.replace(/\s+/g, " ").trim() === d.text.replace(/\s+/g, " ").trim()) {
        showBarMessage("Done. The AI found nothing to tidy. Check the text before you sign.", "ok", 5000);
        return;
      }
      const reply = await send({ fs: "dict-replace", sid: d.id, text: tidy.text, frameIds: [...d.frames] });
      if (reply.ok && reply.data.done) {
        showBarMessage("Done, tidied by the AI. Check the text before you sign.", "ok", 6000);
      } else {
        const why = reply.ok && reply.data.reason === "several"
          ? "The text went into more than one box, so the tidied version was not swapped in."
          : "The text was changed after it was typed, so the tidied version was not swapped in.";
        showBarMessage(why + " You can copy it instead.", "warn", 0, tidy.text);
      }
      return;
    }
    if (d.wantTidy && tidy.state === "failed") {
      showBarMessage(`Typed as you said it. ${tidy.problem || "The AI could not tidy it."}`, "warn", 8000);
      return;
    }
    showBarMessage(d.error ? `Stopped: ${d.error}` : "Done. Check the text before you sign.", d.error ? "warn" : "ok", 4500);
  }

  function showBarMessage(text, kind = "", ms = 4500, copyText = "") {
    barMessage = { text, kind, copyText };
    clearTimeout(barTimer);
    if (ms) barTimer = setTimeout(() => { barMessage = null; renderBar(); }, ms);
    renderBar();
  }

  function renderBar() {
    const d = dict && dict.mode === "chart" ? dict : null;
    if (!d && !barMessage) {
      if (bar) { bar.host.remove(); bar = null; }
      return;
    }
    if (!bar) bar = makeHost("freescribe-voice-host", "left:50%;bottom:16px;transform:translateX(-50%);");
    let html;
    if (d) {
      const live = d.phase === "live";
      const where = d.where ? `into <b>${esc(d.where)}</b>` : "";
      const hint = !d.where && (d.queue.length || live)
        ? `<span class="where warn">Click in the box where the text should go</span>` : `<span class="where">${where}</span>`;
      const status = live
        ? `<span class="dot"></span><span class="what">Dictating ${clock(d.elapsed)}</span><span class="level"><span style="width:${Math.round(Math.min(1, d.level * 1.6) * 100)}%"></span></span>`
        : `<span class="spin"></span><span class="what">${d.phase === "tidying" ? "Tidying with the AI…" : "Typing the last words…"}</span>`;
      const tail = d.text.slice(-400);
      html = `<div class="bar" role="status" aria-live="polite"><div class="top">${status}${hint}
          <button class="x" data-do="cancel" title="Cancel (what was typed stays)" aria-label="Cancel">×</button></div>
        ${tail ? `<div class="text"><bdi>${esc(tail)}</bdi></div>` : `<div class="text"><bdi>Start talking. Pause briefly and the words appear. Say “new line” or “new paragraph” for a break.</bdi></div>`}
        ${live ? `<div class="row"><span class="link">${(window.__fsKeys || {}).dictate ? esc(window.__fsKeys.dictate) + " stops" : ""}</span><span class="spacer"></span>
          <button class="btn ${tidyDefault ? "ghost" : "danger"}" data-do="stop">Stop</button>
          <button class="btn ${tidyDefault ? "danger" : "ghost"}" data-do="stop-tidy" title="The AI fixes punctuation and misheard words; numbers are never changed">Stop &amp; tidy with AI</button></div>` : ""}
        ${d.error ? `<div class="msg warn">${esc(d.error)}</div>` : ""}</div>`;
    } else {
      const m = barMessage;
      html = `<div class="bar"><div class="top"><div class="msg ${m.kind}" style="flex:1">${esc(m.text)}</div>
        ${m.copyText ? `<button class="btn ghost" data-do="copy">Copy text</button>` : ""}
        <button class="x" data-do="close" aria-label="Close">×</button></div></div>`;
    }
    bar.root.innerHTML = html;
    for (const b of bar.root.querySelectorAll("button[data-do]")) {
      b.addEventListener("click", () => {
        const what = b.dataset.do;
        if (what === "stop") { setTidyDefault(false); stopDictation(false); }
        else if (what === "stop-tidy") { setTidyDefault(true); stopDictation(true); }
        else if (what === "cancel") cancelDictation();
        else if (what === "copy") copyText(barMessage && barMessage.copyText);
        else if (what === "close") { barMessage = null; renderBar(); }
      });
    }
  }

  async function copyText(text) {
    let ok = false;
    try { await navigator.clipboard.writeText(text || ""); ok = true; } catch (_error) { ok = false; }
    if (!ok) {
      const area = document.createElement("textarea");
      area.value = text || "";
      area.style.cssText = "position:fixed;opacity:0;top:0;left:0;";
      document.body.appendChild(area);
      area.select();
      try { ok = document.execCommand("copy"); } catch (_error) { ok = false; }
      area.remove();
    }
    showBarMessage(ok ? `Copied. Paste with ${isMac ? "⌘V" : "Ctrl+V"}.` : "Copying was blocked here.", ok ? "ok" : "warn", 4000);
  }

  async function toggle() {
    if (form) return;      // the form window has its own buttons
    if (dict && dict.mode === "chart") {
      if (dict.phase === "live") stopDictation(tidyDefault);
      return;
    }
    try { await startDictation("chart"); } catch (_error) { /* shown */ }
  }

  /* ============================================================ talking through a form */
  let form = null;   // {step, page, said, values, labels, include, problems, frameId, job, message}
  let formUi = null;

  async function openForm() {
    if (form) return;
    if (dict && dict.mode === "chart") { showBarMessage("Stop dictating first.", "warn"); return; }
    form = { step: "reading", said: "" };
    renderForm();
    const reply = await send({ fs: "form-read" });
    if (!reply.ok || !reply.data.count) {
      form.step = "none";
      form.message = reply.ok ? "No form with boxes to fill was found on this page. Open the form (the eForm) first, then try again."
        : reply.error;
      renderForm();
      return;
    }
    form.page = reply.data;
    form.step = "intro";
    try {
      const status = await fs("/api/status");
      form.patient = status.session && status.session.patient ? status.session.patient : "";
      form.hasVisit = !!(status.session && (status.session.has_note || status.session.has_transcript));
      form.recording = !!status.recording;
    } catch (_error) { /* shown when filling */ }
    renderForm();
  }

  function closeForm() {
    if (dict && dict.mode === "form") cancelDictation();
    form = null;
    if (formUi) { formUi.host.remove(); formUi = null; }
  }

  async function talk() {
    form.step = "talking";
    form.said = "";
    form.message = null;
    renderForm();
    try {
      const d = await startDictation("form", (text) => { form.said = text; updateSaid(); });
      d.onDone = (done) => {
        if (!form) return;
        form.said = done.text;
        if (!done.text.trim()) {
          form.step = "intro";
          form.message = { text: done.error || "Nothing was heard. Check the microphone in FreeScribe's settings.", kind: "warn" };
          renderForm();
          return;
        }
        fill(done.text);
      };
      const tick = setInterval(() => {
        if (!form || form.step !== "talking" || !dict) { clearInterval(tick); return; }
        const lv = formUi && formUi.root.querySelector(".level span");
        if (lv) lv.style.width = `${Math.round(Math.min(1, dict.level * 1.6) * 100)}%`;
        const t = formUi && formUi.root.querySelector("[data-clock]");
        if (t) t.textContent = clock(dict.elapsed);
      }, 300);
    } catch (error) {
      form.step = "intro";
      form.message = { text: error.message, kind: "warn" };
      renderForm();
    }
  }

  function updateSaid() {
    const el = formUi && formUi.root.querySelector(".said");
    if (!el) return;
    el.textContent = form.said || "Listening… say what goes in the form.";
    el.classList.toggle("empty", !form.said);
    el.scrollTop = el.scrollHeight;
  }

  async function fill(text) {
    form.step = "filling";
    form.message = null;
    renderForm();
    const page = form.page;
    try {
      const { job } = await fs("/api/eform", { action: "fill", title: page.title, fields: page.fields,
                                               pictures: page.pictures, text });
      for (;;) {
        await sleep(1200);
        if (!form) return;
        const r = await fs("/api/eform", { action: "poll", job });
        if (!r.done) continue;
        form.values = r.values || {};
        form.labels = r.labels || {};
        form.optionNames = r.options || {};
        form.problems = r.problems || [];
        form.include = Object.fromEntries(Object.keys(form.values).map((k) => [k, true]));
        form.step = Object.keys(form.values).length ? "review" : "intro";
        if (!Object.keys(form.values).length) {
          form.message = { text: form.problems[0] || "The AI found nothing for this form in what was said.", kind: "warn" };
        }
        renderForm();
        return;
      }
    } catch (error) {
      if (!form) return;
      form.step = "intro";
      form.message = { text: error.message, kind: "warn" };
      renderForm();
    }
  }

  async function write() {
    const values = {};
    for (const [id, on] of Object.entries(form.include)) if (on) values[id] = form.values[id];
    const reply = await send({ fs: "form-write", frameId: form.page.frameId, values });
    if (!reply.ok || !reply.data.done) {
      form.message = { text: reply.error || "The form changed or was closed; nothing was written.", kind: "warn" };
      renderForm();
      return;
    }
    form.step = "done";
    form.written = reply.data.written;
    renderForm();
  }

  function fieldOf(id) { return (form.page.fields || []).find((f) => f.id === id) || {}; }

  function editorFor(id) {
    const f = fieldOf(id);
    const v = form.values[id] ?? "";
    if (f.kind === "checkbox") return `<label><input type="checkbox" data-val="${id}" ${v === "yes" ? "checked" : ""}> Ticked</label>`;
    if (f.kind === "radio" || f.kind === "choice") {
      const named = (form.optionNames || {})[id];
      const labels = named && named.length === f.options.length ? named
        : f.option_labels && f.option_labels.length === f.options.length ? f.option_labels : f.options;
      return `<select data-val="${id}">${f.options.map((o, i) =>
        `<option value="${esc(o)}" ${o === v ? "selected" : ""}>${esc(labels[i])}</option>`).join("")}</select>`;
    }
    return f.multiline || String(v).length > 60
      ? `<textarea data-val="${id}">${esc(v)}</textarea>` : `<input type="text" data-val="${id}" value="${esc(v)}">`;
  }

  function renderForm() {
    if (!form) return;
    if (!formUi) formUi = makeHost("freescribe-form-host", "inset:0;");
    const page = form.page || {};
    const filled = (page.fields || []).filter((f) => f.existing).length;
    let body = "", foot = "";
    const msg = form.message ? `<div class="msg ${form.message.kind}">${esc(form.message.text)}</div>` : "";
    if (form.step === "reading") {
      body = `<div class="listen"><span class="spin"></span>Reading the form on this page…</div>`;
    } else if (form.step === "none") {
      body = `<div class="msg warn">${esc(form.message)}</div>`;
      foot = `<button class="btn ghost" data-do="close">Close</button>`;
    } else if (form.step === "intro") {
      body = `<div class="facts"><div><b>${esc(page.title || "This form")}</b>: ${page.count} boxes${filled ? `, ${filled} already filled (kept)` : ""}.</div>
          <div>${form.patient ? `Patient from the linked chart: <b>${esc(form.patient)}</b> (name, date of birth and other details come from the chart).`
            : "No chart is linked to the FreeScribe session, so the patient's details are left for you."}</div>
          <div>Say what goes in the form: the diagnosis, findings, restrictions, dates and so on. ${form.hasVisit ? "The visit recorded in FreeScribe is used as well." : ""}</div></div>
        ${form.recording ? `<div class="msg warn">FreeScribe is recording the visit. Stop the recording to talk through the form.</div>` : ""}${msg}`;
      foot = `<span class="note">You check every answer before it goes into the form.</span>
        ${form.hasVisit ? `<button class="btn ghost" data-do="visit-only">Fill from the visit only</button>` : ""}
        <button class="btn ghost" data-do="close">Cancel</button>
        <button class="btn primary" data-do="talk" ${form.recording ? "disabled" : ""}>Start talking</button>`;
    } else if (form.step === "talking") {
      body = `<div class="listen"><span class="dot"></span>Listening <span data-clock>0:00</span><span class="level"><span></span></span></div>
        <div class="said empty">Listening… say what goes in the form.</div>${msg}`;
      foot = `<span class="note">Pause now and then; the words appear as you go.</span>
        <button class="btn ghost" data-do="close">Cancel</button><button class="btn danger" data-do="done-talking">Done, fill the form</button>`;
    } else if (form.step === "filling") {
      body = `<div class="listen"><span class="spin"></span>The AI is filling ${page.count} boxes${form.said ? " from what you said" : ""}…</div>
        <div class="facts">This can take a minute or more with the AI on this computer. Nothing is written into the form yet.</div>
        ${form.said ? `<div class="said">${esc(form.said)}</div>` : ""}`;
      foot = `<button class="btn ghost" data-do="close">Cancel</button>`;
    } else if (form.step === "review") {
      const ids = Object.keys(form.values).sort((a, b) => (fieldOf(a).top || 0) - (fieldOf(b).top || 0));
      const on = ids.filter((id) => form.include[id]).length;
      body = `<div class="facts">Check each answer. Untick any you don't want; you can edit them here.</div>
        <table><thead><tr><th></th><th>Box</th><th>Answer</th></tr></thead><tbody>${ids.map((id) => `
          <tr class="${form.include[id] ? "" : "off"}"><td class="c"><input type="checkbox" data-inc="${id}" ${form.include[id] ? "checked" : ""} aria-label="Use this answer"></td>
          <td class="l">${esc(form.labels[id] || fieldOf(id).label || fieldOf(id).name || "Box")}</td><td>${editorFor(id)}</td></tr>`).join("")}</tbody></table>
        ${form.problems && form.problems.length ? `<div class="msg warn">Some boxes could not be filled: ${esc(form.problems[0])}</div>` : ""}${msg}`;
      foot = `<span class="note">Nothing is saved or submitted. Save the form in OSCAR as usual.</span>
        <button class="btn ghost" data-do="close">Cancel</button><button class="btn primary" data-do="write" ${on ? "" : "disabled"}>Write ${on} into the form</button>`;
    } else if (form.step === "done") {
      body = `<div class="msg ok">Wrote ${form.written} answer${form.written === 1 ? "" : "s"} into the form. Check them, then save the form in OSCAR.</div>`;
      foot = `<button class="btn primary" data-do="close">Close</button>`;
    }
    formUi.root.innerHTML = `<div class="shade"><div class="card" role="dialog" aria-label="Fill this form by talking">
      <div class="head"><h2>Fill this form by talking</h2><button class="x" data-do="close" aria-label="Close">×</button></div>
      <div class="body">${body}</div>${foot ? `<div class="foot">${foot}</div>` : ""}</div></div>`;
    if (form.step === "talking") updateSaid();
    for (const b of formUi.root.querySelectorAll("button[data-do]")) {
      b.addEventListener("click", () => {
        const what = b.dataset.do;
        if (what === "close") closeForm();
        else if (what === "talk") talk();
        else if (what === "visit-only") fill("");
        else if (what === "done-talking") { if (dict) stopDictation(false); form.step = "filling"; renderForm(); }
        else if (what === "write") write();
      });
    }
    for (const box of formUi.root.querySelectorAll("[data-inc]")) {
      box.addEventListener("change", () => { form.include[box.dataset.inc] = box.checked; renderForm(); });
    }
    for (const ed of formUi.root.querySelectorAll("[data-val]")) {
      ed.addEventListener("change", () => {
        form.values[ed.dataset.val] = ed.type === "checkbox" ? (ed.checked ? "yes" : "") : ed.value;
      });
      ed.addEventListener("input", () => { if (ed.type !== "checkbox") form.values[ed.dataset.val] = ed.value; });
    }
  }

  /* ------------------------------------------------------------- shortcut and panel */
  try {
    chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
      if (msg && msg.fs === "voice-toggle") { toggle(); reply({ ok: true }); }
      if (msg && msg.fs === "remove-panel") { if (dict) cancelDictation(); closeForm(); }
      return false;
    });
  } catch (_error) { /* orphaned */ }

  window.__fsVoice = {
    version: FS_VERSION,
    toggle,
    openForm,
    active: () => !!dict,
    tidy: () => tidyDefault,
    setTidy: setTidyDefault,
  };
})();
