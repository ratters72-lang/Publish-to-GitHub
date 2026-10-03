"""tmp_fbel_multiseed.py — cross-seed ledger for the emergency-chase probe.

The 6000-frame rollout is NOT bit-deterministic across processes (two runs of
seed 20260827 produced different event counts), so a single rollout cannot
support a claim about which condition terminates emergency chase.  This driver
sweeps several seeds IN ONE PROCESS and tabulates, per seed:

    tagged misses, entries, releases, lifetime stats,
    the release-cause distribution, and — for each cause — how many of those
    releases fired while the tracked carrier had NOT yet been neutralised
    (the exact case the investigation asks about).

Read-only: it imports the probe and calls its own ledger/report helpers.
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import sys

sys.path.insert(0, ".")

import probe_fullback_emergency_lifetime as P

SEEDS = [
    int(s) for s in os.environ.get(
        "RLM_FBEL_SEEDS", "20260827,20260828,20260829,20260823,20260831"
    ).split(",")
]
FRAMES = int(os.environ.get("RLM_FBEL_FRAMES", "6000"))
OUT = os.environ.get("RLM_FBEL_MULTI_OUT", "tmp_fbel_multiseed.txt")

report = open(OUT, "w", encoding="utf-8")


def out(line=""):
    print(line)
    report.write(line + "\n")


def median(values):
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


GRAND = {}


def main():
    out("=" * 100)
    out("FULLBACK EMERGENCY-CHASE LIFETIME — CROSS-SEED LEDGER")
    out("=" * 100)
    out("  frames per rollout : %d" % FRAMES)
    out("  window per event   : %d frames" % P.WINDOW_FRAMES)
    out("  seeds              : %s" % SEEDS)

    for seed in SEEDS:
        res = P.run_rollout(frames=FRAMES, seed=seed)
        ledger = res["ledger"]
        tagged = [e for e in ledger.miss_events if e["tagged"]]
        records = P.collect_releases(ledger)
        life = P.window_lifetimes(ledger)
        released = [r for r in life if r[3] is not None]
        lifetimes = [r[3] for r in released]

        out("")
        out("-" * 100)
        out("SEED %d" % seed)
        out("-" * 100)
        out("  tagged misses=%d  entries=%d  releases=%d  windows=%d"
            % (len(tagged),
               sum(1 for rows in ledger.traces.values()
                   for r in rows if r["field"] == "(entered)"),
               len(released),
               len(life)))
        if lifetimes:
            out("  lifetime frames: mean %.1f  median %.1f  min %d  max %d"
                % (sum(lifetimes) / len(lifetimes),
                   median(lifetimes), min(lifetimes), max(lifetimes)))

        by_cause = {}
        for rec in records:
            by_cause.setdefault(rec[1].get("cause"), []).append(rec)

        if not by_cause:
            out("  (no release was captured for this seed)")
            continue

        out("  %-30s %6s %6s %10s %8s"
            % ("cause", "n", "live", "dist_mean", "dist_max"))
        for cause in sorted(by_cause, key=lambda c: -len(by_cause[c])):
            recs = by_cause[cause]
            live = sum(1 for r in recs if not P.is_neutralised(r[1], r[2]))
            dists = [d for d in (P.release_distance(r[1]) for r in recs)
                     if d is not None]
            dmean = (sum(dists) / len(dists)) if dists else None
            dmax = max(dists) if dists else None
            out("  %-30s %6d %6d %10s %8s"
                % (cause, len(recs), live,
                   P.fmt(dmean, 2) if dmean is not None else "--",
                   P.fmt(dmax, 2) if dmax is not None else "--"))
            slot = GRAND.setdefault(cause, {"n": 0, "live": 0})
            slot["n"] += len(recs)
            slot["live"] += live

    out("")
    out("=" * 100)
    out("GRAND TOTAL ACROSS %d SEEDS" % len(SEEDS))
    out("=" * 100)
    total = sum(v["n"] for v in GRAND.values())
    total_live = sum(v["live"] for v in GRAND.values())
    out("  %-30s %6s %6s" % ("cause", "n", "live"))
    for cause in sorted(GRAND, key=lambda c: -GRAND[c]["n"]):
        out("  %-30s %6d %6d"
            % (cause, GRAND[cause]["n"], GRAND[cause]["live"]))
    out("  %-30s %6d %6d" % ("TOTAL", total, total_live))
    out("")
    out("  Report written to %s" % OUT)


if __name__ == "__main__":
    main()
    report.flush()
    report.close()
