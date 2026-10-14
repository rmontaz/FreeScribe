# Third-party licences

FreeScribe is released under the GNU Affero General Public License v3.0 (see LICENSE.txt). It is built on open-source libraries, each under its own licence; the full list of dependencies is in the `client_requirements*.txt` files of the [FreeScribe source code](https://github.com/ClinicianFOCUS/FreeScribe).

This page lists the libraries used by PDF form filling, and the notices their licences ask to be reproduced.

## pypdf

Used to read, fill and save PDF forms. Version 6.20.1, [github.com/py-pdf/pypdf](https://github.com/py-pdf/pypdf). Licensed under the BSD 3-Clause License:

Copyright (c) 2006-2008, Mathieu Fenniak  
Some contributions copyright (c) 2007, Ashish Kulkarni  
Some contributions copyright (c) 2014, Steve Witham

All rights reserved.

Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.
* Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following disclaimer in the documentation and/or other materials provided with the distribution.
* The name of the author may not be used to endorse or promote products derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

## PyMuPDF and MuPDF

Used to find the blanks on flat PDF forms and add form fields to them. PyMuPDF version 1.28.2, Copyright (c) Artifex Software, Inc., [github.com/pymupdf/PyMuPDF](https://github.com/pymupdf/PyMuPDF).

PyMuPDF and the MuPDF library it includes are dual-licensed by Artifex Software, Inc. FreeScribe uses them under the GNU Affero General Public License v3.0, the same licence as FreeScribe itself. Their complete source code is available from the link above and from [mupdf.com](https://mupdf.com). MuPDF in turn includes third-party libraries under their own licences, which are listed in its source distribution.

## OSCAR eForm Mapper

The eForm mapper that opens from FreeScribe's sidebar is the OSCAR eForm Mapper (eForm Generator, Standalone v19), Copyright (c) 2026 the eForm Generator author(s), released under the MIT License. FreeScribe's copy adds the "Find fields from PDF" connection; the rest is unchanged. Its files are in `assets/eform_mapper/`:

* `LICENSE.txt`: the mapper's MIT License
* `eForm_Generator_ATTRIBUTIONS.md`: the mapper's own list of the components it uses and the ones its exported eForms use (jQuery, OSCAR EMR conventions)

The mapper uses these libraries, kept in `assets/eform_mapper/vendor/` with their licence files:

* **PDF.js** 3.11.174, Copyright (c) Mozilla Foundation and contributors, Apache License 2.0 (`vendor/LICENSE-pdfjs.txt`)
* **PDF.js standard fonts** from pdfjs-dist 3.11.174, used to draw symbol-font characters such as tick boxes: Foxit fonts, Copyright 2014 PDFium Authors, BSD 3-Clause License (`vendor/standard_fonts/LICENSE_FOXIT`); Liberation Sans, Copyright (c) 2010 Google Corporation and (c) 2012 Red Hat, Inc., SIL Open Font License 1.1 (`vendor/standard_fonts/LICENSE_LIBERATION`)
* **JSZip** 3.10.1, Copyright (c) 2009-2016 Stuart Knightley, David Duponchel, Franz Buchinger, António Afonso, dual-licensed MIT or GPL-3.0 and used under the MIT License (`vendor/LICENSE-jszip.md`)

## Development tools

The following are used only to generate the test PDFs in `tests/fixtures`, and are not part of the FreeScribe application:

* ReportLab, Copyright (c) ReportLab Inc., BSD License, [reportlab.com](https://www.reportlab.com)
* python-docx, Copyright (c) 2013 Steve Canny, MIT License, [github.com/python-openxml/python-docx](https://github.com/python-openxml/python-docx)
