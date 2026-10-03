 import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import run_fb_verifiers as suite

out = {}
failed = []
for name in suite.VERIFIERS:
    if not os.path.exists(name):
        out[name] = {"result": "SKIP", "reason": "not found"}
        continue
    proc = subprocess.run(
        [sys.executable, name],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    text = proc.stdout.decode("utf-8", errors="replace")
    result, metrics = suite._parse(text)
    out[name] = {"result": result or "UNKNOWN", **metrics}
    if result != "PASS":
        failed.append(name)
        with open("tmp/s4_fail_%s.log" % name.replace(".py", ""), "w",
                  encoding="utf-8") as fh:
            fh.write(text)

with open("tmp/s4_suite_result.json", "w", encoding="utf-8") as fh:
    json.dump({"results": out, "failed": failed}, fh, indent=2)

print("verifiers:", len(out))
for name in sorted(out):
    print("  %-58s %s" % (name, out[name].get("result")))
print("failed:", len(failed))
for name in failed:
    print("  !!", name)
