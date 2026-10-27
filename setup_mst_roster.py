"""Configure a MINIMAL, private MST roster for local V3.1 testing.

Does not access the original Workshop V3 database or the Walmart network.
Only enter associates you are authorized to use on this computer.
"""
import csv
from pathlib import Path

from app import PRIVATE, MANAGEMENT_ASSOCIATES, SHIFT_RULES

TARGET = PRIVATE / "mst-roster.csv"
HEADERS = ["Name", "Assoc. #", "Shift", "Job Title"]


def main():
    PRIVATE.mkdir(parents=True, exist_ok=True)
    if (PRIVATE / "Drax Associates.xlsx").exists() or (PRIVATE / "mst-roster.xlsx").exists():
        print("An Excel roster already exists. It takes priority over this CSV.")
        print("Use that authorized roster or remove it from this project's private folder first.")
        return
    entries = {}
    if TARGET.exists():
        with TARGET.open(newline="", encoding="utf-8-sig") as source:
            for row in csv.DictReader(source):
                num = str(row.get("Assoc. #", "")).strip()
                if num.isdecimal():
                    entries[num] = row
    print("V3.1 — local MST shift assignments (no sample accounts are created)")
    print("Associate numbers stay in .local-data/ and are excluded from Git.")
    while True:
        number = input("MST associate number (Enter to finish): ").strip()
        if not number:
            break
        if not number.isdecimal() or len(number) > 12 or number in MANAGEMENT_ASSOCIATES:
            print("Enter a valid non-management associate number.")
            continue
        shift = input("Assigned shift (S1–S5): ").strip().upper()
        if shift not in SHIFT_RULES:
            print("Please use S1, S2, S3, S4 or S5.")
            continue
        name = input("Display name (optional): ").strip() or "MST Associate"
        entries[number] = dict(zip(HEADERS, [name, number, shift, "MST"]))
        print(f"Locally assigned to {shift}.")
    if not entries:
        print("No MST assignments entered.")
        return
    temporary = TARGET.with_suffix(".csv.tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADERS)
        writer.writeheader()
        for num in sorted(entries):
            writer.writerow(entries[num])
    temporary.chmod(0o600)
    temporary.replace(TARGET)
    TARGET.chmod(0o600)
    print(f"Saved {len(entries)} authorized local MST assignment(s) to {TARGET}")


if __name__ == "__main__":
    main()
