import json

with open("tmp/s4_suite_result.json", encoding="utf-8") as fh:
    data = json.load(fh)

results = data["results"]
failed = data["failed"]

print("verifiers: %d" % len(results))
for name in sorted(results):
    entry = results[name]
    verdict = entry.get("result")
    extra = ""
    if verdict != "PASS":
        extra = "  " + json.dumps(
            {k: v for k, v in entry.items() if k != "result"})
    print("  %-56s %s%s" % (name, verdict, extra))

print("")
print("FAILED: %d" % len(failed))
for name in failed:
    print("  !! %s" % name)
