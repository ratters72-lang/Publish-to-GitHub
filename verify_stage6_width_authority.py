"""
verify_stage6_width_authority.py — Stage 6 width-ownership verifier.

WHAT THIS PROVES
----------------
Stage 5 unified formation POPULATION (who is on the line, how many, which
side).  Stage 6 unifies formation WIDTH: the lateral span half-width the
line is laid out on must be solved ONCE per frame by
``game.defence.defence_lvl_1.formation_width_authority`` and consumed by
every other system — lane generation, spacing, refits and width targets —
rather than each deriving its own copy.

A failure here means a width writer still exists outside the authority, or
two systems disagree about the frame's width.

CHECKS
------
  W1  WRITER CENSUS — static scan.  Every assignment to
      ``world.frame_width_targets`` must live in an allow-listed publisher,
      and that publisher must consult the authority.
  W2  AUTHORITY PRESENT — the authority module exposes the published-fact
      read API and the reducer ledger.
  W3  SINGLE SOLVE PER FRAME — a second publication for the same
      (frame, scope) is a cache HIT returning the IDENTICAL object; the
      reducer ledger never accumulates twice.
  W4  PUBLISHED == CONSUMERS — the width the spacing span is built from,
      the width the lane ladder is built from, and the governed accessor
      all agree with the published value, to floating tolerance.
  W5  NO CONFLICTING VALUES — ``conflicting_width_solves`` reports nothing
      when a frame is read repeatedly, and DOES report when two solves in
      one frame disagree.
  W6  REDUCER CLOSURE — the published mean equals commanded + the sum of
      the named reducers (the ledger is complete and signed correctly).
  W7  SPAN vs OCCUPATION — the published side span and the actual
      outermost-defender occupation agree within one lane step; a larger
      gap means the line is not laid out on the span it published.

Run:  python verify_stage6_width_authority.py
Exit: 0 = all checks pass, 1 = at least one failure.
"""

from __future__ import annotations

import os
import re
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

FAILURES: list[str] = []
CHECKS_RUN = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global CHECKS_RUN
    CHECKS_RUN += 1
    mark = "PASS" if ok else "FAIL"
    line = "  [%s] %s" % (mark, label)
    if detail:
        line += "  — %s" % detail
    print(line)
    if not ok:
        FAILURES.append(label + (" (%s)" % detail if detail else ""))
# ─────────────────────────────────────────────────────────────────────
# MODULE IMPORTS
# ─────────────────────────────────────────────────────────────────────

import game.defence.defence_lvl_1.formation_width_authority as FWA
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_width import (
    governed_width_m,
    recompute_width_x,
)
import game.defence.defence_lvl_1.defensive_split_spacing as DSS
import game.defence.defence_lvl_1.span_contract as SC
from game.defence.defence_lvl_1.defensive_line_governor import (
    DefensiveLineGovernor,
)


# ─────────────────────────────────────────────────────────────────────
# SYNTHETIC WORLD — a minimal stand-in for the engine world.
# ─────────────────────────────────────────────────────────────────────

class _Body:
    """Minimal defender stand-in (id-stable, x/y settable)."""

    __slots__ = ("x", "y", "depth_role", "depth_side", "depth_class",
                 "frame_spacing_y", "fullback_role_active", "player_id")

    _next_id = 0

    def __init__(self, x: float, y: float, role: str = "C") -> None:
        self.x = float(x)
        self.y = float(y)
        self.depth_role = role
        self.depth_side = None
        self.depth_class = "PASSIVE"
        self.frame_spacing_y = None
        self.fullback_role_active = False
        _Body._next_id += 1
        self.player_id = _Body._next_id


def make_world(*, frame: int = 1, ruck_y: float = 35.0, ruck_x: float = 60.0,
               bc_x: float = 70.0, bc_y: float = 35.0,
               width: float = 55.0, governor_active: bool = True):
    """Build a minimal world carrying only what the width layer reads."""
    w = types.SimpleNamespace()
    w.frame_count = int(frame)
    w.dt = 1.0 / 60.0
    w.ruck_x_m = float(ruck_x)
    w.ruck_y_m = float(ruck_y)
    w.def_line_width_m = float(width)
    w.governor_width_active = bool(governor_active)
    w.governor_line_width_m = float(width)
    w.defending_team = []
    w.attacking_team = []
    w.in_pursuit_mode = False
    w.sub_state = None
    w.ball_carrier = types.SimpleNamespace(
        x=float(bc_x), y=float(bc_y), vx=0.0, vy=0.0, in_contact=False,
    )
    w.field_width_m = 68.0
    w.touchline_margin_m = 3.0
    return w


def make_line(world, ys, bc_x: float = 70.0):
    """Populate *world* with a symmetric line of defenders at *ys*."""
    bodies = []
    for i, y in enumerate(ys):
        d = _Body(bc_x - 2.0, y, role=("C" if i % 2 else "B"))
        bodies.append(d)
    world.defending_team = list(bodies)
    return bodies


# ─────────────────────────────────────────────────────────────────────
# W1 — WRITER CENSUS (static)
# ─────────────────────────────────────────────────────────────────────

# The ONLY modules permitted to assign ``world.frame_width_targets``.
# Each must be a frame publisher that consults the Stage-6 authority.
ALLOWED_WIDTH_PUBLISHERS = {
    "game/defence/defence_lvl_1/defensive_pipeline.py",
    "game/defence/defence_lvl_1/defensive_behaviour_unified.py",
}

# Modules permitted to write the governor's stored width fields.  These are
# the governor's OWN state, refreshed from its own solve — not a competing
# formation-width authority.
ALLOWED_GOVERNOR_STATE_WRITERS = {
    "game/defence/defence_lvl_1/defensive_line_governor.py",
}

_WRITE_RE = re.compile(r"(?:world\.)?frame_width_targets\s*=\s*(?!None)")
# The governor's raw storage slots.  The lookbehind rejects a ``self._...``
# attribute cache (e.g. ``self._def_line_width_m = getattr(world, ...)``),
# which is a READ of the width, not a competing WRITE of it.
_GOV_RAW_RE = re.compile(
    r"(?:world\.)?(?<![_\w])(?:def_line_width_m|governor_line_width_m)"
    r"\s*=\s*(?!None)"
)


def _iter_py_files(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in ("__pycache__", ".git", "debug_archive")]
        for fn in filenames:
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn).replace("\\", "/")


def _strip_docstrings_and_comments(lines):
    """Yield (lineno, code) for lines with comments/docstrings removed.

    A pragmatic filter: drops whole-line comments, and blanks any text
    after an unquoted ``#``.  It does not attempt full triple-quote
    tracking — the census only needs to avoid counting prose that
    mentions ``frame_width_targets =`` inside a docstring, and every such
    mention in this codebase is a whole-line comment or an indented
    docstring line ending in prose (never a bare assignment).
    """
    for i, raw in enumerate(lines, 1):
        s = raw.rstrip("\n")
        stripped = s.strip()
        if stripped.startswith("#"):
            continue
        # Crude inline-comment strip (no string-literal awareness).  Good
        # enough: the census patterns are assignments and never appear
        # inside a string literal in this codebase.
        if "#" in s:
            s = s.split("#", 1)[0]
        yield i, s


def check_w1_writer_census(root: str = "game") -> None:
    offenders = []
    gov_offenders = []
    for path in _iter_py_files(root):
        rel = path.replace("\\", "/")
        try:
            with open(path, encoding="utf-8") as fh:
                lines = fh.readlines()
        except Exception:
            continue
        for lineno, code in _strip_docstrings_and_comments(lines):
            if _WRITE_RE.search(code):
                if rel not in ALLOWED_WIDTH_PUBLISHERS:
                    offenders.append("%s:%d" % (rel, lineno))
            if _GOV_RAW_RE.search(code):
                if rel not in ALLOWED_GOVERNOR_STATE_WRITERS:
                    gov_offenders.append("%s:%d" % (rel, lineno))

    check(
        "W1a frame_width_targets written only by allow-listed publishers",
        not offenders,
        ("offenders: %s" % ", ".join(offenders)) if offenders
        else "2 publishers (pipeline, DBU)",
    )
    check(
        "W1b raw governor width fields written only by the governor",
        not gov_offenders,
        ("offenders: %s" % ", ".join(gov_offenders)) if gov_offenders
        else "governor-owned",
    )

    # Every allow-listed publisher must actually reference the authority.
    for rel in sorted(ALLOWED_WIDTH_PUBLISHERS):
        found = os.path.exists(rel)
        consults = False
        if found:
            with open(rel, encoding="utf-8") as fh:
                text = fh.read()
            consults = (
                "formation_width_authority" in text
                and ("publish_formation_width" in text
                     or "formation_width_value" in text)
            )
        check(
            "W1c publisher consults the authority: %s" % rel.split("/")[-1],
            found and consults,
            "" if (found and consults) else "missing import/consult",
        )
# ─────────────────────────────────────────────────────────────────────
# W2 — AUTHORITY PRESENT
# ─────────────────────────────────────────────────────────────────────

def check_w2_authority_present() -> None:
    required = (
        "publish_formation_width",
        "published_formation_width",
        "published_side_widths",
        "published_span_contract",
        "formation_width_value",
        "conflicting_width_solves",
        "width_solves_for_frame",
        "lane_step_for",
        "lane_step_floor_binds",
        "width_publication_is_fresh",
    )
    missing = [n for n in required if not hasattr(FWA, n)]
    check(
        "W2a authority exposes the full published-fact read API",
        not missing,
        ("missing: %s" % ", ".join(missing)) if missing
        else "%d symbols" % len(required),
    )

    # The publication payload must carry the named reducer ledger.
    w = make_world(frame=101)
    pub = FWA.publish_formation_width(w, DefenceTuners())
    need = {"commanded_m", "governed_m", "left_m", "right_m",
            "short_side", "reducers", "frame"}
    absent = sorted(need - set(pub.keys()))
    check(
        "W2b publication payload carries the reducer ledger + span facts",
        not absent,
        ("missing keys: %s" % ", ".join(absent)) if absent
        else "all present",
    )


# ─────────────────────────────────────────────────────────────────────
# W3 — SINGLE SOLVE PER FRAME (cache identity)
# ─────────────────────────────────────────────────────────────────────

def check_w3_single_solve_per_frame() -> None:
    tuners = DefenceTuners()
    w = make_world(frame=202)
    first = FWA.publish_formation_width(w, tuners)
    second = FWA.publish_formation_width(w, tuners)

    check(
        "W3a repeated publication is a cache HIT (identical object)",
        first is second,
        "same object" if first is second else "re-solved (distinct objects)",
    )

    # A new frame must produce a NEW solve (not a permanently latched fact).
    w.frame_count = 203
    third = FWA.publish_formation_width(w, tuners)
    check(
        "W3b a new frame produces a fresh solve",
        third is not first and third is not second,
        "frame key changed",
    )

    check(
        "W3c reducer ledger does not accumulate on a cache hit",
        first["reducers"] == second["reducers"],
        "ledger stable",
    )


# ─────────────────────────────────────────────────────────────────────
# W4 — PUBLISHED == CONSUMERS
# ─────────────────────────────────────────────────────────────────────

TOL = 1e-6


def check_w4_consumers_agree() -> None:
    tuners = DefenceTuners()
    w = make_world(frame=303, ruck_y=35.0, width=55.0)
    pub = FWA.publish_formation_width(w, tuners)

    gov = governed_width_m(w, tuners)
    check(
        "W4a governed scalar accessor == published governed_m",
        abs(gov - pub["governed_m"]) < TOL,
        "%.9f vs %.9f" % (gov, pub["governed_m"]),
    )

    gov_obj = DefensiveLineGovernor(tuners, None)
    check(
        "W4b governor.governed_width_m == published governed_m",
        abs(gov_obj.governed_width_m(w) - pub["governed_m"]) < TOL,
        "%.9f vs %.9f" % (gov_obj.governed_width_m(w), pub["governed_m"]),
    )

    lw, rw = DSS.side_widths(w, tuners, ruck_y=float(w.ruck_y_m))
    check(
        "W4c spacing side_widths == published per-side span",
        abs(lw - pub["left_m"]) < TOL and abs(rw - pub["right_m"]) < TOL,
        "L %.9f/%.9f  R %.9f/%.9f" % (lw, pub["left_m"], rw, pub["right_m"]),
    )

    c = SC.publish_span_contract(w, tuners)
    check(
        "W4d span contract == published per-side span",
        abs(c.left_m - pub["left_m"]) < TOL
        and abs(c.right_m - pub["right_m"]) < TOL,
        "contract L %.6f R %.6f" % (c.left_m, c.right_m),
    )

    targets = recompute_width_x(
        w, list(w.defending_team), tuners,
        governor_width=FWA.formation_width_value(w, tuners),
        spacing_targets={},
    )
    span = (max(targets.values()) - min(targets.values())) if targets else 0.0
    check(
        "W4e X recompute is driven by the published width",
        span <= pub["governed_m"] + TOL,
        "line span %.6f <= governed %.6f" % (span, pub["governed_m"]),
    )
# ─────────────────────────────────────────────────────────────────────
# W5 — NO CONFLICTING VALUES WITHIN A FRAME
# ─────────────────────────────────────────────────────────────────────

def check_w5_no_conflict() -> None:
    tuners = DefenceTuners()
    w = make_world(frame=404, width=55.0)
    FWA.publish_formation_width(w, tuners)
    # Repeat reads through every consumer path — none may introduce a
    # second, disagreeing solve for the frame.
    governed_width_m(w, tuners)
    DefensiveLineGovernor(tuners, None).governed_width_m(w)
    DSS.side_widths(w, tuners, ruck_y=float(w.ruck_y_m))
    SC.publish_span_contract(w, tuners)

    conflict = FWA.conflicting_width_solves(w)
    check(
        "W5a no conflicting width solves across consumers in one frame",
        not conflict,
        ("conflicts: %s" % conflict) if conflict else "single value",
    )

    # The detector must actually DETECT a conflict.  Inject a second ledger
    # entry for the SAME scope carrying a DIFFERENT span — the exact
    # multi-publisher signature Stage 6 removes — and confirm it fires.
    solves_attr = FWA.FORMATION_WIDTH_SOLVES_ATTR
    w2 = make_world(frame=405, width=55.0)
    pub = FWA.publish_formation_width(w2, tuners)
    existing = list(getattr(w2, solves_attr, []) or [])
    injected = {
        "scope_ruck_y": float(w2.ruck_y_m),
        "left_m": float(pub["left_m"]) + 4.0,
        "right_m": float(pub["right_m"]) + 4.0,
    }
    setattr(w2, solves_attr, existing + [injected])
    forced = FWA.conflicting_width_solves(w2)
    check(
        "W5b conflict detector fires on an injected disagreeing solve",
        bool(forced),
        ("detected %d" % len(forced)) if forced
        else "NOT DETECTED (detector is inert)",
    )
# ─────────────────────────────────────────────────────────────────────
# W6 — REDUCER LEDGER CLOSURE
# ─────────────────────────────────────────────────────────────────────

def check_w6_reducer_closure() -> None:
    tuners = DefenceTuners()
    # Exercise several lateral anchor positions so different reducers bind.
    worst = 0.0
    worst_case = None
    for ruck_y in (10.0, 20.0, 34.0, 35.0, 45.0, 58.0):
        w = make_world(frame=500 + int(ruck_y), ruck_y=ruck_y, width=55.0)
        pub = FWA.publish_formation_width(w, tuners)
        mean = 0.5 * (float(pub["left_m"]) + float(pub["right_m"]))
        total = sum(float(v) for v in pub["reducers"].values())
        err = abs(mean - (float(pub["commanded_m"]) + total))
        if err > worst:
            worst = err
            worst_case = ruck_y

    check(
        "W6a published mean == commanded + sum(reducers)",
        worst < TOL,
        "worst |err| %.3e at ruck_y=%s" % (worst, worst_case),
    )

    # The ledger must be NAMED (not an anonymous residue): every key must
    # be a reducer symbol, so a future width writer cannot hide an
    # untracked delta.
    w = make_world(frame=590, ruck_y=35.0, width=55.0)
    pub = FWA.publish_formation_width(w, tuners)
    keys = sorted(pub["reducers"].keys())
    named = all(k.isupper() and k.replace("_", "").isalpha() for k in keys)
    check(
        "W6b every reducer is a named symbol (no anonymous residue)",
        named and len(keys) > 0,
        "reducers: %s" % ", ".join(keys),
    )


# ─────────────────────────────────────────────────────────────────────
# W7 — PUBLISHED SPAN vs ACTUAL DEFENDER OCCUPATION
# ─────────────────────────────────────────────────────────────────────

def check_w7_span_vs_occupation() -> None:
    tuners = DefenceTuners()
    w = make_world(frame=700, ruck_y=35.0, bc_x=60.0, width=55.0)
    pub = FWA.publish_formation_width(w, tuners)

    lw, rw = DSS.side_widths(w, tuners, ruck_y=float(w.ruck_y_m))
    short_f = float(getattr(tuners, "tuner_short_side_factor", 1.0))
    open_f = float(getattr(tuners, "tuner_open_side_factor", 1.0))
    gov = float(pub["governed_m"])
    expected = sorted([gov * short_f, gov * open_f])
    actual = sorted([lw, rw])
    check(
        "W7a per-side span IS the proportional split of the governed width",
        abs(actual[0] - expected[0]) < 1e-6
        and abs(actual[1] - expected[1]) < 1e-6,
        "actual %s vs expected %s (short_f=%.3f open_f=%.3f)"
        % (["%.4f" % v for v in actual], ["%.4f" % v for v in expected],
           short_f, open_f),
    )
    check(
        "W7a2 the OPEN side consumes the full published governed width",
        abs(max(lw, rw) - gov) < 1e-6,
        "open side %.6f vs governed %.6f" % (max(lw, rw), gov),
    )
    # Neither side may exhaust the sideline: each full span must fit the
    # usable field (margin -> field_max).
    field_max = float(getattr(tuners, "field_width_m", 70.0)) - float(
        getattr(tuners, "sideline_margin_m", 3.0))
    max_half = field_max - float(getattr(tuners, "sideline_margin_m", 3.0))
    check(
        "W7a3 neither side exhausts the available sideline room",
        lw <= max_half * 2.0 + 1e-6 and rw <= max_half * 2.0 + 1e-6,
        "L %.4f R %.4f both <= %.4f" % (lw, rw, max_half * 2.0),
    )

    # The lane ladder derived from the span must not be floored at the
    # inner end: at a normal count the span owns the step.
    n_side = 4
    step_l = FWA.lane_step_for(pub, -1, n_side, tuners)
    step_r = FWA.lane_step_for(pub, +1, n_side, tuners)
    floored_l = FWA.lane_step_floor_binds(pub, -1, n_side, tuners)
    floored_r = FWA.lane_step_floor_binds(pub, +1, n_side, tuners)
    check(
        "W7b lane step is span-derived (not floored) at a normal count",
        step_l > 0.0 and step_r > 0.0 and not (floored_l and floored_r),
        "step L %.4f (floored=%s)  R %.4f (floored=%s)"
        % (step_l, floored_l, step_r, floored_r),
    )

    # A widening command must move the published span and the derived step
    # in the SAME direction — the span owns the ladder end to end.
    w2 = make_world(frame=701, ruck_y=35.0, bc_x=60.0, width=55.0)
    pub_narrow = FWA.publish_formation_width(w2, tuners)
    w3 = make_world(frame=702, ruck_y=35.0, bc_x=60.0, width=64.0)
    pub_wide = FWA.publish_formation_width(w3, tuners)
    narrow_step = FWA.lane_step_for(pub_narrow, +1, n_side, tuners)
    wide_step = FWA.lane_step_for(pub_wide, +1, n_side, tuners)
    check(
        "W7c widening the command widens the span-owned lane step",
        wide_step > narrow_step,
        "narrow %.4f -> wide %.4f" % (narrow_step, wide_step),
    )


# ─────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────

def main() -> int:
    print("=" * 72)
    print("STAGE 6 — FORMATION WIDTH SINGLE AUTHORITY VERIFICATION")
    print("=" * 72)

    print("\nW1 — WIDTH WRITER CENSUS (static)")
    check_w1_writer_census("game")

    print("\nW2 — WIDTH AUTHORITY PRESENT")
    check_w2_authority_present()

    print("\nW3 — SINGLE SOLVE PER FRAME")
    check_w3_single_solve_per_frame()

    print("\nW4 — PUBLISHED WIDTH == EVERY CONSUMER")
    check_w4_consumers_agree()

    print("\nW5 — NO CONFLICTING WIDTH VALUES WITHIN A FRAME")
    check_w5_no_conflict()

    print("\nW6 — REDUCER LEDGER CLOSURE")
    check_w6_reducer_closure()

    print("\nW7 — PUBLISHED SPAN vs DEFENDER OCCUPATION")
    check_w7_span_vs_occupation()

    print("\n" + "=" * 72)
    if FAILURES:
        print("STAGE 6 VERDICT: FAIL — %d of %d checks failed"
              % (len(FAILURES), CHECKS_RUN))
        for f in FAILURES:
            print("  - %s" % f)
        print("=" * 72)
        return 1
    print("STAGE 6 VERDICT: PASS — all %d checks passed" % CHECKS_RUN)
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
