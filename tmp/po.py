"""po.py - locate failing assertions in the two verifier contract files."""
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

TARGETS = {
    "verify_defensive_pipeline.py": ["structural spacing_y", "_spacing_y"],
    "verify_retreat_width_consistency.py": [
        "fallback == retreat chain Y",
        "SAME split-spacing chain",
    ],
}

for path, pats in TARGETS.items():
    s = open(path, encoding="utf-8", errors="replace").read()
    print("=" * 70)
    print(path)
    print("=" * 70)
    for pat in pats:
        print(f"--- {pat} ---")
        for m in re.finditer(re.escape(pat), s):
            st = max(0, m.start() - 500)
            en = min(len(s), m.end() + 300)
            print(s[st:en])
            print("   ~~~~~~")
