"""
Builds src/FreeScribe.client/assets/medical/drug_names.txt from two public sources:

* Health Canada Drug Product Database, "allfiles.zip" (Open Government Licence - Canada):
  https://www.canada.ca/en/health-canada/services/drugs-health-products/drug-products/drug-product-database/what-data-extract-drug-product-database.html
* RxNorm Current Prescribable Content, "RxNorm_full_prescribe_current.zip" (no licensing restrictions):
  https://www.nlm.nih.gov/research/umls/rxnorm/docs/rxnormfiles.html

Usage: unzip both into one folder (dpd/ and rx/), then
    python scripts/build_drug_names.py <folder> [output]

This software is released under the AGPL-3.0 license.
"""
import csv
import os
import re
import sys

WORD = re.compile(r"^[a-z][a-z\-]{2,}( [a-z][a-z\-]{2,}){0,2}$")


def clean(name):
    return re.sub(r"\s+", " ", name.strip().lower())


def build(folder, out):
    generics, brands = set(), set()
    # Human products only (the database also lists veterinary and disinfectant products)
    human = set()
    with open(os.path.join(folder, "dpd", "drug.txt"), encoding="latin-1", newline="") as f:
        for row in csv.reader(f):
            if len(row) > 2 and row[2] == "Human":
                human.add(row[0])
    # Ingredients such as "NORTRIPTYLINE (NORTRIPTYLINE HYDROCHLORIDE)"
    with open(os.path.join(folder, "dpd", "ingred.txt"), encoding="latin-1", newline="") as f:
        for row in csv.reader(f):
            if len(row) < 3 or row[0] not in human:
                continue
            for part in re.split(r"[()]", row[2]):
                part = clean(part)
                if WORD.match(part):
                    generics.add(part)
    # RxNorm ingredients (IN), precise ingredients (PIN) and brand names (BN)
    with open(os.path.join(folder, "rx", "rrf", "RXNCONSO.RRF"), encoding="utf-8") as f:
        for line in f:
            cols = line.split("|")
            if len(cols) < 15 or cols[1] != "ENG" or cols[11] != "RXNORM":
                continue
            tty, name = cols[12], cols[14]
            if tty in ("IN", "PIN"):
                name = clean(name)
                if WORD.match(name):
                    generics.add(name)
            elif tty == "BN" and re.match(r"^[A-Za-z][A-Za-z\-]{3,}$", name.strip()):
                name = name.strip()
                brands.add(name[0].upper() + name[1:].lower() if name.isupper() else name)
    # Biosimilar suffixes ("-ayyh") are not spoken
    generics = {g for g in generics if not re.search(r"-[a-z]{4}$", g)}
    with open(out, "w", encoding="utf-8") as f:
        f.write("# Drug names for FreeScribe's speech vocabulary and drug-name check.\n")
        f.write("# Generic names: Health Canada Drug Product Database (Open Government Licence - Canada)\n")
        f.write("# and RxNorm Current Prescribable Content (U.S. National Library of Medicine).\n")
        f.write("# Lines starting with 'B ' are brand names; all other lines are generic names.\n")
        for name in sorted(generics):
            f.write(name + "\n")
        for name in sorted(brands - {g.title() for g in generics}):
            f.write("B " + name + "\n")
    print(f"{len(generics)} generic names, {len(brands)} brand names -> {out}")


if __name__ == "__main__":
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    default_out = os.path.join(here, "src", "FreeScribe.client", "assets", "medical", "drug_names.txt")
    build(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else default_out)
