"""
check_startup_log.py

Reads FreeScribe's log from a fresh start on a test machine and decides whether
the start went well: the speech model and the AI model loaded, and nothing
crashed. Prints a summary (and adds it to the GitHub job summary in Actions).
Exit code 0 = passed.

    python scripts/check_startup_log.py app.log [label]

Written for FreeScribe (AGPL-3.0).
"""

import os
import re
import sys

LOADED = "Models loaded successfully on startup"
SPEECH_LOADED = "STT model loaded successfully"
PROBLEMS = [
    ("crash", re.compile(r"Traceback \(most recent call last\)|Unhandled exception|No module named")),
    ("AI model failed", re.compile(r"Model initialization error|Model loading error|Model path does not exist")),
    ("speech model failed", re.compile(r"An error occurred while loading STT")),
    ("models not ready in time", re.compile(r"Models failed to load within|One or more models failed to load")),
]


def check(text):
    findings, passed = [], True
    speech = SPEECH_LOADED in text
    both = LOADED in text
    findings.append(f"- speech model loaded: {'yes' if speech or both else 'no'}")
    findings.append(f"- both models ready (app's own check): {'yes' if both else 'no'}")
    if not both:
        passed = False
    for name, pattern in PROBLEMS:
        lines = [line.strip()[:220] for line in text.splitlines() if pattern.search(line)]
        if lines:
            passed = False
            findings.append(f"- {name}: {len(lines)} line(s), first: `{lines[0]}`")
    return passed, findings


def main(path, label=""):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError as error:
        text, error_text = "", str(error)
    else:
        error_text = ""
    if not text.strip():
        message = f"### {label}: FAILED\n\nNo log was written{': ' + error_text if error_text else ''}.\n"
        print(message)
        write_summary(message)
        return 1
    passed, findings = check(text)
    verdict = "PASSED" if passed else "FAILED"
    body = f"### {label}: {verdict} (startup)\n\n" + "\n".join(findings) + "\n"
    print(body)
    write_summary(body)
    return 0 if passed else 1


def write_summary(text):
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else os.path.basename(sys.argv[1])))
