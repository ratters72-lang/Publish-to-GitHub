import subprocess, sys, os, tempfile

sys.path.insert(0, os.getcwd())

import run_fb_verifiers as _suite

VERIF = list(_suite.VERIFIERS)
LOG = "tmp/s4_out.txt"


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


with open(LOG, "w", encoding="utf-8") as fh:
    fh.write("")
fh.close()

npass = 0
nother = 0
for name in VERIF:
    if not os.path.exists(name):
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write("SKIP     " + name + "\n")
        continue
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as tf:
        p = tf.name
    txt = ""
    crash = ""
    try:
        with open(p, "w", encoding="utf-8") as lf:
            subprocess.run(
                [sys.executable, name],
                stdout=lf,
                stderr=subprocess.STDOUT,
                timeout=1200,
                check=False,
            )
        txt = open(p, encoding="utf-8", errors="replace").read()
    except Exception as exc:                              # noqa: BLE001
        crash = "%s: %s" % (type(exc).__name__, exc)
    finally:
        try:
            os.remove(p)
        except OSError:
            pass
    r = "ERROR" if crash else parse(txt)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write("%-8s %s\n" % (r, name))
        if crash:
            fh.write("   ! " + crash + "\n")
        if r != "PASS":
            for line in txt.splitlines()[-40:]:
                fh.write("   | " + line + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    if r == "PASS":
        npass += 1
    else:
        nother += 1

with open(LOG, "a", encoding="utf-8") as fh:
    fh.write("TOTAL %d PASS %d OTHER %d\n" % (len(VERIF), npass, nother))
