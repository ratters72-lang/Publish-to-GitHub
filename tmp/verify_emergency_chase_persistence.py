"""verify_emergency_chase_persistence.py — EMERGENCY_CHASE lifetime checks.

Verifies the two classified fixes:

  A. The carrier step-back no longer releases the latch
     (`carrier_behind_latched_line` is kept as a predicate but no longer
     authorises a release in `apply_fullback_geometry`).

  B. A broken tackle no longer clears the latch
     (the miss teardown hands the ROLE back with `clear_latch=False`, and
     the engine's capture/clear/re-arm block is gone).

  C. The existing cycle-end ownership still ends the chase.

Read-only.  Runs in seconds; no rollout.
"""

import os
import re
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(ROOT))

from game.defence.defence_lvl_1 import fullback_role as fb  # noqa: E402

FAILURES = []
CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok), detail))
    if not ok:
        FAILURES.append(f"{name} :: {detail}")


class Body:
    """Minimal defender stub carrying the attributes the latch reads."""

    def __init__(self, pid=7, x=50.0, y=35.0):
        self.player_id = pid
        self.x = float(x)
        self.y = float(y)
        self.vx = 0.0
        self.vy = 0.0
        self.fullback_role_active = True
        self.defender_role = None
        self.movement_mode = None
        self.movement_state = None
        self.role = "FULLBACK"


class Carrier:
    def __init__(self, x, y=35.0):
        self.player_id = 99
        self.x = float(x)
        self.y = float(y)
        self.vx = -4.0
        self.vy = 0.0


class World:
    def __init__(self, team):
        self.defending_team = list(team)
        self.players = list(team)
        self.frame_count = 100
        self.ruck_x_m = 60.0
        self.ruck_y_m = 35.0


def latch_state(body):
    return bool(fb.is_fullback_break_latched(body))


# ── A. STRUCTURAL: the step-back site no longer releases ────────────────
src_path = os.path.join(
    os.path.dirname(ROOT),
    "game", "defence", "defence_lvl_1", "fullback_role.py",
)
with open(src_path, encoding="utf-8") as handle:
    src = handle.read()

# Find the region of apply_fullback_geometry where the break verdict is NONE
# and confirm no `clear_fullback_break_latch` call is guarded by the
# step-back predicate anywhere in the module.
stepback_release = re.search(
    r"carrier_behind_latched_line\([^)]*\)\s*:\s*\n\s*clear_fullback_break_latch\(",
    src,
)
check(
    "A_STRUCTURAL_no_stepback_release",
    stepback_release is None,
    "carrier_behind_latched_line still guards a clear_fullback_break_latch call",
)

# The predicate itself must survive as a readable helper.
check(
    "A_STRUCTURAL_predicate_kept",
    "def carrier_behind_latched_line(" in src,
    "carrier_behind_latched_line was deleted rather than de-authorised",
)


# ── A2. BEHAVIOURAL: predicate still reads True on a step-back ──────────
step_back_body = Body()
fb.arm_fullback_break_latch(step_back_body, fb.FB_BREAK_DEPTH, 60.0)
step_back_carrier = Carrier(x=40.0)   # behind the latched anchor + margin
predicate_value = fb.carrier_behind_latched_line(step_back_body, step_back_carrier)
check(
    "A2_behavioural_predicate_reads_stepback",
    predicate_value is True,
    f"expected True, got {predicate_value!r}",
)
check(
    "A2_behavioural_latch_survives_stepback",
    latch_state(step_back_body),
    "latch dropped while merely evaluating the step-back predicate",
)


# ── B. BEHAVIOURAL: the miss teardown keeps the latch ───────────────────
miss_body = Body(pid=11)
fb.arm_fullback_break_latch(miss_body, fb.FB_BREAK_LATERAL, 48.0)
fb.set_fullback_emergency_engaged(miss_body)
miss_world = World([miss_body])

engaged_before = bool(fb.is_fullback_emergency_engaged(miss_body))
check("B_setup_engaged", engaged_before, "engagement marker was not set")

released = fb.release_fullback_emergency_on_miss(miss_world)
check("B_release_reports_body", list(released) == [miss_body],
      f"release_fullback_emergency_on_miss returned {released!r}")
check(
    "B_engagement_cleared",
    not fb.is_fullback_emergency_engaged(miss_body),
    "miss teardown failed to clear the engagement marker",
)
check(
    "B_LATCH_SURVIVES_MISS",
    latch_state(miss_body),
    "latch cleared by the miss teardown — chase does not persist",
)
check(
    "B_latch_kind_preserved",
    getattr(miss_body, fb.FB_BREAK_LATCH_KIND_ATTR, None) == fb.FB_BREAK_LATERAL,
    "the LATERAL break kind was rewritten by the miss teardown",
)

# The default (non-miss) caller MUST still drop the latch.
strict_body = Body(pid=12)
fb.arm_fullback_break_latch(strict_body, fb.FB_BREAK_DEPTH, 60.0)
fb.set_fullback_emergency_engaged(strict_body)
fb.reset_fullback_emergency_engagement(strict_body)
check(
    "B_default_caller_still_clears",
    not latch_state(strict_body),
    "default reset_fullback_emergency_engagement no longer clears the latch",
)


# ── C. CYCLE-END OWNERSHIP still releases ───────────────────────────────
cycle_body = Body(pid=13)
fb.arm_fullback_break_latch(cycle_body, fb.FB_BREAK_DEPTH, 60.0)
cycle_world = World([cycle_body])
fb.clear_fullback_chase_state(cycle_world)
check(
    "C_cycle_end_clears_latch",
    not latch_state(cycle_body),
    "clear_fullback_chase_state (cycle end) no longer clears the latch",
)


# ── D. SOURCE: the engine's capture/clear/re-arm block is gone ──────────
ope_path = os.path.join(
    os.path.dirname(ROOT), "world", "open_play_engine.py",
)
with open(ope_path, encoding="utf-8") as handle:
    ope = handle.read()
check(
    "D_engine_relatch_block_removed",
    "_fb_relatch" not in ope,
    "the broken-tackle capture/clear/re-arm block still present",
)


# ── REPORT ──────────────────────────────────────────────────────────────
print("=" * 74)
print("EMERGENCY_CHASE PERSISTENCE — module-level verification")
print("=" * 74)
for name, ok, detail in CHECKS:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}"
          + (f"  ({detail})" if detail and not ok else ""))
print("-" * 74)
if FAILURES:
    print(f"RESULT: {len(FAILURES)} FAILURE(S) of {len(CHECKS)} checks")
    for line in FAILURES:
        print(f"  !! {line}")
    sys.exit(1)
print(f"RESULT: ALL {len(CHECKS)} CHECKS PASS")
sys.exit(0)
