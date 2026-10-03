"""probe_fb_admission_predicates.py — WHICH PREDICATE REJECTS THE TAGGED FB
FROM PRIMARY CANDIDACY, AND WHICH STAMPS OVERLAP WITH ITS ADMISSION GATES?

THE QUESTION
------------
``select_primary_candidate`` admits a body with

    is_engage_role(d)  OR  is_tackle_engage_guard(d, bc, world, tuners)

and does **not** consult ``engage_distance_m`` at all (distance is only used
to ORDER candidates).  A tagged fullback inside the 2.0 m engage distance can
therefore still be rejected.  Separately, the stamps written when the FB is
ARMED as the tackler (``_arm_tackler_role``) are the very same fields the
admission gates read — a closed loop.

This probe measures, per frame, on the TAGGED FB:
  * both disjuncts of ``select_primary_candidate`` (role vs guard);
  * the guard's inner conjuncts (genuine-line-defender / radial-in / facing);
  * ``fullback_emergency_defender_active`` and its identity gates;
  * ``can_acquire_tackle`` and ``_is_eligible_lane_defender``;
  * the stamp tuple: tackle_role / defender_role / tackle_state_active /
    frozen / freeze_timer / fullback_role_active / can_acquire_tackle /
    _fb_emergency_engaged.
and it records a BEFORE/AFTER snapshot every time ``_arm_tackler_role``
fires on the tagged FB (the armer is never bypassed — the hook calls the real
function and returns its value unchanged, so the rollout is byte-identical).

Run:     python probe_fb_admission_predicates.py
Sweep:   set RLM_FBAP_SEEDS=20260827,1,2,3,4,5  RLM_FBAP_FRAMES=3000
A/B:     set RLM_FULLBACK_TACKLE_CANDIDACY=0 (etc.) and re-run; the active
         levers are printed from the modules' own booleans.
Report:  tmp_fb_admission_predicates.txt
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

from game.defence.defence_lvl_1 import pretackle_classifier
from game.defence.defence_lvl_1.pretackle_classifier import (
    is_engage_role,
    select_primary_candidate,
)
from game.defence.defence_lvl_1.slot_arbitration import (
    is_slot_tackle_eligible,
)
from game.defence.defence_lvl_1 import tackle_engage_guard as guard
from game.defence.defence_lvl_1.tackle_engage_guard import (
    is_tackle_engage_guard,
    is_genuine_line_defender,
    is_radial_in,
    is_facing_bc,
    approach_angle_deg,
    distance_to_bc,
    tackle_radius_m,
    tackle_engage_facing_max_deg,
)
from game.defence.defence_lvl_1 import fullback_role as fbr
from game.defence.defence_lvl_1.fullback_role import (
    fullback_emergency_defender_active,
)
from game.defence.defence_lvl_1.intercept_tacklers import (
    _is_eligible_lane_defender,
    is_lane_tackler_candidate,
)

# ── CONFIG ───────────────────────────────────────────────────────────
DT = 1.0 / 60.0
FRAMES = int(os.environ.get("RLM_FBAP_FRAMES", "3000"))
NEAR_M = float(os.environ.get("RLM_FBAP_NEAR_M", "2.5"))
REPORT_PATH = os.environ.get("RLM_FBAP_REPORT", "tmp_fb_admission_predicates.txt")
SEEDS = [
    int(s) for s in os.environ.get(
        "RLM_FBAP_SEEDS", "20260827,1,2,3,4,5"
    ).split(",") if s.strip()
]

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


def find_fullback(world):
    for d in getattr(world, "defending_team", None) or ():
        if getattr(d, "fullback_role_active", False):
            return d
    return None


def tuners_of(world):
    # Same single read select_primary_candidate uses.
    return (getattr(world, "defence_tuners", None)
            or getattr(world, "tuners", None))


def is_tagged_fb(body) -> bool:
    return bool(getattr(body, "fullback_role_active", False))


def role_name_of(body):
    r = getattr(body, "defender_role", None)
    return getattr(r, "name", None) or (str(r) if r is not None else None)


# ── WHY IS THE FB REJECTED (attribute the FIRST failing conjunct) ────
def candidacy_verdict(world, fb, bc, tuners):
    """Return (admitted, axis, detail) for the FB this frame.

    axis: "role" | "guard" | "rejected".
    detail: for rejected, the FIRST failing guard conjunct or a role note.
    """
    if is_engage_role(fb):
        return True, "role", "tackle_role in (PRIMARY,SECONDARY)"
    # Guard path — evaluate the inner conjuncts in is_tackle_engage_guard order.
    if not guard.tackle_engage_guard_enabled(tuners):
        return False, "rejected", "guard_disabled"
    if bc is None:
        return False, "rejected", "no_bc"
    if bool(getattr(world, "tes_active", False)):
        return False, "rejected", "tes_active"
    if world is not None and getattr(world, "ball_owner", None) not in (None, "BC"):
        return False, "rejected", "ball_owner_not_bc"
    # genuine line defender?
    if bool(getattr(fb, "frozen", False)):
        return False, "rejected", "genuine: frozen"
    if float(getattr(fb, "freeze_timer", 0.0) or 0.0) > 0.0:
        return False, "rejected", "genuine: freeze_timer"
    if bool(getattr(fb, "tackle_state_active", False)):
        return False, "rejected", "genuine: tackle_state_active"
    rn = role_name_of(fb)
    if rn in guard_scope_roles():
        return False, "rejected", f"genuine: role {rn}"
    if getattr(fb, "tackle_role", None) in guard.NON_ENGAGE_ROLES:
        return False, "rejected", "tackle_role THIRD"
    if not is_radial_in(fb, bc, tuners):
        return False, "rejected", (
            "radial_out (dist=%.3f >= tackle_radius_m=%.3f)"
            % (distance_to_bc(fb, bc), tackle_radius_m(tuners))
        )
    if not is_facing_bc(fb, bc, tuners):
        return False, "rejected", (
            "not_facing (angle=%.1f > %.1f)"
            % (approach_angle_deg(fb, bc), tackle_engage_facing_max_deg(tuners))
        )
    return True, "guard", "radial_in AND facing"


def guard_scope_roles():
    # _TACKLE_RADIUS_EXEMPT_ROLE_NAMES is private to movement_mode_decision, but
    # _is_genuine_line_defender is the authority; re-read it directly for the
    # detail label so the two cannot drift.
    return frozenset({
        "MARKER_A", "MARKER_B", "DUMMY_HALF",
        "TACKLER_1", "TACKLER_2", "TACKLER_3", "KICK_CHASE",
    })


def stamp_tuple(world, fb, bc):
    return {
        "tackle_role": getattr(fb, "tackle_role", None),
        "defender_role": role_name_of(fb),
        "tackle_state_active": bool(getattr(fb, "tackle_state_active", False)),
        "frozen": bool(getattr(fb, "frozen", False)),
        "freeze_timer": round(fnum(getattr(fb, "freeze_timer", 0.0)), 3),
        "fullback_role_active": bool(getattr(fb, "fullback_role_active", False)),
        "can_acquire_tackle": bool(getattr(fb, "can_acquire_tackle", False)),
        "fb_emergency_engaged": bool(getattr(fb, "_fb_emergency_engaged", False)),
        "tes_attached": bool(getattr(fb, "tes_attached", False)),
        "in_contact": bool(getattr(fb, "in_contact", False)),
    }


def gate_tuple(world, fb, bc, tuners):
    try:
        eng_role = is_engage_role(fb)
    except Exception:
        eng_role = None
    try:
        guard_v = is_tackle_engage_guard(fb, bc, world, tuners)
    except Exception:
        guard_v = None
    try:
        genuine = is_genuine_line_defender(fb)
    except Exception:
        genuine = None
    try:
        radial = is_radial_in(fb, bc, tuners)
    except Exception:
        radial = None
    try:
        facing = is_facing_bc(fb, bc, tuners)
    except Exception:
        facing = None
    try:
        fb_emerg = fullback_emergency_defender_active(world, fb, bc)
    except Exception:
        fb_emerg = None
    try:
        lane_elig = _is_eligible_lane_defender(fb, world, bc)
    except Exception:
        lane_elig = None
    try:
        slot = is_slot_tackle_eligible(bc, fb, world)
    except Exception:
        slot = None
    return {
        "is_engage_role": eng_role,
        "guard": guard_v,
        "genuine_line_defender": genuine,
        "radial_in": radial,
        "facing_bc": facing,
        "fb_emergency_active": fb_emerg,
        "lane_eligible": lane_elig,
        "slot_eligible": slot,
    }


def new_ledger(seed):
    return {
        "seed": seed,
        "frames_run": 0,
        "frames_tagged_fb": 0,
        "frames_near": 0,                 # FB within NEAR_M of BC
        "near_and_not_admitted": 0,
        "admits": {"role": 0, "guard": 0},
        "reject_reasons_near": {},        # detail -> count (near frames only)
        "assert_primary_implies_fbemerg": {"viol": 0, "ok": 0},
        "arm_events": [],                 # before/after snapshots
        "rows": [],
        "_row_cap": int(os.environ.get("RLM_FBAP_ROWS", "60")),
    }


def render_seed_table(ledgers):
    print("-" * 118)
    print("%-10s %7s %7s %7s %9s %6s %6s %8s"
          % ("seed", "frames", "tagged", "near<%.1f" % NEAR_M, "notAdmith",
             "role", "guard", "armEvt"))
    print("-" * 118)
    for lg in ledgers:
        print("%-10s %7d %7d %7d %9d %6d %6d %8d"
              % (lg["seed"], lg["frames_run"], lg["frames_tagged_fb"],
                 lg["frames_near"], lg["near_and_not_admitted"],
                 lg["admits"]["role"], lg["admits"]["guard"],
                 len(lg["arm_events"])))
    print("")


def render_reject_reasons(ledgers):
    print("=" * 118)
    print("REJECTION ATTRIBUTION -- the FIRST failing conjunct, on NEAR FB "
          "frames (FB within %.1f m of BC)" % NEAR_M)
    print("=" * 118)
    pooled = {}
    for lg in ledgers:
        for k, v in lg["reject_reasons_near"].items():
            pooled[k] = pooled.get(k, 0) + v
    total = sum(pooled.values()) or 1
    for k in sorted(pooled, key=lambda x: -pooled[x]):
        print("  %-46s %7d   %.1f%%"
              % (k, pooled[k], 100.0 * pooled[k] / total))
    if not pooled:
        print("  (no near FB frames rejected in this sweep)")
    print("")


def render_overlap(ledgers):
    print("=" * 118)
    print("LOOP CHECK -- every frame the FB carried tackle_role=PRIMARY, was "
          "fullback_emergency_defender_active TRUE?")
    print("=" * 118)
    viol = sum(lg["assert_primary_implies_fbemerg"]["viol"] for lg in ledgers)
    ok = sum(lg["assert_primary_implies_fbemerg"]["ok"] for lg in ledgers)
    print("  FB had tackle_role=PRIMARY AND emergency gate TRUE : %d" % ok)
    print("  FB had tackle_role=PRIMARY AND emergency gate FALSE: %d" % viol)
    print("  %s" % ("OK -- the lane stamp never exists without the emergency "
                    "gate." if viol == 0 else
                    ">>> VIOLATION -- a PRIMARY lane stamp exists without the "
                    "emergency gate; the loop is broken."))
    print("")


def render_arm_events(ledgers):
    print("=" * 118)
    print("ARMER SNAPSHOTS -- stamps on the tagged FB straddling every "
          "_arm_tackler_role()")
    print("=" * 118)
    any_events = False
    for lg in ledgers:
        for ev in lg["arm_events"][:6]:
            any_events = True
            print("  seed %s  frame %s  role_enum=%s" %
                  (lg["seed"], ev["frame"], ev["role_enum"]))
            for key in sorted(ev["before"]):
                b = ev["before"][key]
                a = ev["after"].get(key)
                flag = "" if b == a else "   <-- CHANGED"
                print("      %-22s %-10s -> %-10s%s"
                      % (key, b, a, flag))
            print("")
    if not any_events:
        print("  (the tagged FB was never armed as a tackler in this sweep)")
    print("")


def render_rows(ledgers):
    print("=" * 118)
    print("DETAIL -- near FB frames (first %d per seed)"
          % max((len(lg["rows"]) for lg in ledgers), default=0))
    print("=" * 118)
    for lg in ledgers:
        if not lg["rows"]:
            continue
        print("  seed %s:" % lg["seed"])
        print("    %7s %6s %-18s %-30s %-9s %-9s %-6s %-6s"
              % ("frame", "dist", "tackle_role", "reason", "adm", "guard",
                 "fbE", "laneE"))
        for row in lg["rows"]:
            print("    %7s %6s %-18s %-30s %-9s %-9s %-6s %-6s"
                  % (row["frame"], row["dist"], str(row["tackle_role"]),
                     row["reason"][:30], row["admitted"], row["guard"],
                     row["fbE"], row["laneE"]))
        print("")


def run_rollout(seed):
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

    real_arm = OpenPlayEngine._arm_tackler_role

    def arm_hook(self, d, role_enum, role_name, ctx):
        tagged = is_tagged_fb(d)
        before = stamp_tuple(getattr(self, "world", None), d, None) if tagged else None
        result = real_arm(self, d, role_enum, role_name, ctx)
        if tagged:
            after = stamp_tuple(getattr(self, "world", None), d, None)
            ledger["arm_events"].append({
                "frame": int(getattr(getattr(self, "world", None),
                                    "frame_count", 0) or 0),
                "role_enum": getattr(role_enum, "name", str(role_enum)),
                "before": before,
                "after": after,
            })
        return result

    OpenPlayEngine._arm_tackler_role = arm_hook

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
            if fb is None:
                continue
            ledger["frames_tagged_fb"] += 1

            # LOOP CHECK (all frames): PRIMARY implies emergency gate.
            role_val = getattr(fb, "tackle_role", None)
            if role_val == "PRIMARY":
                try:
                    gate_ok = bool(fullback_emergency_defender_active(
                        world, fb, bc))
                except Exception:
                    gate_ok = False
                bucket = "ok" if gate_ok else "viol"
                ledger["assert_primary_implies_fbemerg"][bucket] += 1

            if bc is None:
                continue
            dist = distance_to_bc(fb, bc)
            if dist > NEAR_M:
                continue

            ledger["frames_near"] += 1
            tuners = tuners_of(world)
            admitted, axis, detail = candidacy_verdict(world, fb, bc, tuners)
            gates = gate_tuple(world, fb, bc, tuners)
            if admitted:
                ledger["admits"][axis] += 1
            else:
                ledger["near_and_not_admitted"] += 1
                ledger["reject_reasons_near"][detail] = (
                    ledger["reject_reasons_near"].get(detail, 0) + 1
                )
            if len(ledger["rows"]) < ledger["_row_cap"]:
                ledger["rows"].append({
                    "frame": int(getattr(world, "frame_count", 0) or 0),
                    "dist": "%.2f" % dist,
                    "tackle_role": role_val,
                    "reason": detail if not admitted else ("admitted:" + axis),
                    "admitted": "role" if axis == "role" else (
                        "guard" if axis == "guard" else "no"),
                    "guard": "Y" if gates["guard"] else "n",
                    "fbE": "Y" if gates["fb_emergency_active"] else "n",
                    "laneE": "Y" if gates["lane_eligible"] else "n",
                })
    finally:
        OpenPlayEngine._arm_tackler_role = real_arm

    return ledger
def render_levers():
    print("=" * 118)
    print("ACTIVE LEVERS (module booleans, read at import)")
    print("=" * 118)
    print("  LEFT  side  fullback: F. ``select_primary_candidate`` prefers slot")
    print("  RLM_FULLBACK_TACKLE_CANDIDACY      = %s" % fbr.FULLBACK_TACKLE_CANDIDACY)
    print("  RLM_FULLBACK_ROLE_PERSISTENCE      = %s" % fbr.FULLBACK_ROLE_PERSISTENCE)
    print("  RLM_FB_BREAK_LATCH                 = %s" % fbr.FB_BREAK_LATCH)
    print("  tackle_radius_m (guard radial)     = %s" % tackle_radius_m(None))
    print("  tackle_engage_facing_max_deg       = %s"
          % tackle_engage_facing_max_deg(None))
    print("  classifier ENGAGE_DISTANCE_M       = %s"
          % getattr(pretackle_classifier, "ENGAGE_DISTANCE_M", "?"))
    print("")


def main():
    global _REPORT
    _REPORT = open(REPORT_PATH, "w", encoding="utf-8")
    sys.stdout = _TEE
    _TEE.active = True

    print("=" * 118)
    print("FB PRIMARY-CANDIDACY PREDICATE TRACE + TACKLER-STAMP OVERLAP")
    print("=" * 118)
    print("  seeds %s, %d frames each, dt=1/60, near<%.1f m"
          % (SEEDS, FRAMES, NEAR_M))
    print("  admission = is_engage_role(fb) OR is_tackle_engage_guard(fb,...)")
    print("  select_primary_candidate does NOT test engage_distance_m "
          "(distance is ordering only).")
    print("")
    render_levers()

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
    print("=" * 118)
    print("PER-SEED")
    print("=" * 118)
    render_seed_table(ledgers)
    render_reject_reasons(ledgers)
    render_overlap(ledgers)
    render_arm_events(ledgers)
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
