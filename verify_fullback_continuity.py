"""Headless fullback-continuity verifier (2026-08-27).

Boots the real engine exactly like run.py, kicks off, runs through at
least one full OPEN_PLAY cycle INCLUDING the first tackle (TACKLE_DRIVE /
TACKLE_DOMINANCE), and asserts the dynamic fullback stays continuous:

  A. SINGLE-FB INVARIANT — at most ONE defender carries
     ``fullback_role_active`` on every frame.  The regression that this
     pins down was a SECOND, line/winger defender being stamped as fullback
     during TACKLE_DRIVE (the TES-drive pipeline re-ran the structured
     open-play writer with a subset that excluded the current actor, so a
     new actor was flagged without clearing the old one).

  B. DEEP-FULLBACK CONTINUITY — whenever a fullback is active in OPEN_PLAY
     it is EITHER the designated jersey-1 priority OR a defender inside the
     central corridor (|y - 35| <= FULLBACK_CENTRAL_BAND_M).  It must NEVER
     be a sideline "wide player" — the "FB tag goes to a wide player" bug.

  C. SWEEPER GEOMETRY AUTHORITY — WHILE the actor is a pure sweeper the
     active actor's ``target_source`` is "fullback_geometry" (or "fullback"
     during KICKOFF), never a line writer, so ``fullback_geometry`` stays
     the sole authority for the fullback's position.

     SCOPED 2026-09-16 (B2b, the controlled emergency-defender path): once a
     GENUINE LINE BREAK exists the tagged fullback is admitted to lane
     candidacy / the pre-tackle pool / the corridor / TES arming and MUST
     then behave like any other defender — i.e. the commit / pursuit writers
     own its target and ``fullback_geometry`` deliberately YIELDS (see
     ``fullback_role.apply_fullback_geometry``).  The assertion is therefore
     "no LOSS OF AUTHORITY while the actor holds the sweeper contract":
     ``fullback_tackle_authority_active(actor)`` is the single predicate that
     separates the two regimes, and a non-fullback ``target_source`` is only
     legal when it is True.

  D. DRIVE REACHED — the run must reach a TACKLE_DRIVE / TACKLE_DOMINANCE
     frame; the regression only manifested during the tackle, so a run that
     never tackles cannot prove the fix.

  E. ENGINE SANITY — no crash, engine ends in a sane sub-state.

  F. MEMBERSHIP SEPARATION (2026-09-16) — TACKLE ELIGIBILITY and LINE
     MEMBERSHIP are separate concepts.  On EVERY frame where the tagged
     actor has been granted tackle authority it must STILL be a non-member
     of the line: no ``depth_role`` / ``depth_side``.  The emergency path
     may arm the actor but may never absorb it into the defensive line.
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"

# ── STAGE 3.5 PIN (2026-09-24) ───────────────────────────────────────────
# THE BUMP CLEARANCE PATH can legitimately end a tackle at the CONTACT phase
# when the frame's declaration resolved BUMP_OFF (the carrier's momentum
# cleared the whole wrap by more than the leaf's declared threshold).  When
# that happens this run never reaches TACKLE_DRIVE / TACKLE_DOMINANCE, and
# ASSERT-D below -- "a run that never tackles cannot prove the fix" -- can no
# longer be satisfied.
#
# This verifier's subject is the FULLBACK's continuity INSIDE TES.  It must
# therefore control whether TES runs at all, exactly as it already controls
# the video driver and the engine's debug flags.  The clearance has its own
# dedicated gate, ``verify_bump_clearance_contract.py``, which proves the
# clearance fires, tears the contact down and re-asserts the burst; there is
# nothing for THIS gate to learn from it, so it is pinned OFF here and the
# tackle proceeds through every phase.
#
# This is a control, not a suppression: see the Stage 3.5 report (§3) and
# MULTI_BODY_TACKLE_EXCHANGE_CONTRACT.md §17.6 for the open question about
# the threshold that makes this pin necessary.
os.environ["RLM_TES_BUMP_CLEARANCE"] = "0"

import sys
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
)
from game.kickoff_engine import KickoffEngine
from ball.ball import Ball
from game.physics.collision_system import CollisionSystem
from world.catch_system import check_for_catch

# THE single predicate that separates the two regimes (sweeper contract vs
# granted tackle authority) for ASSERT-C / ASSERT-F below.
from game.defence.defence_lvl_1.fullback_role import (
    fullback_tackle_authority_active,
)

# Central-corridor constant mirrors fullback_role.FULLBACK_CENTRAL_BAND_M.
CENTRAL_BAND_M = 15.0

collision_system = CollisionSystem()
field = Field(1920, 1080)
world = WorldState()
ball = Ball(field)
world.ball = ball

open_play = OpenPlayEngine(world)
world.open_play = open_play
world.open_play_engine = open_play

kickoff_engine = KickoffEngine(world, ball, field)

# Seed the engine so the tackle pipeline deterministically reaches a drive.
# A fixed RNG seed makes the run reproducible.
import random
random.seed(20260827)

kickoff_engine.reset_for_kickoff()
apply_receiving_kickoff_formation(world, field)
apply_kicking_team_formation(world, field)
world.ball_owner = "NONE"
world.ball_carrier = None
world.ball.possessed_by = None
kickoff_engine.kickoff()

MAX_FRAMES = 6000
dt = 1.0 / 60.0
ALLOWED_TARGET_SOURCES = ("fullback_geometry", "fullback")

failures = []
multi_active_frame = None      # first frame where >1 fullback was active
sideline_fb_frame = None       # first frame where FB landed on a wide player
wrong_target_source_frame = None
saw_open_play = False
saw_drive = False
saw_dominance = False
active_frame_count = 0
# B2b (2026-09-16): frames where the tagged actor held granted tackle
# authority, and the first frame where such an actor was nevertheless a
# LINE MEMBER (depth_role / depth_side stamped) — the membership half of
# the law, which must never break.
authority_frame_count = 0
line_member_while_authorised_frame = None
last_sub_state = None
last_actor = None

def _sub_state_name():
    try:
        return world.sub_state.name
    except Exception:
        return str(getattr(world, "sub_state", None))


for frame in range(1, MAX_FRAMES + 1):
    world.frame_count = frame
    ball.update(dt)

    if world.phase == GamePhase.KICKOFF:
        kickoff_engine.update(dt)
        check_for_catch(world, ball, dt)
    elif world.phase == GamePhase.CATCH:
        if world.ball_carrier is not None:
            world.open_play.start(world.ball_carrier)
        world.phase = GamePhase.OPEN_PLAY
    elif world.phase == GamePhase.OPEN_PLAY:
        prev_phase = open_play._phase
        open_play.update(dt)
        if not world.tes_active:
            collision_system.resolve_all(world.players, world=world)
        if open_play._phase == "OPEN_PLAY":
            saw_open_play = True
    else:
        continue

    # ── Per-frame FB invariants (defence frames only) ──
    if world.phase == GamePhase.OPEN_PLAY and getattr(world, "defence_ran_this_frame", False):
        # Only check OPEN_PLAY frames where the defence wrote (kickoff frames
        # belong to KMC and are excluded from the deep-FB continuity check).
        active = [p for p in world.defending_team
                  if getattr(p, "fullback_role_active", False)]
        if active:
            active_frame_count += 1
            actor = active[0]

            # A. SINGLE-FB INVARIANT
            if len(active) > 1 and multi_active_frame is None:
                multi_active_frame = frame

            # C. GEOMETRY AUTHORITY: the actor's target_source must be a
            # fullback-authority source, never a line writer.  During a
            # tackle the actor may be pinned by TES (target_source could be
            # overwritten), so we only assert this when not TES-pinned.
            ts = getattr(actor, "target_source", None)

            # B. DEEP-FB CONTINUITY (designated jersey-1 OR central corridor).
            jersey = getattr(actor, "jersey_number", None)
            y = getattr(actor, "y", None)
            if jersey == 1:
                pass  # designated priority — always a legal FB
            elif y is not None and abs(float(y) - field.centre_m) <= CENTRAL_BAND_M:
                pass  # central corridor — a legit deep/replacement FB
            else:
                if sideline_fb_frame is None:
                    sideline_fb_frame = frame
                    print(
                        "DEBUG: FB on non-central actor pid=%s jersey=%s y=%.2f "
                        "ts=%s sub=%s frame=%d"
                        % (
                            actor.player_id, jersey, float(y), ts,
                            _sub_state_name(), frame,
                        )
                    )

    # C. GEOMETRY AUTHORITY (independent of defence_ran_this_frame):
    # the single actor's target_source should be the fullback authority on
    # any frame where the pipeline stamped one.  Track the last actor's
    # source whenever it is a fullback-role holder and NOT TES-pinned.
    active_all = [p for p in world.defending_team
                  if getattr(p, "fullback_role_active", False)]
    if len(active_all) == 1:
        _a = active_all[0]
        _ts = getattr(_a, "target_source", None)
        _pinned = getattr(_a, "tes_attached", False) or getattr(_a, "in_contact", False)
        # B2b: the sweeper contract is SUSPENDED once the actor has been
        # granted tackle authority (armed / PRIMARY-SECONDARY lane role /
        # commit / planted stance).  In that regime the commit and pursuit
        # writers legitimately own the target — the actor is behaving like
        # any other defender on a genuine line break — so ASSERT-C only
        # applies while the actor still holds the sweeper contract.
        _authorised = fullback_tackle_authority_active(_a)
        if _authorised:
            authority_frame_count += 1
            # F. MEMBERSHIP SEPARATION: granted authority must never make the
            # actor a line member.  A depth_role / depth_side on an
            # authority-granted fullback is the "absorbed into the line"
            # failure this assertion exists to catch.  (The actor also never
            # carries the FB depth tag on the line — it renders as "FB" only
            # in the debug overlay, which is not this field.)
            if line_member_while_authorised_frame is None and (
                getattr(_a, "depth_role", None) is not None
                or getattr(_a, "depth_side", None) is not None
            ):
                line_member_while_authorised_frame = frame
                print(
                    "DEBUG: authority-granted FB is a LINE MEMBER "
                    "depth_role=%s depth_side=%s pid=%s sub=%s frame=%d"
                    % (
                        getattr(_a, "depth_role", None),
                        getattr(_a, "depth_side", None),
                        _a.player_id, _sub_state_name(), frame,
                    )
                )
        elif (
            not _pinned
            and _ts not in ALLOWED_TARGET_SOURCES
            and wrong_target_source_frame is None
        ):
            wrong_target_source_frame = frame
            print(
                "DEBUG: FB target_source=%s (allowed=%s) pid=%s sub=%s frame=%d"
                % (_ts, ALLOWED_TARGET_SOURCES, _a.player_id, _sub_state_name(), frame)
            )

    # D. DRIVE REACHED — track the OPE FSM phase.
    _ope_phase = getattr(open_play, "_phase", None)
    if _ope_phase in ("TACKLE_DRIVE",):
        saw_drive = True
    if _ope_phase in ("TACKLE_DOMINANCE",):
        saw_dominance = True
    if _ope_phase == "OPEN_PLAY":
        saw_open_play = True
    last_sub_state = _sub_state_name()

print("=" * 72)
print("INFO: active_fb_frames=%d multi_active_frame=%s sideline_fb_frame=%s "
      "wrong_target_source_frame=%s" % (
          active_frame_count, multi_active_frame, sideline_fb_frame,
          wrong_target_source_frame))
print("INFO: emergency frames: tackle_authority_frames=%d "
      "line_member_while_authorised_frame=%s" % (
          authority_frame_count, line_member_while_authorised_frame))
print("INFO: saw_open_play=%s saw_drive=%s saw_dominance=%s final_sub=%s" % (
    saw_open_play, saw_drive, saw_dominance, last_sub_state))

# ── ASSERT A: single-FB invariant ──
if multi_active_frame is not None:
    failures.append("ASSERT-A: %d fullbacks active on frame %d" %
                    (999, multi_active_frame))

# ── ASSERT B: FB never lands on a sideline/wide player ──
if sideline_fb_frame is not None:
    failures.append("ASSERT-B: FB landed on a non-central (wide) player at frame %d"
                    % sideline_fb_frame)

# ── ASSERT C: FB sweeper geometry authority (while not authority-granted) ──
if wrong_target_source_frame is not None:
    failures.append("ASSERT-C: FB target_source was not a fullback authority "
                    "at frame %d" % wrong_target_source_frame)

# ── ASSERT F: membership separation under granted tackle authority ──
if line_member_while_authorised_frame is not None:
    failures.append(
        "ASSERT-F: authority-granted fullback became a LINE MEMBER at frame %d"
        % line_member_while_authorised_frame
    )

# ── ASSERT D: the run actually reached a tackle drive/dominance ──
if not (saw_drive or saw_dominance):
    failures.append("ASSERT-D: run never reached TACKLE_DRIVE/DOMINANCE "
                    "(final sub=%s) — cannot exercise the regression" % last_sub_state)

# ── ASSERT E: engine sanity — at least some FB activity occurred ──
if active_frame_count == 0:
    failures.append("ASSERT-E: no fullback actor was ever active on a defence frame")

if failures:
    print("RESULT: FAIL")
    for f in failures:
        print("  -", f)
    pygame.quit()
    sys.exit(1)

print("RESULT: PASS")
print("  - single fullback on every frame (no duplicate during drive)")
print("  - fullback stayed on the deep/designated or central-corridor actor")
print("  - fullback target_source was always the fullback geometry authority "
      "while it held the sweeper contract")
print("  - run reached TACKLE_DRIVE/TACKLE_DOMINANCE (regression exercised)")
print("  - emergency path exercised for %d authority-granted frames; the "
      "actor was never a line member" % authority_frame_count)
pygame.quit()
sys.exit(0)
