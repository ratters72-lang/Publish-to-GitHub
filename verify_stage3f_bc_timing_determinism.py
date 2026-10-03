"""verify_stage3f_bc_timing_determinism.py - STAGE 3F BC-timing determinism proof.

Proves the open-play BC timing gates are wall-clock (dt-accumulated), NOT
frame-counted, so spacing and pacing stop inheriting nondeterministic
frame drift:

  [1] SOURCE GUARDS - the seconds clock exists on the world, is wired into
      OpenPlayEngine (accumulate / reset / sync), and every former
      frame-count gate now reads it (with a frames/60 fallback).
  [2] QUIET WINDOW - DefensiveTiming holds the SECONDARY+PASSIVE timing
      locks cleared for exactly ``post_ruck_quiet_window_s`` of SIMULATED
      time at dt = 1/30, 1/60 and 1/120.  The old ``frames_since_ptb < 30``
      gate gave three different wall-clock windows (1.0s / 0.5s / 0.25s).
  [3] STEP-UP RAMP - the governor's dt-scaled ramp reaches full step-up in
      the same simulated seconds at all three tick rates (and matches the
      legacy 60fps behaviour exactly).
  [4] ESCAPE WINDOW - the AttackController BC scoot expires at the same
      simulated seconds at all three tick rates.
  [5] LEGACY FALLBACK - worlds carrying ONLY ``frames_since_ptb`` (the old
      verifier-fixture shape) still resolve through the frames/60 fallback.
  [6] POST-CATCH STABILITY - the post-catch clamp releases after
      ``post_catch_stability_s`` of simulated time at any tick rate.

Run: python verify_stage3f_bc_timing_determinism.py
"""

import math
import os
import sys
import types

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" - {detail}" if detail else ""))
    if not condition:
        failures.append(name)


DT_RATES = (1.0 / 30.0, 1.0 / 60.0, 1.0 / 120.0)


def make_world():
    """Fresh WorldState configured for structured OPEN_PLAY timing."""
    from world.world_state import WorldState, GameSubState
    w = WorldState()
    w.sub_state = GameSubState.OPEN_PLAY
    w.role_governs = False
    w.in_pursuit_mode = False
    w.first_tackle_not_made = False
    w.defensive_line_anchor = 70.0   # BC stays behind the line
    w.ruck_x_m = 60.0
    w.ruck_y_m = 35.0
    w.frames_since_ptb = 0
    w.seconds_since_ptb = 0.0
    return w


def make_defender(pid, x, y):
    from player.player import Player, Team
    d = Player(x=x, y=y, player_id=pid)
    d.team = Team.RECEIVING
    d.is_defender = True
    d.defender_role = None
    d.depth_class = "SECONDARY"
    d.frozen = False
    d.reaction_timer = 0.0
    d.retreat_delay = 0.0
    d.lateral_hesitation = 0.0
    d.step_stagger = 0.0
    d.step_stagger_active = False
    d.timing_locked = False
    return d


def main():
    print("=" * 72)
    print("STAGE 3F BC-TIMING DETERMINISM - SPACING/PACING FRAME-DRIFT FIX")
    print("=" * 72)

    # --------------------------------------------------------------
    print("")
    print("[1] Source guards")
    ope_src = open("world/open_play_engine.py",
                   encoding="utf-8", errors="replace").read()
    ws_src = open("world/world_state.py",
                  encoding="utf-8", errors="replace").read()
    dt_src = open("game/defence/defence_lvl_1/defensive_timing.py",
                  encoding="utf-8", errors="replace").read()
    ac_src = open("game/attack/attack_lvl_4/attack_controller.py",
                  encoding="utf-8", errors="replace").read()
    gov_src = open(
        "game/defence/defence_lvl_1/defensive_line_governor.py",
        encoding="utf-8", errors="replace").read()
    # The step-up/down ramp rates are LINE-SHAPE (compression-timing) tuners
    # and now live in the dedicated ``defensive_line_shape_tuners.py`` module
    # that ``DefenceTuners`` INHERITS; ``post_catch_stability_s`` stayed in
    # ``defensive_tuners.py``.  The source scan therefore reads BOTH modules.
    tun_src = ""
    for _tuner_mod in ("game/defence/defence_lvl_1/defensive_line_shape_tuners.py",
                       "game/defence/defence_lvl_1/defensive_tuners.py"):
        tun_src += open(_tuner_mod, encoding="utf-8",
                        errors="replace").read() + "\n"

    check("world carries seconds_since_ptb",
          "seconds_since_ptb: float = 999.0" in ws_src)
    check("OPE accumulates the seconds clock beside the frame counter",
          "self.seconds_since_ptb += dt" in ope_src)
    check("OPE resets the seconds clock on PTB completion",
          "self.frames_since_ptb = 0\n        self.seconds_since_ptb = 0.0"
          in ope_src)
    check("OPE syncs the seconds clock to the world",
          "self.world.seconds_since_ptb = self.seconds_since_ptb"
          in ope_src)
    check("timing layer reads the seconds clock with frames/60 fallback",
          'getattr(\n            world,\n            "seconds_since_ptb",'
          in dt_src and "frames_since_ptb / 60.0" in dt_src)
    check("attack controller reads the seconds clock with fallback",
          'getattr(world, "seconds_since_ptb", None)' in ac_src
          and "_escape_window_frames / 60.0" in ac_src)
    check("governor step-up ramp is dt-scaled",
          "prev + step_up_rate * dt" in gov_src
          and "prev - step_down_rate * dt" in gov_src)
    check("step-up tuners converted to per-second",
          "self.step_up_rate: float = 6.0" in tun_src
          and "self.step_down_rate: float = 3.0" in tun_src)
    check("post-catch stability has a seconds twin",
          "self.post_catch_stability_s: float = 10.0 / 60.0" in tun_src)

    # --------------------------------------------------------------
    print("")
    print("[2] Post-ruck quiet window is simulated-time based")
    from game.defence.defence_lvl_1.defensive_timing import DefensiveTiming
    from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners

    tuners = DefenceTuners()
    quiet_s = float(getattr(tuners, "post_ruck_quiet_window_s", 0.5))

    boundaries = []
    for dt in DT_RATES:
        w = make_world()
        bc = make_defender(900, 55.0, 35.0)
        bc.possessed = True
        w.ball_carrier = bc
        d = make_defender(901, 62.0, 36.0)
        tm = DefensiveTiming(tuners)

        # NOTE: the probe defender must be PASSIVE — _update_retreat_
        # hesitation unconditionally clears reaction_timer for PRIMARY/
        # SECONDARY every frame, so only a PASSIVE defender can carry an
        # armed reaction timer across a full update pass outside the
        # quiet window (exactly the population the window protects).
        d.depth_class = "PASSIVE"

        sim_t = 0.0
        boundary = None
        # Pre-arm a lock each frame BEFORE update: inside the quiet window
        # update() must force-clear it; outside, it must survive (decay).
        for _ in range(4000):
            d.reaction_timer = 5.0
            tm.update(w, [d], dt)
            sim_t += dt
            if d.reaction_timer > 0.0:
                boundary = sim_t - dt  # last frame still inside the window
                break
            w.seconds_since_ptb += dt
        boundaries.append(boundary)

    for dt, b in zip(DT_RATES, boundaries):
        check(f"quiet window ends ~{quiet_s:.2f}s at dt={dt:.5f}",
              b is not None and abs(b - quiet_s) <= dt + 1e-9,
              f"boundary={b}")

    if all(b is not None for b in boundaries):
        lo, hi = min(boundaries), max(boundaries)
        check("quiet-window length identical across tick rates "
              "(<= 1 coarse step)",
              hi - lo <= DT_RATES[0] + 1e-9,
              f"spread={hi - lo:.5f}s")

    # --------------------------------------------------------------
    print("")
    print("[3] Governor step-up ramp is dt-scaled")
    from game.defence.defence_lvl_1.defensive_line_governor import (
        DefensiveLineGovernor,
    )
    from game.defence.defence_lvl_1.defensive_geometry import DefensiveGeometry

    gov = DefensiveLineGovernor(tuners, geometry=DefensiveGeometry(tuners))
    up_rate = float(getattr(tuners, "step_up_rate", 6.0))
    expected_full_s = 1.0 / up_rate  # 0->1.0 at `up_rate` per second

    ramp_times = []
    for dt in DT_RATES:
        state = 0.0
        sim_t = 0.0
        while state < 1.0 and sim_t < 5.0:
            state = DefensiveLineGovernor._smooth_step(
                state, 0.0, 12.0,
                float(getattr(tuners, "step_up_rate")),
                float(getattr(tuners, "step_down_rate")),
                dt,
            )
            sim_t += dt
        ramp_times.append(sim_t)

    for dt, t in zip(DT_RATES, ramp_times):
        check(f"step-up full ramp ~{expected_full_s:.4f}s at dt={dt:.5f}",
              abs(t - expected_full_s) <= dt + 1e-9,
              f"time={t:.5f}s")
    check("step-up ramp identical across tick rates (<= 1 coarse step)",
          max(ramp_times) - min(ramp_times) <= DT_RATES[0] + 1e-9,
          f"spread={max(ramp_times) - min(ramp_times):.5f}s")
    # Legacy equivalence: at 60fps the old per-frame 0.1 also took 10 frames.
    check("60fps behaviour unchanged (10 frames to full step-up)",
          abs(ramp_times[1] - 10.0 / 60.0) <= 1.0 / 60.0 + 1e-9,
          f"t={ramp_times[1]:.5f}s")

    # --------------------------------------------------------------
    print("")
    print("[4] BC escape window is simulated-time based")
    from game.attack.attack_lvl_4.attack_controller import AttackController
    from world.world_state import GameSubState

    class _FakeGeometry:
        @staticmethod
        def sideline_geometry(bc):
            return None

        @staticmethod
        def forward_corridor(bc):
            return None

        @staticmethod
        def defender_cone(bc, defs):
            return None

        @staticmethod
        def lane_geometry(bc):
            return None

        @staticmethod
        def gap_geometry(bc, defs):
            return None

    class _FakeRunLogic:
        @staticmethod
        def compute_run_vector(bc, geom):
            return (1.0, 0.0)

    class _FakeBC:
        def __init__(self):
            self.x = 65.0
            self.y = 35.0
            self.vx = 0.0
            self.vy = 0.0
            self.max_speed_mps = 8.0
            self.target_x = self.x
            self.target_y = self.y
            self.move_speed = 0.0

    ac = AttackController(_FakeGeometry(), None, _FakeRunLogic())
    escape_frames = int(getattr(make_world(), "escape_window_frames", 20))
    expected_escape_s = escape_frames / 60.0

    escape_times = []
    for dt in DT_RATES:
        w = make_world()
        w.escape_angle_deg = 45
        bc = _FakeBC()
        sim_t = 0.0
        boundary = None
        for _ in range(2000):
            ac.apply_vector(bc, w, dt)
            sim_t += dt
            in_window = bc.move_speed > bc.max_speed_mps * 1.05
            if not in_window:
                boundary = sim_t - dt
                break
            w.seconds_since_ptb += dt
        escape_times.append(boundary)

    for dt, b in zip(DT_RATES, escape_times):
        check(f"escape window ends ~{expected_escape_s:.4f}s at dt={dt:.5f}",
              b is not None and abs(b - expected_escape_s) <= dt + 1e-9,
              f"boundary={b}")
    check("escape window identical across tick rates (<= 1 coarse step)",
          max(escape_times) - min(escape_times) <= DT_RATES[0] + 1e-9,
          f"spread={max(escape_times) - min(escape_times):.5f}s")

    # --------------------------------------------------------------
    print("")
    print("[5] Legacy fixture fallback (frames_since_ptb only)")
    # A world WITHOUT seconds_since_ptb must fall back to frames/60.
    legacy_outside = types.SimpleNamespace(
        role_governs=False,
        ball_carrier=None,
        ball=None,
        first_receiver=None,
        defensive_line_anchor=70.0,
        frames_since_ptb=100,          # 100/60 = 1.67s -> OUTSIDE window
    )
    legacy_inside = types.SimpleNamespace(
        role_governs=False,
        ball_carrier=None,
        ball=None,
        first_receiver=None,
        defensive_line_anchor=70.0,
        frames_since_ptb=10,           # 10/60 = 0.167s -> INSIDE window
    )
    tm = DefensiveTiming(tuners)
    d_out = make_defender(910, 62.0, 36.0)
    d_out.reaction_timer = 5.0
    tm.update(legacy_outside, [d_out], 1.0 / 60.0)
    check("legacy fixture past grace keeps its timer (fallback 100/60)",
          d_out.reaction_timer > 0.0,
          f"reaction_timer={d_out.reaction_timer:.3f}")

    d_in = make_defender(911, 62.0, 36.0)
    d_in.reaction_timer = 5.0
    tm.update(legacy_inside, [d_in], 1.0 / 60.0)
    check("legacy fixture inside grace clears its timer (fallback 10/60)",
          d_in.reaction_timer == 0.0,
          f"reaction_timer={d_in.reaction_timer:.3f}")

    # --------------------------------------------------------------
    print("")
    print("[6] Post-catch stability release is simulated-time based")
    from game.defence.defence_lvl_1.defensive_behaviour_unified import (
        DefensiveBehaviourUnified,
    )

    stability_s = float(getattr(tuners, "post_catch_stability_s",
                                10.0 / 60.0))
    release_times = []
    for dt in DT_RATES:
        w = make_world()
        w.dt = dt
        w.frame_count = 1000
        bc = make_defender(920, 55.0, 35.0)
        bc.possessed = True
        w.ball_carrier = bc
        d = make_defender(921, 62.0, 36.0)
        d.depth_class = "PASSIVE"
        d._prev_depth_class = "PASSIVE"
        d.defended_cap_remaining_s = 8.0
        dbu = DefensiveBehaviourUnified(tuners)

        sim_t = 0.0
        released_at = None
        for _ in range(2000):
            dbu._finalise_lateral_targets(w, [d])
            sim_t += dt
            if d.defended_cap_remaining_s <= 0.0:
                released_at = sim_t
                break
        release_times.append(released_at)

    for dt, t in zip(DT_RATES, release_times):
        check(f"clamp releases ~{stability_s:.4f}s at dt={dt:.5f}",
              t is not None and abs(t - stability_s) <= dt + 1e-9,
              f"time={t}")
    check("stability release identical across tick rates (<= 1 coarse step)",
          max(release_times) - min(release_times) <= DT_RATES[0] + 1e-9,
          f"spread={max(release_times) - min(release_times):.5f}s")

    print("")
    print("=" * 72)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED - BC timing is wall-clock "
          "deterministic; spacing/pacing no longer inherit frame drift")
    sys.exit(0)


if __name__ == "__main__":
    main()
