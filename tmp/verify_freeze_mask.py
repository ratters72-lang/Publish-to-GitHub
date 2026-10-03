# tmp/verify_freeze_mask.py — TES FREEZE-MASK CONTRACT verification.
#
# Proves that stamping ctx["frozen_ids"] with the full player list during
# TACKLE_DRIVE suppresses the three movement-idle checks inside
# FreezeAndMovementAuditAgent (STUCK_NOT_FROZEN, STUCK_SOFT_FREEZE,
# VX_ZERO_JIGGLE) while leaving every OTHER check byte-for-byte intact:
# real-freeze invariants still fire, unmasked frames still fire.
#
# Uses a lightweight fake world.  Only the audit agent itself is exercised.

import io
import os
import sys
from contextlib import redirect_stdout

# Ensure the project root is importable when run as `python tmp/verify_freeze_mask.py`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from game.debug.freeze_audit import FreezeAndMovementAuditAgent
from player.player import Player, DefenderRole


class _FakeBall:
    def __init__(self):
        self.true_x_m = 0.0
        self.true_y_m = 0.0
        self.possessed_by = None
        self.frozen = False


class _FakeWorld:
    """Minimal stand-in: the audit only reads .attacking_team, .defending_team,
    .ball_carrier, .ctx, .sub_state, .ruck_x_m, .defence_ran_this_frame."""

    def __init__(self):
        self.attacking_team = []
        self.defending_team = []
        self.ball_carrier = None
        self.ctx = None
        self.sub_state = "tackle_drive"
        self.ruck_x_m = 60.0
        self.defence_ran_this_frame = False


def make_players():
    a = Player(10.0, 30.0, player_id=1)   # idle attacker (unfrozen, vx=0)
    a.frozen = False
    a.vx = 0.0
    a.vy = 0.0
    a.target_x = 21.0   # offset > 0.2 => triggers vx-zero jiggle / soft-freeze
    a.target_y = 30.0
    a.defender_role = None
    a.movement_writer = None

    d = Player(60.0, 35.0, player_id=2)   # idle defender (unfrozen, vx=0)
    d.frozen = False
    d.vx = 0.0
    d.vy = 0.0
    d.target_x = 71.0   # offset > 0.2
    d.target_y = 35.0
    d.defender_role = DefenderRole.DEF_RETREAT_10
    d.movement_writer = None

    frozen = Player(50.0, 35.0, player_id=3)  # legitimately frozen tackler
    frozen.frozen = True
    frozen.vx = 0.0
    frozen.vy = 0.0
    frozen.target_x = 50.0
    frozen.target_y = 35.0
    frozen.defender_role = DefenderRole.TACKLER_1
    frozen.movement_writer = None

    return [a, d, frozen]


def run_frames(audit, world, players, n, phase, tes_attackers, tes_defenders):
    """Run n audit frames with position/velocity held constant (idle players).

    tes_attackers/tes_defenders are passed so the audit's TES participant
    union is non-None — exactly the setup under which soft-freeze stuck
    counters accumulate and STUCK_SOFT_FREEZE can fire.  (In production the
    OPE calls run() without these args, so tes_union is None and the
    pre-existing counter-clear on line ~270 keeps STUCK_SOFT_FREEZE from
    ever accumulating — that quirk is unrelated to the freeze-mask.)
    """
    for i in range(n):
        world.ctx = {} if world.ctx is None else world.ctx
        with redirect_stdout(io.StringIO()):
            audit.run(world, i + 1, phase, float(i + 1) / 60.0,
                      tes_attackers, tes_defenders)


def collect_violations(audit):
    return list(audit._violations)


def freeze_mask_active(audit, expected_ids):
    return audit._audit_frozen_ids_for_run == set(expected_ids)


def expect(cond, msg):
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"PASS: {msg}")


# ── Scenario 1: UNMASKED TACKLE_DRIVE (no ctx frozen_ids) ─────────────
# Baseline: idle-but-unfrozen players must emit STUCK_NOT_FROZEN /
# STUCK_SOFT_FREEZE / VX_ZERO_JIGGLE as before (no behaviour change).
audit = FreezeAndMovementAuditAgent(report_interval=999, window=10, stuck_threshold=10)
world = _FakeWorld()
players = make_players()
world.attacking_team = [players[0]]
world.defending_team = [players[1], players[2]]
world.ball_carrier = players[0]  # attacker #1 is BC for _all_players dedup

run_frames(audit, world, players, 12, "TACKLE_DRIVE",
           {players[0]}, {players[1], players[2]})
v = collect_violations(audit)
expect(any(x.startswith("STUCK_NOT_FROZEN id=1") for x in v), "unmasked: attacker emits STUCK_NOT_FROZEN")
expect(any(x.startswith("STUCK_NOT_FROZEN id=2") for x in v), "unmasked: defender emits STUCK_NOT_FROZEN")
expect(any(x.startswith("STUCK_SOFT_FREEZE id=1") for x in v), "unmasked: attacker emits STUCK_SOFT_FREEZE")
expect(any(x.startswith("STUCK_SOFT_FREEZE id=2") for x in v), "unmasked: defender emits STUCK_SOFT_FREEZE")
expect(any(x.startswith("VX_ZERO_JIGGLE id=1") for x in v), "unmasked: attacker emits VX_ZERO_JIGGLE")
expect(any(x.startswith("VX_ZERO_JIGGLE id=2") for x in v), "unmasked: defender emits VX_ZERO_JIGGLE")

# ── Scenario 2: MASKED TACKLE_DRIVE (frozen_ids = full player list) ──
# The freeze-mask must suppress all three checks for every player.
audit2 = FreezeAndMovementAuditAgent(report_interval=999, window=10, stuck_threshold=10)
world2 = _FakeWorld()
players2 = make_players()
world2.attacking_team = [players2[0]]
world2.defending_team = [players2[1], players2[2]]
world2.ball_carrier = players2[0]
world2.ctx = {"frozen_ids": [p.player_id for p in players2]}  # exactly what OPE stamps in _update_tackle_drive

run_frames(audit2, world2, players2, 12, "TACKLE_DRIVE",
           {players2[0]}, {players2[1], players2[2]})
v2 = collect_violations(audit2)
expect(freeze_mask_active(audit2, [1, 2, 3]), "mask parsed from ctx.frozen_ids")
expect(not any(x.startswith("STUCK_NOT_FROZEN") for x in v2), "masked: NO STUCK_NOT_FROZEN")
expect(not any(x.startswith("STUCK_SOFT_FREEZE") for x in v2), "masked: NO STUCK_SOFT_FREEZE")
expect(not any(x.startswith("VX_ZERO_JIGGLE") for x in v2), "masked: NO VX_ZERO_JIGGLE")

# ── Scenario 3: REAL FREEZE INVARIANTS STILL FIRE WHEN MASKED ─────────
# Real-freeze checks (p.frozen based) must NOT be suppressed by the mask.
audit3 = FreezeAndMovementAuditAgent(report_interval=999, window=10, stuck_threshold=10)
world3 = _FakeWorld()
players3 = make_players()
# Put a defender with a role that is frozen during OPEN_PLAY => DEFENDER_FROZEN_OPEN_PLAY.
frozen_def = Player(60.0, 35.0, player_id=9)
frozen_def.frozen = True
frozen_def.vx = 0.0
frozen_def.vy = 0.0
frozen_def.target_x = 60.0
frozen_def.target_y = 35.0
frozen_def.defender_role = DefenderRole.DEF_LINE_READY
frozen_def.movement_writer = None

world3.attacking_team = [players3[0]]
world3.defending_team = [frozen_def]
world3.ball_carrier = players3[0]
world3.sub_state = "open_play"
world3.ctx = {"frozen_ids": [players3[0].player_id, frozen_def.player_id]}  # mask present but irrelevant to real-freeze checks

with redirect_stdout(io.StringIO()):
    audit3.run(world3, 1, "OPEN_PLAY", 1.0 / 60.0)
v3 = collect_violations(audit3)
expect(any(x.startswith("DEFENDER_FROZEN_OPEN_PLAY id=9") for x in v3),
       "real-freeze invariant DEFENDER_FROZEN_OPEN_PLAY NOT suppressed by mask")

# ── Scenario 4: mask does NOT leak into the next unmasked frame ──────
# world.ctx is rebuilt every frame; if the new ctx lacks frozen_ids, the
# audit must resume emitting.
audit4 = FreezeAndMovementAuditAgent(report_interval=999, window=10, stuck_threshold=10)
world4 = _FakeWorld()
players4 = make_players()
world4.attacking_team = [players4[0]]
world4.defending_team = [players4[1], players4[2]]
world4.ball_carrier = players4[0]
world4.ctx = {"frozen_ids": []}  # empty mask == no mask
run_frames(audit4, world4, players4, 12, "TACKLE_DRIVE",
           {players4[0]}, {players4[1], players4[2]})
v4 = collect_violations(audit4)
expect(any(x.startswith("STUCK_NOT_FROZEN id=1") for x in v4),
       "empty/absent mask resumes normal STUCK_NOT_FROZEN firing")

print()
print("ALL FREEZE-MASK CHECKS PASSED")
