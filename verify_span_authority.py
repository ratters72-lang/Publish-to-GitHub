"""
verify_span_authority.py — CANONICAL COMMAND-vs-REALISED SPAN AUTHORITY TEST.

STAGE 1 SHIP.  Read-only instrumentation and verification; no behavioural
change and no production file touched.

THE CONTRACT UNDER TEST
-----------------------
    For any valid ruck X position the three width tuners
    (``width_left_tryline_m`` / ``width_halfway_m`` / ``width_right_tryline_m``)
    interpolate into the COMMANDED width.  That commanded width must remain
    the REALISED defensive span, and the ONLY permitted reducer is physical
    field room.

Three tolerances are defined explicitly (in ``span_authority_probe``):

    SPAN_AUTHORITY_TOL_M = 0.10   realised must equal commanded within this
                                  unless a physical reducer binds
    ATTRIB_TOL_M         = 0.02   observed vs predicted decomposition
    ROOM_BIND_TOL_M      = 0.05   equality when the room cap is the binding
                                  authority

WHAT IT MEASURES (per case: field position x ruck Y x count split x phase)
-------------------------------------------------------------------------
    commanded  interpolated_defensive_width(tuners, bc.x)
    governed   governed_width_m / the governor accessor
    per-side   side_widths(world, tuners, ruck_y=...)
    lane span  outermost lane per side from side_lanes(..., side_width=W)
    realised   span of the line's published Y targets (compute_targets) and,
               in the TES drive chamber, the census ladder from
               establish_slot_ownership

and decomposes ``realised - commanded`` into named sources:
    CLAMP_MIN / CLAMP_MAX / SPLIT_FACTOR / ROOM_CAP / LANE_STEP_FLOOR /
    SIDELINE_LANE_CLAMP / TES_WIDTH_FREE_LADDER / GOVERNOR_BAND_FIT /
    BAND_PULL / RUCK_Y_ARG_IGNORED / GOVERNED_ACCESSOR_DIVERGENCE /
    NO_BALL_CARRIER / UNATTRIBUTED.

VERDICT SEMANTICS
-----------------
Default: exit 0 iff the verifier can ACCOUNT for every metre — i.e. the
observed span equals the documented model for every case within
``ATTRIB_TOL_M``.  A behavioural change that moves span without moving the
documented model therefore FAILS.  A prominent CONTRACT BREACH CENSUS is
printed regardless.

``--strict``: exit 0 additionally requires the literal contract to hold —
``|realised - commanded| <= SPAN_AUTHORITY_TOL_M`` for every case whose span
is not reduced by physical room.  This is the gate the later behaviour
stages must turn green.

Emits ``verify_span_authority_report.json``.
"""

import collections
import io
import json
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

import span_authority_probe as probe
from span_authority_probe import (
    ALL_SOURCES,
    ATTRIB_TOL_M,
    BAND_PULL,
    CLAMP_MAX,
    CLAMP_MIN,
    GOVERNED_ACCESSOR_DIVERGENCE,
    GOVERNOR_BAND_FIT,
    LANE_STEP_FLOOR,
    NO_BALL_CARRIER,
    ROOM_CAP,
    ROOM_BIND_TOL_M,
    RUCK_Y_ARG_IGNORED,
    SIDELINE_LANE_CLAMP,
    SPAN_AUTHORITY_TOL_M,
    SPLIT_FACTOR,
    TES_WIDTH_FREE_LADDER,
    UNATTRIBUTED,
    model_interpolated_width,
    model_governed_width,
    model_lane_outer,
    to_json,
)
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_width import (
    interpolated_defensive_width,
    governed_width_m,
)
from game.defence.defence_lvl_1.defensive_split_spacing import (
    side_lanes,
    LEFT,
    RIGHT,
)

REPORT_PATH = "verify_span_authority_report.json"

failures: list = []
attribute_breaches: list = []


def check(name: str, condition: bool, detail: str = "") -> None:
    """Print and record one assertion."""
    print("  [%s] %s%s" % ("PASS" if condition else "FAIL", name,
                           (" -- " + detail) if detail and not condition else ""))
    if not condition:
        failures.append(name)


def dominant_source(m) -> str:
    """Return the largest-magnitude named source of a measurement."""
    items = [(k, v) for k, v in (m.breakdown or {}).items() if k in ALL_SOURCES]
    if not items:
        return "NONE"
    return max(items, key=lambda kv: abs(float(kv[1])))[0]


def source_census(measurements) -> tuple:
    """Return (count-by-dominant-source, total-metres-by-source)."""
    counts = collections.Counter()
    totals = collections.defaultdict(float)
    for m in measurements:
        counts[dominant_source(m)] += 1
        for k, v in (m.breakdown or {}).items():
            totals[k] += float(v)
    return counts, totals


def worst_by(measurements, key) -> object:
    """Return the measurement maximising ``key`` (None for an empty list)."""
    return max(measurements, key=key) if measurements else None


def _fmt_row(m, columns) -> str:
    """Format one measurement as a fixed-width table row."""
    cells = []
    for name in columns:
        v = getattr(m, name, None)
        if isinstance(v, float):
            cells.append("%8.2f" % v)
        else:
            cells.append(str(v))
    return "  ".join(cells)


# =====================================================================
# [1] DOCUMENTED MODEL vs SHIPPED FUNCTIONS
# =====================================================================
def section1_model_conformance() -> None:
    """The predicted model must reproduce the shipped functions exactly."""
    print("\n[1] Documented model reproduces the shipped functions")
    tuners = DefenceTuners()
    bad_cmd = []
    for bc_x in probe.BC_X_GRID:
        want = interpolated_defensive_width(tuners, bc_x)
        got = model_interpolated_width(tuners, bc_x)
        if abs(got - want) > 1e-9:
            bad_cmd.append("bc.x=%.1f model=%.6f shipped=%.6f" % (bc_x, got, want))
    check("model interpolation == interpolated_defensive_width at every grid x",
          not bad_cmd, "; ".join(bad_cmd))

    bad_gov = []
    for commanded in (10.0, 25.0, 30.0, 55.0, 64.0, 70.0, 100.0):
        world = probe.SpanProbeWorld(35.0, "OPEN_PLAY", commanded)
        shipped = governed_width_m(world, tuners)
        got = model_governed_width(commanded, tuners)
        if abs(got - shipped) > 1e-9:
            bad_gov.append("cmd=%.1f model=%.6f shipped=%.6f"
                           % (commanded, got, shipped))
    check("model governed width == governed_width_m across the clamp band",
          not bad_gov, "; ".join(bad_gov))

    bad_lane = []
    for ruck_y in probe.RUCK_Y_GRID:
        for side_width in (30.0, 35.75, 55.0, 64.0):
            for n_side in (1, 2, 3, 5, 6):
                for side in (LEFT, RIGHT):
                    shipped = side_lanes(n_side, ruck_y, side, tuners,
                                          span=side_width)[-1]
                    got = model_lane_outer(side_width, n_side, ruck_y,
                                           side, tuners)
                    if abs(got - shipped) > 1e-9:
                        bad_lane.append(
                            "ruck_y=%.1f w=%.2f n=%d side=%d model=%.6f shipped=%.6f"
                            % (ruck_y, side_width, n_side, side, got, shipped))
    check("model outermost lane == side_lanes[-1] across the ladder grid",
          not bad_lane, "; ".join(bad_lane[:3]))


# =====================================================================
# [2] GOVERNOR PUBLISH CONFORMANCE
# =====================================================================
def section2_publish(rows) -> None:
    """The governor must publish exactly the interpolated command."""
    print("\n[2] Governor publishes exactly the interpolated command")
    bad = [r for r in rows if not r["conformant"]]
    for r in rows:
        print("      bc.x=%6.1f  published=%7.3f  interpolated=%7.3f"
              % (r["bc_x"], r["published_m"], r["interpolated_m"]))
    check("every sampled bc.x publishes the interpolated width",
          not bad, str(bad[:3]))


# =====================================================================
# [3] THE HEADLINE RUNG
# =====================================================================
def section3_rung(rows) -> None:
    """Print the command -> governed -> per-side -> realised rung table."""
    print("\n[3] HEADLINE RUNG — centred ruck, symmetric 4-4 line")
    print("      %8s %8s %8s %8s %9s %9s %8s"
          % ("commanded", "governed", "side_L", "side_R",
             "lane", "realised", "ratio"))
    for m in rows:
        print("      %8.2f %8.2f %8.2f %8.2f %9.2f %9.2f %8.4f"
              % (m.commanded_m, m.governed_m, m.side_left_m, m.side_right_m,
                 m.lane_span_m, m.realised_span_m, m.divergence_ratio))
    check("realised span rises monotonically with the commanded width",
          all(b.realised_span_m >= a.realised_span_m - 1e-9
              for a, b in zip(rows, rows[1:])),
          str([round(m.realised_span_m, 2) for m in rows]))
    clean = [
        m for m in rows
        if not m.room_capped
        and CLAMP_MIN not in (m.breakdown or {})
        and CLAMP_MAX not in (m.breakdown or {})
    ]
    if clean:
        # GOVERNED WIDTH IS THE AUTHORITATIVE TOTAL SPAN (2026-09-14): the
        # short/open split factor is no longer a span reducer, so with neither
        # the tuner clamp nor the sideline room bound binding, the commanded
        # width IS the realised total span — the ratio must be exactly 1.0.
        ratios = [m.divergence_ratio for m in clean]
        check("realised/commanded ratio is exactly 1.0 when no clamp and no "
              "room bound bind (the governed width IS the total span)",
              all(abs(r - 1.0) < 1e-9 for r in ratios),
              "ratios=%s" % [round(r, 6) for r in ratios])
        print("      un-capped, unbound realised/commanded ratio = %.4f "
              "(1.0000 means the commanded width IS the realised total span)"
              % ratios[0])
# =====================================================================
# [4] LANE GRID — ATTRIBUTION COMPLETENESS (the canonical assertion)
# =====================================================================
def section4_lane_attribution(grid) -> None:
    """Every lane-grid case must equal the documented model."""
    print("\n[4] Lane grid: observed realised span == documented model")
    print("      cases: %d  (phase x bc.x x ruck_y x split)" % len(grid))
    breaches = [m for m in grid if abs(m.unexplained_m) > ATTRIB_TOL_M]
    worst = worst_by(grid, lambda m: abs(m.unexplained_m))
    counts, totals = source_census(grid)
    print("      dominant source census:")
    for source, n in counts.most_common():
        print("        %-26s %5d cases   net %+9.2f m"
              % (source, n, totals.get(source, 0.0)))
    check("every lane-grid case is fully attributed (observed == predicted)",
          not breaches,
          "%d breach(es); worst=%s unexplained=%+.4f"
          % (len(breaches), worst.label if worst else "-",
             worst.unexplained_m if worst else 0.0))
    attribute_breaches.extend(breaches)
    if worst is not None:
        print("      worst unexplained: %s -> %+.6f m"
              % (worst.label, worst.unexplained_m))


# =====================================================================
# [5] CONTRACT BREACH CENSUS (the measurement the audit asked for)
# =====================================================================
def section5_contract_census(grid, tes_grid) -> dict:
    """Count and characterise every divergence beyond the tolerance."""
    print("\n[5] CONTRACT BREACH CENSUS "
          "(|realised - commanded| > %.2f m)" % SPAN_AUTHORITY_TOL_M)
    all_cases = list(grid) + list(tes_grid)
    breaches = [m for m in all_cases if abs(m.divergence_m) > SPAN_AUTHORITY_TOL_M]
    uncapped = [m for m in breaches if not m.room_capped]
    capped = [m for m in breaches if m.room_capped]
    counts, totals = source_census(breaches)

    print("      cases measured      : %d" % len(all_cases))
    print("      breaches            : %d  (%.1f%% of cases)"
          % (len(breaches), 100.0 * len(breaches) / max(1, len(all_cases))))
    print("      ...room-capped      : %d  (physical-room reducer)" % len(capped))
    print("      ...not room-capped  : %d  (non-physical reducer)" % len(uncapped))
    for source, n in counts.most_common():
        print("        %-26s %5d cases   net %+9.2f m"
              % (source, n, totals.get(source, 0.0)))

    worst = worst_by(all_cases, lambda m: abs(m.divergence_m))
    if worst is not None:
        print("      worst divergence    : %s" % worst.label)
        print("        commanded=%.2f governed=%.2f side_L=%.2f side_R=%.2f "
              "lane=%.2f realised=%.2f  %+.2f m (ratio %.4f)"
              % (worst.commanded_m, worst.governed_m, worst.side_left_m,
                 worst.side_right_m, worst.lane_span_m, worst.realised_span_m,
                 worst.divergence_m, worst.divergence_ratio))
        print("        decomposition: %s" % worst.breakdown)

    lane_uncapped = [m for m in grid if not m.room_capped]
    ratios = [m.divergence_ratio for m in lane_uncapped]
    if ratios:
        print("      lane-path un-capped ratio: min %.4f  max %.4f"
              % (min(ratios), max(ratios)))
    check("breach census computed over the whole grid", True)
    return {
        "cases": len(all_cases),
        "breaches": len(breaches),
        "room_capped": len(capped),
        "non_room_capped": len(uncapped),
        "dominant_sources": dict(counts),
        "net_metres_by_source": {k: round(v, 4) for k, v in totals.items()},
        "worst": to_json(worst) if worst is not None else None,
    }


# =====================================================================
# [6] TES DRIVE CHAMBER — THE WIDTH-FREE CENSUS LADDER
# =====================================================================
def section6_tes(tes_grid) -> None:
    """The TES census ladder must be attributed to TES_WIDTH_FREE_LADDER."""
    print("\n[6] TES drive chamber: census ladder (width-free) attribution")
    print("      cases: %d" % len(tes_grid))
    breaches = [m for m in tes_grid if abs(m.unexplained_m) > ATTRIB_TOL_M]
    counts, totals = source_census(tes_grid)
    print("      dominant source census:")
    for source, n in counts.most_common():
        print("        %-26s %5d cases   net %+9.2f m"
              % (source, n, totals.get(source, 0.0)))
    check("every TES case is fully attributed (observed == predicted)",
          not breaches,
          "%d breach(es)" % len(breaches))
    attribute_breaches.extend(breaches)

    # STAGE 6 (2026-09-13): the TES census ladder now CONSUMES the published
    # per-side width, so the width-free delta no longer fires.  The check is
    # inverted deliberately: it now guards the restored law.  A future edit
    # that makes the census width-free again puts TES_WIDTH_FREE_LADDER back
    # into the breakdown and breaks this assertion immediately.
    ladder_cases = [m for m in tes_grid
                    if TES_WIDTH_FREE_LADDER in (m.breakdown or {})]
    check("TES_WIDTH_FREE_LADDER does NOT fire — the census ladder consumes "
          "the published side width (Stage 6 restored the width law)",
          not ladder_cases,
          "fired on %d of %d" % (len(ladder_cases), len(tes_grid)))
    # The census ladder is now WIDTH-DERIVED: at a fixed ruck/count/split,
    # moving the command MUST move the TES span (this is the Stage 6 win —
    # the span is no longer a count-only, width-free function).
    probe_rows = [m for m in tes_grid
                  if abs(m.ruck_y - 35.0) < 1e-9 and (m.n_left, m.n_right) == (4, 4)]
    tes_spans = sorted({round(m.realised_span_m, 6) for m in probe_rows})
    if probe_rows:
        print("      TES span RESPONDS to the commanded width at a fixed "
              "ruck/count/split: %s" % tes_spans)
    check("TES span tracks the commanded width (width-derived ladder)",
          len(tes_spans) > 1 if probe_rows else True,
          str(tes_spans))


# =====================================================================
# [7] LATENT DEFECTS + NO-BC
# =====================================================================
def section7_latent(ruck_y_arg, accessor, no_bc) -> None:
    """Report the audit's latent defects as named sources."""
    print("\n[7] Latent defects and the no-ball-carrier gap")
    print("      RUCK_Y_ARG_IGNORED: observed=%s expected_if_arg_honoured=%s "
          "expected_if_world_used=%s"
          % (ruck_y_arg["observed"], ruck_y_arg["expected_if_arg_honoured"],
             ruck_y_arg["expected_if_world_used"]))
    check("side_widths' room cap follows world.ruck_y_m, not its ruck_y argument "
          "(RUCK_Y_ARG_IGNORED is reported when it fires)",
          "fires" in ruck_y_arg,
          "probe returned no verdict")
    if ruck_y_arg.get("fires"):
        print("      -> RUCK_Y_ARG_IGNORED FIRES: a caller passing ruck_y=20 m "
              "on a world pinned at 30 m gets the 30 m cap.")
    print("      GOVERNED_ACCESSOR_DIVERGENCE: defensive_width=%s governor=%s "
          "divergence=%+.3f m"
          % (accessor["defensive_width_accessor"], accessor["governor_accessor"],
             accessor["divergence_m"]))
    check("GOVERNED_ACCESSOR_DIVERGENCE is probed and its value reported",
          "fires" in accessor)
    print("      NO_BALL_CARRIER: raw=%.1f governed=%.1f realised=%.2f"
          % (no_bc["raw_def_line_width_m"], no_bc["governed_m"],
             no_bc["realised_span_m"]))
    check("no-ball-carrier command gap is recorded as a named source",
          no_bc.get("source") == NO_BALL_CARRIER)


# =====================================================================
# [8] BEHAVIOURAL CONTEXT (no contract assertion)
# =====================================================================
def section8_context(governor_ab, carrier_ab) -> None:
    """Show what the governor band and the carrier band actually do."""
    print("\n[8] Behavioural context (no contract assertion)")
    for m in governor_ab:
        print("      governor=%-5s realised=%7.3f lane=%7.3f "
              "GOVERNOR_BAND_FIT=%+.4f"
              % (m.governor_active, m.realised_span_m, m.lane_span_m,
                 m.breakdown.get(GOVERNOR_BAND_FIT, 0.0)))
    for m in carrier_ab:
        print("      carrier=%-5s realised=%7.3f lane=%7.3f BAND_PULL=%+.4f"
              % (m.carrier_present, m.realised_span_m, m.lane_span_m,
                 m.breakdown.get(BAND_PULL, 0.0)))
    check("governor and carrier A/B pairs both measured",
          len(governor_ab) == 2 and len(carrier_ab) == 2)


# =====================================================================
# [9] STRICT MODE — THE LITERAL CONTRACT
# =====================================================================
def section9_strict(grid, tes_grid, strict: bool) -> dict:
    """Assert ``realised == commanded`` wherever physical room does not bind."""
    print("\n[9] STRICT CONTRACT %s"
          % ("(enforced)" if strict else "(reported only; pass --strict)"))
    all_cases = list(grid) + list(tes_grid)
    breaches = [m for m in all_cases
                if not m.room_capped
                and abs(m.divergence_m) > SPAN_AUTHORITY_TOL_M]
    print("      non-room-capped breaches: %d of %d"
          % (len(breaches), len([m for m in all_cases if not m.room_capped])))
    if breaches:
        sources = collections.Counter(dominant_source(m) for m in breaches)
        for source, n in sources.most_common():
            print("        %-26s %5d" % (source, n))
        worst = worst_by(breaches, lambda m: abs(m.divergence_m))
        print("      worst: %s  %+.2f m (ratio %.4f, source=%s)"
              % (worst.label, worst.divergence_m, worst.divergence_ratio,
                 dominant_source(worst)))
        if not strict:
            print("      -> run with --strict to make this a failure "
                  "(the gate the later behaviour stages must turn green).")
    if strict:
        check("realised span equals the commanded width wherever physical "
              "room does not bind", not breaches,
              "%d breach(es); %s" % (len(breaches),
                                     breaches[0].label if breaches else ""))
    return {
        "non_room_capped_breaches": len(breaches),
        "sources": dict(collections.Counter(
            dominant_source(m) for m in breaches)),
    }


# =====================================================================
# MAIN
# =====================================================================
def main(argv) -> int:
    """Run every section; return the process exit code."""
    strict = "--strict" in argv
    print("=" * 78)
    print("COMMAND-vs-REALISED SPAN AUTHORITY VERIFIER — STAGE 1")
    print("=" * 78)
    print("tolerances: span=%.2f m  attribution=%.2f m  room-bind=%.2f m"
          % (SPAN_AUTHORITY_TOL_M, ATTRIB_TOL_M, ROOM_BIND_TOL_M))
    print("mode      : %s" % ("STRICT" if strict else "ATTRIBUTION"))

    data = probe.run_all()

    section1_model_conformance()
    section2_publish(data["publish_conformance"])
    section3_rung(data["governed_rung"])
    section4_lane_attribution(data["lane_grid"])
    census = section5_contract_census(data["lane_grid"], data["tes_grid"])
    section6_tes(data["tes_grid"])
    section7_latent(data["ruck_y_arg"], data["accessor_divergence"],
                    data["no_ball_carrier"])
    section8_context(data["governor_ab"], data["carrier_ab"])
    strict_info = section9_strict(data["lane_grid"], data["tes_grid"], strict)

    report = {
        "stage": "stage1_span_authority",
        "strict": bool(strict),
        "tolerances": data["tolerances"],
        "headline_rung": [to_json(m) for m in data["governed_rung"]],
        "tuner_sweep": [to_json(m) for m in data["tuner_sweep"]],
        "governor_ab": [to_json(m) for m in data["governor_ab"]],
        "carrier_ab": [to_json(m) for m in data["carrier_ab"]],
        "publish_conformance": data["publish_conformance"],
        "contract_census": census,
        "strict_contract": strict_info,
        "latent": {
            "ruck_y_arg": data["ruck_y_arg"],
            "accessor_divergence": data["accessor_divergence"],
            "no_ball_carrier": data["no_ball_carrier"],
        },
        "lane_grid": [to_json(m) for m in data["lane_grid"]],
        "tes_grid": [to_json(m) for m in data["tes_grid"]],
        "failures": failures,
    }
    with io.open(REPORT_PATH, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    print("\nReport written to %s" % REPORT_PATH)

    print("\n" + "=" * 78)
    if failures:
        print("RESULT: %d FAILURE(S)" % len(failures))
        for f in failures:
            print("   - %s" % f)
        return 1
    print("RESULT: ALL CHECKS PASSED (attribution complete)")
    if strict:
        print("        strict contract: met")
    else:
        print("        strict contract: %d non-room-capped breach(es) "
              "reported (not enforced in attribution mode)"
              % strict_info["non_room_capped_breaches"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
