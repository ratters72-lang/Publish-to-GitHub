"""probe_fb_clean_engage_conversion.py — WHY DOES A CLEAN_ENGAGE ON THE TAGGED
FULLBACK NOT BECOME A TACKLE?

THE QUESTION
------------
The tagged fullback reaches valid contact geometry and the pre-tackle machine
classifies a TES-feeding state (``CLOSING_HEAD_ON`` / ``SQUARE_UP_ENGAGE`` /
``COMMIT_PLANTED``), i.e. the state whose OUTCOME is ``CLEAN_ENGAGE`` — and yet
no tackle forms.

THE TRACE THIS PROBE MEASURES
-----------------------------
``CLEAN_ENGAGE`` is a LABEL for a machine STATE (``pretackle_machine``), and it
is consumed by two DIFFERENT gates:

    run_machine(...)     -> returns the state        (NO distance test)
    should_engage(...)   -> the bool "feed TES"      (the FULL window, incl.
                                                       DISTANCE <= 2.0 m)
    apply_outcome(...)   -> the outcome label; for CLEAN_ENGAGE it is a NO-OP

``try_pre_tackle`` (defensive_behaviour_lvl1, the ONLY production caller) is:

    state = run_machine(...)
    if should_engage(..., state=state): return True     # -> enter_tes
    apply_outcome(..., state=state); return False        # <- CLEAN_ENGAGE label

So ``apply_outcome`` runs ONLY when ``should_engage`` returned FALSE.  A frame in
which ``apply_outcome`` reports ``CLEAN_ENGAGE`` is therefore, BY CONSTRUCTION,
a frame where the engage window did NOT open: the state was an engage state but
a ``should_engage`` conjunct closed.  "Received CLEAN_ENGAGE" and "no tackle"
are the SAME event, not a contradiction.

This probe removes the ambiguity by instrumenting the WHOLE chain on the tagged
fullback and recording, per ``should_engage`` call, EXACTLY which conjunct
closed — then counting how many of those closed frames were on an engage state
(silently labelled CLEAN_ENGAGE by apply_outcome) versus how many actually
entered TES.

WHAT IT MEASURES
----------------
  A  MACHINE    run_machine / should_engage / apply_outcome calls on the FB,
                the state classified, the verdict, and the printed outcome.
  B  CONJUNCTS  for every should_engage call ON THE FB, the FIRST conjunct that
                closed (machine_off / not_engage_state / slot / DISTANCE /
                angle / posture / square_up_not_planted / wrong_foot), or PASS.
  C  CONVERT    every ``_start_tackle`` and ``tes_start_tackle`` call, and
                whether the FB was the winning defender.  This is the ground
                truth "a tackle was created".
  D  MISLABEL   engage-state frames where should_engage was FALSE (so
                apply_outcome merely labelled CLEAN_ENGAGE) versus engage-state
                frames where should_engage was TRUE (so TES was entered).

METHOD
------
Read-only: every wrapper calls the real function unchanged and returns its
value, so the rollout is byte-identical to a normal run.  The engine is booted
exactly as ``probe_fb_facing_at_contact`` boots it.

The fixture is NOT reproducible across processes, so the probe sweeps seeds and
pools; no verdict rests on a single seed.

Run:    python probe_fb_clean_engage_conversion.py
Sweep:  $env:RLM_FBCE_SEEDS="20260827,1,2,3,4,5"; $env:RLM_FBCE_FRAMES="3000"
Report: tmp_fb_conversion_report.txt
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

from game.defence.defence_lvl_1 import pretackle_machine
from game.defence.defence_lvl_1.pretackle_machine import (
    ENGAGE_STATES,
    engage_distance_m,
    machine_enabled,
    is_real_planted_posture,
    is_closing_posture,
    wrong_foot_delta,
    wrong_foot_facing_delta,
    approach_angle_from_facing,
    max_approach_angle_deg,
    planted_velocity_eps,
)
from game.defence.defence_lvl_1.pretackle_classifier import (
    is_engage_role,
)
from game.defence.defence_lvl_1.slot_arbitration import (
    is_slot_tackle_eligible,
)
from game.defence.defence_lvl_1.tackle_engage_guard import (
    is_tackle_engage_guard,
)
from game.defence.defence_lvl_1.contest import winner_of
from game.tes import tes as tes_mod

# ── CONFIG ───────────────────────────────────────────────────────────
DT = 1.0 / 60.0
FRAMES = int(os.environ.get("RLM_FBCE_FRAMES", "3000"))
REPORT_PATH = os.environ.get("RLM_FBCE_REPORT", "tmp_fb_conversion_report.txt")
SEEDS = [
    int(s) for s in os.environ.get(
        "RLM_FBCE_SEEDS", "20260827,1,2,3,4,5"
    ).split(",") if s.strip()
]

TACKLE_RADIUS_M = 1.5
ENGAGE_DISTANCE_M_GLOBAL = 2.0   # pretackle_machine.ENGAGE_DISTANCE_M default

_REPORT = None
_REAL_STDOUT = sys.stdout


class _Tee:
    """Report sink with a gate; inactive writes are counted and discarded."""

    def __init__(self):
        self.active = False
        self.suppressed_bytes = 0

    def write(self, s):
        if not self.active:
            try:
                self.suppressed_bytes += len(s)
            except Exception:
                pass
            return len(s)
        try:
            if _REPORT is not None:
                _REPORT.write(s)
        except Exception:
            pass
        return _REAL_STDOUT.write(s)

    def flush(self):
        if not self.active:
            return None
        try:
            if _REPORT is not None:
                _REPORT.flush()
        except Exception:
            pass
        try:
            return _REAL_STDOUT.flush()
        except Exception:
            return None


_TEE = _Tee()
# ── SMALL READS ──────────────────────────────────────────────────────
def fnum(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def name_of(obj):
    if obj is None:
        return None
    return getattr(obj, "name", None) or str(obj)


def state_name(body):
    return name_of(getattr(body, "movement_state", None))


def find_fullback(world):
    """The single tagged fullback, or None."""
    for d in getattr(world, "defending_team", None) or ():
        if getattr(d, "fullback_role_active", False):
            return d
    return None


def is_tagged_fb(body) -> bool:
    return bool(getattr(body, "fullback_role_active", False))


def dist_between(a, b):
    if a is None or b is None:
        return None
    return math.hypot(
        fnum(getattr(a, "x", None)) - fnum(getattr(b, "x", None)),
        fnum(getattr(a, "y", None)) - fnum(getattr(b, "y", None)),
    )


def percent(part, whole):
    if not whole:
        return "--"
    return "%.1f%%" % (100.0 * part / whole)


def pool(ledgers, key):
    return sum(int(lg.get(key, 0) or 0) for lg in ledgers)


def pool_map(ledgers, key):
    out = {}
    for lg in ledgers:
        for k, v in (lg.get(key) or {}).items():
            out[k] = out.get(k, 0) + v
    return out


# ── THE CONJUNCT LABELER (read-only) ─────────────────────────────────
def conjunct_label(world, defender, bc, tuners, state) -> str:
    """Name the FIRST ``should_engage`` conjunct that is closed, or "PASS".

    Replicates ``pretackle_machine.should_engage``'s ordering with the SAME
    pure predicates.  It is a READ: it never advances the wrong-foot baseline,
    so it cannot perturb the frame.  The wrong-foot conjunct is not replicated
    (its read mutates ``bc.prev_facing``); frames that pass every earlier
    conjunct and still fail are reported as "PASS_or_wrongfoot" and counted
    against the real verdict.
    """
    PM = pretackle_machine
    if not machine_enabled(tuners):
        return "machine_off"
    if state is None or state not in ENGAGE_STATES:
        return "not_engage_state"
    try:
        guard = bool(is_tackle_engage_guard(defender, bc, world, tuners))
    except Exception:
        guard = False
    if not (is_slot_tackle_eligible(bc, defender, world) or guard):
        return "slot"
    dist = dist_between(defender, bc)
    if dist is None or dist > engage_distance_m(tuners):
        return "distance"
    angle = approach_angle_from_facing(
        defender, bc, world=world, tuners=tuners,
    )
    if angle >= max_approach_angle_deg(tuners):
        return "angle"
    planted = is_real_planted_posture(
        defender, eps=planted_velocity_eps(tuners),
    )
    closing = is_closing_posture(defender)
    if not (planted or closing):
        return "posture"
    if (state is PM.PretackleMachineState.SQUARE_UP_ENGAGE
            and not planted):
        return "square_up_not_planted"
    return "PASS_or_wrongfoot"


def new_ledger(seed):
    return {
        "seed": seed,
        "frames_run": 0,
        "frames_tagged_fb": 0,
        "frames_chase_armed": 0,
        "frames_in_engage_distance": 0,
        "fb_ids": set(),
        # A MACHINE (tagged FB only)
        "run_machine_calls": 0,
        "run_machine_states": {},
        "should_engage_calls": 0,
        "should_engage_true": 0,
        "apply_outcome_calls": 0,
        "apply_outcome_values": {},
        # B CONJUNCTS (tagged FB only)
        "conjuncts": {},
        # C CONVERT (ground truth)
        "start_tackle_calls": 0,
        "start_tackle_fb_calls": 0,
        "tes_start_calls": 0,
        "tes_start_fb_calls": 0,
        # D MISLABEL bookkeeping
        "engage_state_frames": 0,
        "engage_state_converted": 0,
        "engage_state_labelled_only": 0,
        # detail rows
        "rows": [],
        "_row_cap": int(os.environ.get("RLM_FBCE_ROWS", "80")),
    }


def record_row(ledger, frame, fb, bc, state, label, result, guard, slot):
    if len(ledger["rows"]) >= ledger["_row_cap"]:
        return
    ledger["rows"].append({
        "frame": frame,
        "state": name_of(state),
        "label": label,
        "result": bool(result),
        "dist": dist_between(fb, bc),
        "angle": (approach_angle_from_facing(fb, bc)
                  if bc is not None else None),
        "guard": bool(guard),
        "slot": bool(slot),
        "role": getattr(fb, "tackle_role", None),
        "can_tackle": bool(getattr(fb, "can_acquire_tackle", False)),
        "frozen": bool(getattr(fb, "frozen", False)),
    })
# ── THE ROLLOUT ──────────────────────────────────────────────────────
def run_rollout(seed):
    """Boot the engine, wrap the whole CLEAN_ENGAGE chain, step, aggregate."""
    ledger = new_ledger(seed)

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

    real_run_machine = pretackle_machine.run_machine
    real_should_engage = pretackle_machine.should_engage
    real_apply_outcome = pretackle_machine.apply_outcome
    real_start_tackle = OpenPlayEngine._start_tackle
    real_tes_start = getattr(ope_mod, "tes_start_tackle", None)

    def run_machine_hook(world_arg, defender, bc, tuners):
        state = real_run_machine(world_arg, defender, bc, tuners)
        try:
            if is_tagged_fb(defender):
                ledger["run_machine_calls"] += 1
                key = name_of(state)
                ledger["run_machine_states"][key] = (
                    ledger["run_machine_states"].get(key, 0) + 1
                )
        except Exception:
            pass
        return state

    def should_engage_hook(world_arg, defender, bc, tuners, state=None):
        # The label is computed BEFORE the real call and is a pure read, so it
        # cannot perturb the frame.  The real verdict is what the engine used.
        try:
            tagged = is_tagged_fb(defender)
        except Exception:
            tagged = False
        label = None
        if tagged:
            try:
                label = conjunct_label(
                    world_arg, defender, bc, tuners, state,
                )
            except Exception:
                label = "label_error"
        result = real_should_engage(
            world_arg, defender, bc, tuners, state=state,
        )
        if tagged:
            try:
                ledger["should_engage_calls"] += 1
                if result:
                    ledger["should_engage_true"] += 1
                ledger["conjuncts"][label] = (
                    ledger["conjuncts"].get(label, 0) + 1
                )
                is_engage_state = (
                    state is not None and state in ENGAGE_STATES
                    and label not in ("machine_off", "not_engage_state",
                                      "label_error")
                )
                if is_engage_state:
                    ledger["engage_state_frames"] += 1
                    if result:
                        ledger["engage_state_converted"] += 1
                    else:
                        ledger["engage_state_labelled_only"] += 1
                guard = False
                try:
                    guard = bool(is_tackle_engage_guard(
                        defender, bc, world_arg, tuners))
                except Exception:
                    pass
                slot = False
                try:
                    slot = bool(is_slot_tackle_eligible(
                        bc, defender, world_arg))
                except Exception:
                    pass
                record_row(
                    ledger, int(getattr(world_arg, "frame_count", 0) or 0),
                    defender, bc, state, label, result, guard, slot,
                )
            except Exception:
                pass
        return result

    def apply_outcome_hook(world_arg, defender, bc, tuners, state=None):
        outcome = real_apply_outcome(
            world_arg, defender, bc, tuners, state=state,
        )
        try:
            if is_tagged_fb(defender):
                ledger["apply_outcome_calls"] += 1
                key = name_of(outcome)
                ledger["apply_outcome_values"][key] = (
                    ledger["apply_outcome_values"].get(key, 0) + 1
                )
        except Exception:
            pass
        return outcome

    def start_tackle_hook(self, bc, d):
        try:
            ledger["start_tackle_calls"] += 1
            try:
                winner = winner_of(d)
            except Exception:
                winner = d
            if is_tagged_fb(winner):
                ledger["start_tackle_fb_calls"] += 1
        except Exception:
            pass
        return real_start_tackle(self, bc, d)

    def tes_start_hook(bc, d, world_arg):
        try:
            ledger["tes_start_calls"] += 1
            if is_tagged_fb(d):
                ledger["tes_start_fb_calls"] += 1
        except Exception:
            pass
        return real_tes_start(bc, d, world_arg)

    pretackle_machine.run_machine = run_machine_hook
    pretackle_machine.should_engage = should_engage_hook
    pretackle_machine.apply_outcome = apply_outcome_hook
    OpenPlayEngine._start_tackle = start_tackle_hook
    if real_tes_start is not None:
        ope_mod.tes_start_tackle = tes_start_hook

    try:
        for frame in range(1, FRAMES + 1):
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
                    players = getattr(world, "players", None) or []
                    if players:
                        collision_system.resolve_all(players, world=world)

            ledger["frames_run"] += 1

            fb = find_fullback(world)
            bc = getattr(world, "ball_carrier", None)
            if fb is not None:
                ledger["frames_tagged_fb"] += 1
                ledger["fb_ids"].add(
                    int(getattr(fb, "player_id", -1) or -1)
                )
            if fb is None or bc is None or world.tes_active:
                continue
            if _fb_chase_armed(fb):
                ledger["frames_chase_armed"] += 1
            dist = dist_between(fb, bc)
            if dist is not None and dist <= ENGAGE_DISTANCE_M_GLOBAL:
                ledger["frames_in_engage_distance"] += 1
    finally:
        pretackle_machine.run_machine = real_run_machine
        pretackle_machine.should_engage = real_should_engage
        pretackle_machine.apply_outcome = real_apply_outcome
        OpenPlayEngine._start_tackle = real_start_tackle
        if real_tes_start is not None:
            ope_mod.tes_start_tackle = real_tes_start

    return ledger


def _fb_chase_armed(body) -> bool:
    if body is None:
        return False
    if bool(getattr(body, "_fb_break_latched", False)):
        return True
    return state_name(body) == "CHASE"
# ── REPORT ───────────────────────────────────────────────────────────
def fmt(value, places=2):
    if value is None:
        return "--"
    if isinstance(value, float):
        return ("%%.%df" % places) % value
    return str(value)


def render_seed_table(ledgers):
    print("-" * 122)
    print(
        "%-10s %7s %7s %7s %7s %7s %7s %7s %7s %7s %7s"
        % ("seed", "frames", "tagged", "chase", "in2.0", "rmach", "seng",
           "sengT", "outc", "startTk", "tesFB")
    )
    print("-" * 122)
    for lg in ledgers:
        print(
            "%-10s %7d %7d %7d %7d %7d %7d %7d %7d %7d %7d"
            % (
                lg["seed"], lg["frames_run"], lg["frames_tagged_fb"],
                lg["frames_chase_armed"], lg["frames_in_engage_distance"],
                lg["run_machine_calls"], lg["should_engage_calls"],
                lg["should_engage_true"], lg["apply_outcome_calls"],
                lg["start_tackle_calls"], lg["tes_start_fb_calls"],
            )
        )
    print("")
    print("  in2.0   = frames the tagged FB was within the 2.0 m engage "
          "distance of the carrier")
    print("  rmach   = pretackle_machine.run_machine calls ON THE TAGGED FB")
    print("  seng    = should_engage calls ON THE TAGGED FB   (sengT = True)")
    print("  outc    = apply_outcome calls ON THE TAGGED FB")
    print("  startTk = OpenPlayEngine._start_tackle calls (all defenders)")
    print("  tesFB   = tes_start_tackle calls with the TAGGED FB as defender")
    print("")


def render_machine(ledgers):
    print("=" * 122)
    print("A  MACHINE -- run_machine states / should_engage verdicts / "
          "applied outcomes (tagged FB only)")
    print("=" * 122)
    states = pool_map(ledgers, "run_machine_states")
    print("  run_machine states  : %s"
          % (", ".join("%s=%d" % (k, v)
                       for k, v in sorted(states.items())) or "(none)"))
    print("  should_engage calls : %d   True %d   False %d"
          % (pool(ledgers, "should_engage_calls"),
             pool(ledgers, "should_engage_true"),
             pool(ledgers, "should_engage_calls")
             - pool(ledgers, "should_engage_true")))
    outcomes = pool_map(ledgers, "apply_outcome_values")
    print("  apply_outcome vals  : %s"
          % (", ".join("%s=%d" % (k, v)
                       for k, v in sorted(outcomes.items())) or "(none)"))
    print("")


def render_conjuncts(ledgers):
    print("=" * 122)
    print("B  CONJUNCTS -- the FIRST should_engage conjunct that CLOSED, per "
          "call on the tagged FB")
    print("=" * 122)
    labels = pool_map(ledgers, "conjuncts")
    total = sum(labels.values()) or 1
    order = [
        "machine_off", "not_engage_state", "slot", "distance", "angle",
        "posture", "square_up_not_planted", "PASS_or_wrongfoot",
        "label_error",
    ]
    seen = set()
    for key in order:
        if key in labels:
            seen.add(key)
            print("  %-22s %7d   %s"
                  % (key, labels[key], percent(labels[key], total)))
    for key in sorted(labels):
        if key not in seen:
            print("  %-22s %7d   %s"
                  % (key, labels[key], percent(labels[key], total)))
    print("")
    print("  A label other than PASS_or_wrongfoot means the engage window was "
          "CLOSED at that conjunct:")
    print("  the frame is a NON-conversion, and when its state is an engage "
          "state the machine")
    print("  will still *label* it CLEAN_ENGAGE via apply_outcome.")
    print("")


def render_conversion(ledgers):
    print("=" * 122)
    print("C  CONVERSION -- did a tackle actually get created?")
    print("=" * 122)
    print("  _start_tackle calls (all defenders)          : %d"
          % pool(ledgers, "start_tackle_calls"))
    print("  _start_tackle calls naming the tagged FB     : %d"
          % pool(ledgers, "start_tackle_fb_calls"))
    print("  tes_start_tackle calls                       : %d"
          % pool(ledgers, "tes_start_calls"))
    print("  tes_start_tackle calls with the tagged FB    : %d"
          % pool(ledgers, "tes_start_fb_calls"))
    print("")


def render_mislabel(ledgers):
    print("=" * 122)
    print("D  THE MISLABEL -- engage-state frames: converted vs labelled-only")
    print("=" * 122)
    frames = pool(ledgers, "engage_state_frames")
    conv = pool(ledgers, "engage_state_converted")
    only = pool(ledgers, "engage_state_labelled_only")
    print("  should_engage calls whose state is an ENGAGE state : %d" % frames)
    print("      ... returned True  -> TES entered               : %d   %s"
          % (conv, percent(conv, frames)))
    print("      ... returned False -> apply_outcome labels it   : %d   %s"
          % (only, percent(only, frames)))
    print("")


def render_verdict(ledgers):
    print("=" * 122)
    print("VERDICT")
    print("=" * 122)
    frames = pool(ledgers, "engage_state_frames")
    conv = pool(ledgers, "engage_state_converted")
    only = pool(ledgers, "engage_state_labelled_only")
    tes_fb = pool(ledgers, "tes_start_fb_calls")
    labels = pool_map(ledgers, "conjuncts")
    dominant = None
    for k, v in sorted(labels.items(), key=lambda kv: -kv[1]):
        if k not in ("PASS_or_wrongfoot", "not_engage_state", "machine_off",
                     "label_error"):
            dominant = (k, v)
            break
    if frames == 0:
        print("  The tagged FB never held an engage state in this sweep.  The")
        print("  CLEAN_ENGAGE -> tackle question has no population here; widen")
        print("  the sweep (RLM_FBCE_SEEDS / RLM_FBCE_FRAMES) before concluding.")
        return
    print("  Engage-state calls on the tagged FB : %d" % frames)
    print("       converted (TES entered)        : %d   %s"
          % (conv, percent(conv, frames)))
    print("       labelled-only (window shut)    : %d   %s"
          % (only, percent(only, frames)))
    print("  Tackles actually created by the FB  : %d  (tes_start FB)"
          % tes_fb)
    if dominant is not None:
        print("  Dominant CLOSING conjunct           : %s (%d)"
              % (dominant[0], dominant[1]))
    print("")
    if conv > 0 and tes_fb == 0:
        print("  >>> CLEAN_ENGAGE IS CONVERTING (TES entered) but the tackle is")
        print("      not landing on the FB: the loss is at/after tes_start_tackle,")
        print("      not in the engage gate.  Trace the TES entry next.")
    elif conv == 0 and only > 0:
        print("  >>> NO CLEAN_ENGAGE CONVERTS.  Every engage state on the FB had")
        print("      its window CLOSED by a should_engage conjunct (dominant: %s),"
              % (dominant[0] if dominant else "n/a"))
        print("      and apply_outcome then LABELLED it CLEAN_ENGAGE and did")
        print("      nothing.  The blocker is the should_engage conjunct above,")
        print("      NOT the handover to TES.")
    elif conv > 0 and tes_fb > 0:
        print("  >>> The FB DOES convert some engages into tackles.  Any")
        print("      'never converts' claim is rate-limited, not absolute; see")
        print("      the conversion percentage above.")
    print("")


def render_rows(ledgers):
    print("=" * 122)
    print("DETAIL -- the first should_engage calls on the tagged FB, per seed")
    print("=" * 122)
    for lg in ledgers:
        if not lg["rows"]:
            continue
        print("  seed %s:" % lg["seed"])
        print("    %7s %-18s %-22s %6s %7s %6s %5s %5s %-9s"
              % ("frame", "state", "label", "verdict", "dist", "angle",
                 "guard", "slot", "role"))
        for row in lg["rows"][:40]:
            print("    %7s %-18s %-22s %6s %7s %6s %5s %5s %-9s"
                  % (
                      row["frame"], row["state"] or "-", row["label"],
                      "True" if row["result"] else "False",
                      fmt(row["dist"], 2), fmt(row["angle"], 1),
                      "Y" if row["guard"] else "n", "Y" if row["slot"] else "n",
                      row["role"] or "-",
                  ))
        print("")


def main():
    global _REPORT
    _REPORT = open(REPORT_PATH, "w", encoding="utf-8")
    sys.stdout = _TEE
    _TEE.active = True

    print("=" * 122)
    print("FB CLEAN_ENGAGE -> TACKLE CONVERSION -- every predicate between the "
          "engage verdict and the tackle")
    print("=" * 122)
    print("  seeds %s, %d frames each, dt=1/60" % (SEEDS, FRAMES))
    print("  tackle radius %.1f m | engage distance %.1f m"
          % (TACKLE_RADIUS_M, ENGAGE_DISTANCE_M_GLOBAL))
    print("")

    _TEE.active = False
    ledgers = []
    for seed in SEEDS:
        ledgers.append(run_rollout(seed))

    _TEE.active = True
    _REAL_STDOUT.write(
        "rollouts complete; engine chatter suppressed: %d bytes\n"
        % _TEE.suppressed_bytes
    )

    print("")
    print("=" * 122)
    print("PER-SEED")
    print("=" * 122)
    render_seed_table(ledgers)
    render_machine(ledgers)
    render_conjuncts(ledgers)
    render_conversion(ledgers)
    render_mislabel(ledgers)
    render_verdict(ledgers)
    render_rows(ledgers)

    try:
        sys.stdout = _REAL_STDOUT
    except Exception:
        pass
    try:
        _REPORT.close()
    except Exception:
        pass
    print("  report written to %s" % REPORT_PATH)


if __name__ == "__main__":
    main()
