"""probe_fullback_emergency_lifetime.py — EMERGENCY-CHASE LIFETIME LEDGER.

THE QUESTION
------------
A tagged fullback (``fullback_role_active``) is beaten by the ball carrier.
Emergency chase engages — and then terminates while the carrier is still a
live threat.  WHAT EXACTLY TERMINATES IT?

WHAT IT LOGS — THE MINIMAL SET
------------------------------
Only these six surfaces, and a frame is printed ONLY when one of them CHANGES
(entry and release rows are always printed):

    emergency_active     ``fullback_role.fullback_emergency_defender_active``
                         — the admission gate for the whole emergency path
    break_kind           ``classify_fullback_break`` -> DEPTH / LATERAL / NONE
    can_acquire_tackle   the tackle-authority flag (one gate disjunct)
    tackle_role          the lane / pre-tackle authority stamp
    movement_state       MovementState name (CHASE / DRIFT / RECOVER / ...)
    release_cause        COMPUTED on the frame the gate goes active -> inactive

Every release also carries the FULL GATE VECTOR — each disjunct of the gate
evaluated separately — so the row NAMES the false disjunct instead of
inferring it, plus the supporting numbers (``bc.x``, the DEPTH reference
``line_anchor_x + 1.0``, the sweeper anchor, ``ruck_x``, 2D distance to the
carrier, lane error, phase, OPE sub-phase, engagement stamps).

THE RELEASE CAUSES (attribution order, first match wins)
--------------------------------------------------------
    R1_PHASE_RELEASE      the OPE sub-phase reached RELEASE/PTB/POST_RUCK, so
                          the RELEASE-entry hand-back ran
                          (``reset_fullback_emergency_engagement`` drops
                          ``_fb_emergency_engaged`` and stamps DRIFT)
    R2_TAG_LOST           ``fullback_role_active`` left the tracked body
    R3_POST_MISS_FREEZE   the actor's OWN missed-tackle freeze closed the gate
                          (``freeze_timer > 0`` / ``frozen``) — a gate disjunct
                          went false while the carrier was still standing
    R3_TACKLE_ELIGIBILITY_CLEARED
                          ``can_acquire_tackle`` went True -> False
    R4_ANCHOR_RELOCK      ``ruck_x`` / ``anchor_x`` (or the lock state) changed
                          between frames, i.e. the anchor was re-derived
    R5_BREAK_NONE         ``classify_fullback_break`` went NONE (the carrier
                          is neither past ``line_anchor + 1.0`` nor outside the
                          sweeper's channel inside 2D reach) — the gate's own
                          definition stopped calling it a break
    R6_UNCLASSIFIED       active -> inactive with no above signal (reported
                          honestly, never guessed at)

METHOD
------
Read-only.  The spies WRAP engine callables and call the real function
UNCHANGED:

    pretackle_machine.apply_outcome        — records each missed tackle
    OpenPlayEngine._arm_tackler_role       — records TES arming (context)
    OpenPlayEngine._prepare_release        — records the RELEASE hand-back frame
    anchor_authority.reset_anchor_authority— records the anchor re-lock frame

The per-frame row is sampled AFTER the engine has finished the frame, so every
printed value is the frame's FINAL applied state.

THREAT-LIVENESS TEST (bounded to the release frame)
---------------------------------------------------
The question is about a release while the carrier "remains a live threat".
That is MEASURED: for every release, ``threat_at_release`` asks whether the
carrier had been CLEAN_ENGAGEd at or before that frame (``made_before``) and,
if not, how many frames later the eventual tackle landed.  A release with
``made_before False`` is a termination BEFORE the threat was neutralised.

Run:    python probe_fullback_emergency_lifetime.py
Report: tmp_fullback_emergency_lifetime.txt
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import random
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

import pygame

pygame.display.init()
pygame.display.set_mode((1, 1), pygame.NOFRAME)

import world.open_play_engine as ope_mod

# The engine's own per-frame physics chatter would drown the report.
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

# The miss writer under measurement.
from game.defence.defence_lvl_1 import pretackle_machine as pm

# The emergency gate and its single-source geometry helpers.
from game.defence.defence_lvl_1 import fullback_role as fbr
from game.defence.defence_lvl_1 import anchor_authority as aa


# ── ROLLOUT CONFIG ───────────────────────────────────────────────────
MAX_FRAMES = int(os.environ.get("RLM_FBEL_FRAMES", "6000"))
SEED = int(os.environ.get("RLM_FBEL_SEED", "20260827"))
DT = 1.0 / 60.0

# Frames traced after each miss.  The first full run (180-frame horizon)
# measured lifetime mean 81.7 / median 91 / max 126 frames but left 79 of
# 182 windows still active at the window edge, so the horizon is widened.
WINDOW_FRAMES = int(os.environ.get("RLM_FBEL_WINDOW", "300"))

# Cap on events reported in detail.
MAX_EVENTS_REPORTED = int(os.environ.get("RLM_FBEL_EVENTS", "6"))

# Per-cause cap on release rows printed in section C.  A rollout can produce
# hundreds of releases that share one cause; printing them all buries the rows
# that differ, so the rows are grouped by cause and capped, with full counts.
MAX_ROWS_PER_CAUSE = int(os.environ.get("RLM_FBEL_ROWS", "3"))

REPORT_PATH = os.environ.get(
    "RLM_FBEL_REPORT", "tmp_fullback_emergency_lifetime.txt"
)

# The only outcome that completes a tackle; everything else is a miss.
CLEAN_ENGAGE = "CLEAN_ENGAGE"

# The five SAMPLED surfaces, in report order.  ``release_cause`` is computed
# on the transition, not sampled.
TRACKED_FIELDS = (
    "emergency_active",
    "break_kind",
    "can_acquire_tackle",
    "tackle_role",
    "movement_state",
)

# Phases in which the emergency path is even meaningful.  A release onto one
# of these is R1 by the engine's own phase machine.
POST_TACKLE_SUBPHASES = frozenset({
    "RELEASE",
    "PTB",
    "PTB_EXEC",
    "POST_RUCK",
})

BREAK_DEPTH = fbr.FB_BREAK_DEPTH
BREAK_LATERAL = fbr.FB_BREAK_LATERAL
BREAK_NONE = fbr.FB_BREAK_NONE

# The margin the DEPTH break test uses (mirrors carrier_past_defensive_line's
# default), printed on every release row so that test is auditable.
FB_LINE_BREAK_MARGIN_M = float(fbr.FB_LINE_BREAK_MARGIN_M)


# ═════════════════════════════════════════════════════════════════════
# SMALL HELPERS
# ═════════════════════════════════════════════════════════════════════

def name_of(obj):
    """``obj.name`` when present, else ``str(obj)``, else None."""
    if obj is None:
        return None
    return getattr(obj, "name", None) or str(obj)


def pid_of(p):
    return getattr(p, "player_id", None)


def defending_team(world):
    return list(getattr(world, "defending_team", None) or [])


def find_body(world, pid):
    """The defending-team body with *pid* (None when it is gone)."""
    if pid is None:
        return None
    for d in defending_team(world):
        if pid_of(d) == pid:
            return d
    return None


def ope_subphase(open_play):
    """The OPEN_PLAY engine's own sub-phase name (RELEASE / TACKLE_DRIVE / ...)."""
    if open_play is None:
        return None
    return name_of(getattr(open_play, "_phase", None))


def fnum(value, default=0.0):
    """Float-coerce an attribute without raising."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def gate_context(world, body, bc, open_play):
    """Every value needed to explain the gate, from the SINGLE-SOURCE helpers.

    ``line_anchor_x``, ``compute_fullback_anchor`` and
    ``classify_fullback_break`` are the very functions the gate itself calls,
    so the logged break classification can never disagree with the engine's.
    """
    line_x = fbr.line_anchor_x(world)
    anchor_x = None
    anchor_y = None
    kind = BREAK_NONE
    if bc is not None:
        anchor_x, anchor_y = fbr.compute_fullback_anchor(bc, line_x)
        if body is not None:
            kind = fbr.classify_fullback_break(bc, body, line_x, anchor_y)

    body_x = fnum(getattr(body, "x", 0.0)) if body is not None else None
    body_y = fnum(getattr(body, "y", 0.0)) if body is not None else None
    bc_x = fnum(getattr(bc, "x", 0.0)) if bc is not None else None
    bc_y = fnum(getattr(bc, "y", 0.0)) if bc is not None else None
    dist = None
    lane_err = None
    if body_x is not None and bc_x is not None:
        dist = ((body_x - bc_x) ** 2 + (body_y - bc_y) ** 2) ** 0.5
        lane_err = abs(bc_y - body_y)

    return {
        "bc_x": bc_x,
        "bc_y": bc_y,
        "body_x": body_x,
        "body_y": body_y,
        "dist_to_bc": dist,
        "lane_error": lane_err,
        "line_anchor_x": line_x,
        "sweeper_anchor_x": anchor_x,
        "sweeper_anchor_y": anchor_y,
        "ruck_x": fnum(getattr(world, "ruck_x_m", None), None),
        "anchor_locked": bool(getattr(world, "anchor_locked", False)),
        "tes_active": bool(getattr(world, "tes_active", False)),
        "subphase": ope_subphase(open_play),
        "phase": name_of(getattr(world, "phase", None)),
    }


# ═════════════════════════════════════════════════════════════════════
# THE LEDGER
# ═════════════════════════════════════════════════════════════════════

class EmergencyLedger:
    """Records miss events, per-frame gate samples and the release causes."""

    def __init__(self):
        self.frame = 0
        self.miss_events = []        # every non-engage outcome applied
        self.engage_events = []      # CLEAN_ENGAGE applied to any body
        self.armed_events = []       # TES arming (context)
        self.release_handbacks = []  # frames the RELEASE hand-back ran
        self.anchor_relocks = []     # frames the anchor authority was reset
        self.traces = {}             # event index -> ordered rows
        self.frames = {}             # event index -> per-frame row list
        self.frames_stepped = 0
        self._active = []            # [(event index, until_frame)]
        self._prev = {}              # event index -> previous sample
        self._tracked = {}           # event index -> tracked pid
        self._carrier = {}           # event index -> carrier pid
        self._miss_frame = {}        # event index -> miss frame

    # ── frame boundary ───────────────────────────────────────────────
    def begin_frame(self, world):
        self.frame = int(getattr(world, "frame_count", 0) or 0)

    # ── event recorders ──────────────────────────────────────────────
    def note_outcome(self, defender, outcome, bc):
        """One ``apply_outcome`` call: a miss, or a completed tackle."""
        entry = {
            "frame": self.frame,
            "pid": pid_of(defender),
            "outcome": outcome,
            "tagged": bool(getattr(defender, "fullback_role_active", False)),
        }
        if outcome == CLEAN_ENGAGE:
            entry["bc_pid"] = pid_of(bc)
            self.engage_events.append(entry)
        else:
            self.miss_events.append(entry)

    def note_armed(self, d, role_name):
        self.armed_events.append({
            "frame": self.frame,
            "pid": pid_of(d),
            "role_name": role_name,
            "tagged": bool(getattr(d, "fullback_role_active", False)),
        })

    def note_release_handback(self, world):
        """The OPE RELEASE-entry hand-back ran this frame."""
        engaged = [
            pid_of(p) for p in defending_team(world)
            if fbr.is_fullback_emergency_engaged(p)
        ]
        self.release_handbacks.append({
            "frame": self.frame,
            "engaged_pids": engaged,
        })

    def note_anchor_relock(self, world):
        """The anchor authority was reset (new ruck / PTB / kickoff)."""
        self.anchor_relocks.append({
            "frame": self.frame,
            "ruck_x": fnum(getattr(world, "ruck_x_m", None), None),
        })

    # ── windows ──────────────────────────────────────────────────────
    def open_trace(self, index, tracked_pid, carrier_pid, miss_frame):
        self._tracked[index] = tracked_pid
        self._carrier[index] = carrier_pid
        self._miss_frame[index] = miss_frame
        self._prev.pop(index, None)
        self.traces[index] = []
        self.frames[index] = []
        self._active.append((index, self.frame + WINDOW_FRAMES))

    def close_due(self):
        self._active = [
            (i, until) for (i, until) in self._active if self.frame <= until
        ]

    # ── the sample ───────────────────────────────────────────────────
    def sample(self, world, open_play):
        """Diff each open window's gate sample against the previous frame's."""
        bc = getattr(world, "ball_carrier", None)

        for index, _until in list(self._active):
            body = find_body(world, self._tracked.get(index))
            ctx = gate_context(world, body, bc, open_play)

            sample = {
                "emergency_active": bool(
                    body is not None
                    and fbr.fullback_emergency_defender_active(world, body, bc)
                ),
                "break_kind": (
                    fbr.classify_fullback_break(
                        bc, body, ctx["line_anchor_x"], ctx["sweeper_anchor_y"]
                    )
                    if (bc is not None and body is not None) else BREAK_NONE
                ),
                "can_acquire_tackle": bool(
                    getattr(body, "can_acquire_tackle", False)
                ),
                "tackle_role": getattr(body, "tackle_role", None),
                "movement_state": name_of(getattr(body, "movement_state", None)),
                # supporting values, never change-tracked
                "tag": bool(getattr(body, "fullback_role_active", False)),
                "frozen": bool(getattr(body, "frozen", False)),
                "freeze_timer": fnum(getattr(body, "freeze_timer", 0.0)),
                "engaged": bool(
                    body is not None and fbr.is_fullback_emergency_engaged(body)
                ),
                "commit": bool(getattr(body, "_tackle_commit_active", False)),
                "stance": bool(getattr(body, "pre_tackle_stance", False)),
                "tackle_state": bool(getattr(body, "tackle_state_active", False)),
                "target_x": getattr(body, "target_x", None),
                "target_source": getattr(body, "target_source", None),
                "move_speed": getattr(body, "move_speed", None),
                "ctx": ctx,
                # THE GATE VECTOR — every disjunct of
                # fullback_emergency_defender_active evaluated separately, so a
                # release row NAMES the false one instead of inferring it.
                "gate_vector": {
                    "candidacy": bool(fbr.FULLBACK_TACKLE_CANDIDACY),
                    "tag": bool(getattr(body, "fullback_role_active", False)),
                    "can_acquire_tackle": bool(
                        getattr(body, "can_acquire_tackle", False)
                    ),
                    "not_frozen": not bool(getattr(body, "frozen", False)),
                    "freeze_timer_zero": not (
                        fnum(getattr(body, "freeze_timer", 0.0)) > 0.0
                    ),
                    "break": bool(
                        body is not None
                        and (
                            fbr.line_break_active(world, bc)
                            or fbr.fullback_lateral_break_active(world, body, bc)
                        )
                    ),
                },
            }
            prev = self._prev.get(index)
            self._prev[index] = sample

            if prev is None:
                self._record(index, self.frame, "(baseline)", None, sample)
                self.frames[index].append(
                    {"frame": self.frame, "fields": [], "special": "baseline"}
                )
                continue

            changed = [
                f for f in TRACKED_FIELDS
                if prev.get(f) != sample.get(f)
            ]
            was_active = bool(prev.get("emergency_active"))
            now_active = bool(sample.get("emergency_active"))

            if changed:
                for field in changed:
                    self._record(
                        index, self.frame, field,
                        prev.get(field), sample.get(field),
                    )

            special = None
            if now_active and not was_active:
                special = "ENTRY"
                self._record(index, self.frame, "(entered)", None, sample)
            elif was_active and not now_active:
                special = "RELEASE"
                cause = self.classify_release(prev, sample)
                self._record(
                    index, self.frame, "(released)", cause, sample,
                    cause=cause,
                )

            self.frames[index].append({
                "frame": self.frame,
                "fields": changed,
                "special": special,
            })

        self.close_due()

    def _record(self, index, frame, field, old, new, cause=None):
        self.traces[index].append({
            "frame": frame,
            "field": field,
            "old": old,
            "new": new,
            "cause": cause,
        })

    # ── release attribution ──────────────────────────────────────────
    def classify_release(self, prev, sample):
        """Name the EXACT condition that terminated emergency chase.

        Attribution order is deliberate and documented: the engine's own
        phase machine first (R1), then a lost tag (R2), then the actor's own
        missed-tackle freeze (R3a) or a cleared eligibility flag (R3b), then
        an anchor re-derivation (R4), then the break classification itself
        going NONE (R5).  Anything unexplained is reported as R6 rather than
        guessed at.
        """
        ctx = sample.get("ctx") or {}
        pctx = prev.get("ctx") or {}
        subphase = ctx.get("subphase")
        phase = ctx.get("phase")

        if subphase in POST_TACKLE_SUBPHASES or phase in POST_TACKLE_SUBPHASES:
            return "R1_PHASE_RELEASE"
        if prev.get("tag") and not sample.get("tag"):
            return "R2_TAG_LOST"
        if sample.get("frozen") or sample.get("freeze_timer", 0.0) > 0.0:
            return "R3_POST_MISS_FREEZE"
        if prev.get("can_acquire_tackle") and not sample.get("can_acquire_tackle"):
            return "R3_TACKLE_ELIGIBILITY_CLEARED"
        if (
            ctx.get("ruck_x") != pctx.get("ruck_x")
            or ctx.get("sweeper_anchor_x") != pctx.get("sweeper_anchor_x")
            or ctx.get("anchor_locked") != pctx.get("anchor_locked")
        ):
            return "R4_ANCHOR_RELOCK"
        if sample.get("break_kind") == BREAK_NONE:
            return "R5_BREAK_NONE"
        return "R6_UNCLASSIFIED"

    # ── threat liveness ──────────────────────────────────────────────
    def threat_at_release(self, index, release_frame):
        """(made_before, next_engage_frame) for the tracked carrier.

        ``made_before`` is True when the carrier was CLEAN_ENGAGEd at or
        BEFORE *release_frame*, i.e. the release followed a completed tackle
        (the threat WAS neutralised).  False means the gate closed while the
        carrier was still standing — a termination BEFORE neutralisation,
        which is the case this investigation is about.  ``next_engage_frame``
        places the release relative to the tackle that eventually landed
        (None when none ever did).
        """
        miss_frame = self._miss_frame.get(index)
        carrier_pid = self._carrier.get(index)
        if miss_frame is None or carrier_pid is None:
            return None, None
        made_before = False
        next_frame = None
        for ev in self.engage_events:
            if ev.get("bc_pid") != carrier_pid or ev["frame"] < miss_frame:
                continue
            if ev["frame"] <= release_frame:
                made_before = True
            elif next_frame is None or ev["frame"] < next_frame:
                next_frame = ev["frame"]
        return made_before, next_frame
# ═════════════════════════════════════════════════════════════════════
# SPY INSTALLATION
# ═════════════════════════════════════════════════════════════════════

def install_spies(ledger):
    """Wrap the engine callables; every wrapper calls the real function."""
    real_outcome = pm.apply_outcome
    real_arm = OpenPlayEngine._arm_tackler_role
    real_prepare_release = getattr(OpenPlayEngine, "_prepare_release", None)
    real_reset_anchor = aa.reset_anchor_authority

    def outcome(world, defender, bc, tuners, state=None, *a, **kw):
        res = real_outcome(world, defender, bc, tuners, state=state, *a, **kw)
        try:
            ledger.note_outcome(defender, name_of(res), bc)
        except Exception:
            pass
        return res

    def arm(ope_self, d, role_enum, role_name, ctx):
        res = real_arm(ope_self, d, role_enum, role_name, ctx)
        try:
            ledger.note_armed(d, role_name)
        except Exception:
            pass
        return res

    def prepare_release(ope_self, *a, **kw):
        # Record BEFORE the real call: the hand-back drops the engagement
        # marker, so sampling afterwards would report an empty set and hide
        # exactly the fact this spy exists to capture.
        try:
            ledger.note_release_handback(getattr(ope_self, "world", None))
        except Exception:
            pass
        return real_prepare_release(ope_self, *a, **kw)

    def reset_anchor(world, *a, **kw):
        res = real_reset_anchor(world, *a, **kw)
        try:
            ledger.note_anchor_relock(world)
        except Exception:
            pass
        return res

    pm.apply_outcome = outcome
    OpenPlayEngine._arm_tackler_role = arm
    if real_prepare_release is not None:
        OpenPlayEngine._prepare_release = prepare_release
    aa.reset_anchor_authority = reset_anchor
    return {
        "outcome": real_outcome,
        "arm": real_arm,
        "prepare_release": real_prepare_release,
        "reset_anchor": real_reset_anchor,
    }


def uninstall_spies(originals):
    """Restore the real callables (probe hygiene)."""
    pm.apply_outcome = originals["outcome"]
    OpenPlayEngine._arm_tackler_role = originals["arm"]
    if originals["prepare_release"] is not None:
        OpenPlayEngine._prepare_release = originals["prepare_release"]
    aa.reset_anchor_authority = originals["reset_anchor"]


# ═════════════════════════════════════════════════════════════════════
# THE ROLLOUT — the real engine, booted as in the ownership probe
# ═════════════════════════════════════════════════════════════════════

def run_rollout(frames=MAX_FRAMES, seed=SEED, verbose=False):
    """Boot the engine, kick off, step it, return the lifetime ledger."""
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

    ledger = EmergencyLedger()
    originals = install_spies(ledger)
    opened = 0

    try:
        for frame in range(1, frames + 1):
            world.frame_count = frame
            ball.update(DT)
            ledger.begin_frame(world)

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

            # Open a window for every miss recorded during this frame.
            while opened < len(ledger.miss_events):
                ev = ledger.miss_events[opened]
                if ev["tagged"]:
                    ledger.open_trace(
                        opened,
                        ev["pid"],
                        getattr(world, "ball_carrier", None)
                        and pid_of(world.ball_carrier),
                        ev["frame"],
                    )
                opened += 1

            # SAMPLE AFTER the engine has finished the frame.
            ledger.sample(world, open_play)
            ledger.frames_stepped = frame

            if verbose and frame % 1000 == 0:
                print(
                    "    ... frame %d (misses=%d)"
                    % (frame, len(ledger.miss_events)),
                    file=_REAL_STDOUT, flush=True,
                )
    finally:
        uninstall_spies(originals)

    return {"ledger": ledger, "world": world, "open_play": open_play}


# ═════════════════════════════════════════════════════════════════════
# REPORT
# ═════════════════════════════════════════════════════════════════════

_REPORT = None
_REAL_STDOUT = sys.stdout


class _tee:
    """Write to the report file and the real stdout at once."""

    def write(self, s):
        try:
            if _REPORT is not None:
                _REPORT.write(s)
        except Exception:
            pass
        return _REAL_STDOUT.write(s)

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


def histogram(values, limit=24):
    hist = {}
    for v in values:
        hist[v] = hist.get(v, 0) + 1
    return sorted(hist.items(), key=lambda kv: -kv[1])[:limit]


def median(values):
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def fmt(value, places=2):
    if value is None:
        return "--"
    if isinstance(value, float):
        return ("%%.%df" % places) % value
    return str(value)


def render_change(field, old, new):
    return "%-20s %s -> %s" % (field, old, new)


def render_release(cause, sample, release_frame, made_before, next_frame):
    """Evidence-bearing rendering of one release.

    Leads with the FALSE GATE DISJUNCT(S) — read straight off the evaluated
    gate vector, so the terminating condition is named, not inferred.
    """
    ctx = sample.get("ctx") or {}
    gate = sample.get("gate_vector") or {}
    false_disjuncts = [k for k, v in gate.items() if not v]
    if made_before:
        threat = "carrier ALREADY brought to ground at/before this frame"
    elif next_frame is None:
        threat = "THREAT LIVE — carrier NEVER brought to ground after the miss"
    else:
        threat = "THREAT LIVE at release — carrier brought to ground LATER"
    gap = "--" if next_frame is None else str(next_frame - release_frame)
    return (
        "CAUSE=%s | FALSE GATE DISJUNCT(S): %s\n"
        "                        break=%s state=%s can_tackle=%s"
        " tackle_role=%s engaged=%s\n"
        "                        bc_x=%s line_anchor=%s (DEPTH needs bc_x > %s)"
        " sweeper_anchor=%s ruck_x=%s locked=%s\n"
        "                        subphase=%s | dist_bc=%s lane_err=%s\n"
        "                        tag=%s frozen=%s freeze_timer=%s commit=%s"
        " stance=%s tackle_state=%s\n"
        "                        target_x=%s src=%s speed=%s\n"
        "                        THREAT: %s (frames to next engage: %s)"
        % (
            cause,
            (", ".join(false_disjuncts) if false_disjuncts
             else "none (gate was TRUE)"),
            sample.get("break_kind"),
            sample.get("movement_state"),
            sample.get("can_acquire_tackle"),
            sample.get("tackle_role"),
            sample.get("engaged"),
            fmt(ctx.get("bc_x")),
            fmt(ctx.get("line_anchor_x")),
            fmt((ctx.get("line_anchor_x") or 0.0) + FB_LINE_BREAK_MARGIN_M),
            fmt(ctx.get("sweeper_anchor_x")),
            fmt(ctx.get("ruck_x")),
            ctx.get("anchor_locked"),
            ctx.get("subphase"),
            fmt(ctx.get("dist_to_bc")),
            fmt(ctx.get("lane_error")),
            sample.get("tag"),
            sample.get("frozen"),
            fmt(sample.get("freeze_timer")),
            sample.get("commit"),
            sample.get("stance"),
            sample.get("tackle_state"),
            fmt(sample.get("target_x")),
            sample.get("target_source"),
            fmt(sample.get("move_speed")),
            threat,
            gap,
        )
    )


def collect_releases(ledger):
    """[(index, row, made_before, next_engage_frame)] for every release."""
    out = []
    for index in sorted(ledger.traces):
        for row in ledger.traces[index]:
            if row["field"] == "(released)":
                made_before, next_frame = ledger.threat_at_release(
                    index, row["frame"]
                )
                out.append((index, row, made_before, next_frame))
    return out


def is_neutralised(row, made_before):
    """True when the tracked carrier had ALREADY been brought to ground.

    Two independent signals, because a completed tackle does not always land
    as a ``CLEAN_ENGAGE`` on that exact carrier id:

      1. ``made_before`` — the carrier was CLEAN_ENGAGEd at or before the
         release frame (the bounded engage test), or
      2. the release frame's OPE sub-phase is a POST-TACKLE phase
         (RELEASE / PTB / POST_RUCK).  Reaching RELEASE *is* the engine
         declaring the tackle cycle finished, which is why the phase-release
         bucket is the legitimate end of a completed tackle.

    A release that satisfies NEITHER is a termination while the carrier was
    still standing — the case this investigation is about.
    """
    if made_before:
        return True
    subphase = ((row.get("new") or {}).get("ctx") or {}).get("subphase")
    return subphase in POST_TACKLE_SUBPHASES


def release_distance(row):
    """The 2D defender-to-carrier distance recorded on a release row."""
    ctx = (row.get("new") or {}).get("ctx") or {}
    return ctx.get("dist_to_bc")


def window_lifetimes(ledger):
    """[(index, start_frame, end_frame, lifetime, release_cause)] per window."""
    out = []
    for index, rows in ledger.traces.items():
        entries = [r["frame"] for r in rows if r["field"] == "(entered)"]
        releases = [
            (r["frame"], r.get("cause"))
            for r in rows if r["field"] == "(released)"
        ]
        miss_frame = ledger._miss_frame.get(index)
        start = entries[0] if entries else miss_frame
        for (rel_frame, cause) in releases:
            anchor = None
            for e in entries:
                if e <= rel_frame:
                    anchor = e
            if anchor is None:
                anchor = miss_frame
            out.append((
                index,
                anchor,
                rel_frame,
                (rel_frame - anchor) if (anchor is not None) else None,
                cause,
            ))
        if not releases:
            out.append((index, start, None, None, None))
    return out


def report(res):
    ledger = res["ledger"]

    banner("FULLBACK EMERGENCY-CHASE LIFETIME — MINIMAL LEDGER")
    print("  seed                        : %d" % SEED)
    print("  frames stepped              : %d" % ledger.frames_stepped)
    print("  dt                          : %.6f s" % DT)
    print("  window traced per event     : %d frames" % WINDOW_FRAMES)
    print("  fields tracked              : %s" % ", ".join(TRACKED_FIELDS))
    print("  plus                        : release_cause, the full gate"
          " vector, and the bounded threat test")

    # ── A. COVERAGE ──
    sub("A. COVERAGE — was the question exercised, and on what evidence?")
    tagged_misses = [e for e in ledger.miss_events if e["tagged"]]
    tagged_armed = [e for e in ledger.armed_events if e["tagged"]]
    engaged_handbacks = [
        h for h in ledger.release_handbacks if h["engaged_pids"]
    ]
    print("  apply_outcome calls (non-engage) : %d"
          % len(ledger.miss_events))
    print("  of those, on a TAGGED fullback   : %d" % len(tagged_misses))
    print("  CLEAN_ENGAGE outcomes recorded   : %d"
          % len(ledger.engage_events))
    print("  TES arming calls                 : %d (tagged: %d)"
          % (len(ledger.armed_events), len(tagged_armed)))
    print("  RELEASE-entry hand-back calls     : %d"
          % len(ledger.release_handbacks))
    print("  ... of those, with an ENGAGED FB  : %d" % len(engaged_handbacks))
    if engaged_handbacks:
        print("  engaged pids at hand-back         : %s"
              % sorted({p for h in engaged_handbacks
                        for p in h["engaged_pids"]}))
    print("  anchor-authority reset calls      : %d"
          % len(ledger.anchor_relocks))
    if ledger.anchor_relocks:
        print("  ruck_x at those resets            : %s"
              % [fmt(r["ruck_x"]) for r in ledger.anchor_relocks[:12]])
    if tagged_misses:
        print("  first tagged miss frame           : %d"
              % tagged_misses[0]["frame"])
        print("  tagged miss pids                  : %s"
              % sorted({e["pid"] for e in tagged_misses}))
        print("  -- miss outcome distribution (tagged only) --")
        for k, v in histogram(e["outcome"] for e in tagged_misses):
            print("    %-24s = %d" % (k, v))
    else:
        print("")
        print("  !! NO miss was applied to a tagged fullback.  The lifetime")
        print("     question was not exercised; nothing is claimed below.")

    # ── B. LIFETIME ──
    life = window_lifetimes(ledger)
    releases = [r for r in life if r[3] is not None]
    open_ended = [r for r in life if r[3] is None]
    lifetimes = [r[3] for r in releases]
    release_records = collect_releases(ledger)
    live_records = [
        r for r in release_records if not is_neutralised(r[1], r[2])
    ]

    sub("B. EMERGENCY-CHASE LIFETIME — entry to release, per window")
    print("  windows traced                     : %d" % len(life))
    print("  windows that reached a RELEASE     : %d" % len(releases))
    print("  windows still active at window end : %d" % len(open_ended))
    entries_total = sum(
        1 for rows in ledger.traces.values()
        for r in rows if r["field"] == "(entered)"
    )
    print("  ENTRY transitions observed         : %d" % entries_total)
    print("  RELEASE transitions observed       : %d" % len(releases))
    if lifetimes:
        print("  lifetime frames (entry->release)   : mean %s  median %s"
              "  min %d  max %d"
              % (fmt(sum(lifetimes) / len(lifetimes), 1),
                 fmt(median(lifetimes), 1), min(lifetimes), max(lifetimes)))
    if releases:
        print("  -- release-cause distribution --")
        for k, v in histogram(r[4] for r in releases):
            print("    %-30s = %d" % (k, v))
        print("  -- same, split by whether the carrier had ALREADY been"
              " brought to ground --")
        print("     (neutralised = bounded CLEAN_ENGAGE at/before the release"
              " OR the")
        print("      release frame's sub-phase is RELEASE/PTB/POST_RUCK)")
        for cause, n in sorted(
            dict(histogram(r[4] for r in releases)).items(),
            key=lambda kv: -kv[1],
        ):
            recs = [r for r in release_records
                    if r[1].get("cause") == cause]
            live_n = sum(1 for r in recs if not is_neutralised(r[1], r[2]))
            dists = [d for d in (release_distance(r[1]) for r in recs)
                     if d is not None]
            dist_txt = ("dist at release min %s mean %s max %s"
                        % (fmt(min(dists), 2),
                           fmt(sum(dists) / len(dists), 2),
                           fmt(max(dists), 2))) if dists else "dist --"
            print("    %-30s total %3d   still-live %3d   (%-42s)"
                  % (cause, n, live_n, dist_txt))

    # ── C. EVERY RELEASE, WITH ITS EVIDENCE ──
    sub("C. RELEASES BY CAUSE — the exact terminating condition, with numbers")
    if not release_records:
        print("  (no release transition was captured inside the traced windows)")
    by_cause = {}
    for rec in release_records:
        by_cause.setdefault(rec[1].get("cause"), []).append(rec)
    for cause in sorted(by_cause, key=lambda c: -len(by_cause[c])):
        recs = by_cause[cause]
        live_n = sum(1 for r in recs if not is_neutralised(r[1], r[2]))
        dists = [d for d in (release_distance(r[1]) for r in recs)
                 if d is not None]
        print("")
        print("  ── %s : %d release(s), %d of them while the carrier was"
              " STILL LIVE" % (cause, len(recs), live_n))
        if dists:
            print("     dist(defender->carrier) at release: min %s  mean %s"
                  "  max %s" % (fmt(min(dists), 2),
                               fmt(sum(dists) / len(dists), 2),
                               fmt(max(dists), 2)))
        for index, row, made_before, next_frame in recs[:MAX_ROWS_PER_CAUSE]:
            print("")
            print("     EVENT %d — release frame %d" % (index, row["frame"]))
            print("       " + render_release(
                row.get("cause"), row["new"], row["frame"],
                made_before, next_frame,
            ))
            print("       REFINED: carrier_neutralised_at_release=%s"
                  % is_neutralised(row, made_before))

    # ── D. DETAILED WINDOWS ──
    sub("D. DETAILED WINDOWS — change-only trace, first %d tagged events"
        % MAX_EVENTS_REPORTED)
    shown = 0
    for index, ev in enumerate(ledger.miss_events):
        if not ev["tagged"]:
            continue
        if shown >= MAX_EVENTS_REPORTED:
            break
        shown += 1
        rows = ledger.traces.get(index) or []
        print("")
        print("  ═══════════════════════════════════════════════════════════")
        print("  EVENT %d — miss frame %d, pid=%s, outcome=%s"
              % (index, ev["frame"], ev["pid"], ev["outcome"]))
        print("  ═══════════════════════════════════════════════════════════")
        print("  (a frame prints ONLY when a tracked field CHANGED; entry"
              " and release always print)")
        if not rows:
            print("    no rows (window was never sampled)")
            continue
        for row in rows:
            field = row["field"]
            if field == "(baseline)":
                s = row["new"]
                print("    f=%-6s BASELINE  active=%s break=%s can_tackle=%s"
                      " tackle_role=%s state=%s"
                      % (row["frame"], s.get("emergency_active"),
                         s.get("break_kind"), s.get("can_acquire_tackle"),
                         s.get("tackle_role"), s.get("movement_state")))
            elif field == "(entered)":
                s = row["new"]
                print("    f=%-6s ** ENTRY ** active=True break=%s"
                      " can_tackle=%s tackle_role=%s state=%s"
                      % (row["frame"], s.get("break_kind"),
                         s.get("can_acquire_tackle"), s.get("tackle_role"),
                         s.get("movement_state")))
            elif field == "(released)":
                made_before, next_frame = ledger.threat_at_release(
                    index, row["frame"]
                )
                print("    f=%-6s ** RELEASE **" % row["frame"])
                print("            " + render_release(
                    row.get("cause"), row["new"], row["frame"],
                    made_before, next_frame,
                ))
            else:
                print("    f=%-6s CHANGE   %s"
                      % (row["frame"], render_change(field, row["old"],
                                                     row["new"])))

    # ── E. VERDICT ──
    banner("VERDICT — measured, not inferred")
    if not tagged_misses:
        print("  INCONCLUSIVE: no missed tackle was applied to a tagged")
        print("  fullback in this rollout, so emergency-chase lifetime was")
        print("  never exercised.  Nothing is claimed.")
        print("")
        print("  Report written to %s" % REPORT_PATH)
        return

    if not releases:
        print("  NO RELEASE was observed after any tagged miss: emergency")
        print("  chase stayed active for the entire %d-frame window on every"
              % WINDOW_FRAMES)
        print("  traced event, so no gate transition terminated it inside the")
        print("  traced horizon.")
        print("")
        print("  Report written to %s" % REPORT_PATH)
        return

    causes = dict(histogram(r[4] for r in releases))
    live_causes = dict(histogram(r[1].get("cause") for r in live_records))

    print("  traced windows                  = %d" % len(life))
    print("  releases observed               = %d" % len(releases))
    print("  lifetime (entry->release)       = mean %s frames, median %s"
          % (fmt(sum(lifetimes) / len(lifetimes), 1),
             fmt(median(lifetimes), 1)))
    print("")
    print("  ── RELEASE-CAUSE DISTRIBUTION ──")
    for cause, n in sorted(causes.items(), key=lambda kv: -kv[1]):
        print("    %-30s %3d release(s)" % (cause, n))
    print("")
    print("  ── RELEASES BEFORE THE CARRIER WAS NEUTRALISED (live threat) ──")
    print("     (NEITHER a bounded CLEAN_ENGAGE at/before the release frame")
    print("      NOR a post-tackle sub-phase at the release frame)")
    if live_records:
        for cause, n in sorted(live_causes.items(), key=lambda kv: -kv[1]):
            print("    %-30s %3d release(s)" % (cause, n))
        top_live, top_live_n = max(live_causes.items(), key=lambda kv: kv[1])
        print("")
        print("  THE ANSWER: emergency chase terminates while the carrier is")
        print("  still a live threat via %s" % top_live)
        print("  — %d of the %d observed releases, and %d of the %d releases"
              % (top_live_n, len(releases), top_live_n, len(release_records)))
        print("  that fired before neutralisation.  The gate disjunct that is")
        print("  FALSE on those frames is named in each release row above; the")
        print("  values beside it are the frame's final applied state.")
        print("")
        print("  Representative live-threat release rows:")
        for index, row, _made, next_frame in live_records[:3]:
            gap = "--" if next_frame is None else str(next_frame - row["frame"])
            print("    EVENT %d f=%s cause=%s  (tackle landed %s frames later)"
                  % (index, row["frame"], row.get("cause"), gap))
            print("      " + render_release(
                row.get("cause"), row["new"], row["frame"], False, next_frame
            ))
    else:
        print("    none — every release in these windows followed a completed")
        print("    tackle on that carrier, so no termination-before-")
        print("    neutralisation was captured in this rollout.")
    print("")
    print("  Report written to %s" % REPORT_PATH)


# ═════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════

def main():
    global _REPORT
    real = sys.stdout

    # PHASE 1: the rollout, with the engine's own chatter discarded.
    _devnull = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = _devnull
    try:
        res = run_rollout(verbose=True)
    finally:
        sys.stdout = real
        try:
            _devnull.close()
        except Exception:
            pass

    # PHASE 2: the report, into its own clean file and the terminal.
    _REPORT = open(REPORT_PATH, "w", encoding="utf-8")
    sys.stdout = _tee()
    try:
        report(res)
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
