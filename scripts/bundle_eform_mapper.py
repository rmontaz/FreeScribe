"""
Bundles the OSCAR eForm Mapper (eForm Generator) with FreeScribe.

Copies the mapper page, its vendor/ libraries and its licence files into
src/FreeScribe.client/assets/eform_mapper/ and adds the FreeScribe connection:
a "Find fields from PDF" button that appears only when FreeScribe opens the page,
and asks FreeScribe to find the fields in the PDF the form came from.

Usage, from the repository root:

    python scripts/bundle_eform_mapper.py "/path/to/eForm_Generator.html"

The vendor/ folder, LICENSE.txt and eForm_Generator_ATTRIBUTIONS.md are taken
from the same folder as the mapper page. Run it again whenever the mapper changes.

FreeScribe's copy also has vendor/standard_fonts/ (pdf.js 3.11.174's standard
fonts, from the pdfjs-dist package), which pdf.js needs to draw tick boxes typed
in symbol fonts such as ZapfDingbats. This script leaves that folder in place.
If a newer mapper no longer contains a piece of code the additions hook into,
the script stops and says which one, without writing anything.

This software is released under the AGPL-3.0 license.
"""

import hashlib
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "src", "FreeScribe.client", "assets", "eform_mapper")
VENDOR_FILES = ["pdf.min.js", "pdf.worker.min.js", "jszip.min.js", "LICENSE-pdfjs.txt", "LICENSE-jszip.md"]
MARK = "FreeScribe connection (added by FreeScribe)"

HEADER_NOTE = """
  Bundled with FreeScribe: this copy adds a "Find fields from PDF" button that
  appears when FreeScribe opens the page (look for "FreeScribe connection").
  Everything else is the mapper as written by its author(s); the original is unchanged.
"""

CSS = """
  /* FreeScribe connection (added by FreeScribe) */
  .fs-panel{margin-top:10px;border:1px solid #ddd6fe;background:#f5f3ff;border-radius:10px;padding:8px 10px}
  .fs-panel[hidden]{display:none}
  .fs-badge{background:#7c3aed;color:#fff;border-radius:999px;padding:2px 8px;font-size:11px;font-weight:600}
  .fs-panel select{width:auto;min-width:120px}
  .fs-panel label{display:inline-flex}
"""

PANEL = """    <!-- FreeScribe connection (added by FreeScribe): shown only when FreeScribe opens this page -->
    <div id="fsPanel" class="fs-panel" hidden>
      <div class="row" style="align-items:center">
        <span class="fs-badge">FreeScribe</span><span id="fsStatus" class="small"></span>
        <button id="btnFsDetect" class="btn-violet" title="FreeScribe reads the PDF itself and places fields exactly where it draws lines, boxes and tick boxes. Works for PDFs made on a computer, not for scans.">Find fields from PDF</button>
        <select id="fsScope" title="Which mapper pages to fill">
          <option value="page">This page</option>
          <option value="all">All pages</option>
        </select>
        <label class="small" style="display:flex;align-items:center;gap:6px"><input type="checkbox" id="fsReplace" checked/> Replace fields already there</label>
      </div>
      <div class="row small" style="margin-top:6px;align-items:center">
        <span id="fsPdfName"></span>
        <button id="btnFsPickPdf" type="button">Choose source PDF&hellip;</button>
        <input id="fsPdfFile" type="file" accept="application/pdf,.pdf" hidden/>
      </div>
      <div class="small" style="margin-top:4px">For scanned forms, keep using Auto Detect.</div>
    </div>

"""

SCRIPT = r"""
  // ===================== FreeScribe connection (added by FreeScribe) =====================
  // Opened from FreeScribe, the page can ask FreeScribe (a server on this computer only) to
  // read the PDF the form came from and place fields exactly where the PDF draws them.
  // Opened as a plain file, the panel stays hidden and nothing here runs.
  state.sourcePdf = state.sourcePdf || null;
  const FS = { token: '', busy: false };

  function fsBytesToBase64(buffer){
    const bytes = new Uint8Array(buffer);
    let binary = '';
    for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(binary);
  }
  function fsCapturePdf(name, buffer){
    try { state.sourcePdf = { name: String(name || 'form.pdf'), data: fsBytesToBase64(buffer) }; }
    catch(_){ state.sourcePdf = null; }
    fsShowPdf();
  }
  function fsShowPdf(){
    const el = $('fsPdfName');
    if (!el) return;
    el.textContent = state.sourcePdf ? `Source PDF: ${state.sourcePdf.name}`
                                     : 'No source PDF yet. Load the form with the PDF→Image Tool, or choose it:';
  }
  async function fsRequest(path, body){
    const headers = { 'X-FreeScribe-Token': FS.token };
    if (body) headers['Content-Type'] = 'application/json';
    const res = await fetch(path, { method: body ? 'POST' : 'GET', headers, body: body ? JSON.stringify(body) : undefined, cache: 'no-store' });
    let data = {};
    try { data = await res.json(); } catch(_){}
    if (!res.ok) throw new Error(data.error || `FreeScribe answered ${res.status}.`);
    return data;
  }
  function fsTargetPages(){
    const loaded = Object.keys(state.pages).map(Number).filter(n => state.pages[n] && state.pages[n].imgData).sort((a,b) => a-b);
    if ($('fsScope').value === 'all') return loaded;
    return loaded.includes(Number(state.currentPage)) ? [Number(state.currentPage)] : [];
  }
  async function fsFindFields(){
    if (FS.busy) return;
    if (!state.sourcePdf){ msg('Choose the PDF this form came from first.', 'warn'); $('fsPdfFile').click(); return; }
    const pages = fsTargetPages();
    if (!pages.length){ msg('Load the form pages first (PDF→Image Tool).', 'warn'); return; }
    const replace = $('fsReplace').checked;
    const before = state.fields.filter(f => pages.includes(Number(f.page))).length;
    const kept = replace ? state.fields.filter(f => !pages.includes(Number(f.page))) : state.fields;
    FS.busy = true; $('btnFsDetect').disabled = true;
    msg('FreeScribe is reading the PDF…');
    try{
      const res = await fsRequest('/api/detect', {
        pdf: state.sourcePdf.data,
        pages: pages.map(n => ({ page: n, pdfPage: state.pages[n].pdfPage || n,
                                 naturalW: state.pages[n].naturalW, naturalH: state.pages[n].naturalH })),
        tagCatalog: [...state.tagCatalog],
        takenNames: kept.map(f => f.name)
      });
      const found = res.fields || [];
      pushUndo();
      if (replace) state.fields = kept;
      found.forEach(f => addField({ page: f.page, x: f.x, y: f.y, width: f.width, height: f.height, type: f.type,
                                    name: f.name, oscarTag: f.oscarTag || '', label: f.label || '' }));
      state.selected.clear();
      renderAll();
      const boxes = found.filter(f => f.type === 'checkbox').length;
      const tagged = found.filter(f => f.oscarTag).length;
      const where = pages.length === 1 ? `page ${pages[0]}` : `${pages.length} pages`;
      const replaced = replace && before ? ` Replaced the ${before} field(s) that were there (Undo brings them back).` : '';
      msg(found.length
          ? `Found ${found.length} field(s) on ${where}: ${boxes} checkbox(es) and ${found.length - boxes} other. ${tagged} tagged from their labels.${replaced} Check them before exporting.`
          : `No fields found on ${where}.`, found.length ? 'ok' : 'warn');
    }catch(err){ msg('Find fields from PDF failed: ' + (err.message || err), 'err'); }
    finally{ FS.busy = false; $('btnFsDetect').disabled = false; }
  }
  async function fsConnect(){
    const match = /(?:^|[#&])fs=([A-Za-z0-9_-]+)/.exec(location.hash || '');
    let token = match ? match[1] : '';
    try { if (token) sessionStorage.setItem('fsToken', token); else token = sessionStorage.getItem('fsToken') || ''; } catch(_){}
    if (match){ try { history.replaceState(null, '', location.pathname + location.search); } catch(_){} }
    if (!token || location.protocol === 'file:') return;
    FS.token = token;
    $('fsPanel').hidden = false;
    try{
      await fsRequest('/api/status');
      $('fsStatus').textContent = 'Connected';
      fsShowPdf();
    }catch(_){
      $('fsStatus').textContent = 'Not connected. Open the mapper again from FreeScribe.';
      $('btnFsDetect').disabled = true;
    }
  }
  $('btnFsDetect').addEventListener('click', fsFindFields);
  $('btnFsPickPdf').addEventListener('click', () => $('fsPdfFile').click());
  $('fsPdfFile').addEventListener('change', async (e) => {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    try { fsCapturePdf(file.name, await file.arrayBuffer()); msg(`Source PDF set to ${file.name}.`, 'ok'); }
    catch(err){ msg('Could not read that PDF: ' + (err.message || err), 'err'); }
    finally { e.target.value = ''; }
  });
  fsConnect();
  // =================== end of FreeScribe connection ===================

"""

# (description, text to find, replacement, how many times it must occur)
PATCHES = [
    ("title", "<title>OSCAR eForm Mapper (Standalone v19)</title>",
     "<title>OSCAR eForm Mapper (Standalone v19 + FreeScribe)</title>", 1),
    ("styles", "</style>\n</head>", CSS + "</style>\n</head>", 1),
    ("panel", '    <div class="quickstart">', PANEL + '    <div class="quickstart">', 1),
    ("keep the PDF when it is loaded",
     "        const arr = await file.arrayBuffer();\n",
     "        const arr = await file.arrayBuffer();\n        fsCapturePdf(file.name, arr.slice(0));\n", 1),
    ("remember which PDF page each image came from",
     "async function loadBlobIntoMapper(pageNo, blob, fileName=''){",
     "async function loadBlobIntoMapper(pageNo, blob, fileName='', pdfPage=null){", 1),
    ("store the PDF page number",
     "        fileName: resolvedName\n      };",
     "        fileName: resolvedName,\n        pdfPage: pdfPage || null\n      };", 1),
    ("single page conversion",
     "await loadBlobIntoMapper(targetPage, outputs[0].blob, titleSlug()+'_page'+targetPage+'.png');",
     "await loadBlobIntoMapper(targetPage, outputs[0].blob, titleSlug()+'_page'+targetPage+'.png', outputs[0].page);", 1),
    ("all pages conversion",
     "await loadBlobIntoMapper(out.page, out.blob, titleSlug()+'_page'+out.page+'.png');",
     "await loadBlobIntoMapper(out.page, out.blob, titleSlug()+'_page'+out.page+'.png', out.page);", 1),
    ("save the PDF with the session",
     "      exportAuthor: state.exportAuthor || ($('eformAuthor')?.value || ''),\n      snap: { ...state.snap }\n    };",
     "      exportAuthor: state.exportAuthor || ($('eformAuthor')?.value || ''),\n      snap: { ...state.snap },\n"
     "      sourcePdf: state.sourcePdf || null\n    };", 1),
    ("restore page numbers from a session",
     "fileName:pd.fileName||'' };",
     "fileName:pd.fileName||'', pdfPage:Number(pd.pdfPage)||null };", 1),
    ("restore the PDF from a session",
     "    state.showLabels = obj.showLabels !== false;\n",
     "    state.sourcePdf = (obj.sourcePdf && obj.sourcePdf.data) ? obj.sourcePdf : null;\n    fsShowPdf();\n"
     "    state.showLabels = obj.showLabels !== false;\n", 1),
    ("draw symbol-font tick boxes properly in page images",
     "pdfjsLib.getDocument({data:arr})",
     "pdfjsLib.getDocument({data:arr, standardFontDataUrl:'vendor/standard_fonts/'})", 1),
    ("connection code", "  setCurrentPage(1);\n  renderAll();\n})();\n</script>",
     SCRIPT + "  setCurrentPage(1);\n  renderAll();\n})();\n</script>", 1),
]


def patch(html: str) -> str:
    if MARK in html:
        raise SystemExit("This file already has the FreeScribe connection. Give the original mapper file.")
    for description, old, new, count in PATCHES:
        found = html.count(old)
        if found != count:
            raise SystemExit(f"Could not add the FreeScribe connection ({description}): expected the mapper code\n"
                             f"    {old.strip()[:100]}\nonce, found it {found} time(s). Nothing was written.")
        html = html.replace(old, new)
    header_end = html.index("-->")
    return html[:header_end] + HEADER_NOTE + html[header_end:]


def main(source: str):
    source_dir = os.path.dirname(os.path.abspath(source))
    with open(source, encoding="utf-8") as handle:
        html = patch(handle.read())
    if not os.path.isfile(os.path.join(TARGET, "vendor", "standard_fonts", "FoxitDingbats.pfb")):
        print("Note: vendor/standard_fonts/ is missing from FreeScribe's copy; tick boxes typed in symbol "
              "fonts will be drawn as dots. Copy standard_fonts/ from pdfjs-dist 3.11.174 into it.")
    missing = [name for name in VENDOR_FILES if not os.path.isfile(os.path.join(source_dir, "vendor", name))]
    missing += [name for name in ("LICENSE.txt", "eForm_Generator_ATTRIBUTIONS.md")
                if not os.path.isfile(os.path.join(source_dir, name))]
    if missing:
        raise SystemExit("Missing next to the mapper: " + ", ".join(missing) + ". Nothing was written.")

    os.makedirs(os.path.join(TARGET, "vendor"), exist_ok=True)
    with open(os.path.join(TARGET, "eform_mapper.html"), "w", encoding="utf-8") as handle:
        handle.write(html)
    for name in VENDOR_FILES:
        shutil.copyfile(os.path.join(source_dir, "vendor", name), os.path.join(TARGET, "vendor", name))
    for name in ("LICENSE.txt", "eForm_Generator_ATTRIBUTIONS.md"):
        shutil.copyfile(os.path.join(source_dir, name), os.path.join(TARGET, name))
    digest = hashlib.sha256(open(source, "rb").read()).hexdigest()[:16]
    print(f"Bundled {os.path.basename(source)} (sha256 {digest}…) into {os.path.relpath(TARGET, ROOT)}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
