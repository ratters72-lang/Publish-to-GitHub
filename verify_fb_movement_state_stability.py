"""verify_fb_movement_state_stability.py — Focused verifier.

Confirms the last-defender (fullback) movement-state / anchor stability fix
for the TES-drive line-instability bug:

  Symptom (reported):
    "Line instability only appears when the fullback gets stuck — not the
     markers — and the moment FB movement-state is overwritten, the backfield
     geometry collapses and the defensive line starts bending around a frozen
     last-defender."

  Root cause (2026-09-02):
    apply_fullback_geometry (the authoritative FB writer, which sets
    movement_state = DRIFT + deep anchor + unfreeze) was ONLY called from
    update_open_play (OPEN_PLAY) and _reassert_fullback (RELEASE / PTB / 
    POST_RUCK).  During the TES phases (TACKLE_INIT / CONTACT / DRIVE /
    DOMINANCE) it is NOT called.  The TACKLE_INIT stasis block pins every
    defender — including the FB — to target_x = d.x / target_y = d.y, and
    update_tes_drive excludes the FB from its pool.  So a tackle starting
    while the FB is mid-glide leaves the last defender parked at the tackle
    point through the whole TES era; the reformed line bends around it.

  Fix (2026-09-02):
    Re-assert the FB geometry authority at frame-end inside the TES stasis
    block in OpenPlayEngine.update(), so the FB tracks the live ruck anchor
    every TES frame (DRIFT + deep anchor + unfrozen + integrated).

Run:  python verify_fb_movement_state_stability.py
Exit 0 on success; prints a PASS/FAIL line per assertion.
"""
import sys
import math

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player, DefenderRole
from player.movement_states import MovementState
from world.world_state import WorldState, GameSubState
from world.open_play_engine import OpenPlayEngine
from game.defence.defence_lvl_1.fullback_role import (
    apply_fullback_geometry,
    compute_fullback_anchor,
)
from game.defence.defence_lvl_1.anchor_authority import (
    defensive_line_anchor_m as _anchor_m,
)

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" - " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def make_defender(pid, x, y, fullback=False, defender_role=None, depth_role=None,
                  speed=8.0, vx=0.0, vy=0.0, jersey=None):
    d = Player(x=x, y=y, player_id=pid)
    d.fullback_role_active = fullback
    d.defender_role = defender_role
    d.depth_role = depth_role
    d.frozen = False
    d.freeze_timer = 0.0
    d.target_x = x
    d.target_y = y
    d.vx = vx
    d.vy = vy
    d.max_speed_mps = speed
    d.max_defensive_speed_mps = 8.3
    d.tackle_role = "PASSIVE"
    d._tackle_commit_active = False
    d.lane_index = 0
    d._prev_lane_index = 0
    d.timing_locked = False
    d.reaction_timer = 0.0
    d.retreat_delay = 0.0
    d.lateral_hesitation = 0.0
    d.step_stagger = 0.0
    d.step_stagger_active = False
    d.is_tackler = False
    d.movement_state = MovementState.DRIFT if fullback else None
    if jersey is not None:
        d.jersey_number = jersey
    return d


def make_world(defending_team, attacking_team=None, ball_carrier=None,
               ruck_x=50.0, ruck_y=35.0, phase=GameSubState.OPEN_PLAY):
    attacking_team = attacking_team or []
    b_carrier = ball_carrier
    b_carrier_list = [b_carrier] if b_carrier is not None else []
    w = WorldState()
    w.defending_team = defending_team
    w.attacking_team = attacking_team
    w.players = list(defending_team) + list(attacking_team) + b_carrier_list
    w.ball_carrier = b_carrier
    w.ball_carrier = ball_carrier
    w.ruck_x_m = ruck_x
    w.ruck_y_m = ruck_y
    w.sub_state = phase
    w.tackler_ids = set()
    w.primary_tackler_id = None
    w.secondary_tacklers = []
    w.frame_count = 0
    w.dt = 1.0 / 60.0
    w.in_pursuit_mode = False
    w.first_tackle_not_made = False
    w.defence_enabled = True
    w.use_structured_open_play = True
    w._prev_frame_open_play = None
    w.defensive_line_anchor = ruck_x + 10.0
    w.defensive_line_update_enabled = True
    w.defensive_chase_enabled = True
    w.ball_owner = "BC"
    w.ball_x_m = ruck_x
    w.ball_y_m = ruck_y
    w.marker_lock_duration_s = 0.75
    w.marker_freeze_timer = 0.75
    w.tes_active = False
    w.phase = "OPEN_PLAY"
    w.game_state = "OPEN_PLAY"
    w.role_governs = False
    w.current_target_writer = ""
    w.marker_freeze_timer = 0.75
    w.first_receiver = None
    w.defended_cap_remaining_s = 0.0
    w.post_catch_chase_window_s = 0.0
    w.frames_since_ptb = 999
    w.seconds_since_ptb = 999.0
    return w


def _fb_in(world):
    for d in world.defending_team:
        if getattr(d, "fullback_role_active", False):
            return d
    return None


def main():
    print("=" * 64)
    print("FULLBACK MOVEMENT-STATE STABILITY VERIFIER (2026-09-02)")
    print("=" * 64)

    # ══════════════════════════════════════════════════════════════
    # TEST 1 — apply_fullback_geometry contract
    # ══════════════════════════════════════════════════════════════
    print("\n[1] apply_fullback_geometry: DRIFT + unfrozen + deep anchor")
    fb1 = make_defender(1, x=80.0, y=35.0, fullback=True,
                        defender_role=DefenderRole.DEF_LINE_READY)
    bc1 = make_defender(99, x=55.0, y=35.0)
    w1 = make_world([fb1], ball_carrier=bc1, ruck_x=50.0)
    apply_fullback_geometry(w1, fb1)
    anchor_x, anchor_y = compute_fullback_anchor(bc1, w1.defensive_line_anchor)
    check("1.1 FB movement_state == DRIFT",
          getattr(fb1.movement_state, "name", None) == "DRIFT",
          "movement_state=%s" % getattr(fb1.movement_state, "name", None))
    check("1.2 FB frozen == False", not fb1.frozen, "frozen=%s" % fb1.frozen)
    check("1.3 FB target_source == 'fullback_geometry'",
          getattr(fb1, "target_source", None) == "fullback_geometry",
          "target_source=%s" % getattr(fb1, "target_source", None))
    check("1.4 FB target_x == deep anchor_x (%.2f)" % anchor_x,
          abs(fb1.target_x - anchor_x) < 1e-6,
          "target_x=%.3f anchor=%.3f" % (fb1.target_x, anchor_x))
    check("1.5 FB target_y == anchor_y (%.2f)" % anchor_y,
          abs(fb1.target_y - anchor_y) < 1e-6,
          "target_y=%.3f anchor=%.3f" % (fb1.target_y, anchor_y))

    # ══════════════════════════════════════════════════════════════
    # TEST 2 — TACKLE_INIT stasis pin overwrites FB target (bug premise)
    # ══════════════════════════════════════════════════════════════
    print("\n[2] TACKLE_INIT stasis overwrites the FB geometry (pre-fix)")
    fb2 = make_defender(1, x=82.0, y=35.0, fullback=True,
                        defender_role=DefenderRole.DEF_LINE_READY)
    bc2 = make_defender(99, x=55.0, y=35.0)
    # Give the FB a live deep anchor FIRST (as OPEN_PLAY would have left it).
    w2 = make_world([fb2], ball_carrier=bc2, ruck_x=50.0)
    apply_fullback_geometry(w2, fb2)
    pre_pin_tx, pre_pin_ty = fb2.target_x, fb2.target_y
    # Reproduce the TACKLE_INIT stasis pin exactly (the writer that
    # overwrites the geometry without a fullback guard).
    for d in w2.defending_team:
        d.vx = 0.0
        d.vy = 0.0
        d.target_x = d.x
        d.target_y = d.y
    check("2.1 stasis pinned FB target to its CURRENT position (bug)",
          abs(fb2.target_x - fb2.x) < 1e-6 and abs(fb2.target_y - fb2.y) < 1e-6,
          "target=(%.2f,%.2f) pos=(%.2f,%.2f)" % (fb2.target_x, fb2.target_y, fb2.x, fb2.y))
    check("2.2 stasis LOST the deep anchor (target != deep anchor)",
          abs(fb2.target_x - pre_pin_tx) > 1e-3,
          "target_x=%.3f deep_anchor=%.3f" % (fb2.target_x, pre_pin_tx))

    # ══════════════════════════════════════════════════════════════
    # TEST 3 — the frame-end FB geometry authority re-assert RESTORES it
    # ══════════════════════════════════════════════════════════════
    print("\n[3] frame-end FB geometry authority re-assert (fix)")
    # Same world; now apply the fix's re-assert (mirrors the OPE TES block).
    for _d in w2.defending_team:
        if getattr(_d, "fullback_role_active", False):
            apply_fullback_geometry(w2, _d)
            if not _d.frozen:
                _d.update(w2.dt)
    anchor_x2, anchor_y2 = compute_fullback_anchor(bc2, w2.defensive_line_anchor)
    check("3.1 FB movement_state restored to DRIFT",
          getattr(fb2.movement_state, "name", None) == "DRIFT",
          "movement_state=%s" % getattr(fb2.movement_state, "name", None))
    check("3.2 FB frozen == False", not fb2.frozen, "frozen=%s" % fb2.frozen)
    check("3.3 FB target_source == 'fullback_geometry'",
          getattr(fb2, "target_source", None) == "fullback_geometry",
          "target_source=%s" % getattr(fb2, "target_source", None))
    check("3.4 FB target_x restored to deep anchor (%.2f)" % anchor_x2,
          abs(fb2.target_x - anchor_x2) < 1e-6,
          "target_x=%.3f anchor=%.3f" % (fb2.target_x, anchor_x2))
    check("3.5 FB moved (x changed after re-assert + update)",
          abs(fb2.x - 82.0) > 1e-6,
          "x=%.3f (started 82.0)" % fb2.x)

    # ══════════════════════════════════════════════════════════════
    # TEST 4 — real OpenPlayEngine.update() in a TES phase: FB stays DRIFT
    # ══════════════════════════════════════════════════════════════
    print("\n[4] OpenPlayEngine.update() in TACKLE_CONTACT: FB stays DRIFT + deep")
    try:
        fb4 = make_defender(1, x=82.0, y=35.0, fullback=True,
                            defender_role=DefenderRole.DEF_LINE_READY, jersey=1)
        line1 = make_defender(2, x=60.0, y=30.0, depth_role="A")
        line2 = make_defender(3, x=60.0, y=40.0, depth_role="B")
        bc4 = make_defender(99, x=55.0, y=35.0)
        w4 = make_world([fb4, line1, line2], attacking_team=[], ball_carrier=bc4,
                        ruck_x=50.0)
        ope = OpenPlayEngine(w4)
        w4.open_play = ope
        w4.open_play_engine = ope
        # Establish a TACKLE_CONTACT frame.
        ope._phase = "TACKLE_CONTACT"
        ope.ball_carrier = bc4
        ope.primary = line1
        ope.joiners = []
        ope.tes_ctx = None
        w4.tes_active = True
        ope._phase_timer = 0.0
        # Seed the FB as the engine would at OPEN_PLAY entry.
        apply_fullback_geometry(w4, fb4)
        # Run one full engine update.
        ope.update(1.0 / 60.0)
        fb_after = _fb_in(w4)
        check("4.1 FB still fullback_role_active", fb_after is not None,
              "no FB found")
        if fb_after is not None:
            check("4.2 FB movement_state == DRIFT",
                  getattr(fb_after.movement_state, "name", None) == "DRIFT",
                  "movement_state=%s" % getattr(fb_after.movement_state, "name", None))
            check("4.3 FB frozen == False", not fb_after.frozen,
                  "frozen=%s" % fb_after.frozen)
            check("4.4 FB target_source == 'fullback_geometry'",
                  getattr(fb_after, "target_source", None) == "fullback_geometry",
                  "target_source=%s" % getattr(fb_after, "target_source", None))
            anchor_x4, anchor_y4 = compute_fullback_anchor(bc4, _anchor_m(w4))
            check("4.5 FB target_x == live deep anchor (%.2f)" % anchor_x4,
                  abs(fb_after.target_x - anchor_x4) < 1e-6,
                  "target_x=%.3f anchor=%.3f" % (fb_after.target_x, anchor_x4))
    except Exception as exc:  # pragma: no cover - diagnostic
        import traceback
        traceback.print_exc()
        check("4.x OpenPlayEngine.update() ran without error", False,
              "raised %s" % exc)

    # ══════════════════════════════════════════════════════════════
    # TEST 5 — BREAKAWAY CHASE: FB must DETECT the BC past the line
    # ══════════════════════════════════════════════════════════════
    print("\n[5] BREAKAWAY CHASE: FB detects BC past the line (2026-09-03)")
    fb5 = make_defender(1, x=84.0, y=35.0, fullback=True,
                        defender_role=DefenderRole.DEF_LINE_READY)
    # BC clearly PAST the defensive line: anchor = ruck_x + 10 = 60,
    # so bc.x > 61 triggers the breakaway chase.
    bc5 = make_defender(99, x=70.0, y=35.0)
    w5 = make_world([fb5], ball_carrier=bc5, ruck_x=50.0)
    apply_fullback_geometry(w5, fb5)
    check("5.1 FB movement_state == CHASE (breakaway)",
          getattr(fb5.movement_state, "name", None) == "CHASE",
          "movement_state=%s" % getattr(fb5.movement_state, "name", None))
    check("5.2 FB target_source == 'fullback_geometry'",
          getattr(fb5, "target_source", None) == "fullback_geometry",
          "target_source=%s" % getattr(fb5, "target_source", None))
    check("5.3 FB target_x == bc.x - 1.0 (goal-side pursuit point)",
          abs(fb5.target_x - (bc5.x - 1.0)) < 1e-6,
          "target_x=%.3f expect=%.3f" % (fb5.target_x, bc5.x - 1.0))
    check("5.4 FB target_y == bc.y",
          abs(fb5.target_y - bc5.y) < 1e-6,
          "target_y=%.3f expect=%.3f" % (fb5.target_y, bc5.y))
    check("5.5 FB frozen == False", not fb5.frozen, "frozen=%s" % fb5.frozen)

    # ══════════════════════════════════════════════════════════════
    # TEST 6 — MMDL decide_movement_state: FB chase-aware mirror
    # ══════════════════════════════════════════════════════════════
    print("\n[6] decide_movement_state: FB CHASE on breakaway, DRIFT otherwise")
    from game.defence.defence_lvl_1.movement_mode_decision import (
        decide_movement_state as _decide_ms,
    )
    fb6a = make_defender(1, x=84.0, y=35.0, fullback=True,
                         defender_role=DefenderRole.DEF_LINE_READY)
    bc6a = make_defender(99, x=70.0, y=35.0)
    w6a = make_world([fb6a], ball_carrier=bc6a, ruck_x=50.0)
    check("6.1 FB MMDL returns CHASE (BC past line)",
          getattr(_decide_ms(w6a, fb6a, bc6a), "name", None) == "CHASE",
          "state=%s" % getattr(_decide_ms(w6a, fb6a, bc6a), "name", None))
    fb6b = make_defender(1, x=84.0, y=35.0, fullback=True,
                         defender_role=DefenderRole.DEF_LINE_READY)
    bc6b = make_defender(99, x=55.0, y=35.0)
    w6b = make_world([fb6b], ball_carrier=bc6b, ruck_x=50.0)
    check("6.2 FB MMDL returns DRIFT (BC onside)",
          getattr(_decide_ms(w6b, fb6b, bc6b), "name", None) == "DRIFT",
          "state=%s" % getattr(_decide_ms(w6b, fb6b, bc6b), "name", None))

    print("\n" + "=" * 64)
    if failures:
        print("RESULT: %d FAILURE(S): %s" % (len(failures), failures))
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
