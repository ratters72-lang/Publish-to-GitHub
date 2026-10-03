"""tmp/fb_chase_diag.py -- WHY does the tagged fullback not chase / not tackle?

Read-only live diagnostic on the shipped engine.  Boots exactly like
``tmp/fb_close.py`` (seed 20260827, dt=1/60) but runs long enough for
tackles and play-the-ball cycles to occur, and records -- for the tagged
fullback only -- every gate that can refuse it:

  * the phase and whether the ball is still in the kickoff flight,
  * whether a genuine break is live (depth / lateral),
  * the two admission predicates (emergency defender, tackle authority),
  * whether the gate that completes the chase handover would accept,
  * the tackle_role, can_acquire_tackle, frozen / freeze_timer,
  * the distance to the carrier and whether the body is in ``tackler_ids``.

It also installs the repo's own admission ledger so every REJECTED
line-break evaluation is attributed to a named reason.

Run:  python tmp/fb_chase_diag.py
Also writes tmp/fb_chase_diag.txt with the same report.
"""
import collections
import io
import math
import os

os.environ["SDL_VIDEODRIVER"] = "dummy"

import sys

sys.path.insert(0, ".")

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
from game.defence.defence_lvl_1 import emergency_chase_admission_audit as audit
from game.defence.defence_lvl_1.slot_arbitration import is_slot_tackle_eligible
from game.defence.defence_lvl_1.pretackle_machine import is_closing_posture

SEED = int(os.environ.get("RLM_DIAG_SEED", "20260827"))
MAX_FRAMES = int(os.environ.get("RLM_DIAG_FRAMES", "12000"))
DT = 1.0 / 60.0

_REAL_STDOUT = sys.stdout
_REPORT = []


def _quiet():
    sys.stdout = io.TextIOWrapper(
        open(os.devnull, "wb"), encoding="utf-8", errors="replace"
    )


def _loud():
    sys.stdout = _REAL_STDOUT


def _phase_name(world):
    phase = getattr(world, "phase", None)
    return getattr(phase, "name", None) or str(phase)


def _state_name(actor):
    ms = getattr(actor, "movement_state", None)
    return getattr(ms, "name", None) if ms is not None else None


def _emit(line=""):
    _REPORT.append(line)


def _run():
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
    open_play = OpenPlayEngine(world)
    world.open_play = open_play
    world.open_play_engine = open_play
    kickoff_engine = KickoffEngine(world, ball, field)

    audit.reset_admission_audit(world)
    audit.install_ledger(world)
    release_clear_rows = []
    drive_end_rows = []
    _prepare_release = open_play._prepare_release

    def _traced_prepare_release():
        actor = next(
            (p for p in world.defending_team
             if getattr(p, "fullback_role_active", False)),
            None,
        )
        before = (
            actor.x, actor.y, actor.vx, actor.vy,
            actor.target_x, actor.target_y,
            bool(getattr(actor, "tes_attached", False)),
            bool(getattr(actor, "in_contact", False)),
        ) if actor is not None else None
        result = _prepare_release()
        actor = next(
            (p for p in world.defending_team
             if getattr(p, "fullback_role_active", False)),
            None,
        )
        after = (
            actor.x, actor.y, actor.vx, actor.vy,
            actor.target_x, actor.target_y,
            bool(getattr(actor, "tes_attached", False)),
            bool(getattr(actor, "in_contact", False)),
            _state_name(actor),
        ) if actor is not None else None
        drive_end_rows.append((world.frame_count, before, after))
        return result

    open_play._prepare_release = _traced_prepare_release
    _clear_tes_artefacts = open_play._clear_tes_artefacts

    def _traced_clear_tes_artefacts():
        fb_before = next(
            (p for p in world.defending_team
             if getattr(p, "fullback_role_active", False)),
            None,
        )
        before = (
            bool(getattr(fb_before, "_fb_emergency_engaged", False)),
            bool(getattr(fb_before, "_fb_post_tackle_return_active", False)),
        ) if fb_before is not None else None
        result = _clear_tes_artefacts()
        fb_after = next(
            (p for p in world.defending_team
             if getattr(p, "fullback_role_active", False)),
            None,
        )
        after = (
            bool(getattr(fb_after, "_fb_emergency_engaged", False)),
            bool(getattr(fb_after, "_fb_post_tackle_return_active", False)),
        ) if fb_after is not None else None
        release_clear_rows.append((world.frame_count, before, after))
        return result

    open_play._clear_tes_artefacts = _traced_clear_tes_artefacts
    _resolve_all = collision_system.resolve_all

    def _traced_resolve_all(players, world=None):
        active_fb = next(
            (p for p in world.defending_team
             if getattr(p, "fullback_role_active", False)),
            None,
        ) if world is not None else None
        before = None
        if active_fb is not None and getattr(
            active_fb, "_fb_post_tackle_return_active", False
        ):
            before = (
                active_fb.x, active_fb.y, active_fb.vx, active_fb.vy,
                active_fb.body.collision_enabled,
            )
        result = _resolve_all(players, world=world)
        if before is not None:
            after = (
                active_fb.x, active_fb.y, active_fb.vx, active_fb.vy,
                active_fb.body.collision_enabled,
            )
            if before != after and (
                getattr(open_play, "_phase", None)
                in ("RELEASE", "PTB_PREP", "PTB_EXEC", "PTB")
            ):
                collision_rows.append((
                    getattr(world, "frame_count", None),
                    getattr(open_play, "_phase", None),
                    before, after,
                ))
        return result

    collision_system.resolve_all = _traced_resolve_all

    t = collections.defaultdict(int)
    states = collections.Counter()
    roles = collections.Counter()
    phases = collections.Counter()
    chase_gates = collections.Counter()
    break_gates = collections.Counter()
    kick_gates = collections.Counter()
    chase_rows = []
    kick_rows = []
    first_tackle_frame = None
    min_dist = None
    min_dist_frame = None
    prev_in_tackler_ids = False
    prev_attached = False
    post_tackle_watch_until = -1
    post_tackle_rows = []
    collision_rows = []

    kickoff_engine.reset_for_kickoff()
    apply_receiving_kickoff_formation(world, field)
    apply_kicking_team_formation(world, field)
    world.ball_owner = "NONE"
    world.ball_carrier = None
    world.ball.possessed_by = None
    kickoff_engine.kickoff()

    for frame in range(1, MAX_FRAMES + 1):
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

        active = [p for p in world.defending_team
                  if getattr(p, "fullback_role_active", False)]
        if len(active) != 1:
            continue
        actor = active[0]
        bc = getattr(world, "ball_carrier", None)
        if bc is None:
            continue

        pid = getattr(actor, "player_id", None)
        in_tackler_ids = bool(
            pid is not None and pid in getattr(world, "tackler_ids", set())
        )
        attached = bool(getattr(actor, "tes_attached", False))
        if in_tackler_ids and not prev_in_tackler_ids:
            t["fb_tackle_events"] += 1
        if prev_in_tackler_ids and not in_tackler_ids:
            post_tackle_watch_until = max(post_tackle_watch_until, frame + 480)
        if attached and not prev_attached:
            t["fb_attach_events"] += 1
        prev_in_tackler_ids = in_tackler_ids
        prev_attached = attached
        if in_tackler_ids:
            t["in_tackler_ids"] += 1
            if first_tackle_frame is None:
                first_tackle_frame = frame

        t["active"] += 1
        phases[_phase_name(world)] += 1
        states[_state_name(actor)] += 1
        roles[getattr(actor, "tackle_role", None)] += 1

        dist = math.hypot(
            float(getattr(actor, "x", 0.0)) - float(getattr(bc, "x", 0.0)),
            float(getattr(actor, "y", 0.0)) - float(getattr(bc, "y", 0.0)),
        )
        if min_dist is None or dist < min_dist:
            min_dist = dist
            min_dist_frame = frame

        try:
            depth_break = bool(fb.fullback_depth_break_active(world, actor, bc))
            lateral_break = bool(
                fb.fullback_lateral_break_active(world, actor, bc)
            )
            emergency = bool(
                fb.fullback_emergency_defender_active(world, actor, bc)
            )
        except Exception as exc:
            depth_break = lateral_break = emergency = False
            t["predicate_error"] += 1
            t.setdefault("predicate_error_exc", repr(exc))
        try:
            authority = bool(fb.fullback_tackle_authority_active(actor))
        except Exception:
            authority = False
        try:
            command_ok = bool(
                fb.is_fullback_command_target(
                    getattr(actor, "target_x", None)
                )
            )
        except Exception:
            command_ok = False
        try:
            side_owner = bool(fb.fullback_tackle_side_owner_live(actor))
        except Exception:
            side_owner = False

        try:
            slot_in = bool(is_slot_tackle_eligible(bc, actor, world))
        except Exception:
            slot_in = False
        try:
            closing = bool(is_closing_posture(actor))
        except Exception:
            closing = False
        can_acquire = bool(getattr(actor, "can_acquire_tackle", False))
        frozen = bool(getattr(actor, "frozen", False))
        freeze_timer = float(getattr(actor, "freeze_timer", 0.0) or 0.0)
        state = _state_name(actor)
        role = getattr(actor, "tackle_role", None)
        if (
            frame <= post_tackle_watch_until
            and (frame % 5 == 0 or frame == post_tackle_watch_until - 120)
        ):
            post_tackle_rows.append((
                frame,
                getattr(open_play, "_phase", None),
                round(float(actor.x), 3),
                round(float(actor.y), 3),
                round(float(actor.target_x), 3),
                round(float(actor.target_y), 3),
                round(float(actor.vx), 3),
                round(float(actor.vy), 3),
                round(float(getattr(world, "ruck_x_m", 0.0) or 0.0), 3),
                round(float(getattr(world, "ruck_y_m", 0.0) or 0.0), 3),
                state,
                bool(getattr(actor, "_fb_post_tackle_return_active", False)),
                bool(getattr(actor, "_fb_emergency_engaged", False)),
                bool(getattr(actor, "frozen", False)),
                getattr(actor, "target_source", None),
                in_tackler_ids,
            ))
        in_kickoff = bool(getattr(world, "in_kickoff", False))
        tes_active = bool(getattr(world, "tes_active", False))

        gate_bits = (
            ("emergency", emergency),
            ("authority", authority),
            ("command_ok", command_ok),
            ("side_owner", side_owner),
            ("slot_in", slot_in),
            ("closing", closing),
            ("can_acquire", can_acquire),
            ("depth_break", depth_break),
            ("lateral_break", lateral_break),
            ("frozen", frozen),
            ("freeze_timer", freeze_timer > 0.0),
            ("tes_active", tes_active),
            ("in_tackler_ids", in_tackler_ids),
        )

        if state == "CHASE":
            t["chase"] += 1
            for label, value in gate_bits:
                if value:
                    chase_gates[label] += 1
            if len(chase_rows) < 40:
                chase_rows.append((frame, round(dist, 2), role,
                                   dict(gate_bits)))
        if depth_break or lateral_break:
            t["break_live"] += 1
            for label, value in gate_bits:
                if value:
                    break_gates[label] += 1
        if in_kickoff:
            t["in_kickoff"] += 1
            t["kickoff_chase" if state == "CHASE" else "kickoff_other"] += 1
            for label, value in gate_bits:
                if value:
                    kick_gates[label] += 1
            if len(kick_rows) < 40:
                kick_rows.append((frame, round(dist, 2), state, role,
                                  getattr(actor, "target_x", None),
                                  dict(gate_bits)))

    out = dict(t)
    out["post_tackle_first_frame"] = first_tackle_frame
    out["min_bc_dist"] = min_dist
    out["min_bc_dist_frame"] = min_dist_frame
    out["role_counter"] = dict(roles)
    out["state_counter"] = dict(states)
    out["phase_counter"] = dict(phases)
    out["chase_gate_true_counts"] = dict(chase_gates)
    out["break_gate_true_counts"] = dict(break_gates)
    out["kickoff_gate_true_counts"] = dict(kick_gates)
    out["chase_rows"] = chase_rows
    out["kick_rows"] = kick_rows
    out["post_tackle_rows"] = post_tackle_rows
    out["collision_rows"] = collision_rows
    out["release_clear_rows"] = release_clear_rows
    out["drive_end_rows"] = drive_end_rows
    try:
        out["admission_digest"] = audit.summarise_admission_audit(world)
        rows = audit.records_for(world)
        verdicts = collections.Counter()
        for row in rows:
            verdicts[row.verdict] += row.repeat_count
        out["admission_verdicts"] = dict(verdicts)
        out["admission_total_rows"] = len(rows)
        out["admission_sample"] = [
            audit.format_admission_record(row) for row in rows[:12]
        ]
    except Exception as exc:
        out["admission_error"] = repr(exc)
    return out


def main():
    _quiet()
    res = _run()
    _loud()

    _emit("=" * 74)
    _emit("FULLBACK CHASE / TACKLE DIAGNOSTIC (seed %d, %d frames)"
          % (SEED, MAX_FRAMES))
    _emit("=" * 74)
    for key in sorted(res):
        if key in ("chase_rows", "kick_rows", "admission_sample"):
            continue
        _emit("  %-26s %s" % (key, res[key]))

    _emit("")
    _emit("-- first 40 CHASE frames: (frame, dist_to_bc, role, true gates) --")
    for frame, dist, role, bits in res.get("chase_rows", []):
        true_gates = sorted(k for k, v in bits.items() if v)
        _emit("  %-7s d=%-7s role=%-10s %s"
              % (frame, dist, role, ",".join(true_gates) or "-"))

    _emit("")
    _emit("-- first 40 KICKOFF-era frames: (frame, dist, state, role, "
          "target_x, true gates) --")
    for frame, dist, state, role, tx, bits in res.get("kick_rows", []):
        true_gates = sorted(k for k, v in bits.items() if v)
        _emit("  %-7s d=%-7s %-8s role=%-10s tx=%-8s %s"
              % (frame, dist, state, role, tx, ",".join(true_gates) or "-"))

    _emit("")
    _emit("-- post-tackle fullback target/movement (frame, phase, x/y, "
          "target, velocity, ruck, state, return, engaged, frozen, source, tackler) --")
    for row in res.get("post_tackle_rows", []):
        _emit("  " + repr(row))

    _emit("")
    _emit("-- collision changes during PTB return --")
    for row in res.get("collision_rows", [])[:40]:
        _emit("  " + repr(row))

    _emit("")
    _emit("-- release teardown fullback flags --")
    for row in res.get("release_clear_rows", []):
        _emit("  " + repr(row))

    _emit("")
    _emit("-- drive-end fullback handoff (before -> after) --")
    for row in res.get("drive_end_rows", []):
        _emit("  " + repr(row))

    _emit("")
    _emit("-- admission ledger: first 12 rows --")
    for line in res.get("admission_sample", []):
        _emit("  " + line)

    text = "\n".join(_REPORT) + "\n"
    print(text, end="")
    with open("tmp/fb_chase_diag.txt", "w", encoding="utf-8") as handle:
        handle.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
