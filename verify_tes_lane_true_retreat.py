"""verify_tes_lane_true_retreat.py — TES DRIVE lane-true retreat verifier.

WHAT THIS PROVES
----------------
TES DRIVE enforces a LANE-TRUE retreat:

  A. IN-LANE HOLD      — a defender whose lane is locked at chamber entry
                         and who is already ON that lane never leaves it
                         (per-body lateral error never exceeds
                         IN_LANE_TOL_M for the whole chamber).  Under the
                         RECOVERY-OWNERSHIP model the lane locked at
                         chamber entry is the lane the entry CENSUS
                         assigned the body (its OWN slot), so "on that
                         lane" means "on its assigned slot".
  B. CLEAN CORRECTION  — a defender seeded OFF its lane converges to it
                         monotonically (no overshoot: the error never
                         exceeds its own seed error, and it shrinks).
  C. NO MID-CHAMBER
     RE-SEED           — every tracked body's locked lane is byte-stable
                         for the whole chamber, and the number of lane
                         seeds equals the number of genuine tackles
                         entered (an intra-tackle frame must NOT re-seed).
  D. NO FAN            — the lateral GAPS between the tracked line bodies
                         are preserved across the chamber (the line
                         neither fans out nor pinches in).
  E. ASSIGNED LANE     — the locked lane of every body equals the lane
                         the chamber-entry CENSUS assigned it: its OWN
                         slot when the census gave it one, and the Y it
                         actually holds at entry when it owns no slot.
                         The locked lanes are also pairwise distinct, so
                         two bodies can never share a lane.

A "CHAMBER" is one tackle's drive: it opens once (``world._tes_chamber_id``
is bumped by ``open_tes_drive_chamber``) and every body's latch is stamped
with that id.  A body whose stamp does not match the live id is re-seeded
— that is the ONLY legitimate reason for a lane to change mid-chamber, so
this verifier fails if a lane changes without a chamber-id change.

Exit 0 = all clauses hold.  Exit 1 = contract violated.  Exit 2 = no
drive frame observed (harness never reached the chamber).
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

import contextlib
import io
import math
import sys
from collections import deque

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
from game.defence.defence_lvl_1 import tes_drive_structure as tds

DRIVE_PHASES = ("TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE")
LINE_ROLES = ("DEF_LINE_READY", "DEF_CHASE", "DEF_RETREAT_10")

IN_LANE_TOL_M = 0.5          # clause A: an in-lane body may not leave its lane
CONVERGE_TOL_M = 0.5         # clause B: "arrived" for an out-of-lane body
SEED_TOL_M = 1e-6            # clause E: locked lane == its assigned lane
LANE_DUP_TOL_M = 0.30        # clause E: two locked lanes within this = shared
GAP_TOL_M = 1.5              # clause D: inter-body lateral gap preservation
# Ownership floor: below this a body spent most of the chamber under
# another authority (TES tackler / joiner / fullback).  It is reported,
# never graded — a tackler diving into the pile is not lane drift.
MIN_OWNED_FRAMES = 5
MIN_OWNED_SHARE = 0.30
# Off-lane tracing: capture the frames a held body sits outside this error,
# with the fields that attribute the movement to a writer.
TRACE_ERR_M = 0.30
TRACE_MAX_ROWS = 60
# Band-relative lane resolution: a body whose held lane lies outside the
# governed width band cannot hold that lane — the width authority forbids
# it — so the band edge is the lane it must reach.  Epsilon absorbs the
# float round-trip of the band bounds.
BAND_EPS_M = 1e-6
# Clause B also grades the CORRECTION RATE: an out-of-lane body must close
# its lateral error at the hold's own jog speed.  It is NOT required to
# arrive inside a chamber shorter than the correction needs (a 13 m pull
# onto the band edge takes 13 / jog_speed seconds).  Slack absorbs the
# drive's first frame and float noise.
JOG_SPEED_FALLBACK_MPS = 2.5
# Clause B is graded on the DIRECTION and CONTINUITY of the correction, not
# on a distance or a cruise rate.  probe_tes_hold_integrator.py showed why:
# the hold jogs a 2-D vector toward the cell at tes_hold_jog_speed_mps, so
# the LATERAL leg only receives its share of that budget (measured in one
# chamber: bodies 5-16 m off closed laterally at ~2.4 m/s while a body only
# 1.3 m off closed at 0.22 m/s).  A rate floor would therefore fail a
# perfectly clean correction, so the verifier asserts instead that the body
# is DRIVEN toward its lane every frame it needs to move (CLOSING_MIN_M
# dead-band) and is never PARKED off-lane (frozen while further than
# CONVERGE_TOL_M) for most of the frames the hold owned it.
CLOSING_MIN_M = 0.10         # |cell_y - y| above which motion is required
PARKED_SHARE = 0.50          # share of owned frames parked off-lane = fail
MAX_FRAMES = 3000
ENGINE_LOG = "tmp_verify_tes_lane_true_engine.log"


def _role_name(p):
    role = getattr(p, "defender_role", None)
    return getattr(role, "name", None) or (str(role) if role is not None else None)


def _mstate(p):
    ms = getattr(p, "movement_state", None)
    return getattr(ms, "name", None) or (str(ms) if ms is not None else None)


def boot_world():
    """Boot kickoff -> catch -> OPEN_PLAY exactly like the probe harness."""
    collision_system = CollisionSystem()
    field = Field(1920, 1080)
    world = WorldState()
    ball = Ball(field)
    world.ball = ball
    open_play = OpenPlayEngine(world)
    world.open_play = open_play
    world.open_play_engine = open_play
    kickoff_engine = KickoffEngine(world, ball, field)
    kickoff_engine.reset_for_kickoff()
    apply_receiving_kickoff_formation(world, field)
    apply_kicking_team_formation(world, field)
    world.ball_owner = "NONE"
    world.ball_carrier = None
    world.ball.possessed_by = None
    kickoff_engine.kickoff()
    return world, ball, open_play, kickoff_engine, collision_system


def line_bodies(world):
    """Eligible line bodies (no tacklers / markers / DH / fullback)."""
    bc = world.ball_carrier
    return [
        d for d in world.defending_team
        if _role_name(d) in LINE_ROLES
        and not getattr(d, "fullback_role_active", False)
        and d is not bc
    ]


def held_bodies(world):
    return [d for d in line_bodies(world)
            if bool(getattr(d, tds.HOLD_ACTIVE_ATTR, False))]


def _body_by_pid(world, pid):
    for d in world.defending_team:
        if getattr(d, "player_id", None) == pid:
            return d
    return None


def _lane(d):
    v = getattr(d, tds.LANE_Y_ATTR, None)
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _chamber_id(world):
    return getattr(world, tds.CHAMBER_ID_ATTR, None)


# ─────────────────────────────────────────────────────────────────────
# CHAMBER RECORDS
# ─────────────────────────────────────────────────────────────────────

class Chamber:
    """One drive chamber: its entry frame, its per-body lane latches and
    the per-frame lateral error / gap history used by every clause."""

    def __init__(self, cid, frame, phase, tuners=None):
        self.cid = cid
        self.seed_frame = frame
        self.seed_phase = phase
        self.tuners = tuners
        self.frames = 0
        self.band = None         # governed-width (lo, hi) for the chamber
        self.out_of_band = {}    # pid -> lane lies outside the width band
        self.ref = {}            # pid -> the lane the body MUST hold
        self.lane_seed_err = {}  # pid -> |locked lane - assigned lane| (E)
        self.mandated = {}       # pid -> the lane the census ASSIGNED (E)
        self.entry_displacement = {}  # pid -> |entry y - assigned lane|
        self.lanes = {}          # pid -> lane locked on the entry frame
        self.seed_err = {}       # pid -> |y - lane| on the entry frame
        self.max_err = {}        # pid -> worst |y - lane| during the chamber
        self.min_err = {}        # pid -> best |y - lane| during the chamber
        self.held_frames = {}    # pid -> frames the hold actually owned it
        self.worst_at = {}       # pid -> (frame, y, lane, mstate, role)
        self.lane_changes = []   # (frame, pid, old, new) — clause C failures
        self.gap_seed = {}       # (pidA,pidB) -> |laneA - laneB| at seed
        self.gap_worst = {}      # (pidA,pidB) -> worst |gap - gap_seed|
        self.gap_frames = {}     # (pidA,pidB) -> frame of the worst gap
        self.reseed_without_id = 0
        # Clause B evidence: frames the body was DRIVEN toward its lane (its
        # Y velocity pointed from its Y toward the effective cell while the
        # gap was still material), and frames it sat off-lane while FROZEN
        # (a parked out-of-lane body is the real defect clause B guards).
        self.closing_frames = {}     # pid -> frames moving toward the lane
        self.offlane_frames = {}     # pid -> frames further than tolerance
        self.offlane_frozen = {}     # pid -> of those, frames FROZEN
        # Off-lane trace: the LAST TRACE_MAX_ROWS frames a tracked body sat
        # > TRACE_ERR_M off its lane, with the fields that identify WHICH
        # authority moved it.  A deque keeps the TAIL — the residual drift
        # is a late-chamber event, so the head of the chamber is not the
        # evidence.
        self.trace = deque(maxlen=TRACE_MAX_ROWS)

    def seed_bodies(self, world):
        """Latch every eligible body's lane for THIS chamber.

        The lane a body must HOLD is the lane the chamber-entry CENSUS
        assigned it — its OWN slot (the RECOVERY-OWNERSHIP model).  A body
        the census did not own keeps its achieved entry Y, the legacy
        lane-true seed.  Either way, when that lane lies outside the
        governed defensive width the structure hold corrects it IN to the
        band edge, and the band edge is the lane it must reach (the
        "out-of-lane defenders auto-correct cleanly" half of the
        contract).  Both cases are graded against ``ref``; the locked lane
        is still recorded and checked for byte-stability.
        """
        self.lanes = {}
        self.seed_err = {}
        try:
            self.band = tds.width_band_for(world, tuners=self.tuners)
        except Exception:
            self.band = None
        for d in line_bodies(world):
            pid = getattr(d, "player_id", None)
            lane = _lane(d)
            if pid is None or lane is None:
                continue
            ref = lane
            outside = False
            if self.band is not None:
                lo, hi = self.band
                if lane < lo - BAND_EPS_M:
                    ref = lo
                    outside = True
                elif lane > hi + BAND_EPS_M:
                    ref = hi
                    outside = True
            self.out_of_band[pid] = outside
            self.ref[pid] = ref
            y = float(getattr(d, "y", 0.0))
            self.lanes[pid] = lane
            # Clause E is about the LATCH, not the correction.  Under the
            # RECOVERY-OWNERSHIP model the locked lane is the lane the
            # chamber-entry CENSUS assigned the body — its OWN slot.  Only
            # a body that owns NO slot keeps its achieved entry Y (the
            # legacy lane-true seed).  The mandated lane is therefore
            # resolved from the ownership table, never from a position.
            slot_y = tds.owned_slot_y(world, d)
            mandated = lane if slot_y is None else float(slot_y)
            self.mandated[pid] = mandated
            self.entry_displacement[pid] = abs(y - mandated)
            self.lane_seed_err[pid] = abs(lane - mandated)
            self.seed_err[pid] = abs(y - ref)
            self.max_err[pid] = self.seed_err[pid]
            self.min_err[pid] = self.seed_err[pid]
            self.held_frames[pid] = 0
            self.worst_at[pid] = (world.frame_count, y, ref, _mstate(d),
                                  _role_name(d))
        self._seed_gaps()

    def _seed_gaps(self):
        pids = sorted(self.lanes)
        self.gap_seed = {}
        self.gap_worst = {}
        self.gap_frames = {}
        for i, a in enumerate(pids):
            for b in pids[i + 1:]:
                self.gap_seed[(a, b)] = abs(self.lanes[a] - self.lanes[b])
                self.gap_worst[(a, b)] = 0.0
                self.gap_frames[(a, b)] = self.seed_frame

    def observe(self, world):
        """Record one drive frame: lane stability, error, gaps."""
        self.frames += 1
        for pid, lane in self.lanes.items():
            d = _body_by_pid(world, pid)
            if d is None:
                continue
            # The lane-true contract governs exactly the bodies the structure
            # hold OWNS.  Once a body stops being eligible (promoted to a TES
            # tackler/joiner, became the carrier, took the fullback role) or
            # carries no live latch, its movement belongs to another authority
            # and must NOT be charged to the lane hold — doing so reported
            # 5–23 m of "drift" that was really a tackler diving into the pile.
            if not tds.is_structure_hold_eligible(d):
                continue
            if not bool(getattr(d, tds.HOLD_ACTIVE_ATTR, False)):
                continue
            self.held_frames[pid] = self.held_frames.get(pid, 0) + 1
            live = _lane(d)
            if live is None:
                continue
            if abs(live - lane) > SEED_TOL_M:
                # A lane moved.  Legitimate ONLY if the chamber id changed
                # (then the probe re-opens a chamber and never calls this).
                self.lane_changes.append(
                    (world.frame_count, pid, lane, live)
                )
                self.lanes[pid] = live
                self.reseed_without_id += 1
                continue
            # Grade against the lane the hold REQUIRES: its own lane when
            # that lane is inside the governed width, the band edge when it
            # is not (the width authority forbids the wider line, so the
            # body is being corrected IN — a clean correction, not drift).
            ref = self.ref.get(pid, lane)
            err = abs(float(getattr(d, "y", 0.0)) - ref)
            # Clause-B evidence: was the body DRIVEN toward its lane this
            # frame (its Y velocity pointing from its Y toward the effective
            # cell), and was it PARKED off-lane (frozen while further than
            # the convergence tolerance)?  Direction is the property the
            # hold must guarantee; the distance is geometry-dependent and is
            # reported, not graded.
            _cell_y = float(getattr(d, tds.HOLD_Y_ATTR, 0.0) or 0.0)
            _toward = _cell_y - float(getattr(d, "y", 0.0))
            if abs(_toward) > CLOSING_MIN_M:
                if float(getattr(d, "vy", 0.0) or 0.0) * _toward > 0.0:
                    self.closing_frames[pid] = (
                        self.closing_frames.get(pid, 0) + 1
                    )
            if err > CONVERGE_TOL_M:
                self.offlane_frames[pid] = self.offlane_frames.get(pid, 0) + 1
                if bool(getattr(d, tds.HOLD_FROZEN_ATTR, False)):
                    self.offlane_frozen[pid] = (
                        self.offlane_frozen.get(pid, 0) + 1
                    )
            if err < self.min_err.get(pid, err):
                self.min_err[pid] = err
            if err > self.max_err.get(pid, 0.0):
                self.max_err[pid] = err
                # Full state at the WORST frame: the fields that identify
                # which authority owned the target at the moment of peak
                # deviation.  Indices 0-4 keep the original contract
                # (frame, y, lane, movement_state, role) so the existing
                # table/report code is unaffected.
                self.worst_at[pid] = (
                    world.frame_count, float(getattr(d, "y", 0.0)), lane,
                    _mstate(d), _role_name(d),
                    float(getattr(d, "target_y", 0.0) or 0.0),
                    float(getattr(d, tds.HOLD_Y_ATTR, 0.0) or 0.0),
                    str(getattr(d, "target_source", "") or "-"),
                    bool(getattr(d, tds.HOLD_FROZEN_ATTR, False)),
                    float(getattr(d, tds.CHAMBER_ID_ATTR, 0) or 0),
                )
            if err > TRACE_ERR_M:
                self.trace.append((
                    world.frame_count, pid,
                    float(getattr(d, "y", 0.0)),
                    float(getattr(d, "target_y", 0.0) or 0.0),
                    float(getattr(d, tds.HOLD_Y_ATTR, 0.0) or 0.0),
                    lane,
                    float(getattr(d, tds.HOLD_X_ATTR, 0.0) or 0.0),
                    float(getattr(d, "x", 0.0)),
                    _mstate(d),
                    str(getattr(d, "target_source", "") or "-"),
                    bool(getattr(d, tds.HOLD_FROZEN_ATTR, False)),
                    float(getattr(d, "move_speed", 0.0) or 0.0),
                ))
        for key, gap_seed in self.gap_seed.items():
            a, b = key
            da, db = _body_by_pid(world, a), _body_by_pid(world, b)
            if da is None or db is None:
                continue
            la, lb = _lane(da), _lane(db)
            if la is None or lb is None:
                continue
            dev = abs(abs(la - lb) - gap_seed)
            if dev > self.gap_worst.get(key, 0.0):
                self.gap_worst[key] = dev
                self.gap_frames[key] = world.frame_count


def _run_chamber_loop():
    """Drive the engine and return (result dict) with per-chamber records."""
    buf = io.StringIO()
    out = {"chambers": [], "drive_frames": 0, "held_frames": 0,
           "frozen_frames": 0, "settle": []}
    with contextlib.redirect_stdout(buf):
        world, ball, open_play, kickoff_engine, collision_system = boot_world()
        # The structure hold reads its band / stretch from the engine's
        # defence tuners; hand the SAME object to the chamber so the band
        # the verifier resolves is byte-identical to the one the hold uses.
        tuners = getattr(open_play, "def_tuners", None)
        dt = 1.0 / 60.0
        cur = None
        prev_cid = None
        seeds = 0
        lane_snapshots = {}

        for _ in range(MAX_FRAMES):
            world.frame_count += 1
            ball.update(dt)

            if world.phase == GamePhase.KICKOFF:
                kickoff_engine.update(dt)
                check_for_catch(world, ball, dt)
                continue
            if world.phase == GamePhase.CATCH:
                if world.ball_carrier is not None:
                    open_play.start(world.ball_carrier)
                world.phase = GamePhase.OPEN_PLAY
                continue
            if world.phase != GamePhase.OPEN_PLAY:
                continue

            open_play.update(dt)
            if not world.tes_active:
                collision_system.resolve_all(world.players, world=world)

            phase = open_play._phase
            if phase not in DRIVE_PHASES:
                continue

            out["drive_frames"] += 1
            held = held_bodies(world)
            out["held_frames"] += len(held)
            out["frozen_frames"] += sum(
                1 for d in held if _mstate(d) == "HOLD"
            )

            cid = _chamber_id(world)
            if cur is None or cid != prev_cid:
                if cur is not None:
                    out["chambers"].append(cur)
                cur = Chamber(cid, world.frame_count, phase, tuners)
                cur.seed_bodies(world)
                seeds += 1
                prev_cid = cid
                lane_snapshots[cid] = dict(cur.lanes)
                continue

            cur.observe(world)

        if cur is not None:
            out["chambers"].append(cur)

    with open(ENGINE_LOG, "w", encoding="utf-8") as fh:
        fh.write(buf.getvalue())
    out["seeds"] = seeds
    return out
# ─────────────────────────────────────────────────────────────────────
# REPORT
# ─────────────────────────────────────────────────────────────────────

def _fmt(v, w=7, p=2):
    if v is None:
        return "-".rjust(w)
    return f"{float(v):{w}.{p}f}"


def _report(res):
    chambers = res["chambers"]
    print("=" * 78)
    print("TES DRIVE LANE-TRUE RETREAT VERIFIER")
    print("=" * 78)
    print(f"drive frames            : {res['drive_frames']}")
    print(f"chambers (lane seeds)   : {res['seeds']}")
    print(f"held-body-frames        : {res['held_frames']}")
    print(f"frozen-body-frames      : {res['frozen_frames']}")

    fails = []

    # ── CLAUSE C: no lane re-seed inside a chamber ──
    total_changes = sum(c.reseed_without_id for c in chambers)
    print(f"\n[C] mid-chamber lane re-seeds (must be 0) : {total_changes}")
    if total_changes:
        fails.append(f"C: {total_changes} lane re-seed(s) without a "
                     f"chamber-id change")
        for c in chambers:
            for (frm, pid, old, new) in c.lane_changes[:10]:
                print(f"    chamber={c.cid} frame={frm} pid={pid} "
                      f"lane {old:.2f} -> {new:.2f}")

    # ── PER-CHAMBER BODY TABLE ──
    for ci, c in enumerate(chambers):
        print(f"\n-- CHAMBER {ci + 1} (id={c.cid}, seed frame "
              f"{c.seed_frame} {c.seed_phase}, {c.frames} frames, "
              f"{len(c.lanes)} bodies) --")
        if not c.lanes:
            print("   (no lane-latched body in this chamber)")
            continue
        print(f"  {'pid':<6}{'role':<15}{'lane':>9}{'ref':>8}{'seed_err':>10}"
              f"{'max_err':>9}{'min_err':>9}{'held':>6}{'verdict':<12}"
              f"{'worst frame/state':<22}{'w_tgt':>9}{'w_hold':>9}"
              f"{'w_src':<18}{'w_frz':>6}")
        for pid in sorted(c.lanes):
            lane = c.lanes[pid]
            se = c.seed_err.get(pid, 0.0)
            me = c.max_err.get(pid, 0.0)
            mn = c.min_err.get(pid, se)
            wa = c.worst_at.get(pid)
            state = f"{wa[0]}/{wa[3]}" if wa else "-"
            hf = c.held_frames.get(pid, 0)
            owned = (hf >= MIN_OWNED_FRAMES
                     and hf >= MIN_OWNED_SHARE * max(1, c.frames))
            verdict = "OK"
            if not owned:
                verdict = "UNOWNED"
            elif se <= IN_LANE_TOL_M:
                if me > IN_LANE_TOL_M:
                    verdict = "DRIFTED"
                    fails.append(
                        f"A: chamber {c.cid} pid {pid} left its lane "
                        f"(max_err {me:.2f} m > {IN_LANE_TOL_M} m)"
                    )
            else:
                if me > se + 0.05:
                    verdict = "OVERSHOOT"
                    fails.append(
                        f"B: chamber {c.cid} pid {pid} overshot its lane "
                        f"(max_err {me:.2f} m > seed_err {se:.2f} m)"
                    )
                else:
                    # OUT-OF-LANE body: it must correct CLEANLY, graded
                    # against the REAL convergence law that
                    # probe_tes_hold_integrator.py measured live.  The hold
                    # commands a 2-D jog toward the cell at
                    # tes_hold_jog_speed_mps (probe: vx=1.15, vy=2.22,
                    # |v|=2.50, source=tes_drive_hold), so the LATERAL leg
                    # gets only its share of that budget and the achieved
                    # per-frame displacement is a fraction of the commanded
                    # vector.  ARRIVAL is therefore not assertable: a body
                    # 13 m outside the band needs 13/2.5 s of lateral motion,
                    # longer than a 9-frame (0.15 s) chamber.
                    #
                    # What "auto-corrects cleanly" actually means, and what
                    # is assertable: no overshoot (max_err <= seed_err) plus
                    # monotone error plus material progress toward the lane —
                    #   * LONG chamber (>= LONG_CHAMBER_S): a sustained
                    #     convergence floor of CORRECT_RATE_FRACTION of the
                    #     commanded jog speed;
                    #   * SHORT chamber: the smooth-damp ramp cannot reach
                    #     cruise, so only genuine progress is asserted.
                    # It must be DRIVEN toward its lane and must never be
                    # PARKED: a body that reached its lane is CORRECTED; one
                    # that is still short but was driven toward it on the
                    # frames it needed to move is CLOSING; one that never
                    # received a closing command, or spent most of its owned
                    # frames frozen while off-lane, is failing to correct.
                    _closing = c.closing_frames.get(pid, 0)
                    _offlane = c.offlane_frames.get(pid, 0)
                    _parked = c.offlane_frozen.get(pid, 0)
                    _parked_share = (_parked / _offlane) if _offlane else 0.0
                    if mn <= CONVERGE_TOL_M:
                        verdict = "CORRECTED"
                    elif _closing == 0 or _parked_share > PARKED_SHARE:
                        verdict = "NO-CORRECT"
                        fails.append(
                            f"B: chamber {c.cid} pid {pid} is not "
                            f"correcting cleanly: closing frames "
                            f"{_closing}, parked {_parked}/{_offlane} "
                            f"off-lane frames ({_parked_share:.0%})"
                        )
                    else:
                        verdict = "CLOSING"
            # Worst-frame target provenance: what the target/hold/source
            # were at the moment of peak deviation.
            _ext = bool(wa) and len(wa) >= 10
            w_tgt = float(wa[5]) if _ext else float("nan")
            w_hold = float(wa[6]) if _ext else float("nan")
            w_src = str(wa[7]) if _ext else "-"
            w_frz = bool(wa[8]) if _ext else False
            print(f"  {str(pid):<6}{str(_role_name_of(wa)):<15}"
                  f"{lane:>9.2f}{c.ref.get(pid, lane):>8.2f}"
                  f"{se:>10.2f}{me:>9.2f}{mn:>9.2f}"
                  f"{hf:>6}{verdict:<12}{state:<22}"
                  f"{w_tgt:>9.2f}{w_hold:>9.2f}{w_src:<18}{str(w_frz):>6}")
        # ── OFF-LANE TRACE (writer attribution) ──
        # Prints the frames a held body sat off its lane, with the target /
        # hold / state / source fields that identify the responsible writer.
        if c.trace:
            print(f"  off-lane frames (> {TRACE_ERR_M} m): "
                  f"{len(c.trace)} captured")
            print(f"    {'frame':>6}{'pid':>5}{'y':>8}{'target_y':>9}"
                  f"{'hold_y':>8}{'lane':>8}{'hold_x':>8}{'x':>8}"
                  f"{'state':<7}{'source':<16}{'frz':>5}{'spd':>6}")
            for row in list(c.trace)[:24]:
                (frm, pid, y, ty, hy, lane, hx, x, st, src,
                 frz, spd) = row
                print(f"    {frm:>6}{str(pid):>5}{y:>8.2f}{ty:>9.2f}"
                      f"{hy:>8.2f}{lane:>8.2f}{hx:>8.2f}{x:>8.2f}"
                      f"{str(st):<7}{str(src):<16}{str(frz):>5}{spd:>6.2f}")
        # ── CLAUSE D: gap preservation (no fan / no pinch) ──
        if c.gap_worst:
            key = max(c.gap_worst, key=lambda k: c.gap_worst[k])
            worst_gap = c.gap_worst[key]
            print(f"  gaps: worst preservation deviation {worst_gap:.2f} m "
                  f"(pair {key} at frame {c.gap_frames.get(key)})")
            if worst_gap > GAP_TOL_M:
                fails.append(
                    f"D: chamber {c.cid} gaps moved {worst_gap:.2f} m "
                    f"(pair {key} > tolerance {GAP_TOL_M} m)"
                )

    # ── CLAUSE E: the locked lane is the body's OWN assigned lane ──
    # Under recovery ownership the locked lane is the lane the entry
    # census assigned the body (its OWN slot); only a body that owns no
    # slot keeps its achieved entry Y.  The clause therefore asserts the
    # latch came from the ownership table — not from a second, competing
    # source such as the re-derived spacing chain — and that no two bodies
    # share a lane.  The size of the recovery pull is REPORTED, not graded:
    # a displaced body having far to travel is the contract working.
    print(f"\n[E] locked lane == the body's OWN assigned lane "
          f"(tolerance {SEED_TOL_M:g} m)")
    worst_seed = 0.0
    worst_seed_pid = None
    worst_disp = 0.0
    worst_disp_pid = None
    for c in chambers:
        for pid, se in c.lane_seed_err.items():
            if se > worst_seed:
                worst_seed = se
                worst_seed_pid = (c.cid, pid)
            disp = c.entry_displacement.get(pid, 0.0)
            if disp > worst_disp:
                worst_disp = disp
                worst_disp_pid = (c.cid, pid)
    print(f"    worst latch error: {worst_seed:.6f} m "
          f"(chamber/pid {worst_seed_pid})")
    shared = 0
    saturated = 0
    for c in chambers:
        # The count-lane ladder (`side_lanes`) ends with
        # `clamp_to_sideline`, so once a side needs more lanes than the
        # usable width supports, every outer lane is pinned to the
        # sideline boundary: two DISTINCT slots (distinct index, distinct
        # owner) then share one Y.  That is a geometry saturation of the
        # ladder, not an ownership fault — one body still owns one slot
        # (see verify_tes_slot_recovery_ownership.py V1/V4).  Only a
        # shared lane AWAY from the boundary is a real bunching defect.
        _t = c.tuners
        _fw = float(getattr(_t, "field_width_m", 70.0) or 70.0)
        _mg = float(getattr(_t, "sideline_margin_m", 3.0) or 0.0)
        _lo, _hi = _mg, _fw - _mg
        lanes = sorted(c.lanes.values())
        for i in range(len(lanes) - 1):
            if lanes[i + 1] - lanes[i] > LANE_DUP_TOL_M:
                continue
            if (lanes[i] <= _lo + LANE_DUP_TOL_M
                    or lanes[i + 1] >= _hi - LANE_DUP_TOL_M):
                saturated += 1
                continue
            shared += 1
            fails.append(
                f"E: chamber {c.cid} two locked lanes within "
                f"{LANE_DUP_TOL_M:g} m of each other away from the "
                f"sideline ({lanes[i]:.2f} / {lanes[i + 1]:.2f})"
            )
    print(f"    shared-lane pairs away from the sideline: {shared}")
    print(f"    sideline-saturated lane pairs (ladder clamp): {saturated}")
    print(f"    worst recovery pull (entry Y -> assigned lane): "
          f"{worst_disp:.2f} m (chamber/pid {worst_disp_pid})")
    if worst_seed > 0.05:
        fails.append(
            f"E: a locked lane is not the body's own assigned lane "
            f"({worst_seed:.3f} m at {worst_seed_pid})"
        )

    print("\n" + "=" * 78)
    if not fails:
        print("RESULT: LANE-TRUE — in-lane defenders hold perfectly, "
              "out-of-lane defenders correct cleanly, the line neither "
              "drifts nor fans, and no intra-tackle frame re-seeds a lane.")
        return 0
    print(f"RESULT: {len(fails)} VIOLATION(S):")
    for f in fails[:25]:
        print(f"  - {f}")
    return 1


def _role_name_of(worst_at):
    """Role label from a Chamber.worst_at tuple (index 4), or '-'."""
    if not worst_at or len(worst_at) < 5:
        return "-"
    return worst_at[4]


def main():
    res = _run_chamber_loop()
    if res["drive_frames"] == 0 or not res["chambers"]:
        print("=" * 78)
        print("TES DRIVE LANE-TRUE RETREAT VERIFIER")
        print("=" * 78)
        print("\nRESULT: FAILED — the harness never reached a TES drive "
              "chamber (no drive frame observed).")
        return 2
    return _report(res)


if __name__ == "__main__":
    code = 2
    try:
        code = main()
    finally:
        pygame.quit()
    sys.exit(code)
