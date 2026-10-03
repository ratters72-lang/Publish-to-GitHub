import json
import sys

text = open("tmp/s4_fb_suite.txt", encoding="utf-8", errors="replace").read()
line = [l for l in text.splitlines() if l.startswith("FULLBACK_TELEMETRY_JSON:")]
if not line:
    print("NO JSON LINE FOUND")
    print(text[-4000:])
    sys.exit(1)
data = json.loads(line[-1].split(": ", 1)[1])
bad = {k: v for k, v in data.items() if v.get("result") != "PASS"}
print("verifiers:", len(data))
for name in sorted(data):
    print("  %-58s %s" % (name, data[name].get("result")))
print("non-PASS:", len(bad))
for name in sorted(bad):
    print("  !!", name, bad[name])
