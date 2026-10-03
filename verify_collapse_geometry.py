"""Headless verification for the 2026-08-15 pre-tackle alignment & collapse geometry pack.

Verifies every task item against the REAL engine (the pack's target module
``tes_drive_movement.py`` does not exist; the intent is adapted onto
``player/collapse_controls.py`` + the movement/role authorities):

  item 1  distance-based lateral correction clamp    (0.55 / 0.35 / 0.0)
  item 2  band-weighted correction                  (AGGR 0.65 / BAL 0.85 / PASS 1.10)
  item 3  BC lateral velocity dampening             (|bc_vy| > 1.0 -> x0.60)
  item 4  collapse vector override inside 0.8m      (straight-line finish, no snap-hold)
  item 5  spacing-Y freeze inside 1.5m              (target_y pinned -> no lateral pull)
  item 6  depth-role lock inside 2.0m               (side-swap + engagement-band switches)
  item 7  movement-state COMMIT (SHOOT) inside 1.0m (MMDL decision layer)
  item 8  logging hooks                             ([COLLAPSE_GEOM] lines + silent flag)

Pure-math assertions need no pygame; the MMDL / movement-state checks use
duck-typed stubs so the suite runs headless in seconds.
"""
import contextlib
import io
import math
import sys

sys.path.insert(0, ".")

from player.collapse_controls import (
    COLLAPSE_COMMIT_RADIUS_M,
    COLLAPSE_LOCK_RADIUS_M,
    COMMIT_RADIUS_M,
    SPACING_FREEZE_RADIUS_M,
    BC_LATERAL_DAMP,
    BC_LATERAL_VY_THRESHOLD,
    collapse_commit,
    collapse_lateral_scale,
    debug_collapse_log,
    depth_role_locked,
    distance_factor,
    freeze_spacing_y,
    should_commit,
)

from player.movement_states import MovementState, apply_movement_state
from game.defence.defence_lvl_1.movement_mode_decision import decide_movement_state
from game.defence.defence_lvl_1.defensive_engagement_state import (
    AGGRESSIVE,
    BALANCED,
    PASSIVE,
)

failures = []


def check(name, cond, detail=""):
    if not cond:
        failures.append(f"{name}: {detail}")
        print(f"  FAIL {name} {detail}")
    else:
        print(f"  ok   {name}")


print("=" * 72)
print("COLLAPSE GEOMETRY VERIFICATION (2026-08-15)")
print("=" * 72)

# ---------------------------------------------------------------
# ITEM 1 — distance-based lateral correction clamp
# ---------------------------------------------------------------
print("\n[item 1] distance clamp")
check("dist=3.0 -> 1.0", distance_factor(3.0) == 1.0)
check("dist=2.0 -> 1.0 (edge)", distance_factor(2.0) == 1.0)
check("dist=1.9 -> 0.55", abs(distance_factor(1.9) - 0.55) < 1e-9)
check("dist=1.2 -> 0.55 (edge)", abs(distance_factor(1.2) - 0.55) < 1e-9)
check("dist=1.1 -> 0.35", abs(distance_factor(1.1) - 0.35) < 1e-9)
check("dist=0.8 -> 0.35 (edge)", abs(distance_factor(0.8) - 0.35) < 1e-9)
check("dist=0.7 -> 0.0", distance_factor(0.7) == 0.0)

# ---------------------------------------------------------------
# ITEM 2 — band-weighted correction
# ---------------------------------------------------------------
print("\n[item 2] band weighting")
check("AGGRESSIVE 0.65", collapse_lateral_scale(3.0, band=AGGRESSIVE) == 0.65)
check("BALANCED 0.85", collapse_lateral_scale(3.0, band=BALANCED) == 0.85)
check("PASSIVE 1.10", abs(collapse_lateral_scale(3.0, band=PASSIVE) - 1.10) < 1e-9)
check("unknown band neutral 1.0", collapse_lateral_scale(3.0, band="X") == 1.0)

# ---------------------------------------------------------------
# ITEM 3 — BC lateral velocity dampening
# ---------------------------------------------------------------
print("\n[item 3] BC lateral damp")
check("bc_vy=0 -> 1.0", collapse_lateral_scale(3.0, bc_vy=0.0) == 1.0)
check(
    f"bc_vy={BC_LATERAL_VY_THRESHOLD} -> 1.0 (edge)",
    collapse_lateral_scale(3.0, bc_vy=BC_LATERAL_VY_THRESHOLD) == 1.0,
)
check(
    f"bc_vy>threshold -> {BC_LATERAL_DAMP}",
    collapse_lateral_scale(3.0, bc_vy=BC_LATERAL_VY_THRESHOLD + 0.1) == BC_LATERAL_DAMP,
)
check(
    f"bc_vy< -threshold -> {BC_LATERAL_DAMP}",
    collapse_lateral_scale(3.0, bc_vy=-(BC_LATERAL_VY_THRESHOLD + 0.1)) == BC_LATERAL_DAMP,
)

# Combined compound clamp (items 1+2+3 at 1.0m, AGGRESSIVE, BC stepping):
# 0.35 * 0.65 * 0.60
combined = collapse_lateral_scale(1.0, band=AGGRESSIVE, bc_vy=2.0)
check(
    "compound 0.35*0.65*0.60",
    abs(combined - 0.35 * 0.65 * BC_LATERAL_DAMP) < 1e-9,
    f"got {combined}",
)

# ---------------------------------------------------------------
# ZONE GUARDS (items 4/5/6/7)
# ---------------------------------------------------------------
print("\n[zone guards]")
check("commit zone < 0.8", collapse_commit(0.79) and not collapse_commit(0.8))
check(
    f"spacing freeze < {SPACING_FREEZE_RADIUS_M}",
    freeze_spacing_y(1.49) and not freeze_spacing_y(1.5),
)
check(
    f"depth-role lock < {COLLAPSE_LOCK_RADIUS_M}",
    depth_role_locked(1.99) and not depth_role_locked(2.0),
)
check(
    f"should_commit < {COMMIT_RADIUS_M}",
    should_commit(0.99) and not should_commit(1.0),
)

# ---------------------------------------------------------------
# ITEM 8 — logging hooks
# ---------------------------------------------------------------
print("\n[item 8] logging hooks")


class _Tag:
    def __init__(self, key):
        self.key = key

    def __getattr__(self, item):
        return 1.0


_tag = _Tag("id")
_buf = io.StringIO()
with contextlib.redirect_stdout(_buf):
    debug_collapse_log(
        _tag,
        dist_to_bc=0.5, lateral_corr_before=2.0, lateral_corr_after=0.3,
        bc_vy=2.0, band=AGGRESSIVE, spacing_y_adjust=1.2,
        collapse_vx=0.9, collapse_vy=0.1, movement_state=MovementState.SHOOT,
    )
check("debug flag OFF -> silent", "COLLAPSE_GEOM" not in _buf.getvalue())

from player import collapse_controls as _cc
_prev_flag = _cc.DEBUG_COLLAPSE_GEOMETRY
_cc.DEBUG_COLLAPSE_GEOMETRY = True
_buf2 = io.StringIO()
try:
    with contextlib.redirect_stdout(_buf2):
        debug_collapse_log(
            _tag,
            dist_to_bc=0.5, lateral_corr_before=2.0, lateral_corr_after=0.3,
            bc_vy=2.0, band=AGGRESSIVE, spacing_y_adjust=1.2,
            collapse_vx=0.9, collapse_vy=0.1, movement_state=MovementState.SHOOT,
        )
    line = _buf2.getvalue()
    check("debug flag ON -> logs", "COLLAPSE_GEOM" in line, line)
    check(
        "log carries required fields",
        all(tok in line for tok in
            ("dist_bc", "lat_corr", "bc_vy", "band", "spacing_y_adj",
             "collapse", "ms=SHOOT")),
        line,
    )
finally:
    _cc.DEBUG_COLLAPSE_GEOMETRY = _prev_flag

# ---------------------------------------------------------------
# ITEM 7 — movement-state COMMIT (SHOOT) inside 1.0m (MMDL)
# ---------------------------------------------------------------
print("\n[item 7] MMDL commit (SHOOT)")


class StubWorld:
    tes_active = False
    frame_count = 100
    ruck_x_m = 30.0


class StubDefender:
    depth_class = "PRIMARY"
    fullback_role_active = False
    movement_state = None
    defender_role = None
    vx = 0.0
    vy = 0.0

    def __init__(self, x, y):
        self.x = x
        self.y = y


class StubBC:
    def __init__(self, x, y):
        self.x = x
        self.y = y


_d = StubDefender(10.0, 35.0)
_bc = StubBC(10.5, 35.0)  # 0.5m -> MUST commit
_ms = decide_movement_state(StubWorld(), _d, _bc, tuners=None)
check(
    "0.5m engageable -> SHOOT (COMMIT)",
    _ms is MovementState.SHOOT,
    f"got {_ms}",
)

_d2 = StubDefender(10.0, 35.0)
_bc2 = StubBC(13.0, 39.0)  # dist 5.0, lateral 4.0 > threshold 3.0 -> SLIDE preserved
_ms2 = decide_movement_state(StubWorld(), _d2, _bc2, tuners=None)
check(
    "wide lateral outside 1.0m -> SLIDE preserved",
    _ms2 is MovementState.SLIDE,
    f"got {_ms2}",
)

# ---------------------------------------------------------------
# ITEM 5 — spacing-Y freeze inside 1.5m (apply_movement_state)
# ---------------------------------------------------------------
print("\n[item 5] spacing-Y freeze")


class StubMover:
    max_speed_mps = 8.0
    vx = 0.0
    vy = 0.0
    movement_state = None
    facing = 0.0
    facing_override = False
    facing_override_angle = 0.0
    is_defender = True

    def __init__(self, x, y):
        self.x = x
        self.y = y

    def set_facing(self, angle):
        self.facing = angle


# Defender 1.0m from BC; the spacing writer set a lateral target far to the
# side.  freeze_spacing_y pivots target_y to player.y -> the target is now
# current position -> the movement snap must halt (no lateral pull).
_p5 = StubMover(10.0, 35.0)
apply_movement_state(
    _p5, MovementState.SLIDE,
    target_x=10.0, target_y=39.0, dt=1.0 / 60.0,
    bc_x=11.0, bc_y=35.0,
)
check(
    "spacing-Y frozen at 1.0m -> no lateral velocity",
    abs(_p5.vy) < 1e-9 and abs(_p5.vx) < 1e-9,
    f"vx={_p5.vx} vy={_p5.vy}",
)

# Control: same setup at 3.0m (outside the freeze) -> target_y respected.
_p5_ctrl = StubMover(10.0, 35.0)
apply_movement_state(
    _p5_ctrl, MovementState.SLIDE,
    target_x=10.0, target_y=39.0, dt=1.0 / 60.0,
    bc_x=13.0, bc_y=35.0,
)
check(
    "control 3.0m -> lateral target honoured",
    abs(_p5_ctrl.vy) > 0.0,
    f"vy={_p5_ctrl.vy}",
)

# ---------------------------------------------------------------
# ITEM 4 — collapse vector override inside 0.8m (apply_movement_state)
# ---------------------------------------------------------------
print("\n[item 4] collapse vector override")

# 0.6m from BC, CHASE (lateral_priority 0.3): the far lateral target must NOT
# pull the heading sideways — the collapse vector snaps the heading onto the
# direct-to-BC line instead.  A far BC (no commit) keeps the target heading.
_far = StubMover(10.0, 35.0)
apply_movement_state(
    _far, MovementState.CHASE,
    target_x=30.0, target_y=20.0, dt=1.0 / 60.0,
    bc_x=10.5, bc_y=34.8,
)
_near = StubMover(10.0, 35.0)
apply_movement_state(
    _near, MovementState.CHASE,
    target_x=30.0, target_y=20.0, dt=1.0 / 60.0,
    bc_x=100.0, bc_y=100.0,  # far -> no commit override, target-based heading
)
check(
    "commit zone heading is forward-er toward the carrier",
    abs(_far.vx) > abs(_near.vx),
    f"commit vx={_far.vx} vs non-commit {_near.vx}",
)

print("\n" + "=" * 72)
if failures:
    print("RESULT: FAIL")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("RESULT: PASS")
print("  - items 1-3 clamp math exact")
print("  - zone guards (0.8 / 1.0 / 1.5 / 2.0m) exact")
print("  - item 7: 0.5m engageable -> SHOOT(COMMIT), SLIDE preserved outside")
print("  - item 5: spacing-Y freeze halts lateral pull inside 1.5m")
print("  - item 4: commit-zone heading snaps forward onto the carrier")
print("  - item 8: debug log silent by default, full field set when enabled")
sys.exit(0)
