"""
verify_line_shape_tuners.py — SINGLE-FILE LINE-SHAPE TUNER CONTRACT.

WHAT THIS PINS
--------------
Every defensive LINE WIDTH and COMPRESSION control lives in ONE module:

    game/defence/defence_lvl_1/defensive_line_shape_tuners.py

and the canonical tuner object simply INHERITS it:

    class DefenceTuners(DefensiveLineShapeTuners): ...

The point of the consolidation is that a future width or compression
adjustment is a SINGLE-FILE edit with no cross-module tracing, while the
authority chains (``interpolated_defensive_width`` -> governor publication ->
geometry consumers) stay exactly where they were and behaviour stays
byte-identical until a tuner value is deliberately changed.

The verifier is PURE and HEADLESS.  It never boots the engine.

Sections:
  [1] ONE HOME — the dedicated module exists, is importable, and the
      canonical ``DefenceTuners`` is a genuine subclass of the base class
      that owns the line-shape tuners.
  [2] NO LEAK-BACK — ``defensive_tuners.py`` no longer DECLARES any of the
      moved line-shape tuners (its source assigns none of them), so there is
      exactly one declaration site for every knob.
  [3] REGISTRY COMPLETE — every name in ``LINE_SHAPE_TUNER_NAMES`` exists on
      the live instance, and the moved width/compression members are present
      with their documented values.
  [4] WIDTH AUTHORITY CHAIN INTACT — the four-anchor curve, the ONE
      defending-tryline region, the two goal-line compression knobs and the
      ``width_defending_tryline_m`` alias all still resolve through the same
      readers, and the region alias cannot diverge from
      ``width_right_tryline_m``.
  [5] TASK-NAMED ALIASES — ``goal_line_compression_10m`` and
      ``goal_line_compression_tryline`` are present and point at the SAME
      storage as ``defending_10m_compression`` / ``defending_tryline_compression``
      (aliases, not renamed knobs — the verifiers that read the canonical
      names keep working).

Emits verify_line_shape_tuners_report.json; exits 0 when all checks pass.

Run:  python verify_line_shape_tuners.py
"""

from __future__ import annotations

import ast
import io
import json
import re
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

TUNERS_PATH = "game/defence/defence_lvl_1/defensive_tuners.py"
SHAPE_TUNERS_PATH = (
    "game/defence/defence_lvl_1/defensive_line_shape_tuners.py"
)

failures: list[str] = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(name)
    return bool(condition)


def read_src(path: str) -> str:
    with io.open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


# ── 1. ONE HOME ──────────────────────────────────────────────────────
def section1():
    print("\n[1] ONE HOME — the dedicated line-shape module")
    from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
    from game.defence.defence_lvl_1 import defensive_line_shape_tuners as ls

    check("the dedicated module exists and imports",
          ls.__name__ == "game.defence.defence_lvl_1."
                           "defensive_line_shape_tuners")
    check("the base class is declared there",
          hasattr(ls, "DefensiveLineShapeTuners"))
    check("the tuner registry is declared there",
          hasattr(ls, "LINE_SHAPE_TUNER_NAMES")
          and len(ls.LINE_SHAPE_TUNER_NAMES) > 0,
          f"names={len(getattr(ls, 'LINE_SHAPE_TUNER_NAMES', []))}")

    base = ls.DefensiveLineShapeTuners
    check("the canonical tuner object is a genuine SUBCLASS "
          "(inheritance, not a copy)",
          issubclass(DefenceTuners, base) and DefenceTuners is not base)

    # The base class must OWN the declarations: instantiating it alone must
    # produce a live width/compression surface.
    solo = base()
    check("the base class alone carries the width curve",
          all(hasattr(solo, n) for n in
              ("width_left_tryline_m", "width_halfway_m",
               "width_right_tryline_m")))
    check("the base class alone carries the compression knobs",
          all(hasattr(solo, n) for n in
              ("defending_10m_compression",
               "defending_tryline_compression",
               "compression_trigger_m")))

    # The canonical object must present the identical surface.
    t = DefenceTuners()
    missing = [n for n in ls.LINE_SHAPE_TUNER_NAMES if not hasattr(t, n)]
    check("every registered name is present on the canonical object",
          not missing, f"missing={missing}")


# ── 2. NO LEAK-BACK ──────────────────────────────────────────────────
_MOVED_STORES = (
    "width_left_tryline_m",
    "width_halfway_m",
    "width_right_tryline_m",
    "width_at_own_tryline_m",
    "width_at_halfway_m",
    "width_at_opp_tryline_m",
    "defending_10m_compression",
    "defending_tryline_compression",
    "compression_trigger_m",
    "compression_smooth_rate",
    "compression_band_width_m",
    "compression_strength",
    "step_up_trigger_m",
    "step_up_rate",
    "step_down_rate",
    "role_width_anchor",
    "width_curve_factor",
    "field_width_m",
    "field_length_m",
    "sideline_margin_m",
)


def _stored_attribute_names(src: str) -> set:
    """Every ``self.<name> = ...`` target name in *src* (AST stores)."""
    tree = ast.parse(src)
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for tgt in node.targets:
            if (isinstance(tgt, ast.Attribute)
                    and isinstance(tgt.value, ast.Name)
                    and tgt.value.id == "self"):
                names.add(tgt.attr)
        # AnnAssign: self.x: float = 1.0
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            tgt = node.target
            if (isinstance(tgt, ast.Attribute)
                    and isinstance(tgt.value, ast.Name)
                    and tgt.value.id == "self"):
                names.add(tgt.attr)
    return names


def section2():
    print("\n[2] NO LEAK-BACK — one declaration site per line-shape tuner")

    shape_src = read_src(SHAPE_TUNERS_PATH)
    tuners_src = read_src(TUNERS_PATH)

    shape_stores = _stored_attribute_names(shape_src)
    tuners_stores = _stored_attribute_names(tuners_src)

    # The dedicated module must declare the moved members.
    absent_in_shape = [n for n in _MOVED_STORES if n not in shape_stores]
    check("the dedicated module declares the moved line-shape members",
          not absent_in_shape, f"absent={absent_in_shape}")

    # The inheriting module must NOT declare any of them any more.
    leaked = sorted(n for n in _MOVED_STORES if n in tuners_stores)
    check("defensive_tuners.py no longer DECLARES any moved line-shape tuner",
          not leaked, f"leaked={leaked}")

    # The inheriting module must still point at the new home (discoverability).
    check("defensive_tuners.py points at the new single source of truth",
          "defensive_line_shape_tuners" in tuners_src)
    check("defensive_tuners.py imports the base class",
          "from game.defence.defence_lvl_1.defensive_line_shape_tuners "
          "import" in tuners_src)


# ── 3. REGISTRY + DOCUMENTED VALUES ──────────────────────────────────
_DOCUMENTED = {
    "field_width_m": 70.0,
    "field_length_m": 100.0,
    "sideline_margin_m": 3.0,
    "width_left_tryline_m": 30.0,
    "width_halfway_m": 45.0,
    "width_right_tryline_m": 60.0,
    "defending_10m_compression": 0.95,
    "defending_tryline_compression": 1.0,
}


def section3():
    print("\n[3] REGISTRY + documented values")
    from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
    from game.defence.defence_lvl_1.defensive_line_shape_tuners import (
        LINE_SHAPE_TUNER_NAMES,
    )

    t = DefenceTuners()

    check("the registry has no duplicate names",
          len(set(LINE_SHAPE_TUNER_NAMES)) == len(LINE_SHAPE_TUNER_NAMES),
          f"count={len(LINE_SHAPE_TUNER_NAMES)}")

    bad = []
    for name, want in _DOCUMENTED.items():
        if not hasattr(t, name):
            bad.append(f"{name}: MISSING")
            continue
        got = getattr(t, name)
        if isinstance(want, float):
            if abs(float(got) - want) > 1e-9:
                bad.append(f"{name}: got {got!r} want {want!r}")
        elif got != want:
            bad.append(f"{name}: got {got!r} want {want!r}")
    check("documented line-shape values are unchanged by the relocation",
          not bad, "; ".join(bad))

    # Tables that live with the line shape.
    for table in ("WIDTH_OFFSETS", "ROLE_WIDTH_OFFSETS", "ROLE_DEPTH_M"):
        check(f"the {table} table lives with the line shape",
              hasattr(t, table) and bool(getattr(t, table)))


# ── 4. WIDTH AUTHORITY CHAIN ─────────────────────────────────────────
def section4():
    print("\n[4] WIDTH AUTHORITY CHAIN intact")
    from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
    from game.defence.defence_lvl_1.defensive_width import (
        interpolated_defensive_width,
        attacking_tryline_anchor_width_m,
        halfway_anchor_width_m,
        defending_10m_anchor_width_m,
        defending_tryline_anchor_width_m,
        defending_region_width_m,
    )

    t = DefenceTuners()

    # The curve is still the single width authority, interpolated by bc.x.
    for bc_x in (10.0, 60.0, 100.0, 110.0):
        w = interpolated_defensive_width(t, bc_x)
        check(f"interpolated_defensive_width(t, bc.x={bc_x:.0f}) resolves",
              isinstance(w, float) and w > 0.0, f"width={w:.4f}")

    anchors = (
        attacking_tryline_anchor_width_m(t),
        halfway_anchor_width_m(t),
        defending_10m_anchor_width_m(t),
        defending_tryline_anchor_width_m(t),
    )
    check("the four anchors remain monotonically ordered",
          anchors[0] <= anchors[1] <= anchors[2] <= anchors[3],
          " ".join(f"{a:.2f}" for a in anchors))

    region = defending_region_width_m(t)
    check("the ONE defending-tryline region is the tryline anchor",
          abs(region - anchors[3]) < 1e-9,
          f"region={region:.4f} tryline={anchors[3]:.4f}")
    check("the region is the width_right_tryline_m value (no divergence)",
          abs(region - t.width_right_tryline_m) < 1e-9)
    check("the 10 m anchor is a COMPRESSION of the region "
          "(no separate 10 m width tuner)",
          not hasattr(t, "width_defending_10m_m")
          and abs(anchors[2] - region * t.defending_10m_compression) < 1e-9)

    # The goal-line knobs move ONLY their own anchor.
    t2 = DefenceTuners()
    base10 = defending_10m_anchor_width_m(t2)
    basetry = defending_tryline_anchor_width_m(t2)
    t2.defending_10m_compression = 0.70
    check("the 10 m knob moves ONLY the 10 m anchor",
          abs(defending_10m_anchor_width_m(t2) - basetry * 0.70) < 1e-9
          and abs(defending_tryline_anchor_width_m(t2) - basetry) < 1e-9)
    # RESTORE the 10 m knob to its shipped neutral BEFORE moving the tryline
    # knob, so any change to the 10 m anchor below could only come from a
    # leakage between the two knobs.
    t2.defending_10m_compression = 0.95
    t2.defending_tryline_compression = 0.70
    check("the tryline knob moves ONLY the tryline anchor",
          abs(defending_tryline_anchor_width_m(t2) - basetry * 0.70) < 1e-9
          and abs(defending_10m_anchor_width_m(t2) - base10) < 1e-9)


# ── 5. TASK-NAMED ALIASES ────────────────────────────────────────────
def section5():
    print("\n[5] Task-named aliases (goal-line compression)")
    from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners

    t = DefenceTuners()

    check("goal_line_compression_10m exists",
          hasattr(t, "goal_line_compression_10m"))
    check("goal_line_compression_tryline exists",
          hasattr(t, "goal_line_compression_tryline"))

    check("goal_line_compression_10m reads the canonical 10 m knob",
          abs(float(t.goal_line_compression_10m)
              - float(t.defending_10m_compression)) < 1e-12)

    # Aliases must be LIVE, not a one-time copy: writing one must move the
    # other (the whole point of an alias is that the names cannot diverge).
    t.defending_10m_compression = 0.83
    check("writing the canonical 10 m knob moves the alias",
          abs(float(t.goal_line_compression_10m) - 0.83) < 1e-12,
          f"alias={t.goal_line_compression_10m}")
    t.goal_line_compression_10m = 0.91
    check("writing the alias moves the canonical 10 m knob",
          abs(float(t.defending_10m_compression) - 0.91) < 1e-12,
          f"canonical={t.defending_10m_compression}")

    t.defending_tryline_compression = 0.77
    check("writing the canonical tryline knob moves the alias",
          abs(float(t.goal_line_compression_tryline) - 0.77) < 1e-12)
    t.goal_line_compression_tryline = 0.88
    check("writing the alias moves the canonical tryline knob",
          abs(float(t.defending_tryline_compression) - 0.88) < 1e-12)

    # width_defending_tryline_m is the ONE goal-line width region alias.
    t.width_defending_tryline_m = 57.5
    check("writing width_defending_tryline_m sets width_right_tryline_m",
          abs(float(t.width_right_tryline_m) - 57.5) < 1e-12)
    check("reading width_defending_tryline_m reflects the region",
          abs(float(t.width_defending_tryline_m) - 57.5) < 1e-12)


def main():
    print("=" * 74)
    print("SINGLE-FILE LINE-SHAPE TUNER CONTRACT")
    print("(all defensive width / compression controls in ONE module)")
    print("=" * 74)

    section1()
    section2()
    section3()
    section4()
    section5()

    report = {
        "line_shape_tuners": {
            "single_module": SHAPE_TUNERS_PATH,
            "inherited_by": TUNERS_PATH,
            "all_checks_passed": not failures,
            "failures": failures,
        }
    }
    with io.open("verify_line_shape_tuners_report.json", "w",
                 encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    print("\nReport written to verify_line_shape_tuners_report.json")

    print("\n" + "=" * 74)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        return 1
    print("RESULT: PASS — every defence line-shape tuner lives in ONE file, "
          "the canonical object inherits it,")
    print("        and the width/compression authority chain resolves "
          "unchanged.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
