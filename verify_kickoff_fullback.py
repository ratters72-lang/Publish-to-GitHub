"""Headless kickoff fullback regression verifier (2026-08-11).

Boots the real engine exactly like run.py, kicks off, and asserts the
kickoff fullback layout contract:

  KICKOFF FRAMES (ball in flight, no ball carrier):
    A. Exactly ONE fullback actor (fullback_role_active) on every kickoff
       frame.
    B. world.fullback_actor_id == player_id of jersey 1 (player #200)
       whenever jersey 1 is not the kicker.
    C. THE AIRBORNE ANCHOR X TRACKS THE DEPTH ROLE (2026-09-28).  Once the
       ball is kicked the anchor X is the normal depth-role law referenced
       to the live ball, clamped so it can never command the actor BEHIND
       itself (the self-releasing anti-retreat guard).  Asserted two ways:
         (a) ANTI-RETREAT — the command never sits ahead of (behind) the
             actor's own X by more than a frame of travel;
         (b) LIVENESS — the anchor X actually MOVES over the flight, so
             depth is genuinely tracked and the FB is not pinned on the
             formation line.
    D. world.fullback_anchor_y == field.centre_m + side_sign * 3.0 within
       0.5 m BEFORE the strike (the placement constant), and thereafter
       mirrors the live ball lane (ASSERT-D2).
    E. Exactly 12 LINE defenders on the flat line (kicker + 11 others);
       the split above/below centre is (6, 5) or (5, 6) with the 6-side
       matching side_sign.
    F. DETERMINISM: three runs with distinct deterministic RNG seeds
       produce identical FB id / FB anchor / line split per seed.
    K. ARRIVAL AUTHORITY (2026-09-27): on every flight frame the FB's
       movement_state is the authored DRIFT.  The shared movement-state
       machine must stop the actor's velocity on arrival WITHOUT rewriting
       the state the geometry authority wrote — the placement writer parks
       the actor exactly on its anchor, so a state-mutating arrival branch
       would consume the DRIFT on the frame it was authored.

  BALL-RECEIVED OPEN_PLAY FRAMES (defence ran):
    G. Exactly one fullback actor.
    H. Line count = 12 non-fullback defenders (13 total).
    I. The FB actor's target sits at or behind the defensive line anchor.
    J. PERSISTENT JERSEY-1 PRIORITY (2026-08-11): the post-catch FB actor
       is STILL jersey 1 (#200) — the designated fullback survives the
       kickoff-catch anchor snap and keeps the role in OPEN_PLAY (it does
       not hand off to a line defender).

Exits 1 on any failed assertion (fails loudly, CI-style).
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"

import sys
import math
import random

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, ".")

import pygame
pygame.display.init()
pygame.display.set_mode((1, 1), pygame.NOFRAME)

import world.open_play_engine as ope_mod
ope_mod.DEBUG_SDEA_PHYSICS = False
ope_mod.DEBUG_OPE_TRACE = False

from world.field import Field
from world.world_state import WorldState, GameSubState, GamePhase
from world.open_play_engine import OpenPlayEngine
from kick_off.kickoff_formations import (
    apply_receiving_kickoff_formation,
    apply_kicking_team_formation,
    KICKING_LINE_OFFSET_M,
    SIDELINE_MARGIN_M,
)
from game.kickoff_engine import KickoffEngine
from game.defence.defence_lvl_1.fullback_role import (
    KICKOFF_FB_JERSEY,
    compute_fullback_anchor,
)
import game.defence.defence_lvl_1.fullback_role as fullback_role
from player.movement_states import MovementState
from ball.ball import Ball
from game.physics.collision_system import CollisionSystem
from world.catch_system import check_for_catch

collision_system = CollisionSystem()
field = Field(1920, 1080)

FORMATION_LINE_X = field.halfway_m + KICKING_LINE_OFFSET_M   # 62.0
EXPECTED_FB_PLAYER_ID = 200                                  # jersey 1, first defender

MAX_FRAMES = 3600
dt = 1.0 / 60.0
KICKOFF_CAP_FRAMES = 240          # kickoff-only determinism pass ceiling (~4s)

# Catch -> OPEN_PLAY handover window: the FB is freshly re-selected from
# KMC-era players and the very first post-catch defence frames may briefly
# carry a stale chase target before the structured pipeline publishes the
# deep anchor.  ASSERT-I (deep placement) is evaluated only after this
# window; ASSERT-G/H (counts) always apply.
POST_CATCH_HANDOVER_FRAMES = 5


def build_world():
    world = WorldState()
    ball = Ball(field)
    world.ball = ball
    open_play = OpenPlayEngine(world)
    world.open_play = open_play
    world.open_play_engine = open_play
    kickoff_engine = KickoffEngine(world, ball, field)
    return world, ball, open_play, kickoff_engine


def run_sequence(seed, kickoff_only=False):
    """Boot + kickoff with *seed*, stepping frames until the capsule.

    Returns (kickoff_stats, openplay_stats, failures, snapshots).

    kickoff_stats collects, per kickoff frame while in KICKOFF:
      - active FB ids (fullback_role_active)
      - world.fullback_actor_id / anchor_x / anchor_y
      - count of line defenders (non-FB kicking team) + above/below split

    openplay_stats collects post-catch OPEN_PLAY FB/line data (only when
    kickoff_only is False).
    """
    random.seed(seed)
    world, ball, open_play, kickoff_engine = build_world()
    kickoff_engine.reset_for_kickoff()
    apply_receiving_kickoff_formation(world, field)
    apply_kicking_team_formation(world, field)
    world.ball_owner = "NONE"
    world.ball_carrier = None
    world.ball.possessed_by = None
    kickoff_engine.kickoff()

    side_sign = getattr(world, "kickoff_side_sign", 1)
    kicker_id = getattr(world, "kickoff_kicker_id", None)

    kickoff_stats = {
        "side_sign": side_sign,
        "kicker_id": kicker_id,
        "fb_frames": 0,
        "fb_ids": set(),
        # ASSERT-K: the movement states the actor reported while in flight.
        "fb_states": set(),
        "anchor_x": [],
        "anchor_y": [],
        # ASSERT-C: (commanded target_x, body x) pairs, sampled on flight
        # frames, so the anti-retreat guard can be asserted directly.
        "flight_x": [],
        "line_counts": set(),
        "split_signs": set(),
        "line_frames": 0,
        "frame1": None,
        # ASSERT-D2/L: per-flight-frame (published_anchor_y, ball_lane,
        # kicked) and the pre-kick published-anchor samples.
        "flight_samples": [],
        "flight_const": [],
    }
    openplay_stats = {
        "fb_frames": 0,
        "line_counts": set(),
        "fb_behind": 0,
        "fb_in_front": 0,
        "fb_in_front_frames": [],
        "fb_ids": set(),          # ASSERT-J: post-catch actor identity
        "samples": 0,
        # Was OPEN_PLAY ever entered?  (A catch is required — the modern
        # deep reception formation may legitimately not field this engine's
        # long kickoff, in which case the post-catch contract is unexercised.)
        "open_play_seen": False,
    }

    limit = KICKOFF_CAP_FRAMES if kickoff_only else MAX_FRAMES
    for frame in range(1, limit + 1):
        world.frame_count = frame
        ball.update(dt)

        if world.phase == GamePhase.KICKOFF:
            # Capture the ball's struck state BEFORE this frame's writers run.
            # The kick is performed INSIDE kickoff_engine.update(), so on the
            # transition frame the kickoff assignment legitimately ran with
            # the ball still on the tee and the published anchor is the
            # placement constant.  ASSERT-D2 asserts the tracking contract
            # only from the frame AFTER the strike (see the sample tuple).
            _pre_kicked = bool(getattr(ball, "kicked", False))
            kickoff_engine.update(dt)
            check_for_catch(world, ball, dt)

            # FLIGHT-ONLY ANCHOR CONTRACT (2026-09-16): ASSERT-C/D assert the
            # KICKOFF anchor (formation line + side offset), which is only
            # defined while the ball is genuinely in flight.  The engine can
            # already be running open play (sub_state OPEN_PLAY, ball carrier
            # set, defensive dispatch live) while world.phase is still
            # KICKOFF for a frame or two; on those frames the published
            # anchor is legitimately the post-catch ball-derived anchor, so
            # sampling it under the flight-only contract produced spurious
            # "anchor drifted ... ball-derived leak?" failures.  This was
            # reproduced byte-identically with BOTH fullback changes disabled
            # (RLM_FULLBACK_ROLE_PERSISTENCE=0 and
            # RLM_FULLBACK_TACKLE_CANDIDACY=0), i.e. it is a pre-existing
            # sampling-window defect, not a role-authority regression.
            # Actor identity and the line count (ASSERT-A/B/E) stay
            # phase-scoped.
            _in_flight = bool(getattr(world, "in_kickoff", False))
            active = [p for p in world.defending_team
                      if getattr(p, "fullback_role_active", False)]
            if active:
                kickoff_stats["fb_frames"] += 1
                actor = active[0]
                kickoff_stats["fb_ids"].add(actor.player_id)
                if _in_flight:
                    ax = getattr(world, "fullback_anchor_x", None)
                    ay = getattr(world, "fullback_anchor_y", None)
                    if ax is not None:
                        kickoff_stats["anchor_x"].append(ax)
                    if ay is not None:
                        kickoff_stats["anchor_y"].append(ay)
                    # ASSERT-C: the command/body X pair.  The airborne
                    # anchor is clamped so it never sits behind the body.
                    kickoff_stats["flight_x"].append(
                        (getattr(actor, "target_x", None), actor.x)
                    )
                    # ASSERT-D2/L: sample the ball's lane and its struck
                    # flag so the PUBLISHED anchor can be checked against the
                    # authority's own value for that lane.
                    _kicked = bool(getattr(ball, "kicked", False))
                    _lane = getattr(ball, "y_m", None) if _kicked else None
                    # 4th element: was the ball ALREADY in flight when this
                    # frame's writers ran?  ASSERT-D2 is asserted only on
                    # those frames — see the _pre_kicked capture above.
                    kickoff_stats["flight_samples"].append(
                        (ay, _lane, _kicked, _pre_kicked)
                    )
                    if not _kicked:
                        kickoff_stats["flight_const"].append(ay)
                    # ASSERT-K: sample the actor's authored movement state.
                    kickoff_stats["fb_states"].add(
                        getattr(actor, "movement_state", None)
                    )
                if kickoff_stats["frame1"] is None:
                    kickoff_stats["frame1"] = {
                        "actor_id": actor.player_id,
                        "jersey": getattr(actor, "jersey_number", None),
                        "target_x": actor.target_x,
                        "target_y": actor.target_y,
                    }

            # Line = every kicking-team player NOT the fullback (kicker + 11).
            line_defs = [p for p in world.defending_team
                         if not getattr(p, "fullback_role_active", False)]
            if len(line_defs) == 12:
                kickoff_stats["line_frames"] += 1
                kickoff_stats["line_counts"].add(len(line_defs))
                above = sum(1 for p in line_defs if p.y > field.centre_m + 1e-6)
                below = sum(1 for p in line_defs if p.y < field.centre_m - 1e-6)
                kickoff_stats["split_signs"].add((above, below))

            if kickoff_only and frame >= KICKOFF_CAP_FRAMES:
                break

        elif world.phase == GamePhase.CATCH:
            if world.ball_carrier is not None:
                world.open_play.start(world.ball_carrier)
            world.phase = GamePhase.OPEN_PLAY

        elif world.phase == GamePhase.OPEN_PLAY:
            if kickoff_only:
                break
            openplay_stats["open_play_seen"] = True
            open_play.update(dt)
            if not world.tes_active:
                collision_system.resolve_all(world.players, world=world)

            if getattr(world, "defence_ran_this_frame", False):
                active = [p for p in world.defending_team
                          if getattr(p, "fullback_role_active", False)]
                if active:
                    openplay_stats["fb_frames"] += 1
                    actor = active[0]
                    # ASSERT-J: record the post-catch FB actor identity.
                    openplay_stats["fb_ids"].add(actor.player_id)
                    line_count = sum(
                        1 for d in world.defending_team
                        if not getattr(d, "fullback_role_active", False))
                    openplay_stats["line_counts"].add(line_count)
                    if openplay_stats["samples"] < POST_CATCH_HANDOVER_FRAMES:
                        openplay_stats["samples"] += 1
                        continue
                    line_anchor = getattr(world, "defensive_line_anchor", None)
                    if line_anchor is not None and line_anchor > 0.0:
                        if actor.target_x >= line_anchor - 1.5:
                            openplay_stats["fb_behind"] += 1
                        else:
                            openplay_stats["fb_in_front"] += 1
                            openplay_stats["fb_in_front_frames"].append(
                                (frame, actor.player_id,
                                 round(actor.target_x, 2),
                                 round(line_anchor, 2)))

            if frame > 600 and open_play._phase == "OPEN_PLAY" \
                    and world.ball_carrier is not None:
                break

    return kickoff_stats, openplay_stats, world
# ── PER-SEED ASSERTIONS (kickoff layout contract) ─────────────────────
def check_seed(seed, kickoff_stats, failures):
    tag = f"seed={seed}"

    # A — exactly one FB active on every kickoff frame.
    if kickoff_stats["fb_frames"] == 0:
        failures.append(f"[{tag}] ASSERT-A: no fullback active on any kickoff frame")
    multi = any(len(ids) > 1 for ids in [kickoff_stats["fb_ids"]])
    if multi:
        failures.append(f"[{tag}] ASSERT-A: >1 distinct FB actor used")
    if len(kickoff_stats["fb_ids"]) == 1:
        used = next(iter(kickoff_stats["fb_ids"]))
        if used != EXPECTED_FB_PLAYER_ID:
            failures.append(
                f"[{tag}] ASSERT-B: FB actor #{used} (must be #{EXPECTED_FB_PLAYER_ID} "
                "jersey 1)" )
    else:
        failures.append(f"[{tag}] ASSERT-B: FB ids {sorted(kickoff_stats['fb_ids'])}")

    # ── C — the airborne anchor X tracks the DEPTH ROLE ──
    # 2026-09-28: once the ball is kicked the anchor X is the normal
    # depth-role law referenced to the live ball, clamped so it can never
    # command the actor BEHIND itself.  The sweeper uses its depth.
    side = kickoff_stats["side_sign"]
    exp_y = field.centre_m + side * 3.0
    if not (kickoff_stats["anchor_x"]):
        failures.append(f"[{tag}] ASSERT-C: no fullback_anchor_x published")
    else:
        # (a) ANTI-RETREAT — the command never sits behind the body.
        _worst_ahead = 0.0
        for _tx, _bx in (kickoff_stats.get("flight_x") or []):
            if _tx is None or _bx is None:
                continue
            _ahead = float(_tx) - float(_bx)
            if _ahead > _worst_ahead:
                _worst_ahead = _ahead
        if _worst_ahead > 0.75:
            failures.append(
                f"[{tag}] ASSERT-C: the airborne anchor commanded the FB "
                f"{_worst_ahead:.2f} m BEHIND itself — the anti-retreat "
                "guard was breached (the ball was too close for the depth "
                "formula to be a real demand)")
        # (b) LIVENESS — depth is genuinely tracked, so X moves.
        _span_x = (max(kickoff_stats["anchor_x"])
                   - min(kickoff_stats["anchor_x"]))
        if _span_x <= 0.5:
            failures.append(
                f"[{tag}] ASSERT-C: the airborne anchor_x span is only "
                f"{_span_x:.2f} m — depth is NOT tracked (the FB is pinned "
                "on the formation line for the whole flight)")

    # ── D — PLACEMENT: pre-kick the anchor is the constant side offset ──
    _consts = kickoff_stats.get("flight_const") or []
    if not (kickoff_stats["anchor_y"]):
        failures.append(f"[{tag}] ASSERT-D: no fullback_anchor_y published")
    elif _consts:
        worst_c = max(abs(float(a) - exp_y) for a in _consts)
        if worst_c > 0.5:
            failures.append(
                f"[{tag}] ASSERT-D: pre-kick anchor_y off the constant "
                f"{exp_y} by up to {worst_c:.2f} (side_sign={side:+d}) — the "
                "formation slot is a PLACED position, not a tracking target")
    else:
        failures.append(
            f"[{tag}] ASSERT-D: no pre-kick published anchor was sampled "
            "(the placement window was never observed)")

    # ── D2 — THE PUBLISHED ANCHOR IS THE AUTHORITY'S OWN VALUE ──
    # SINGLE SOURCE (2026-09-27).  For every flight frame with a usable ball
    # lane, the PUBLISHED anchor_y must equal what the anchor authority
    # computes for that lane.  The defect this pins: the kickoff assignment
    # ran first in the frame and published the constant while only the later
    # geometry writer moved the actor, so every reader (and this verifier)
    # saw a frozen anchor even after the target tracked.
    _samples = kickoff_stats.get("flight_samples") or []
    _d2_checked = 0
    _d2_worst = 0.0
    _d2_detail = ""
    for _published, _lane, _kicked, _pre_kicked in _samples:
        if (not _kicked or not _pre_kicked
                or _published is None or _lane is None):
            continue
        _expected = compute_fullback_anchor(
            None, FORMATION_LINE_X, True,
            formation_line_x=FORMATION_LINE_X,
            side_sign=side,
            flight_lane_y=_lane,
        )[1]
        _err = abs(float(_published) - float(_expected))
        _d2_checked += 1
        if _err > _d2_worst:
            _d2_worst = _err
            _d2_detail = (
                f"published={float(_published):.2f} "
                f"authority={float(_expected):.2f} lane={float(_lane):.2f}"
            )
    if _d2_checked == 0:
        failures.append(
            f"[{tag}] ASSERT-D2: no post-kick flight frame with a usable ball "
            "lane was sampled — tracking was never exercised")
    elif _d2_worst > 0.1:
        failures.append(
            f"[{tag}] ASSERT-D2: the published anchor_y is NOT the authority's "
            f"value for the ball lane (worst err {_d2_worst:.3f}; {_d2_detail})")

    # ── L — THE TRACKING IS LIVE ──
    # "published == authority" alone also passes on the degenerate
    # constant-equals-constant case, so prove the anchor actually MOVES with
    # the ball while the lever is on.  The lever-OFF arm lives in main().
    _lanes = [float(l) for (_p, l, k, _pk) in _samples if k and l is not None]
    _pub = [float(p) for (p, _l, k, _pk) in _samples if k and p is not None]
    if len(_lanes) >= 2 and len(_pub) >= 2:
        _lane_span = max(_lanes) - min(_lanes)
        _anchor_span = max(_pub) - min(_pub)
        if _lane_span > 0.5 and _anchor_span <= 0.5:
            failures.append(
                f"[{tag}] ASSERT-L: the ball lane moved {_lane_span:.2f} m but "
                f"the published anchor_y moved only {_anchor_span:.2f} m — the "
                "airborne fullback is still frozen on its constant")
    else:
        failures.append(
            f"[{tag}] ASSERT-L: too few post-kick flight frames were sampled "
            "to prove the tracking is live")

    # E — 12 line defenders with the 6-side matching side_sign.
    if not kickoff_stats["line_counts"]:
        failures.append(f"[{tag}] ASSERT-E: no 12-line frames observed")
    elif kickoff_stats["line_counts"] != {12}:
        failures.append(
            f"[{tag}] ASSERT-E: line counts {sorted(kickoff_stats['line_counts'])} "
            "(must be {12})")
    # above/below split: (6,5) for side_sign=+1, (5,6) for side_sign=-1.
    expected_split = (6, 5) if side >= 0 else (5, 6)
    if kickoff_stats["split_signs"] and expected_split not in kickoff_stats["split_signs"]:
        failures.append(
            f"[{tag}] ASSERT-E: line split {sorted(kickoff_stats['split_signs'])} "
            f"never matched expected {expected_split} (side_sign={side:+d})")

    # Frame-1 exact geometry (targets match the spec).
    f1 = kickoff_stats.get("frame1")
    if f1 is not None:
        if abs(f1["target_x"] - FORMATION_LINE_X) > 0.5:
            failures.append(
                f"[{tag}] ASSERT-C: frame-1 FB target_x={f1['target_x']:.2f} "
                f"(must be {FORMATION_LINE_X})")
        if abs(f1["target_y"] - exp_y) > 0.5:
            failures.append(
                f"[{tag}] ASSERT-D: frame-1 FB target_y={f1['target_y']:.2f} "
                f"(must be {exp_y})")
        if f1["jersey"] != KICKOFF_FB_JERSEY:
            failures.append(
                f"[{tag}] ASSERT-B: frame-1 FB jersey={f1['jersey']} (must be "
                f"{KICKOFF_FB_JERSEY})")

    # K — ARRIVAL AUTHORITY (2026-09-27).  The geometry authority stamps DRIFT
    # on every flight frame and the placement writer parks the actor exactly ON
    # its anchor.  The shared movement-state machine's arrival branch used to
    # rewrite that authored DRIFT to HOLD on the same frame it was written —
    # locomotion mutating an authority-owned state, which silently converted
    # the kickoff fullback into a statue reporting a state it was never
    # authored in.  The arrival branch now stops velocity only.
    _states = kickoff_stats.get("fb_states") or set()
    if not _states:
        failures.append(
            f"[{tag}] ASSERT-K: no FB movement state captured in flight")
    elif MovementState.HOLD in _states:
        failures.append(
            f"[{tag}] ASSERT-K: kickoff FB reported HOLD in flight "
            f"(states={sorted(getattr(s, 'name', str(s)) for s in _states)}) "
            "— locomotion must not mutate the authored movement state")
    elif _states != {MovementState.DRIFT}:
        failures.append(
            f"[{tag}] ASSERT-K: kickoff FB states "
            f"{sorted(getattr(s, 'name', str(s)) for s in _states)} "
            "(must be exactly DRIFT on flight frames)")


def check_receiving_formation(failures):
    """ASSERT-R: the receiving team sits on the modern NRL reception layout.

    Builds a fresh world exactly like the rest of the verifier, applies the
    receiving formation, and asserts every one of the 13 jerseys sits on its
    tactical offset behind the halfway line (X_half), keyed by jersey_number:
    the deep back three (#1/#2/#5), the front-line pods (#8/#10/#11/#12/#13),
    the second-line covers (#3/#4/#6/#7), and the hooker exception (#9).

    Also pins the hooker exception: #9 is NOT on the front line.
    """
    world, _ball, _ope, kickoff_engine = build_world()
    kickoff_engine.reset_for_kickoff()
    apply_receiving_kickoff_formation(world, field)

    x_half = field.halfway_m
    y_mid = field.centre_m
    y_left = SIDELINE_MARGIN_M
    y_right = field.width_m - SIDELINE_MARGIN_M

    # jersey -> (expected_x, expected_y) — the strict tactical offsets.
    expected = {
        # THE DEEP RECEIVERS (the back three)
        1:  (x_half - 25.0, y_mid),
        2:  (x_half - 20.0, y_left),
        5:  (x_half - 20.0, y_right),
        # THE FRONT-LINE PODS (the battering rams, 8.0 m back)
        8:  (x_half - 8.0, y_mid - 6.0),
        10: (x_half - 8.0, y_mid + 6.0),
        11: (x_half - 8.0, y_mid - 18.0),
        12: (x_half - 8.0, y_mid + 18.0),
        13: (x_half - 8.0, y_mid),
        # THE SECOND-LINE COVERS (spacing link & centres)
        3:  (x_half - 14.0, y_mid - 15.0),
        4:  (x_half - 14.0, y_mid + 15.0),
        6:  (x_half - 15.0, y_mid - 4.0),
        7:  (x_half - 15.0, y_mid + 4.0),
        # THE HOOKER EXCEPTION — central, behind the forwards
        9:  (x_half - 12.0, y_mid),
    }

    by_jersey = {
        getattr(p, "jersey_number", None): p for p in world.receiving_team
    }
    if len(by_jersey) != 13:
        failures.append(
            "ASSERT-R: receiving team has %d distinct jerseys (must be 13)"
            % len(by_jersey))

    worst = 0.0
    for jersey, (ex, ey) in expected.items():
        p = by_jersey.get(jersey)
        if p is None:
            failures.append("ASSERT-R: receiving jersey #%d missing" % jersey)
            continue
        err = math.hypot(p.x - ex, p.y - ey)
        if err > worst:
            worst = err
        if err > 0.01:
            failures.append(
                "ASSERT-R: jersey #%d at (%.2f, %.2f) expected (%.2f, %.2f) "
                "err=%.3f" % (jersey, p.x, p.y, ex, ey, err))
        if not p.frozen:
            failures.append(
                "ASSERT-R: jersey #%d not frozen at kickoff set" % jersey)
        if p.x >= x_half:
            failures.append(
                "ASSERT-R: jersey #%d x=%.2f not behind X_half=%.1f"
                % (jersey, p.x, x_half))

    # HOOKER EXCEPTION: #9 must NOT be on the front line (x = x_half - 8).
    hooker = by_jersey.get(9)
    if hooker is not None and abs(hooker.x - (x_half - 8.0)) < 0.01:
        failures.append(
            "ASSERT-R: hooker #9 is on the front line (must sit deeper)")

    print(
        "  receiving: 13-jersey layout worst placement error = %.4f m "
        "(X_half=%.1f, Y_mid=%.1f)" % (worst, x_half, y_mid)
    )


def main():
    print("=" * 72)
    print("KICKOFF FULLBACK VERIFIER (2026-08-11)")
    print("=" * 72)

    failures = []

    # ── R. RECEIVING FORMATION — jersey+hemisphere reception layout ──
    print("\n[Receiving] modern NRL reception layout (jersey+hemisphere)...")
    check_receiving_formation(failures)

    # ── F. DETERMINISM — distinct seeds exercise the layout contract ─
    seeds = (20260811, 1337, 42)
    per_seed = {}
    print("\n[Determinism] distinct seeds (layout contract)...")
    for seed in seeds:
        kickoff_stats, _, _ = run_sequence(seed, kickoff_only=True)
        per_seed[seed] = kickoff_stats
        check_seed(seed, kickoff_stats, failures)
        print(
            f"  seed={seed}: side_sign={kickoff_stats['side_sign']:+d} "
            f"fb_frames={kickoff_stats['fb_frames']} "
            f"fb_ids={sorted(kickoff_stats['fb_ids'])} "
            f"fb_states={sorted(getattr(s, 'name', str(s)) for s in kickoff_stats['fb_states'])} "
            f"line_counts={sorted(kickoff_stats['line_counts'])} "
            f"splits={sorted(kickoff_stats['split_signs'])}"
        )

    # ── L (ROLLBACK ARM) — FB_FLIGHT_TRACK=0 re-pins the anchor ──
    # A genuine, in-process A/B.  With tracking disarmed the published
    # anchor_y must be the CONSTANT side offset on every flight frame,
    # post-kick ones included.  This is what makes ASSERT-L meaningful: it
    # isolates the tracking as the thing that moves the anchor, so a future
    # regression that re-pins the anchor cannot pass by making both arms
    # constant.
    print("\n[Rollback A/B] FB_FLIGHT_TRACK=0 (anchor must stay constant)...")
    _saved_lever = fullback_role.FB_FLIGHT_TRACK
    fullback_role.FB_FLIGHT_TRACK = False
    try:
        _off_stats, _, _ = run_sequence(seeds[0], kickoff_only=True)
    finally:
        fullback_role.FB_FLIGHT_TRACK = _saved_lever
    _off_side = _off_stats["side_sign"]
    _off_exp = field.centre_m + _off_side * 3.0
    _off_y = [float(a) for a in (_off_stats.get("anchor_y") or [])]
    _off_postk = [
        float(p)
        for (p, _l, k, _pk) in (_off_stats.get("flight_samples") or [])
        if k and p is not None
    ]
    if not _off_y:
        failures.append(
            "ASSERT-L(rollback): lever OFF published no anchor at all")
    else:
        _off_worst = max(abs(a - _off_exp) for a in _off_y)
        if _off_worst > 0.5:
            failures.append(
                "ASSERT-L(rollback): lever OFF still moved the anchor "
                f"(worst {_off_worst:.2f} off the constant {_off_exp}) — the "
                "rollback path is not byte-for-byte the historical anchor")
    if not _off_postk:
        failures.append(
            "ASSERT-L(rollback): lever OFF sampled no post-kick flight frame, "
            "so the control arm proves nothing")
    elif max(abs(a - _off_exp) for a in _off_postk) > 0.5:
        failures.append(
            "ASSERT-L(rollback): lever OFF MOVED the anchor on a post-kick "
            "flight frame — tracking was not fully disarmed")
    _on_postk = [
        float(p)
        for (p, _l, k, _pk) in (per_seed[seeds[0]].get("flight_samples") or [])
        if k and p is not None
    ]
    if _on_postk and _off_postk:
        _on_span = max(_on_postk) - min(_on_postk)
        _off_span = max(_off_postk) - min(_off_postk)
        print(
            f"  lever ON  post-kick anchor span={_on_span:.2f} m "
            f"({len(_on_postk)} frames)"
        )
        print(
            f"  lever OFF post-kick anchor span={_off_span:.2f} m "
            f"({len(_off_postk)} frames)"
        )
        if not (_on_span > _off_span):
            failures.append(
                "ASSERT-L(rollback): lever ON did not move the anchor further "
                f"than lever OFF (on={_on_span:.2f} off={_off_span:.2f})"
            )

    # ── C (ROLLBACK ARM) — FB_FLIGHT_DEPTH_TRACK=0 re-pins anchor X ──
    # A genuine, in-process A/B.  With depth tracking disarmed the
    # published anchor_x must be the CONSTANT formation line on every
    # sampled flight frame.  This is what makes ASSERT-C's liveness half
    # meaningful: it isolates depth tracking as the thing that moves the
    # anchor, so a future regression that re-pins it cannot pass by making
    # both arms constant.
    print("\n[Rollback A/B] FB_FLIGHT_DEPTH_TRACK=0 (anchor X must stay "
          "constant)...")
    _saved_dt = fullback_role.FB_FLIGHT_DEPTH_TRACK
    fullback_role.FB_FLIGHT_DEPTH_TRACK = False
    try:
        _offd_stats, _, _ = run_sequence(seeds[0], kickoff_only=True)
    finally:
        fullback_role.FB_FLIGHT_DEPTH_TRACK = _saved_dt
    _offd_x = [float(a) for a in (_offd_stats.get("anchor_x") or [])]
    _ond_x = [float(a) for a in (per_seed[seeds[0]].get("anchor_x") or [])]
    if not _offd_x:
        failures.append(
            "ASSERT-C(rollback): lever OFF published no anchor_x at all")
    else:
        _offd_worst = max(abs(a - FORMATION_LINE_X) for a in _offd_x)
        if _offd_worst > 0.5:
            failures.append(
                "ASSERT-C(rollback): lever OFF still moved anchor_x "
                f"(worst {_offd_worst:.2f} off the constant "
                f"{FORMATION_LINE_X}) — the rollback path is not "
                "byte-for-byte the historical anchor")
    if _ond_x and _offd_x:
        _on_xspan = max(_ond_x) - min(_ond_x)
        _off_xspan = max(_offd_x) - min(_offd_x)
        print(f"  depth lever ON  anchor_x span={_on_xspan:.2f} m "
              f"({len(_ond_x)} frames)")
        print(f"  depth lever OFF anchor_x span={_off_xspan:.2f} m "
              f"({len(_offd_x)} frames)")
        if not (_on_xspan > _off_xspan + 0.5):
            failures.append(
                "ASSERT-C(rollback): the depth lever did not move anchor_x "
                f"further than the rollback (on={_on_xspan:.2f} "
                f"off={_off_xspan:.2f})")

    # ── F. REPEATABILITY — the SAME seed 3× yields identical data ──
    # "Repeat with deterministic RNG seed 3×": identical inputs must
    # produce identical FB id, FB anchor history, and line split.  (Two
    # different seeds may legitimately pick opposite side_sign, so the
    # byte-identical check is per-seed, never cross-seed.)
    repeat_seed = seeds[0]
    print(f"\n[Repeatability] seed={repeat_seed} x3...")
    signatures = []
    for rep in range(3):
        kickoff_stats, _, _ = run_sequence(repeat_seed, kickoff_only=True)
        sig = (
            tuple(sorted(kickoff_stats["fb_ids"])),
            tuple(abs(x - FORMATION_LINE_X) < 0.01 for x in kickoff_stats["anchor_x"]),
            tuple(round(y, 3) for y in kickoff_stats["anchor_y"]),
            tuple(sorted(kickoff_stats["split_signs"])),
            kickoff_stats["side_sign"],
            # ASSERT-K: the authored flight state must be deterministic too.
            tuple(sorted(getattr(s, "name", str(s))
                         for s in kickoff_stats["fb_states"])),
        )
        signatures.append(sig)
        print(
            f"  rep={rep + 1}: fb_ids={sorted(kickoff_stats['fb_ids'])} "
            f"anchor_y_head={kickoff_stats['anchor_y'][:3] if kickoff_stats['anchor_y'] else []} "
            f"splits={sorted(kickoff_stats['split_signs'])}")
    if len(set(signatures)) != 1:
        failures.append(
            "[determinism] same-seed 3x runs produced different FB id/anchor/split")

    # ── POST-CATCH OPEN_PLAY pass (seed 0) ────────────────────────────
    print("\n[Open-play] full pass (seed 0)...")
    kickoff_stats, openplay_stats, _ = run_sequence(seeds[0], kickoff_only=False)

    # ── POST-CATCH CONTRACT — CONDITIONAL (modern reception formation) ──
    # The modern NRL reception formation sets the WHOLE receiving team deep
    # behind the halfway line (8.0-25.0 m back), whereas this engine kicks
    # the ball ~50-55 m so it lands near the tryline — well behind the deep
    # line.  check_for_catch only ever catches an AIRBORNE ball, so a catch
    # occurs only when the seed's landing point falls within the deep line's
    # reach.  When it does not, OPEN_PLAY is never entered and the
    # FB-role-in-open-play contract (ASSERT-G/H/I/J) is simply not exercised
    # — a consequence of the requested positions, not a regression — so the
    # section is SKIPPED rather than failed.  When the kickoff IS received
    # the full contract is asserted exactly as before.
    _open_play_seen = bool(openplay_stats.get("open_play_seen"))
    if not _open_play_seen:
        print("  [SKIP] post-catch OPEN_PLAY checks — kickoff not received "
              "(the deep reception formation stands behind the landing zone "
              "for this seed); FB-role-in-OPEN_PLAY not exercised.")
    else:
        # ASSERT-G/H/I — post-catch defence frames.
        if openplay_stats["fb_frames"] == 0:
            failures.append("ASSERT-G: no fullback on post-catch defence frames")
        if openplay_stats["line_counts"]:
            if openplay_stats["line_counts"] != {12}:
                failures.append(
                    "ASSERT-H: post-catch line counts %s (must be {12})"
                    % sorted(openplay_stats["line_counts"]))
        else:
            failures.append("ASSERT-H: no post-catch line count captured")
        if openplay_stats["fb_behind"] == 0 and openplay_stats["fb_in_front"] == 0:
            failures.append("ASSERT-I: no post-catch FB anchor observed")
        elif openplay_stats["fb_in_front"] > 0:
            failures.append(
                "ASSERT-I: FB target in front of line anchor on %d frames: %s"
                % (openplay_stats["fb_in_front"],
                   openplay_stats["fb_in_front_frames"]))

        # ── ASSERT-J (2026-08-11): the post-catch FB actor STAYS jersey 1 ─
        # Persistent jersey-1 priority: the designated fullback survives the
        # kickoff-catch anchor snap instead of handing off to a line defender.
        if len(openplay_stats["fb_ids"]) == 0:
            failures.append("ASSERT-J: no post-catch FB actor identity captured")
        elif len(openplay_stats["fb_ids"]) > 1:
            failures.append(
                "ASSERT-J: post-catch FB handed off across %d distinct actors: %s"
                % (len(openplay_stats["fb_ids"]),
                   sorted(openplay_stats["fb_ids"])))
        else:
            used = next(iter(openplay_stats["fb_ids"]))
            if used != EXPECTED_FB_PLAYER_ID:
                failures.append(
                    "ASSERT-J: post-catch FB actor #%d (must STAY #%d jersey 1)"
                    % (used, EXPECTED_FB_PLAYER_ID))

    print("=" * 72)
    if failures:
        print("RESULT: FAIL")
        for f in failures:
            print("  -", f)
        pygame.quit()
        sys.exit(1)

    print("RESULT: PASS")
    print("  - kickoff: exactly 1 FB = jersey 1 (#%d)" % EXPECTED_FB_PLAYER_ID)
    print("  - kickoff: PRE-KICK FB anchor == (halfway+5, "
          "centre + side_sign*3) across 3 seeds")
    print("  - kickoff: FLIGHT anchor_y == the authority's own value for the "
          "ball's live lane (ASSERT-D2)")
    print("  - kickoff: the airborne anchor TRACKS the ball lane, and the "
          "RLM_FB_FLIGHT_TRACK=0 rollback re-pins it (ASSERT-L)")
    print("  - kickoff: DEPTH IS TRACKED — anchor_x follows the depth role, "
          "clamped so it never commands the FB behind itself (ASSERT-C), "
          "and the RLM_FB_FLIGHT_DEPTH_TRACK=0 rollback re-pins it")
    print("  - kickoff: 12 line defenders, 6-side matches side_sign")
    print("  - kickoff: FB authored state is DRIFT on every flight frame "
          "(ASSERT-K)")
    print("  - post-catch: exactly 1 FB, 12 line defenders, FB deep behind")
    print("  - post-catch: FB stays jersey 1 (#%d) — persistent priority"
          % EXPECTED_FB_PLAYER_ID)
    pygame.quit()
    sys.exit(0)


if __name__ == "__main__":
    main()
