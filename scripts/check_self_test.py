"""
check_self_test.py

Reads the report written by `FreeScribe --self-test` and decides whether the
install passed: every step worked, the transcript contains enough of the words
spoken in the test conversation, and a note was written that mentions the
complaint. Prints a summary (and adds it to the GitHub job summary when run in
GitHub Actions). Exit code 0 = passed.

    python scripts/check_self_test.py report.json [label]

Written for FreeScribe (AGPL-3.0).
"""

import json
import os
import re
import sys

# Words from scripts/test_visit.txt that a working transcription should contain
EXPECTED = ["chest", "pain", "three days", "stairs", "rest", "breath", "metformin", "diabetes",
            "blood pressure", "ecg", "blood work", "next week"]
NEED = 7                     # of the above
NOTE_MUST_MENTION = ["chest"]


def found_words(text):
    lowered = re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())
    lowered = re.sub(r"\s+", " ", lowered)
    hits = [w for w in EXPECTED if w in lowered]
    if "ekg" in lowered and "ecg" not in hits:
        hits.append("ecg")
    return hits


def main(path, label=""):
    lines, passed = [], True
    try:
        with open(path, encoding="utf-8") as handle:
            report = json.load(handle)
    except (OSError, ValueError) as error:
        print(f"{label}: no readable self-test report ({error})")
        write_summary(f"### {label}\n\n:x: No self-test report: {error}\n")
        return 1

    steps = report.get("steps", {})
    lines.append(f"Version {report.get('version')} on {report.get('platform')}, "
                 f"{report.get('total_seconds')} s in total")
    for name, step in steps.items():
        mark = "ok" if step.get("ok") else "FAILED"
        extra = f" ({step.get('error')})" if step.get("error") else ""
        took = f", took {step['took']} s" if "took" in step else ""
        lines.append(f"- {name}: {mark} at {step.get('seconds')} s{took}{extra}")
        if not step.get("ok"):
            passed = False

    transcript = steps.get("transcript", {}).get("text", "")
    hits = found_words(transcript)
    lines.append(f"- transcript words: {len(hits)}/{len(EXPECTED)} heard ({', '.join(hits)})")
    if len(hits) < NEED:
        passed = False
        lines.append(f"  needs at least {NEED}")

    note = steps.get("note", {}).get("text", "")
    if "note" not in steps:
        passed = False
        lines.append("- note: not written")
    elif not all(word in note.lower() for word in NOTE_MUST_MENTION):
        passed = False
        lines.append(f"- note does not mention: {', '.join(NOTE_MUST_MENTION)}")

    verdict = "PASSED" if passed else "FAILED"
    print(f"{label}: {verdict}")
    print("\n".join(lines))
    print("\n--- transcript ---\n" + transcript + "\n--- note ---\n" + note)
    write_summary(f"### {label}: {verdict}\n\n" + "\n".join(lines) +
                  f"\n\n<details><summary>Transcript and note</summary>\n\n```\n{transcript}\n```\n\n"
                  f"```\n{note}\n```\n</details>\n")
    return 0 if passed else 1


def write_summary(text):
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else os.path.basename(sys.argv[1])))
