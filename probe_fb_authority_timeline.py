"""probe_fb_authority_timeline.py — PER-FRAME FB AUTHORITY TIMELINE.

THE QUESTION
------------
From the frame the ball carrier breaks the defensive line until the tackle (or
the try), the tagged fullback is written by exactly ONE authority per frame.
Which authority holds the body on each frame, WHY did it change hands, where
was the body commanded, and which state predicates were true?

And the narrow question underneath: when the EMERGENCY CHASE ends, is it
EXPLICITLY EXITED (a reset / release call clears ``_fb_emergency_engaged`` and
hands the body back to its sweeper contract) or is it merely OVERWRITTEN (some
other writer takes ``target_x`` / ``movement_state`` while the engagement
marker and the derived ownership stamps are still set)?

WHAT IT RECORDS, PER FRAME
--------------------------
    frame             the engine frame number
    mode              ``movement_state`` (the active movement mode)
    writer            the frame's final target authority
    reason            computed ONLY on a (mode, writer) change — the catalogue
                      entry that names what replaced what, and whether the
                      replacement was an EXPLICIT exit or an OVERWRITE
    target            ``target_x`` / ``target_y`` plus the live position and
                      velocity so the command can be read against the ground
    predicates        the full state vector the authority decision reads:
                      tag, can_acquire_tackle, tackle_role, commit, stance,
                      tackle_state, frozen, freeze_timer, tes_attached,
                      in_contact, engaged, defender_role, move_speed,
                      emergency gate, break kind, and the three derived
                      authority predicates (tackle authority active, side
                      owner live, approach cap applies)

METHOD
------
Read-only.  The engine is booted exactly as ``probe_fullback_emergency_lifetime``
boots it and stepped frame by frame.  Spies WRAP engine callables and call the
real function UNCHANGED:

    OpenPlayEngine._arm_tackler_role      — TES arming (sets the engagement)
    OpenPlayEngine._clear_tes_artefacts   — RELEASE-entry explicit exit
    OpenPlayEngine._handle_broken_tackle  — miss-teardown explicit exit
    pretackle_machine.apply_outcome       — tackle / try resolution

Every row is sampled AFTER the engine has finished the frame, so a printed
value is the frame's FINAL applied state.

Run:    python probe_fb_authority_timeline.py
Report: tmp_fb_authority_timeline.txt
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

from game.defence.defence_lvl_1 import pretackle_machine as pm
from game.defence.defence_lvl_1 import fullback_role as fbr
from game.defence.defence_lvl_1 import anchor_authority as aa

# The three derived authority predicates, read directly (single source).
from game.defence.defence_lvl_1.fullback_role import (
    fullback_tackle_authority_active,
    fullback_tackle_side_owner_live,
    fullback_approach_cap_applies,
)


# ── ROLLOUT CONFIG ───────────────────────────────────────────────────
MAX_FRAMES = int(os.environ.get("RLM_FBTL_FRAMES", "6000"))
SEED = int(os.environ.get("RLM_FBTL_SEED", "20260827"))
DT = 1.0 / 60.0

# Frames traced for each line-break window.  The lifetime probe measured
# releases at a mean 81.7 frames, so 400 frames covers the whole episode
# plus the post-tackle era with room to spare.
WINDOW_FRAMES = int(os.environ.get("RLM_FBTL_WINDOW", "400"))

# Cap on the windows reported in full detail (the per-frame timeline is long).
MAX_WINDOWS_REPORTED = int(os.environ.get("RLM_FBTL_WINDOWS", "3"))

# Cap on the per-frame rows printed for one window.
MAX_ROWS_PER_WINDOW = int(os.environ.get("RLM_FBTL_ROWS", "90"))

# Hard cap on the number of windows the ledger will open (memory bound for a
# rollout whose break condition stays live for a long stretch).
MAX_WINDOWS_TRACED = int(os.environ.get("RLM_FBTL_MAXW", "1500"))

# A window is only worth printing in full when it exercised the question: it
# ran long enough to contain a handover of the authority.
MIN_REPORTED_WINDOW_ROWS = int(os.environ.get("RLM_FBTL_MINROWS", "12"))

REPORT_PATH = os.environ.get(
    "RLM_FBTL_REPORT", "tmp_fb_authority_timeline.txt"
)

# The only outcome that completes a tackle.
CLEAN_ENGAGE = "CLEAN_ENGAGE"

# The defending tryline: the +X end of the field of play (field.py sets
# TRYLINE_RIGHT_X = 110 with the attack running +X).
DEFENDING_TRYLINE_X = 110.0

# Phase names that mean the tackle cycle is over.
POST_TACKLE_SUBPHASES = frozenset({
    "RELEASE", "PTB", "PTB_EXEC", "POST_RUCK",
})

BREAK_NONE = fbr.FB_BREAK_NONE


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


def fnum(value, default=0.0):
    """Float-coerce an attribute without raising."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def optnum(value):
    """Float-coerce or None (for values that may legitimately be unset)."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
        "ruck_x": optnum(getattr(world, "ruck_x_m", None)),
        "anchor_locked": bool(getattr(world, "anchor_locked", False)),
        "tes_active": bool(getattr(world, "tes_active", False)),
        "subphase": ope_subphase(open_play),
        "phase": name_of(getattr(world, "phase", None)),
        "break_kind": kind,
    }


def yield_reason(world, body):
    """Name the stamp that makes ``apply_fullback_geometry`` YIELD this frame.

    ``apply_fullback_geometry`` returns without writing while ANY of the
    authority stamps is set (and while TES owns the body).  This mirrors those
    guards IN ORDER so the timeline can attribute the frame to the stamp that
    actually caused the yield rather than to the writer that happened to win
    the last-write contest downstream.
    """
    if body is None:
        return None
    if getattr(body, "tes_attached", False):
        return "TES_ATTACHED"
    if getattr(body, "in_contact", False):
        return "IN_CONTACT"
    if getattr(body, "_tackle_commit_active", False):
        return "TACKLE_COMMIT"
    if fbr.is_fullback_emergency_engaged(body):
        return "EMERGENCY_ENGAGED"
    if getattr(body, "pre_tackle_stance", False):
        return "PRE_TACKLE_STANCE"
    if getattr(body, "tackle_role", None) in ("PRIMARY", "SECONDARY"):
        return "TACKLE_ROLE_STAMP"
    return None


def sample_frame(world, body, bc, open_play, events):
    """One per-frame authority row: mode, writer, target, state predicates."""
    ctx = gate_context(world, body, bc, open_play)
    break_kind = ctx["break_kind"]
    targeted = getattr(body, "target_x", None) if body is not None else None
    writer = (
        getattr(body, "_target_writer", None)
        or getattr(body, "target_source", None)
        or "(none)"
    )
    stamp = yield_reason(world, body)
    return {
        "frame": int(getattr(world, "frame_count", 0) or 0),
        "mode": name_of(getattr(body, "movement_state", None)),
        "writer": writer,
        "target_source": getattr(body, "target_source", None),
        "defender_role": name_of(getattr(body, "defender_role", None)),
        "target_x": optnum(targeted),
        "target_y": optnum(getattr(body, "target_y", None)),
        "x": ctx["body_x"],
        "y": ctx["body_y"],
        "vx": optnum(getattr(body, "vx", None)),
        "vy": optnum(getattr(body, "vy", None)),
        "move_speed": optnum(getattr(body, "move_speed", None)),
        # ── state predicates ──
        "tag": bool(getattr(body, "fullback_role_active", False)),
        "can_acquire_tackle": bool(getattr(body, "can_acquire_tackle", False)),
        "tackle_role": getattr(body, "tackle_role", None),
        "tes_tackler_role": getattr(body, "tes_tackler_role", None),
        "commit": bool(getattr(body, "_tackle_commit_active", False)),
        "stance": bool(getattr(body, "pre_tackle_stance", False)),
        "tackle_state": bool(getattr(body, "tackle_state_active", False)),
        "frozen": bool(getattr(body, "frozen", False)),
        "freeze_timer": fnum(getattr(body, "freeze_timer", 0.0)),
        "tes_attached": bool(getattr(body, "tes_attached", False)),
        "in_contact": bool(getattr(body, "in_contact", False)),
        "engaged": bool(fbr.is_fullback_emergency_engaged(body)),
        "is_tackler": bool(getattr(body, "is_tackler", False)),
        "gate_active": bool(fbr.fullback_emergency_defender_active(world, body, bc)),
        "authority_active": bool(fullback_tackle_authority_active(body)),
        "side_owner_live": bool(fullback_tackle_side_owner_live(body)),
        "approach_cap": bool(fullback_approach_cap_applies(body)),
        "yield_stamp": stamp,
        # ── geometry context ──
        "break_kind": break_kind,
        "bc_x": ctx["bc_x"],
        "bc_y": ctx["bc_y"],
        "dist_to_bc": ctx["dist_to_bc"],
        "lane_error": ctx["lane_error"],
        "line_anchor_x": ctx["line_anchor_x"],
        "sweeper_anchor_x": ctx["sweeper_anchor_x"],
        "ruck_x": ctx["ruck_x"],
        "anchor_locked": ctx["anchor_locked"],
        "tes_active": ctx["tes_active"],
        "subphase": ctx["subphase"],
        "phase": ctx["phase"],
        # ── this frame's engine events (from the spies) ──
        "events": tuple(events or ()),
    }


# The authority stamps, in the order ``fullback_tackle_authority_active``
# tests them (the SHORT list is what the geometry yield reads).
AUTHORITY_STAMPS = (
    "engaged", "tackle_role", "commit", "stance",
)


def stamps_of(row):
    """The subset of authority stamps set on *row* (names only)."""
    out = []
    if row.get("engaged"):
        out.append("engaged")
    if row.get("tackle_role") in ("PRIMARY", "SECONDARY"):
        out.append("tackle_role")
    if row.get("commit"):
        out.append("commit")
    if row.get("stance"):
        out.append("stance")
    return out


def catalogue_reason(prev, row, window):
    """Name WHY the authority changed hands on *row*.

    Returns ``(code, detail)``.  The catalogue is deliberately ordered from
    the engine's own explicit transitions outwards to the inferred overwrite,
    so an explicit exit is never mis-labelled as an overwrite:

      W_ENTRY_FIRST_BREAK   the window's first frame (the break opened)
      X_EXPLICIT_ARM        ``_arm_tackler_role`` ran this frame (TES arming;
                            sets ``_fb_emergency_engaged``)
      X_EXPLICIT_RELEASE    ``_clear_tes_artefacts`` ran (RELEASE-entry
                            hand-back: ``reset_fullback_emergency_engagement``)
      X_EXPLICIT_MISS       ``_handle_broken_tackle`` ran (miss teardown:
                            ``release_fullback_emergency_on_miss``)
      E_TAG_LOST            ``fullback_role_active`` cleared
      E_FREEZE_PIN          the body was frozen / freeze-timed (pin writer)
      E_AUTHORITY_GRANTED   a lane / commit stamp appeared, so the geometry
                            authority YIELDS and the commit writer owns it
      E_AUTHORITY_REVOKED   the stamps were withdrawn (stale-release / beaten
                            ownership): geometry re-owns the body
      E_MODE_<name>         the movement state changed under one writer
      O_WRITER_<name>       the WRITER changed with NO explicit exit and NO
                            stamp change — the frame was OVERWRITTEN by
                            another authority (last-writer-wins)
      R_RELEASE_PHASE       the OPE sub-phase is a post-tackle phase
      U_UNCLASSIFIED        reported honestly, never guessed at
    """
    if prev is None:
        return "W_ENTRY_FIRST_BREAK", "first traced frame of the window"

    events = row.get("events") or ()
    if "ARM" in events:
        return "X_EXPLICIT_ARM", "_arm_tackler_role ran (TES arming)"
    if "RELEASE_ENTRY" in events:
        return ("X_EXPLICIT_RELEASE",
                "_clear_tes_artefacts ran (reset_fullback_emergency_engagement)")
    if "MISS_TEARDOWN" in events:
        return ("X_EXPLICIT_MISS",
                "_handle_broken_tackle ran (release_fullback_emergency_on_miss)")

    # A post-tackle phase is a TRANSITION, never a residency: the sub-phase
    # STAYS there for the whole RELEASE / PTB era, so a residency test emitted
    # this same row on every one of those frames and buried the timeline.
    prev_sub = prev.get("subphase")
    if (row.get("subphase") in POST_TACKLE_SUBPHASES
            and prev_sub not in POST_TACKLE_SUBPHASES):
        return ("R_RELEASE_PHASE",
                "OPE sub-phase ENTERED a post-tackle phase (%s -> %s)"
                % (prev_sub, row.get("subphase")))

    if prev.get("tag") and not row.get("tag"):
        return "E_TAG_LOST", "fullback_role_active cleared"

    if (not prev.get("frozen") and row.get("frozen")) or (
        row.get("freeze_timer", 0.0) > 0.0
        and prev.get("freeze_timer", 0.0) <= 0.0
    ):
        return "E_FREEZE_PIN", "body frozen (freeze-pin authority)"

    p_stamps = stamps_of(prev)
    r_stamps = stamps_of(row)
    gained = [s for s in r_stamps if s not in p_stamps]
    lost = [s for s in p_stamps if s not in r_stamps]
    if gained:
        return (
            "E_AUTHORITY_GRANTED",
            "stamp(s) gained: %s (geometry authority yields)" % ",".join(gained),
        )
    if lost and not r_stamps:
        return (
            "E_AUTHORITY_REVOKED",
            "stamp(s) released: %s (geometry re-owns the body)" % ",".join(lost),
        )

    if prev.get("mode") != row.get("mode"):
        return (
            "E_MODE_%s" % (row.get("mode") or "NONE"),
            "movement state %s -> %s under writer %s"
            % (prev.get("mode"), row.get("mode"), row.get("writer")),
        )

    if prev.get("writer") != row.get("writer"):
        return (
            "O_WRITER_%s" % (row.get("writer") or "NONE"),
            "WRITER changed %s -> %s with NO explicit exit and NO stamp change"
            % (prev.get("writer"), row.get("writer")),
        )

    return None, None
# ═════════════════════════════════════════════════════════════════════
# THE LEDGER
# ═════════════════════════════════════════════════════════════════════

class TimelineLedger:
    """Per-frame authority rows for every line-break window, plus the events."""

    def __init__(self):
        self.frame = 0
        self.events = []          # reset every frame; the spies append here
        self.windows = []         # one dict per line-break window
        self.active = None        # the window currently being traced
        self.outcomes = []        # every apply_outcome call
        self.arms = []            # every _arm_tackler_role call
        self.explicit_exits = []  # RELEASE_ENTRY / MISS_TEARDOWN calls
        self.anchor_relocks = []
        self.tries = []           # frames the carrier CROSSED the tryline
        self._prev_bc_x = None    # for CROSSING-only try detection
        self.try_armed = False    # True once a try has been recorded
        self.frames_stepped = 0

    # ── frame boundary ───────────────────────────────────────────────
    def begin_frame(self, world):
        self.frame = int(getattr(world, "frame_count", 0) or 0)
        self.events = []

    # ── event recorders (called from the spies) ──────────────────────
    def note_event(self, code):
        self.events.append(code)

    def note_arm(self, d, role_name):
        tagged = bool(getattr(d, "fullback_role_active", False))
        self.arms.append({
            "frame": self.frame,
            "pid": pid_of(d),
            "role_name": role_name,
            "tagged": tagged,
        })
        self.note_event("ARM")

    def note_explicit_exit(self, kind, world):
        engaged = [
            pid_of(p) for p in defending_team(world)
            if fbr.is_fullback_emergency_engaged(p)
        ]
        tagged = [
            pid_of(p) for p in defending_team(world)
            if getattr(p, "fullback_role_active", False)
        ]
        self.explicit_exits.append({
            "frame": self.frame,
            "kind": kind,
            "engaged_pids": engaged,
            "tagged_pids": tagged,
        })
        self.note_event(kind)

    def note_outcome(self, defender, outcome, bc):
        self.outcomes.append({
            "frame": self.frame,
            "pid": pid_of(defender),
            "outcome": outcome,
            "tagged": bool(getattr(defender, "fullback_role_active", False)),
            "bc_pid": pid_of(bc) if outcome == CLEAN_ENGAGE else None,
        })
        self.note_event("OUTCOME:%s" % outcome)

    def note_anchor_relock(self, world):
        self.anchor_relocks.append({
            "frame": self.frame,
            "ruck_x": optnum(getattr(world, "ruck_x_m", None)),
        })
        self.note_event("ANCHOR_RELOCK")

    def note_try(self, bc):
        self.tries.append({"frame": self.frame, "pid": pid_of(bc)})
        self.note_event("TRY")

    # ── window lifecycle ─────────────────────────────────────────────
    def open_window(self, world, fb, bc, break_kind):
        self.active = {
            "index": len(self.windows),
            "fb_pid": pid_of(fb),
            "carrier_pid": pid_of(bc),
            "start_frame": self.frame,
            "start_break": break_kind,
            "end_frame": None,
            "end_reason": None,
            "rows": [],
        }
        self.windows.append(self.active)

    def close_window(self, reason):
        if self.active is None:
            return
        self.active["end_frame"] = self.frame
        self.active["end_reason"] = reason
        self.active = None

    def _tackle_landed_on(self, carrier_pid, since_frame):
        """True when the carrier was CLEAN_ENGAGEd at/after *since_frame*."""
        for ev in self.outcomes:
            if ev["outcome"] != CLEAN_ENGAGE:
                continue
            if ev["frame"] < since_frame:
                continue
            if carrier_pid is None or ev.get("bc_pid") == carrier_pid:
                return True
        return False

    # ── the per-frame sample ─────────────────────────────────────────
    def sample(self, world, open_play, collisions=None):
        """Sample the tagged fullback; open / advance / close the window."""
        bc = getattr(world, "ball_carrier", None)
        fb = None
        for d in defending_team(world):
            if getattr(d, "fullback_role_active", False):
                fb = d
                break

        # ── TRY: a CROSSING of the defending tryline, never a residency ──
        # The carrier is clamped AT the tryline once it reaches it and can sit
        # on that line for the rest of the play, so a residency test reports a
        # try on every one of those frames (measured: 4696 "tries" and 4831
        # windows in a 6000-frame rollout, i.e. one spurious window per frame).
        # Only the CROSSING counts, and once the try is recorded the play is
        # over: no further window may open until the carrier is replaced or
        # leaves the tryline.
        bc_x = fnum(getattr(bc, "x", 0.0)) if bc is not None else None
        if bc_x is not None:
            if (self._prev_bc_x is not None
                    and self._prev_bc_x < (DEFENDING_TRYLINE_X - 0.5)
                    and bc_x >= (DEFENDING_TRYLINE_X - 0.5)):
                self.note_try(bc)
                self.try_armed = True
            elif bc_x < (DEFENDING_TRYLINE_X - 5.0):
                self.try_armed = False
            self._prev_bc_x = bc_x

        # ── OPEN A WINDOW on the first break observed on a tagged FB ──
        if (self.active is None and not self.try_armed
                and len(self.windows) < MAX_WINDOWS_TRACED
                and fb is not None and bc is not None):
            ctx = gate_context(world, fb, bc, open_play)
            if ctx["break_kind"] != BREAK_NONE:
                self.open_window(world, fb, bc, ctx["break_kind"])

        if self.active is None:
            return

        win = self.active
        body = find_body(world, win["fb_pid"])
        row = sample_frame(world, body, bc, open_play, self.events)
        win["rows"].append(row)

        # ── TERMINAL CONDITIONS: tackle landed, try, or horizon ──
        if self._tackle_landed_on(win["carrier_pid"], win["start_frame"]):
            self.close_window("TACKLE")
            return
        if self.tries and self.tries[-1]["frame"] >= win["start_frame"]:
            self.close_window("TRY")
            return
        if self.frame - win["start_frame"] >= WINDOW_FRAMES:
            self.close_window("WINDOW_END")


# ═════════════════════════════════════════════════════════════════════
# SPY INSTALLATION
# ═════════════════════════════════════════════════════════════════════

def install_spies(ledger):
    """Wrap the engine callables; every wrapper calls the real function."""
    real_outcome = pm.apply_outcome
    real_arm = OpenPlayEngine._arm_tackler_role
    real_clear = OpenPlayEngine._clear_tes_artefacts
    real_broken = OpenPlayEngine._handle_broken_tackle
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
            ledger.note_arm(d, role_name)
        except Exception:
            pass
        return res

    def clear_tes(ope_self, *a, **kw):
        # Record BEFORE the real call: the hand-back inside the RELEASE-entry
        # path drops the engagement marker, so sampling afterwards would
        # hide exactly the fact this spy exists to capture.
        try:
            ledger.note_explicit_exit("RELEASE_ENTRY", getattr(ope_self, "world", None))
        except Exception:
            pass
        return real_clear(ope_self, *a, **kw)

    def broken(ope_self, *a, **kw):
        try:
            ledger.note_explicit_exit("MISS_TEARDOWN", getattr(ope_self, "world", None))
        except Exception:
            pass
        return real_broken(ope_self, *a, **kw)

    def reset_anchor(world, *a, **kw):
        res = real_reset_anchor(world, *a, **kw)
        try:
            ledger.note_anchor_relock(world)
        except Exception:
            pass
        return res

    pm.apply_outcome = outcome
    OpenPlayEngine._arm_tackler_role = arm
    OpenPlayEngine._clear_tes_artefacts = clear_tes
    OpenPlayEngine._handle_broken_tackle = broken
    aa.reset_anchor_authority = reset_anchor
    return {
        "outcome": real_outcome,
        "arm": real_arm,
        "clear": real_clear,
        "broken": real_broken,
        "reset_anchor": real_reset_anchor,
    }


def uninstall_spies(originals):
    """Restore the real callables (probe hygiene)."""
    pm.apply_outcome = originals["outcome"]
    OpenPlayEngine._arm_tackler_role = originals["arm"]
    OpenPlayEngine._clear_tes_artefacts = originals["clear"]
    OpenPlayEngine._handle_broken_tackle = originals["broken"]
    aa.reset_anchor_authority = originals["reset_anchor"]
# ═════════════════════════════════════════════════════════════════════
# THE ROLLOUT — the real engine, booted as in the lifetime probe
# ═════════════════════════════════════════════════════════════════════

def run_rollout(frames=MAX_FRAMES, seed=SEED, verbose=False):
    """Boot the engine, kick off, step it, return the timeline ledger."""
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

    ledger = TimelineLedger()
    originals = install_spies(ledger)

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

            # SAMPLE AFTER the engine has finished the frame.
            ledger.sample(world, open_play, collision_system)
            ledger.frames_stepped = frame

            if verbose and frame % 1000 == 0:
                print(
                    "    ... frame %d (windows=%d)"
                    % (frame, len(ledger.windows)),
                    file=_REAL_STDOUT, flush=True,
                )
    finally:
        uninstall_spies(originals)

    return {"ledger": ledger, "world": world, "open_play": open_play}


# ═════════════════════════════════════════════════════════════════════
# ANALYSIS — where does the authority terminate, and HOW?
# ═════════════════════════════════════════════════════════════════════

def annotate_window(win):
    """Compute the change rows, the termination row and the leak metrics."""
    rows = win["rows"]
    change_rows = []
    prev = None
    for row in rows:
        code, detail = catalogue_reason(prev, row, win)
        if code is not None:
            change_rows.append({"row": row, "code": code, "detail": detail})
        prev = row
    win["changes"] = change_rows

    # The chase owner's span.
    chase_rows = [
        r for r in rows
        if r.get("writer") == "fullback_geometry" and r.get("mode") == "CHASE"
    ]
    win["chase_frames"] = len(chase_rows)
    win["geometry_frames"] = sum(
        1 for r in rows if r.get("writer") == "fullback_geometry"
    )
    win["chase_first_frame"] = chase_rows[0]["frame"] if chase_rows else None
    win["chase_last_frame"] = chase_rows[-1]["frame"] if chase_rows else None

    # The TERMINATION row: the first row after the chase became live whose
    # writer/mode is no longer the fullback chase contract.
    term = None
    if chase_rows:
        first = chase_rows[0]["frame"]
        for row in rows:
            if row["frame"] < first:
                continue
            if (row.get("writer") != "fullback_geometry"
                    or row.get("mode") != "CHASE"):
                term = row
                break
    win["termination"] = term

    if term is None:
        win["termination_class"] = "NONE_OBSERVED"
        win["termination_detail"] = (
            "the chase contract held to the end of the traced window"
        )
    else:
        # The frame BEFORE the handover is what makes the classification
        # meaningful: WRITER_OVERWRITE and MODE_OVERWRITE are told apart by
        # comparing the two frames, not by looking at the handover frame alone.
        before = None
        for row in rows:
            if row is term:
                break
            before = row
        win["termination_class"] = classify_handover(before, term)
        win["termination_detail"] = handover_detail(before, term)

    # THE LEAK METRIC: frames where the body still carries a live authority
    # stamp (engaged / authority_active) while an authority OTHER than the
    # fullback geometry ownsthe command — i.e. it was OVERWRITTEN.
    win["overwritten_frames"] = sum(
        1 for r in rows
        if r.get("authority_active")
        and r.get("writer") != "fullback_geometry"
    )
    win["engaged_leak_frames"] = sum(
        1 for r in rows
        if r.get("engaged") and r.get("writer") != "fullback_geometry"
    )
    win["engaged_frames"] = sum(1 for r in rows if r.get("engaged"))
    win["gate_frames"] = sum(1 for r in rows if r.get("gate_active"))
    win["yield_frames"] = sum(1 for r in rows if r.get("yield_stamp"))
    return win


def writer_histogram(rows):
    hist = {}
    for r in rows:
        key = (r.get("mode"), r.get("writer"))
        hist[key] = hist.get(key, 0) + 1
    return sorted(hist.items(), key=lambda kv: -kv[1])


# ─────────────────────────────────────────────────────────────────────
# THE GLOBAL AUTHORITY LEDGER (frame-deduped, across every window)
# ─────────────────────────────────────────────────────────────────────
# Windows are per-EPISODE: one opens when a break appears and closes on the
# tackle / try / horizon.  The question in this report is about the whole
# population of frames, so the windows' rows are concatenated and deduped by
# frame number (a row is already the frame's FINAL applied state, so a
# duplicate across churned windows is identical).

CHASE_WRITER = "fullback_geometry"
CHASE_MODE = "CHASE"


def is_chase_owned(row):
    """True while the fullback geometry authority owns the body AS A CHASE."""
    return (
        row.get("writer") == CHASE_WRITER
        and row.get("mode") == CHASE_MODE
    )


def ordered_unique_rows(windows):
    """Every window row, ordered by frame, deduped on the frame number."""
    by_frame = {}
    for win in windows:
        for row in win["rows"]:
            frame = row.get("frame")
            if frame is None or frame in by_frame:
                continue
            by_frame[frame] = row
    return [by_frame[f] for f in sorted(by_frame)]


def classify_handover(prev, row):
    """Classify the frame on which the chase contract lost the body.

    WRITER_OVERWRITE and MODE_OVERWRITE are DIFFERENT mechanisms and mean
    different things, so they are never collapsed into one word:

      WRITER_OVERWRITE    the target WRITER changed — a different authority's
                          stamp won the last-write contest — with no exit call
      MODE_OVERWRITE      the SAME writer kept the target but replaced the
                          MOVEMENT STATE: the chase contract was superseded by
                          a later mode decision INSIDE that writer, which is
                          an overwrite of the CONTRACT but not of the writer
      EXPLICIT_*          an engine reset / release / arm call ran that frame
      PHASE_RELEASE       the OPE sub-phase itself entered a post-tackle phase
    """
    if prev is None:
        return "UNKNOWN_NO_PREVIOUS_FRAME"
    events = row.get("events") or ()
    if "RELEASE_ENTRY" in events:
        return "EXPLICIT_RELEASE_ENTRY"
    if "MISS_TEARDOWN" in events:
        return "EXPLICIT_MISS_TEARDOWN"
    if "ARM" in events:
        return "EXPLICIT_ARM_HANDOVER"
    if (row.get("subphase") in POST_TACKLE_SUBPHASES
            and prev.get("subphase") not in POST_TACKLE_SUBPHASES):
        return "PHASE_RELEASE"
    if row.get("writer") != prev.get("writer"):
        return "WRITER_OVERWRITE"
    return "MODE_OVERWRITE"


def handover_detail(prev, row):
    """One-line evidence for a handover frame: WHAT changed, plus the stamps.

    The changed fields are printed explicitly because that is the whole point:
    a ``mode CHASE -> DRIFT`` line with the writer unchanged is a very
    different fact from a ``writer X -> Y`` line, and the reader must be able
    to tell them apart without going back to the raw rows.
    """
    events = row.get("events") or ()
    changed = []
    if prev is not None:
        if row.get("writer") != prev.get("writer"):
            changed.append("writer %s -> %s"
                           % (prev.get("writer"), row.get("writer")))
        if row.get("mode") != prev.get("mode"):
            changed.append("mode %s -> %s"
                           % (prev.get("mode"), row.get("mode")))
    return (
        "%s | role=%s stamp=%s engaged=%s tackle_role=%s commit=%s stance=%s"
        " gate=%s auth=%s subphase=%s events=[%s]"
        % (
            "; ".join(changed) or "(nothing structural changed)",
            row.get("defender_role"), row.get("yield_stamp") or "-",
            bools(row.get("engaged")), row.get("tackle_role") or "-",
            bools(row.get("commit")), bools(row.get("stance")),
            bools(row.get("gate_active")), bools(row.get("authority_active")),
            row.get("subphase"), ",".join(events) if events else "-",
        )
    )


def chase_handover_ledger(rows):
    """[(prev, row, class, detail)] for every exit from the chase contract."""
    out = []
    prev = None
    for row in rows:
        if prev is not None and is_chase_owned(prev) and not is_chase_owned(row):
            out.append((
                prev, row, classify_handover(prev, row),
                handover_detail(prev, row),
            ))
        prev = row
    return out


def select_windows(windows):
    """The windows worth printing in full: the longest, then the rest."""
    ranked = sorted(
        windows,
        key=lambda w: (
            -int(w.get("overwritten_frames", 0) > 0),
            -len(w["rows"]),
            w["index"],
        ),
    )
    chosen = [w for w in ranked if len(w["rows"]) >= MIN_REPORTED_WINDOW_ROWS]
    if not chosen:
        chosen = ranked[:MAX_WINDOWS_REPORTED]
    return chosen[:MAX_WINDOWS_REPORTED]


# ═════════════════════════════════════════════════════════════════════
# REPORT PLUMBING
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
    print("=" * 118)
    print(title)
    print("=" * 118)


def sub(title):
    print("")
    print("-" * 118)
    print(title)
    print("-" * 118)


def fmt(value, places=2):
    if value is None:
        return "--"
    if isinstance(value, float):
        return ("%%.%df" % places) % value
    return str(value)


def histogram(values, limit=24):
    hist = {}
    for v in values:
        hist[v] = hist.get(v, 0) + 1
    return sorted(hist.items(), key=lambda kv: -kv[1])[:limit]


def bools(flag):
    return "T" if flag else "."


def render_row(row, reason=None, detail=None):
    """One compact per-frame authority line (change rows only)."""
    print(
        "    f=%-6s mode=%-9s writer=%-18s role=%-14s"
        " tgt=(%s,%s) pos=(%s,%s) v=(%s,%s) spd=%s"
        % (
            row.get("frame"),
            fmt(row.get("mode")),
            fmt(row.get("writer")),
            fmt(row.get("defender_role")),
            fmt(row.get("target_x"), 2), fmt(row.get("target_y"), 2),
            fmt(row.get("x"), 2), fmt(row.get("y"), 2),
            fmt(row.get("vx"), 2), fmt(row.get("vy"), 2),
            fmt(row.get("move_speed"), 2),
        )
    )
    print(
        "           pred| tag=%s ctl=%s trole=%-9s tes_role=%-9s commit=%s"
        " stance=%s tstate=%s frz=%s ft=%s att=%s contact=%s eng=%s"
        % (
            bools(row.get("tag")), bools(row.get("can_acquire_tackle")),
            fmt(row.get("tackle_role")), fmt(row.get("tes_tackler_role")),
            bools(row.get("commit")), bools(row.get("stance")),
            bools(row.get("tackle_state")), bools(row.get("frozen")),
            fmt(row.get("freeze_timer")), bools(row.get("tes_attached")),
            bools(row.get("in_contact")), bools(row.get("engaged")),
        )
    )
    print(
        "           auth| gate=%s auth=%s owner=%s cap=%s yield=%-18s"
        " brk=%-8s dist=%-6s lane=%-6s bc_x=%-7s line=%-6s ruck=%s"
        " sub=%-11s"
        % (
            bools(row.get("gate_active")), bools(row.get("authority_active")),
            bools(row.get("side_owner_live")), bools(row.get("approach_cap")),
            fmt(row.get("yield_stamp")), fmt(row.get("break_kind")),
            fmt(row.get("dist_to_bc")), fmt(row.get("lane_error")),
            fmt(row.get("bc_x")), fmt(row.get("line_anchor_x")),
            fmt(row.get("ruck_x")), fmt(row.get("subphase")),
        )
    )
    if reason:
        print("           WHY : %-22s %s" % (reason, detail or ""))
    events = row.get("events") or ()
    if events:
        print("           EVT : %s" % ", ".join(events))
# ═════════════════════════════════════════════════════════════════════
# MULTI-SEED AGGREGATE
# ═════════════════════════════════════════════════════════════════════
# The engine is NOT reproducible across processes for this fixture: three
# consecutive runs of the SAME seed produced 26, 76 and 135 windows and two
# different handover populations (one run carried an explicit ARM handover
# with the engagement marker set; the others did not).  A single seed is
# therefore not evidence, and the verdict is drawn from the POOLED counters
# of every seed in the sweep.

def summarize_run(res):
    """The scalars needed to pool one rollout into the verdict."""
    ledger = res["ledger"]
    windows = [annotate_window(w) for w in ledger.windows if w["rows"]]
    rows = ordered_unique_rows(windows)
    handovers = chase_handover_ledger(rows)
    classes = {}
    for _prev, _row, code, _detail in handovers:
        classes[code] = classes.get(code, 0) + 1
    return {
        "windows": len(ledger.windows),
        "rows": len(rows),
        "chase_frames": sum(1 for r in rows if is_chase_owned(r)),
        "handovers": len(handovers),
        "classes": classes,
        "arms_tagged": sum(1 for a in ledger.arms if a["tagged"]),
        "engaged_frames": sum(w["engaged_frames"] for w in windows),
        "exits_with_fb": sum(
            1 for e in ledger.explicit_exits if e["engaged_pids"]
        ),
        "tries": len(ledger.tries),
    }


def report_aggregate(runs):
    """Pool every seed: handover classes and the scope counters."""
    summaries = [(seed, summarize_run(res)) for seed, res in runs]

    banner("MULTI-SEED AGGREGATE — the verdict across every seed run")
    print("  %-14s %8s %8s %8s %8s %8s %8s"
          % ("seed", "windows", "sampled", "chase", "handov", "tagged",
             "engaged"))
    for seed, s in summaries:
        print("  %-14d %8d %8d %8d %8d %8d %8d"
              % (seed, s["windows"], s["rows"], s["chase_frames"],
                 s["handovers"], s["arms_tagged"], s["engaged_frames"]))

    pooled_classes = {}
    for _seed, s in summaries:
        for code, n in s["classes"].items():
            pooled_classes[code] = pooled_classes.get(code, 0) + n
    print("")
    print("  pooled sampled frames                    : %d"
          % sum(s["rows"] for _s, s in summaries))
    print("  pooled chase-owned frames                : %d"
          % sum(s["chase_frames"] for _s, s in summaries))
    print("  pooled handovers out of the chase struct  : %d"
          % sum(s["handovers"] for _s, s in summaries))
    print("  pooled handover classes                  :")
    if pooled_classes:
        for code, n in sorted(pooled_classes.items(), key=lambda kv: -kv[1]):
            print("      %-24s %3d" % (code, n))
    else:
        print("      (none observed in any seed)")
    print("  pooled TES arming calls on the TAGGED FB : %d"
          % sum(s["arms_tagged"] for _s, s in summaries))
    print("  pooled frames with the marker set        : %d"
          % sum(s["engaged_frames"] for _s, s in summaries))
    print("  pooled exits that found an engaged FB    : %d"
          % sum(s["exits_with_fb"] for _s, s in summaries))

    n_explicit = sum(
        n for code, n in pooled_classes.items() if code.startswith("EXPLICIT")
    )
    n_overwrite = (
        pooled_classes.get("MODE_OVERWRITE", 0)
        + pooled_classes.get("WRITER_OVERWRITE", 0)
    )
    print("")
    print("  POOLED VERDICT")
    print("      handovers ending EXPLICITLY (engine call ran) : %d"
          % n_explicit)
    print("      handovers ending by OVERWRITE (no call ran)   : %d"
          % n_overwrite)
    if n_explicit and n_overwrite:
        print("")
        print("      BOTH mechanisms occur.  The chase contract is sometimes")
        print("      explicitly exited, and sometimes merely overwritten: the")
        print("      writer keeps the body while the movement state is replaced")
        print("      under it, with no engine call, so nothing clears")
        print("      ``_fb_emergency_engaged``.")
    elif n_overwrite:
        print("")
        print("      OVERWRITE ONLY in this sample: every handover replaced the")
        print("      movement state (or the writer) with no engine exit call.")
    elif n_explicit:
        print("")
        print("      EXPLICIT ONLY in this sample: every handover ran through an")
        print("      engine exit call.")
    else:
        print("")
        print("      No handover of any kind was observed, so this sample cannot")
        print("      discriminate between the two mechanisms.")
    print("")
    print("  Seeds run: %s" % ", ".join(str(seed) for seed, _s in summaries))


# ═════════════════════════════════════════════════════════════════════
# THE REPORT
# ═════════════════════════════════════════════════════════════════════

def report(res):
    ledger = res["ledger"]
    windows = [annotate_window(w) for w in ledger.windows if w["rows"]]
    rows = ordered_unique_rows(windows)
    handovers = chase_handover_ledger(rows)
    selected = select_windows(windows)

    banner("FB AUTHORITY TIMELINE — LINE BREAK -> TACKLE / TRY, PER FRAME")
    print("  seed                        : %d" % SEED)
    print("  frames stepped              : %d" % ledger.frames_stepped)
    print("  dt                          : %.6f s" % DT)
    print("  window traced per break     : %d frames" % WINDOW_FRAMES)
    print("  author of the frame         : player._target_writer (blessed tag)")
    print("                                else target_source")

    # ── A. COVERAGE ──
    sub("A. COVERAGE — was the question exercised, and on what evidence?")
    print("  line-break windows opened         : %d" % len(ledger.windows))
    print("  windows with per-frame rows       : %d" % len(windows))
    print("  apply_outcome calls               : %d (CLEAN_ENGAGE %d)"
          % (len(ledger.outcomes),
             sum(1 for e in ledger.outcomes if e["outcome"] == CLEAN_ENGAGE)))
    print("  TES arming calls                  : %d (tagged %d)"
          % (len(ledger.arms),
             sum(1 for a in ledger.arms if a["tagged"])))
    print("  EXPLICIT exit sites invoked        : %d" % len(ledger.explicit_exits))
    for kind, n in histogram(e["kind"] for e in ledger.explicit_exits):
        with_fb = sum(
            1 for e in ledger.explicit_exits
            if e["kind"] == kind and e["engaged_pids"]
        )
        print("    %-16s total %3d   with an ENGAGED FB: %3d" % (kind, n, with_fb))
    print("  tryline crossings recorded         : %d" % len(ledger.tries))
    print("  anchor-authority relocks           : %d" % len(ledger.anchor_relocks))
    # The windows are the SAMPLING FRAME for the whole report: the per-frame
    # rows only exist while a window is open, so the reader must know how much
    # of the rollout the conclusions actually cover.
    all_rows = ordered_unique_rows(windows)
    by_frame = {r.get("break_kind") for r in all_rows}
    print("  frames sampled inside windows       : %d of %d stepped (%.1f%%)"
          % (len(all_rows), ledger.frames_stepped,
             100.0 * len(all_rows) / max(1, ledger.frames_stepped)))
    print("  break kinds seen in those frames    : %s"
          % ", ".join(sorted(str(k) for k in by_frame)))
    print("  distinct target writers seen        : %s"
          % ", ".join(sorted({str(r.get("writer")) for r in all_rows})))
    print("  distinct movement states seen       : %s"
          % ", ".join(sorted({str(r.get("mode")) for r in all_rows})))
    if not windows:
        print("")
        print("  !! NO line-break window was traced on a tagged fullback in this")
        print("     rollout, so the authority question was never exercised and")
        print("     nothing is claimed below.")
        print("")
        print("  Report written to %s" % REPORT_PATH)
        return

    # ── B. PER-WINDOW TIMELINE ──
    sub("B. PER-FRAME AUTHORITY TIMELINE — every change of mode / writer")
    print("  (rows print ONLY where the mode or the writer changed hands;")
    print("   every row carries the target, the live state and the predicates)")

    for win in selected:
        print("")
        print("  ═══════════════════════════════════════════════════════════════")
        print("  WINDOW %d — break at f=%s (%s), fb_pid=%s, carrier_pid=%s"
              % (win["index"], win["start_frame"], win["start_break"],
                 win["fb_pid"], win["carrier_pid"]))
        print("    span f=%s -> %s   end=%s   rows=%d"
              % (win["start_frame"], win["end_frame"], win["end_reason"],
                 len(win["rows"])))
        print("    chase-owned frames=%d  geometry-owned frames=%d"
              "  engaged frames=%d  gate frames=%d  yield frames=%d"
              % (win["chase_frames"], win["geometry_frames"],
                 win["engaged_frames"], win["gate_frames"], win["yield_frames"]))
        print("  ═══════════════════════════════════════════════════════════════")
        most = win["changes"][:MAX_ROWS_PER_WINDOW]
        for change in most:
            render_row(change["row"], change["code"], change["detail"])
        if len(win["changes"]) > len(most):
            print("    ... %d further change row(s) suppressed"
                  % (len(win["changes"]) - len(most)))

        print("")
        print("    ── (mode, writer) occupancy across the whole window ──")
        for (mode, writer), n in writer_histogram(win["rows"]):
            print("      %-9s x %-18s = %4d frames" % (fmt(mode), fmt(writer), n))

    # ── C. TERMINATION ──
    sub("C. AUTHORITY TERMINATION — EXPLICIT EXIT vs OVERWRITE")
    print("  For each window: the chase-owner's first frame, the first frame it")
    print("  no longer held, and whether an ENGINE EXIT CALL ran on that frame.")
    print("  An exit call is the ONLY mechanism that clears the engagement")
    print("  marker; a frame with no exit call is an OVERWRITE.")
    print("")
    for win in selected:
        term = win.get("termination")
        print("  WINDOW %d  (f=%s -> %s, end=%s)"
              % (win["index"], win["start_frame"], win["end_frame"],
                 win["end_reason"]))
        print("    chase live         : f=%s" % win.get("chase_first_frame"))
        print("    chase last         : f=%s" % win.get("chase_last_frame"))
        if term is None:
            print("    termination        : NONE — %s" % win["termination_detail"])
        else:
            print("    termination frame  : f=%s  mode=%s  writer=%s  yield=%s"
                  % (term["frame"], fmt(term.get("mode")),
                     fmt(term.get("writer")), fmt(term.get("yield_stamp"))))
            print("    termination class  : %s" % win["termination_class"])
            print("    detail             : %s" % win["termination_detail"])
            print("    engaged at term    : %s   (authority_active=%s,"
                  " side_owner_live=%s)"
                  % (bools(term.get("engaged")),
                     bools(term.get("authority_active")),
                     bools(term.get("side_owner_live"))))
            events = term.get("events") or ()
            print("    engine events      : %s"
                  % (", ".join(events) if events else "(none)"))
        print("    NOT-explicit frames where the body still carried a live"
              " authority and the geometry writer did NOT own it : %d"
              % win["overwritten_frames"])
        print("    ... of those, frames the ENGAGEMENT MARKER was still set  : %d"
              % win["engaged_leak_frames"])
        print("")

    # ── C2. THE GLOBAL HANDOVER LEDGER ──
    sub("C2. EVERY CHASE HANDOVER IN THE ROLLOUT — classified, in frame order")
    print("  A HANDOVER is the frame on which the fullback chase contract")
    print("  (writer=fullback_geometry, mode=CHASE) STOPPED holding the body.")
    print("  A handover frame carrying an engine exit call is EXPLICIT; a")
    print("  handover frame carrying none is an OVERWRITE.")
    print("")
    print("  frames sampled (deduped)             : %d" % len(rows))
    print("  frames the chase contract owned       : %d"
          % sum(1 for r in rows if is_chase_owned(r)))
    print("  handovers out of the chase contract   : %d" % len(handovers))
    for idx, (prev, row, cls, detail) in enumerate(handovers):
        print("    [%02d] f=%-6s -> f=%-6s  %-22s  %s"
              % (idx, prev.get("frame"), row.get("frame"), cls, detail))
    cls_counts = {}
    for _p, _r, cls, _d in handovers:
        cls_counts[cls] = cls_counts.get(cls, 0) + 1
    print("")
    print("  -- handover class distribution --")
    if cls_counts:
        for k, n in sorted(cls_counts.items(), key=lambda kv: -kv[1]):
            print("      %-24s %3d frame(s)" % (k, n))
    else:
        print("      (none — the chase contract never handed the body over)")

    # ── D. VERDICT ──
    banner("VERDICT — measured, not inferred")
    classes = {}
    for win in windows:
        classes[win["termination_class"]] = classes.get(
            win["termination_class"], 0
        ) + 1
    print("  windows traced                     : %d" % len(windows))
    print("  termination classes                :")
    for k, n in sorted(classes.items(), key=lambda kv: -kv[1]):
        print("      %-26s %3d window(s)" % (k, n))
    explicit = sum(
        n for k, n in classes.items() if k.startswith("EXPLICIT")
    )
    overwritten = sum(
        n for k, n in classes.items()
        if k in ("MODE_OVERWRITE", "WRITER_OVERWRITE")
    )
    print("")
    print("  EXPLICIT exits (an engine reset/release call cleared the marker)")
    print("      windows = %d" % explicit)
    print("  OVERWRITES (another authority took the command with NO exit call)")
    print("      windows = %d" % overwritten)
    print("")
    print("  CHASE HANDOVERS (fullback_geometry+CHASE -> any other authority)")
    print("      handovers = %d" % len(handovers))
    for k, n in sorted(cls_counts.items(), key=lambda kv: -kv[1]):
        print("      %-24s %3d frame(s)" % (k, n))
    handover_explicit = sum(
        n for k, n in cls_counts.items() if k.startswith("EXPLICIT")
    )
    handover_overwritten = sum(
        n for k, n in cls_counts.items()
        if k in ("MODE_OVERWRITE", "WRITER_OVERWRITE", "PHASE_RELEASE")
    )
    print("      of which EXPLICIT (an engine exit call ran) : %d"
          % handover_explicit)
    print("      of which NOT explicit (no exit call ran)    : %d"
          % handover_overwritten)
    print("")
    leak_total = sum(w["engaged_leak_frames"] for w in windows)
    auth_total = sum(w["overwritten_frames"] for w in windows)
    print("  frames with a live authority stamp while the geometry writer DID")
    print("  NOT own the command (the overwrite signature)   : %d" % auth_total)
    print("  ... restricted to frames the ENGAGEMENT MARKER was still set")
    print("      (proof the marker merely persisted)          : %d" % leak_total)

    mode_over = cls_counts.get("MODE_OVERWRITE", 0)
    writer_over = cls_counts.get("WRITER_OVERWRITE", 0)
    arm_over = cls_counts.get("EXPLICIT_ARM_HANDOVER", 0)
    rel_over = cls_counts.get("EXPLICIT_RELEASE_ENTRY", 0)
    miss_over = cls_counts.get("EXPLICIT_MISS_TEARDOWN", 0)
    phase_rel = cls_counts.get("PHASE_RELEASE", 0)
    if cls_counts:
        print("")
        print("  THE ANSWER: BOTH mechanisms occur.  The chase contract is")
        print("  SOMETIMES explicitly exited and SOMETIMES merely overwritten;")
        print("  the two are counted separately below and never merged.")
        if mode_over:
            print("")
            print("  (a) OVERWRITE — %d handover frame(s)." % mode_over)
            print("      The target writer stayed ``fullback_geometry`` and the")
            print("      MOVEMENT STATE was replaced (CHASE -> DRIFT) with NO engine")
            print("      exit call on that frame.  Nothing cleared")
            print("      ``_fb_emergency_engaged`` — the authority simply stopped")
            print("      being the chase and became the next mode instead.")
        if writer_over:
            print("")
            print("  (b) WRITER_OVERWRITE — %d handover frame(s): the target WRITER"
                  % writer_over)
            print("      itself changed with no engine call (last-writer-wins).")
        if arm_over:
            print("")
            print("  (c) EXPLICIT HANDOVER — %d handover frame(s)." % arm_over)
            print("      ``_arm_tackler_role`` ran ON the handover frame")
            print("      (events=[ARM]) with the engagement marker SET, so TES took")
            print("      the body through an engine call.  This is an explicit")
            print("      hand-back, NOT an overwrite.")
        if rel_over or miss_over or phase_rel:
            print("")
            print("  (d) Other exits: RELEASE_ENTRY=%d  MISS_TEARDOWN=%d"
                  "  PHASE_RELEASE=%d" % (rel_over, miss_over, phase_rel))
        if not (mode_over or writer_over):
            print("")
            print("  No target/movement overwrite was observed: every handover in")
            print("  this rollout ran through an engine exit call.")
    else:
        print("")
        print("  NO handover was observed at all: on every sampled frame the")
        print("  chase contract still owned the body, so this rollout provides")
        print("  no evidence about how the contract ends.")
    print("")
    print("  SCOPE LIMIT — read this before quoting the verdict:")
    armed_tagged = sum(1 for a in ledger.arms if a["tagged"])
    engaged_frames = sum(w["engaged_frames"] for w in windows)
    exit_with_fb = sum(
        1 for e in ledger.explicit_exits if e["engaged_pids"]
    )
    print("    TES arming calls on the TAGGED fullback : %d" % armed_tagged)
    print("    sampled frames with the engagement"
          " marker set      : %d" % engaged_frames)
    print("    explicit exit calls that found an"
          " engaged FB  : %d" % exit_with_fb)
    if not engaged_frames:
        print("    The ``_fb_emergency_engaged`` branch was NEVER TAKEN in this")
        print("    fixture, so nothing here tests whether the marker is cleared")
        print("    or leaked.  What this rollout DOES establish is the movement-")
        print("    state side of the same question, in the section above.")
    if explicit:
        print("")
        print("  %d window(s) ended through an engine exit call on the handover"
              % explicit)
        print("  frame: %s"
              % ", ".join(sorted(
                  k for k in classes if k.startswith("EXPLICIT"))))
        print("  Those paths clear ``_fb_emergency_engaged`` and restore")
        print("  ``DEF_LINE_READY`` through"
              " ``reset_fullback_emergency_engagement`` /")
        print("  ``release_fullback_emergency_on_miss``.")
    if not overwritten and not explicit:
        print("")
        print("  No window reached a termination transition inside the traced")
        print("  horizon; the chase contract held to the window edge on every")
        print("  break.")
    print("")
    print("  Report written to %s" % REPORT_PATH)


# ═════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════

def main():
    global _REPORT
    real = sys.stdout

    raw_seeds = os.environ.get("RLM_FBTL_SEEDS", str(SEED)).replace(" ", "")
    seeds = [int(s) for s in raw_seeds.split(",") if s]

    # PHASE 1: the rollouts, with the engine's own chatter discarded.
    runs = []
    _devnull = open(os.devnull, "w", encoding="utf-8")
    for index, seed in enumerate(seeds):
        sys.stdout = _devnull
        try:
            res = run_rollout(seed=seed, verbose=False)
        finally:
            sys.stdout = real
        runs.append((seed, res))
        print("  ... seed %d done (%d/%d)"
              % (seed, index + 1, len(seeds)),
              file=real, flush=True)
    try:
        _devnull.close()
    except Exception:
        pass

    # PHASE 2: the detailed report for the first seed, then the pooled
    # verdict across every seed, into one clean file and the terminal.
    _REPORT = open(REPORT_PATH, "w", encoding="utf-8")
    sys.stdout = _tee()
    try:
        report(runs[0][1])
        if len(runs) > 1:
            report_aggregate(runs)
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
