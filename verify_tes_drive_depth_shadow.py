"""Headless verification for the 2026-08-16 TES-drive BC-shadow re-anchor.

Boots the real engine exactly like run.py, runs until a TACKLE_DRIVE, and
while the BC is driven forward samples:

  * The live ball-carrier X delta across drive frames.
  * Every non-TES-participant defender's role-depth X target
    (``target_x``) and depth_role.

Fails loudly if:
  A. No tackle / no TACKLE_DRIVE was reached within MAX_FRAMES.
  B. The BC never moved forward during the drive (test cannot prove the
     shadow).  This is a soft assertion: a zero-drive tackle still passes
     the shadow checks, but we need drive motion to prove reaction.
  C. SHADOW: during the drive, any eligible line defender's target_x
     moved LESS than 50% of the BC's forward delta (the line stayed
     anchored to the stale pre-drive ruck instead of stepping back with
     the carrier).
  D. A defender with a valid depth_role received no tes_drive_shadow
     target_source on the sampled drive frame (writer never fired).
  E. The dynamic fullback was shadowed (must be excluded).
  F. Markers / DH (defender_role MARKER_A/B or DUMMY_HALF) were shadowed.

The verifier reads the SAME role-depth table the engine uses
(DefenceTuners.WIDTH_OFFSETS) so the expected target is independent of
the pipeline internals.
"""
import math
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"

import sys
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, ".")

import pygame
pygame.display.init()
pygame.display.set_mode((1, 1), pygame.NOFRAME)

import world.open_play_engine as ope_mod
ope_mod.DEBUG_SDEA_PHYSICS = False

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
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners

# ─────────────────────────────────────────────────────────────────────
# TASK XIV SUPERSESSION GUARD (2026-08-27)
# ─────────────────────────────────────────────────────────────────────
# The legacy Stage-3E BC-shadow contract (target_source='tes_drive_shadow',
# whole line chases the driven carrier) is INTENTIONALLY REPLACED by the
# Task XIV pre-tackle attempt authority (stable tackle-phase anchors +
# freeze/slow contracts + deterministic per-side COMMIT).  When
# DefenceTuners.pretackle_attempt_enabled is True (the LIVE / default
# state), the assertions in THIS verifier test a contract that no longer
# runs, so the verifier reports SUPERSEDED and exits 0 rather than failing
# on behaviour that is now correctly displaced.
if DefenceTuners().pretackle_attempt_enabled:
    print("=" * 72)
    print("TES-DRIVE BC-SHADOW VERIFICATION (2026-08-16)")
    print("=" * 72)
    print("RESULT: SUPERSEDED")
    print("  - Legacy Stage-3E BC-shadow contract replaced by Task XIV")
    print("    pre-tackle attempt authority (DefenceTuners.")
    print("    pretackle_attempt_enabled=True).")
    print("  - The defensive line now settles to stable tackle-phase")
    print("    anchors instead of chasing the driven carrier; this")
    print("    verifier's assertions no longer reflect the live contract.")
    sys.exit(0)

MARKER_ROLES = frozenset({"MARKER_A", "MARKER_B", "DUMMY_HALF"})
VALID_DEPTH_ROLES = frozenset({"A", "B", "C", "D", "CENTRE", "WINGER"})


def role_name(role) -> str:
    """Render a DefenderRole value as a plain string for dict keys."""
    return getattr(role, "value", None) or str(role)


collision_system = CollisionSystem()
field = Field(1920, 1080)
world = WorldState()
world.ruck_established = True  # STAGE 3B: post-TACKLE_INIT band/role scenario

ball = Ball(field)
world.ball = ball

open_play = OpenPlayEngine(world)
world.open_play = open_play
world.open_play_engine = open_play

kickoff_engine = KickoffEngine(world, ball, field)


def reset_ball_and_world():
    kickoff_engine.reset_for_kickoff()


def kickoff():
    kickoff_engine.kickoff()


reset_ball_and_world()
apply_receiving_kickoff_formation(world, field)
apply_kicking_team_formation(world, field)
world.ball_owner = "NONE"
world.ball_carrier = None
world.ball.possessed_by = None
kickoff()

MAX_FRAMES = 1200
dt = 1.0 / 60.0

DEPTH_ROLES = {"A", "B", "C", "D", "CENTRE", "WINGER"}

# All depth-role keys present in the engine's table.
offsets = DefenceTuners().WIDTH_OFFSETS

drive_start_bc_x = None
drive_end_bc_x = None
drive_frames = []
shadow_failures = []
writer_seen = False
failures = []
drive_reached = False


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
        open_play.update(dt)
        if not world.tes_active:
            collision_system.resolve_all(world.players, world=world)

        cur = open_play._phase
        if cur != "TACKLE_DRIVE":
            if drive_reached:
                # Drive ended (DOMINANCE/RELEASE/abort). Stop sampling.
                break
            continue

        drive_reached = True
        bc = world.ball_carrier
        if bc is None:
            continue
        if drive_start_bc_x is None:
            drive_start_bc_x = bc.x
        drive_end_bc_x = bc.x

        # Mirror the engine's EXACT TES registry (world/open_play_engine.py
        # _update_defence_during_tes_drive): primary + joiners + attach-slot
        # map + tes_ctx.attached + tes_ctx.attached_ids.  A defender in any of
        # these is TES-owned (p.update(dt)), NOT soft-follow shadowed; missing
        # a registry means the verifier measures TES physics as a "failed"
        # shadow closure.
        tes_ids = set()
        if open_play.primary is not None:
            tes_ids.add(open_play.primary.player_id)
        for j in open_play.joiners:
            tes_ids.add(j.player_id)
        for _slot_pid in getattr(open_play, "_attached_player_slots", {}):
            tes_ids.add(_slot_pid)
        _ctx = getattr(open_play, "tes_ctx", None)
        if _ctx is not None:
            _ca = getattr(_ctx, "attached", None)
            if _ca:
                for _cid in _ca:
                    tes_ids.add(_cid)
            _cai = getattr(_ctx, "attached_ids", None)
            if _cai:
                for _cid in _cai:
                    tes_ids.add(_cid)

        drive_frames.append({
            "frame": frame,
            "bc_x": bc.x,
            "bc_vx": getattr(bc, "vx", 0.0),
            "bc_vy": getattr(bc, "vy", 0.0),
            "defenders": {
                d.player_id: {
                    "x": d.x,
                    "y": getattr(d, "y", 0.0),
                    "vx": getattr(d, "vx", 0.0),
                    "vy": getattr(d, "vy", 0.0),
                    "target_x": getattr(d, "target_x", None),
                    "target_y": getattr(d, "target_y", None),
                    "depth_role": getattr(d, "depth_role", None),
                    "depth_side": getattr(d, "depth_side", None),
                    "target_source": getattr(d, "target_source", None),
                    "defender_role": role_name(getattr(d, "defender_role", None)),
                    "fullback_role_active": getattr(d, "fullback_role_active", False),
                    "_tackle_commit_active": getattr(d, "_tackle_commit_active", False),
                    "frozen": getattr(d, "frozen", False),
                    "in_tes": d.player_id in tes_ids,
                }
                for d in world.defending_team
            },
        })

if not drive_reached:
    print("FAIL: no TACKLE_DRIVE reached within", MAX_FRAMES, "frames")
    pygame.quit()
    sys.exit(1)

# ── ASSERT B: BC must have moved forward during the drive ──
bc_delta = 0.0
if drive_start_bc_x is not None and drive_end_bc_x is not None:
    bc_delta = drive_end_bc_x - drive_start_bc_x


def eligible_shadow(defender):
    """True when the module-2 writer SHOULD have shadowed this defender."""
    if defender["fullback_role_active"]:
        return False
    if defender["_tackle_commit_active"]:
        return False
    if defender["frozen"]:
        return False
    if defender["in_tes"]:
        return False
    role = defender["depth_role"]
    if role is None or role not in DEPTH_ROLES:
        return False
    return True


# ── ASSERT C/D/E/F: shadow behaviour on every sampled drive frame ──
first_shadowed = None
first_seen_role_x = {}
last_seen_role_x = {}
first_seen_pos_x = {}
last_seen_pos_x = {}
prev_eligible = {}
adj_bc_delta = {}
adj_pos_delta = {}
adj_tgt_delta = {}
for idx, snap in enumerate(drive_frames):
    bc_x = snap["bc_x"]
    for pid, d in snap["defenders"].items():
        if eligible_shadow(d):
            if d["target_source"] != "tes_drive_shadow":
                failures.append(
                    "ASSERT-D: pid %s depth_role=%s target_source=%r during "
                    "drive (expected 'tes_drive_shadow')" % (
                        pid, d["depth_role"], d["target_source"]))
            writer_seen = True
            expected = bc_x + offsets.get(d["depth_role"], 5.0)
            if abs(d["target_x"] - expected) > 0.01:
                failures.append(
                    "ASSERT-C: pid %s role=%s target_x=%.3f expected %.3f "
                    "(bc.x=%.3f + offset %s) frame %s" % (
                        pid, d["depth_role"], d["target_x"], expected,
                        bc_x, offsets.get(d["depth_role"], "?"), snap["frame"]))
            if first_shadowed is None:
                first_shadowed = pid
        elif (
            d["depth_role"] in DEPTH_ROLES
            and d["defender_role"] not in MARKER_ROLES
        ):
            # Excluded (FB/commit/frozen/TES) must NOT carry the shadow tag.
            if d["target_source"] == "tes_drive_shadow":
                failures.append(
                    "ASSERT-E: pid %s was shadowed but is excluded "
                    "(%s role=%s depth_role=%s tes=%s frozen=%s)" % (
                        pid, d["defender_role"], d["target_source"],
                        d["depth_role"], d["in_tes"], d["frozen"]))
        if eligible_shadow(d):
            if pid not in first_seen_role_x:
                first_seen_role_x[pid] = d["target_x"]
            last_seen_role_x[pid] = d["target_x"]
            if pid not in first_seen_pos_x:
                first_seen_pos_x[pid] = d["x"]
            last_seen_pos_x[pid] = d["x"]
            # ADJACENT-ELIGIBLE METRICS (2026-08-16): eligibility is
            # intermittent (a defender can join the tackle mid-drive, be
            # stripped, then become eligible again).  B2/B3 must ONLY measure
            # movement across pairs of CONSECUTIVE frames where the pid was
            # eligible, and compare against the BC advance over exactly those
            # frames -- otherwise TES-physics frames (when the defender is a
            # joiner) and the global drive delta contaminate the ratio with
            # non-shadow behaviour.
            prev_bc = prev_eligible.get(pid)
            prev_eligible[pid] = (snap["bc_x"], d["x"], d["target_x"])
            if prev_bc is not None:
                bc_step = snap["bc_x"] - prev_bc[0]
                pos_step = d["x"] - prev_bc[1]
                tgt_step = d["target_x"] - prev_bc[2]
                if d["target_x"] is not None and prev_bc[2] is not None:
                    adj_bc_delta[pid] = adj_bc_delta.get(pid, 0.0) + bc_step
                    adj_pos_delta[pid] = adj_pos_delta.get(pid, 0.0) + pos_step
                    adj_tgt_delta[pid] = adj_tgt_delta.get(pid, 0.0) + tgt_step
        else:
            # Not eligible this frame: break the consecutive-eligible chain so
            # the next eligible frame cannot pair across the ineligible gap.
            prev_eligible.pop(pid, None)

if not writer_seen:
    failures.append(
        "ASSERT-D2: no eligible depth-role defender ever received "
        "target_source='tes_drive_shadow' during the drive")

# ── ASSERT F: markers / DH never shadowed ──
for snap in drive_frames:
    for pid, d in snap["defenders"].items():
        if d["defender_role"] in MARKER_ROLES and d["target_source"] == "tes_drive_shadow":
            failures.append(
                "ASSERT-F: marker/DH pid %s (role %s) was shadowed"
                % (pid, d["defender_role"]))

# ══════════════════════════════════════════════════════════════════
# STAGE 3E TIMING VERIFIERS (2026-08-25)
#
#   ASSERT-G  VELOCITY-CAP COMPLIANCE — on every sampled frame where a
#             defender was shadowed, |v| must not exceed the unified
#             defensive ceiling.  Validates the soft-follow speed clamp
#             survives the Stage 3E EMA smoothing (the clamp is applied
#             to the raw proportional term; alpha ∈ (0, 1] only shrinks
#             the smoothed result toward the previous capped value, so it
#             can never exceed the ceiling).
#   ASSERT-H  FOLLOW CONTINUITY ACROSS ELIGIBILITY TOGGLES — when a
#             defender's shadow state flips between two CONSECUTIVE
#             sampled frames, the velocity jump must stay bounded.  With
#             EMA alpha = 0.6 the worst-case single-frame delta is
#             0.6 × (desired − prev); the desired term is wall-capped at
#             the ceiling so |Δv| ≤ 0.6 × 2 × ceiling ≈ 10 m/s.
#             A generous 14 m/s bound catches un-smoothed spikes without
#             false-failing legitimate pursuit accelerations.
#   ASSERT-I  STATIC-CARRIER REST — a shadowed defender whose X-error to
#             its role-depth target stays inside the 5 cm deadband for TWO
#             CONSECUTIVE frames while the ball carrier itself is near-still
#             must rest its X velocity at zero.  Y is deliberately excluded:
#             the ruck-adjacent pin is the last Y writer of the frame and
#             snaps side-first defenders onto ruck_y ± offset, so err_y=0 is
#             an externally-pinned signal, not a follow rest.  The
#             consecutive framing also lets the single frame on which a
#             follow just crossed into the band legitimately carry residual
#             velocity.
# ══════════════════════════════════════════════════════════════════
_DRIVE_CEILING = float(DefenceTuners().max_defensive_speed_mps)
_DRIVE_DEADBAND_M = 0.05          # mirrors defence_manager follow deadband
_DRIVE_STATIC_CARRIER_MPS = 0.8   # mirrors defence_manager static-carrier gate
# Ceiling-relative continuity bound: the worst-case single-frame delta is
# |prev| + |cur| ≤ ceiling + 0.6·ceiling = 1.6·ceiling for an EMA alpha of
# 0.6; a 2.5× ceiling bound covers player-max TES physics with headroom while
# still failing an un-smoothed raw discontinuity (which would be multi-ceiling).
_DRIVE_CONTINUITY_MAX_DV_MPS = 2.5 * _DRIVE_CEILING

# ASSERT-G: velocity-cap compliance on shadowed frames
for snap in drive_frames:
    for pid, d in snap["defenders"].items():
        if d["target_source"] != "tes_drive_shadow":
            continue
        v_mag = math.hypot(d["vx"], d["vy"])
        if v_mag > _DRIVE_CEILING + 1e-6:
            failures.append(
                "ASSERT-G: pid %s shadowed frame %s |v|=%.3f exceeds "
                "ceiling %.3f" % (pid, snap["frame"], v_mag, _DRIVE_CEILING))

# ASSERT-H: velocity continuity across shadow-state toggles
# between consecutive sampled frames
for i in range(1, len(drive_frames)):
    prev_snap = drive_frames[i - 1]
    cur_snap = drive_frames[i]
    for pid, cur in cur_snap["defenders"].items():
        prev = prev_snap["defenders"].get(pid)
        if prev is None:
            continue
        prev_shadowed = prev["target_source"] == "tes_drive_shadow"
        cur_shadowed = cur["target_source"] == "tes_drive_shadow"
        if prev_shadowed == cur_shadowed:
            continue
        dvx = cur["vx"] - prev["vx"]
        dvy = cur["vy"] - prev["vy"]
        dv_mag = math.hypot(dvx, dvy)
        if dv_mag > _DRIVE_CONTINUITY_MAX_DV_MPS:
            failures.append(
                "ASSERT-H: pid %s shadow-state flipped %s->%s at frame %s "
                "with |dv|=%.3f m/s (exceeds %.1f smoothing bound)" % (
                    pid, prev_shadowed, cur_shadowed, cur_snap["frame"],
                    dv_mag, _DRIVE_CONTINUITY_MAX_DV_MPS))

# ASSERT-I: static-carrier rest (X axis, consecutive-in-band)
_prev_in_band_x = {}   # pid -> bool (prev snapshot in-band on X)
_prev_bc_vx = {}       # pid -> bc.vx at prev snapshot
for snap in drive_frames:
    bc_vx_i = snap.get("bc_vx", 0.0)
    for pid, d in snap["defenders"].items():
        if d["target_source"] != "tes_drive_shadow":
            _prev_in_band_x.pop(pid, None)
            _prev_bc_vx.pop(pid, None)
            continue
        if d["target_x"] is None:
            continue
        err_x_i = abs(d["target_x"] - d["x"])
        in_band_i = err_x_i < _DRIVE_DEADBAND_M
        prev_in_band_i = _prev_in_band_x.get(pid, False)
        prev_bc_vx_i = _prev_bc_vx.get(pid, bc_vx_i)
        carrier_static_i = (abs(bc_vx_i) < _DRIVE_STATIC_CARRIER_MPS
                            and abs(prev_bc_vx_i) < _DRIVE_STATIC_CARRIER_MPS)
        if prev_in_band_i and in_band_i and carrier_static_i:
            if abs(d["vx"]) > 1e-9:
                failures.append(
                    "ASSERT-I: pid %s frame %s X settled in-band for 2 "
                    "consecutive static-carrier frames but vx=%.6f != 0" % (
                        pid, snap["frame"], d["vx"]))
        _prev_in_band_x[pid] = in_band_i
        _prev_bc_vx[pid] = bc_vx_i

# ── ASSERT B2: shadowed targets must track the BC drive ──
# Measure each eligible defender's target_x motion only across CONSECUTIVE
# eligible frames (adj_tgt_delta), and compare it against the BC advance over
# the SAME frames (adj_bc_delta).  This avoids contamination from frames
# where the defender was a TES joiner (its target was owned by TES physics).
if adj_tgt_delta:
    for pid, tgt_delta in adj_tgt_delta.items():
        bc_adj = adj_bc_delta.get(pid, 0.0)
        if bc_adj <= 0.3:
            continue
        if tgt_delta < bc_adj * 0.5 - 0.05:
            failures.append(
                "ASSERT-B2: pid %s line target advanced %.3f while BC "
                "advanced %.3f over eligible frames (must track >= 50%%)" % (
                    pid, tgt_delta, bc_adj))
else:
    print("NOTE: no adjacent-eligible frame pairs to measure B2 (single-frame "
          "eligible windows) - skipping")

# ── ASSERT B3: actual position must converge onto the role-depth target ──
# The soft-follow (vx/vy proportional) drives ACTUAL x toward the shadowed
# target (bc.x + role offset).  The correct observable is GAP CLOSURE, not a
# raw positive delta: a defender who starts AHEAD of its depth line (e.g. a
# commit-tackler fallback or in-front line position) must be pulled BACKWARD
# into bc.x + offset, while one who starts behind is carried forward.  A
# raw "x advanced" ratio would (incorrectly) fail the backward-converging
# defenders.
#
# Two assertions:
#   B3a (hard): a meaningful starting gap must NEVER grow materially (a
#       growing gap proves the follow is not the last integrator, or its
#       direction was inverted).  Net growth >= 0.30 m is a hard failure.
#   B3b (soft): a meaningful starting gap should CLOSE by at least 0.25 m
#       over the eligible windows when the BC advanced enough to matter.
#       (No ratio-to-bc_adj: a defender starting only ~0.9 m off its depth
#       line can only close ~0.9 m total; requiring 30% of a 3.5 m BC drive
#       for every defender is physically wrong.  0.25 m of real closure is
#       unambiguous visual proof the follow acts.)
if first_seen_pos_x:
    for pid, x_first in first_seen_pos_x.items():
        x_last = last_seen_pos_x.get(pid)
        t_first = first_seen_role_x.get(pid)
        t_last = last_seen_role_x.get(pid)
        bc_adj = adj_bc_delta.get(pid, 0.0)
        if x_last is None or t_first is None or t_last is None:
            continue
        if bc_adj <= 0.3:
            continue
        gap_first = abs(x_first - (t_first or x_first))
        gap_last = abs(x_last - (t_last or x_last))
        if gap_first < 0.15:
            # Already effectively on/near target at first eligible frame:
            # there is nothing meaningful to close; position tracking is
            # already proven by B2 (target motion tracks the drive).
            continue
        closure = gap_first - gap_last
        if gap_last - gap_first > 0.30:
            failures.append(
                "ASSERT-B3a: pid %s gap |x-target| GREW %.3f -> %.3f over "
                "eligible frames while BC advanced %.3f (soft-follow must "
                "never push the line away from its depth target)" % (
                    pid, gap_first, gap_last, bc_adj))
        elif closure < 0.25 and gap_last > 0.20:
            failures.append(
                "ASSERT-B3b: pid %s gap |x-target| %.3f -> %.3f (closed "
                "%.3f) while BC advanced %.3f over eligible frames "
                "(soft-follow must visibly close the line onto its depth "
                "target)" % (pid, gap_first, gap_last, closure, bc_adj))
else:
    print("NOTE: no eligible frames to measure B3 (single-frame "
          "eligible windows) - skipping")

print("=" * 72)
print("TES-DRIVE BC-SHADOW VERIFICATION (2026-08-16)")
print("=" * 72)
print("Drive sampled:", len(drive_frames), "frames")
print("BC X: %.3f -> %.3f (delta %.3f m)" % (
    drive_start_bc_x or 0.0, drive_end_bc_x or 0.0, bc_delta))
print("Writer fired on eligible defenders:", writer_seen)
if first_shadowed is not None:
    print("First shadowed pid:", first_shadowed)
print("\n-- sampled frame detail (first + last) --")
for which in (0, len(drive_frames) - 1):
    if which >= len(drive_frames):
        continue
    snap = drive_frames[which]
    print("\nframe", snap["frame"], "bc.x=%.3f" % snap["bc_x"])
    for pid, d in sorted(snap["defenders"].items()):
        print("  pid %-3s role=%-8s depth=%-7s src=%-18s tx=%.3f"
              % (pid, d["defender_role"], d["depth_role"],
                 d["target_source"], d["target_x"] if d["target_x"] is not None else -1.0))

print("\n" + "=" * 72)
if failures:
    print("RESULT: FAIL")
    for f in failures:
        print("  -", f)
    pygame.quit()
    sys.exit(1)

print("RESULT: PASS")
if bc_delta > 0.3:
    print("  - The defensive line shadowed the BC drive: eligible defenders")
    print("    moved with the carrier at their role-depth X offset.")
else:
    print("  - Shadow writer fired correctly (drive delta small: %.2f m)." % bc_delta)
print("  - Fullback / committed tacklers / markers / DH excluded.")
pygame.quit()
sys.exit(0)
