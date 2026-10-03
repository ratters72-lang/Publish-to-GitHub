"""POST-STAGE-3F REBANDING verifier (TASK IX, 2026-08-26).

SPEC CONTEXT
------------
TES is stable, but the defensive line can still present uneven spacing
because the initial band/depth assignments are occasionally incorrect.
With TES no longer overriding Y-axis geometry (FX-4/TD-4), these
misassignments are visible: two defenders can be seeded into the SAME
band, and spacing/retreat attempt to correct them too late in the PTB
preamble.  The fix adds a rebanding step immediately AFTER Stage-3F
timing stabilisation (the post-ruck quiet window) that recomputes ideal
band membership and reassigns BEFORE TES_INIT.

FIX UNDER TEST
--------------
``game/defence/defence_lvl_1/defensive_rebanding.py``
    reband_after_stabilisation(world, defenders):
      GATE 1  ruck_established must be True (Stage-3B pivot).
      GATE 2  Stage-3F quiet window elapsed (seconds_since_ptb >=
              tuners.post_ruck_quiet_window_s).
      STEP 1  Re-run the count-driven side split on SETTLED positions;
              flipped defenders lose their stale seed.
      STEP 2  Per-side slot invariants: single-capacity de-dup +
              exactly-one-A (kills same-band doubling).
      STEP 3  Restamp role_depth_target_x/y from the anchor authority.
DBU wiring: called immediately after BOTH def_timing.update() sites,
before any _start_tackle; republishes frame geometry on change.

PARTS
-----
  P1  Quiet-window gate: inside the window the reband is a NO-OP even
      on a doubled band.
  P2  Ruck-pivot gate: ruck_established False -> NO-OP.
  P3  Post-window same-band DOUBLING repair: two A claimants on one
      side collapse to exactly one A, invariants restored.
  P4  Post-window MISSING-A repair: a side with zero A gains exactly
      one A.
  P5  SIDE MIS-SPLIT repair: every defender stamped onto one side gets
      re-split by the count authority; flipped holders lose their seed
      and the following assign_depth_roles bootstraps clean bands.
  P6  IDEMPOTENCE: a second post-window call on a correct line is a
      no-op returning False.
  P7  EXCLUSIONS: fullback actor / markers / TACKLE-STATE holders are
      never touched by the reband.
  P8  DBU WIRING: the call sits between def_timing.update() and the
      next _start_tackle in BOTH OPEN_PLAY pipelines, with the import
      present.

Exit code 0 when all scenarios pass, 1 otherwise.
"""
import sys
import json

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player, DefenderRole
from game.defence.defence_lvl_1.defensive_rebanding import (
    reband_after_stabilisation,
    quiet_window_elapsed,
)
from game.defence.defence_lvl_1.defensive_depth_roles import (
    assign_depth_roles,
)

RUCK_X = 60.0
RUCK_Y = 35.0
DEF_LINE_WIDTH = 40.0

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(name)


class RebandWorld:
    """Minimal world for the reband gates (mirrors MiniWorld pattern)."""

    def __init__(self, seconds_since_ptb=0.6, ruck_established=True):
        self.ruck_x_m = RUCK_X
        self.ruck_y_m = RUCK_Y
        self.def_line_width_m = DEF_LINE_WIDTH
        self.open_play_engine = None          # -> live DefenceTuners
        self.ruck_established = ruck_established
        self.seconds_since_ptb = seconds_since_ptb


def make_defender(player_id, x, y):
    return Player(x=x, y=y, player_id=player_id)


def make_line():
    """Seven-defender line straddling ruck_y=35 (attack runs +X)."""
    return [
        make_defender(1, 70.0, 14.0),
        make_defender(2, 70.0, 22.0),
        make_defender(3, 70.0, 31.0),
        make_defender(4, 70.0, 33.0),
        make_defender(5, 70.0, 42.0),
        make_defender(6, 70.0, 50.0),
        make_defender(7, 70.0, 58.0),
    ]


def seed_sides_roles(line, spec):
    """spec: {player_id: (side, role)}; marks everyone seeded."""
    for d in line:
        s, r = spec[d.player_id]
        d.depth_side = s
        d.depth_role = r
        d._depth_seeded = True


def role_map(line):
    return {d.player_id: getattr(d, "depth_role", None) for d in line}


def side_counts(line):
    from collections import Counter
    per = {-1: Counter(), 1: Counter()}
    for d in line:
        per[getattr(d, "depth_side", None)][getattr(d, "depth_role", None)] += 1
    return per


def invariants_hold(line):
    per = side_counts(line)
    for side, counts in per.items():
        for role in ("WINGER", "CENTRE", "A", "B", "C"):
            if counts.get(role, 0) > 1:
                return False, f"side {side}: {counts.get(role)}x {role}"
        interior = sum(
            n for r, n in counts.items()
            if isinstance(r, str) and r not in ("WINGER", "CENTRE")
        )
        if interior >= 1 and counts.get("A", 0) != 1:
            return False, f"side {side}: A={counts.get('A', 0)} (interior={interior})"
    return True, ""


DOUBLE_A_SEED = {
    1: (-1, "WINGER"),
    2: (-1, "CENTRE"),
    3: (-1, "A"),     # <- contested
    4: (-1, "A"),     # <- contested (same-band doubling)
    5: (1, "WINGER"),
    6: (1, "CENTRE"),
    7: (1, "A"),
}

MISSING_A_SEED = {
    1: (-1, "WINGER"),
    2: (-1, "CENTRE"),
    3: (-1, "B"),
    4: (-1, "B"),     # <- zero A on the left side
    5: (1, "WINGER"),
    6: (1, "CENTRE"),
    7: (1, "A"),
}


# ─────────────────────────────────────────────────────────────────────
# P1 — Stage-3F quiet-window gate
# ─────────────────────────────────────────────────────────────────────

def p1_quiet_window_gate():
    print()
    print("[P1] Inside the quiet window the reband is a NO-OP")
    w = RebandWorld(seconds_since_ptb=0.2)
    check("P1.0: gate helper agrees the window has NOT elapsed",
          quiet_window_elapsed(w) is False)
    line = make_line()
    seed_sides_roles(line, DOUBLE_A_SEED)
    before = role_map(line)
    ret = reband_after_stabilisation(w, line)
    check("P1.1: reband returned False inside the window", ret is False)
    check("P1.2: doubled A left UNTOUCHED inside the window",
          role_map(line) == before)


def p2_ruck_pivot_gate():
    print()
    print("[P2] ruck_established=False is a NO-OP")
    w = RebandWorld(seconds_since_ptb=0.6, ruck_established=False)
    check("P2.0: clock elapsed but pivot not armed",
          quiet_window_elapsed(w) is True)
    line = make_line()
    seed_sides_roles(line, DOUBLE_A_SEED)
    before = role_map(line)
    ret = reband_after_stabilisation(w, line)
    check("P2.1: reband returned False pre-pivot", ret is False)
    check("P2.2: roles untouched pre-pivot", role_map(line) == before)


# ─────────────────────────────────────────────────────────────────────
# P3 — same-band doubling repair (the headline defect)
# ─────────────────────────────────────────────────────────────────────

def p3_doubling_repair():
    print()
    print("[P3] Post-window: two A claimants collapse to exactly one A")
    w = RebandWorld(seconds_since_ptb=0.6)
    line = make_line()
    seed_sides_roles(line, DOUBLE_A_SEED)
    ret = reband_after_stabilisation(w, line)
    check("P3.0: reband detected + fixed (returned True)", ret is True)
    ok, why = invariants_hold(line)
    check("P3.1: slot invariants restored", ok, why)
    per = side_counts(line)
    check("P3.2: LEFT holds exactly one A", per[-1].get("A", 0) == 1,
          str({k: dict(v) for k, v in per.items()}))
    check("P3.3: RIGHT untouched (still one A)", per[1].get("A", 0) == 1)


def p4_missing_a_repair():
    print()
    print("[P4] Post-window: a side with zero A gains exactly one A")
    w = RebandWorld(seconds_since_ptb=0.6)
    line = make_line()
    seed_sides_roles(line, MISSING_A_SEED)
    per_before = side_counts(line)
    check("P4.0: precondition — LEFT has zero A",
          per_before[-1].get("A", 0) == 0)
    ret = reband_after_stabilisation(w, line)
    check("P4.1: reband fired", ret is True)
    ok, why = invariants_hold(line)
    check("P4.2: slot invariants restored", ok, why)
    per = side_counts(line)
    check("P4.3: LEFT now holds exactly one A", per[-1].get("A", 0) == 1,
          str({k: dict(v) for k, v in per.items()}))


def p5_side_mis_split():
    print()
    print("[P5] Post-window: single-side stamping is re-split; flipped "
          "holders lose their stale seed")
    w = RebandWorld(seconds_since_ptb=0.6)
    line = make_line()
    spec = {pid: (1, r) for pid, (_, r) in DOUBLE_A_SEED.items()}
    seed_sides_roles(line, spec)          # EVERYONE stamped RIGHT
    ret = reband_after_stabilisation(w, line)
    check("P5.0: reband detected the mis-split", ret is True)
    per = side_counts(line)
    check("P5.1: both sides populated after re-split",
          sum(per[-1].values()) >= 1 and sum(per[1].values()) >= 1,
          str({k: dict(v) for k, v in per.items()}))
    flipped = [d for d in line if getattr(d, "depth_side", None) == -1]
    check("P5.2: flipped holders lost their stale seed",
          all(not getattr(d, "_depth_seeded", True) for d in flipped),
          str([d.player_id for d in flipped]))
    # The NEXT assign_depth_roles bootstraps them into valid bands.
    assign_depth_roles(line, w)
    ok, why = invariants_hold(line)
    check("P5.3: invariants hold after the follow-up assignment", ok, why)


def p6_idempotence():
    print()
    print("[P6] Second post-window call on a correct line is a no-op")
    w = RebandWorld(seconds_since_ptb=0.6)
    line = make_line()
    seed_sides_roles(line, MISSING_A_SEED)
    reband_after_stabilisation(w, line)          # repair frame
    snap = {d.player_id: (d.depth_side, d.depth_role) for d in line}
    ret2 = reband_after_stabilisation(w, line)
    snap2 = {d.player_id: (d.depth_side, d.depth_role) for d in line}
    check("P6.0: second call returned False", ret2 is False)
    check("P6.1: assignments byte-stable across the second call",
          snap == snap2)


def p7_exclusions():
    print()
    print("[P7] Fullback / markers / TACKLE-STATE holders are never touched")
    w = RebandWorld(seconds_since_ptb=0.6)
    line = make_line()
    seed_sides_roles(line, DOUBLE_A_SEED)

    fb = make_defender(90, 75.0, 35.0)
    fb.fullback_role_active = True
    mk = make_defender(91, 61.0, 34.5)
    mk.defender_role = DefenderRole.MARKER_A
    held = make_defender(92, 66.0, 36.0)
    held.tackle_state_active = True
    extras = [fb, mk, held]

    ret = reband_after_stabilisation(w, line + extras)
    check("P7.0: reband still processed the real line", ret is True)
    check("P7.1: fullback actor got NO depth role",
          getattr(fb, "depth_role", None) is None)
    check("P7.2: marker kept its excluded-role clearance",
          getattr(mk, "depth_role", None) is None)
    check("P7.3: TACKLE-STATE holder got NO depth role",
          getattr(held, "depth_role", None) is None)


def p8_dbu_wiring():
    print()
    print("[P8] DBU wiring: import present; call sits between "
          "def_timing.update() and the next _start_tackle in BOTH paths")
    src = open(
        "game/defence/defence_lvl_1/defensive_behaviour_unified.py",
        encoding="utf-8",
    ).read()

    imp = ("from game.defence.defence_lvl_1.defensive_rebanding import"
           in src)
    check("P8.0: reband import present in DBU", imp)

    timing_idx = [i for i in range(len(src))
                  if src.startswith("self.def_timing.update(", i)]
    reband_idx = [i for i in range(len(src))
                  if src.startswith("_reband_after_stabilisation(world", i)]
    tackle_idx = [i for i in range(len(src))
                  if src.startswith("_start_tackle(bc, primary)", i)]
    check("P8.1: two def_timing.update sites found", len(timing_idx) == 2,
          str(timing_idx))
    check("P8.2: two reband call sites found", len(reband_idx) == 2,
          str(reband_idx))
    if len(timing_idx) == 2 and len(reband_idx) == 2:
        check("P8.3: structured-path reband follows its def_timing.update",
              reband_idx[0] > timing_idx[0])
        check("P8.4: unified-path reband follows its def_timing.update",
              reband_idx[1] > timing_idx[1])
        nxt1 = [t for t in tackle_idx if t > reband_idx[0]]
        nxt2 = [t for t in tackle_idx if t > reband_idx[1]]
        check("P8.5: a _start_tackle fires AFTER the structured reband",
              bool(nxt1))
        check("P8.6: a _start_tackle fires AFTER the unified reband",
              bool(nxt2))

    pub = src.count("_republish_geometry(")
    alias = src.count(
        "run_defensive_pipeline as _republish_geometry"
    )
    check("P8.7: frame-geometry republish wired at both call sites",
          pub == 2 and alias == 2,
          f"calls={pub} local-imports={alias}")


def main():
    print("=" * 64)
    print("POST-STAGE-3F REBANDING VALIDATION (TASK IX, 2026-08-26)")
    print("=" * 64)

    p1_quiet_window_gate()
    p2_ruck_pivot_gate()
    p3_doubling_repair()
    p4_missing_a_repair()
    p5_side_mis_split()
    p6_idempotence()
    p7_exclusions()
    p8_dbu_wiring()

    report = {
        "reband_after_stabilisation": {
            "quiet_window_gate": not failures,
            "ruck_pivot_gate": not failures,
            "same_band_doubling_repaired": not failures,
            "missing_a_repaired": not failures,
            "side_resplit": not failures,
            "idempotent": not failures,
            "exclusions_honoured": not failures,
            "dbu_wired": not failures,
            "issues_remaining": failures,
        }
    }
    with open("verify_reband_report.json", "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    print()
    print("=" * 64)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
