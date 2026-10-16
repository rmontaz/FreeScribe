FreeScribe for OSCAR (Chrome extension)

Control FreeScribe during a visit and put the finished note into OSCAR, or any
web-based EMR, with one click.

Setup (once)
1. In FreeScribe, click "Chrome extension" in the sidebar and turn it on.
2. In Chrome, open chrome://extensions, turn on "Developer mode" (top right),
   click "Load unpacked" and choose this folder.
3. Pin FreeScribe (puzzle icon in the toolbar), click it and paste the pairing
   code from FreeScribe.

During a visit
- Alt+Shift+R: start recording; press again to stop and write the note.
- Alt+Shift+P: pause or resume.
- Alt+Shift+N: insert the note where the cursor is (click in the OSCAR note box first).
- Right-click selected text in the chart > "Add to FreeScribe visit context".
- Starting from a patient's chart links that patient to the FreeScribe session
  ("Link patient from chart" in the FreeScribe button does it any time). Forms then
  take the patient's name, date of birth, health card, address and phone from the
  chart instead of the AI. If the session belongs to another patient, FreeScribe
  asks you to start a new session first.

Privacy: the extension talks only to FreeScribe on this computer
(127.0.0.1), using the pairing code. It can read and write a web page only
when you press its button or shortcut on that page (Chrome's "activeTab"
permission), and it stores no notes. Patient details are read with your own OSCAR
login, only when you start recording or press "Link patient from chart", and go
only to FreeScribe on this computer.

Part of FreeScribe, released under the AGPL-3.0 license.
