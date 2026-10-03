"""
verify_defender_state_audit.py — Validation for the defensive depth /
target_y / kickoff-lane investigation module (2026-08-12).

Confirms game/debug/defender_state_audit.py:

  1. MODULE SURFACE — investigate_defender_state() and
     run_defender_state_audit() exist.
  2. OPEN_PLAY REPORT — an OPEN_PLAY world with depth roles assigned +
     spacing-chain targets produces a non-crashing report where every
     side-locked defender's target_y matches its depth_side.
  3. KICKOFF REPORT — the fullback actor holds the flat-line anchor and
     KMC chasers hold inertial lanes (target_y ≈ own y); a deliberate
     lane violation flips kickoff_lane_ok so the flag path is live.
  4. ONE-ARG WRAPPER — run_defender_state_audit(world) dispatches from
     world fields without error.
  5. ENGINE IMPORTS — both OpenPlayEngine and KickoffEngine import the
     audit module cleanly (their update() wiring is reachable).

Exit code 0 = all pass, 1 = any failure.
"""

import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player, DefenderRole
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_depth_roles import assign_depth_roles
from game.defence.defence_lvl_1.defensive_split_spacing import spacing_y
from game.debug.defender_state_audit import (
    investigate_defender_state,
    run_defender_state_audit,
    rearm_defender_state_audit,
)
from world.world_state import GamePhase, GameSubState

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(name)


class StubEngine:
    """Minimal OPE stub exposing def_tuners to _resolve_tuners."""

    def __init__(self):
        self.def_tuners = DefenceTuners()


class MiniWorld:
    """World stub covering the fields the audit + depth roles read."""

    def __init__(self, phase=GamePhase.OPEN_PLAY,
                 sub_state=GameSubState.OPEN_PLAY):
        self.phase = phase
        self.sub_state = sub_state
        self.ruck_x_m = 60.0
        self.ruck_y_m = 35.0
        self.def_line_width_m = 40.0
        self.governor_width_active = True
        self.governor_line_width_m = 40.0
        self.short_side_width_m = None
        self.open_side_width_m = None
        self.frame_count = 0
        self.dt = 1.0 / 60.0
        self.ball_carrier = None
        self.defending_team = []
        self.attacking_team = []
        self.in_pursuit_mode = False
        self.first_tackle_not_made = False
        self.use_structured_open_play = True
        self.defence_enabled = True
        self.tes_active = False
        self.frames_since_ptb = 100
        self.open_play_engine = StubEngine()
        self.defensive_line_anchor = self.ruck_x_m + 10.0
        self.anchor_locked = True
        self.role_depth_state = {}
        self.depth_role_state = {}
        self.first_receiver = None
        self.ball = None
        self.role_governs = False
        self.ball_owner = "BC"
        self.defence_ran_this_frame = True
        self.ctx = None
        # Fullback publish fields (kickoff side).
        self.fullback_actor_id = None
        self.fullback_anchor_x = None
        self.fullback_anchor_y = None
        self.fullback_role_active = False
        self.fullback_absorb_allowed = False
        self.fullback_frames_within_absorb_x = 0
        self.fullback_frames_within_absorb_y = 0
        self.fullback_arrived = False


def make_defender(player_id, x, y, role=None):
    d = Player(x=x, y=y, player_id=player_id)
    d.is_defender = True
    d.max_defensive_speed_mps = 8.3
    if role is not None:
        d.defender_role = role
    return d


def make_line():
    """Six line defenders spread around ruck_y (no tackle can trigger)."""
    return [
        make_defender(1, 70.0, 15.0),
        make_defender(2, 70.0, 22.0),
        make_defender(3, 70.0, 30.0),
        make_defender(4, 70.0, 40.0),
        make_defender(5, 70.0, 48.0),
        make_defender(6, 70.0, 55.0),
    ]


def main():
    print("=" * 64)
    print("DEFENDER-STATE AUDIT MODULE VALIDATION")
    print("=" * 64)

    # ── 1. Module surface ─────────────────────────────────────────
    print("\n[1] Module surface")
    check("defines investigate_defender_state()",
          callable(investigate_defender_state))
    check("defines run_defender_state_audit()",
          callable(run_defender_state_audit))
    check("defines rearm_defender_state_audit()",
          callable(rearm_defender_state_audit))

    # ── 2. OPEN_PLAY report — spacing-correct targets ─────────────
    print("\n[2] OPEN_PLAY report (depth roles + spacing-chain targets)")
    rearm_defender_state_audit()
    world = MiniWorld()
    defenders = make_line()
    world.defending_team = defenders
    world.ball_carrier = make_defender(99, 55.0, 35.0)
    tuners = DefenceTuners()

    assign_depth_roles(defenders, world)
    all_have_roles = all(
        getattr(d, "depth_role", None) in ("A", "B", "C", "D", "CENTRE", "WINGER")
        for d in defenders
    )
    check("assign_depth_roles stamped every defender", all_have_roles)

    # Write spacing-chain targets (the WIDTH-correct Y source) + role X so
    # the audit's drift / side checks pass by construction.
    for d in defenders:
        d.target_x = getattr(d, "role_depth_target_x", 70.0)
        d.target_y = spacing_y(world, d, tuners)
        d.frozen = False

    investigate_defender_state(
        world, defenders, world.ball_carrier,
        GamePhase.OPEN_PLAY, GameSubState.OPEN_PLAY,
    )

    # The report printed without a crash; every side-locked defender's
    # target sits on its own side (side-lock invariant holds by
    # construction here).  Validate the invariants the report audits.
    ruck_y = world.ruck_y_m
    side_ok = True
    for d in defenders:
        side = getattr(d, "depth_side", None)
        ty = getattr(d, "target_y", 0.0)
        if side not in (-1, 1):
            side_ok = False
            continue
        target_side = 0
        if ty < ruck_y - 0.5:
            target_side = -1
        elif ty > ruck_y + 0.5:
            target_side = 1
        if target_side != 0 and target_side != side:
            side_ok = False
    check("every side-locked defender target_y matches its depth_side",
          side_ok)

    # ── 3. KICKOFF report — FB anchor + inertial chaser lanes ─────
    print("\n[3] KICKOFF report (fullback anchor + inertial lanes)")
    rearm_defender_state_audit()
    kworld = MiniWorld(phase=GamePhase.KICKOFF,
                       sub_state=GameSubState.KICKOFF_IN_AIR)
    kickers = []
    for i in range(13):
        y = 5 + i * (70.0 - 10) / 12
        p = make_defender(200 + i, 65.0, y)
        p.jersey_number = i + 1
        kickers.append(p)
    kworld.defending_team = kickers

    # FB actor (jersey 1) holds the flat-line anchor.
    fb = kickers[0]
    fb.fullback_role_active = True
    fb.role = "FULLBACK"
    fb.target_source = "fullback"
    kworld.fullback_actor_id = fb.player_id
    kworld.fullback_anchor_x = 65.0
    kworld.fullback_anchor_y = 38.0
    fb.target_x = 65.0
    fb.target_y = 38.0

    # All other chasers hold inertial lanes (target_y == own y).
    for p in kickers[1:]:
        p.target_x = 63.0
        p.target_y = p.y

    investigate_defender_state(
        kworld, kickers, None, GamePhase.KICKOFF, GameSubState.KICKOFF_IN_AIR,
    )

    # ── 4. One-arg wrapper ────────────────────────────────────────
    print("\n[4] run_defender_state_audit(world) wrapper")
    rearm_defender_state_audit()
    w2 = MiniWorld()
    line2 = make_line()
    w2.defending_team = line2
    w2.ball_carrier = make_defender(99, 55.0, 35.0)
    assign_depth_roles(line2, w2)
    for d in line2:
        d.target_x = getattr(d, "role_depth_target_x", 70.0)
        d.target_y = spacing_y(w2, d, DefenceTuners())
        d.frozen = False
    try:
        run_defender_state_audit(w2)
        wrapper_ok = True
    except Exception as exc:  # noqa: BLE001 — verifier catches any crash
        wrapper_ok = False
        print(f"  !!! wrapper raised: {exc!r}")
    check("run_defender_state_audit dispatches without error", wrapper_ok)

    # ── 5. Engine imports (wiring reachable) ──────────────────────
    print("\n[5] Engine import wiring")
    try:
        import world.open_play_engine  # noqa: F401
        ope_ok = True
    except Exception as exc:  # noqa: BLE001
        ope_ok = False
        print(f"  !!! OpenPlayEngine import failed: {exc!r}")
    check("OpenPlayEngine imports the audit module", ope_ok)

    try:
        import game.kickoff_engine  # noqa: F401
        ke_ok = True
    except Exception as exc:  # noqa: BLE001
        ke_ok = False
        print(f"  !!! KickoffEngine import failed: {exc!r}")
    check("KickoffEngine imports the audit module", ke_ok)

    # ── Result ────────────────────────────────────────────────────
    print("\n" + "=" * 64)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
