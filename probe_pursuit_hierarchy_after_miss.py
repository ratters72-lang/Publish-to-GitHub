"""probe_pursuit_hierarchy_after_miss.py — is PURSUIT entering too low in the
decision hierarchy?

THE QUESTION
------------
A defender is beaten by the ball carrier (broken tackle / bump-off / pre-tackle
miss).  In the frames after that miss the body is supposed to resume pursuing
the live carrier.  The reported symptom is that it does not, or does so only
after a delay it should not have.

This probe does not argue a cause.  It drives the real engine, detects the miss
on the beaten body and then logs, FOR EVERY FRAME of the post-miss window:

  * every stage of the OPEN_PLAY dispatch that WROTE ``target_x`` / ``target_y``
    on that body, in true call order (before -> after, per stage);
  * the writer-conflict band ledger for that body: every proposal with its
    PRIORITY TIER, the per-axis winner, and the suppressed losers;
  * the frame's final winning decision and target (state, source, tier, and the
    stage that made the last write);
  * the pure predicate reads that explain the outcome (frozen / freeze_timer,
    the commit latches, seed staleness, band / glide / recovery eligibility);
  * whether ``Player.update`` ran, the velocity the frame produced, and the
    distance to the carrier (closure).

GOAL
----
Decide whether the chase is GENERATED correctly and then OVERRIDDEN or DELAYED
by higher-priority positioning / hold / recovery / shape-maintenance logic.

WHY A STAGE LEDGER (not inference)
----------------------------------
The write path is last-writer-wins and many writers touch one body per frame
(behaviour pipeline, fullback geometry, the target_x glide cap, seed-hold, the
writer-priority bands, catch-X authority, the line-break static guard, the
obligated-retreat pin, the freeze clock, then integration).  No reader can tell
from the source which one owned a given frame, so this probe wraps EACH of those
stages and records the target either side of it: every write is attributed to
the stage that made it, in order.

The band ledger is read from the resolver's own bookkeeping
(``world._writer_priority_bands``) after ``resolve`` runs, so the priority tiers
and the suppressed losers are the ENGINE'S numbers, not a paraphrase.

METHOD
------
Read-only.  Every spy calls the real callable UNCHANGED; the per-frame row is
SAMPLED after the engine has finished the frame, so each printed value is the
frame's FINAL applied state.

Run:     python probe_pursuit_hierarchy_after_miss.py
Report:  tmp_pursuit_hierarchy_after_miss.txt

A/B (one process, several rollouts):
    set RLM_PHM_LEGS=baseline,bands_off,fep_off,glide_off,clock_off
Legs: baseline; bands_off (writer_priority_bands disabled on the world);
fep_off (RLM_FREEZE_EXPIRY_PURSUIT=0); glide_off (RLM_GLIDE_SMOOTH=0);
clock_off (RLM_TES_FREEZE_CLOCK=0); pmp_off (RLM_POST_MISS_PURSUIT=0).

``pmp_off`` is the BASELINE arm for the post-miss PURSUIT ENTITLEMENT
(``game/defence/defence_lvl_1/post_miss_pursuit.py``).  With the lever off
the entitlement is inert on every path it wired -- the miss teardown arms
nothing, the MMDL / retreat / static-guard carve-outs all short-circuit,
and the dispatch's final pursuit write emits nothing -- so the leg is the
LEGACY behaviour and the ``baseline`` leg can be compared against it
directly.
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import math
import random
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

import pygame

pygame.display.init()
pygame.display.set_mode((1, 1), pygame.NOFRAME)

import world.open_play_engine as ope_mod

# The engine's own per-frame chatter would drown the report.
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
from player.player import Player

# ── THE STAGES UNDER MEASUREMENT ─────────────────────────────────────
# Each is reached from DefenceManager.update_open_play by a FUNCTION-LOCAL
# ``from <module> import <name>``, so patching the module attribute is the call
# site the manager resolves at call time.
from game.defence.defence_lvl_1 import pretackle_machine as pm_mod
from game.defence.defence_lvl_1 import writer_priority_bands as wpb_mod
from game.defence.defence_lvl_1 import line_break_static_guard as lbsg_mod
from game.defence.defence_lvl_1 import seed_hold as sh_mod
from game.defence.defence_lvl_1 import target_x_glide as txg_mod
from game.defence.defence_lvl_1 import catch_x_authority as cxa_mod
from game.defence.defence_lvl_1 import defensive_behaviour_unified as dbu_mod
from game.managers import retreat_manager as rm_mod

# ── PURE PREDICATES (read-only reads of the engine's own laws) ────────
from game.defence.defence_lvl_1.seed_hold import is_seed_stale
from game.defence.defence_lvl_1.writer_priority_bands import (
    is_band_eligible,
    priority_for_source,
    winner_for,
)
from game.defence.defence_lvl_1.target_x_glide import (
    is_target_x_glide_eligible,
)
from game.defence.defence_lvl_1.line_break_static_guard import (
    is_static_guard_recovery_eligible,
)
from game.defence.defence_lvl_1.retreat_obligation import is_retreat_obligated
from game.defence.defence_lvl_1.pretackle_corridor import is_corridor_owned
from game.defence.defence_lvl_1.movement_mode_decision import (
    carrier_past_defensive_line,
)
from game.defence.defence_lvl_1.anchor_authority import defensive_line_anchor_m


# ═════════════════════════════════════════════════════════════════════
# ROLLOUT CONFIG
# ═════════════════════════════════════════════════════════════════════
FRAMES = int(os.environ.get("RLM_PHM_FRAMES", "6000"))
SEED = int(os.environ.get("RLM_PHM_SEED", "20260827"))
DT = 1.0 / 60.0

# Frames dumped in detail after each miss event.
WINDOW = int(os.environ.get("RLM_PHM_WINDOW", "120"))

# Cap on events reported in detail.
MAX_EVENTS = int(os.environ.get("RLM_PHM_EVENTS", "6"))

REPORT_PATH = os.environ.get(
    "RLM_PHM_REPORT", "tmp_pursuit_hierarchy_after_miss.txt"
)

# A/B legs, one rollout each in this process.
LEGS = tuple(
    part.strip()
    for part in os.environ.get("RLM_PHM_LEGS", "baseline").split(",")
    if part.strip()
)

# Which trace kinds to record — all three by default.  Set
# RLM_PHM_TRACE_KINDS=BUMP_OFF,BROKEN_TACKLE to trace ONLY the bodies the ball
# carrier beat outright, which is the subject of this investigation.
TRACE_KINDS = frozenset(
    part.strip()
    for part in os.environ.get(
        "RLM_PHM_TRACE_KINDS",
        "PRE_TACKLE_MISS,BUMP_OFF,BROKEN_TACKLE",
    ).split(",")
    if part.strip()
)

# A commanded X within this many metres of the live carrier (plus a matching
# Y) is a PURSUIT POINT: the body is being sent AT the carrier.
CLOSE_M = 2.0

# ``apply_outcome`` engages on this outcome; everything else is a miss.
CLEAN_ENGAGE = "CLEAN_ENGAGE"

# The chase family of ``target_source`` stamps.
CHASE_FAMILY_TOKENS = (
    "pursuit",
    "chase",
    "line_break",
    "tackler_commit",
    "shoot",
)

# The hold / shape / recovery family of ``target_source`` stamps.
SHAPE_FAMILY_TOKENS = (
    "spacing",
    "width",
    "retreat",
    "seed_hold",
    "struct_freeze",
    "freeze_timer",
    "structured",
    "mmdl_hold",
    "mmdl_drift",
    "mmdl_retreat",
)


# ═════════════════════════════════════════════════════════════════════
# SMALL HELPERS
# ═════════════════════════════════════════════════════════════════════

def name_of(obj):
    """``obj.name`` when present, else ``str(obj)``, else None."""
    if obj is None:
        return None
    return getattr(obj, "name", None) or str(obj)


def fnum(value):
    """Return *value* as float when numeric, else None (never raises)."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def pid_of(player):
    return getattr(player, "player_id", None)


def role_name(player):
    role = getattr(player, "defender_role", None)
    return name_of(role)


def is_chase_family(source) -> bool:
    """True when *source* is a stamp of the chase family."""
    if not source:
        return False
    text = str(source).lower()
    return any(token in text for token in CHASE_FAMILY_TOKENS)


def is_shape_family(source) -> bool:
    """True when *source* is a hold / shape / recovery stamp."""
    if not source:
        return False
    text = str(source).lower()
    return any(token in text for token in SHAPE_FAMILY_TOKENS)


def all_bodies(world):
    """Every distinct player on the world, keyed by player_id."""
    out = {}
    for group in ("attacking_team", "defending_team", "players"):
        for player in list(getattr(world, group, None) or []):
            if player is None:
                continue
            pid = pid_of(player)
            if pid is None:
                continue
            out.setdefault(pid, player)
    carrier = getattr(world, "ball_carrier", None)
    if carrier is not None and pid_of(carrier) is not None:
        out.setdefault(pid_of(carrier), carrier)
    return out


# ═════════════════════════════════════════════════════════════════════
# THE LEDGER
# ═════════════════════════════════════════════════════════════════════

class Ledger:
    """Records the miss events and, after each, the per-frame write ledger."""

    def __init__(self, leg="baseline"):
        self.leg = leg
        self.frame = 0
        self.bodies = {}          # pid -> Player (refreshed every frame)
        self.traces = []          # one trace per miss event
        self.trace_of_pid = {}    # pid -> [trace index, ...]
        self.rows = {}            # trace index -> [(frame, row), ...]
        self.stage_writes = {}    # (trace index, frame) -> [write, ...]
        self.frame_calls = set()  # pids whose Player.update ran this frame
        self.miss_events = []
        self.broken_events = []
        self.armed_events = []
        self.frames_stepped = 0
        self.open_play_frames = 0
        self.live_break_frames = 0
        self.handback_frames = 0
        self._trace_seq = 0

    # ── frame boundary ───────────────────────────────────────────────
    def begin_frame(self, frame, world):
        self.frame = int(frame)
        self.bodies = all_bodies(world)
        self.frame_calls = set()

    def active_trace_indices(self, frame=None):
        """Trace indices whose window covers *frame*."""
        frame = self.frame if frame is None else frame
        return [
            index for index, trace in enumerate(self.traces)
            if trace["event_frame"] <= frame <= trace["until_frame"]
        ]

    def tracked_pids(self):
        """Pids with at least one trace covering the current frame."""
        out = []
        for index in self.active_trace_indices():
            pid = self.traces[index]["pid"]
            if pid is not None and pid not in out:
                out.append(pid)
        return out

    def tracing(self) -> bool:
        return bool(self.active_trace_indices())

    # ── event recorders ──────────────────────────────────────────────
    def note_armed(self, player, role_name_str):
        self.armed_events.append({
            "frame": self.frame,
            "pid": pid_of(player),
            "role": role_name_str,
        })

    def note_outcome(self, world, defender, outcome):
        """A pre-tackle outcome was applied to *defender*."""
        text = name_of(outcome)
        self.miss_events.append({
            "frame": self.frame,
            "pid": pid_of(defender),
            "outcome": text,
            "timer": fnum(getattr(defender, "freeze_timer", 0.0)),
            "commit": bool(getattr(defender, "_tackle_commit_active", False)),
            "pursuit": bool(
                getattr(defender, "_pursuit_commit_active", False)
            ),
        })
        if text and text != CLEAN_ENGAGE:
            self._open_trace(pid_of(defender), "PRE_TACKLE_MISS", text)

    def note_broken(self, world, primary, joiners, bump_off):
        """The engine tore down a broken tackle / bump-off."""
        ids = []
        if primary is not None:
            ids.append(pid_of(primary))
        for joiner in joiners or ():
            ids.append(pid_of(joiner))
        self.broken_events.append({
            "frame": self.frame,
            "bump_off": bool(bump_off),
            "tackler_ids": ids,
            "timer": (
                fnum(getattr(primary, "freeze_timer", 0.0))
                if primary is not None else None
            ),
        })
        kind = "BUMP_OFF" if bump_off else "BROKEN_TACKLE"
        for pid in ids:
            self._open_trace(pid, kind, None)

    def _open_trace(self, pid, kind, outcome):
        """Open (or extend) a trace window for the beaten body *pid*.

        An event on a body already inside a window EXTENDS it, so the several
        teardown writers of one miss (the broken-tackle teardown, then the
        pre-tackle miss outcome) cannot open overlapping traces on one body.
        """
        if pid is None or kind not in TRACE_KINDS:
            return
        for index in self.trace_of_pid.get(pid, ()):
            trace = self.traces[index]
            if self.frame - trace["event_frame"] <= 5:
                trace["until_frame"] = max(
                    trace["until_frame"], self.frame + WINDOW
                )
                trace["repeats"] = trace.get("repeats", 0) + 1
                return
        index = self._trace_seq
        self._trace_seq += 1
        self.traces.append({
            "index": index,
            "pid": pid,
            "kind": kind,
            "outcome": outcome,
            "event_frame": self.frame,
            "until_frame": self.frame + WINDOW,
            "repeats": 0,
        })
        self.trace_of_pid.setdefault(pid, []).append(index)
        self.rows[index] = []

    # ── stage capture ────────────────────────────────────────────────
    def begin_stage(self):
        """(target_x, target_y, target_source) per tracked body, or None."""
        if not self.tracing():
            return None
        out = {}
        for pid in self.tracked_pids():
            body = self.bodies.get(pid)
            if body is None:
                continue
            out[pid] = (
                fnum(getattr(body, "target_x", None)),
                fnum(getattr(body, "target_y", None)),
                getattr(body, "target_source", None),
            )
        return out

    def end_stage(self, stage, before):
        """Record every target write *stage* made on a tracked body."""
        if before is None:
            return
        for pid, (tx0, ty0, src0) in before.items():
            body = self.bodies.get(pid)
            if body is None:
                continue
            tx1 = fnum(getattr(body, "target_x", None))
            ty1 = fnum(getattr(body, "target_y", None))
            src1 = getattr(body, "target_source", None)
            if tx1 == tx0 and ty1 == ty0 and src1 == src0:
                continue
            self._record_write(pid, stage, tx0, ty0, tx1, ty1, src1)

    def _record_write(self, pid, stage, tx0, ty0, tx1, ty1, source):
        row = {
            "stage": stage,
            "tx0": tx0,
            "ty0": ty0,
            "tx1": tx1,
            "ty1": ty1,
            "source": source,
        }
        for index in self.trace_of_pid.get(pid, ()):
            trace = self.traces[index]
            if trace["event_frame"] <= self.frame <= trace["until_frame"]:
                bucket = self.stage_writes.setdefault(
                    (index, self.frame), []
                )
                bucket.append(row)

    def note_handback(self):
        self.handback_frames += 1

    # ── per-frame sample ─────────────────────────────────────────────
    def sample(self, world):
        """One row per tracked trace, after the engine finished the frame."""
        carrier = getattr(world, "ball_carrier", None)
        bc_x = fnum(getattr(carrier, "x", None))
        bc_y = fnum(getattr(carrier, "y", None))
        try:
            anchor_x = float(defensive_line_anchor_m(world))
        except Exception:
            anchor_x = None
        try:
            break_live = bool(
                carrier is not None
                and carrier_past_defensive_line(world, carrier)
            )
        except Exception:
            break_live = None
        if break_live:
            self.live_break_frames += 1

        for index in self.active_trace_indices():
            trace = self.traces[index]
            body = self.bodies.get(trace["pid"])
            if body is None:
                continue
            row = self._build_row(
                body, world, bc_x, bc_y, anchor_x, break_live, index,
            )
            self.rows[index].append((self.frame, row))

    def _build_row(self, body, world, bc_x, bc_y, anchor_x, break_live,
                   trace_index):
        """The frame's FINAL measured state for one beaten body."""
        tx = fnum(getattr(body, "target_x", None))
        ty = fnum(getattr(body, "target_y", None))
        x = fnum(getattr(body, "x", 0.0)) or 0.0
        y = fnum(getattr(body, "y", 0.0)) or 0.0
        vx = fnum(getattr(body, "vx", 0.0)) or 0.0
        vy = fnum(getattr(body, "vy", 0.0)) or 0.0
        source = getattr(body, "target_source", None)

        dist_to_bc = None
        if bc_x is not None and bc_y is not None:
            dist_to_bc = math.hypot(x - bc_x, y - bc_y)

        stale = band_ok = glide_ok = recover_ok = obligated = corridor = None
        try:
            stale = bool(is_seed_stale(body, world))
        except Exception:
            pass
        try:
            band_ok = bool(is_band_eligible(body, None))
        except Exception:
            pass
        try:
            glide_ok = bool(is_target_x_glide_eligible(body, world))
        except Exception:
            pass
        try:
            recover_ok = bool(
                is_static_guard_recovery_eligible(body, world)
            )
        except Exception:
            pass
        try:
            obligated = bool(is_retreat_obligated(body, world))
        except Exception:
            pass
        try:
            corridor = bool(is_corridor_owned(body))
        except Exception:
            pass

        # ── the band ledger for this body, this frame ──
        winner_x = winner_y = None
        proposals = {}
        try:
            state = getattr(world, "_writer_priority_bands", None) or {}
            per_axis = (state.get("proposals") or {}).get(id(body)) or {}
            for axis, by_source in per_axis.items():
                proposals[axis] = {
                    str(src): (int(prio), float(val))
                    for src, (prio, val) in by_source.items()
                }
            winner = winner_for(world, body) or {}
            winner_x = winner.get("x")
            winner_y = winner.get("y")
        except Exception:
            pass

        return {
            "frame": self.frame,
            "pid": pid_of(body),
            "x": x,
            "y": y,
            "vx": vx,
            "vy": vy,
            "speed": math.hypot(vx, vy),
            "target_x": tx,
            "target_y": ty,
            "off_x": None if tx is None else tx - x,
            "off_y": None if ty is None else ty - y,
            "source": source,
            "source_tier": priority_for_source(source),
            "writer": getattr(body, "_target_writer", None),
            "state": name_of(getattr(body, "movement_state", None)),
            "last_decision": getattr(body, "last_decision", None),
            "move_speed": fnum(getattr(body, "move_speed", None)),
            "role": role_name(body),
            "depth_class": getattr(body, "depth_class", None),
            "tackle_role": getattr(body, "tackle_role", None),
            "frozen": bool(getattr(body, "frozen", False)),
            "freeze_timer": fnum(getattr(body, "freeze_timer", 0.0)),
            "commit": bool(getattr(body, "_tackle_commit_active", False)),
            "pursuit_commit": bool(
                getattr(body, "_pursuit_commit_active", False)
            ),
            "tackle_state": bool(getattr(body, "tackle_state_active", False)),
            "stance": bool(getattr(body, "pre_tackle_stance", False)),
            "pt_state": name_of(getattr(body, "pre_tackle_state", None)),
            "tes_locked": bool(getattr(body, "tes_locked", False)),
            "in_contact": bool(getattr(body, "in_contact", False)),
            "is_tackler": bool(getattr(body, "is_tackler", False)),
            "seed_stale": stale,
            "band_eligible": band_ok,
            "glide_eligible": glide_ok,
            "recover_eligible": recover_ok,
            "obligated": obligated,
            "corridor": corridor,
            "depth_role": getattr(body, "depth_role", None),
            "depth_seeded": bool(getattr(body, "_depth_seeded", False)),
            "bc_x": bc_x,
            "bc_y": bc_y,
            "dist_to_bc": dist_to_bc,
            "anchor_x": anchor_x,
            "break_live": break_live,
            "update_ran": pid_of(body) in self.frame_calls,
            "proposals": proposals,
            "band_winner_x": winner_x,
            "band_winner_y": winner_y,
            "writes": self.stage_writes.get((trace_index, self.frame)) or [],
        }
# ═════════════════════════════════════════════════════════════════════
# SPY INSTALLATION — every wrapper calls the real callable UNCHANGED
# ═════════════════════════════════════════════════════════════════════

def install_spies(ledger):
    """Wrap the miss writers, the dispatch stages and the integrator."""
    originals = {}

    # ── the miss itself ──
    originals["outcome"] = pm_mod.apply_outcome

    def outcome(world, defender, bc, tuners, state=None, *a, **kw):
        result = originals["outcome"](
            world, defender, bc, tuners, state=state, *a, **kw
        )
        try:
            ledger.note_outcome(world, defender, result)
        except Exception:
            pass
        return result

    pm_mod.apply_outcome = outcome

    originals["broken"] = OpenPlayEngine._handle_broken_tackle

    def broken(self, *, bump_off=False):
        primary = getattr(self, "primary", None)
        joiners = list(getattr(self, "joiners", None) or ())
        world = getattr(self, "world", None)
        try:
            ledger.note_broken(world, primary, joiners, bump_off)
        except Exception:
            pass
        return originals["broken"](self, bump_off=bump_off)

    OpenPlayEngine._handle_broken_tackle = broken

    originals["arm"] = OpenPlayEngine._arm_tackler_role

    def arm_tackler_role(self, defender, role_enum, role_name_str, ctx):
        result = originals["arm"](
            self, defender, role_enum, role_name_str, ctx
        )
        try:
            ledger.note_armed(defender, role_name_str)
        except Exception:
            pass
        return result

    OpenPlayEngine._arm_tackler_role = arm_tackler_role

    # ── the dispatch stages, in the order the manager calls them ──
    def wrap(stage, real):
        def wrapper(*a, **kw):
            before = ledger.begin_stage()
            result = real(*a, **kw)
            try:
                ledger.end_stage(stage, before)
            except Exception:
                pass
            return result
        return wrapper

    originals["dbu_apply"] = dbu_mod.DefensiveBehaviourUnified.apply
    dbu_mod.DefensiveBehaviourUnified.apply = wrap(
        "behaviour", originals["dbu_apply"]
    )

    originals["wpb_resolve"] = wpb_mod.resolve
    wpb_mod.resolve = wrap("band_resolve", originals["wpb_resolve"])

    originals["glide"] = txg_mod.glide_cap_target_x
    txg_mod.glide_cap_target_x = wrap("glide_cap", originals["glide"])

    originals["seed_hold"] = sh_mod.apply_seed_hold
    sh_mod.apply_seed_hold = wrap("seed_hold", originals["seed_hold"])

    originals["catch_x"] = cxa_mod.apply_catch_x_authority
    cxa_mod.apply_catch_x_authority = wrap("catch_x", originals["catch_x"])

    originals["lb_guard"] = lbsg_mod.enforce_line_break_static_guard
    lbsg_mod.enforce_line_break_static_guard = wrap(
        "line_break_guard", originals["lb_guard"]
    )

    originals["forced_retreat"] = rm_mod.RetreatManager.apply_forced_retreat
    rm_mod.RetreatManager.apply_forced_retreat = wrap(
        "obligated_retreat", originals["forced_retreat"]
    )

    # ── the freeze clock (the expiry hand-back) ──
    originals["decay"] = ope_mod.decay_freeze_timers

    def decay(world, dt):
        before = ledger.begin_stage()
        result = originals["decay"](world, dt)
        try:
            ledger.end_stage("freeze_clock", before)
            if result:
                ledger.note_handback()
        except Exception:
            pass
        return result

    ope_mod.decay_freeze_timers = decay

    # ── the integrator (which bodies integrated this frame) ──
    originals["update"] = Player.update

    def update(self, dt, attacking_direction=1):
        try:
            ledger.frame_calls.add(pid_of(self))
        except Exception:
            pass
        return originals["update"](self, dt, attacking_direction)

    Player.update = update

    return originals


def uninstall_spies(originals):
    """Restore every real callable (probe hygiene)."""
    pm_mod.apply_outcome = originals["outcome"]
    OpenPlayEngine._handle_broken_tackle = originals["broken"]
    OpenPlayEngine._arm_tackler_role = originals["arm"]
    dbu_mod.DefensiveBehaviourUnified.apply = originals["dbu_apply"]
    wpb_mod.resolve = originals["wpb_resolve"]
    txg_mod.glide_cap_target_x = originals["glide"]
    sh_mod.apply_seed_hold = originals["seed_hold"]
    cxa_mod.apply_catch_x_authority = originals["catch_x"]
    lbsg_mod.enforce_line_break_static_guard = originals["lb_guard"]
    rm_mod.RetreatManager.apply_forced_retreat = originals["forced_retreat"]
    ope_mod.decay_freeze_timers = originals["decay"]
    Player.update = originals["update"]


# ═════════════════════════════════════════════════════════════════════
# THE A/B LEGS — runtime only, no code change
# ═════════════════════════════════════════════════════════════════════

# leg name -> {env var: value}
_LEG_ENV = {
    "baseline": {},
    "bands_off": {},
    "fep_off": {"RLM_FREEZE_EXPIRY_PURSUIT": "0"},
    "glide_off": {"RLM_GLIDE_SMOOTH": "0"},
    "clock_off": {"RLM_TES_FREEZE_CLOCK": "0"},
    # The post-miss pursuit entitlement OFF (the legacy arm).
    "pmp_off": {"RLM_POST_MISS_PURSUIT": "0"},
}

# leg name -> world attribute overrides applied after the world exists
_LEG_WORLD = {
    "baseline": {},
    "bands_off": {"writer_priority_bands_enabled": False},
    "fep_off": {},
    "glide_off": {},
    "clock_off": {},
    "pmp_off": {},
}


def apply_leg(leg, world):
    """Apply one leg's runtime levers; returns the env to restore."""
    saved = {}
    for var, value in _LEG_ENV.get(leg, {}).items():
        saved[var] = os.environ.get(var)
        os.environ[var] = value
    for attr, value in _LEG_WORLD.get(leg, {}).items():
        setattr(world, attr, value)
    return saved


def restore_leg(saved):
    for var, previous in saved.items():
        if previous is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = previous


# ═════════════════════════════════════════════════════════════════════
# THE ROLLOUT — the real engine, booted like the other post-miss probes
# ═════════════════════════════════════════════════════════════════════

def run_rollout(leg="baseline", frames=None, seed=None, verbose=False):
    """Boot the engine, kick off, step it, return the measured ledger."""
    frames = FRAMES if frames is None else int(frames)
    seed = SEED if seed is None else int(seed)

    collision_system = CollisionSystem()
    field = Field(1920, 1080)
    world = WorldState()
    ball = Ball(field)
    world.ball = ball

    open_play = OpenPlayEngine(world)
    world.open_play = open_play
    world.open_play_engine = open_play

    kickoff_engine = KickoffEngine(world, ball, field)

    random.seed(seed)
    kickoff_engine.reset_for_kickoff()
    apply_receiving_kickoff_formation(world, field)
    apply_kicking_team_formation(world, field)
    world.ball_owner = "NONE"
    world.ball_carrier = None
    world.ball.possessed_by = None
    kickoff_engine.kickoff()

    ledger = Ledger(leg)
    saved = apply_leg(leg, world)
    originals = install_spies(ledger)

    try:
        for frame in range(1, frames + 1):
            world.frame_count = frame
            ball.update(DT)
            ledger.begin_frame(frame, world)

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
                    players = getattr(world, "players", None) or []
                    if players:
                        collision_system.resolve_all(players, world=world)

            # SAMPLE AFTER the engine has finished the frame.
            ledger.sample(world)
            ledger.frames_stepped = frame
            if getattr(open_play, "_phase", None) == "OPEN_PLAY":
                ledger.open_play_frames += 1

            if verbose and frame % 1000 == 0:
                print(
                    "    ... [%s] frame %d (traces=%d)"
                    % (leg, frame, len(ledger.traces)),
                    file=_REAL_STDOUT, flush=True,
                )
    finally:
        uninstall_spies(originals)
        restore_leg(saved)

    return {"ledger": ledger, "world": world, "open_play": open_play}
# ═════════════════════════════════════════════════════════════════════
# ANALYSIS
# ═════════════════════════════════════════════════════════════════════

def _f(value, width=8, prec=2):
    """Format a possibly-None float."""
    if value is None:
        return "None".rjust(width)
    if isinstance(value, str):
        return value.rjust(width)
    try:
        return ("%.*f" % (prec, float(value))).rjust(width)
    except (TypeError, ValueError):
        return str(value).rjust(width)


def latest_write(row):
    """The last stage that wrote this body's target this frame."""
    writes = row.get("writes") or []
    return writes[-1] if writes else None


def write_chain(row):
    """``stage:stage:...`` for every write this frame, in order."""
    writes = row.get("writes") or []
    if not writes:
        return "-"
    return ":".join(str(w.get("stage")) for w in writes)


def chase_proposal_x(row):
    """The strongest proposal on X that points AT the carrier, if any.

    Returns ``(source, priority, value, distance_to_bc)`` for the
    highest-priority proposal whose X lies within ``CLOSE_M`` of the carrier,
    or None when no writer proposed a point at the carrier this frame.
    """
    bc_x = row.get("bc_x")
    if bc_x is None:
        return None
    proposals = (row.get("proposals") or {}).get("x") or {}
    best = None
    for source, (prio, value) in proposals.items():
        if abs(float(value) - float(bc_x)) > CLOSE_M:
            continue
        if best is None or prio > best[1]:
            best = (source, prio, float(value))
    if best is None:
        return None
    return (best[0], best[1], best[2], abs(best[2] - float(bc_x)))


def final_target_at_carrier(row):
    """True when the frame's FINAL target sits on the live carrier."""
    tx = row.get("target_x")
    ty = row.get("target_y")
    bc_x = row.get("bc_x")
    bc_y = row.get("bc_y")
    if None in (tx, ty, bc_x, bc_y):
        return False
    return abs(tx - bc_x) <= CLOSE_M and abs(ty - bc_y) <= 3.0


def classify(row):
    """The frame's verdict: why the beaten body did or did not pursue.

    Returns ``(code, detail)``.  Every branch reads measured fields; nothing
    here is inferred from the source.
    """
    winner = row.get("band_winner_x")
    winner_src = None
    winner_prio = None
    if isinstance(winner, (tuple, list)) and len(winner) >= 2:
        winner_src, winner_prio = str(winner[0]), int(winner[1])

    source = row.get("source")
    chase_prop = chase_proposal_x(row)
    source_chase = is_chase_family(source)
    at_carrier = final_target_at_carrier(row)
    speed = row.get("speed") or 0.0

    if row.get("frozen") or (row.get("freeze_timer") or 0.0) > 0.0:
        return (
            "HELD_BY_FREEZE_PIN",
            "frozen=%.2f/frozen flag" % (row.get("freeze_timer") or 0.0),
        )

    if source == "seed_hold" or (
        row.get("seed_stale")
        and (row.get("move_speed") or 0.0) <= 0.0
        and not source_chase
    ):
        return (
            "HELD_BY_SEED_HOLD",
            "stale seed, move_speed=%.2f" % (row.get("move_speed") or 0.0),
        )

    if (row.get("commit") or row.get("pursuit_commit")) and not row.get("writes"):
        latch = []
        if row.get("commit"):
            latch.append("_tackle_commit_active")
        if row.get("pursuit_commit"):
            latch.append("_pursuit_commit_active")
        return "EXEMPT_BY_COMMIT_LATCH", "+".join(latch)

    if at_carrier and speed > 0.5:
        return "PURSUIT_LIVE", "target on carrier, speed=%.2f" % speed

    if at_carrier:
        return (
            "PURSUIT_TARGET_NO_MOTION",
            "target on carrier, speed=%.2f" % speed,
        )

    if chase_prop is not None and winner_src is not None:
        if winner_src != chase_prop[0] and (winner_prio or 0) >= chase_prop[1]:
            return (
                "OVERRIDDEN_BY:%s" % winner_src,
                "chase %s(prio=%d) lost to %s(prio=%d)"
                % (chase_prop[0], chase_prop[1], winner_src, winner_prio),
            )

    if source_chase:
        return "CHASE_STAMP_STATIC_TARGET", str(source)

    if row.get("commit") or row.get("pursuit_commit"):
        return "EXEMPT_BY_COMMIT_LATCH", "latch held, no chase target"

    return "NO_PURSUIT_INTENT", str(source)


def trace_delay(rows):
    """Frames from the miss to the first frame the body CLOSES on the carrier.

    A closing frame is one whose FINAL target sits on the carrier AND whose
    distance to the carrier is smaller than the previous row's.  Returns
    ``(delay_frames or None, first_dist, last_dist)``.
    """
    prev_dist = None
    first_dist = rows[0][1].get("dist_to_bc") if rows else None
    last_dist = rows[-1][1].get("dist_to_bc") if rows else None
    for frame, row in rows:
        dist = row.get("dist_to_bc")
        if (
            final_target_at_carrier(row)
            and prev_dist is not None
            and dist is not None
            and dist < prev_dist
        ):
            return frame - rows[0][0], first_dist, last_dist
        prev_dist = dist
    return None, first_dist, last_dist


def histogram(values, limit=20):
    hist = {}
    for value in values:
        hist[value] = hist.get(value, 0) + 1
    return sorted(hist.items(), key=lambda kv: -kv[1])[:limit]


def leg_metrics(res):
    """Compact per-leg metrics used by the A/B comparison."""
    ledger = res["ledger"]
    delays = []
    never = 0
    codes = {}
    dist_first = []
    dist_last = []
    for trace in ledger.traces:
        rows = ledger.rows.get(trace["index"]) or []
        if not rows:
            continue
        delay, first, last = trace_delay(rows)
        if first is not None:
            dist_first.append(first)
        if last is not None:
            dist_last.append(last)
        if delay is None:
            never += 1
        else:
            delays.append(delay)
        for _frame, row in rows:
            code, _detail = classify(row)
            codes[code] = codes.get(code, 0) + 1
    return {
        "leg": ledger.leg,
        "traces": len(ledger.traces),
        "misses": len(ledger.miss_events),
        "broken": len(ledger.broken_events),
        "frames_stepped": ledger.frames_stepped,
        "open_play_frames": ledger.open_play_frames,
        "delays": delays,
        "never": never,
        "codes": codes,
        "dist_first": dist_first,
        "dist_last": dist_last,
    }


def _mean(values):
    return (sum(values) / len(values)) if values else None


def _median(values):
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


# ═════════════════════════════════════════════════════════════════════
# REPORT
# ═════════════════════════════════════════════════════════════════════

_REPORT = None
_REAL_STDOUT = sys.stdout


class _tee:
    """Write to the report file and the real stdout at once."""

    def write(self, text):
        try:
            if _REPORT is not None:
                _REPORT.write(text)
        except Exception:
            pass
        return _REAL_STDOUT.write(text)

    def flush(self):
        try:
            if _REPORT is not None:
                _REPORT.flush()
        except Exception:
            pass
        return _REAL_STDOUT.flush()


def banner(title):
    print("")
    print("=" * 108)
    print(title)
    print("=" * 108)


def sub(title):
    print("")
    print("-" * 108)
    print(title)
    print("-" * 108)


def print_row_line(frame, row):
    """One compact line: the frame's final winning decision and target."""
    code, detail = classify(row)
    write = latest_write(row)
    stage = write["stage"] if write else "-"
    winner = row.get("band_winner_x")
    if isinstance(winner, (tuple, list)) and len(winner) >= 2:
        win_txt = "%s@%d" % (winner[0], int(winner[1]))
    else:
        win_txt = "-"
    prop = chase_proposal_x(row)
    prop_txt = "%s@%d" % (prop[0], prop[1]) if prop else "-"
    print(
        "    f=%-6s %-9s src=%-22s tier=%-4s last=%-16s win=%-22s "
        "chase=%-20s tx=%s ty=%s off_x=%s off_y=%s spd=%s ms=%s dist=%s"
        " | %s (%s)"
        % (
            frame,
            str(row.get("state")),
            str(row.get("source")),
            str(row.get("source_tier")),
            stage,
            win_txt,
            prop_txt,
            _f(row.get("target_x"), 8, 2),
            _f(row.get("target_y"), 8, 2),
            _f(row.get("off_x"), 7, 2),
            _f(row.get("off_y"), 7, 2),
            _f(row.get("speed"), 6, 2),
            _f(row.get("move_speed"), 6, 2),
            _f(row.get("dist_to_bc"), 7, 2),
            code,
            detail,
        )
    )


def report(results):
    """Print the whole measured report for every leg that was run."""
    banner("PURSUIT DECISION HIERARCHY AFTER A MISS — MEASURED")
    print("  seed                          : %d" % SEED)
    print("  dt                            : %.6f s (%.1f Hz)" % (DT, 1.0 / DT))
    print("  frames per leg                : %d" % FRAMES)
    print("  window per miss               : %d frames" % WINDOW)
    print("  legs run                      : %s" % ", ".join(LEGS))
    print("  pursuit-point radius          : %.1f m" % CLOSE_M)

    # ══ A. COVERAGE ══
    sub("A. COVERAGE — did the miss path fire, and on which bodies?")
    for res in results:
        ledger = res["ledger"]
        print("  ── leg %s ──" % ledger.leg)
        print("     frames stepped             : %d" % ledger.frames_stepped)
        print("     OPEN_PLAY frames           : %d" % ledger.open_play_frames)
        print("     live-line-break frames     : %d" % ledger.live_break_frames)
        print("     freeze-clock tick frames   : %d" % ledger.handback_frames)
        print("     apply_outcome calls        : %d" % len(ledger.miss_events))
        print("     _handle_broken_tackle      : %d" % len(ledger.broken_events))
        print("     TES arming calls           : %d" % len(ledger.armed_events))
        print("     traces opened              : %d" % len(ledger.traces))
        if ledger.miss_events:
            print("     -- outcome distribution --")
            for key, count in histogram(
                event["outcome"] for event in ledger.miss_events
            ):
                print("       %-24s = %d" % (str(key), count))
        if ledger.broken_events:
            bump = sum(1 for e in ledger.broken_events if e["bump_off"])
            print("     broken tackle: bump_off=%d plain=%d"
                  % (bump, len(ledger.broken_events) - bump))
            for event in ledger.broken_events[:MAX_EVENTS]:
                print("       f=%-6s bump_off=%-5s tacklers=%s timer=%s"
                      % (event["frame"], event["bump_off"],
                         event["tackler_ids"], event["timer"]))

    primary = results[0]
    ledger = primary["ledger"]
    if not ledger.traces:
        banner("VERDICT")
        print("  INCONCLUSIVE: no miss event was captured on any body in this")
        print("  rollout, so the post-miss hierarchy was never exercised.")
        print("")
        print("  Report written to %s" % REPORT_PATH)
        return

    # ══ B. THE FRAME VERDICTS ══
    sub("B. THE FRAME VERDICTS — why the beaten body did or did not pursue")
    all_codes = {}
    all_stages = {}
    all_sources = {}
    latched = 0
    stale_frames = 0
    total = 0
    for trace in ledger.traces:
        for _frame, row in ledger.rows.get(trace["index"]) or []:
            total += 1
            code, _detail = classify(row)
            all_codes[code] = all_codes.get(code, 0) + 1
            write = latest_write(row)
            key = write["stage"] if write else "(no write)"
            all_stages[key] = all_stages.get(key, 0) + 1
            source_key = str(row.get("source"))
            all_sources[source_key] = all_sources.get(source_key, 0) + 1
            if row.get("commit") or row.get("pursuit_commit"):
                latched += 1
            if row.get("seed_stale"):
                stale_frames += 1
    print("  traced body-frames            : %d" % total)
    print("  -- verdict histogram (leg %s) --" % ledger.leg)
    for code, count in sorted(all_codes.items(), key=lambda kv: -kv[1]):
        print("    %-44s = %5d  (%.1f%%)"
              % (code, count, 100.0 * count / max(1, total)))
    print("  -- the stage that made the LAST target write --")
    for stage, count in sorted(all_stages.items(), key=lambda kv: -kv[1]):
        print("    %-44s = %5d" % (stage, count))
    print("  -- final target_source --")
    for source, count in sorted(all_sources.items(), key=lambda kv: -kv[1])[:16]:
        print("    %-44s = %5d" % (source, count))
    print("")
    print("  frames with a commit/pursuit LATCH held : %d / %d"
          % (latched, total))
    print("  frames with a STALE SEED               : %d / %d"
          % (stale_frames, total))

    # The task's subject is a BEATEN defender, so the verdicts are also split
    # by the kind of miss that opened the trace: a broken tackle / bump-off
    # (the carrier beat the tackler outright) versus a pre-tackle miss outcome
    # applied during ordinary play (the common case in raw counts).
    by_kind = {}
    for trace in ledger.traces:
        for _frame, row in ledger.rows.get(trace["index"]) or []:
            code, _detail = classify(row)
            bucket = by_kind.setdefault(trace["kind"], {})
            bucket[code] = bucket.get(code, 0) + 1
    print("")
    print("  -- verdict histogram per trace KIND --")
    for kind, codes in sorted(
        by_kind.items(), key=lambda kv: -sum(kv[1].values())
    ):
        kind_total = sum(codes.values())
        kind_traces = sum(1 for t in ledger.traces if t["kind"] == kind)
        print("    %s (%d frames over %d traces):"
              % (kind, kind_total, kind_traces))
        for code, count in sorted(codes.items(), key=lambda kv: -kv[1])[:8]:
            print("      %-40s = %5d  (%.1f%%)"
                  % (code, count, 100.0 * count / max(1, kind_total)))

    # ══ C. THE PER-FRAME LEDGER ══
    sub("C. THE PER-FRAME LEDGER — the final winning decision/target, every frame")
    for trace in ledger.traces[:MAX_EVENTS]:
        rows = ledger.rows.get(trace["index"]) or []
        print("")
        print("  ═══════════════════════════════════════════════════════════")
        print("  TRACE %d — pid=%s kind=%s outcome=%s event_frame=%d "
              "(repeats=%d)"
              % (trace["index"], trace["pid"], trace["kind"],
                 trace["outcome"], trace["event_frame"], trace["repeats"]))
        print("  ═══════════════════════════════════════════════════════════")
        if not rows:
            print("    (no rows sampled for this trace)")
            continue
        delay, first, last = trace_delay(rows)
        print("    frames-from-miss to first CLOSING frame : %s"
              % ("none within the window" if delay is None else delay))
        print("    distance to carrier  first -> last      : %s -> %s m"
              % (_f(first, 7, 2), _f(last, 7, 2)))
        print("    (a pursuit point = a target within %.1f m of bc.x;"
              " 'last' = the stage that made the frame's last write)"
              % CLOSE_M)
        for frame, row in rows:
            print_row_line(frame, row)

    # ══ D. THE NAMED OVERRIDERS ══
    sub("D. THE NAMED OVERRIDERS — when a chase point existed and LOST")
    lost_pairs = {}
    won_pairs = {}
    for trace in ledger.traces:
        for _frame, row in ledger.rows.get(trace["index"]) or []:
            prop = chase_proposal_x(row)
            winner = row.get("band_winner_x")
            if isinstance(winner, (tuple, list)) and len(winner) >= 2:
                winner_src, winner_prio = str(winner[0]), int(winner[1])
            else:
                winner_src, winner_prio = None, None
            if prop is None:
                continue
            if winner_src is None:
                continue
            if winner_src == prop[0] or (winner_prio or 0) < prop[1]:
                key = "%s@%d" % (prop[0], prop[1])
                won_pairs[key] = won_pairs.get(key, 0) + 1
            else:
                key = "%s@%d  <-  %s@%d" % (
                    prop[0], prop[1], winner_src, winner_prio
                )
                lost_pairs[key] = lost_pairs.get(key, 0) + 1
    print("  -- chase points that WON the X axis --")
    if won_pairs:
        for key, count in sorted(won_pairs.items(), key=lambda kv: -kv[1])[:16]:
            print("    %-52s = %5d" % (key, count))
    else:
        print("    (none: no chase point ever won the X axis)")
    print("  -- chase points that LOST the X axis (loser <- winner) --")
    if lost_pairs:
        for key, count in sorted(lost_pairs.items(), key=lambda kv: -kv[1])[:20]:
            print("    %-52s = %5d" % (key, count))
    else:
        print("    (none: every chase point either won, or none was proposed)")

    # ══ E. DELAY TO RESUMED PURSUIT ══
    sub("E. DELAY TO RESUMED PURSUIT — per trace")
    print("  %-7s %-7s %-15s %-12s %-10s %s"
          % ("trace", "pid", "kind", "event_frame", "delay",
             "modal verdict before closure"))
    for trace in ledger.traces:
        rows = ledger.rows.get(trace["index"]) or []
        if not rows:
            continue
        delay, _first, _last = trace_delay(rows)
        codes = [classify(row)[0] for _f, row in rows]
        before = codes if delay is None else codes[:max(1, delay)]
        modal = histogram(before, 1)
        print("  %-7s %-7s %-15s %-12s %-10s %s"
              % (trace["index"], trace["pid"], trace["kind"],
                 trace["event_frame"],
                 "NEVER" if delay is None else delay,
                 modal[0][0] if modal else "-"))

    # ══ F. THE A/B ══
    if len(results) > 1:
        sub("F. THE A/B — the same rollout with one lever moved")
        print("  %-12s %-8s %-8s %-10s %-13s %-12s %s"
              % ("leg", "traces", "misses", "never-res", "median delay",
                 "mean delay", "mean dist first -> last"))
        for res in results:
            metrics = leg_metrics(res)
            firsts = [d for d in metrics["dist_first"] if d is not None]
            lasts = [d for d in metrics["dist_last"] if d is not None]
            pair = "%.2f -> %.2f" % (_mean(firsts) or 0.0,
                                     _mean(lasts) or 0.0)
            print("  %-12s %-8d %-8d %-10d %-13s %-12s %s"
                  % (metrics["leg"], metrics["traces"], metrics["misses"],
                     metrics["never"],
                     _f(_median(metrics["delays"]), 9, 0),
                     _f(_mean(metrics["delays"]), 9, 1), pair))
        print("")
        print("  -- verdict histogram per leg --")
        for res in results:
            metrics = leg_metrics(res)
            print("    %s:" % metrics["leg"])
            for code, count in sorted(metrics["codes"].items(),
                                      key=lambda kv: -kv[1])[:8]:
                print("      %-42s = %5d" % (code, count))

    # ══ VERDICT ══
    banner("VERDICT — measured, not inferred")
    metrics = leg_metrics(primary)
    print("  leg                            : %s" % ledger.leg)
    print("  traced body-frames             : %d" % total)
    print("  traces (misses on a body)      : %d" % len(ledger.traces))
    print("  traces that NEVER closed       : %d" % metrics["never"])
    print("  median frames to close         : %s"
          % _f(_median(metrics["delays"]), 6, 1))
    print("")
    print("  dominant per-frame verdicts:")
    for code, count in sorted(all_codes.items(), key=lambda kv: -kv[1])[:6]:
        print("    %-44s = %5d" % (code, count))
    print("")
    overridden = sum(
        count for code, count in all_codes.items()
        if code.startswith("OVERRIDDEN_BY:")
    )
    chase_live = all_codes.get("PURSUIT_LIVE", 0)
    no_intent = all_codes.get("NO_PURSUIT_INTENT", 0)
    print("  pursued live                    : %5d" % chase_live)
    print("  a chase point existed but LOST  : %5d" % overridden)
    print("  no pursuit point proposed       : %5d" % no_intent)
    print("")
    if overridden > max(chase_live, 1):
        print("  ANSWER: the chase IS generated and is then OVERRIDDEN in the")
        print("  writer-priority bands — see section D for the winning writers")
        print("  and their tiers.")
    elif chase_live == 0 and no_intent > overridden:
        print("  ANSWER: no pursuit point is being PROPOSED for the beaten body")
        print("  in the first place — the miss teardown / eligibility gates, and")
        print("  not the arbitration order, are what delay the chase.")
    else:
        print("  ANSWER: the chase is generated and largely survives; the")
        print("  residual delay is bounded by the reasons in sections B and E.")
    print("")
    print("  Report written to %s" % REPORT_PATH)


# ═════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════

def main():
    global _REPORT
    real = sys.stdout

    results = []
    for leg in LEGS:
        _devnull = open(os.devnull, "w", encoding="utf-8")
        sys.stdout = _devnull
        try:
            res = run_rollout(leg=leg, verbose=True)
        finally:
            sys.stdout = real
            try:
                _devnull.close()
            except Exception:
                pass
        results.append(res)

    _REPORT = open(REPORT_PATH, "w", encoding="utf-8")
    sys.stdout = _tee()
    try:
        report(results)
    finally:
        sys.stdout = real
        try:
            _REPORT.flush()
            _REPORT.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
