"""Per-channel breakdown of goal_line_outcomes.csv (READ-ONLY diagnostic).

The aggregate table hides the structure: squeezing the line pulls defenders
inward, which should help the CENTRE channels and expose the WIDE ones.  Print
metres conceded and break/try rate per (band, scenario, channel) so the report
can state where the squeeze helps and where it costs.
"""
import csv
import statistics

PATH = "goal_line_outcomes.csv"
SCENARIOS = ("neutral", "10m_squeeze", "tryline_squeeze", "both_squeeze")

rows = list(csv.DictReader(open(PATH, encoding="utf-8")))
bands = []
for r in rows:
    if r["band"] not in bands:
        bands.append(r["band"])
channels = []
for r in rows:
    c = float(r["channel"])
    if c not in channels:
        channels.append(c)
channels.sort()

for band in bands:
    print("=" * 78)
    print("band = %s" % band)
    print("=" * 78)
    print("  %-16s %8s %9s %9s %10s" % ("scenario", "channel",
                                        "n", "metres", "break%"))
    for ch in channels:
        for sc in SCENARIOS:
            sub = [r for r in rows
                   if r["band"] == band and r["label"] == sc
                   and float(r["channel"]) == ch and r["booted"] == "True"]
            if not sub:
                continue
            metres = [float(r["metres"]) for r in sub if r["metres"]]
            brk = sum(1 for r in sub if r["broke"] == "True")
            print("  %-16s %8.1f %9d %9.2f %8.0f%%"
                  % (sc, ch, len(sub),
                     statistics.mean(metres) if metres else float("nan"),
                     100.0 * brk / len(sub)))
        print()
