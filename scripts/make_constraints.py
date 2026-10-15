"""
make_constraints.py

Writes constraints/<platform>.txt from a release build's log: the exact version of
every Python package that build installed. The release workflow installs with
`pip install -r <requirements> -c constraints/<platform>.txt`, so a later build gets
the same packages instead of whatever is newest that day (an unpinned dependency
once broke every installer at startup).

To refresh after a build you have tested:
    gh run view <run id> -R <owner/repo> --log --job <job id> > build.log
    python scripts/make_constraints.py build.log constraints/<platform>.txt <release tag>

Written for FreeScribe (AGPL-3.0).
"""

import re
import sys


def installed_versions(log_text):
    """{package: version} from pip's "Successfully installed" lines (a later install wins)."""
    versions = {}
    for line in log_text.splitlines():
        match = re.search(r"Successfully installed (.+)$", line)
        if not match:
            continue
        for item in match.group(1).split():
            name, _, version = item.rpartition("-")
            if name and version and version[0].isdigit():
                versions[name.lower().replace("_", "-")] = version
    return versions


def main(log_path, out_path, tag):
    with open(log_path, encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    versions = installed_versions(text)
    if not versions:
        sys.exit(f"No 'Successfully installed' lines in {log_path}")
    with open(out_path, "w", encoding="utf-8") as out:
        out.write(f"# Exact package versions installed by the working {tag} build.\n")
        out.write("# Used as pip constraints (-c) so later builds install the same versions.\n")
        out.write("# Refresh with scripts/make_constraints.py after testing a new build.\n")
        for name in sorted(versions):
            out.write(f"{name}=={versions[name]}\n")
    print(f"{out_path}: {len(versions)} packages")


if __name__ == "__main__":
    main(*sys.argv[1:4])
