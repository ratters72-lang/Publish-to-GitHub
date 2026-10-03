"""probe_depth_boundary.py — LIVE EVIDENCE for the depth-target boundary.

Drives the REAL writers with real ``Player`` bodies and prints, per carrier
position, the UNCLAMPED value the old law would have published next to the
value the patched engine actually publishes:

    raw  = bc.x + WIDTH_OFFSETS[depth_role]     (the pre-fix law, reported
                                                 for comparison only)
    stamp= d.role_depth_target_x after stamp_drive_depth_x
    tes  = d.target_x after apply_tes_depth_authority (TES drive chamber)
    lock = d.depth_lock_x after _refresh_depth_lock_x (the positional lock)

The probe also reports the number of boundary violations the audit sees and
the number the enforcement pass repairs, and asserts the headline invariant:
NO non-fullback depth value published anywhere exceeds the team's own
tryline (+X = 110), while the fullback is left free in-goal.

Run:  python probe_depth_boundary.py
"""
import os
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import sys
from types import SimpleNamespace

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import DefenderRole, Player
from game.defence.defence_lvl_1 import depth_boundary_guard as dbg
from game.defence.defence_lvl_1 import defensive_depth_roles as ddr
from game.defence.defence_lvl_1 import tes_depth_authority as tda
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.anchor_authority import RIGHT_TRYLINE_X

TUNERS = DefenceTuners()
TRYLINE = float(RIGHT_TRYLINE_X)
OFFSETS = dict(TUNERS.WIDTH_OFFSETS)
ROLES = ("A", "B", "C", "D", "CENTRE", "WINGER")

# Carrier positions swept across the point where the raw law crosses the
# tryline.  The crossing is bc.x = 110 - 10 = 100 for the WINGER.
CARRIER_XS = (90.0, 99.0, 100.0, 101.0, 105.0, 108.0, 110.0, 115.0)

FAILURES = []


def fail(msg):
    FAILURES.append(msg)
    print("  !! %s" % msg)


class _Sub:
    def __init__(self, value):
        self.value = value


def make_world(bc_x):
    """A TACKLE_DRIVE world stub (drive phase => bc.x-anchored depth law)."""
    return SimpleNamespace(
        sub_state=_Sub("tackle_drive"),
        _ope_phase_label="TACKLE_DRIVE",
        ball_carrier=SimpleNamespace(x=float(bc_x), y=35.0, vx=0.0, vy=0.0),
        ruck_x_m=float(bc_x) - 3.0,
        ruck_y_m=35.0,
        frame_count=1,
        dt=1.0 / 60.0,
        open_play_engine=SimpleNamespace(def_tuners=TUNERS),
    )


def make_body(pid, role, x=60.0, y=35.0):
    d = Player(x=float(x), y=float(y), player_id=pid)
    d.fullback_role_active = False
    d.depth_role = role
    d.role_depth_target_x = None
    d.role_depth_target_y = None
    d.depth_locked = True
    d.depth_lock_x = float(x)
    d.defender_role = DefenderRole.DEF_RETREAT_10
    d.target_x = float(x)
    d.target_y = float(y)
    d.move_speed = 0.0
    return d


def make_fullback(pid, x=105.0, y=35.0):
    d = Player(x=float(x), y=float(y), player_id=pid)
    d.fullback_role_active = True
    d.depth_role = None
    d.role_depth_target_x = None
    d.depth_locked = True
    d.depth_lock_x = float(x)
    d.defender_role = DefenderRole.DEF_RETREAT_10
    d.target_x = float(x)
    d.target_y = float(y)
    return d


def section(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def probe_per_role():
    section("[A] PER-ROLE: raw law vs the published depth stamp (carrier "
            "driven toward the +X tryline)")
    print("  %-9s %-8s %-9s %-9s %-9s" % (
        "bc.x", "role", "raw", "stamp", "clamped?"))
    print("  " + "-" * 52)

    clamped_rows = 0
    published = []
    for bc_x in CARRIER_XS:
        world = make_world(bc_x)
        for role in ROLES:
            body = make_body(100 + ROLES.index(role), role)
            raw = float(bc_x) + OFFSETS[role]
            ddr.stamp_drive_depth_x(world, [body])
            stamp = float(body.role_depth_target_x)
            published.append(stamp)
            did = abs(stamp - raw) > 1e-9
            if did:
                clamped_rows += 1
            print("  %-9.1f %-8s %-9.2f %-9.2f %-9s" % (
                bc_x, role, raw, stamp, "YES" if did else "-"))
            if stamp > TRYLINE + 1e-9:
                fail("bc.x=%.1f role=%s published %.2f > tryline %.2f"
                     % (bc_x, role, stamp, TRYLINE))

    print("\n  rows clamped: %d / %d" % (clamped_rows, len(published)))
    print("  max published stamp: %.2f (tryline %.2f)"
          % (max(published), TRYLINE))
    if max(published) > TRYLINE + 1e-9:
        fail("a published stamp exceeded the tryline")
    if clamped_rows == 0:
        fail("the clamp never bound — the probe proved nothing")


def probe_tes_authority():
    section("[B] TES DEPTH AUTHORITY: the X it PUBLISHES inside the drive "
            "chamber")
    print("  %-9s %-8s %-9s %-9s %-9s" % (
        "bc.x", "role", "raw", "target_x", "clamped?"))
    print("  " + "-" * 52)

    for bc_x in (105.0, 110.0, 115.0):
        world = make_world(bc_x)
        for role in ROLES:
            body = make_body(300 + ROLES.index(role), role, x=60.0)
            raw = float(bc_x) + OFFSETS[role]
            granted = tda.apply_tes_depth_authority(world, [body], TUNERS)
            if not granted:
                print("  %-9.1f %-8s %-9.2f %-9s %-9s" % (
                    bc_x, role, raw, "(not obligated)", "-"))
                continue
            got = float(body.target_x)
            did = abs(got - raw) > 1e-9
            print("  %-9.1f %-8s %-9.2f %-9.2f %-9s" % (
                bc_x, role, raw, got, "YES" if did else "-"))
            if got > TRYLINE + 1e-9:
                fail("authority published %.2f > tryline %.2f (bc.x=%.1f "
                     "role=%s)" % (got, TRYLINE, bc_x, role))


def probe_positional_lock():
    section("[C] POSITIONAL LOCK (defence_manager._refresh_depth_lock_x): "
            "the glide target a non-fullback body is walked toward")
    from game.managers.defence_manager import _refresh_depth_lock_x

    print("  %-9s %-8s %-9s %-9s" % ("bc.x", "role", "lock", "legal?"))
    print("  " + "-" * 40)

    world = make_world(110.0)
    for role in ROLES:
        body = make_body(500 + ROLES.index(role), role, x=70.0)
        body.role_depth_target_x = TRYLINE + 10.0     # illegal injection
        _refresh_depth_lock_x(body, world)
        lock = float(body.depth_lock_x)
        print("  %-9.1f %-8s %-9.2f %-9s" % (
            110.0, role, lock, "YES" if lock <= TRYLINE + 1e-9 else "NO"))
        if lock > TRYLINE + 1e-9:
            fail("lock %.2f > tryline (role=%s)" % (lock, role))

    # The fullback exemption is a POSITIVE requirement, not a loophole: its
    # backfield anchor is intentionally allowed slightly in-goal.
    fb = make_fullback(900, x=40.0)
    fb.role_depth_target_x = TRYLINE + 2.0
    _refresh_depth_lock_x(fb, world)
    print("\n  fullback (EXEMPT): lock=%.2f (legal past the tryline)"
          % float(fb.depth_lock_x))
    if abs(float(fb.depth_lock_x) - (TRYLINE + 2.0)) > 1e-9:
        fail("the fullback's in-goal anchor was clamped (it must be exempt)")


def probe_enforcement_pass():
    section("[D] ENFORCEMENT PASS on a full 13-body line with illegal "
            "values injected by an out-of-law writer")
    world = make_world(112.0)
    line = []
    for i, role in enumerate(ROLES):
        d = make_body(700 + i, role, x=60.0)
        d.role_depth_target_x = 112.0 + OFFSETS[role]   # illegal injection
        d.depth_lock_x = 112.0 + OFFSETS[role]
        line.append(d)
    fb = make_fullback(799, x=100.0)
    fb.role_depth_target_x = TRYLINE + 11.0             # intentionally legal
    line.append(fb)

    violations = dbg.audit_depth_boundary(world, line, TUNERS)
    repaired = dbg.apply_depth_boundary_guard(world, line, TUNERS)
    after = dbg.audit_depth_boundary(world, line, TUNERS)

    print("  violations before: %d" % len(violations))
    for row in violations:
        print("    %s" % row)
    print("  bodies repaired  : %d" % len(repaired))
    print("  violations after : %d" % len(after))
    print("  max non-FB stamp : %.2f" % max(
        float(d.role_depth_target_x) for d in line
        if not getattr(d, "fullback_role_active", False)))
    print("  fullback stamp   : %.2f (left in-goal, exempt)"
          % float(fb.role_depth_target_x))

    if after:
        fail("violations survive the enforcement pass: %s" % after)
    # The audit reports ONE ROW PER VIOLATING ATTRIBUTE (a body with both an
    # illegal stamp and an illegal lock yields two rows); the enforcement
    # pass returns ONE ENTRY PER REPAIRED BODY.  Compare distinct body ids.
    violating_ids = set()
    for row in violations:
        for token in row.split():
            if token.startswith("id="):
                violating_ids.add(token)
    repaired_ids = {"id=%s" % getattr(d, "player_id", "?") for d in repaired}
    print("  distinct bodies repaired: %d (from %d violation rows)"
          % (len(repaired_ids), len(violations)))
    if repaired_ids != violating_ids:
        fail("repaired ids %s != violating ids %s"
             % (sorted(repaired_ids), sorted(violating_ids)))
    if abs(float(fb.role_depth_target_x) - (TRYLINE + 11.0)) > 1e-9:
        fail("the enforcement pass clamped the exempt fullback")


def probe_equivalence():
    section("[E] EQUIVALENCE: the same clamp the 10 m retreat protection "
            "has always used")
    from game.defence.defence_lvl_1.anchor_authority import (
        defensive_line_anchor_m,
    )
    world = make_world(108.0)
    world.ruck_x_m = 108.0
    anchor = defensive_line_anchor_m(world)
    body = make_body(1, "WINGER")
    depth = dbg.clamp_depth_target_x(world, body, 108.0 + 10.0, TUNERS)
    print("  ruck/anchor at x=108: retreat anchor = %.2f "
          "(raw ruck_x + 10 = 118.00)" % anchor)
    print("  depth target at bc.x=108, WINGER = %.2f "
          "(raw bc.x + 10 = 118.00)" % depth)
    if abs(anchor - depth) > 1e-9:
        fail("the two protections disagree at the boundary "
             "(anchor=%.2f depth=%.2f)" % (anchor, depth))


def main():
    print("DEPTH-TARGET BOUNDARY PROBE")
    print("defending team's own tryline: +X = %.1f  (field envelope %.1f..%.1f)"
          % (TRYLINE, dbg.depth_boundary_envelope()[0],
             dbg.depth_boundary_envelope()[1]))
    print("the old law: depth X = bc.x + WIDTH_OFFSETS[role]  "
          "(WINGER offset %.1f => crosses the tryline at bc.x = %.1f)"
          % (OFFSETS["WINGER"], TRYLINE - OFFSETS["WINGER"]))

    probe_per_role()
    probe_tes_authority()
    probe_positional_lock()
    probe_enforcement_pass()
    probe_equivalence()

    print("\n" + "=" * 78)
    if FAILURES:
        print("PROBE RESULT: %d FAILURE(S)" % len(FAILURES))
        for msg in FAILURES:
            print("  - %s" % msg)
        return 1
    print("PROBE RESULT: the boundary invariant holds — no non-fullback "
          "depth target or positional lock could be produced beyond the "
          "team's own tryline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
