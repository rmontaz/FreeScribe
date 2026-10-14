# eForm Generator — Third-Party Attributions & Licenses

**Tool:** `eForm_Generator.html` ("OSCAR eForm Mapper, Standalone v19")
**Audited:** 2026-10-06. Updated the same day after the changes listed under [Changes made](#changes-made-2026-10-06).
**Tool's own license:** MIT. See `LICENSE.txt`; the header comment in `eForm_Generator.html` names the license and third-party parts.

The tool has two separate surfaces with different dependencies:

1. **The mapper itself**: what runs in your browser while you build a form.
2. **The generated eForm**: the HTML and ZIP package it exports, which runs inside OSCAR/Juno.

---

## 1. Libraries loaded by the mapper

| Library | Version | Loaded from | Used for | License | Copyright |
|---|---|---|---|---|---|
| **PDF.js** (`pdf.min.js` + `pdf.worker.min.js`) | 3.11.174 | `vendor/` folder, with cdnjs fallback | PDF → PNG page converter | Apache License 2.0 | © Mozilla Foundation and contributors |
| **JSZip** | 3.10.1 | `vendor/` folder, with cdnjs fallback | Building `oscar_eform_package.zip` | Dual **MIT** or GPL-3.0 (you choose; MIT is the practical choice) | © 2009–2016 Stuart Knightley, David Duponchel, Franz Buchinger, António Afonso |

**Bundled inside JSZip's distribution:** pako (MIT; includes zlib-derived code under the zlib license), plus small MIT-licensed helpers (`lie`, `setimmediate`, `readable-stream`). The JSZip MIT notice covers redistribution of the minified build.

### Obligations
- **Apache-2.0 (PDF.js):** the library is now vendored, so its license ships with it in `vendor/LICENSE-pdfjs.txt`. Keep that file whenever you copy the `vendor/` folder.
- **MIT (JSZip):** the license is in `vendor/LICENSE-jszip.md`, and the minified file keeps its own header.

### Not a dependency
- **Fonts:** the CSS lists `Inter, Segoe UI, Arial` and `ui-monospace, SFMono-Regular, Menlo`. None of these are downloaded; they render only if already installed on the machine. No attribution is needed.
- **Field detection** (`detectCurrentPage`, `rectScan`, `edgeCoverage`, `cornerPresence`): original code using standard image-processing techniques (adaptive threshold, gradient map, flood-fill connected components, run-length line finding). No third-party code or model.
- No `fetch`, analytics, `localStorage` or other network calls beyond the CDN script loads.

---

## 2. Dependencies embedded in the generated eForm

| Item | Version / form | Source | License | Notes |
|---|---|---|---|---|
| **jQuery** | 3.7.1 (default) or 1.7.1 (legacy option) | 3.7.1: cdnjs with an integrity hash. Both modes fall back to OSCAR's `../js/jquery-1.7.1.min.js` | MIT | © OpenJS Foundation and contributors. 1.7.1 was released dual MIT/GPL-2.0, © 2011 John Resig, and includes Sizzle.js. |
| **jQuery Migrate** | 3.4.1 (default mode only) | cdnjs with an integrity hash | MIT | © OpenJS Foundation. Restores jQuery APIs that older OSCAR scripts may still call. |
| **OSCAR `faxControl.js`** | Whatever the server ships | `${oscar_javascript_path}eforms/faxControl.js`, referenced and not copied | GPL-2.0 (part of OSCAR EMR) | Loaded at runtime from the OSCAR server. |
| **OSCAR eForm conventions** | n/a | `oscarDB="…"` tags, `${oscar_image_path}`, `${oscar_javascript_path}`, `../eform/displayImage.do?imagefile=consult_sig_…`, `eform.properties` package format | OSCAR EMR, GPL-2.0 | Interface names and placeholders, used for compatibility. They're API identifiers, not copied code. |

### Snippets likely derived from OSCAR community eForms
The export template (around lines 2553–2680) includes several patterns that are common across publicly shared OSCAR eForms:

- `parent-field` / `child-*` show/hide and `only-one-*` exclusive-checkbox jQuery handlers
- `setDirtyFlag` / `releaseDirtyFlag` / `confirmExit`, including the "You have attempted to leave this page…" message
- `SignForm()` loading `consult_sig_<providerNo>.png`
- `setFaxNo`, `checkGender` and `loadSig` stubs

**Provenance:** unconfirmed. These look adapted from community eForm templates and the OSCAR eForm documentation rather than written from scratch. If the originals were taken from OSCAR's source tree, they're GPL-2.0. Generated eForms run *inside* OSCAR, which is itself GPL-2.0, so in practice there's no conflict. Still, credit the OSCAR eForm community if you share the tool. If you know which template these came from, add it here.

**Template import:** the tool can also copy external `<script src>` URLs and inline scripts *from eForms you import* into the export (`includeSnippets`). Those carry whatever license the imported eForm had. You are responsible for them case by case.

### Platform
- **OSCAR EMR** (McMaster University / OSCAR EMR community): GPL-2.0.
- **Juno EMR** (an OSCAR fork; referenced in the "OSCAR/Juno preflight" panel): inherits GPL-2.0 from OSCAR.

---

## 3. Third-party services contacted at runtime

| Service | When | What's sent |
|---|---|---|
| cdnjs (Cloudflare) | Mapper: only if `vendor/` is missing | Your IP and user agent (script request only; no form or patient data) |
| cdnjs (Cloudflare) | Generated eForm in default jQuery mode, each time it opens in OSCAR | The clinic workstation's IP and user agent |

With `vendor/` next to the HTML, the mapper runs fully offline. Choosing "OSCAR local 1.7.1 (legacy)" makes generated eForms contact no third-party services. Page images and patient data never leave the browser.

---

## 4. Under consideration (not yet integrated)

| Item | License | Notes |
|---|---|---|
| CommonForms / **FFDNet-S/L** (Joe Barrow), github.com/jbarrow/commonforms | **Unverified**: confirm before use | YOLO-based. Ultralytics YOLO weights are commonly AGPL-3.0, which would matter if the tool is distributed. |
| onnxruntime-web | MIT (© Microsoft) | Would be required to run FFDNet in the browser. |

---

## Changes made (2026-10-06)

1. **License and header:** an MIT `LICENSE.txt` was added, and a header comment in the HTML lists third-party components. The copyright line reads "the eForm Generator author(s)"; put your name there.
2. **jQuery upgrade:** exported eForms now default to jQuery 3.7.1 + Migrate 3.4.1. 1.7.1 has known XSS advisories: CVE-2012-6708, CVE-2015-9251, CVE-2019-11358, CVE-2020-11022 and CVE-2020-11023. A new "jQuery in exported eForm" option keeps legacy 1.7.1 (OSCAR's local copy, no internet). `.error()` was changed to `.on('error')`, which works in both versions. The Google CDN reference was removed. **Before rolling out, test fax, print and signature on your OSCAR/Juno server.** If `faxControl.js` misbehaves, switch the option to legacy.
3. **Offline:** PDF.js and JSZip are in `vendor/` with their licenses. Opened by double-click (`file://`), the PDF worker runs on the main thread because browsers block Web Workers there; it's slower on large PDFs but works.
4. **Integrity:** every CDN `<script>` (fallbacks and exported jQuery) has a SHA-384 SRI hash. One gap: the pdf.js *worker* fallback from the CDN is launched as a Web Worker, which can't carry an SRI hash. Keeping `vendor/` in place avoids it.

A backup of the original is at `eForm_Generator_v19_backup.html`.
