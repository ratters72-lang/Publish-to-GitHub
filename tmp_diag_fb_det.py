"""Locate the first divergence between two same-seed FB traces (temp diagnostic)."""
import hashlib
import io
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
_REAL = sys.stdout

import verify_fb_determinism as vfd


def _silent_run():
    sys.stdout = io.TextIOWrapper(
        open(os.devnull, "wb"), encoding="utf-8", errors="replace"
    )
    try:
        return vfd.run_simulation()
    finally:
        sys.stdout = _REAL


a = _silent_run()
b = _silent_run()

print("len_a=%d len_b=%d" % (len(a), len(b)))
print("hash_a=%s" % hashlib.sha256("\n".join(a).encode()).hexdigest()[:16])
print("hash_b=%s" % hashlib.sha256("\n".join(b).encode()).hexdigest()[:16])

shown = 0
for i in range(min(len(a), len(b))):
    if a[i] != b[i]:
        print("FIRST_DIFF index=%d" % i)
        print("  A: %s" % a[i])
        print("  B: %s" % b[i])
        shown += 1
        if shown >= 3:
            break
if shown == 0:
    print("NO_DIFF within process")
sys.exit(0)
