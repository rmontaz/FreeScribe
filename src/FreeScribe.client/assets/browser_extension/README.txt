FreeScribe for OSCAR (Chrome extension)

Control FreeScribe during a visit and put the finished note into OSCAR, or any
web-based EMR, with one click.

Setup (once)
1. In FreeScribe, click "Chrome extension" in the sidebar and turn it on.
2. In Chrome, open chrome://extensions, turn on "Developer mode" (top right),
   click "Load unpacked" and choose this folder.
3. Pin FreeScribe (puzzle icon in the toolbar), click it and paste the pairing
   code from FreeScribe.
4. Open OSCAR, click the FreeScribe button and switch on "Floating panel on this
   site". Chrome asks once to allow the extension on your OSCAR address. The
   panel then appears on OSCAR pages, including the pop-up chart windows that
   have no toolbar.

The floating panel
- Drag it by its top bar; drop it near the left or right edge to stick it there
  (or use the two side buttons). The minus button shrinks it to a small button.
  It remembers where you put it.
- "Don't show on this site" (the ... menu) turns it off; the switch in the
  FreeScribe button turns it back on.

During a visit
- Alt+Shift+R: start recording; press again to stop and write the note.
- Alt+Shift+P: pause or resume.
- Alt+Shift+N: insert the note where the cursor is (click in the OSCAR note box first).
- Alt+Shift+D: dictate where the cursor is; press again to stop.
- These are the default keys. If one clashes with your computer or another extension (Chrome
  leaves a shortcut unset when another extension already uses its keys), change it: the panel's
  ... menu > "Change keyboard shortcuts" opens Chrome's page for that. The panel's footer shows
  the keys as they are set now.
- Right-click selected text in the chart > "Add to FreeScribe visit context".
- Starting from a patient's chart links that patient to the FreeScribe session
  ("Link patient from chart" in the FreeScribe button does it any time). Forms then
  take the patient's name, date of birth, health card, address and phone from the
  chart instead of the AI. If the session belongs to another patient, FreeScribe
  asks you to start a new session first.

Dictating into the chart
- Click in the box where the text should go (the encounter note, a letter, any box of an
  eForm), then press the panel's dictate button (next to Start recording) or Alt+Shift+D.
- Talk. Each time you pause, the words are typed at the cursor. Click another box while
  talking and the next words go there, so a form can be dictated box by box. Say "new line"
  or "new paragraph" for a break.
- Stop, or "Stop & tidy with AI": the AI fixes punctuation, capital letters and misheard
  words in what was typed. Its version is refused if it changed a number or added or left
  out much, and it only replaces the typed text when that is still unchanged and in one box.
- FreeScribe does the listening, with the microphone and speech-to-text set in FreeScribe
  (not Chrome's), so dictation can't be on while FreeScribe is recording a visit.

Filling an eForm by talking
- Open the eForm, then the panel's ... menu > "Fill this form by talking".
- FreeScribe lists the form's boxes, then you press Start talking and say what goes in
  the form. "Fill from the visit only" uses the recorded visit without talking.
- The AI fills the boxes from what you said and the FreeScribe session. The patient's name,
  date of birth and the like come from the linked chart; your own details from "Your
  details..." in FreeScribe. Many eForms are a picture of the paper form with boxes on top;
  the labels are then read from the picture on this computer.
- Every answer is listed for you to check, edit or untick. Only then are they written into
  the form. Nothing is saved or submitted: save the form in OSCAR as usual.

Privacy: the extension talks only to FreeScribe on this computer
(127.0.0.1), using the pairing code. It can read and write a web page only
when you press its button or shortcut on that page (Chrome's "activeTab"
permission), or on the sites you turned the floating panel on for, and it
stores no notes. On those sites it remembers which text box you clicked last
(in the page only), so "Insert into chart" knows where the note goes. Patient details are read with your own OSCAR
login, only when you start recording or press "Link patient from chart", and go
only to FreeScribe on this computer. When you fill a form by talking, the form's boxes
(and the picture of the form, when its labels are only in the picture) go only to
FreeScribe on this computer.

Part of FreeScribe, released under the AGPL-3.0 license.
