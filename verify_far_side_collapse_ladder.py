"""verify_far_side_collapse_ladder.py — Focused far-side ladder verifier (2026-09-07).

Reproduces the "far-side width loss / unintended demotions" defect before any
fix is applied.  Backs the analysis that the open/far side loses its ruck-
adjacent A pivot when the ball-side count bias drops it to n=2.

The defect: in ``_bootstrap_side_roles`` the W/C ladder is gated on
``is_short_side``.  The short side collapses n=2 → ``WINGER + A`` (A pivot
guaranteed), but the OPEN side leaves n=2 as ``WINGER + CENTRE``, then
``_ensure_exactly_one_a`` cannot promote an A because both defenders are W/C
(it only promotes from the non-W/C pool, which is empty).  The far/open side
then has no ruck-adjacent hinge → spacing loses its inner anchor → width loss.

Run:  python verify_far_side_collapse_ladder.py
Exit 0 on success; prints a PASS/FAIL line per assertion.
"""
import sys
import types

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_split_spacing import (
    decide_side_counts,
    resolve_short_side,
    side_widths,
    LEFT,
    RIGHT,
)
from game.defence.defence_lvl_1.defensive_depth_roles import (
    _get_role_anchors,
    _bootstrap_side_roles,
    _maintain_side,
)

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" - " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def mk(i, y, role=None):
    d = types.SimpleNamespace()
    d.player_id = i
    d.y = float(y)
    d.x = 68.0
    d.depth_role = role
    d.depth_side = -1
    d.fullback_role_active = False
    d.defender_role = None
    d.tackle_state_active = False
    d._depth_seeded = True if role is not None else False
    d._force_innermost_seed = False
    return d


def make_world(ruck_y, ruck_x=60.0):
    w = types.SimpleNamespace()
    w.ruck_y_m = ruck_y
    w.ruck_x_m = ruck_x
    w.def_line_width_m = 40.0
    w.ruck_established = True
    w.governor_width_active = False
    w.ball_carrier = None
    w.sub_state = "open_play"
    w.just_left_kickoff_timer = 0.0
    w.kickoff_side_sign = None
    w._last_count_split = None
    return w


def make_bc(y):
    return types.SimpleNamespace(x=70.0, y=float(y))


def side_role_map(side_defs):
    return sorted(
        ((d.player_id, getattr(d, "depth_role", None)) for d in side_defs),
        key=lambda p: p[0],
    )


# ─────────────────────────────────────────────────────────────
# TEST 1 — reproduce the OPEN-side n=2 bootstrap no-A defect
# ─────────────────────────────────────────────────────────────
print("=" * 64)
print("TEST 1 — OPEN side (is_short_side=False) n=2 bootstrap")
print("=" * 64)
world = make_world(35.0)
tuners = DefenceTuners()
left_width, right_width = side_widths(world, tuners)
anchors = _get_role_anchors(60.0, 35.0, left_width, right_width, tuners)

# Open side, n=2: the two widest Y (y=55.0 and y=62.0).  The count-aware
# ladder collapses n=2 to WINGER + A (skip CENTRE) so the ruck-adjacent A
# pivot always exists — the far-side fix.
d0 = mk(0, 62.0)   # widest -> WINGER
d1 = mk(1, 55.0)   # second widest -> A (was CENTRE pre-fix; no-A defect)
out = _bootstrap_side_roles([d0, d1], 1, 35.0, anchors, is_short_side=False)
print("  open n=2 roles:", side_role_map([d0, d1]))
check("OPEN n=2 bootstrap assigns WINGER to widest", d0.depth_role == "WINGER",
      "got %r" % d0.depth_role)
# POST-FIX: n=2 open side must be WINGER + A, NEVER WINGER + CENTRE (which
# left the far side without an A hinge pivot).
check("OPEN n=2 bootstrap assigns A to 2nd (FIXED: not CENTRE)",
      d1.depth_role == "A", "got %r (post-fix expected A)" % d1.depth_role)
has_a = any(getattr(d, "depth_role", None) == "A" for d in (d0, d1))
print("  -> one-A-per-side invariant at n=2 (post-fix): A=%s" % has_a)
check("OPEN n=2 MUST HAVE ONE A", has_a,
      "open side n=2 must have an A pivot (fixed)")

# ─────────────────────────────────────────────────────────────
# TEST 2 — the DECIDE_COUNTS path that drops the far side to n=2
# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 64)
print("TEST 2 — decide_side_counts drops a side to n=2 (ruck_y=35, ball LEFT)")
print("=" * 64)
world2 = make_world(35.0)
world2.ball_carrier = make_bc(30.0)   # BC 5 m LEFT of ruck_y==35 -> ball_side=LEFT
n_left, n_right = decide_side_counts(
    world2, tuners, 6, ruck_y=35.0,
    ball_side=LEFT, ball_side_bias=1,
)
print("  n_left=%d n_right=%d (short_side=%s)" %
      (n_left, n_right,
       "LEFT" if resolve_short_side(world2, tuners, ruck_y=35.0) == LEFT else "RIGHT"))
check("far/open side (RIGHT) drops to n=2 under ball-side bias",
      n_right == 2, "n_right=%d" % n_right)
check("short side (LEFT) receives the surplus",
      n_left == 4, "n_left=%d" % n_left)

# ─────────────────────────────────────────────────────────────
# TEST 3 — full assign_depth_roles line: far side has no A at n=2
# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 64)
print("TEST 3 — full line assign_depth_roles: far side A-pivot gap")
print("=" * 64)
# 6 defenders.  ruck_y=35, ball carrier LEFT of ruck (y=30).  The count split
# gives LEFT=4 / RIGHT=2.  The RIGHT (far/open) side bootstraps to n=2.
from game.defence.defence_lvl_1.defensive_depth_roles import assign_depth_roles

world3 = make_world(35.0)
world3.ball_carrier = make_bc(30.0)
line = [
    mk(1, 12.0),   # LEFT widest
    mk(2, 20.0),   # LEFT
    mk(3, 33.0),   # LEFT near ruck
    mk(4, 37.0),   # RIGHT near ruck
    mk(5, 50.0),   # RIGHT
    mk(6, 62.0),   # RIGHT widest
]
assign_depth_roles(line, world3, ball_side=LEFT, ball_side_bias=1)
right = [d for d in line if getattr(d, "depth_side", None) == RIGHT]
left = [d for d in line if getattr(d, "depth_side", None) == LEFT]
print("  LEFT  count=%d roles=%s" % (len(left), side_role_map(left)))
print("  RIGHT count=%d roles=%s" % (len(right), side_role_map(right)))
check("full line: RIGHT (far) count == 2", len(right) == 2,
      "count=%d" % len(right))
check("full line: far side has exactly one A (pre-fix FAILS)",
      sum(1 for d in right if getattr(d, "depth_role", None) == "A") == 1,
      "far-side A count=%d (roles=%s)" % (
          sum(1 for d in right if getattr(d, "depth_role", None) == "A"),
          side_role_map(right)))

# ─────────────────────────────────────────────────────────────
# TEST 4 — maintenance path reproduces the same far-side no-A gap
# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 64)
print("TEST 4 — maintenance path keeps the far side no-A (n=2, OPEN)")
print("=" * 64)
dm0 = mk(0, 62.0, "WINGER")
dm1 = mk(1, 55.0, "CENTRE")
_maintain_side([dm0, dm1], 1, anchors, 35.0, is_short_side=False)
print("  maintain open n=2 roles:", side_role_map([dm0, dm1]))
check("maintenance OPEN n=2 has exactly one A (pre-fix FAILS)",
      sum(1 for d in (dm0, dm1) if getattr(d, "depth_role", None) == "A") == 1,
      "roles=%s" % side_role_map([dm0, dm1]))

# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 64)
if failures:
    print("RESULT: %d FAILURE(S): %s" % (len(failures), failures))
    print("These failures are the PRE-FIX reproduction of the far-side "
          "n=2 no-A defect.  After the fix they must all PASS.")
    sys.exit(1)
print("RESULT: ALL CHECKS PASSED")
sys.exit(0)
