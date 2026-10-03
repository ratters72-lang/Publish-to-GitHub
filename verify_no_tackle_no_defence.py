"""
verify_no_tackle_no_defence.py STAGE 11 verifier.

Confirms the engaged-only no-tackle / no-defence gate (2026-09-09):

  A. Static wiring:
       - ``no_tackle_no_defence_active`` imported into open_play_engine;
       - the gate guards ``run_frame_preamble`` (preamble skipped when active);
       - the gate routes ``update_open_play`` into ``_run_idle_open_play_pursuit``
         when active;
       - ``_run_idle_open_play_pursuit`` exists and is the cheap path (never
         calls run_frame_preamble / update_open_play, never writes depth roles,
         never touches the frame dicts).
  B. Predicate runtime probe (engaged-only form):
       - canary OFF          -> False (legacy byte-identical);
       - canary ON, no BC    -> False (kickoff frames run defence);
       - canary ON, defender CLOSE (<radius) -> False (engaged -> defence runs);
       - canary ON, defender FAR  (>radius)  -> True  (idle -> suppress);
       - canary ON, contact on defender -> False;  contact on BC -> False;
       - canary ON, non-OPEN_PLAY phase -> False.
  C. A/B perf on a REAL headless boot: drive N idle OPEN_PLAY frames with the
     gate OFF vs ON, timing ``open_play.update(dt)`` and counting the
     ``run_frame_preamble`` / ``update_open_play`` dispatches.  The ON run must
     dispatch zero of those (gate active) and be measurably cheaper.
  D. Tackle still forms: with the gate ON, one defender closing into engagement
     releases the gate (predicate -> False) and the full dispatch runs, so real
     tackle opportunities are NOT suppressed.

Run:  python verify_no_tackle_no_defence.py
Exit 0 on success, 1 on failure.
"""
import math
import os
import re
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, os.getcwd())
try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

FAILURES = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" - " + str(detail)) if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def read(p):
    with open(os.path.join(os.getcwd(), p), encoding="utf-8", errors="replace") as f:
        return f.read()


# ─────────────────────────────────────────────────────────────────────────
# A. STATIC WIRING
# ─────────────────────────────────────────────────────────────────────────
def section_a():
    print("\n[A] static wiring")
    ope_src = read("world/open_play_engine.py")

    check("A1 gate imported (no_tackle_no_defence_active as _ntnd_active)",
          "no_tackle_no_defence_active as _ntnd_active" in ope_src)

    # Preamble gate: `if not _ntnd_active(...): run_frame_preamble(...)`
    m_preamble = re.search(
        r"if not _ntnd_active\(self\.world, self\.def_tuners, phase=self\._phase\):\s*\n\s*"
        r"self\.defence_manager\.run_frame_preamble\(self\.world\)",
        ope_src,
    )
    check("A2 preamble gated (run_frame_preamble skipped when gate active)",
          m_preamble is not None)
    # Every run_frame_preamble call must be guarded by the gate (none may be
    # unconditional).  Scan the lines: for each call site, look backwards for
    # the `if not _ntnd_active(...)` guard before reaching a real statement.
    _lines = ope_src.splitlines()
    unguarded = []
    for _i, _ln in enumerate(_lines):
        if "run_frame_preamble(self.world)" in _ln:
            guarded = False
            for _back in range(1, 7):
                _j = _i - _back
                if _j < 0:
                    break
                _prev = _lines[_j]
                if "if not _ntnd_active(self.world" in _prev:
                    guarded = True
                    break
                _prev_strip = _prev.strip()
                if not _prev_strip or _prev_strip.startswith("#"):
                    continue
                # A real code line before finding the guard -> unguarded.
                break
            if not guarded:
                unguarded.append((_i + 1, _ln.strip()))
    check("A3 no unconditional run_frame_preamble remains",
          len(unguarded) == 0, unguarded)

    m_dispatch = re.search(
        r"if _ntnd_active\(self\.world, self\.def_tuners, phase=self\._phase\):\s*\n\s*"
        r"self\._run_idle_open_play_pursuit\(dt\)\s*\n"
        r"\s*else:\s*\n\s*"
        r"self\.defence_manager\.update_open_play\(self, dt\)",
        ope_src,
    )
    check("A4 update_open_play routed through the gate (idle vs full dispatch)",
          m_dispatch is not None)

    check("A5 _run_idle_open_play_pursuit defined",
          re.search(r"def _run_idle_open_play_pursuit\(self, dt", ope_src) is not None)

    # Pull the idle-pursuit body and confirm it never calls the expensive path.
    m_idle = re.search(
        r"def _run_idle_open_play_pursuit\(self, dt: float\) -> None:(.*?)\n    def ",
        ope_src,
        re.S,
    )
    idle_body = m_idle.group(1) if m_idle else ""
    check("A6 idle pursuit never calls run_frame_preamble / update_open_play",
          "run_frame_preamble" not in idle_body and "update_open_play" not in idle_body)
    check("A7 idle pursuit never writes depth_role / depth_class / tackle_role",
          "depth_role =" not in idle_body
          and "depth_class =" not in idle_body
          and "tackle_role =" not in idle_body)
    check("A8 idle pursuit never touches frame dicts (frame_spacing_targets/frame_width_targets)",
          "frame_spacing_targets" not in idle_body
          and "frame_width_targets" not in idle_body)
    check("A9 idle pursuit re-asserts fullback geometry (backfield anchor never drops)",
          "apply_fullback_geometry" in idle_body)


# ─────────────────────────────────────────────────────────────────────────
# B. PREDICATE RUNTIME PROBE
# ─────────────────────────────────────────────────────────────────────────
class FakeWorld(SimpleNamespace):
    pass


class FakeP:
    def __init__(self, pid, x, y, in_contact=False):
        self.player_id = pid
        self.x = float(x)
        self.y = float(y)
        self.in_contact = in_contact


def _mk(defenders, bc, attack=None):
    w = FakeWorld()
    w.defending_team = defenders
    w.attacking_team = attack if attack is not None else ([bc] if bc else [])
    w.ball_carrier = bc
    w._ope_phase_label = "OPEN_PLAY"
    return w


def section_b():
    print("\n[B] predicate runtime probe")
    from game.defence.defence_lvl_1.defensive_pipeline import (
        no_tackle_no_defence_active as _ntnd,
    )
    from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners

    tuners = DefenceTuners()
    check("B0 tuner exists (default OFF)", tuners.no_tackle_no_defence_enabled is False)
    radius = tuners.no_tackle_engagement_radius_m
    print("      engagement radius = %s m" % radius)

    # B1 canary OFF -> False
    tuners.no_tackle_no_defence_enabled = False
    w = _mk([], None)
    check("B1 canary OFF -> False (legacy)",
          _ntnd(w, tuners, phase="OPEN_PLAY") is False)

    tuners.no_tackle_no_defence_enabled = True

    # B2 no BC -> False
    w = _mk([], None)
    check("B2 no BC -> False (kickoff frames run defence)",
          _ntnd(w, tuners, phase="OPEN_PLAY") is False)

    # B3 defender CLOSE (<radius) -> False (engaged -> defence runs). bc(60,35) d(55,35)
    bc = FakeP(1, 60.0, 35.0); d = FakeP(2, 55.0, 35.0)
    check("B3 defender CLOSE (engaged) -> False (defence runs)",
          _ntnd(_mk([d], bc), tuners, phase="OPEN_PLAY") is False)

    # B4 defender FAR (>radius) -> True (idle -> suppress). bc(60,35) d(40,30)
    bc = FakeP(1, 60.0, 35.0); d = FakeP(2, 40.0, 30.0)
    check("B4 defender FAR (idle) -> True (suppress)",
          _ntnd(_mk([d], bc), tuners, phase="OPEN_PLAY") is True)

    # B5 contact on defender -> False
    bc = FakeP(1, 60.0, 35.0); d = FakeP(2, 40.0, 30.0, in_contact=True)
    check("B5 contact on defender -> False",
          _ntnd(_mk([d], bc), tuners, phase="OPEN_PLAY") is False)

    # B6 contact on BC -> False
    bc = FakeP(1, 60.0, 35.0, in_contact=True); d = FakeP(2, 40.0, 30.0)
    check("B6 contact on BC -> False",
          _ntnd(_mk([d], bc), tuners, phase="OPEN_PLAY") is False)

    # B7 non-OPEN_PLAY phase -> False
    bc = FakeP(1, 60.0, 35.0); d = FakeP(2, 40.0, 30.0)
    check("B7 non-OPEN_PLAY phase -> False",
          _ntnd(_mk([d], bc), tuners, phase="TACKLE_DRIVE") is False)


# ─────────────────────────────────────────────────────────────────────────
# C + D. HEADLESS ENGINE A/B  (real boot)
# ─────────────────────────────────────────────────────────────────────────
def boot_engine():
    os.environ["SDL_VIDEODRIVER"] = "dummy"
    os.environ["SDL_AUDIODRIVER"] = "dummy"
    import pygame
    pygame.init()
    pygame.display.set_mode((1, 1))

    from world.field import Field
    from world.world_state import WorldState, GamePhase
    from world.open_play_engine import OpenPlayEngine
    from kick_off.kickoff_formations import (
        apply_receiving_kickoff_formation,
        apply_kicking_team_formation,
    )
    from game.kickoff_engine import KickoffEngine
    from ball.ball import Ball
    from game.physics.collision_system import CollisionSystem

    W, H = 1920, 1080
    field = Field(W, H)
    world = WorldState()
    ball = Ball(field)
    world.ball = ball
    open_play = OpenPlayEngine(world)
    world.open_play = open_play
    world.open_play_engine = open_play
    kickoff_engine = KickoffEngine(world, ball, field)
    collision_system = CollisionSystem()

    kickoff_engine.reset_for_kickoff()
    apply_receiving_kickoff_formation(world, field)
    apply_kicking_team_formation(world, field)
    world.ball_owner = "NONE"
    world.ball_carrier = None
    world.ball.possessed_by = None
    kickoff_engine.kickoff()
    # Stash the kickoff engine so _force_open_play() can drive it.
    world._kickoff_engine_ref = kickoff_engine

    return world, open_play, ball, kickoff_engine, collision_system, field


def _force_open_play(world, open_play, dt):
    """Drive the engine from kickoff until OPEN_PLAY with a registered BC."""
    from world.world_state import GamePhase
    from world.catch_system import check_for_catch
    from game.physics.collision_system import CollisionSystem
    ball = world.ball
    collision_system = CollisionSystem()
    for _ in range(600):
        ball.update(dt)
        if world.phase == GamePhase.KICKOFF:
            kickoff_engine = getattr(world, "_kickoff_engine_ref", None)
            if kickoff_engine is not None:
                kickoff_engine.update(dt)
            check_for_catch(world, ball, dt)
        elif world.phase == GamePhase.CATCH:
            if world.ball_carrier is not None and world.open_play is not None:
                world.open_play.start(world.ball_carrier)
            world.phase = GamePhase.OPEN_PLAY
        elif world.phase == GamePhase.OPEN_PLAY:
            world.open_play.update(dt)
            if not getattr(world, "tes_active", False):
                collision_system.resolve_all(world.players, world=world)
        if world.phase == GamePhase.OPEN_PLAY and world.ball_carrier is not None:
            return True
    return False


def _place_idle(world, bcx=86.0, bcy=35.0, def_start_x=30.0):
    """Force an idle OPEN_PLAY: BC far ahead, all defenders far behind."""
    bc = world.ball_carrier
    if bc is None:
        return False
    bc.x = bcx; bc.y = bcy; bc.vx = 0.0; bc.vy = 0.0
    bc.in_contact = False; bc.tackle_locked = False; bc.tes_locked = False
    n = 0
    for d in world.defending_team:
        d.x = def_start_x - (n % 5) * 3.0
        d.y = 20.0 + (n % 7) * 5.0
        d.vx = 0.0; d.vy = 0.0
        d.in_contact = False; d.tackle_locked = False; d.tes_locked = False
        d.frozen = False; d.freeze_timer = 0.0
        d.is_tackler = False
        n += 1
    from world.world_state import GamePhase
    world.phase = GamePhase.OPEN_PLAY
    world.tes_active = False
    return True


def _wrap_counters(open_play):
    dm = open_play.defence_manager
    counts = {"preamble": 0, "openplay": 0, "idle": 0}
    orig_preamble = dm.run_frame_preamble
    orig_openplay = dm.update_open_play
    orig_idle = open_play._run_idle_open_play_pursuit

    def cp(world):
        counts["preamble"] += 1
        return orig_preamble(world)

    def cu(ope, dt):
        counts["openplay"] += 1
        return orig_openplay(ope, dt)

    def ci(dt):
        counts["idle"] += 1
        return orig_idle(dt)

    dm.run_frame_preamble = cp
    dm.update_open_play = cu
    open_play._run_idle_open_play_pursuit = ci
    return counts


def _run_ab(world, open_play, gate_on, frames, dt):
    from game.defence.defence_lvl_1.defensive_pipeline import (
        no_tackle_no_defence_active as _ntnd,
    )
    open_play.def_tuners.no_tackle_no_defence_enabled = gate_on
    counts = _wrap_counters(open_play)
    from game.physics.collision_system import CollisionSystem
    collision_system = CollisionSystem()

    gate_active_frames = 0
    t_samples = []
    phase = "OPEN_PLAY"
    for _ in range(frames):
        t0 = time.perf_counter()
        world.open_play.update(dt)
        if not getattr(world, "tes_active", False):
            collision_system.resolve_all(world.players, world=world)
        t1 = time.perf_counter()
        t_samples.append(t1 - t0)
        if _ntnd(world, open_play.def_tuners, phase=open_play._phase):
            gate_active_frames += 1
        phase = open_play._phase
        if phase != "OPEN_PLAY":
            break
    avg_ms = (sum(t_samples) / len(t_samples)) * 1000.0 if t_samples else 0.0
    return counts, avg_ms, gate_active_frames, phase, len(t_samples)


def section_cd():
    print("\n[C] headless A/B perf (real boot, idle OPEN_PLAY)")
    try:
        world, open_play, ball, kickoff_engine, collision_system, field = boot_engine()
    except Exception as e:
        check("C0 engine boot", False, "boot failed: %r" % e)
        return

    dt = 1.0 / 60.0
    ok = _force_open_play(world, open_play, dt)
    check("C0 engine boot + reach OPEN_PLAY with BC", ok)

    if not ok:
        return

    # Scenario A: gate OFF (full defensive pipeline each frame).
    ok_idle = _place_idle(world)
    check("C1 idle placement applied", ok_idle)
    counts_a, avg_a, gate_a, phase_a, n_a = _run_ab(
        world, open_play, gate_on=False, frames=200, dt=dt)
    print("      A (gate OFF): avg=%.3f ms/frame  preamble=%d  update_open_play=%d"
          "  frames=%d" % (avg_a, counts_a["preamble"], counts_a["openplay"], n_a))
    check("C2 gate OFF == full pipeline runs (preamble>0 and update_open_play>0)",
          counts_a["preamble"] > 0 and counts_a["openplay"] > 0)

    # Scenario B: gate ON (idle pursuit). Re-place identical idle layout.
    _place_idle(world)
    counts_b, avg_b, gate_b, phase_b, n_b = _run_ab(
        world, open_play, gate_on=True, frames=200, dt=dt)
    print("      B (gate ON):  avg=%.3f ms/frame  gate_active=%d  preamble=%d"
          "  update_open_play=%d  frames=%d"
          % (avg_b, gate_b, counts_b["preamble"], counts_b["openplay"], n_b))
    check("C3 gate ON suppresses the full pipeline while idle (preamble==0, update_open_play==0)",
          counts_b["preamble"] == 0 and counts_b["openplay"] == 0)
    check("C4 gate ON runs idle pursuit (idle_calls>0)", counts_b["idle"] > 0)
    check("C5 gate stays active on idle frames (gate_active == frames run)",
          gate_b == n_b, "gate_b=%s n_b=%s" % (gate_b, n_b))

    if avg_a > 0 and avg_b > 0:
        speedup = (avg_a - avg_b) / avg_a * 100.0
        print("      speedup (idle frames): %.1f%%  (%.3f -> %.3f ms/frame)"
              % (speedup, avg_a, avg_b))
        check("C6 idle dispatch is measurably cheaper (B < A)",
              avg_b < avg_a, "A=%.3f B=%.3f" % (avg_a, avg_b))
    else:
        check("C6 idle dispatch is measurably cheaper (B < A)", False,
              "no timing samples")

    # Scenario D: gate ON but a defender closes into engagement -> gate must
    # release so a real tackle can still form.
    print("\n[D] gate releases on engagement (tackles NOT suppressed)")
    _place_idle(world)
    open_play.def_tuners.no_tackle_no_defence_enabled = True
    # Move one defender to 8 m from the BC (inside 12 m engagement radius).
    bc = world.ball_carrier
    d = world.defending_team[0]
    d.x = bc.x - 8.0; d.y = bc.y; d.vx = 4.0; d.vy = 0.0
    counts_d, avg_d, gate_d, phase_d, n_d = _run_ab(
        world, open_play, gate_on=True, frames=120, dt=dt)
    print("      D: gate_active=%d  preamble=%d  update_open_play=%d  final_phase=%s"
          % (gate_d, counts_d["preamble"], counts_d["openplay"], phase_d))
    check("D1 defender in engagement releases the gate (gate_active < frames)",
          gate_d < n_d, "gate_d=%s n_d=%s" % (gate_d, n_d))
    check("D2 full dispatch resumes once engaged (preamble>0 or update_open_play>0)",
          counts_d["preamble"] > 0 or counts_d["openplay"] > 0)


# ─────────────────────────────────────────────────────────────────────────
# E. IMPORTS
# ─────────────────────────────────────────────────────────────────────────
def section_e():
    print("\n[E] engine module tree imports cleanly")
    import importlib
    mods = [
        "world.open_play_engine",
        "game.managers.defence_manager",
        "game.defence.defence_lvl_1.defensive_pipeline",
        "game.defence.defence_lvl_1.defensive_tuners",
    ]
    ok = True
    for m in mods:
        try:
            importlib.import_module(m)
        except Exception as e:
            ok = False
            print("      [import FAIL] %s: %s" % (m, e))
    check("E1 all engine modules import", ok)


def main():
    print("=" * 64)
    print("NO-TACKLE / NO-DEFENCE GATE VERIFIER (STAGE 11)")
    print("=" * 64)
    section_a()
    section_b()
    section_cd()
    section_e()
    print("\n" + "=" * 64)
    if FAILURES:
        print("RESULT: %d FAILURE(S): %s" % (len(FAILURES), FAILURES))
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
