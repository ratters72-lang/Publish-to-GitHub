import subprocess
import sys
import os
import tempfile

sys.path.insert(0, os.getcwd())

import run_fb_verifiers as _suite

VERIF = list(_suite.VERIFIERS)


def parse(text):
    res = None
    fail = False
    passed = False
    for line in text.splitlines():
        up = line.strip().upper()
        if "RESULT:" in up:
            tok = line.split("RESULT:", 1)[1].strip().split()
            tok = tok[0].upper() if tok else "UNKNOWN"
            if tok in ("PASS", "ALL"):
                res = "PASS"
                passed = True
            else:
                res = tok
                fail = True
        if up.startswith("PASS:"):
            passed = True
            if res is None:
                res = "PASS"
        if up.startswith("FAIL:"):
            fail = True
            res = "FAIL"
        if "ALL CHECKS PASSED" in up:
            passed = True
            if res in (None, "UNKNOWN"):
                res = "PASS"
    if fail:
        return "FAIL"
    return res or ("PASS" if passed else "UNKNOWN")


start = int(sys.argv[1])
end = int(sys.argv[2])
LOG = "tmp/chunk_%d_%d.txt" % (start, end)

def emit(line):
    with open(LOG, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    print(line)


with open(LOG, "w", encoding="utf-8") as handle:
    handle.write("")
handle.close()

for name in VERIF[start:end]:
    if not os.path.exists(name):
        emit("SKIP     " + name)
        continue
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as tf:
        path = tf.name
    txt = ""
    crash = ""
    try:
        with open(path, "w", encoding="utf-8") as lf:
            subprocess.run(
                [sys.executable, name],
                stdout=lf,
                stderr=subprocess.STDOUT,
                timeout=900,
                check=False,
            )
        txt = open(path, encoding="utf-8", errors="replace").read()
    except Exception as exc:
        crash = "%s: %s" % (type(exc).__name__, exc)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    r = "ERROR" if crash else parse(txt)
    emit("%-8s %s" % (r, name))
    if crash:
        emit("   ! " + crash)
    if r != "PASS":
        for tail_line in txt.splitlines()[-30:]:
            emit("   | " + tail_line)
