"""probe_open_play_stall_authority.py — WHO pins a body still in OPEN_PLAY?

THE QUESTION
------------
The engine now reaches OPEN_PLAY cleanly (RELEASE -> PTB -> OPEN_PLAY all
progress), yet some bodies sit still in OPEN_PLAY holding a target they are
not parked on, with ``frozen`` False.  The earlier instrument proved the
carrier's stall is not a freeze.  This probe asks the NEXT question: which
authority owns those bodies' final target / decision, and why did it fire.

THE DECLARED CANDIDATE AUTHORITIES (order = the order they can stop a body)
--------------------------------------------------------------------------
  1. ``frozen`` / ``freeze_timer`` — the freeze pin.  Excluded by the stalled
     predicate, recorded on every row so the exclusion is visible.
  2. ``seed_hold.apply_seed_hold`` — the stale-seed lockdown.  Writes
     ``target_y = frame-start Y``, clamps ``target_x`` into the anchor band,
     sets ``move_speed = 0`` / ``vx = vy = 0``, and maps to
     ``PRIORITY_SEED_HOLD = 800`` in the writer-priority bands, so it OUTRANKS
     spacing (50) / width (50) / MMDL (60) / ruck pin (200) and becomes the
     frame's final target.  This is the only authority that produces the
     signature with ``frozen`` False.
  3. ``movement_mode_decision.decide_movement_state`` returning HOLD for the
     same staleness predicate (the MMDL force-hold runs AFTER the last-resort
     stuck fallback, so it deliberately overrides the anti-stall rescue).
  4. The committed-tackler / pre-tackle owner writers, which legitimately
     write ``target_x = d.x`` + ``move_speed = 0`` at TES arming.
  5. ``_run_idle_open_play_pursuit`` — the engaged-only idle branch, which
     SKIPS frozen bodies, so a body still frozen here keeps target == x.
  6. The preamble/dispatch gate asymmetry: ``_ntnd_active()`` is evaluated
     twice per frame and the attack pipeline moves the carrier between the
     two calls, so the full defensive dispatch can run on a frame whose
     geometry preamble did NOT.

MEASUREMENT, NOT ARGUMENT
-------------------------
The probe wraps (never replaces) the real entry points — the preamble, the
full dispatch, the idle branch, ``apply_seed_hold`` and
``decide_movement_state`` — and records, per frame, exactly which of them
ran.  A body is then attributed by the evidence it actually carries
(``target_source``, the MMDL verdict for that body, the seed-hold list, the
freeze flags), never by inference.

THE STALLED PREDICATE (stated once, used everywhere)
----------------------------------------------------
    stalled <=> frozen is False
            AND hypot(vx, vy) < STALL_SPEED_MPS
            AND (|target_x - x| > TARGET_EPS OR |target_y - y| > TARGET_EPS)

Read-only instrument: no gameplay file is imported for writing, no module
constant is changed in the shipped engine (the one monkeypatch is on a
probe-local wrapper that calls the original unchanged).

Run:  python probe_open_play_stall_authority.py
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

# The engine's own per-frame physics prints would drown the probe's report.
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

from game.defence.defence_lvl_1 import seed_hold as seed_hold_mod
from game.defence.defence_lvl_1 import defensive_behaviour_unified as dbu_mod
from game.defence.defence_lvl_1 import defensive_pipeline as pipeline_mod
from game.defence.defence_lvl_1 import movement_mode_decision as mmd_mod
from game.defence.defence_lvl_1.seed_hold import is_seed_stale

# ── THE STALLED PREDICATE ────────────────────────────────────────────
STALL_SPEED_MPS = 0.05
TARGET_EPS_M = 0.05
# A body whose target_x is within this of its own x is a pinned X.
PINNED_X_EPS_M = 0.05

MAX_FRAMES = 6000
SEED = 20260827

# Storage caps (the per-frame aggregate ledger is complete; detail rows are
# capped so a long rollout cannot exhaust memory).
MAX_STALL_ROWS = 6000
EXAMPLE_FRAME_LIMIT = 40
EXAMPLE_BODIES_PER_FRAME = 16

REPORT_PATH = "tmp_open_play_stall_report.txt"

# Roles that hold a dedicated movement owner and are therefore EXPECTED to be
# still / pinned at some point (markers, DH, TES tacklers, chase bodies).
DEDICATED_OWNER_ROLES = frozenset({
    "MARKER_A", "MARKER_B", "DUMMY_HALF",
    "TACKLER_1", "TACKLER_2", "TACKLER_3",
    "DEF_CHASE", "KICK_CHASE",
})
# ═════════════════════════════════════════════════════════════════════
# THE SPIES — which entry point ran, on which frame, for which bodies
# ═════════════════════════════════════════════════════════════════════
# Every spy calls the real method UNCHANGED and records what it saw.  The
# engine's own behaviour is therefore byte-identical with and without them;
# the only patch that could matter is ``seed_hold.apply_seed_hold``, which
# the manager resolves by a function-local import at call time, so patching
# the module attribute intercepts exactly the real call.

class FrameSpy:
    """Per-frame record of which OPEN_PLAY entry points actually ran."""

    def __init__(self):
        self.current = None
        self.frames = []

    def begin(self, frame):
        self.current = {
            "frame": frame,
            "preamble": 0,
            "dispatch": 0,
            "idle": 0,
            "seed_hold": [],        # player_ids held by apply_seed_hold
            "seed_hold_n": 0,
            "mmdl_hold": [],        # player_ids the MMDL decided HOLD
            "mmdl_calls": 0,
            "publish_sources": [],
        }

    def end(self):
        if self.current is not None:
            self.frames.append(self.current)
        self.current = None

    def note_preamble(self):
        if self.current is not None:
            self.current["preamble"] += 1

    def note_dispatch(self):
        if self.current is not None:
            self.current["dispatch"] += 1

    def note_idle(self):
        if self.current is not None:
            self.current["idle"] += 1

    def note_seed_hold(self, ids):
        if self.current is None:
            return
        self.current["seed_hold_n"] += len(ids)
        self.current["seed_hold"].extend(ids)

    def note_mmdl(self, pid, state_name):
        if self.current is None:
            return
        self.current["mmdl_calls"] += 1
        if state_name == "HOLD":
            self.current["mmdl_hold"].append(pid)


def install_spies(open_play, spy):
    """Wrap the real entry points.  Each wrapper calls the original."""
    real_preamble = open_play.defence_manager.run_frame_preamble
    real_dispatch = open_play.defence_manager.update_open_play
    real_idle = open_play._run_idle_open_play_pursuit
    real_seed_hold = seed_hold_mod.apply_seed_hold
    real_decide = dbu_mod.decide_movement_state
    real_publish = pipeline_mod.stamp_frame_geometry_publish

    def preamble(world, *a, **kw):
        spy.note_preamble()
        return real_preamble(world, *a, **kw)

    def dispatch(ope, dt, *a, **kw):
        spy.note_dispatch()
        return real_dispatch(ope, dt, *a, **kw)

    def idle(dt, *a, **kw):
        spy.note_idle()
        return real_idle(dt, *a, **kw)

    def seed_hold(world, defenders, pre_pos=None, *a, **kw):
        held = real_seed_hold(world, defenders, pre_pos=pre_pos, *a, **kw)
        try:
            spy.note_seed_hold(
                [getattr(d, "player_id", None) for d in (held or [])]
            )
        except Exception:
            pass
        return held

    def decide(world, defender, bc, tuners=None, *a, **kw):
        state = real_decide(world, defender, bc, tuners, *a, **kw)
        try:
            spy.note_mmdl(
                getattr(defender, "player_id", None),
                getattr(state, "name", None) or str(state),
            )
        except Exception:
            pass
        return state

    def publish(world, source, *a, **kw):
        if spy.current is not None:
            spy.current["publish_sources"].append(source)
        return real_publish(world, source, *a, **kw)

    open_play.defence_manager.run_frame_preamble = preamble
    open_play.defence_manager.update_open_play = dispatch
    open_play._run_idle_open_play_pursuit = idle
    seed_hold_mod.apply_seed_hold = seed_hold
    dbu_mod.decide_movement_state = decide
    pipeline_mod.stamp_frame_geometry_publish = publish


def name_of(obj):
    """``obj.name`` when present, else ``str(obj)``, else ``None``."""
    if obj is None:
        return None
    return getattr(obj, "name", None) or str(obj)


def fnum(v):
    """Return *v* as float when numeric, else None (never raises)."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ═════════════════════════════════════════════════════════════════════
# THE SEED-STALENESS CLAUSES — named individually so the trigger is legible
# ═════════════════════════════════════════════════════════════════════

_SEED_LANE_ROLES = frozenset({"A", "B", "C", "D", "CENTRE", "WINGER"})


def seed_stale_clauses(d, world):
    """Return the list of ``is_seed_stale`` clauses that are TRUE for *d*.

    Mirrors ``seed_hold.is_seed_stale`` clause for clause, so a stalled body's
    row names WHICH precondition failed instead of only that one did.
    """
    clauses = []
    if bool(getattr(d, "fullback_role_active", False)):
        clauses.append("EXEMPT_fullback")
        return clauses
    if bool(getattr(d, "frozen", False)):
        clauses.append("EXEMPT_frozen")
        return clauses
    if float(getattr(d, "freeze_timer", 0.0) or 0.0) > 0.0:
        clauses.append("EXEMPT_freeze_timer")
        return clauses
    if bool(getattr(d, "tackle_state_active", False)):
        clauses.append("EXEMPT_tackle_state")
        return clauses

    if bool(getattr(world, "ruck_established", True)):
        role = getattr(d, "depth_role", None)
        if role is None or role not in _SEED_LANE_ROLES:
            clauses.append("depth_role_invalid")
        if getattr(d, "depth_side", None) is None:
            clauses.append("depth_side_None")
        if not bool(getattr(d, "_depth_seeded", False)):
            clauses.append("_depth_seeded_False")
        if getattr(d, "role_depth_target_x", None) is None:
            clauses.append("role_depth_target_x_None")
        if getattr(d, "role_depth_target_y", None) is None:
            clauses.append("role_depth_target_y_None")

    if not pipeline_mod.frame_geometry_is_fresh(world):
        clauses.append("geometry_not_fresh")
    dct = getattr(world, "frame_spacing_targets", None)
    if not dct:
        clauses.append("spacing_dict_empty")
    elif id(d) not in dct:
        clauses.append("not_in_spacing_dict")
    if getattr(d, "frame_spacing_y", None) is None:
        clauses.append("frame_spacing_y_None")
    return clauses


# ═════════════════════════════════════════════════════════════════════
# THE PER-BODY ROW
# ═════════════════════════════════════════════════════════════════════

def body_row(frame, world, open_play, d, spy_frame):
    """One row: every authority's verdict on *d* for this frame."""
    vx = fnum(getattr(d, "vx", 0.0)) or 0.0
    vy = fnum(getattr(d, "vy", 0.0)) or 0.0
    x = fnum(getattr(d, "x", 0.0)) or 0.0
    y = fnum(getattr(d, "y", 0.0)) or 0.0
    tx = fnum(getattr(d, "target_x", None))
    ty = fnum(getattr(d, "target_y", None))
    speed = math.hypot(vx, vy)
    off_x = None if tx is None else abs(tx - x)
    off_y = None if ty is None else abs(ty - y)

    spacing = getattr(world, "frame_spacing_targets", None) or {}
    pins = getattr(world, "frame_pin_targets", None) or {}
    role = getattr(d, "defender_role", None)
    clauses = seed_stale_clauses(d, world)
    real_clauses = [c for c in clauses if not c.startswith("EXEMPT_")]

    row = {
        "frame": frame,
        "pid": getattr(d, "player_id", None),
        "role": name_of(role),
        "depth_role": getattr(d, "depth_role", None),
        "depth_side": getattr(d, "depth_side", None),
        "depth_class": getattr(d, "depth_class", None),
        "tackle_role": getattr(d, "tackle_role", None),
        "x": round(x, 3),
        "y": round(y, 3),
        "vx": round(vx, 4),
        "vy": round(vy, 4),
        "speed": round(speed, 4),
        "target_x": None if tx is None else round(tx, 3),
        "target_y": None if ty is None else round(ty, 3),
        "off_x": None if off_x is None else round(off_x, 3),
        "off_y": None if off_y is None else round(off_y, 3),
        "pinned_x": (off_x is not None and off_x <= PINNED_X_EPS_M),
        "pinned_y": (off_y is not None and off_y <= PINNED_X_EPS_M),
        "move_speed": fnum(getattr(d, "move_speed", None)),
        "movement_state": name_of(getattr(d, "movement_state", None)),
        "target_source": getattr(d, "target_source", None),
        "band_axis": getattr(d, "_band_axis", None),
        "frozen": bool(getattr(d, "frozen", False)),
        "freeze_timer": fnum(getattr(d, "freeze_timer", 0.0)),
        "tackle_state_active": bool(getattr(d, "tackle_state_active", False)),
        "pretackle_state": name_of(getattr(d, "pre_tackle_state", None)),
        "tackle_commit": bool(getattr(d, "_tackle_commit_active", False)),
        "pursuit_commit": bool(getattr(d, "_pursuit_commit_active", False)),
        "tes_attached": bool(getattr(d, "tes_attached", False)),
        "is_tackler": bool(getattr(d, "is_tackler", False)),
        "fullback": bool(getattr(d, "fullback_role_active", False)),
        "mmdl_moved": bool(getattr(d, "_mmdl_moved", False)),
        "arrival_mode": bool(getattr(d, "arrival_mode", False)),
        "depth_seeded": bool(getattr(d, "_depth_seeded", False)),
        "role_tx_present": getattr(d, "role_depth_target_x", None) is not None,
        "role_ty_present": getattr(d, "role_depth_target_y", None) is not None,
        "frame_spacing_y": fnum(getattr(d, "frame_spacing_y", None)),
        "in_spacing_dict": id(d) in spacing,
        "in_pin_dict": id(d) in pins,
        "preamble_ran": spy_frame.get("preamble", 0) > 0,
        "dispatch_ran": spy_frame.get("dispatch", 0) > 0,
        "idle_ran": spy_frame.get("idle", 0) > 0,
        "seed_held_this_frame": getattr(d, "player_id", None)
        in (spy_frame.get("seed_hold") or []),
        "mmdl_hold_this_frame": getattr(d, "player_id", None)
        in (spy_frame.get("mmdl_hold") or []),
        "seed_clauses": real_clauses,
        "seed_stale": bool(is_seed_stale(d, world)),
        "dedicated_owner_role": (
            name_of(role) in DEDICATED_OWNER_ROLES
        ),
    }
    row["stalled"] = bool(
        row["frozen"] is False
        and speed < STALL_SPEED_MPS
        and (
            (off_x is not None and off_x > TARGET_EPS_M)
            or (off_y is not None and off_y > TARGET_EPS_M)
        )
    )
    return row


def attribute(row):
    """Name the authority that owns this row's final target / decision.

    Order matches the declared candidate list.  Every branch reads evidence
    the engine itself stamped, so the label is a measurement.
    """
    src = row.get("target_source") or ""
    if row.get("frozen"):
        return "1_FREEZE_PIN"
    if row.get("seed_held_this_frame") or "seed_hold" in src:
        return "2_SEED_HOLD"
    if row.get("mmdl_hold_this_frame"):
        return "3_MMDL_HOLD"
    # ── SOURCE STAMP OUTRANKS EVERY DERIVED SHIM FLAG (2026-09-25) ──
    # `target_source` is WRITTEN by the authority that authored the target;
    # the flags below it are DERIVED.  In particular
    # `_sync_shims` sets `_tackle_commit_active = True` for every
    # COMMIT/PLANTED pre-tackle body, so a PRE_TACKLE row carries
    # `tackle_commit == True` without any tackler-commit writer having run.
    # Testing the shim first mislabelled 11 of the 13 pre_tackle stalls as
    # "4_TACKLER_COMMIT" and sent the first attribution pass after the wrong
    # module.  The stamp is the measurement; only when it is absent do the
    # flags have any say.
    if "pursuit_commit" in src:
        return "4_PURSUIT_COMMIT"
    if "tackler_commit" in src:
        return "4_TACKLER_COMMIT"
    if "pre_tackle" in src or "pta_" in src or "pretackle" in src:
        return "4_PRE_TACKLE"
    if "tackle_state" in src:
        return "4_TACKLE_STATE"
    # ── FLAG-ONLY FALLBACKS (no usable source stamp) ──
    if row.get("pretackle_state") not in (None, "NONE"):
        return "4_PRE_TACKLE"
    if row.get("tackle_commit"):
        return "4_TACKLER_COMMIT"
    if row.get("pursuit_commit"):
        return "4_PURSUIT_COMMIT"
    if row.get("tackle_state_active"):
        return "4_TACKLE_STATE"
    if row.get("idle_ran") and row.get("frozen"):
        return "5_IDLE_SKIPS_FROZEN"
    if row.get("dispatch_ran") and not row.get("preamble_ran"):
        return "6_GATE_ASYMMETRY"
    if src.startswith("band:"):
        return "7_BAND_WINNER_" + src.split(":", 1)[1]
    if "freeze_pin" in src:
        return "1_FREEZE_PIN"
    return "8_UNNAMED_SRC_" + (src or "none")
# ═════════════════════════════════════════════════════════════════════
# THE ROLLOUT — the real engine, booted exactly like probe_release_deadlock
# ═════════════════════════════════════════════════════════════════════

def run_rollout(frames=MAX_FRAMES, seed=SEED, verbose=False):
    """Boot, kick off, step the real engine; return the per-frame ledger."""
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

    spy = FrameSpy()
    install_spies(open_play, spy)

    frame_rows = []      # per OPEN_PLAY frame: spy summary + counts
    stall_rows = []      # detail rows for stalled bodies (capped)
    examples = []        # (frame, [detail rows for every body]) for examples
    dt = 1.0 / 60.0

    for frame in range(1, frames + 1):
        world.frame_count = frame
        ball.update(dt)

        spy.begin(frame)
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
        spy.end()

        if verbose and frame % 500 == 0:
            print("    ... frame %d" % frame)

        # ── snapshot ONLY while the engine owns OPEN_PLAY ──
        if getattr(open_play, "_phase", None) != "OPEN_PLAY":
            continue
        if world.phase != GamePhase.OPEN_PLAY:
            continue
        if is_tackle_phase(world):
            continue

        sframe = spy.frames[-1] if spy.frames else {}
        defenders = list(getattr(world, "defending_team", None) or [])
        rows = [
            body_row(frame, world, open_play, d, sframe) for d in defenders
        ]
        stalled = [r for r in rows if r["stalled"]]

        frame_rows.append({
            "frame": frame,
            "n_bodies": len(rows),
            "n_stalled": len(stalled),
            "n_frozen": sum(1 for r in rows if r["frozen"]),
            "n_seed_stale": sum(1 for r in rows if r["seed_stale"]),
            "preamble": sframe.get("preamble", 0),
            "dispatch": sframe.get("dispatch", 0),
            "idle": sframe.get("idle", 0),
            "seed_hold_n": sframe.get("seed_hold_n", 0),
            "seed_hold_ids": list(sframe.get("seed_hold") or []),
            "mmdl_hold_ids": list(sframe.get("mmdl_hold") or []),
            "mmdl_calls": sframe.get("mmdl_calls", 0),
            "publish_sources": list(sframe.get("publish_sources") or []),
            "attribution": {},
            "clauses": {},
        })
        for r in stalled:
            a = attribute(r)
            frame_rows[-1]["attribution"][a] = (
                frame_rows[-1]["attribution"].get(a, 0) + 1
            )
            key = ",".join(r["seed_clauses"]) or "<no clause>"
            frame_rows[-1]["clauses"][key] = (
                frame_rows[-1]["clauses"].get(key, 0) + 1
            )

        if stall_rows and len(stall_rows) >= MAX_STALL_ROWS:
            pass
        else:
            for r in stalled:
                if len(stall_rows) < MAX_STALL_ROWS:
                    stall_rows.append(r)

        if stalled and len(examples) < EXAMPLE_FRAME_LIMIT:
            examples.append((frame, rows[:EXAMPLE_BODIES_PER_FRAME]))

    return {
        "frame_rows": frame_rows,
        "stall_rows": stall_rows,
        "examples": examples,
        "spy_frames": spy.frames,
        "world": world,
        "open_play": open_play,
    }


# ═════════════════════════════════════════════════════════════════════
# ANALYSIS
# ═════════════════════════════════════════════════════════════════════

def stall_runs_by_player(stall_rows):
    """Longest consecutive-frame stall run per player, over OPEN_PLAY frames."""
    per_pid = {}
    for r in stall_rows:
        per_pid.setdefault(r["pid"], []).append(r["frame"])
    runs = {}
    for pid, frames in per_pid.items():
        frames = sorted(frames)
        best = 1
        cur = 1
        for i in range(1, len(frames)):
            # A gap > 1 frame breaks the run (the body moved in between).
            if frames[i] == frames[i - 1] + 1:
                cur += 1
            else:
                cur = 1
            best = max(best, cur)
        runs[pid] = {"frames": len(frames), "longest_run": best,
                     "first": frames[0], "last": frames[-1]}
    return runs


def aggregate(res):
    frame_rows = res["frame_rows"]
    stall_rows = res["stall_rows"]
    n_open = len(frame_rows)

    preamble_frames = sum(1 for f in frame_rows if f["preamble"] > 0)
    dispatch_frames = sum(1 for f in frame_rows if f["dispatch"] > 0)
    idle_frames = sum(1 for f in frame_rows if f["idle"] > 0)
    # The asymmetry: the full dispatch ran while the preamble did not.
    asym = sum(
        1 for f in frame_rows if f["dispatch"] > 0 and f["preamble"] == 0
    )
    no_geom_publish = sum(
        1 for f in frame_rows
        if "preamble" not in f["publish_sources"]
    )
    seed_hold_frames = sum(1 for f in frame_rows if f["seed_hold_n"] > 0)
    mmdl_hold_frames = sum(1 for f in frame_rows if f["mmdl_hold_ids"])

    attrib = {}
    roles = {}
    states = {}
    sources = {}
    clauses = {}
    for r in stall_rows:
        a = attribute(r)
        attrib[a] = attrib.get(a, 0) + 1
        roles[r["role"]] = roles.get(r["role"], 0) + 1
        states[r["movement_state"]] = states.get(r["movement_state"], 0) + 1
        src = r["target_source"] or "<none>"
        sources[src] = sources.get(src, 0) + 1
        key = ",".join(r["seed_clauses"]) or "<no clause>"
        clauses[key] = clauses.get(key, 0) + 1

    runs = stall_runs_by_player(stall_rows)
    persistent = {p: v for p, v in runs.items() if v["longest_run"] >= 10}

    return {
        "open_frames": n_open,
        "preamble_frames": preamble_frames,
        "dispatch_frames": dispatch_frames,
        "idle_frames": idle_frames,
        "asymmetry_frames": asym,
        "no_geom_publish_frames": no_geom_publish,
        "seed_hold_frames": seed_hold_frames,
        "mmdl_hold_frames": mmdl_hold_frames,
        "stall_total": len(stall_rows),
        "stall_by_attribution": attrib,
        "stall_by_role": roles,
        "stall_by_state": states,
        "stall_by_source": sources,
        "stall_by_clauses": clauses,
        "runs": runs,
        "persistent": persistent,
    }


# ═════════════════════════════════════════════════════════════════════
# REPORT SINK (the engine's per-frame chatter is suppressed)
# ═════════════════════════════════════════════════════════════════════

_REPORT = None
_REAL_STDOUT = sys.stdout


def silence():
    sys.stdout = open(os.devnull, "w", encoding="utf-8")


def speak():
    if _REPORT is not None:
        sys.stdout = _REPORT


def close_report():
    global _REPORT
    if _REPORT is not None:
        _REPORT.flush()
        _REPORT.close()
        _REPORT = None
    sys.stdout = _REAL_STDOUT


def banner(title):
    print("")
    print("=" * 100)
    print(title)
    print("=" * 100)


def sub(title):
    print("")
    print("-" * 100)
    print(title)
    print("-" * 100)


def topn(dct, n=12):
    return sorted(dct.items(), key=lambda kv: -kv[1])[:n]


def print_body_row(r):
    print("  f=%-5s id=%-4s role=%-14s state=%-8s src=%-28s "
          "spd=%-7s off_x=%-7s off_y=%-7s pin_x=%-5s frozen=%-5s "
          "seed_stale=%-5s hold=%-5s held=%-5s"
          % (
              r["frame"], r["pid"], str(r["role"]), str(r["movement_state"]),
              str(r["target_source"]), r["speed"], r["off_x"], r["off_y"],
              r["pinned_x"], r["frozen"], r["seed_stale"],
              r["mmdl_hold_this_frame"], r["seed_held_this_frame"],
          ))
    print("        x=%-8s y=%-8s tx=%-8s ty=%-8s move_speed=%-6s "
          "depth_role=%-7s side=%-4s seeded=%-5s in_spacing=%-5s "
          "in_pin=%-5s geo_y=%-8s"
          % (
              r["x"], r["y"], r["target_x"], r["target_y"], r["move_speed"],
              str(r["depth_role"]), str(r["depth_side"]), r["depth_seeded"],
              r["in_spacing_dict"], r["in_pin_dict"], r["frame_spacing_y"],
          ))
    print("        clauses=%s | preamble_ran=%s dispatch_ran=%s idle_ran=%s"
          % (r["seed_clauses"], r["preamble_ran"], r["dispatch_ran"],
             r["idle_ran"]))
# ═════════════════════════════════════════════════════════════════════
# TACKLE-PHASE FILTER (mirrors the engine's own sub-state vocabulary)
# ═════════════════════════════════════════════════════════════════════

_TACKLE_SUBSTATE_NAMES = frozenset({
    "TACKLE_INIT", "TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE",
    "TACKLE_RELEASE",
})


def is_tackle_phase(world):
    """True while the tackle sub-state machine owns the frame."""
    return name_of(getattr(world, "sub_state", None)) in _TACKLE_SUBSTATE_NAMES


# ═════════════════════════════════════════════════════════════════════
# THE REPORT
# ═════════════════════════════════════════════════════════════════════

def report(res):
    agg = aggregate(res)
    frame_rows = res["frame_rows"]

    banner("OPEN_PLAY STALL AUTHORITY — which writer owns the still bodies")
    print("  stalled predicate : frozen False AND speed < %.2f m/s AND "
          "(|target_x - x| > %.2f OR |target_y - y| > %.2f)"
          % (STALL_SPEED_MPS, TARGET_EPS_M, TARGET_EPS_M))
    print("  seed              : %d     frames stepped: %d"
          % (SEED, MAX_FRAMES))
    print("  OPEN_PLAY frames captured (non-tackle)     = %d"
          % agg["open_frames"])

    sub("A. WHICH ENTRY POINT RAN — measured from the spies, not assumed")
    print("  frames the geometry PREAMBLE ran            = %d" % agg["preamble_frames"])
    print("  frames the FULL defence dispatch ran        = %d" % agg["dispatch_frames"])
    print("  frames the idle branch ran                  = %d" % agg["idle_frames"])
    print("  frames dispatch ran WITHOUT the preamble    = %d"
          % agg["asymmetry_frames"])
    print("  frames with NO 'preamble' geometry publish  = %d"
          % agg["no_geom_publish_frames"])
    print("  frames apply_seed_hold returned holders     = %d"
          % agg["seed_hold_frames"])
    print("  frames the MMDL decided HOLD for any body   = %d"
          % agg["mmdl_hold_frames"])

    sub("B. THE STALLS — how many body-frames, and who owns the target")
    print("  stalled body-frames                         = %d"
          % agg["stall_total"])
    if agg["stall_by_attribution"]:
        for k, v in topn(agg["stall_by_attribution"], 20):
            print("    %-34s = %d" % (k, v))
    else:
        print("    (none)")

    sub("C. WHO THEY ARE — role / movement_state / target_source")
    for label, dct in (("role", agg["stall_by_role"]),
                       ("movement_state", agg["stall_by_state"]),
                       ("target_source", agg["stall_by_source"])):
        print("  -- by %s --" % label)
        for k, v in topn(dct, 15):
            print("    %-34s = %d" % (str(k), v))

    sub("D. WHY — which is_seed_stale clause fired on the stalled frames")
    if agg["stall_by_clauses"]:
        for k, v in topn(agg["stall_by_clauses"], 20):
            print("    %-60s = %d" % (k, v))
    else:
        print("    (no stalled body carried a stale-seed clause)")

    sub("E. PERSISTENCE — longest consecutive stalled run per player")
    print("  %-6s %-8s %-12s %-8s %-8s" %
          ("pid", "frames", "longest_run", "first", "last"))
    for pid, v in sorted(agg["runs"].items(),
                         key=lambda kv: -kv[1]["longest_run"]):
        print("  %-6s %-8s %-12s %-8s %-8s"
              % (pid, v["frames"], v["longest_run"], v["first"], v["last"]))
    print("")
    print("  players stalled for >= 10 consecutive frames = %d %s"
          % (len(agg["persistent"]), sorted(agg["persistent"])))

    sub("F. EXAMPLE STALLED FRAMES (full body ledger)")
    for frame, rows in res["examples"][:12]:
        print("")
        print("  === frame %d ===" % frame)
        fr = next((f for f in frame_rows if f["frame"] == frame), None)
        if fr is not None:
            print("      preamble=%d dispatch=%d idle=%d seed_hold=%d %s "
                  "mmdl_hold=%s publish=%s"
                  % (fr["preamble"], fr["dispatch"], fr["idle"],
                     fr["seed_hold_n"], fr["seed_hold_ids"],
                     fr["mmdl_hold_ids"], fr["publish_sources"]))
        for r in rows:
            print_body_row(r)

    sub("G. REPRESENTATIVE STALLS — one full row per (owner, movement_state)")
    groups = {}
    for r in res["stall_rows"]:
        key = (attribute(r), str(r["movement_state"]))
        groups.setdefault(key, []).append(r)
    for key in sorted(groups, key=lambda k: -len(groups[k])):
        rows = groups[key]
        print("")
        print("  --- %s / %s : %d row(s) ---" % (key[0], key[1], len(rows)))
        print_body_row(rows[0])

    banner("VERDICT")
    attrib = agg["stall_by_attribution"]
    if not attrib:
        print("  NO stalled body-frame was observed under this predicate.")
        print("  The stall is not reproducible under this boot/seed, or the")
        print("  bodies involved move less than %.2f m/s only while frozen."
              % STALL_SPEED_MPS)
        return agg

    top = topn(attrib, 1)[0]
    print("  dominant owner of the stalled body-frames = %s (%d frames)"
          % (top[0], top[1]))
    if top[0].startswith("2_SEED_HOLD"):
        print("  -> The STALE-SEED LOCKDOWN owns the stall.  The clauses")
        print("     below name the exact precondition that made the seeds")
        print("     stale on those frames.")
    elif top[0].startswith("3_MMDL_HOLD"):
        print("  -> The MMDL HOLD decision owns the stall, and its input is")
        print("     the same staleness predicate.")
    elif top[0].startswith("4_"):
        print("  -> A committed / pre-tackle owner writer holds the body.")
    elif top[0].startswith("5_"):
        print("  -> The idle branch skipped a frozen body.")
    elif top[0].startswith("6_"):
        print("  -> The preamble/dispatch gate asymmetry is in play.")
    else:
        print("  -> The owner is one of the band-winner / named-source rows;")
        print("     the target_source histogram above names it verbatim.")

    print("")
    print("  most common staleness clause over the stalls = %s"
          % (topn(agg["stall_by_clauses"], 1)[0]
             if agg["stall_by_clauses"] else "<none>"))
    print("")
    print("  Report written to %s" % REPORT_PATH)
    return agg


# ═════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════

def main():
    global _REPORT
    _REPORT = open(REPORT_PATH, "w", encoding="utf-8")

    silence()
    res = run_rollout()
    speak()

    report(res)
    close_report()
    return 0


if __name__ == "__main__":
    sys.exit(main())
