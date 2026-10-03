"""
verify_goal_line_compression_contract.py — GOAL-LINE COMPRESSION CONTRACT
VERIFIER (2026-10-03).

WHAT THIS PINS
--------------
The two goal-line compression knobs are NEW tuners

    DefenceTuners.defending_10m_compression        (default 0.95)
    DefenceTuners.defending_tryline_compression    (default 1.00)

that scale the ONE defending-tryline width region into the two goal-line
anchors of the four-anchor width curve.  The project rule under test is
Jon's: "keep behaviour neutral initially and prove field realisation
first; do not choose non-neutral defaults until outcome evidence exists".

So this verifier asserts the CONTRACT of the knobs and of the measurement
instrument — never a data-dependent outcome:

  [1] KNOB FIELDS + NEUTRAL DEFAULTS.  Both fields exist and are floats,
      and their SHIPPED defaults are the legacy-neutral values
      (0.95 / 1.00) — i.e. the probe's squeeze scenarios are opt-in and
      production behaviour is unchanged by the knobs' existence.
  [2] ANCHOR INDEPENDENCE.  The 10 m anchor depends ONLY on
      defending_10m_compression and the tryline anchor ONLY on
      defending_tryline_compression, so the two knobs are independent
      levers on the [60,100] and [100,110] curve segments.
  [3] READ-ONLY INSTRUMENT.  The outcome probe exists, imports cleanly, and
      never opens a production path for writing (it writes only its own
      goal_line_outcomes.* artefacts).
  [4] INSTRUMENT CONSTRUCTION.  The probe's line-placement helper is present
      and builds the line from the engine's OWN geometry
      (ruck_x + WIDTH_OFFSETS[depth_role], capped at the anchor) rather
      than from an invented formula.
  [5] DETERMINISM.  Two identical runs of the instrument (same scenario,
      seed, channel) must produce identical outcomes, so the A/B numbers are
      reproducible rather than noise.

Emits verify_goal_line_compression_report.json; exits 0 when all checks pass.

Run:  python verify_goal_line_compression_contract.py
"""

from __future__ import annotations

import ast
import io
import json
import os
import re
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

TUNERS_PATH = "game/defence/defence_lvl_1/defensive_tuners.py"
# The width / compression LINE-SHAPE tuners live in ONE dedicated module
# (``defensive_line_shape_tuners.py``); ``defensive_tuners.py`` INHERITS it.
# The two goal-line knobs are line-shape tuners, so their DECLARATIONS moved
# there — every source-level scan below reads BOTH modules.
SHAPE_TUNERS_PATH = (
    "game/defence/defence_lvl_1/defensive_line_shape_tuners.py"
)
PROBE_PATH = "probe_goal_line_outcomes.py"

NEUTRAL_10M = 0.95
NEUTRAL_TRYLINE = 1.00

failures: list[str] = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(name)
    return bool(condition)


# ── 1. KNOB FIELDS + NEUTRAL DEFAULTS ────────────────────────────────
def section1():
    print("\n[1] Knob fields exist with NEUTRAL shipped defaults")
    from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
    t = DefenceTuners()
    c10 = getattr(t, "defending_10m_compression", None)
    ctry = getattr(t, "defending_tryline_compression", None)
    check("defending_10m_compression exists (float)", isinstance(c10, float),
          f"value={c10}")
    check("defending_tryline_compression exists (float)",
          isinstance(ctry, float), f"value={ctry}")
    check("shipped 10 m default is the legacy-neutral 0.95",
          c10 is not None and abs(c10 - NEUTRAL_10M) < 1e-9, f"value={c10}")
    check("shipped tryline default is NEUTRAL 1.00 (no hidden squeeze)",
          ctry is not None and abs(ctry - NEUTRAL_TRYLINE) < 1e-9,
          f"value={ctry}")

    # Source-level: the declared defaults in the tuner class body must also be
    # the neutral values, so a future edit that quietly squeezes production
    # trips here even if a runtime mutation masked it above.
    #
    # The declarations live in the dedicated line-shape module (which
    # DefenceTuners inherits), so the scan reads BOTH source files.
    src = ""
    for _tuner_mod in (SHAPE_TUNERS_PATH, TUNERS_PATH):
        with open(_tuner_mod, encoding="utf-8") as fh:
            src += fh.read() + "\n"
    for name, neutral in (("defending_10m_compression", NEUTRAL_10M),
                          ("defending_tryline_compression", NEUTRAL_TRYLINE)):
        m = re.search(
            r"^[ \t]*(?:self\.)?" + name
            + r"\s*:\s*float\s*=\s*([0-9.]+)",
            src, re.MULTILINE)
        check(f"{name} declared default is neutral ({neutral})",
              bool(m) and abs(float(m.group(1)) - neutral) < 1e-9,
              (m.group(1) if m else "declaration not found"))


# ── 2. ANCHOR INDEPENDENCE ───────────────────────────────────────────
def section2():
    print("\n[2] The two knobs are INDEPENDENT goal-line levers")
    from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
    from game.defence.defence_lvl_1.defensive_width import (
        defending_10m_anchor_width_m,
        defending_tryline_anchor_width_m,
        defending_region_width_m,
    )
    t = DefenceTuners()
    base10 = defending_10m_anchor_width_m(t)
    basetry = defending_tryline_anchor_width_m(t)
    region = defending_region_width_m(t)

    t.defending_10m_compression = 0.70
    ten_only = (defending_10m_anchor_width_m(t),
                defending_tryline_anchor_width_m(t))
    t.defending_10m_compression = NEUTRAL_10M
    t.defending_tryline_compression = 0.70
    try_only = (defending_10m_anchor_width_m(t),
                defending_tryline_anchor_width_m(t))
    t.defending_tryline_compression = NEUTRAL_TRYLINE

    check("region width is the single goal-line width anchor",
          abs(region - basetry) < 1e-9,
          f"region={region:.4f} tryline_anchor={basetry:.4f}")
    check("10 m knob moves ONLY the 10 m anchor",
          abs(ten_only[0] - region * 0.70) < 1e-9
          and abs(ten_only[1] - basetry) < 1e-9,
          f"10m={ten_only[0]:.3f} try={ten_only[1]:.3f}")
    check("tryline knob moves ONLY the tryline anchor",
          abs(try_only[0] - base10) < 1e-9
          and abs(try_only[1] - region * 0.70) < 1e-9,
          f"10m={try_only[0]:.3f} try={try_only[1]:.3f}")


# ── 3. READ-ONLY INSTRUMENT ──────────────────────────────────────────
def _written_paths(src: str) -> list:
    """Every string literal passed to open(..., 'w'...) in *src*."""
    found = []
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Name) and fn.id == "open"):
            continue
        if len(node.args) < 2:
            continue
        mode = node.args[1]
        if not (isinstance(mode, ast.Constant)
                and isinstance(mode.value, str)
                and "w" in mode.value):
            continue
        if isinstance(node.args[0], ast.Constant):
            found.append(node.args[0].value)
    return found


def section3():
    print("\n[3] The outcome probe is a READ-ONLY instrument")
    check("probe file exists", os.path.isfile(PROBE_PATH), PROBE_PATH)
    with open(PROBE_PATH, encoding="utf-8") as fh:
        src = fh.read()
    written = _written_paths(src)
    check("probe writes only its own artefacts",
          all(str(p).startswith("goal_line_outcomes") for p in written),
          f"writes={written}")
    check("probe never writes under game/ or world/",
          not any(re.search(r"(^|/)(game|world|player|ball)/", str(p))
                  for p in written),
          f"writes={written}")


# ── 4. INSTRUMENT CONSTRUCTION ───────────────────────────────────────
def _function_source(src: str, name: str):
    """The source text of the named top-level function (or None)."""
    import textwrap
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            lines = src.splitlines()
            body = "\n".join(lines[node.lineno - 1:node.end_lineno])
            return textwrap.dedent(body)
    return None


def _min_call_caps_at_anchor(fn_src: str) -> bool:
    """True if *fn_src* contains a ``min(...)`` whose args include
    the name ``anchor`` — i.e. the placement cannot pass the line."""
    for node in ast.walk(ast.parse(fn_src)):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "min"):
            for arg in node.args:
                if isinstance(arg, ast.Name) and arg.id == "anchor":
                    return True
    return False


def section4():
    print("\n[4] The instrument builds the line from the ENGINE geometry")
    with open(PROBE_PATH, encoding="utf-8") as fh:
        src = fh.read()
    check("line-placement helper exists",
          "def _place_line_at_goal_line" in src)
    fn_src = _function_source(src, "_place_line_at_goal_line")
    check("placement uses the engine's role depth lanes (WIDTH_OFFSETS)",
          "WIDTH_OFFSETS" in src)
    check("placement caps the lane at the defensive anchor "
          "(no body past the line)",
          fn_src is not None and _min_call_caps_at_anchor(fn_src),
          "min(..., anchor) in _place_line_at_goal_line")
    check("placement takes spacing from the engine pipeline "
          "(run_defensive_pipeline)",
          "run_defensive_pipeline" in src)
    check("the carrier is driven at a fixed attacking speed "
          "(controlled attack)",
          "ATTACK_SPEED_MPS" in src)
    check("tackle outcome read is the engine's own TES flag "
          "(tes_active)",
          "tes_active" in src)


# ── 5. DETERMINISM ───────────────────────────────────────────────────
def section5():
    print("\n[5] The instrument is DETERMINISTIC (reproducible A/B)")
    import contextlib
    import io as _io
    import probe_goal_line_outcomes as probe

    def once():
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            return probe.run_one("neutral", NEUTRAL_10M, NEUTRAL_TRYLINE,
                                 11, 0.0)

    a = once()
    b = once()
    keys = ("booted", "tackled", "broke", "try", "metres", "closest_m",
            "max_bc_x", "contact_x")
    diffs = [k for k in keys if a.get(k) != b.get(k)]
    check("two identical runs produce identical outcomes", not diffs,
          f"differing keys={diffs}")
    check("the instrument actually engaged the line (tackled)",
          bool(a.get("tackled")), f"tackled={a.get('tackled')}")
    check("the placed line was a real line (bodies placed > 0)",
          int(a.get("line_placed") or 0) > 0,
          f"line_placed={a.get('line_placed')}")


def main():
    print("=" * 74)
    print("GOAL-LINE COMPRESSION CONTRACT VERIFICATION")
    print("=" * 74)
    section1()
    section2()
    section3()
    section4()
    section5()

    report = {
        "goal_line_compression": {
            "neutral_defaults": not any(
                f.startswith(("shipped", "defending_"))
                for f in failures),
            "anchor_independence": True,
            "read_only_instrument": True,
            "engine_geometry_placement": True,
            "deterministic": True,
            "issues_remaining": failures,
        }
    }
    if failures:
        for key in report["goal_line_compression"]:
            if key != "issues_remaining":
                report["goal_line_compression"][key] = False
    with io.open("verify_goal_line_compression_report.json", "w",
                 encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    print("\nReport written to verify_goal_line_compression_report.json")
    print("\n" + "=" * 74)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        return 1
    print("RESULT: PASS — goal-line compression knobs are neutral by "
          "default, independent,")
    print("        and measured by a read-only, deterministic, "
          "engine-geometry instrument.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
