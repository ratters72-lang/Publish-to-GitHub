"""tmp/dump_checks.py — print the exact failing checks' source regions."""

import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

REGIONS = [
    ("verify_contact_authority_contract.py", 735, 40),
    ("verify_contact_authority_contract.py", 885, 55),
    ("verify_contest_transport_contract.py", 78, 14),
    ("verify_contest_transport_contract.py", 810, 40),
    ("verify_contest_preservation_contract.py", 1, 0),
    ("verify_contest_record_schema_contract.py", 1, 0),
]

for path, start, count in REGIONS:
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
    if count == 0:
        # find the check with the failing index
        for i, ln in enumerate(lines, 1):
            low = ln.lower()
            if ("carrier as the authority left it" in ln
                    or "authority left it" in ln
                    or "disturbed by EXACTLY ONE" in ln):
                print("")
                print("### %s  (anchored at line %d)" % (path, i))
                for j in range(max(1, i - 22), min(len(lines), i + 8) + 1):
                    print("%5d| %s" % (j, lines[j - 1].rstrip()))
        continue
    print("")
    print("### %s  lines %d..%d" % (path, start, start + count - 1))
    for j in range(start, min(len(lines), start + count - 1) + 1):
        print("%5d| %s" % (j, lines[j - 1].rstrip()))
