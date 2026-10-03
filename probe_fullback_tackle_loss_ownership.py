"""probe_fullback_tackle_loss_ownership.py — WHO OWNS A BEATEN FULLBACK'S TARGET?

THE QUESTION
------------
A tagged fullback (``fullback_role_active``) is admitted to the emergency
tackle path on a genuine line break, armed as a TES tackler, and then the
ball carrier BEATS the tackle (BUMP_OFF / broken tackle / pre-tackle miss).
On the FIRST FRAME AFTER that tackle-loss the fullback is supposed to be on
the CHASE authority (pursuit of the live carrier).  The reported symptom is
that it instead stays on the FULLBACK-DEPTH authority -- ``target_source``
``"fullback_geometry"`` / ``movement_state`` DRIFT / the sweeper anchor --
so the last defender never pursues the carrier it just missed.

This probe does not argue the cause.  It drives the real engine, detects the
tackle-loss event on a TAGGED fullback, and dumps that body's COMPLETE target
/ movement ledger for the frames after it, including the pure predicate reads
that decide whether the CHASE override could fire.

WHAT IT LOGS (the requested fields, plus the inputs that explain them)
----------------------------------------------------------------------
  role                    player.role / defender_role
  movement_state          MovementState name
  target_source           behaviour stamp (read by the engine)
  target_x / target_y     the commanded point
  bc_x                    the live carrier's X
  last target writer      player._target_writer (the canonical blessed tag
                          written by ``stamp_target_write`` -- and by
                          ``apply_fullback_geometry`` directly)
plus, to make the ownership decision auditable:
  fullback_tackle_authority_active  the predicate that makes the depth
                                    writer YIELD
  each of its four stamps: _fb_emergency_engaged, tackle_role,
  _tackle_commit_active, pre_tackle_stance
  line_break_active / classify_fullback_break / the line anchor
  freeze_timer / frozen / move_speed / vx,vy / engine phase

METHOD
------
Read-only: every spy calls the real callable UNCHANGED, and the per-frame
ledger is built by SAMPLING the fullback after the engine has finished the
frame (so it is the frame's FINAL, applied state).

Run:  python probe_fullback_tackle_loss_ownership.py
Report: tmp_fullback_tackle_loss_ownership.txt
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

# The subjects / writers under measurement.
from game.defence.defence_lvl_1 import fullback_role as fb
from game.defence.defence_lvl_1 import pretackle_machine as pm
from game.defence.defence_lvl_1.anchor_authority import (
    defensive_line_anchor_m,
)


# ── ROLLOUT CONFIG ───────────────────────────────────────────────────
MAX_FRAMES = int(os.environ.get("RLM_FB_LOSS_FRAMES", "6000"))
SEED = int(os.environ.get("RLM_FB_LOSS_SEED", "20260827"))
DT = 1.0 / 60.0

# Frames dumped after each loss event.
WINDOW_FRAMES = int(os.environ.get("RLM_FB_LOSS_WINDOW", "90"))

# Cap on detail rows so a long rollout cannot exhaust memory.
MAX_EVENTS_REPORTED = 6
MAX_LEDGER_ROWS = 200000

REPORT_PATH = os.environ.get(
    "RLM_FB_LOSS_REPORT", "tmp_fullback_tackle_loss_ownership.txt"
)

# Movement states that mean "pursuing the carrier" (the CHASE authority).
CHASE_STATES = frozenset({"CHASE", "SHOOT"})


# ═════════════════════════════════════════════════════════════════════
# SMALL HELPERS
# ═════════════════════════════════════════════════════════════════════

def name_of(obj):
    """``obj.name`` when present, else ``str(obj)``, else None."""
    if obj is None:
        return None
    return getattr(obj, "name", None) or str(obj)


def fnum(v):
    """Return *v* as float when numeric, else None (never raises)."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def pid_of(p):
    return getattr(p, "player_id", None)


def deep_get(obj, attr, default=None):
    """Read *attr* off *obj* defensively."""
    try:
        return getattr(obj, attr, default)
    except Exception:
        return default


# ═════════════════════════════════════════════════════════════════════
# THE LEDGER
# ═════════════════════════════════════════════════════════════════════

class Ledger:
    """Accumulator for every measured fact in the rollout."""

    def __init__(self):
        self.frame = 0
        self.phase = None
        self.sub_state = None
        self.engine_phase = None
        self.tes_active = False
        # events
        self.armed_events = []      # a tagged FB was armed as a TES tackler
        self.broken_events = []     # _handle_broken_tackle was called
        self.miss_events = []       # pre-tackle machine applied a non-engage
        self.bc_events = []         # ball-carrier identity changes
        # per-frame fullback ledger: pid -> [row, ...]
        self.fb_rows = {}
        # which pids ever carried the tag
        self.tagged_pids = set()
        self.frames_stepped = 0
        self.open_play_frames = 0
        # previous ball-carrier position, for a measured carrier speed
        self._prev_bc = None

    # ── frame boundary ───────────────────────────────────────────────
    def begin_frame(self, world, open_play):
        self.frame = int(getattr(world, "frame_count", 0) or 0)
        self.phase = name_of(getattr(world, "phase", None))
        self.sub_state = name_of(getattr(world, "sub_state", None))
        self.engine_phase = getattr(open_play, "_phase", None)
        self.tes_active = bool(getattr(world, "tes_active", False))

    # ── event recorders ──────────────────────────────────────────────
    def note_armed(self, d, role_name):
        self.armed_events.append({
            "frame": self.frame,
            "engine_phase": self.engine_phase,
            "pid": pid_of(d),
            "role_name": role_name,
            "tagged": bool(getattr(d, "fullback_role_active", False)),
        })

    def note_broken(self, bump_off, primary, joiners, world):
        ids = []
        if primary is not None:
            ids.append(pid_of(primary))
        for j in (joiners or ()):
            ids.append(pid_of(j))
        tagged_ids = [
            pid_of(p) for p in (self._team(world))
            if getattr(p, "fullback_role_active", False)
        ]
        self.broken_events.append({
            "frame": self.frame,
            "engine_phase": self.engine_phase,
            "bump_off": bool(bump_off),
            "tackler_ids": ids,
            "tagged_fullback_ids": tagged_ids,
            "tagged_fullback_was_primary": any(
                pid_of(primary) == t for t in tagged_ids
            ) if primary is not None else False,
            # STRICT participation: a tagged fullback is "involved" in this
            # teardown ONLY when it is one of the tackler ids the engine
            # actually wrapped.  (Keying on "a tagged fullback exists in the
            # world" produced false positives: an unrelated tackler pair
            # breaking a tackle is not a tackle lost BY the fullback.)
            "tagged_pids_in_tacklers": [i for i in ids if i in tagged_ids],
            "tagged_fullback_involved": any(i in tagged_ids for i in ids),
        })

    def note_miss(self, d, outcome):
        self.miss_events.append({
            "frame": self.frame,
            "engine_phase": self.engine_phase,
            "pid": pid_of(d),
            "outcome": name_of(outcome),
            "tagged": bool(getattr(d, "fullback_role_active", False)),
        })

    @staticmethod
    def _team(world):
        return getattr(world, "defending_team", None) or []

    # ── per-frame sample ─────────────────────────────────────────────
    def sample(self, world, open_play):
        """Snapshot every tagged fullback's complete target/movement state."""
        bc = getattr(world, "ball_carrier", None)
        bc_x = fnum(getattr(bc, "x", None))
        # Measured carrier speed (this frame's displacement / dt).
        bc_speed = None
        if bc is not None and bc_x is not None:
            if self._prev_bc is not None:
                dx = bc_x - self._prev_bc[0]
                dy = (fnum(getattr(bc, "y", 0.0)) or 0.0) - self._prev_bc[1]
                bc_speed = math.hypot(dx, dy) / DT
            self._prev_bc = (bc_x, fnum(getattr(bc, "y", 0.0)) or 0.0)
        # The line anchor is the reference the break predicate uses.
        try:
            anchor_x = float(defensive_line_anchor_m(world))
        except Exception:
            anchor_x = None

        for d in self._team(world):
            tagged = bool(getattr(d, "fullback_role_active", False))
            if tagged:
                self.tagged_pids.add(pid_of(d))
            # Record the body while it carries the tag, OR for a few frames
            # after the tag is lost (so a tag-drop is visible).
            if not tagged and pid_of(d) not in self.tagged_pids:
                continue

            row = self._build_row(d, world, bc, bc_x, anchor_x, tagged,
                                  bc_speed=bc_speed)
            bucket = self.fb_rows.setdefault(pid_of(d), [])
            if len(bucket) < MAX_LEDGER_ROWS:
                bucket.append(row)

    def _build_row(self, d, world, bc, bc_x, anchor_x, tagged,
                   bc_speed=None):
        """One measured row for *d* (the frame's FINAL applied state)."""
        tx = fnum(getattr(d, "target_x", None))
        ty = fnum(getattr(d, "target_y", None))
        x = fnum(getattr(d, "x", 0.0)) or 0.0
        y = fnum(getattr(d, "y", 0.0)) or 0.0

        # Pure predicate reads: WHY could the CHASE override not fire?
        try:
            authority = bool(fb.fullback_tackle_authority_active(d))
        except Exception:
            authority = None
        try:
            engaged = bool(fb.is_fullback_emergency_engaged(d))
        except Exception:
            engaged = None
        try:
            on_break = bool(fb.line_break_active(world, bc))
        except Exception:
            on_break = None
        try:
            anchor_y = fb.compute_fullback_anchor(bc, anchor_x)[1]
            break_kind = fb.classify_fullback_break(
                bc, d, anchor_x, anchor_y
            )
        except Exception:
            break_kind = None

        # CLOSURE METRICS — does the body actually close on the carrier?
        dist_to_bc = None
        if bc is not None:
            dist_to_bc = math.hypot(
                x - (fnum(getattr(bc, "x", 0.0)) or 0.0),
                y - (fnum(getattr(bc, "y", 0.0)) or 0.0),
            )
        try:
            approach_cap = bool(fb.fullback_approach_cap_applies(d))
        except Exception:
            approach_cap = None

        return {
            "frame": self.frame,
            "phase": self.phase,
            "sub_state": self.sub_state,
            "engine_phase": self.engine_phase,
            "tes_active": self.tes_active,
            "pid": pid_of(d),
            "tagged": tagged,
            "role": deep_get(d, "role"),
            "defender_role": name_of(deep_get(d, "defender_role")),
            "movement_state": name_of(deep_get(d, "movement_state")),
            "movement_mode": name_of(deep_get(d, "movement_mode")),
            "target_source": deep_get(d, "target_source"),
            "target_writer": deep_get(d, "_target_writer"),
            "target_writer_detail": deep_get(d, "_target_writer_detail"),
            "target_x": tx,
            "target_y": ty,
            "bc_x": bc_x,
            "x": x,
            "y": y,
            "vx": fnum(getattr(d, "vx", 0.0)),
            "vy": fnum(getattr(d, "vy", 0.0)),
            "move_speed": fnum(getattr(d, "move_speed", None)),
            "frozen": bool(getattr(d, "frozen", False)),
            "freeze_timer": fnum(getattr(d, "freeze_timer", 0.0)),
            "authority_active": authority,
            "emergency_engaged": engaged,
            "tackle_role": deep_get(d, "tackle_role"),
            "tes_tackler_role": deep_get(d, "tes_tackler_role"),
            "tackle_commit_active": bool(
                getattr(d, "_tackle_commit_active", False)
            ),
            "pre_tackle_stance": bool(
                getattr(d, "pre_tackle_stance", False)
            ),
            "pre_tackle_state": name_of(deep_get(d, "pre_tackle_state")),
            "tackle_state_active": bool(
                getattr(d, "tackle_state_active", False)
            ),
            "is_tackler": bool(getattr(d, "is_tackler", False)),
            "pa_state": deep_get(d, "_pretackle_attempt_state"),
            "target_offset": (None if tx is None else abs(tx - x)),
            "dist_to_bc": dist_to_bc,
            "bc_speed": bc_speed,
            "fb_speed": fnum(getattr(d, "move_speed", None)),
            "approach_cap": approach_cap,
            "line_break_active": on_break,
            "break_kind": break_kind,
            "line_anchor_x": anchor_x,
        }


# ═════════════════════════════════════════════════════════════════════
# SPY INSTALLATION
# ═════════════════════════════════════════════════════════════════════

def install_spies(open_play, ledger):
    """Wrap the real entry points; every wrapper calls the original."""
    real_arm = OpenPlayEngine._arm_tackler_role
    real_broken = open_play._handle_broken_tackle
    real_outcome = pm.apply_outcome

    def arm_tackler_role(ope_self, d, role_enum, role_name, ctx):
        res = real_arm(ope_self, d, role_enum, role_name, ctx)
        try:
            ledger.note_armed(d, role_name)
        except Exception:
            pass
        return res

    def broken(*a, **kw):
        # Bound as an INSTANCE attribute, so no ``self`` is passed: the
        # engine object comes from the closure, and ``real_broken`` is
        # already a bound method.
        # ``bump_off`` may arrive positionally or by keyword.
        bump = bool(kw.get("bump_off", False))
        if a:
            bump = bool(a[0])
        world = getattr(open_play, "world", None)
        primary = getattr(open_play, "primary", None)
        joiners = list(getattr(open_play, "joiners", None) or ())
        try:
            ledger.note_broken(bump, primary, joiners, world)
        except Exception:
            pass
        return real_broken(*a, **kw)

    def outcome(world, defender, bc, tuners, state=None, *a, **kw):
        res = real_outcome(world, defender, bc, tuners, state=state, *a, **kw)
        try:
            if name_of(res) != "CLEAN_ENGAGE":
                ledger.note_miss(defender, res)
        except Exception:
            pass
        return res

    OpenPlayEngine._arm_tackler_role = arm_tackler_role
    open_play._handle_broken_tackle = broken
    pm.apply_outcome = outcome

    return {
        "arm": real_arm,
        "broken": real_broken,
        "outcome": real_outcome,
    }


def uninstall_spies(open_play, originals):
    """Restore the real callables (probe hygiene)."""
    OpenPlayEngine._arm_tackler_role = originals["arm"]
    open_play._handle_broken_tackle = originals["broken"]
    pm.apply_outcome = originals["outcome"]
# ═════════════════════════════════════════════════════════════════════
# THE ROLLOUT — the real engine, booted exactly like
# probe_stuck_carrier_after_miss_bump.py
# ═════════════════════════════════════════════════════════════════════

def run_rollout(frames=MAX_FRAMES, seed=SEED, candidacy=None, verbose=False):
    """Boot the engine, kick off, step it, and return the measured ledger.

    ``candidacy``: None (shipped default), or a raw value for
    ``RLM_FULLBACK_TACKLE_CANDIDACY`` (the A/B control that revokes the tag
    at TES arming).
    """
    var = "RLM_FULLBACK_TACKLE_CANDIDACY"
    prev = os.environ.get(var)
    if candidacy is None:
        os.environ.pop(var, None)
    else:
        os.environ[var] = str(candidacy)

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

    ledger = Ledger()
    originals = install_spies(open_play, ledger)

    try:
        for frame in range(1, frames + 1):
            world.frame_count = frame
            ball.update(DT)

            ledger.begin_frame(world, open_play)

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
            ledger.sample(world, open_play)
            ledger.frames_stepped = frame
            if ledger.engine_phase == "OPEN_PLAY":
                ledger.open_play_frames += 1

            if verbose and frame % 1000 == 0:
                print("    ... frame %d (tagged FB pids=%s)"
                      % (frame, sorted(ledger.tagged_pids)),
                      file=_REAL_STDOUT, flush=True)
    finally:
        uninstall_spies(open_play, originals)
        if prev is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = prev

    return {
        "ledger": ledger,
        "world": world,
        "open_play": open_play,
    }


# ═════════════════════════════════════════════════════════════════════
# ANALYSIS — locate the tackle-loss event and read the ownership
# ═════════════════════════════════════════════════════════════════════

def _rows_by_frame(ledger, pid):
    """{frame: row} for one pid's ledger."""
    return {r["frame"]: r for r in ledger.fb_rows.get(pid, [])}


def find_loss_events(ledger):
    """The ordered tackle-loss events on the TAGGED fullback.

    A loss event is either
      a) a broken-tackle / BUMP_OFF teardown in which a tagged fullback was
         the primary (the carrier beat the last defender outright), or
      b) a pre-tackle MISS outcome applied to a tagged fullback.
    Each event carries the tagged pid whose ledger must be dumped.
    """
    events = []
    tagged = set(ledger.tagged_pids)

    for ev in ledger.broken_events:
        # STRICT: the tagged body must be one of the WRAPPED tacklers.  A
        # teardown between two other defenders is NOT a tackle lost by the
        # fullback and must not enter the event set.
        ours = [i for i in ev["tackler_ids"] if i in tagged]
        if not ours:
            continue
        events.append({
            "frame": ev["frame"],
            "kind": "BUMP_OFF" if ev["bump_off"] else "BROKEN_TACKLE",
            "pid": ours[0],
            "detail": (
                "tacklers=%s tagged=%s primary=%s"
                % (ev["tackler_ids"], ev["tagged_fullback_ids"],
                   ev["tagged_fullback_was_primary"])
            ),
        })

    for ev in ledger.miss_events:
        if not ev["tagged"]:
            continue
        events.append({
            "frame": ev["frame"],
            "kind": "PRE_TACKLE_MISS",
            "pid": ev["pid"],
            "detail": "outcome=%s" % ev["outcome"],
        })

    events.sort(key=lambda e: e["frame"])
    return events


def armed_frames_for(ledger, pid):
    """Frames at which *pid* (a tagged FB) was armed as a TES tackler."""
    return [e["frame"] for e in ledger.armed_events if e["pid"] == pid]


def _fs(v, width=8, prec=3):
    """Format a possibly-None float."""
    if v is None:
        return "None".rjust(width)
    if isinstance(v, str):
        return v.rjust(width)
    return ("%.*f" % (prec, float(v))).rjust(width)


def print_row_line(r, marker=""):
    """One compact ledger line."""
    print(
        "  %s f=%-5s ph=%-16s sub=%-16s state=%-8s src=%-18s writer=%-18s "
        "tx=%s ty=%s bcx=%s x=%s auth=%s eng=%s tr=%-9s tc=%s st=%s "
        "frz=%s ftimer=%s ms=%s"
        % (
            marker.ljust(4),
            r["frame"], str(r["engine_phase"]), str(r["sub_state"]),
            str(r["movement_state"]), str(r["target_source"]),
            str(r["target_writer"]),
            _fs(r["target_x"], 8, 3), _fs(r["target_y"], 8, 3),
            _fs(r["bc_x"], 8, 3), _fs(r["x"], 8, 3),
            str(r["authority_active"]), str(r["emergency_engaged"]),
            str(r["tackle_role"]), str(r["tackle_commit_active"]),
            str(r["pre_tackle_stance"]),
            str(r["frozen"]), _fs(r["freeze_timer"], 6, 3),
            _fs(r["move_speed"], 6, 3),
        )
    )


def print_first_frame_verdict(r):
    """The requested first-frame dump, one field per line, with explanation."""
    print("      ── FIRST FRAME AFTER THE LOSS ──")
    print("        role                    = %s" % r["role"])
    print("        defender_role           = %s" % r["defender_role"])
    print("        movement_state          = %s" % r["movement_state"])
    print("        movement_mode           = %s" % r["movement_mode"])
    print("        target_source           = %s" % r["target_source"])
    print("        target_x / target_y     = %s / %s"
          % (_fs(r["target_x"], 10, 3), _fs(r["target_y"], 10, 3)))
    print("        bc_x                    = %s"
          % _fs(r["bc_x"], 10, 3))
    print("        LAST TARGET WRITER      = %s   (detail=%s)"
          % (r["target_writer"], r["target_writer_detail"]))
    print("        |target_x - x|          = %s"
          % _fs(r["target_offset"], 10, 3))
    print("        vx / vy                 = %s / %s"
          % (_fs(r["vx"], 10, 4), _fs(r["vy"], 10, 4)))
    print("        --- WHY THE CHASE OVERRIDE COULD NOT FIRE ---")
    print("        fullback_tackle_authority_active = %s" % r["authority_active"])
    print("          _fb_emergency_engaged           = %s" % r["emergency_engaged"])
    print("          tackle_role                     = %s" % r["tackle_role"])
    print("          _tackle_commit_active           = %s" % r["tackle_commit_active"])
    print("          pre_tackle_stance               = %s" % r["pre_tackle_stance"])
    print("        line_break_active       = %s" % r["line_break_active"])
    print("        classify_fullback_break = %s" % r["break_kind"])
    print("        line_anchor_x           = %s   (bc_x - anchor = %s)"
          % (_fs(r["line_anchor_x"], 10, 3),
             _fs(None if (r["bc_x"] is None or r["line_anchor_x"] is None)
                 else r["bc_x"] - r["line_anchor_x"], 10, 3)))
    print("        frozen / freeze_timer   = %s / %s"
          % (r["frozen"], _fs(r["freeze_timer"], 8, 3)))
    print("        move_speed              = %s"
          % _fs(r["move_speed"], 8, 3))
    print("        engine phase            = %s / sub=%s / tes_active=%s"
          % (r["engine_phase"], r["sub_state"], r["tes_active"]))


def explain_ownership(r):
    """Name the mechanism that kept DEPTH ownership on this frame."""
    state = r["movement_state"]
    src = str(r["target_source"])
    writer = str(r["target_writer"])
    if r["authority_active"]:
        who = []
        if r["emergency_engaged"]:
            who.append("_fb_emergency_engaged")
        if r["tackle_role"] in ("PRIMARY", "SECONDARY"):
            who.append("tackle_role=%s" % r["tackle_role"])
        if r["tackle_commit_active"]:
            who.append("_tackle_commit_active")
        if r["pre_tackle_stance"]:
            who.append("pre_tackle_stance")
        return (
            "DEPTH WRITER YIELDED: fullback_tackle_authority_active is True "
            "(%s), so apply_fullback_geometry returned WITHOUT writing. The "
            "command in force is whatever the last tackle-side writer left "
            "(writer=%s, source=%s, state=%s)."
            % (", ".join(who) or "?", writer, src, state)
        )
    if str(r["break_kind"]) == "NONE":
        return (
            "DEPTH RE-ASSERTED, NO BREAK CLASSIFIED: the geometry writer ran "
            "the anchor branch and stamped source=%s / state=%s because "
            "classify_fullback_break returned NONE (bc_x - anchor = %.3f, "
            "lateral channel within tolerance). The breakaway CHASE override "
            "is unreachable while the predicate reads NONE."
            % (src, state,
               (r["bc_x"] - r["line_anchor_x"])
               if (r["bc_x"] is not None and r["line_anchor_x"] is not None)
               else float("nan"))
        )
    if state in CHASE_STATES:
        return ("CHASE AUTHORITY ACTIVE (state=%s, writer=%s)."
                % (state, writer))
    return (
        "UNCLASSIFIED: state=%s source=%s writer=%s authority=%s break=%s."
        % (state, src, writer, r["authority_active"], r["break_kind"])
    )


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


def histogram(rows, key_fn, limit=20):
    hist = {}
    for r in rows:
        k = key_fn(r)
        hist[k] = hist.get(k, 0) + 1
    return sorted(hist.items(), key=lambda kv: -kv[1])[:limit]


def print_hist(title, rows, key_fn, n=12):
    print("  -- %s --" % title)
    for k, v in histogram(rows, key_fn, n):
        print("    %-40s = %d" % (str(k), v))


def report(res):
    ledger = res["ledger"]
    banner("FULLBACK TACKLE-LOSS OWNERSHIP — MEASURED")
    print("  seed                          : %d" % SEED)
    print("  frames stepped                : %d" % ledger.frames_stepped)
    print("  OPEN_PLAY frames (engine's)   : %d" % ledger.open_play_frames)
    print("  dt                            : %.6f s" % DT)
    print("  tagged fullback pids          : %s" % sorted(ledger.tagged_pids))

    sub("A. COVERAGE — did the instrument observe the emergency path?")
    print("  _arm_tackler_role calls       : %d" % len(ledger.armed_events))
    tagged_armed = [e for e in ledger.armed_events if e["tagged"]]
    print("  of those, on a TAGGED fullback: %d" % len(tagged_armed))
    if tagged_armed:
        print("  first tagged arming frame     : %d"
              % tagged_armed[0]["frame"])
    print("  _handle_broken_tackle calls   : %d" % len(ledger.broken_events))
    involved = [e for e in ledger.broken_events
                if e["tagged_fullback_involved"]]
    print("  of those, tagged FB involved  : %d" % len(involved))
    for e in ledger.broken_events[:12]:
        print("    f=%-6s bump_off=%-5s tacklers=%-14s tagged=%-14s primary=%s"
              % (e["frame"], e["bump_off"], e["tackler_ids"],
                 e["tagged_fullback_ids"], e["tagged_fullback_was_primary"]))
    print("  apply_outcome non-engage calls: %d" % len(ledger.miss_events))
    tagged_miss = [e for e in ledger.miss_events if e["tagged"]]
    print("  of those, on a TAGGED fullback: %d" % len(tagged_miss))
    if tagged_miss:
        print_hist("miss outcome on a tagged FB", tagged_miss,
                   lambda r: r["outcome"], 8)

    total_rows = sum(len(v) for v in ledger.fb_rows.values())
    print("  fullback ledger rows          : %d" % total_rows)
    if not tagged_armed and not tagged_miss:
        print("")
        print("  !! The tagged fullback was never armed and never missed in this")
        print("     rollout.  Sections C-E are INCONCLUSIVE.")

    # ── C. THE LOSS EVENTS ──
    events = find_loss_events(ledger)
    sub("C. THE TACKLE-LOSS EVENTS (a tagged fullback beaten by the carrier)")
    print("  loss events found             : %d" % len(events))
    for e in events[:MAX_EVENTS_REPORTED]:
        print("    f=%-6s kind=%-16s pid=%-5s %s"
              % (e["frame"], e["kind"], e["pid"], e["detail"]))

    if events:
        for idx, ev in enumerate(events[:MAX_EVENTS_REPORTED]):
            pid = ev["pid"]
            rows = _rows_by_frame(ledger, pid)
            f0 = ev["frame"]
            print("")
            print("  ═══════════════════════════════════════════════════════")
            print("  EVENT %d — frame %d, kind=%s, pid=%s" % (idx, f0, ev["kind"], pid))
            print("  ═══════════════════════════════════════════════════════")
            first = rows.get(f0)
            if first is not None:
                print_first_frame_verdict(first)
                print("")
                print("        → VERDICT: %s" % explain_ownership(first))
            else:
                print("      (no ledger row for the event frame — body untracked)")

            # The window.
            print("")
            print("  ── LEDGER WINDOW (frames %d..%d) ──" % (f0, f0 + WINDOW_FRAMES))
            armed_fs = set(armed_frames_for(ledger, pid))
            shown = 0
            for fr in range(f0, f0 + WINDOW_FRAMES + 1):
                r = rows.get(fr)
                if r is None:
                    continue
                marker = "ARMD" if fr in armed_fs else ""
                print_row_line(r, marker)
                shown += 1
                if shown >= 60:
                    print("  ... (window truncated at 60 lines)")
                    break

    # ── D. OWNERSHIP DISTRIBUTION OVER POST-LOSS FRAMES ──
    sub("D. OWNERSHIP OF THE TAGGED FULLBACK (all its frames after a loss)")
    post_loss_rows = []
    for ev in events:
        pid = ev["pid"]
        rows = _rows_by_frame(ledger, pid)
        for fr in range(ev["frame"], ev["frame"] + WINDOW_FRAMES + 1):
            r = rows.get(fr)
            if r is not None:
                post_loss_rows.append(r)
    if post_loss_rows:
        print("  post-loss fullback frames sampled : %d" % len(post_loss_rows))
        print_hist("movement_state", post_loss_rows,
                   lambda r: r["movement_state"], 10)
        print_hist("target_source", post_loss_rows,
                   lambda r: r["target_source"], 12)
        print_hist("LAST TARGET WRITER (_target_writer)", post_loss_rows,
                   lambda r: r["target_writer"], 12)
        n_auth = sum(1 for r in post_loss_rows if r["authority_active"])
        n_chase = sum(1 for r in post_loss_rows
                      if r["movement_state"] in CHASE_STATES)
        n_depth = sum(1 for r in post_loss_rows
                      if r["target_writer"] == "fullback_geometry")
        print("")
        print("  authority_active True         : %d / %d"
              % (n_auth, len(post_loss_rows)))
        print("  movement_state CHASE/SHOOT    : %d / %d"
              % (n_chase, len(post_loss_rows)))
        print("  last writer fullback_geometry : %d / %d"
              % (n_depth, len(post_loss_rows)))
        print_hist("break_kind while post-loss", post_loss_rows,
                   lambda r: r["break_kind"], 6)
        print_hist("frozen while post-loss", post_loss_rows,
                   lambda r: r["frozen"], 4)
    else:
        print("  (no post-loss frames captured)")

    # ── E. THE VERDICT ──
    banner("VERDICT — measured, not inferred")
    if not events:
        print("  No tackle-loss event on a tagged fullback occurred, so the")
        print("  ownership question was not exercised.  Nothing is claimed.")
        return
    first_rows = []
    for ev in events:
        r = _rows_by_frame(ledger, ev["pid"]).get(ev["frame"])
        if r is not None:
            first_rows.append(r)
    n_depth_owned = sum(
        1 for r in first_rows
        if r["movement_state"] not in CHASE_STATES
        and r["target_writer"] == "fullback_geometry"
    )
    n_yielded = sum(1 for r in first_rows if r["authority_active"])
    n_no_break = sum(1 for r in first_rows
                     if str(r["break_kind"]) == "NONE")
    print("  first-frame rows examined     = %d" % len(first_rows))
    print("  still DEPTH-owned (writer=fullback_geometry, not CHASE) = %d"
          % n_depth_owned)
    print("  depth writer YIELDED (authority_active True)            = %d"
          % n_yielded)
    print("  break predicate read NONE                               = %d"
          % n_no_break)

    # ── CLOSURE: did the beat defender actually pursue the carrier? ──
    live_break = [r for r in post_loss_rows
                  if str(r["break_kind"]) == "DEPTH"]
    if live_break:
        n_capped = sum(1 for r in live_break if r["approach_cap"])
        caps = [r["fb_speed"] for r in live_break
                if r["fb_speed"] is not None]
        bcs = [r["bc_speed"] for r in live_break
               if r["bc_speed"] is not None]
        dists = [r["dist_to_bc"] for r in live_break
                 if r["dist_to_bc"] is not None]
        print("")
        print("  -- CLOSURE while a genuine DEPTH break is live (%d frames) --"
              % len(live_break))
        print("     sweeper approach cap APPLIES on        : %d / %d"
              % (n_capped, len(live_break)))
        if caps:
            print("     defender move_speed  mean/max           : %.3f / %.3f m/s"
                  % (sum(caps) / len(caps), max(caps)))
        if bcs:
            print("     carrier  speed       mean/max           : %.3f / %.3f m/s"
                  % (sum(bcs) / len(bcs), max(bcs)))
        if dists:
            print("     defender->carrier distance  first/last  : %.3f -> %.3f m"
                  % (dists[0], dists[-1]))
            print("     distance GREW over the run              : %s"
                  % ("YES" if dists[-1] > dists[0] else "no"))
        n_cap_and_slow = sum(
            1 for r in live_break
            if r["approach_cap"] and (r["fb_speed"] or 0.0)
            < 2.5
        )
        print("     capped AND slower than 2.5 m/s          : %d / %d"
              % (n_cap_and_slow, len(live_break)))


# ═════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════

def main():
    global _REPORT
    real = sys.stdout

    _devnull = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = _devnull
    try:
        res = run_rollout()
    finally:
        sys.stdout = real
        try:
            _devnull.close()
        except Exception:
            pass

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
