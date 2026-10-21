"""
Builds a single-file, offline copy of FreeScribe's OSCAR eForm Mapper to share.

FreeScribe's copy of the mapper (src/FreeScribe.client/assets/eform_mapper/) loads
its libraries from a vendor/ folder next to it. This script writes one HTML file
with everything it needs inside it, so it works from a USB stick, an email
attachment or a shared drive, with no internet and no other files:

- pdf.js 3.11.174 and its worker (the PDF -> PNG converter)
- pdf.js's standard fonts (tick boxes and text drawn in fonts the PDF doesn't carry,
  such as ZapfDingbats)
- JSZip 3.10.1 (the OSCAR package download)
- the licence texts of the mapper and of every bundled library

Everything else is FreeScribe's mapper as it is, including the fixes made for
FreeScribe (each form gets its own image and file names). The "Find fields"
panel stays hidden: it only appears when FreeScribe itself opens the page.
Exported eForms still load jQuery the usual way (from the internet, with OSCAR's
own copy as the fallback), because they run inside OSCAR, not in this file.

Usage, from the repository root (run it again after any change to the mapper):

    python scripts/build_standalone_mapper.py [output.html]

The default output is dist/eForm_Generator_FreeScribe_offline.html.
If the mapper no longer contains a piece of code this script replaces, it stops
and says which one, without writing anything.

This software is released under the AGPL-3.0 license.
"""

import base64
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAPPER = os.path.join(ROOT, "src", "FreeScribe.client", "assets", "eform_mapper")
VENDOR = os.path.join(MAPPER, "vendor")
FONTS = os.path.join(VENDOR, "standard_fonts")
DEFAULT_OUTPUT = os.path.join(ROOT, "dist", "eForm_Generator_FreeScribe_offline.html")

LICENSES = [
    ("OSCAR eForm Mapper (eForm Generator) - MIT License", os.path.join(MAPPER, "LICENSE.txt")),
    ("Third-party attributions", os.path.join(MAPPER, "eForm_Generator_ATTRIBUTIONS.md")),
    ("PDF.js 3.11.174 - Apache License 2.0", os.path.join(VENDOR, "LICENSE-pdfjs.txt")),
    ("JSZip 3.10.1 - MIT or GPL-3.0", os.path.join(VENDOR, "LICENSE-jszip.md")),
    ("pdf.js standard fonts (Foxit) - BSD-style licence", os.path.join(FONTS, "LICENSE_FOXIT")),
    ("pdf.js standard fonts (Liberation) - SIL Open Font License 1.1", os.path.join(FONTS, "LICENSE_LIBERATION")),
]

HEADER_OLD = """  Libraries load from ./vendor/ first and fall back to cdnjs (SRI-pinned) if absent.

  Bundled with FreeScribe: this copy adds a "Find fields" button that
  appears when FreeScribe opens the page (look for "FreeScribe connection").
  Everything else is the mapper as written by its author(s); the original is unchanged.
-->"""

HEADER_NEW = """  OFFLINE SINGLE-FILE EDITION: pdf.js (with its worker and standard fonts) and JSZip
  are embedded below, unmodified, so this one file works with no internet and no
  other files. Their licence texts are at the end of this file (id="licenses").

  FreeScribe's edition: each form gets its own image and file names, and a "Find fields"
  button appears only when FreeScribe opens the page (look for "FreeScribe connection").
  Rebuild with scripts/build_standalone_mapper.py in the FreeScribe repository.
-->"""

LIB_TAGS = re.compile(
    r'<!-- Local copies first \(offline\); CDN fallback is integrity-pinned\. -->\n'
    r'<script src="vendor/pdf\.min\.js"></script>\n<script>window\.pdfjsLib \|\| document\.write\([^\n]*\n'
    r'<script src="vendor/jszip\.min\.js"></script>\n<script>window\.JSZip \|\| document\.write\([^\n]*\n')

WORKER_OLD = re.compile(
    r"    // Prefer the local worker\. Browsers refuse Web Workers.*?\n    function ensurePdfWorker\(\)\{\n.*?\n"
    r"      return pdfWorkerReady;\n    \}\n", re.S)

WORKER_NEW = r"""    // Offline edition: pdf.js's worker is embedded in this page (<script id="pdfjsWorkerSource">).
    // Served over http(s) it runs as a real Web Worker from a blob URL; opened as a file
    // (file://), where some browsers refuse workers, it runs on the page's own thread.
    let pdfWorkerReady = null;
    function ensurePdfWorker(){
      if (pdfWorkerReady) return pdfWorkerReady;
      pdfWorkerReady = (async () => {
        if (pdfjsLib.GlobalWorkerOptions.workerSrc || window.pdfjsWorker) return;
        const source = (document.getElementById('pdfjsWorkerSource') || {}).textContent || '';
        if (!source) throw new Error('The embedded pdf.js worker is missing from this file.');
        if (location.protocol !== 'file:'){
          pdfjsLib.GlobalWorkerOptions.workerSrc =
            URL.createObjectURL(new Blob([source], { type: 'application/javascript' }));
          return;
        }
        const el = document.createElement('script');
        el.textContent = source;
        document.head.appendChild(el);
        pdfjsLib.GlobalWorkerOptions.workerSrc = 'embedded';  // unused: window.pdfjsWorker is set
      })();
      return pdfWorkerReady;
    }

    // pdf.js asks for its standard fonts (ZapfDingbats tick boxes, Helvetica...) by file
    // name; they are embedded in this page (<script id="pdfjsStandardFonts">).
    class EmbeddedStandardFonts {
      constructor(){
        if (!EmbeddedStandardFonts.files){
          const el = document.getElementById('pdfjsStandardFonts');
          EmbeddedStandardFonts.files = el ? JSON.parse(el.textContent) : {};
        }
      }
      async fetch({ filename }){
        const b64 = EmbeddedStandardFonts.files[filename];
        if (!b64) throw new Error('Font not embedded: ' + filename);
        const raw = atob(b64), bytes = new Uint8Array(raw.length);
        for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
        return bytes;
      }
    }
"""

REPLACEMENTS = [
    ("header note", HEADER_OLD, HEADER_NEW),
    ("licence pointer", "Licensed under the MIT License - see LICENSE.txt in this folder.",
     "Licensed under the MIT License - the text is at the end of this file (id=\"licenses\")."),
    ("pdf.js credit", "(vendor/pdf.min.js, vendor/pdf.worker.min.js)", "(embedded in this file)"),
    ("JSZip credit", "(vendor/jszip.min.js)", "(embedded in this file)"),
    ("converter description",
     "Uses pdf.js from the local vendor/ folder (works offline); falls back to the cdnjs CDN if that folder is missing.",
     "Uses pdf.js embedded in this file (works fully offline)."),
    ("pdf.js missing message",
     "pdf.js failed to load (vendor/ folder missing and CDN unreachable).",
     "pdf.js failed to load from this file."),
    ("standard fonts",
     "pdfjsLib.getDocument({data:arr, standardFontDataUrl:'vendor/standard_fonts/'})",
     "pdfjsLib.getDocument({data:arr, StandardFontDataFactory: EmbeddedStandardFonts, useWorkerFetch: false})"),
]


def read(path, mode="r"):
    with open(path, mode, **({} if "b" in mode else {"encoding": "utf-8"})) as handle:
        return handle.read()


def script_safe(text, what):
    """Text that is about to sit inside a <script> element must not end it early."""
    if re.search(r"</script|<!--", text, re.IGNORECASE):
        raise SystemExit(f"{what} contains '</script' or '<!--' and cannot be embedded as it is.")
    return text


def build(output):
    html = read(os.path.join(MAPPER, "eform_mapper.html"))

    for name, old, new in REPLACEMENTS:
        if html.count(old) != 1:
            raise SystemExit(f"The mapper changed: could not find the {name} to replace. Nothing written.")
        html = html.replace(old, new)

    if len(WORKER_OLD.findall(html)) != 1:
        raise SystemExit("The mapper changed: could not find ensurePdfWorker() to replace. Nothing written.")
    html = WORKER_OLD.sub(lambda _m: WORKER_NEW, html)

    pdf_js = script_safe(read(os.path.join(VENDOR, "pdf.min.js")), "pdf.min.js")
    worker = script_safe(read(os.path.join(VENDOR, "pdf.worker.min.js")), "pdf.worker.min.js")
    jszip = script_safe(read(os.path.join(VENDOR, "jszip.min.js")), "jszip.min.js")
    fonts = {name: base64.b64encode(read(os.path.join(FONTS, name), "rb")).decode("ascii")
             for name in sorted(os.listdir(FONTS)) if name.endswith((".pfb", ".ttf"))}
    libs = (
        "<!-- Embedded, unmodified: pdf.js 3.11.174 (Apache-2.0, Mozilla Foundation), its worker and\n"
        "     standard fonts, and JSZip 3.10.1 (MIT or GPL-3.0). Licences: see id=\"licenses\" at the end. -->\n"
        f"<script>\n{pdf_js}\n</script>\n"
        f"<script>\n{jszip}\n</script>\n"
        f'<script type="text/plain" id="pdfjsWorkerSource">{worker}</script>\n'
        f'<script type="application/json" id="pdfjsStandardFonts">{json.dumps(fonts)}</script>\n'
    )
    if len(LIB_TAGS.findall(html)) != 1:
        raise SystemExit("The mapper changed: could not find the vendor/ <script> tags to replace. Nothing written.")
    html = LIB_TAGS.sub(lambda _m: libs, html)

    licenses = "\n\n".join(
        f"==== {title} ====\n\n{script_safe(read(path), title).strip()}" for title, path in LICENSES)
    end = html.rindex("</html>")
    html = (html[:end] + '<script type="text/plain" id="licenses">\n'
            "Licences of the eForm Mapper and of the libraries embedded in this file.\n"
            "(The attributions describe the folder edition, where the libraries sit in vendor/;\n"
            "in this single-file edition the same, unmodified libraries are embedded above.)\n\n"
            f"{licenses}\n</script>\n" + html[end:])

    if re.search(r'src="vendor/|vendor/standard_fonts|vendor/pdf', html):
        raise SystemExit("A reference to the vendor/ folder is left in the page. Nothing written.")

    os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
    with open(output, "w", encoding="utf-8") as handle:
        handle.write(html)
    print(f"Wrote {output} ({len(html.encode('utf-8')) / 1048576:.1f} MB, {len(fonts)} fonts embedded)")


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUTPUT)
