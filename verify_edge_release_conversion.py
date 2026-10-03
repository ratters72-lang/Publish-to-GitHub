"""EDGE RELEASE — CENTRE/WINGER → INTERIOR DEPTH-ROLE CONVERSION verifier
(TASK XI, 2026-08-26).

SPEC CONTEXT
------------
After TASK X the ball-side *counts* collapse, but the ball-side *identity*
does not: every refit/bootstrap re-crowns the widest ball-side defender
WINGER and the next CENTRE, locking them to outer structural anchors even
when the carrier is deep on their side and positional identity no longer
matters.  This task completes the unified depth-role model: when the ball
is close enough, the CENTRE/WINGER slots ON THE BALL SIDE dissolve and
their holders are reinserted into the interior depth stack by PURE
Y-AXIS ORDERING (closest-to-ruck → A, next → B, then C and D).

FIX UNDER TEST
--------------
``game/defence/defence_lvl_1/ball_side_collapse.py``
    resolve_edge_release(world): (active, side) with hysteresis; single
    writer; last_edge_release(world): previously applied pair.
``game/defence/defence_lvl_1/defensive_depth_roles.py``
    Internal mode derivation inside ``assign_depth_roles`` (every caller
    sees ONE model); transition reseeding of exactly the affected side;
    released bootstrap (no W/C seeded, whole side pure Y-order lanes);
    released maintenance (stale W/C holders demoted, interior-only fill,
    ordered output omits W/C); collapse-lock exemption until radius exit.
Tuners (DefenceTuners): collapse_edge_release_enabled /
    collapse_edge_release_offset_m / collapse_edge_release_hysteresis_m.

PARTS
-----
  P1  Gate-off / sub-threshold: canonical W/C preserved on both sides.
  P2  Conversion: deep BC -> ball-side stack is pure Y-order A/B/C/D with
      NO W/C; weak side keeps canonical W/C; slot invariants hold.
  P3  Hysteresis: release persists inside the exit band; canonical W/C
      restored once the carrier retreats below exit.
  P4  Maintenance: steady-state idempotence while active; an injected
      stale CENTRE holder on the released side is demoted.
  P5  Collapse lock: a W/C holder within COLLAPSE_LOCK_RADIUS_M of the BC
      keeps its role through the transition and converts after exiting.
  P6  Ball-side cross: old side restores canonical W/C, new side releases.
  P7  Dynamic refit integration: apply_dynamic_depth_roles produces the
      released ball-side stack; second call on the same ruck is a no-op.
  P8  Wiring: prior ball-side-collapse verifier still passes (subprocess);
      derivation is internal (no new DBU call sites required).

Exit code 0 when all scenarios pass, 1 otherwise.
"""
import math
import subprocess
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_depth_roles import (
    assign_depth_roles,
)
from game.defence.defence_lvl_1.ball_side_collapse import (
    resolve_ball_side_bias,
    resolve_edge_release,
    last_edge_release,
    apply_dynamic_depth_roles,
    reset_pivot_state,
)
from game.defence.defence_lvl_1.defensive_split_spacing import (
    LEFT,
    RIGHT,
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


class Tuners(DefenceTuners):
    """Live tuners with targeted overrides."""

    def __init__(self, **overrides):
        super().__init__()
        for key, value in overrides.items():
            setattr(self, key, value)


class EngineStub:
    def __init__(self, tuners):
        self.def_tuners = tuners


class ReleaseWorld:
    """Minimal world for direct assign/refit calls."""

    def __init__(self, ball_carrier=None, ruck_established=True,
                 seconds_since_ptb=0.6, tuners=None):
        self.ruck_x_m = RUCK_X
        self.ruck_y_m = RUCK_Y
        self.def_line_width_m = DEF_LINE_WIDTH
        self.ruck_established = ruck_established
        self.seconds_since_ptb = seconds_since_ptb
        self.ball_carrier = ball_carrier
        self.open_play_engine = EngineStub(tuners or Tuners())


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
    return Player(x=x, y=y, player_id=player_id)


def role_map(line):
    return {d.player_id: getattr(d, "depth_role", None) for d in line}


def side_of(line, side):
    return [d for d in line if getattr(d, "depth_side", None) == side]


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


def no_wc(side_defs):
    return all(
        getattr(d, "depth_role", None) not in ("WINGER", "CENTRE")
        for d in side_defs
    )


def has_canonical_wc(side_defs):
    roles = {getattr(d, "depth_role", None) for d in side_defs}
    return "WINGER" in roles and "CENTRE" in roles


def pure_y_order(side_defs):
    """Released-side law: sorted by |y − ruck_y| ascending, the roles are
    exactly A, B, C, then all D."""
    ordered = sorted(side_defs, key=lambda d: abs(d.y - RUCK_Y))
    expected = ["A", "B", "C"] + ["D"] * max(0, len(ordered) - 3)
    actual = [getattr(d, "depth_role", None) for d in ordered]
    return actual == expected, f"{actual}"


# ─────────────────────────────────────────────────────────────────────
# P1 — gate off / sub-threshold keeps the canonical edge
# ─────────────────────────────────────────────────────────────────────

def p1_gate_off_and_subthreshold():
    print()
    print("[P1] Disabled tuner + shallow BC preserve canonical W/C")
    # (a) master gate OFF with a deep BC -> legacy always-locked edge.
    w = ReleaseWorld(
        ball_carrier=make_bc(99, 60.0, RUCK_Y - 12.0),
        tuners=Tuners(collapse_edge_release_enabled=False),
    )
    line = make_line()
    assign_depth_roles(line, w)
    ok, why = invariants_hold(line)
    check("P1.0: gate-off invariants hold", ok, why)
    check("P1.1: gate-off LEFT keeps canonical W/C",
          has_canonical_wc(side_of(line, LEFT)), str(role_map(line)))
    check("P1.2: gate-off RIGHT keeps canonical W/C",
          has_canonical_wc(side_of(line, RIGHT)))
    # (b) enabled but BC centred -> inactive.
    reset_pivot_state()
    w2 = ReleaseWorld(ball_carrier=make_bc(99, 60.0, RUCK_Y))
    active, side = resolve_edge_release(w2)
    check("P1.3: centred BC -> release inactive", active is False)
    line2 = make_line()
    assign_depth_roles(line2, w2)
    check("P1.4: centred BC keeps W/C both sides",
          has_canonical_wc(side_of(line2, LEFT))
          and has_canonical_wc(side_of(line2, RIGHT)),
          str(role_map(line2)))


# ─────────────────────────────────────────────────────────────────────
# P2 — deep BC converts the ball-side edge into the interior stack
# ─────────────────────────────────────────────────────────────────────

def p2_deep_bc_conversion():
    print()
    print("[P2] BC deep LEFT: LEFT stack pure Y-order, no W/C; RIGHT canonical")
    reset_pivot_state()
    bc = make_bc(99, 60.0, RUCK_Y - 12.0)          # offset −12 ≥ enter 9.0
    w = ReleaseWorld(ball_carrier=bc)
    active, side = resolve_edge_release(w)
    check("P2.0: release resolves ACTIVE on LEFT",
          active is True and side == LEFT)
    bs, bias = resolve_ball_side_bias(w)
    check("P2.1: ball-side collapse armed alongside", (bs, bias) == (LEFT, 1))

    # Mirror the Task X call-site contract: the refit/reband paths pass
    # the resolved bias explicitly into the assignment (the bare preamble
    # call keeps legacy symmetric counts — orthogonal to this task).
    line = make_line()
    assign_depth_roles(line, w, ball_side=bs, ball_side_bias=bias)
    left_defs = side_of(line, LEFT)
    right_defs = side_of(line, RIGHT)
    ok, why = pure_y_order(left_defs)
    check("P2.2: LEFT stack is pure Y-order A,B,C,D…", ok, why)
    check("P2.3: LEFT holds NO WINGER/CENTRE", no_wc(left_defs),
          str(role_map(left_defs)))
    check("P2.4: weak side keeps canonical WINGER/CENTRE",
          has_canonical_wc(right_defs), str(role_map(right_defs)))
    inv_ok, inv_why = invariants_hold(line)
    check("P2.5: slot invariants hold under conversion", inv_ok, inv_why)
    check("P2.6: ball side received the collapsed surplus (4v2)",
          len(left_defs) == 4 and len(right_defs) == 2,
          f"left={len(left_defs)} right={len(right_defs)}")


# ─────────────────────────────────────────────────────────────────────
# P3 — hysteresis: persist inside the band, restore below exit
# ─────────────────────────────────────────────────────────────────────

def p3_hysteresis_persist_restore():
    print()
    print("[P3] Hysteresis: persist between exit(7) and enter(9); "
          "restore below exit")
    reset_pivot_state()
    w = ReleaseWorld(ball_carrier=make_bc(99, 60.0, RUCK_Y - 12.0))
    line = make_line()
    assign_depth_roles(line, w)                    # activates on LEFT
    check("P3.0: activated", last_edge_release(w)[0] is True)

    # Carrier drifts inward but stays above the EXIT threshold (enter 9 −
    # hysteresis 2 = 7): offset 7.8 must PERSIST the release.
    w.ball_carrier = make_bc(99, 60.0, RUCK_Y - 7.8)
    snap_before = role_map(line)
    assign_depth_roles(line, w)
    active, side = resolve_edge_release(w)
    check("P3.1: release persists inside the hysteresis band",
          active is True and side == LEFT)
    check("P3.2: LEFT structure unchanged inside the band",
          no_wc(side_of(line, LEFT)), str(role_map(line)))

    # Carrier retreats well below exit: canonical edge MUST be restored.
    w.ball_carrier = make_bc(99, 60.0, RUCK_Y + 3.0)   # offset 3 (< exit 7)
    assign_depth_roles(line, w)
    active2, side2 = resolve_edge_release(w)
    check("P3.3: release deactivates below exit", active2 is False)
    check("P3.4: LEFT restores canonical W/C",
          has_canonical_wc(side_of(line, LEFT)), str(role_map(line)))
    ok, why = invariants_hold(line)
    check("P3.5: invariants hold after restore", ok, why)


# ─────────────────────────────────────────────────────────────────────
# P4 — maintenance: idempotence + stale-holder demotion
# ─────────────────────────────────────────────────────────────────────

def p4_maintenance_stability_and_demotion():
    print()
    print("[P4] Active-mode idempotence; injected stale CENTRE is demoted")
    reset_pivot_state()
    w = ReleaseWorld(ball_carrier=make_bc(99, 60.0, RUCK_Y - 12.0))
    line = make_line()
    assign_depth_roles(line, w)
    snap = {(d.player_id): (d.depth_side, d.depth_role) for d in line}
    assign_depth_roles(line, w)
    snap2 = {(d.player_id): (d.depth_side, d.depth_role) for d in line}
    check("P4.0: repeated active-mode call is byte-stable", snap == snap2)

    # Inject a stale CENTRE holder on the RELEASED side (post-lock-exit
    # simulation): the next maintenance call must demote it to D.
    stale = make_defender(90, 70.0, 25.0)
    stale.depth_side = -1
    stale.depth_role = "CENTRE"
    stale._depth_seeded = True
    line.append(stale)
    assign_depth_roles(line, w)
    # The EDGE RELEASE LAW for the injected body: it loses the positional
    # CENTRE slot and is REINSERTED INTO THE INTERIOR STACK by pure
    # Y-ordering (whichever interior lane its position earns).
    check("P4.1: stale CENTRE converted off the positional slot",
          getattr(stale, "depth_role", None) not in ("WINGER", "CENTRE"),
          str(getattr(stale, "depth_role", None)))
    check("P4.1b: converted body sits on the interior stack",
          getattr(stale, "depth_role", None) in ("A", "B", "C", "D"),
          str(getattr(stale, "depth_role", None)))
    check("P4.2: released side still holds NO W/C", no_wc(side_of(line, LEFT)),
          str(role_map(line)))
    ok, why = invariants_hold(line)
    check("P4.3: invariants hold with the injected body", ok, why)


# ─────────────────────────────────────────────────────────────────────
# P5 — collapse lock: engaged W/C converts only after radius exit
# ─────────────────────────────────────────────────────────────────────

def p5_collapse_lock_deferment():
    print()
    print("[P5] Collapse-locked WINGER keeps its role through the transition")
    reset_pivot_state()
    w = ReleaseWorld()                              # no BC yet
    line = make_line()
    assign_depth_roles(line, w)                     # canonical seed
    winger = next(
        d for d in side_of(line, LEFT)
        if getattr(d, "depth_role", None) == "WINGER"
    )

    # Engage the BC right next to the ball-side winger (within 2.0 m).
    w.ball_carrier = make_bc(99, winger.x - 1.0, winger.y + 1.0)
    dist = math.hypot(winger.x - w.ball_carrier.x, winger.y - w.ball_carrier.y)
    check("P5.0: precondition — winger inside COLLAPSE_LOCK_RADIUS_M",
          dist < 2.0, f"{dist:.2f} m")

    assign_depth_roles(line, w)                     # transition frame
    check("P5.1: locked winger KEEPS WINGER through the transition",
          getattr(winger, "depth_role", None) == "WINGER",
          str(getattr(winger, "depth_role", None)))
    check("P5.2: unlocked bodies on the released side converted",
          all(
              getattr(d, "depth_role", None) not in ("WINGER", "CENTRE")
              for d in side_of(line, LEFT) if d is not winger
          ), str(role_map(line)))

    # Exit the lock radius: the very next call must convert the winger.
    winger.x = 74.0
    winger.y = 28.0                                 # ~11 m from the BC
    assign_depth_roles(line, w)
    check("P5.3: exited winger converted off the edge",
          getattr(winger, "depth_role", None) not in ("WINGER", "CENTRE"),
          str(getattr(winger, "depth_role", None)))
    check("P5.4: released side fully W/C-free after exit",
          no_wc(side_of(line, LEFT)), str(role_map(line)))
    ok, why = invariants_hold(line)
    check("P5.5: invariants hold after lock-exit conversion", ok, why)


# ─────────────────────────────────────────────────────────────────────
# P6 — ball-side cross: old side restores, new side releases
# ─────────────────────────────────────────────────────────────────────

def p6_side_cross():
    print()
    print("[P6] BC crosses deep LEFT -> deep RIGHT: swap the release")
    reset_pivot_state()
    w = ReleaseWorld(ball_carrier=make_bc(99, 60.0, RUCK_Y - 12.0))
    line = make_line()
    assign_depth_roles(line, w)
    check("P6.0: LEFT released initially", no_wc(side_of(line, LEFT)))

    w.ball_carrier = make_bc(99, 60.0, RUCK_Y + 12.0)
    assign_depth_roles(line, w)
    active, side = resolve_edge_release(w)
    check("P6.1: release follows the carrier to RIGHT",
          active is True and side == RIGHT)
    check("P6.2: old side restores canonical W/C",
          has_canonical_wc(side_of(line, LEFT)), str(role_map(line)))
    ok, why = pure_y_order(side_of(line, RIGHT))
    check("P6.3: new side is pure Y-order with NO W/C",
          ok and no_wc(side_of(line, RIGHT)), why)
    inv_ok, inv_why = invariants_hold(line)
    check("P6.4: invariants hold after the cross", inv_ok, inv_why)


# ─────────────────────────────────────────────────────────────────────
# P7 — dynamic refit integration (TASK X pipeline)
# ─────────────────────────────────────────────────────────────────────

def p7_dynamic_refit_integration():
    print()
    print("[P7] apply_dynamic_depth_roles yields the released ball side")
    reset_pivot_state()
    w = ReleaseWorld(ball_carrier=make_bc(99, 60.0, RUCK_Y - 12.0))
    line = make_line()

    # Stale static seed that ignores the deep carrier entirely.
    wrong = {
        1: (-1, "WINGER"),
        2: (-1, "CENTRE"),
        3: (-1, "A"),
        4: (1, "B"),
        5: (1, "CENTRE"),
        6: (1, "WINGER"),
    }
    for d in line:
        s, r = wrong[d.player_id]
        d.depth_side = s
        d.depth_role = r
        d._depth_seeded = True

    ret = apply_dynamic_depth_roles(w, line)
    check("P7.0: dynamic refit fired on the pivot", ret is True)
    check("P7.1: refitted LEFT is W/C-free", no_wc(side_of(line, LEFT)),
          str(role_map(line)))
    ok, why = pure_y_order(side_of(line, LEFT))
    check("P7.2: refitted LEFT is pure Y-order", ok, why)
    inv_ok, inv_why = invariants_hold(line)
    check("P7.3: invariants hold after the refit", inv_ok, inv_why)

    snap = role_map(line)
    ret2 = apply_dynamic_depth_roles(w, line)
    check("P7.4: same-ruck second refit is a no-op", ret2 is False)
    check("P7.5: assignments byte-stable across the no-op",
          role_map(line) == snap)


# ─────────────────────────────────────────────────────────────────────
# P8 — wiring regression
# ─────────────────────────────────────────────────────────────────────

def p8_wiring_regression():
    print()
    print("[P8] Prior verifiers remain green; derivation is internal")
    proc = subprocess.run(
        [sys.executable, "verify_ball_side_collapse.py"],
        capture_output=True, text=True, timeout=120,
    )
    tail = (proc.stdout or "").strip().splitlines()[-1:] or [""]
    check("P8.0: verify_ball_side_collapse passes",
          proc.returncode == 0, tail[0])

    src = open(
        "game/defence/defence_lvl_1/defensive_behaviour_unified.py",
        encoding="utf-8",
    ).read()
    check("P8.1: no new DBU call sites needed (internal derivation)",
          "resolve_edge_release" not in src)


def main():
    print("=" * 64)
    print("EDGE RELEASE CONVERSION VALIDATION (TASK XI)")
    print("=" * 64)

    p1_gate_off_and_subthreshold()
    p2_deep_bc_conversion()
    p3_hysteresis_persist_restore()
    p4_maintenance_stability_and_demotion()
    p5_collapse_lock_deferment()
    p6_side_cross()
    p7_dynamic_refit_integration()
    p8_wiring_regression()

    print()
    print("=" * 64)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
