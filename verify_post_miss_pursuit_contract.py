"""verify_post_miss_pursuit_contract.py -- the POST-MISS PURSUIT entitlement law.

THE FINDING THIS CONTRACT ANSWERS
---------------------------------
`PURSUIT_DECISION_HIERARCHY_REPORT.md` measured a beaten defender over 77,681
post-miss body-frames.  Pursuit was never out-ranked (`OVERRIDDEN_BY = 0`):
the chase was WITHHELD before it was ever proposed, and the frame after the
freeze expired was owned by the obligated-body retreat authority
(`retreat_non_compliant`, the dispatch's FINAL target write) driving the body
AWAY from the carrier it had just been beaten by.

`post_miss_pursuit` owns the missing intent.  This verifier asserts its
contract:

  P1  the ``RLM_POST_MISS_PURSUIT`` lever -- name, default, spellings -- and
      the window override;
  P2  ARMING: the deadline written, and every case that must decline (no
      body, the carrier itself, a dedicated owner, no frame number, lever
      off);
  P3  OWNERSHIP declines -- never armed, expired, frozen, live
      ``freeze_timer``, live TES, exempt role, fullback tag, tackle-state
      holder, in contact, TES-attached, pre-tackle stance, no carrier, the
      carrier itself, no genuine break, a live tackle-side owner;
  P4  OWNERSHIP accepts the clean beaten body with a genuine break -- the
      premise every other check rests on;
  P5  the WRITE: the full pursuit contract (point on the carrier, CHASE,
      governed ceiling, locks cleared, source stamp) and that a non-entitled
      body is left byte-identical;
  P6  SINGLE SOURCING: the ceiling, the break predicate, the owner gate and
      the freeze-pin latch clear are all delegated, and the exempt role set
      matches ``line_break_static_guard``'s so the pursuit writers cannot
      disagree about dedicated owners;
  P7  THE FREEZE IS NOT BYPASSED: a body still pinned is never entitled;
  P8  INTEGRATION: the entitlement is consultable by the guard, exempted in
      the retreat writer, skipped in the MMDL, written as the dispatch's
      final target, armed at the single miss-teardown site, and the
      beat-body commit-latch release now runs on BOTH teardown paths;
  P9  the ``post_miss_pursuit`` stamp is registered at the commit tier;
  P10 the retreat writer really does leave an entitled body alone -- executed
      through the real ``apply_forced_retreat``.

Run:  python verify_post_miss_pursuit_contract.py
"""

import os
import types
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

sys.path.insert(0, ".")

import pygame

pygame.display.init()
pygame.display.set_mode((1, 1), pygame.NOFRAME)

from player.player import Player, DefenderRole
from player.movement_states import MovementState
from game.defence.defence_lvl_1 import post_miss_pursuit as pmp
from game.defence.defence_lvl_1 import line_break_static_guard as lbsg
from game.defence.defence_lvl_1 import writer_priority_bands as wpb
from game.managers import retreat_manager as rm

HERE = os.path.dirname(os.path.abspath(__file__))
FAILURES = []

ANCHOR_X = 60.0
CARRIER_X = 70.0


def check(name, ok, detail=""):
    if ok:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s  %s" % (name, detail))
        FAILURES.append(name)


def src_of(rel_path):
    with open(os.path.join(HERE, rel_path), "r", encoding="utf-8") as fh:
        return fh.read()


class Carrier:
    def __init__(self, x=CARRIER_X, y=40.0):
        self.x = x
        self.y = y
        self.is_ball_carrier = True


class World:
    """Minimum world for ``defensive_line_anchor_m`` + the break predicate."""

    def __init__(self, frame=1000, bc=None, tes_active=False, anchor=ANCHOR_X):
        self.frame_count = frame
        self.ball_carrier = bc if bc is not None else Carrier()
        self.defensive_line_anchor = anchor
        self.anchor_locked = True
        self.ruck_x_m = anchor - 10.0
        self.ruck_y_m = 35.0
        self.tes_active = tes_active
        self.defending_team = []
        self.players = []
        self.defending_team_ids = []


def make_body(x=62.0, y=35.0, role=DefenderRole.DEF_LINE_READY, pid=7):
    d = Player(x, y, player_id=pid)
    d.is_defender = True
    d.defender_role = role
    d.max_defensive_speed_mps = 8.3
    d.max_speed_mps = 9.2
    d.target_x = x
    d.target_y = y
    return d


# ═════════════════════════════════════════════════════════════════════
def main():
    print("=" * 78)
    print("POST-MISS PURSUIT ENTITLEMENT CONTRACT")
    print("=" * 78)

    saved = os.environ.pop(pmp.POST_MISS_PURSUIT_ENV_VAR, None)
    saved_win = os.environ.pop("RLM_POST_MISS_PURSUIT_FRAMES", None)
    try:
        # ── P1: the lever ─────────────────────────────────────────────
        check("P1_env_var_name",
              pmp.POST_MISS_PURSUIT_ENV_VAR == "RLM_POST_MISS_PURSUIT",
              "got %r" % (pmp.POST_MISS_PURSUIT_ENV_VAR,))
        check("P1_default_on", pmp.post_miss_pursuit_enabled() is True)
        for off in ("0", "false", "no", "off", ""):
            os.environ[pmp.POST_MISS_PURSUIT_ENV_VAR] = off
            check("P1_disarmed_%r" % off,
                  pmp.post_miss_pursuit_enabled() is False)
        for on in ("1", "true", "yes"):
            os.environ[pmp.POST_MISS_PURSUIT_ENV_VAR] = on
            check("P1_armed_%r" % on,
                  pmp.post_miss_pursuit_enabled() is True)
        os.environ.pop(pmp.POST_MISS_PURSUIT_ENV_VAR, None)

        check("P1_window_default",
              pmp.post_miss_pursuit_window_frames()
              == pmp.POST_MISS_PURSUIT_WINDOW_FRAMES,
              "got %r" % (pmp.post_miss_pursuit_window_frames(),))
        check("P1_window_exceeds_bump_off_freeze",
              pmp.POST_MISS_PURSUIT_WINDOW_FRAMES
              > 1.2 * 60.0,
              "the window must outlast the 1.2 s bump-off freeze")
        os.environ["RLM_POST_MISS_PURSUIT_FRAMES"] = "45"
        check("P1_window_env_override",
              pmp.post_miss_pursuit_window_frames() == 45,
              "got %r" % (pmp.post_miss_pursuit_window_frames(),))
        os.environ["RLM_POST_MISS_PURSUIT_FRAMES"] = "not-a-number"
        check("P1_window_bad_env_falls_back",
              pmp.post_miss_pursuit_window_frames()
              == pmp.POST_MISS_PURSUIT_WINDOW_FRAMES)
        os.environ.pop("RLM_POST_MISS_PURSUIT_FRAMES", None)
        check("P1_source_stamp", pmp.POST_MISS_PURSUIT_SOURCE
              == "post_miss_pursuit")

        # ── P2: arming ───────────────────────────────────────────────
        world = World(frame=500)
        body = make_body()
        check("P2_arms_and_returns_true",
              pmp.arm_post_miss_pursuit(world, body) is True)
        check("P2_deadline_is_frame_plus_window",
              pmp.post_miss_pursuit_until(body)
              == 500 + pmp.POST_MISS_PURSUIT_WINDOW_FRAMES,
              "got %r" % (pmp.post_miss_pursuit_until(body),))
        check("P2_decline_none_body",
              pmp.arm_post_miss_pursuit(world, None) is False)

        carrier = world.ball_carrier
        check("P2_decline_the_carrier_itself",
              pmp.arm_post_miss_pursuit(world, carrier) is False
              and pmp.post_miss_pursuit_until(carrier) is None)

        for role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B,
                     DefenderRole.DUMMY_HALF, DefenderRole.TACKLER_1):
            owner = make_body(role=role)
            check("P2_decline_owner_%s" % getattr(role, "name", role),
                  pmp.arm_post_miss_pursuit(world, owner) is False
                  and pmp.post_miss_pursuit_until(owner) is None)

        fb = make_body()
        fb.fullback_role_active = True
        check("P2_decline_fullback_tag",
              pmp.arm_post_miss_pursuit(world, fb) is False)

        frameless = types.SimpleNamespace(ball_carrier=world.ball_carrier)
        check("P2_decline_no_frame_number",
              pmp.arm_post_miss_pursuit(frameless, make_body()) is False)

        os.environ[pmp.POST_MISS_PURSUIT_ENV_VAR] = "0"
        off_body = make_body()
        check("P2_decline_lever_off",
              pmp.arm_post_miss_pursuit(world, off_body) is False
              and pmp.post_miss_pursuit_until(off_body) is None)
        os.environ.pop(pmp.POST_MISS_PURSUIT_ENV_VAR, None)

        # clear()
        pmp.clear_post_miss_pursuit(body)
        check("P2_clear_drops_the_entitlement",
              pmp.post_miss_pursuit_until(body) is None)

        # ── P4: the premise -- a clean beaten body with a live break ──
        clean = make_body()
        pmp.arm_post_miss_pursuit(world, clean)
        check("P4_clean_beaten_body_is_entitled",
              pmp.owns_post_miss_pursuit(world, clean) is True,
              "the base stub must be entitled before the declines mean "
              "anything")

        # ── P3: the declines ─────────────────────────────────────────
        never = make_body()
        check("P3_decline_never_armed",
              pmp.owns_post_miss_pursuit(world, never) is False)

        expired = make_body()
        pmp.arm_post_miss_pursuit(world, expired)
        past = World(frame=pmp.post_miss_pursuit_until(expired) + 1,
                     bc=world.ball_carrier)
        check("P3_decline_window_expired",
              pmp.owns_post_miss_pursuit(past, expired) is False)
        check("P3_accepts_on_the_last_frame",
              pmp.owns_post_miss_pursuit(world, expired) is True)

        frozen = make_body()
        pmp.arm_post_miss_pursuit(world, frozen)
        frozen.frozen = True
        check("P3_decline_frozen", pmp.owns_post_miss_pursuit(world, frozen)
              is False)

        timer = make_body()
        pmp.arm_post_miss_pursuit(world, timer)
        timer.frozen = False
        timer.freeze_timer = 0.5
        check("P3_decline_live_freeze_timer",
              pmp.owns_post_miss_pursuit(world, timer) is False)

        tes_world = World(tes_active=True, bc=world.ball_carrier)
        tes_body = make_body()
        pmp.arm_post_miss_pursuit(tes_world, tes_body)
        check("P3_decline_live_tes",
              pmp.owns_post_miss_pursuit(tes_world, tes_body) is False)

        for flag in ("tackle_state_active", "in_contact", "tes_attached",
                     "pre_tackle_stance"):
            holder = make_body()
            pmp.arm_post_miss_pursuit(world, holder)
            setattr(holder, flag, True)
            check("P3_decline_%s" % flag,
                  pmp.owns_post_miss_pursuit(world, holder) is False)

        no_bc = World(bc=None)
        nb = make_body()
        pmp.arm_post_miss_pursuit(no_bc, nb)
        no_bc.ball_carrier = None
        check("P3_decline_no_carrier",
              pmp.owns_post_miss_pursuit(no_bc, nb) is False)

        behind = World(bc=Carrier(x=ANCHOR_X - 5.0))
        bb = make_body()
        pmp.arm_post_miss_pursuit(behind, bb)
        check("P3_decline_no_genuine_break",
              pmp.owns_post_miss_pursuit(behind, bb) is False)
        on_line = World(bc=Carrier(x=ANCHOR_X + 1.0))
        ol = make_body()
        pmp.arm_post_miss_pursuit(on_line, ol)
        check("P3_decline_on_the_margin",
              pmp.owns_post_miss_pursuit(on_line, ol) is False)

        owner_live = make_body()
        pmp.arm_post_miss_pursuit(world, owner_live)
        original_owner = pmp._tackle_side_owner_live
        pmp._tackle_side_owner_live = lambda _d: True
        try:
            check("P3_decline_live_tackle_side_owner",
                  pmp.owns_post_miss_pursuit(world, owner_live) is False)
        finally:
            pmp._tackle_side_owner_live = original_owner

        os.environ[pmp.POST_MISS_PURSUIT_ENV_VAR] = "0"
        off = make_body()
        pmp.arm_post_miss_pursuit(world, off)
        off._post_miss_pursuit_until_frame = 99999
        check("P3_decline_lever_off",
              pmp.owns_post_miss_pursuit(world, off) is False)
        os.environ.pop(pmp.POST_MISS_PURSUIT_ENV_VAR, None)

        check("P3_decline_none_body",
              pmp.owns_post_miss_pursuit(world, None) is False)

        # ── P5: the write ────────────────────────────────────────────
        from game.defence.defence_lvl_1 import freeze_expiry_pursuit as fep

        write_world = World(frame=800)
        writer = make_body(x=63.0, y=34.0)
        pmp.arm_post_miss_pursuit(write_world, writer)
        writer.timing_locked = True
        writer.reaction_timer = 1.0
        writer.retreat_delay = 1.0
        writer.lateral_hesitation = 1.0
        writer.step_stagger = 1.0
        writer.step_stagger_active = True
        ok = pmp.pursue_carrier(write_world, writer)
        ceiling = pmp.defensive_ceiling_mps(write_world, writer)
        bc_w = write_world.ball_carrier
        check("P5_returns_true", ok is True)
        check("P5_target_is_the_live_carrier",
              (writer.target_x, writer.target_y) == (float(bc_w.x),
                                                     float(bc_w.y)),
              "target=(%r, %r)" % (writer.target_x, writer.target_y))
        check("P5_state_is_chase",
              writer.movement_state == MovementState.CHASE,
              "state=%r" % (writer.movement_state,))
        check("P5_state_satisfies_closing_posture",
              writer.movement_state in (MovementState.CHASE,
                                        MovementState.SHOOT))
        check("P5_speed_is_the_governed_ceiling",
              writer.move_speed == min(float(writer.max_speed_mps), ceiling)
              and writer.move_speed > 0.0,
              "speed=%r ceiling=%r" % (writer.move_speed, ceiling))
        check("P5_source_stamp",
              writer.target_source == pmp.POST_MISS_PURSUIT_SOURCE)
        check("P5_locks_cleared",
              writer.timing_locked is False
              and writer.reaction_timer == 0.0
              and writer.retreat_delay == 0.0
              and writer.lateral_hesitation == 0.0
              and writer.step_stagger == 0.0
              and writer.step_stagger_active is False)

        untouched = make_body(x=63.0, y=34.0)
        untouched.target_x = 41.5
        untouched.target_y = 22.5
        untouched.movement_state = MovementState.DRIFT
        untouched.move_speed = 1.234
        before = (untouched.target_x, untouched.target_y,
                  untouched.movement_state, untouched.move_speed,
                  getattr(untouched, "target_source", None))
        check("P5_returns_false_when_not_entitled",
              pmp.pursue_carrier(write_world, untouched) is False)
        check("P5_non_entitled_body_left_byte_identical",
              (untouched.target_x, untouched.target_y,
               untouched.movement_state, untouched.move_speed,
               getattr(untouched, "target_source", None)) == before)

        # ── P7: the freeze is NOT bypassed ───────────────────────────
        pinned = make_body(x=63.0, y=34.0)
        pmp.arm_post_miss_pursuit(write_world, pinned)
        pinned.frozen = True
        pinned.target_x = 12.5
        pinned.target_y = 44.5
        pinned.movement_state = MovementState.HOLD
        held = (pinned.target_x, pinned.target_y, pinned.frozen,
                pinned.movement_state)
        check("P7_no_write_while_pinned",
              pmp.pursue_carrier(write_world, pinned) is False)
        check("P7_pinned_body_untouched",
              (pinned.target_x, pinned.target_y, pinned.frozen,
               pinned.movement_state) == held)

        # ── P6: single sourcing ──────────────────────────────────────
        check("P6_ceiling_matches_freeze_expiry_pursuit",
              pmp.defensive_ceiling_mps(None, None)
              == fep.defensive_ceiling_mps(None, None),
              "got %r vs %r" % (pmp.defensive_ceiling_mps(None, None),
                                fep.defensive_ceiling_mps(None, None)))
        pmp_src = src_of("game/defence/defence_lvl_1/post_miss_pursuit.py")
        check("P6_reuses_the_canonical_break_predicate",
              "carrier_past_defensive_line" in pmp_src)
        check("P6_reuses_the_owner_gate",
              "fullback_tackle_side_owner_live" in pmp_src)
        check("P6_reuses_the_recovery_ceiling",
              "defensive_ceiling_mps" in pmp_src
              and "freeze_expiry_pursuit" in pmp_src)
        check("P6_reuses_the_freeze_pin_latch_clear",
              "_clear_freeze_pin_latch" in pmp_src)
        check("P6_exempt_roles_match_the_static_guard",
              pmp._EXEMPT_ROLE_NAMES == lbsg._EXEMPT_ROLE_NAMES,
              "%r vs %r" % (sorted(pmp._EXEMPT_ROLE_NAMES),
                            sorted(lbsg._EXEMPT_ROLE_NAMES)))
        check("P6_no_local_exemption_drift",
              "_EXEMPT_ROLE_NAMES = frozenset" in pmp_src)
        check("P6_import_safety_no_project_module_scope_imports",
              pmp_src.count("\nfrom game.") == 0
              and pmp_src.count("\nimport game.") == 0)

        # ── P8: integration wiring ───────────────────────────────────
        engine_src = src_of("world/open_play_engine.py")
        check("P8_engine_arms_once",
              engine_src.count(
                  "_arm_pursuit(self.world, _beaten_body, bump_off=bump_off)"
              ) == 1,
              "the single miss-teardown site must arm every beaten body")
        check("P8_engine_arms_with_the_bump_off_flag",
              "bump_off=bump_off" in engine_src,
              "the arm must carry the pre-emptive (bump-off) verdict, which "
              "selects the SHORT deadline")
        check("P8_engine_arms_only_for_recorded_beaten_bodies",
              engine_src.find("if _beaten:")
              < engine_src.find("_arm_pursuit(self.world, _beaten_body,"),
              "arming must be inside the `if _beaten:` block")
        check("P8_engine_gates_the_latch_release_on_the_lever",
              "if _pmp_enabled():\n                "
              "self._clear_bump_commit_lock(d)" in engine_src,
              "the plain-path beat-body latch release must be gated on the "
              "pursuit lever")
        check("P8_engine_latch_release_no_longer_bump_off_only",
              "if bump_off:\n                # A bumped-off tackler is "
              "beaten" not in engine_src,
              "the beat-body commitment release must run on BOTH paths")
        check("P8_engine_records_the_beaten_bodies_before_teardown",
              "_beaten = []" in engine_src)

        dbu_src = src_of("game/defence/defence_lvl_1/defensive_behaviour_unified.py")
        check("P8_mmdl_skips_entitled_bodies",
              dbu_src.count("_owns_post_miss_pursuit(world, d)") == 1,
              "found %d" % dbu_src.count("_owns_post_miss_pursuit(world, d)"))
        # The entitlement skip must be immediately followed by the
        # retreat-obligation skip: a beaten body is normally STILL obligated
        # (DEF_RETREAT_10), so if the obligation skip came first the body
        # would be handed the retreat target it no longer owes and the
        # pursuit write would be discarded.
        _pmp_at = dbu_src.find("if _owns_post_miss_pursuit(world, d):")
        _obl_at = dbu_src.find(
            "if is_retreat_obligated(d, world):", _pmp_at + 1
        ) if _pmp_at >= 0 else -1
        check("P8_mmdl_skip_precedes_the_retreat_obligation_skip",
              _pmp_at >= 0 and _obl_at > _pmp_at and (_obl_at - _pmp_at) < 900,
              "pmp@%d obligation@%d" % (_pmp_at, _obl_at))
        check("P8_mmdl_entitlement_skip_does_not_mark_mmdl_moved",
              "if _owns_post_miss_pursuit(world, d):\n"
              "                d.mmdl_target_x = d.target_x" in dbu_src,
              "the entitlement skip must hand the body back to Player.update")

        rm_src = src_of("game/managers/retreat_manager.py")
        check("P8_retreat_writer_exempts_entitled_bodies",
              rm_src.count("_owns_post_miss_pursuit(world, d)") == 1,
              "found %d" % rm_src.count("_owns_post_miss_pursuit(world, d)"))

        lbsg_src = src_of("game/defence/defence_lvl_1/line_break_static_guard.py")
        check("P8_guard_carves_out_entitled_bodies_from_the_obligation_veto",
              "_is_retreat_obligated(d, world) and not "
              "_owns_post_miss_pursuit(" in lbsg_src)
        check("P8_freeze_expiry_pursuit_still_delegates_to_the_guard",
              "is_static_guard_recovery_eligible" in
              src_of("game/defence/defence_lvl_1/freeze_expiry_pursuit.py"),
              "the carve-out reaches the expiry hand-back through this "
              "delegation, so the two writers share one eligible set")

        dm_src = src_of("game/managers/defence_manager.py")
        check("P8_dispatch_writes_the_final_pursuit_target",
              dm_src.count("_owned_pursuit_bodies(") == 1
              and dm_src.count("_pursue_carrier(world, _owned, bc)") == 1)
        check("P8_final_write_precedes_movement_integration",
              dm_src.find("_pursue_carrier(world, _owned, bc)")
              < dm_src.find("# ---- Apply movement ----"),
              "the pursuit write must be the dispatch's LAST target write")

        # ── P9: the band tier ────────────────────────────────────────
        check("P9_stamp_registered_at_the_commit_tier",
              wpb.priority_for_source(pmp.POST_MISS_PURSUIT_SOURCE)
              == wpb.PRIORITY_COMMIT,
              "got %r" % wpb.priority_for_source(
                  pmp.POST_MISS_PURSUIT_SOURCE))
        check("P9_commit_tier_outranks_retreat",
              wpb.PRIORITY_COMMIT > wpb.PRIORITY_RETREAT)

        # ── P10: the retreat writer really does leave it alone ───────
        retreat_world = World(frame=900)
        retreat_world.ruck_x_m = ANCHOR_X - 10.0
        tuners = types.SimpleNamespace(
            retreat_compliance_distance_m=10.0,
            max_defensive_speed_mps=8.3,
        )
        entitled = make_body(x=52.0, y=35.0, pid=11)
        entitled.target_x = 41.5
        pmp.arm_post_miss_pursuit(retreat_world, entitled)
        control = make_body(x=52.0, y=36.0, pid=12)
        control.target_x = 41.5
        spacing = {id(entitled): 35.0, id(control): 36.0}
        rm.apply_forced_retreat(
            retreat_world, [entitled, control], tuners,
            spacing_targets=spacing,
        )
        check("P10_entitled_body_is_exempt_from_the_retreat_write",
              entitled.target_x == 41.5
              and getattr(entitled, "target_source", None)
              != "retreat_non_compliant",
              "target=%r src=%r" % (entitled.target_x,
                                    getattr(entitled, "target_source", None)))
        check("P10_control_body_is_still_retreated",
              abs(control.target_x - ANCHOR_X) < 1e-9
              and control.target_source == "retreat_non_compliant",
              "target=%r src=%r" % (control.target_x, control.target_source))

        print("")




        print("-" * 78)
        if FAILURES:
            print("FAILED %d check(s): %s" % (len(FAILURES), ", ".join(FAILURES)))
            return 1
        print("ALL CHECKS PASSED")
        return 0
    finally:
        os.environ.pop(pmp.POST_MISS_PURSUIT_ENV_VAR, None)
        os.environ.pop("RLM_POST_MISS_PURSUIT_FRAMES", None)
        if saved is not None:
            os.environ[pmp.POST_MISS_PURSUIT_ENV_VAR] = saved
        if saved_win is not None:
            os.environ["RLM_POST_MISS_PURSUIT_FRAMES"] = saved_win


if __name__ == "__main__":
    sys.exit(main())
