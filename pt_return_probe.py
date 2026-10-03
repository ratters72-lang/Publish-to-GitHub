"""pt_return_probe.py -- live probe: post-tackle return window lifecycle.

WHY THIS EXISTS
---------------
The lifecycle defect: ``arm_fullback_post_tackle_return`` set
``_fb_post_tackle_return_active`` and NOTHING ever cleared it.  The window is
therefore expected to (a) ARM at drive completion, (b) RELEASE on the frame
the body reaches its assigned return target (COMPLETION) or the frame a
higher-priority owner takes it (INTERRUPTION), and (c) stop overriding the
per-frame anchor once released.

This boots the SHIPPED engine exactly like ``tmp/tackle_trace.py``
(dummy SDL, seed RLM_PT_SEED default 20260827, dt = 1/60) and records, for
the tagged fullback:

  * every ARM and every RELEASE (with the release reason stamped), and
  * a per-frame row (phase, engaged, return-active, return target, fb x/y,
    fb target, the NORMAL ball-derived anchor and the RUCK anchor, and the
    FB-BC gap), so the post-tackle leg can be measured directly.

The probe prints a compact timeline plus, per tackle window, the gap
statistics on the RELEASE -> PTB leg (the "does it still sit beside the
carrier after the tackle" question).

Read-only: it monkeypatches the ``fullback_role`` module FUNCTIONS in this
process only.  No production file is edited.

Run:   python pt_return_probe.py
Writes: pt_return_probe.txt
Env:   RLM_PT_SEED   (default 20260827)
       RLM_PT_FRAMES (default 12000)
"""

import io
import math
import os
import sys


os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
sys.path.insert(0, ".")

_REAL_STDOUT = sys.stdout


def _quiet():
    sys.stdout = io.TextIOWrapper(
        open(os.devnull, "wb"), encoding="utf-8", errors="replace"
    )


def _loud():
    sys.stdout = _REAL_STDOUT


_quiet()
import pygame

pygame.display.init()
pygame.display.set_mode((1, 1), pygame.NOFRAME)

import world.open_play_engine as ope_mod

ope_mod.DEBUG_SDEA_PHYSICS = False
ope_mod.DEBUG_OPE_TRACE = False

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
from world.catch_system import check_for_catch
from game.defence.defence_lvl_1 import fullback_role as fb
from game.defence.defence_lvl_1.anchor_authority import (
    defensive_line_anchor_m,
)

_loud()

SEED = int(os.environ.get("RLM_PT_SEED", "20260827"))
MAX_FRAMES = int(os.environ.get("RLM_PT_FRAMES", "12000"))
DT = 1.0 / 60.0

_PTB_PHASES = ("RELEASE", "PTB_PREP", "PTB_EXEC", "PTB",
               "POST_RUCK_OPEN_PLAY")
_CD_PHASES = ("TACKLE_INIT", "TACKLE_CONTACT", "TACKLE_DRIVE",
              "TACKLE_DOMINANCE")

EVENTS = []          # (frame, kind, pid, reason)


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _pid(actor):
    return getattr(actor, "player_id", None)


def _tagged(world):
    return [
        p for p in getattr(world, "defending_team", [])
        if getattr(p, "fullback_role_active", False)
    ]


# ------------------------------------------------------------------ #
# MODULE-LEVEL INSTRUMENTATION (arm / clear only)
# ------------------------------------------------------------------ #
_CUR_FRAME = [0]


def _install_module_probes():
    real_arm = fb.arm_fullback_post_tackle_return
    real_clear = fb.clear_fullback_post_tackle_return

    def arm(d):
        result = real_arm(d)
        if result:
            EVENTS.append((_CUR_FRAME[0], "ARM", _pid(d), ""))
        return result

    def clear(d, reason: str = ""):
        was = fb.is_fullback_post_tackle_return_active(d)
        result = real_clear(d, reason=reason)
        if was:
            EVENTS.append((_CUR_FRAME[0], "RELEASE", _pid(d), reason))
        return result

    fb.arm_fullback_post_tackle_return = arm
    fb.clear_fullback_post_tackle_return = clear
    # The engine binds the arm function as a MODULE ALIAS at import time
    # (``arm_fullback_post_tackle_return as _fb_arm_post_tackle_return``),
    # so patching only the source attribute misses the live call site.
    # Re-point the engine's alias too, or the ARM timeline is invisible.
    try:
        import world.open_play_engine as _ope
        _ope._fb_arm_post_tackle_return = arm
    except Exception:                                    # noqa: BLE001
        pass
    return real_arm, real_clear


def _row(frame, world, open_play, body):
    bc = getattr(world, "ball_carrier", None)
    ref = bc if bc is not None else getattr(world, "ball", None)
    line_anchor = _num(defensive_line_anchor_m(world), 0.0)
    try:
        normal = fb.compute_fullback_anchor(ref, line_anchor)
    except Exception:                                    # noqa: BLE001
        normal = (float("nan"), float("nan"))
    try:
        ruck = fb.compute_fullback_ruck_anchor(world, line_anchor)
    except Exception:                                    # noqa: BLE001
        ruck = (float("nan"), float("nan"))
    tx, ty = fb.assigned_fullback_return_target(body)
    fx, fy = _num(getattr(body, "x", 0.0)), _num(getattr(body, "y", 0.0))
    gap = None
    if bc is not None:
        gap = math.hypot(
            fx - _num(getattr(bc, "x", 0.0)),
            fy - _num(getattr(bc, "y", 0.0)),
        )
    return {
        "frame": frame,
        "phase": getattr(open_play, "_phase", None),
        "frozen": bool(getattr(body, "frozen", False)),
        "speed": _num(getattr(body, "move_speed", 0.0)),
        "ms": getattr(getattr(body, "movement_state", None), "name", None),
        # The MMDL's per-frame integration contract: True means "position
        # already integrated this frame -- skip Player.update".  If it is
        # stale (carried out of OPEN_PLAY into RELEASE/PTB) the body is
        # never integrated, which is indistinguishable from being parked.
        "mmdl": bool(getattr(body, "_mmdl_moved", False)),
        "dl": bool(getattr(body, "depth_locked", False)),
        "engaged": bool(getattr(body, "_fb_emergency_engaged", False)),
        "active": fb.is_fullback_post_tackle_return_active(body),
        "ret": (tx, ty),
        "x": fx,
        "y": fy,
        "tx": _num(getattr(body, "target_x", 0.0)),
        "ty": _num(getattr(body, "target_y", 0.0)),
        "normal": normal,
        "ruck": ruck,
        "gap": gap,
        "bcx": (_num(getattr(bc, "x", 0.0)) if bc is not None else None),
    }


def run():
    import random

    random.seed(SEED)
    if not pygame.display.get_init():
        pygame.display.init()
    pygame.display.set_mode((1, 1), pygame.NOFRAME)

    collision_system = CollisionSystem()
    field = Field(1920, 1080)
    world = WorldState()
    ball = Ball(field)
    world.ball = ball
    open_play = OpenPlayEngine(world, seed=SEED)
    world.open_play = open_play
    world.open_play_engine = open_play
    kickoff_engine = KickoffEngine(world, ball, field)

    _install_module_probes()

    body = [None]
    rows = []

    kickoff_engine.reset_for_kickoff()
    apply_receiving_kickoff_formation(world, field)
    apply_kicking_team_formation(world, field)
    world.ball_owner = "NONE"
    world.ball_carrier = None
    world.ball.possessed_by = None
    kickoff_engine.kickoff()

    for frame in range(1, MAX_FRAMES + 1):
        _CUR_FRAME[0] = frame
        world.frame_count = frame
        ball.update(DT)
        if world.phase == GamePhase.KICKOFF:
            kickoff_engine.update(DT)
            check_for_catch(world, ball, DT)
        elif world.phase == GamePhase.CATCH:
            if world.ball_carrier is not None:
                world.open_play.start(world.ball_carrier)
            world.phase = GamePhase.OPEN_PLAY
        elif world.phase == GamePhase.OPEN_PLAY:
            open_play.update(DT)
            if not world.tes_active:
                collision_system.resolve_all(world.players, world=world)
        else:
            continue

        if body[0] is None:
            tagged = _tagged(world)
            if tagged:
                body[0] = tagged[0]
        if body[0] is not None:
            rows.append(_row(frame, world, open_play, body[0]))

    return {
        "rows": rows,
        "pid": _pid(body[0]) if body[0] is not None else None,
        "seed": SEED,
        "frames": MAX_FRAMES,
    }


def _stats(values):
    clean = [v for v in values if v is not None]
    if not clean:
        return None
    return (min(clean), max(clean), sum(clean) / len(clean))


def _fmt(stats):
    if not stats:
        return "-"
    return "(%.2f/%.2f/%.2f)" % stats


def _windows(rows):
    """Split rows into tackle windows on the contact-drive -> PTB -> open leg."""
    out = []
    current = None
    for r in rows:
        if r["phase"] in _CD_PHASES:
            if current is None:
                current = {"rows": []}
                out.append(current)
        if current is not None:
            current["rows"].append(r)
            if r["phase"] == "OPEN_PLAY":
                current = None
    return out


def report(result):
    rows = result["rows"]
    lines = []
    emit = lines.append

    emit("=" * 96)
    emit("POST-TACKLE RETURN LIFECYCLE PROBE  seed %d  frames %d  fb pid=%s"
         % (result["seed"], result["frames"], result["pid"]))
    emit("=" * 96)

    arms = [e for e in EVENTS if e[1] == "ARM"]
    rels = [e for e in EVENTS if e[1] == "RELEASE"]
    emit("ARM events     : %d" % len(arms))
    for f, _k, pid, _r in arms[:40]:
        emit("   f=%-6s pid=%s" % (f, pid))
    emit("RELEASE events : %d" % len(rels))
    for f, _k, pid, reason in rels[:40]:
        emit("   f=%-6s pid=%s reason=%s" % (f, pid, reason))

    active_frames = sum(1 for r in rows if r["active"])
    emit("")
    emit("frames with window ACTIVE : %d of %d" % (active_frames, len(rows)))
    if arms:
        first = arms[0][0]
        last = arms[-1][0]
        emit("first ARM f=%s   last ARM f=%s" % (first, last))

    wins = _windows(rows)
    emit("")
    emit("TACKLE WINDOWS: %d" % len(wins))
    emit("")
    emit("-- PER WINDOW: post-contact (RELEASE->PTB) leg --")
    emit("  rows= contact-drive frames   ptb= release->ptb frames")
    emit("  act= window-active frames on the ptb leg")
    emit("  ptb_gap= FB-BC gap on the ptb leg (min/max/mean)")
    emit("  end_gap= FB-BC gap on the last ptb frame")
    for i, w in enumerate(wins):
        wrows = w["rows"]
        cd = [r for r in wrows if r["phase"] in _CD_PHASES]
        ptb = [r for r in wrows if r["phase"] in _PTB_PHASES]
        act = sum(1 for r in ptb if r["active"])
        ptb_gap = _stats(r["gap"] for r in ptb)
        end_gap = ptb[-1]["gap"] if ptb else None
        emit("  [%2d] f%-5s..%-5s cd=%-3d ptb=%-3d act=%-3d "
             "ptb_gap=%-18s end_gap=%s"
             % (i, wrows[0]["frame"], wrows[-1]["frame"], len(cd), len(ptb),
                act, _fmt(ptb_gap),
                ("%.2f" % end_gap) if end_gap is not None else "-"))

    # Detail on the window that actually carried an armed window.
    armed_windows = [
        w for w in wins
        if any(r["active"] for r in w["rows"])
    ]
    if armed_windows:
        w = armed_windows[0]
        arows = w["rows"]
        emit("")
        emit("-- ARMED WINDOW DIAGNOSTIC (per phase, first armed window) --")
        emit("  The window commands the ruck anchor ~30 m behind the body.")
        emit("  Does the body move toward it?")
        emit("  phase                  n   fz mmdl   dl     dx     dy   "
             "dist_to_cmd_t0  dist_to_cmd_t1")
        emit("   (fz = frozen flag, mmdl = _mmdl_moved skip-integration flag)")
        order = []
        for r in arows:
            if r["phase"] not in order:
                order.append(r["phase"])
        for ph in order:
            prs = [r for r in arows if r["phase"] == ph]
            n = len(prs)
            fz = sum(1 for r in prs if r["frozen"])
            mz = sum(1 for r in prs if r["mmdl"])
            dz = sum(1 for r in prs if r["dl"])
            dx = prs[-1]["x"] - prs[0]["x"]
            dy = prs[-1]["y"] - prs[0]["y"]
            d0 = math.hypot(prs[0]["x"] - prs[0]["tx"],
                            prs[0]["y"] - prs[0]["ty"])
            d1 = math.hypot(prs[-1]["x"] - prs[-1]["tx"],
                            prs[-1]["y"] - prs[-1]["ty"])
            emit("  %-20s %4d %4d %4d %4d %6.2f %6.2f %15.2f %15.2f"
                 % (ph or "-", n, fz, mz, dz, dx, dy, d0, d1))
        act = [r for r in arows if r["active"]]
        if act:
            n_act = len(act)
            emit("")
            emit("  armed frames                  : %d" % n_act)
            emit("  armed AND frozen (fz)         : %d"
                 % sum(1 for r in act if r["frozen"]))
            emit("  armed AND _mmdl_moved (skip)  : %d"
                 % sum(1 for r in act if r["mmdl"]))
            emit("  armed AND depth_locked        : %d"
                 % sum(1 for r in act if r["dl"]))
            emit("  armed AND engaged             : %d"
                 % sum(1 for r in act if r["engaged"]))
            emit("  armed NET displacement        : %.3f m"
                 % math.hypot(act[-1]["x"] - act[0]["x"],
                              act[-1]["y"] - act[0]["y"]))
            emit("  armed dist to commanded tgt   : t0=%.2f  t1=%.2f"
                 % (math.hypot(act[0]["x"] - act[0]["tx"],
                               act[0]["y"] - act[0]["ty"]),
                    math.hypot(act[-1]["x"] - act[-1]["tx"],
                               act[-1]["y"] - act[-1]["ty"])))

    # AFTER the window releases, does the body head for the ball-derived
    # anchor again (the retreat) or stay ruck-anchored?
    emit("")
    emit("-- POST-WINDOW ANCHOR OWNERSHIP --")
    emit("  counts, over frames AFTER the first release, of which anchor the")
    emit("  FB target is nearest (ruck vs ball-derived).")
    if rels:
        after = [r for r in rows
                 if r["frame"] > rels[0][0] and r["phase"] == "OPEN_PLAY"]
        near_ruck = 0
        near_normal = 0
        for r in after:
            d_ruck = math.hypot(r["tx"] - r["ruck"][0], r["ty"] - r["ruck"][1])
            d_norm = math.hypot(r["tx"] - r["normal"][0],
                                r["ty"] - r["normal"][1])
            if d_ruck <= d_norm:
                near_ruck += 1
            else:
                near_normal += 1
        emit("  post-release OPEN_PLAY frames : %d" % len(after))
        emit("  target nearest RUCK anchor    : %d" % near_ruck)
        emit("  target nearest NORMAL anchor  : %d" % near_normal)
    else:
        emit("  (no release observed)")

    text = "\n".join(lines) + "\n"
    _REAL_STDOUT.write(text)
    try:
        with open("pt_return_probe.txt", "w", encoding="utf-8") as fh:
            fh.write(text)
    except OSError:
        pass
    return 0


def main():
    import traceback

    _quiet()
    try:
        result = run()
    except Exception:                                    # noqa: BLE001
        _loud()
        traceback.print_exc()
        return 1
    _loud()
    return report(result)


if __name__ == "__main__":
    sys.exit(main())
