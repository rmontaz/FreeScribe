/*
 * oscar_query.js: runs a read-only query the clinician approved in FreeScribe's chat through
 * OSCAR's own "Query by Example" report (Admin > Reports), with the clinician's own login.
 *
 * The report page is opened in a hidden frame on this OSCAR site, the query is put in its box
 * and submitted the way a person would (so OSCAR's own checks and security tokens apply), and
 * the result table is read back. FreeScribe checked the query is a single SELECT first.
 *
 * Part of FreeScribe, released under the AGPL-3.0 license.
 */
(() => {
  if (window.top !== window || window.__fsOscarQuery) return;

  const PAGES = ["oscarReport/RptByExample.do", "oscarReport/RptByExample.jsp", "oscarReport/RptByExample"];
  const MAX_ROWS = 1000;
  const PAGE_MS = 30000;          // opening the report page
  const QUERY_MS = 180000;        // OSCAR running the query (large clinics: big tables)

  // Where OSCAR lives on this site: usually /oscar, but a page can sit elsewhere (OSCAR Pro's newer
  // screens), so /oscar is always tried too
  const bases = () => {
    const first = location.pathname.split("/")[1];
    return [...new Set([first ? `${location.origin}/${first}` : null, `${location.origin}/oscar`,
      location.origin].filter(Boolean))];
  };

  const loginPage = (doc) => !!doc.querySelector("input[type=password]") ||
    /login\.do|logout\.jsp/i.test((doc.querySelector("form") || {}).action || "");

  const sqlBox = (doc) => doc.querySelector("textarea[name=sql], input[name=sql]");

  // Opens ``url`` in a hidden frame and resolves with it once loaded
  function frameAt(url) {
    return new Promise((resolve, reject) => {
      const frame = document.createElement("iframe");
      frame.setAttribute("aria-hidden", "true");
      frame.tabIndex = -1;
      frame.style.cssText = "position:fixed;width:1px;height:1px;left:-9999px;top:-9999px;border:0;opacity:0;";
      const timer = setTimeout(() => { frame.remove(); reject(new Error("OSCAR's report page took too long to open.")); }, PAGE_MS);
      frame.addEventListener("load", () => { clearTimeout(timer); resolve(frame); }, { once: true });
      frame.src = url;
      document.documentElement.appendChild(frame);
    });
  }

  const nextLoad = (frame) => new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(Object.assign(new Error(
      "OSCAR took more than 3 minutes to run the query. It is probably too slow for a database this size: " +
      "ask a narrower question (a shorter time period, one condition)."), { final: true })), QUERY_MS);
    frame.addEventListener("load", () => { clearTimeout(timer); resolve(); }, { once: true });
  });

  const cellText = (cell) => (cell.innerText !== undefined && cell.innerText !== "" ? cell.innerText : cell.textContent || "")
    .replace(/\s+/g, " ").trim();

  // The result table. Where it sits differs between OSCAR versions (OSCAR Pro puts it in a layer that
  // comes before the form in the page, though it shows below it), so it is found by what it is: a table
  // whose first row is column headings (th), with no form fields, nested tables or navigation links.
  function readResult(doc) {
    const box = sqlBox(doc);
    const form = box && box.form;
    let best = null, bestScore = -1;
    for (const table of doc.querySelectorAll("table")) {
      if (table.querySelector("table, input, select, textarea, form, button")) continue;
      if (form && (table.contains(form) || form.contains(table))) continue;
      const rows = [...table.rows].filter((r) => r.cells.length);
      if (!rows.length) continue;
      const cells = rows.flatMap((r) => [...r.cells]);
      const links = cells.every((c) => { const a = c.querySelector("a"); return a && cellText(c) === cellText(a); });
      if (links) continue;                                     // the page's menu
      const header = [...rows[0].cells].every((c) => c.tagName === "TH");
      if (!header) continue;                                   // OSCAR's results always start with headings
      if (rows.length > bestScore) { best = rows; bestScore = rows.length; }
    }
    if (!best) return null;
    const columns = [...best[0].cells].map(cellText);
    const rows = best.slice(1, MAX_ROWS + 1).map((r) => [...r.cells].map(cellText));
    return { columns, rows };
  }

  // OSCAR's message when the query failed (the first line that reads like an error)
  function errorText(doc) {
    const text = doc.body ? (doc.body.innerText || doc.body.textContent || "") : "";
    const bad = /exception|error has occurred|syntax|unknown column|no such (column|table)|doesn't exist|access denied|not authori[sz]ed|permission|privilege/i;
    const line = text.split(/\n+/).map((l) => l.replace(/\s+/g, " ").trim()).find((l) => bad.test(l));
    return line ? line.slice(0, 300) : "";
  }

  // The report address that worked on this site, shared by all its tabs (tried first next time)
  const KNOWN_KEY = "freescribe-oscar-report";
  let known = (() => { try { return localStorage.getItem(KNOWN_KEY) || ""; } catch (_e) { return ""; } })();
  const remember = (url) => { known = url; try { localStorage.setItem(KNOWN_KEY, url); } catch (_e) { /* private mode */ } };
  let kept = null;                // the hidden report page, reused for the next query
  let idleTimer = null;
  const IDLE_MS = 90000;          // the hidden page is closed after this long without a query
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // What OSCAR showed instead of the report (its title or first heading), for the message
  function shown(doc) {
    if (!doc) return "nothing";
    const title = (doc.title || "").trim();
    const heading = doc.querySelector("h1, h2, h3");
    const body = doc.body ? (doc.body.innerText || doc.body.textContent || "") : "";
    const text = (title || (heading && heading.textContent) || body).replace(/\s+/g, " ").trim().slice(0, 80);
    return text ? `“${text}”` : "a blank page";
  }
  const missing = (doc) => !!doc && /\b404\b|not found/i.test(`${doc.title} ${doc.body ? doc.body.textContent.slice(0, 300) : ""}`);

  // The report page at ``url`` in a hidden frame, with its query box; a page OSCAR (or a service in
  // front of it) answers with something else is tried again after a pause
  async function openReport(url, problem) {
    for (let attempt = 0; attempt < 3; attempt++) {
      let frame = null;
      try {
        frame = await frameAt(url);
      } catch (error) {
        problem.text = error.message;
        await sleep(1500 * (attempt + 1));
        continue;
      }
      const doc = frame.contentDocument;
      if (doc && loginPage(doc)) {
        frame.remove();
        throw Object.assign(new Error("Log in to OSCAR in this browser first."), { final: true });
      }
      const box = doc && sqlBox(doc);
      if (box && box.form) return frame;
      const why = doc && errorText(doc);
      const gone = missing(doc) && url !== known;
      if (!gone || !problem.real) {          // a missing address never hides what OSCAR said at the real one
        problem.text = why || `OSCAR showed ${shown(doc)} instead of the Query by Example report.`;
        problem.real = !gone;
      }
      frame.remove();
      if (gone) return null;                                     // not this address: try the next one
      await sleep(1500 * (attempt + 1));
    }
    return null;
  }

  async function run(sql) {
    clearTimeout(idleTimer);
    const problem = { text: "OSCAR's Query by Example report was not found on this site (looked for " +
      "oscarReport/RptByExample.do). Check that your OSCAR login can open Admin > Reports > Query by Example." };
    let frame = kept && kept.isConnected && kept.contentDocument && sqlBox(kept.contentDocument) ? kept : null;
    if (!frame) {
      if (kept) kept.remove();
      kept = null;
      const urls = [];
      for (const base of bases()) for (const page of PAGES) urls.push(`${base}/${page}`);
      if (known && urls.includes(known)) urls.splice(urls.indexOf(known), 1), urls.unshift(known);
      for (const url of urls) {
        frame = await openReport(url, problem);
        if (frame) { remember(url); break; }
      }
      if (!frame) throw new Error(problem.text);
    }
    kept = frame;
    try {
      const box = sqlBox(frame.contentDocument);
      box.value = sql;
      // From here on the query has been sent: it is never sent again
      const loaded = nextLoad(frame);
      const button = box.form.querySelector("input[type=submit], button[type=submit], button:not([type])");
      if (button) button.click(); else box.form.submit();
      try { await loaded; } catch (error) { error.final = true; throw error; }
      const doc = frame.contentDocument;
      if (!doc) throw new Error("OSCAR's answer could not be read.");
      if (loginPage(doc)) throw new Error("OSCAR asked to log in again. Log in, then run it again.");
      if (!sqlBox(doc)) throw new Error(`OSCAR showed ${shown(doc)} instead of the result of the query.`);
      const result = readResult(doc);
      // OSCAR Pro shows a query with an error as an empty table without column names (and no message)
      const named = result && result.columns.some((c) => c.trim());
      if (!result || !named) {
        const why = errorText(doc);
        throw new Error(why || "OSCAR returned no result table: the query probably has an error " +
          "(OSCAR doesn't say which), for example a column this OSCAR doesn't have.");
      }
      return { columns: result.columns, rows: result.rows };
    } catch (error) {
      frame.remove();
      kept = null;
      throw error;
    } finally {
      idleTimer = setTimeout(() => { if (kept) kept.remove(); kept = null; }, window.__fsQueryIdleMs || IDLE_MS);
    }
  }

  window.__fsOscarQuery = run;
})();
