"""verify_freeze_expiry_pursuit_contract.py -- the expiry HAND-BACK law.

THE DEFECT
----------
A defender beaten by a line break is marked by
``OpenPlayEngine._handle_broken_tackle`` with ``frozen = True`` and
``freeze_timer = 1.0 s`` (1.2 s on BUMP_OFF).  While that timer is live the
target-generation chain pins ``target_x`` to the body's own ``x`` three
times over (DBU step 7 ``struct_freeze_timer``, the engine's freeze-pin
latch, and the freeze clock's own expiry branch), and EVERY recovery writer
declines the body because they all exempt a live ``freeze_timer`` /
``frozen`` -- including ``line_break_static_guard``, whose entire contract
is to clear a static post-line-break target.  ``target_x == x`` therefore
means the accel-lerp produces ZERO velocity while the carrier runs clear:
``defence_reactivity_audit`` reports the frame as
``STATIC_TARGET_AFTER_LINE_BREAK`` plus ``BAD_DECISION_ON_LINE_BREAK``.

THE LAW
-------
The pin is a MEASUREMENT of where the body fell over, never a command to
hold station.  The expiry tick is the first frame on which the recovery
writers are eligible again, so the hand-back must ALREADY be a live command
rather than a second self-pin.  ``freeze_expiry_pursuit`` owns that
decision; this verifier asserts its contract:

  F1  the ``RLM_FREEZE_EXPIRY_PURSUIT`` lever, its default and spellings;
  F2  SINGLE-SOURCED eligibility: ``is_static_guard_eligible`` IS the
      recovery predicate plus the two freeze gates, and the recovery
      predicate ignores ``frozen`` / ``freeze_timer`` entirely -- so the
      guard and the hand-back can never disagree about which bodies are
      genuine line bodies;
  F3  the base stub really is a clean, unowned, recoverable line body
      (the premise every other check rests on);
  F4  the hand-back DECLINES every case it must: no carrier, live TES, a
      marker / DUMMY_HALF / fullback-tagged owner, a tackle-state holder,
      a commit / pursuit-commit body, an attached / in-contact body, a
      pre-tackle stance holder, no genuine break, lever disarmed;
  F5  on a genuine break for a genuine line body it writes the FULL
      pursuit contract (point, state, freeze, latch, locks, ceiling speed,
      source stamp) and returns True;
  F6  a live ``frozen`` flag and a live ``freeze_timer`` -- the exact
      state of a beaten defender -- do NOT block the hand-back;
  F7  INTEGRATION through the engine's single expiry site
      (``decay_freeze_timers``): expiry on a break hands back a live
      pursuit point, expiry without a break keeps the legacy self-pin, the
      disarmed lever keeps the legacy self-pin, and a RUNNING tick leaves
      the target alone;
  F8  the engine wires the hand-back at exactly one site, inside
      ``decay_freeze_timers``, and gates the legacy self-pin on it;
  F9  the hand-back's source single-sources the exemption set, the break
      predicate and the owner gate (so drift is caught here);
  F10 the audit that reports the symptom still defines both labels.

Run:  python verify_freeze_expiry_pursuit_contract.py
"""

import os
import re
import sys

sys.path.insert(0, ".")

import world.open_play_engine as ope
from player.movement_states import MovementState
from player.player import DefenderRole
from game.defence.defence_lvl_1 import line_break_static_guard as lbsg
from game.defence.defence_lvl_1 import freeze_expiry_pursuit as fep

DT = 1.0 / 60.0
ANCHOR_X = 60.0
FAILURES = []

ENGINE_SRC = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "world", "open_play_engine.py"
)
FEP_SRC = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "game", "defence", "defence_lvl_1", "freeze_expiry_pursuit.py",
)
AUDIT_SRC = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "game", "debug", "defence_reactivity_audit.py",
)


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s  %s" % (name, detail))
        FAILURES.append(name)


class LineBody:
    """A beaten line defender: frozen, live timer, target pinned to own x."""

    def __init__(self, **kw):
        self.x = kw.get("x", 10.0)
        self.y = kw.get("y", 35.0)
        # The pin: target sitting on the body's own position.
        self.target_x = kw.get("target_x", 10.0)
        self.target_y = kw.get("target_y", 35.0)
        self.frozen = kw.get("frozen", True)
        self.freeze_timer = kw.get("freeze_timer", DT * 0.5)
        self.movement_state = kw.get("movement_state", MovementState.DRIFT)
        self.move_speed = kw.get("move_speed", 1.245)
        self.max_speed_mps = kw.get("max_speed_mps", 9.2)
        self.timing_locked = True
        self.reaction_timer = 1.0
        self.retreat_delay = 1.0
        self.lateral_hesitation = 1.0
        self.step_stagger = 1.0
        self.step_stagger_active = True
        self.target_source = "struct_freeze_timer"
        self.defender_role = kw.get("defender_role", DefenderRole.DEF_LINE_READY)
        self._freeze_pin_active = True
        self._freeze_pin_x = self.x
        self._freeze_pin_y = self.y


class Carrier:
    def __init__(self, x=70.0, y=40.0):
        self.x = x
        self.y = y
        self.is_ball_carrier = True


class LineWorld:
    """Minimum world for ``defensive_line_anchor_m`` + the break predicate."""

    def __init__(self, players, bc, anchor_x=ANCHOR_X, tes_active=False):
        self.players = list(players)
        self.ball_carrier = bc
        self.defensive_line_anchor = anchor_x
        self.anchor_locked = True
        self.ruck_x_m = anchor_x - 10.0
        self.tes_active = tes_active


def _write_set_ok(d, bc, world) -> bool:
    """Every field the full pursuit contract must own."""
    ceiling = fep.defensive_ceiling_mps(world, d)
    return (
        (d.target_x, d.target_y) == (float(bc.x), float(bc.y))
        and d.movement_state == MovementState.CHASE
        and d.frozen is False
        and d.timing_locked is False
        and d.reaction_timer == 0.0
        and d.retreat_delay == 0.0
        and d.lateral_hesitation == 0.0
        and d.step_stagger == 0.0
        and d.step_stagger_active is False
        and d.move_speed > 0.0
        and d.move_speed == min(float(d.max_speed_mps), ceiling)
        and d.target_source == fep.FREEZE_EXPIRY_PURSUIT_SOURCE
        and getattr(d, "_freeze_pin_active", None) is False
        and not hasattr(d, "_freeze_pin_x")
        and not hasattr(d, "_freeze_pin_y")
    )


def main() -> int:
    print("=" * 78)
    print("FREEZE EXPIRY PURSUIT CONTRACT")
    print("=" * 78)

    # ── F1: the lever ─────────────────────────────────────────────────
    check("F1_env_var_name",
          fep.FREEZE_EXPIRY_PURSUIT_ENV_VAR == "RLM_FREEZE_EXPIRY_PURSUIT",
          "got %r" % (fep.FREEZE_EXPIRY_PURSUIT_ENV_VAR,))
    saved = os.environ.pop(fep.FREEZE_EXPIRY_PURSUIT_ENV_VAR, None)
    try:
        check("F1_default_on", fep.freeze_expiry_pursuit_enabled() is True)
        for off in ("0", "false", "no", "off", ""):
            os.environ[fep.FREEZE_EXPIRY_PURSUIT_ENV_VAR] = off
            check("F1_disarmed_%r" % off,
                  fep.freeze_expiry_pursuit_enabled() is False)
        for on in ("1", "true", "yes"):
            os.environ[fep.FREEZE_EXPIRY_PURSUIT_ENV_VAR] = on
            check("F1_armed_%r" % on,
                  fep.freeze_expiry_pursuit_enabled() is True)
        os.environ.pop(fep.FREEZE_EXPIRY_PURSUIT_ENV_VAR, None)

        bc = Carrier()
        world = LineWorld([], bc)

        # ── F2: single-sourced eligibility ───────────────────────────
        for frozen in (False, True):
            for timer in (0.0, 0.5):
                body = LineBody(frozen=frozen, freeze_timer=timer)
                recovery = lbsg.is_static_guard_recovery_eligible(body, world)
                gate = (not frozen) and (timer <= 0.0)
                check(
                    "F2_guard_is_recovery_plus_freeze_gates"
                    "_frozen=%s_timer=%s" % (frozen, timer),
                    lbsg.is_static_guard_eligible(body, world) == (recovery and gate),
                    "guard=%r recovery=%r gate=%r"
                    % (lbsg.is_static_guard_eligible(body, world), recovery, gate),
                )
        # the recovery predicate IGNORES both freeze fields
        base = LineBody(frozen=True, freeze_timer=5.0)
        check("F2_recovery_ignores_freeze_fields",
              lbsg.is_static_guard_recovery_eligible(base, world) is True)
        base.frozen = False
        base.freeze_timer = 0.0
        check("F2_recovery_stable_across_freeze_fields",
              lbsg.is_static_guard_recovery_eligible(base, world) is True)
        # and the guard itself DOES refuse both
        check("F2_guard_refuses_frozen",
              lbsg.is_static_guard_eligible(
                  LineBody(frozen=True, freeze_timer=0.0), world) is False)
        check("F2_guard_refuses_live_timer",
              lbsg.is_static_guard_eligible(
                  LineBody(frozen=False, freeze_timer=0.5), world) is False)

        # ── F3: the premise -- a clean, unowned line body ────────────
        probe = LineBody()
        from game.defence.defence_lvl_1.pretackle_corridor import (
            is_corridor_owned,
        )
        from game.defence.defence_lvl_1.retreat_obligation import (
            is_retreat_obligated,
        )
        check("F3_stub_not_corridor_owned", is_corridor_owned(probe) is False)
        check("F3_stub_not_retreat_obligated",
              is_retreat_obligated(probe, world) is False)
        check("F3_stub_is_recovery_eligible",
              lbsg.is_static_guard_recovery_eligible(probe, world) is True)
        check("F3_break_is_live",
              fep.is_expiry_pursuit_eligible(probe, world) is True,
              "the base stub must be eligible before the declines mean "
              "anything")

        # ── F4: the declines ─────────────────────────────────────────
        no_bc_world = LineWorld([probe], None)
        check("F4_declines_no_carrier",
              fep.hand_back_expiry_pursuit(probe, no_bc_world) is False
              and probe.target_x == 10.0)

        tes_world = LineWorld([probe], bc, tes_active=True)
        check("F4_declines_live_tes",
              fep.hand_back_expiry_pursuit(probe, tes_world) is False)

        for role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B,
                     DefenderRole.DUMMY_HALF):
            body = LineBody(defender_role=role)
            check("F4_declines_role_%s" % getattr(role, "name", role),
                  fep.hand_back_expiry_pursuit(body, world) is False
                  and body.target_x == 10.0)

        for flag in ("fullback_role_active", "tackle_state_active",
                     "_tackle_commit_active", "_pursuit_commit_active",
                     "tes_attached", "in_contact", "pre_tackle_stance"):
            body = LineBody()
            setattr(body, flag, True)
            check("F4_declines_%s" % flag,
                  fep.hand_back_expiry_pursuit(body, world) is False
                  and body.target_x == 10.0)

        # no genuine break: the carrier has NOT passed the line
        behind = LineWorld([probe], Carrier(x=ANCHOR_X - 5.0))
        check("F4_declines_no_break",
              fep.hand_back_expiry_pursuit(probe, behind) is False
              and probe.target_x == 10.0)
        # exactly ON the margin is still not past it (canonical 1.0 m law)
        on_line = LineWorld([probe], Carrier(x=ANCHOR_X + 1.0))
        check("F4_declines_on_the_margin",
              fep.hand_back_expiry_pursuit(probe, on_line) is False)

        os.environ[fep.FREEZE_EXPIRY_PURSUIT_ENV_VAR] = "0"
        off_body = LineBody()
        check("F4_declines_lever_off",
              fep.hand_back_expiry_pursuit(off_body, world) is False
              and off_body.target_x == 10.0)
        os.environ.pop(fep.FREEZE_EXPIRY_PURSUIT_ENV_VAR, None)

        # ── F5: the full pursuit contract ────────────────────────────
        body = LineBody(move_speed=1.245, max_speed_mps=9.2)
        ok = fep.hand_back_expiry_pursuit(body, world)
        check("F5_returns_true", ok is True)
        check("F5_writes_full_pursuit_contract",
              _write_set_ok(body, bc, world),
              "target=(%r, %r) state=%s speed=%r src=%r frozen=%r latch=%r"
              % (body.target_x, body.target_y, body.movement_state,
                 body.move_speed, body.target_source, body.frozen,
                 getattr(body, "_freeze_pin_x", None)))
        check("F5_target_is_the_carrier",
              (body.target_x, body.target_y) == (bc.x, bc.y))
        check("F5_state_satisfies_closing_posture",
              body.movement_state in (MovementState.CHASE, MovementState.SHOOT),
              "the pre-tackle classifier's is_closing_posture gate")

        # ── F5b: the speed is the GOVERNED ceiling, not the physical max ──
        from game.defence.defence_lvl_1.defensive_tuners import (
            DefenceTuners,
        )
        authority = float(DefenceTuners().max_defensive_speed_mps)
        check("F5b_ceiling_is_the_tuner_authority",
              fep.defensive_ceiling_mps(None, None) == authority,
              "got %r authority %r"
              % (fep.defensive_ceiling_mps(None, None), authority))
        check("F5b_fast_body_held_to_ceiling_not_physical_max",
              body.move_speed == min(float(body.max_speed_mps), authority)
              and body.move_speed < float(body.max_speed_mps),
              "speed=%r ceiling=%r physical=%r"
              % (body.move_speed, authority, body.max_speed_mps))

        # a body SLOWER than the ceiling is clamped DOWN to its own max
        slow = LineBody(max_speed_mps=authority - 2.0)
        fep.hand_back_expiry_pursuit(slow, world)
        check("F5b_slow_body_clamped_to_own_max",
              slow.move_speed == float(slow.max_speed_mps),
              "speed=%r own_max=%r" % (slow.move_speed, slow.max_speed_mps))

        # a tuner object stamped on the world IS the authority
        class FakeTuners:
            max_defensive_speed_mps = 7.0

        tuned_world = LineWorld([], bc)
        tuned_world.def_tuners = FakeTuners()
        tuned_body = LineBody()
        fep.hand_back_expiry_pursuit(tuned_body, tuned_world)
        check("F5b_world_tuner_respected",
              fep.defensive_ceiling_mps(tuned_world, tuned_body) == 7.0
              and tuned_body.move_speed == 7.0,
              "speed=%r" % tuned_body.move_speed)
        check("F5b_agrees_with_static_guard_ceiling_rule",
              tuned_body.move_speed
              == min(float(tuned_body.max_speed_mps),
                     float(FakeTuners.max_defensive_speed_mps)),
              "the two recovery writers must restore the same pace")

        # ── F6: the beat-defender state does not block the hand-back ──
        beat = LineBody(frozen=True, freeze_timer=ope.BUMP_OFF_DEFENDER_FREEZE_S)
        check("F6_frozen_with_live_timer_still_handed_back",
              fep.hand_back_expiry_pursuit(beat, world) is True
              and beat.frozen is False
              and beat.freeze_timer > 0.0,
              "timer=%r" % beat.freeze_timer)
        # ...but a body with NO timer at all is not the expiry case and is
        # still handed back only because eligibility does not read the timer;
        # that is intended: the caller is the expiry branch.
        check("F6_eligibility_ignores_timer",
              fep.is_expiry_pursuit_eligible(
                  LineBody(frozen=False, freeze_timer=0.0), world) is True)

        # ── F7: integration through the engine's expiry site ─────────
        # (a) expiry WITH a break -> live pursuit point
        body = LineBody(freeze_timer=DT * 0.5)
        world_a = LineWorld([body], bc)
        ope.decay_freeze_timers(world_a, DT)
        check("F7_expiry_on_break_writes_pursuit",
              body.freeze_timer == 0.0
              and (body.target_x, body.target_y) == (bc.x, bc.y)
              and body.target_source == fep.FREEZE_EXPIRY_PURSUIT_SOURCE
              and body.movement_state == MovementState.CHASE,
              "target=(%r, %r) src=%r" % (body.target_x, body.target_y,
                                          body.target_source))
        check("F7_expiry_on_break_clears_latch",
              body._freeze_pin_active is False
              and not hasattr(body, "_freeze_pin_x"))

        # (b) expiry with NO break -> the legacy self-pin, unchanged
        body = LineBody(freeze_timer=DT * 0.5)
        world_b = LineWorld([body], Carrier(x=ANCHOR_X - 5.0))
        ope.decay_freeze_timers(world_b, DT)
        check("F7_expiry_without_break_keeps_self_pin",
              body.freeze_timer == 0.0
              and (body.target_x, body.target_y) == (body.x, body.y)
              and body.target_source == "struct_freeze_timer",
              "target=(%r, %r)" % (body.target_x, body.target_y))

        # (c) disarmed lever -> the legacy self-pin, unchanged
        os.environ[fep.FREEZE_EXPIRY_PURSUIT_ENV_VAR] = "0"
        body = LineBody(freeze_timer=DT * 0.5)
        world_c = LineWorld([body], bc)
        ope.decay_freeze_timers(world_c, DT)
        check("F7_disarmed_lever_keeps_self_pin",
              body.freeze_timer == 0.0
              and (body.target_x, body.target_y) == (body.x, body.y),
              "target=(%r, %r)" % (body.target_x, body.target_y))
        os.environ.pop(fep.FREEZE_EXPIRY_PURSUIT_ENV_VAR, None)

        # (d) a RUNNING tick must not touch the target at all
        body = LineBody(freeze_timer=0.5, target_x=40.0, target_y=60.0)
        world_d = LineWorld([body], bc)
        ope.decay_freeze_timers(world_d, DT)
        check("F7_running_tick_leaves_target_alone",
              (body.target_x, body.target_y) == (40.0, 60.0)
              and abs(body.freeze_timer - (0.5 - DT)) < 1e-12,
              "target=(%r, %r) timer=%r"
              % (body.target_x, body.target_y, body.freeze_timer))

        # (e) a body already at 0.0 is never written by the clock
        body = LineBody(freeze_timer=0.0, target_x=40.0)
        world_e = LineWorld([body], bc)
        ticked = ope.decay_freeze_timers(world_e, DT)
        check("F7_zero_timer_not_ticked",
              ticked == 0 and body.target_x == 40.0,
              "ticked=%r target=%r" % (ticked, body.target_x))
    finally:
        os.environ.pop(fep.FREEZE_EXPIRY_PURSUIT_ENV_VAR, None)
        if saved is not None:
            os.environ[fep.FREEZE_EXPIRY_PURSUIT_ENV_VAR] = saved

    # ── F8: the engine wires it at exactly one site ──────────────────
    with open(ENGINE_SRC, "r", encoding="utf-8") as fh:
        src = fh.read()
    call = "_hand_back_expiry_pursuit("
    n_calls = src.count(call)
    check("F8_engine_calls_hand_back_once", n_calls == 1,
          "found %d call sites" % n_calls)
    idx_call = src.find(call)
    idx_clock = src.find("def decay_freeze_timers(")
    idx_next_def = src.find("\nPOST_PTB_IMMUNITY_FRAMES", idx_clock)
    check("F8_call_inside_the_freeze_clock",
          0 <= idx_clock < idx_call < idx_next_def,
          "clock@%d call@%d next@%d" % (idx_clock, idx_call, idx_next_def))
    check("F8_legacy_self_pin_gated_on_hand_back",
          "if not _handed_back and hasattr(p, \"target_x\"):" in src,
          "the historical self-pin must be the fallback branch")

    # ── F9: the hand-back single-sources its inputs ──────────────────
    with open(FEP_SRC, "r", encoding="utf-8") as fh:
        fep_src = fh.read()
    check("F9_imports_recovery_eligibility",
          "is_static_guard_recovery_eligible" in fep_src,
          "must reuse the guard's exemption set, not restate it")
    check("F9_imports_the_break_predicate",
          "carrier_past_defensive_line" in fep_src,
          "must reuse the canonical break definition")
    check("F9_imports_the_owner_gate",
          "fullback_tackle_side_owner_live" in fep_src,
          "a stamp is not ownership: must ask the owner gate")
    check("F9_no_local_exemption_list",
          "_EXEMPT_ROLE_NAMES" not in fep_src,
          "the exemption set must not be duplicated in this module")

    # ── F10: the reported symptom keeps its definition ───────────────
    with open(AUDIT_SRC, "r", encoding="utf-8") as fh:
        audit_src = fh.read()
    check("F10_audit_defines_static_target_label",
          "STATIC_TARGET_AFTER_LINE_BREAK" in audit_src)
    check("F10_audit_defines_bad_decision_label",
          "BAD_DECISION_ON_LINE_BREAK" in audit_src)
    check("F10_audit_static_tolerance_is_0p1",
          "abs(tx - p.x) < 0.1" in audit_src,
          "the 10 cm pin the hand-back removes")

    print("")
    print("-" * 78)
    if FAILURES:
        print("FAILED %d check(s): %s" % (len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
