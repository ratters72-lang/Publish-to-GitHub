"""BALL-SIDE COLLAPSE + DYNAMIC DEPTH-ROLES verifier (TASK X, 2026-08-26).

SPEC CONTEXT
------------
The depth-role system was static: roles seeded once per kickoff, then held
under hysteresis ("when every slot is occupied, roles are immutable").
That static inheritance corrupts CENTRE/WINGER (edge structure) and A/B/C
(middle bands) because roles seeded while the line was still settling
survive into the tackle even though settled positions disagree.  Rebanding
(TASK IX) fixed only invariant violations (doubling / missing-A /
mis-splits); the dynamic refit below goes further.

FIX UNDER TEST
--------------
``game/defence/defence_lvl_1/ball_side_collapse.py``
    detect_pivot(world):          new ruck pivot since last refit
    resolve_ball_side_bias(world):(ball_side, bias) from BC.y vs ruck_y
    apply_dynamic_depth_roles(world, defenders): refit depth roles from
        scratch off ball-side + ruck context + SETTLED positions at every
        ruck pivot; ball-side count collapse; collapse-lock exemption.
DBU wiring: applied BEFORE both ``reband_after_stabilisation`` sites;
combined republish on either change.

PARTS
-----
  P1  Pre-pivot gate: ruck_established=False -> NO-OP; state forgotten.
  P2  Post-window refit: stale seed-era roles are wiped and re-fitted to
      the settled positions (widest->WINGER, ruck-closest->A); invariants
      restored on every side.
  P3  Ball-side collapse: BC deep LEFT -> LEFT count > RIGHT count (bias 1
      shifts one body); no BC -> legacy symmetric counts.
  P4  Idempotence: same-ruck second call -> no-op, assignments stable.
  P5  Re-pivot: ruck point moves beyond threshold -> refit fires again.
  P6  Quiet-window gate: inside the Stage-3F window the refit is a NO-OP.
  P7  Exclusions + collapse lock: fullback / markers / TACKLE-STATE /
      within-lock-radius-of-BC defenders untouched; the rest refit.
  P8  DBU wiring: import present; dynamic refit wraps BOTH reband sites,
      BEFORE the reband.

Exit code 0 when all scenarios pass, 1 otherwise.
"""
import sys
import math

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player, DefenderRole
from game.defence.defence_lvl_1.ball_side_collapse import (
    detect_pivot,
    resolve_ball_side_bias,
    apply_dynamic_depth_roles,
    reset_pivot_state,
)
from game.defence.defence_lvl_1.defensive_rebanding import quiet_window_elapsed

RUCK_X = 60.0
RUCK_Y = 35.0
DEF_LINE_WIDTH = 40.0

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(name)


class CollapseWorld:
    """Minimal world for the dynamic-refit gates (mirrors RebandWorld)."""

    def __init__(self, seconds_since_ptb=0.6, ruck_established=True,
                 ruck_y=RUCK_Y, ruck_x=RUCK_X, ball_carrier=None):
        self.ruck_x_m = ruck_x
        self.ruck_y_m = ruck_y
        self.def_line_width_m = DEF_LINE_WIDTH
        self.open_play_engine = None          # -> live DefenceTuners
        self.ruck_established = ruck_established
        self.seconds_since_ptb = seconds_since_ptb
        self.ball_carrier = ball_carrier


def make_defender(player_id, x, y):
    return Player(x=x, y=y, player_id=player_id)


def make_line():
    """Six-defender line straddling ruck_y=35 (attack runs +X)."""
    return [
        make_defender(1, 70.0, 14.0),
        make_defender(2, 70.0, 22.0),
        make_defender(3, 70.0, 31.0),
        make_defender(4, 70.0, 42.0),
        make_defender(5, 70.0, 50.0),
        make_defender(6, 70.0, 58.0),
    ]


def make_bc(player_id, x, y):
    bc = Player(x=x, y=y, player_id=player_id)
    return bc


def seed_sides_roles(line, spec):
    """spec: {player_id: (side, role)}; marks everyone seeded."""
    for d in line:
        s, r = spec[d.player_id]
        d.depth_side = s
        d.depth_role = r
        d._depth_seeded = True


# Deliberately WRONG static seed: the widest defender should be WINGER and
# the ruck-closest should be A, but these swap them / scramble the middle.
WRONG_SEED = {
    1: (-1, "A"),        # y=14 widest-left should be WINGER
    2: (-1, "WINGER"),   # y=22 should be CENTRE (interior)
    3: (-1, "CENTRE"),   # y=31 ruck-closest-left should be A
    4: (1, "B"),         # y=42 ruck-closest-right should be A
    5: (1, "A"),         # y=50 should be CENTRE
    6: (1, "WINGER"),    # y=58 rightmost should be WINGER (ok)
}


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
        if side not in (-1, 1):
            continue
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


def widest_is_winger(line):
    """On each populated side the widest |d.y-ruck_y| defender holds WINGER."""
    ok = True
    for side in (-1, 1):
        side_defs = [d for d in line if getattr(d, "depth_side", None) == side]
        if not side_defs:
            continue
        widest = max(side_defs, key=lambda d: abs(d.y - RUCK_Y))
        if getattr(widest, "depth_role", None) != "WINGER":
            ok = False
    return ok


def ruck_closest_is_a(line):
    """On each populated side the ruck-closest |d.y-ruck_y| defender holds A."""
    ok = True
    for side in (-1, 1):
        side_defs = [d for d in line if getattr(d, "depth_side", None) == side]
        if not side_defs:
            continue
        # Only when the side has interior (non-W/C) defenders.
        if any(d.depth_role not in ("WINGER", "CENTRE") for d in side_defs):
            closest = min(side_defs, key=lambda d: abs(d.y - RUCK_Y))
            if getattr(closest, "depth_role", None) != "A":
                ok = False
    return ok


# ─────────────────────────────────────────────────────────────────────
# P1 — pre-pivot gate
# ─────────────────────────────────────────────────────────────────────

def p1_pre_pivot_gate():
    print()
    print("[P1] ruck_established=False is a NO-OP and forgets state")
    w = CollapseWorld(seconds_since_ptb=0.6, ruck_established=False)
    line = make_line()
    seed_sides_roles(line, WRONG_SEED)
    before = role_map(line)
    ret = apply_dynamic_depth_roles(w, line)
    check("P1.0: detect_pivot False pre-pivot",
          detect_pivot(w) is False)
    check("P1.1: apply returned False pre-pivot", ret is False)
    check("P1.2: roles untouched pre-pivot", role_map(line) == before)


# ─────────────────────────────────────────────────────────────────────
# P2 — post-window refit of stale seed-era roles
# ─────────────────────────────────────────────────────────────────────

def p2_post_window_refit():
    print()
    print("[P2] Post-window: stale seed-era roles refit to settled positions")
    reset_pivot_state()          # isolate from prior scenario id reuse
    w = CollapseWorld(seconds_since_ptb=0.6)
    line = make_line()
    seed_sides_roles(line, WRONG_SEED)
    check("P2.0: precondition — quiet window elapsed",
          quiet_window_elapsed(w) is True)
    ret = apply_dynamic_depth_roles(w, line)
    check("P2.1: refit fired", ret is True)
    ok, why = invariants_hold(line)
    check("P2.2: slot invariants restored", ok, why)
    check("P2.3: every eligible defender re-seeded",
          all(getattr(d, "depth_role", None) in
              ("A", "B", "C", "D", "CENTRE", "WINGER") for d in line),
          str(role_map(line)))
    check("P2.4: widest defender per side is WINGER", widest_is_winger(line))
    check("P2.5: ruck-closest interior defender is A", ruck_closest_is_a(line))


# ─────────────────────────────────────────────────────────────────────
# P3 — ball-side collapse
# ─────────────────────────────────────────────────────────────────────

def p3_ball_side_collapse():
    print()
    print("[P3] BC deep LEFT: LEFT count > RIGHT count; no BC -> symmetric")
    # (a) BC on the left; import LEFT/RIGHT signs.
    from game.defence.defence_lvl_1.defensive_split_spacing import LEFT, RIGHT
    reset_pivot_state()          # isolate from prior scenario id reuse
    bc = make_bc(99, 60.0, RUCK_Y - 12.0)   # y=23, offset -12 -> LEFT
    w = CollapseWorld(seconds_since_ptb=0.6, ball_carrier=bc)
    # First call arms the pivot (stamped state).
    line = make_line()
    seed_sides_roles(line, WRONG_SEED)
    apply_dynamic_depth_roles(w, line)

    bs, bias = resolve_ball_side_bias(w)
    check("P3.0: ball side resolved to LEFT", bs == LEFT, str(bs))
    check("P3.1: bias active", bias == 1, str(bias))
    counts = side_counts(line)
    n_left = sum(counts[-1].values())
    n_right = sum(counts[1].values())
    check("P3.2: LEFT count > RIGHT count (collapse)",
          n_left > n_right, f"left={n_left} right={n_right}")
    check("P3.3: LEFT got the surplus (4) / RIGHT held 2",
          n_left == 4 and n_right == 2, f"left={n_left} right={n_right}")
    ok, why = invariants_hold(line)
    check("P3.4: invariants hold under the biased collapse", ok, why)

    # (b) No BC -> legacy symmetric counts.
    w2 = CollapseWorld(seconds_since_ptb=0.6, ruck_established=True)
    reset_pivot_state()
    line2 = make_line()
    seed_sides_roles(line2, WRONG_SEED)
    apply_dynamic_depth_roles(w2, line2)
    counts2 = side_counts(line2)
    n_left2 = sum(counts2[-1].values())
    n_right2 = sum(counts2[1].values())
    check("P3.5: no BC -> symmetric counts",
          n_left2 == n_right2, f"left={n_left2} right={n_right2}")
    ok2, why2 = invariants_hold(line2)
    check("P3.6: invariants hold without ball-side bias", ok2, why2)


# ─────────────────────────────────────────────────────────────────────
# P4 — idempotence within the same ruck
# ─────────────────────────────────────────────────────────────────────

def p4_idempotence():
    print()
    print("[P4] Second post-window call on the same ruck is a no-op")
    reset_pivot_state()          # isolate from prior scenario id reuse
    w = CollapseWorld(seconds_since_ptb=0.6)
    line = make_line()
    seed_sides_roles(line, WRONG_SEED)
    apply_dynamic_depth_roles(w, line)          # refit frame
    snap = {d.player_id: (d.depth_side, d.depth_role) for d in line}
    ret2 = apply_dynamic_depth_roles(w, line)
    snap2 = {d.player_id: (d.depth_side, d.depth_role) for d in line}
    check("P4.0: second call returned False", ret2 is False)
    check("P4.1: assignments byte-stable across the second call",
          snap == snap2, str(snap))


# ─────────────────────────────────────────────────────────────────────
# P5 — re-pivot on ruck movement
# ─────────────────────────────────────────────────────────────────────

def p5_re_pivot():
    print()
    print("[P5] Ruck point moves beyond threshold -> refit fires again")
    reset_pivot_state()          # isolate from prior scenario id reuse
    w = CollapseWorld(seconds_since_ptb=0.6, ruck_y=RUCK_Y)
    line = make_line()
    seed_sides_roles(line, WRONG_SEED)
    apply_dynamic_depth_roles(w, line)          # pivot 1
    snap1 = {(d.player_id): (d.depth_side, d.depth_role) for d in line}
    # Move the ruck beyond pivot_ruck_move_threshold_m (0.5) -> new pivot.
    w.ruck_y_m = RUCK_Y + 0.8
    line2 = make_line()
    seed_sides_roles(line2, WRONG_SEED)
    check("P5.0: detect_pivot sees a new pivot after ruck move",
          detect_pivot(w) is True)
    ret = apply_dynamic_depth_roles(w, line2)
    check("P5.1: refit fired on the new pivot", ret is True)
    ok, why = invariants_hold(line2)
    check("P5.2: invariants hold after re-pivot refit", ok, why)


# ─────────────────────────────────────────────────────────────────────
# P6 — quiet-window gate
# ─────────────────────────────────────────────────────────────────────

def p6_quiet_window_gate():
    print()
    print("[P6] Inside the Stage-3F quiet window the refit is a NO-OP")
    reset_pivot_state()          # isolate from prior scenario id reuse
    w = CollapseWorld(seconds_since_ptb=0.2)   # < 0.5
    line = make_line()
    seed_sides_roles(line, WRONG_SEED)
    before = role_map(line)
    check("P6.0: quiet window NOT elapsed", quiet_window_elapsed(w) is False)
    ret = apply_dynamic_depth_roles(w, line)
    check("P6.1: refit returned False inside the window", ret is False)
    check("P6.2: roles untouched inside the window", role_map(line) == before)


# ─────────────────────────────────────────────────────────────────────
# P7 — exclusions + collapse lock
# ─────────────────────────────────────────────────────────────────────

def p7_exclusions_and_lock():
    print()
    print("[P7] Fullback / markers / TACKLE-STATE / collapse-lock untouched")
    reset_pivot_state()          # isolate from prior scenario id reuse
    w = CollapseWorld(seconds_since_ptb=0.6)
    line = make_line()
    seed_sides_roles(line, WRONG_SEED)

    fb = make_defender(90, 75.0, RUCK_Y)
    fb.fullback_role_active = True
    mk = make_defender(91, 61.0, RUCK_Y - 0.5)
    mk.defender_role = DefenderRole.MARKER_A
    held = make_defender(92, 66.0, RUCK_Y + 0.5)
    held.tackle_state_active = True
    # A defender within COLLAPSE_LOCK_RADIUS_M (2.0) of a BC at the ruck.
    bc = make_bc(99, 62.0, RUCK_Y)
    locked = make_defender(93, 61.5, RUCK_Y + 0.2)   # ~1.3 m from BC
    extras = [fb, mk, held, locked]
    w.ball_carrier = bc

    # Established roles for the exclusions must not be clobbered.
    fb.depth_role = "FB_STAMP"
    mk.depth_role = None
    held.depth_role = None
    locked.depth_role = "A"
    locked.depth_side = 1
    locked._depth_seeded = True

    ret = apply_dynamic_depth_roles(w, line + extras)
    check("P7.0: refit still processed the real line", ret is True)
    check("P7.1: fullback actor preserved",
          getattr(fb, "depth_role", None) == "FB_STAMP")
    check("P7.2: marker untouched (None)",
          getattr(mk, "depth_role", None) is None)
    check("P7.3: TACKLE-STATE holder untouched (None)",
          getattr(held, "depth_role", None) is None)
    # The collapse-locked defender keeps its pre-existing A.
    check("P7.4: collapse-locked defender preserved A",
          getattr(locked, "depth_role", None) == "A",
          str(getattr(locked, "depth_role", None)))
    check("P7.5: collapse-locked defender preserved side +1",
          getattr(locked, "depth_side", None) == 1)


# ─────────────────────────────────────────────────────────────────────
# P8 — DBU wiring
# ─────────────────────────────────────────────────────────────────────

def p8_dbu_wiring():
    print()
    print("[P8] DBU wiring: dynamic refit wraps BOTH reband sites, before reband")
    src = open(
        "game/defence/defence_lvl_1/defensive_behaviour_unified.py",
        encoding="utf-8",
    ).read()

    imp = ("apply_dynamic_depth_roles as _apply_dynamic_depth_roles" in src)
    check("P8.0: dynamic-refit import present in DBU", imp)

    dyn_idx = [i for i in range(len(src))
               if src.startswith("_apply_dynamic_depth_roles(world", i)]
    reband_idx = [i for i in range(len(src))
                  if src.startswith("_reband_after_stabilisation(world", i)]
    check("P8.1: two dynamic-refit call sites found", len(dyn_idx) == 2,
          str(dyn_idx))
    check("P8.2: two reband call sites found", len(reband_idx) == 2,
          str(reband_idx))
    if len(dyn_idx) == 2 and len(reband_idx) == 2:
        check("P8.3: refit 1 precedes reband 1",
              dyn_idx[0] < reband_idx[0])
        check("P8.4: refit 2 precedes reband 2",
              dyn_idx[1] < reband_idx[1])

    # The combined republish condition must be present at both sites.
    cond = src.count("if _dynamic_refit or _reband_after_stabilisation(world, line_defenders):")
    check("P8.5: combined republish condition wired twice", cond == 2,
          str(cond))


def main():
    print("=" * 64)
    print("BALL-SIDE COLLAPSE + DYNAMIC DEPTH-ROLES VALIDATION (TASK X)")
    print("=" * 64)

    reset_pivot_state()
    p1_pre_pivot_gate()
    p2_post_window_refit()
    p3_ball_side_collapse()
    p4_idempotence()
    p5_re_pivot()
    p6_quiet_window_gate()
    p7_exclusions_and_lock()
    p8_dbu_wiring()

    print()
    print("=" * 64)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
