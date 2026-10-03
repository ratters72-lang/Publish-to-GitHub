"""High-precision read of goal_line_outcomes.csv (READ-ONLY).

Prints per-(band, scenario) means to 6 dp and the max |difference| vs neutral
per channel, so the report can state exactly how large the tryline knob's
effect is (the 2-dp aggregate hides a sub-centimetre shift).
"""
import csv
import statistics

rows = [r for r in csv.DictReader(open("goal_line_outcomes.csv",
                                       encoding="utf-8"))
        if r["booted"] == "True"]
bands = []
for r in rows:
    if r["band"] not in bands:
        bands.append(r["band"])
scenarios = ("neutral", "10m_squeeze", "tryline_squeeze", "both_squeeze")


def mean(band, label, col):
    vals = [float(r[col]) for r in rows
            if r["band"] == band and r["label"] == label and r[col]]
    return statistics.mean(vals) if vals else float("nan")


for band in bands:
    print("band = %s" % band)
    summary = {}
    for sc in scenarios:
        m = mean(band, sc, "metres")
        c = mean(band, sc, "closest_m")
        summary[sc] = (m, c)
        print("  %-16s metres=%.6f  closest=%.6f" % (sc, m, c))
    nm, nc = summary["neutral"]
    for sc in scenarios[1:]:
        m, c = summary[sc]
        print("  delta %-12s dmetres=%+.6f  dclosest=%+.6f" % (sc, m - nm, c - nc))
    # max per-channel absolute difference vs neutral, at run granularity
    print("  per-channel max |dmetres| vs neutral:")
    chans = sorted({float(r["channel"]) for r in rows if r["band"] == band})
    for sc in scenarios[1:]:
        worst = 0.0
        for ch in chans:
            base = statistics.mean(
                [float(r["metres"]) for r in rows
                 if r["band"] == band and r["label"] == "neutral"
                 and float(r["channel"]) == ch])
            got = statistics.mean(
                [float(r["metres"]) for r in rows
                 if r["band"] == band and r["label"] == sc
                 and float(r["channel"]) == ch])
            worst = max(worst, abs(got - base))
        print("    %-16s %.6f m" % (sc, worst))
    print()
