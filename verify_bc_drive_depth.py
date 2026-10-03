"""verify_bc_drive_depth.py — BC-anchored drive depth verifier (2026-09-05).

Confirms the defensive-line X depth reference is the BALL CARRIER during the
drive (OPEN_PLAY with an active ball carrier) and reverts to the RUCK when the
drive ends (no BC, or geometry disabled for a non-OPEN_PLAY phase).

CONTRACT (user verdict, Option A):
    target_x = base_x + WIDTH_OFFSETS[depth_role]
    where base_x = bc.x during the drive, and ruck_x otherwise.

Pre-fix, compute_role_targets always used ruck_x as the base, so line
defenders took their depth off the ruck instead of the ball carrier.

_run:  python verify_bc_drive_depth.py
Exit 0 on success; prints a PASS/FAIL line per assertion.
"""
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player
from game.defence.defence_lvl_1.defensive_geometry import (
    DefensiveGeometry,
    geometry_active,
)
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners

RUCK_X = 60.0
RUCK_Y = 35.0

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" - " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


class MiniWorld:
    """World stub exposing the ruck + pipeline-frame contract fields."""

    def __init__(self, ruck_x=RUCK_X, ruck_y=RUCK_Y):
        self.ruck_x_m = ruck_x
        self.ruck_y_m = ruck_y
        self.def_line_width_m = 40.0

        # ── Frame-pipeline contract (TASK 2 / TASK 8) ──
        # depth_roles_frame == frame_count => assign_depth_roles is a NO-OP
        # (we pre-seed depth_role/depth_side so the assignment guard must not
        # re-bootstrap and re-order them mid-test).
        # frame_geometry_ready + frame_geometry_frame + frame_spacing_targets
        # => frame_spacing_covers() True so compute_role_targets reads Y from
        # the published dict instead of recomputing the count-lane chain.
        self.frame_count = 0
        self.depth_roles_frame = 0
        self.frame_geometry_ready = True
        self.frame_geometry_frame = 0
        self.frame_spacing_targets = {}

        # Phase control for geometry_active().
        self.phase = "OPEN_PLAY"
        self.sub_state = None
        self.ball_carrier = None

        self.open_play_engine = None


def make_defender(pid, x, y, depth_role, depth_side):
    d = Player(x=x, y=y, player_id=pid)
    d.depth_role = depth_role
    d.depth_side = depth_side
    d._depth_seeded = True
    d.fullback_role_active = False
    d.tackle_state_active = False
    d.frozen = False
    d.freeze_timer = 0.0
    d.max_speed_mps = 8.3
    d.max_defensive_speed_mps = 8.3
    return d


def make_players():
    # Six line defenders: W/C/A per side (canonical ladder).
    return [
        make_defender(1, 60.0, 12.0, "WINGER", -1),
        make_defender(2, 60.0, 20.0, "CENTRE", -1),
        make_defender(3, 60.0, 28.0, "A", -1),
        make_defender(4, 60.0, 42.0, "A", 1),
        make_defender(5, 60.0, 50.0, "CENTRE", 1),
        make_defender(6, 60.0, 58.0, "WINGER", 1),
    ]


def make_bc(x, y):
    bc = Player(x=float(x), y=float(y), player_id=100)
    bc.attack_role = "ball_carrier"
    return bc


def wire_spacing(world, defenders):
    """Publish frame spacing Y = ruck_y for every defender (Y not under test)."""
    world.frame_spacing_targets = {
        id(d): world.ruck_y_m for d in defenders
    }


def main():
    print("=" * 64)
    print("BC-ANCHORED DRIVE DEPTH (2026-09-05)")
    print("=" * 64)

    tuners = DefenceTuners()
    offsets = tuners.WIDTH_OFFSETS

    # ─────────────────────────────────────────────────────────────
    # TEST 1 — DRIVE ACTIVE (OPEN_PLAY + BC): base_x = bc.x
    # ─────────────────────────────────────────────────────────────
    print("\n[1] drive active (OPEN_PLAY + ball carrier) -> target_x = bc.x + WIDTH_OFFSETS[role]")
    w1 = MiniWorld()
    bc = make_bc(72.0, 32.0)   # BC ahead of the ruck (a live drive)
    w1.ball_carrier = bc
    defenders1 = make_players()
    wire_spacing(w1, defenders1)

    geom = DefensiveGeometry(tuners)

    # Sanity: geometry_active must be True for the drive.
    check("1.0 geometry_active(OPEN_PLAY) == True", geometry_active(w1))

    targets1 = geom.compute_role_targets(w1, defenders1)
    for d in defenders1:
        role = getattr(d, "depth_role", "D") or "D"
        expected = bc.x + offsets.get(role, offsets.get("D", 5.0))
        got = targets1[id(d)][0]
        check(
            "1.role=%s target_x == bc.x + WIDTH_OFFSETS[%s]" % (role, role),
            abs(got - expected) < 1e-6,
            "target_x=%.4f expected=%.4f (bc.x=%.4f)" % (got, expected, bc.x),
        )

    # ─────────────────────────────────────────────────────────────
    # TEST 2 — NO BALL CARRIER (drive absent): base_x = ruck_x
    # ─────────────────────────────────────────────────────────────
    print("\n[2] no ball carrier -> target_x = ruck_x + WIDTH_OFFSETS[role]")
    w2 = MiniWorld()
    w2.ball_carrier = None
    defenders2 = make_players()
    wire_spacing(w2, defenders2)

    targets2 = geom.compute_role_targets(w2, defenders2)
    for d in defenders2:
        role = getattr(d, "depth_role", "D") or "D"
        expected = RUCK_X + offsets.get(role, offsets.get("D", 5.0))
        got = targets2[id(d)][0]
        check(
            "2.role=%s target_x == ruck_x + WIDTH_OFFSETS[%s]" % (role, role),
            abs(got - expected) < 1e-6,
            "target_x=%.4f expected=%.4f" % (got, expected),
        )

    # ─────────────────────────────────────────────────────────────
    # TEST 3 — DRIVE ENDED (BC present but geometry disabled): base_x = ruck_x
    # ─────────────────────────────────────────────────────────────
    print("\n[3] drive ended (BC present, geometry disabled) -> target_x = ruck_x + WIDTH_OFFSETS[role]")
    w3 = MiniWorld()
    w3.ball_carrier = make_bc(72.0, 32.0)
    w3.phase = "PTB"   # GEOMETRY_DISABLED_PHASES member -> geometry_active False
    defenders3 = make_players()
    wire_spacing(w3, defenders3)

    check("3.0 geometry_active(PTB) == False", not geometry_active(w3))

    targets3 = geom.compute_role_targets(w3, defenders3)
    for d in defenders3:
        role = getattr(d, "depth_role", "D") or "D"
        expected = RUCK_X + offsets.get(role, offsets.get("D", 5.0))
        got = targets3[id(d)][0]
        check(
            "3.role=%s target_x == ruck_x + WIDTH_OFFSETS[%s]" % (role, role),
            abs(got - expected) < 1e-6,
            "target_x=%.4f expected=%.4f" % (got, expected),
        )

    print("\n" + "=" * 64)
    if failures:
        print("RESULT: %d FAILURE(S): %s" % (len(failures), failures))
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
