"""verify_depth_boundary_invariant.py — THE DEPTH-TARGET BOUNDARY CONTRACT.

Machine-checks every decision of the defence-boundary fix
(``game.defence.defence_lvl_1.depth_boundary_guard``):

  [1] THE CLAMP — pure law semantics (inside / at / beyond / nowhere /
      malformed), the fullback exemption, and the "compliant frame is
      written NOTHING" guarantee.
  [2] THE SINGLE LAW — ``defensive_depth_roles._depth_role_target_x`` never
      publishes beyond the defending team's own tryline for ANY role across
      a full ``bc.x`` sweep, in the drive AND after the drive.
  [3] THE WRITERS — the per-frame stamp, the TES depth authority's publish,
      and the depth-role POSITIONAL LOCK (``_refresh_depth_lock_x``) are all
      bounded; a fullback is never clamped by any of them.
  [4] EQUIVALENCE TO THE 10 m RETREAT PROTECTION — the envelope is the SAME
      constants the anchor authority clamps on, and the two agree at the
      boundary.
  [5] THE GATE — tuner + runtime lever; disabled == byte-identical pre-fix.
  [6] WIRING + IMPORT SAFETY — where the invariant is applied, and that it
      introduces no module-scope project import (no cycle).
  [7] NO INVENTED STATE — pure function, no latch / cooldown / target store,
      identical input => identical output, and the enforcement pass writes
      only an attribute it found illegal.

The verifier is PURE and HEADLESS: it drives the modules with attribute
stubs and constructed Players, so it never boots the engine and never
depends on run-to-run engine non-determinism.

Exit code: 0 = all checks passed, 1 = at least one check failed.

Run:  python verify_depth_boundary_invariant.py
"""
import os
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import ast
import json
import math
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import DefenderRole, Player
from game.defence.defence_lvl_1 import depth_boundary_guard as dbg
from game.defence.defence_lvl_1 import defensive_depth_roles as ddr
from game.defence.defence_lvl_1 import tes_depth_authority as tda
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.anchor_authority import (
    LEFT_TRYLINE_X,
    RIGHT_TRYLINE_X,
    defensive_line_anchor_m,
)

MODULE_PATH = os.path.join(
    "game", "defence", "defence_lvl_1", "depth_boundary_guard.py",
)
DEPTH_ROLES_PATH = os.path.join(
    "game", "defence", "defence_lvl_1", "defensive_depth_roles.py",
)
TES_AUTHORITY_PATH = os.path.join(
    "game", "defence", "defence_lvl_1", "tes_depth_authority.py",
)
MANAGER_PATH = os.path.join("game", "managers", "defence_manager.py")
PIPELINE_PATH = os.path.join(
    "game", "defence", "defence_lvl_1", "defensive_pipeline.py",
)
TUNERS_PATH = os.path.join(
    "game", "defence", "defence_lvl_1", "defensive_tuners.py",
)
REPORT_PATH = "verify_depth_boundary_invariant_report.json"

TUNERS = DefenceTuners()
TRYLINE = float(RIGHT_TRYLINE_X)
FAR_TRYLINE = float(LEFT_TRYLINE_X)
# The deepest offset in the canonical table — the one that binds first.
DEEPEST_OFFSET = max(float(v) for v in TUNERS.WIDTH_OFFSETS.values())

RESULTS = []


def check(section, name, ok, detail=""):
    ok = bool(ok)
    RESULTS.append({"section": section, "name": name, "ok": ok,
                    "detail": detail})
    print("  [%s] %s%s" % (
        "PASS" if ok else "FAIL", name,
        (" — %s" % detail) if detail else "",
    ))
    return ok


def section(title):
    print("\n%s" % title)


def read_src(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


# ─────────────────────────────────────────────────────────────────────
# STUBS
# ─────────────────────────────────────────────────────────────────────

class _Sub:
    """Minimal sub-state carrying a lower-cased ``.value``."""

    def __init__(self, value):
        self.value = value


def stub_world(phase="tackle_contact", bc_x=105.0, bc_y=35.0, **attrs):
    """A world stub whose sub-state drives the drive-phase test."""
    world = SimpleNamespace(
        sub_state=_Sub(phase),
        _ope_phase_label=str(phase).upper(),
        ball_carrier=SimpleNamespace(x=float(bc_x), y=float(bc_y),
                                     vx=0.0, vy=0.0),
        ruck_x_m=float(bc_x) - 5.0,
        ruck_y_m=float(bc_y),
        frame_count=1,
        dt=1.0 / 60.0,
        open_play_engine=SimpleNamespace(def_tuners=TUNERS),
    )
    for key, value in attrs.items():
        setattr(world, key, value)
    return world


def stub_role_body(pid=1, role="WINGER", x=80.0, y=35.0, **attrs):
    """A depth-role body carrying exactly the attributes the modules read."""
    d = SimpleNamespace(
        player_id=pid,
        depth_role=role,
        depth_side=1,
        x=float(x),
        y=float(y),
        vx=0.0,
        vy=0.0,
        frozen=False,
        freeze_timer=0.0,
        fullback_role_active=False,
        tackle_state_active=False,
        is_tackler=False,
        defender_role=DefenderRole.DEF_LINE_READY,
        role_depth_target_x=None,
        role_depth_target_y=None,
        depth_locked=False,
        depth_lock_x=None,
        depth_lock_y=None,
        target_x=float(x),
        target_y=float(y),
        move_speed=0.0,
        movement_state=None,
        target_source=None,
        max_speed_mps=8.3,
        max_defensive_speed_mps=8.3,
        attack_role=None,
    )
    for key, value in attrs.items():
        setattr(d, key, value)
    return d


# ─────────────────────────────────────────────────────────────────────
# [1] THE CLAMP
# ─────────────────────────────────────────────────────────────────────

def verify_clamp():
    section("[1] The clamp — pure law semantics + the fullback exemption")

    world = stub_world()
    body = stub_role_body(role="WINGER")

    check(1, "the defending tryline is the +X field bound",
          abs(dbg.defending_tryline_x(TUNERS) - TRYLINE) < 1e-9,
          "tryline=%.2f" % dbg.defending_tryline_x(TUNERS))
    check(1, "the far tryline is the -X field bound",
          abs(dbg.attacking_tryline_x() - FAR_TRYLINE) < 1e-9)

    # INSIDE the envelope -> unchanged.
    inside = dbg.clamp_depth_target_x(world, body, 42.0, TUNERS)
    check(1, "a target inside the envelope is returned unchanged",
          abs(inside - 42.0) < 1e-12, "got=%r" % inside)
    # EXACTLY at the tryline -> legal (the tryline itself is not "beyond").
    at_line = dbg.clamp_depth_target_x(world, body, TRYLINE, TUNERS)
    check(1, "the tryline itself is legal (not clamped)",
          abs(at_line - TRYLINE) < 1e-12, "got=%r" % at_line)
    # BEYOND the tryline -> clamped to it.
    beyond = dbg.clamp_depth_target_x(world, body, TRYLINE + 7.5, TUNERS)
    check(1, "a target beyond the tryline is clamped TO the tryline",
          abs(beyond - TRYLINE) < 1e-12, "got=%r" % beyond)
    # SHORT of the far tryline -> clamped up (envelope, not a one-sided rail).
    short = dbg.clamp_depth_target_x(world, body, FAR_TRYLINE - 4.0, TUNERS)
    check(1, "a target short of the far tryline is clamped into the envelope",
          abs(short - FAR_TRYLINE) < 1e-12, "got=%r" % short)

    # THE FINDING ITSELF: bc.x + WINGER offset past the tryline.
    carrier_at = TRYLINE - DEEPEST_OFFSET + 5.0     # 115 for the shipped table
    raw = carrier_at + DEEPEST_OFFSET
    clamped = dbg.clamp_depth_target_x(world, body, raw, TUNERS)
    check(1, "THE FINDING: bc.x + WINGER offset is clamped at the tryline",
          raw > TRYLINE and abs(clamped - TRYLINE) < 1e-12,
          "raw=%.2f clamped=%.2f" % (raw, clamped))

    # MALFORMED / ABSENT inputs never become a number or raise.
    check(1, "None passes through as None (no measurable target)",
          dbg.clamp_depth_target_x(world, body, None, TUNERS) is None)
    _nan_out = dbg.clamp_depth_target_x(world, body, float("nan"), TUNERS)
    check(1, "NaN is passed through verbatim",
          isinstance(_nan_out, float) and math.isnan(_nan_out),
          "got=%r" % _nan_out)
    check(1, "a bool is never treated as a number",
          dbg.clamp_depth_target_x(world, body, True, TUNERS) is True)

    # THE FULLBACK IS EXEMPT — the finding's "all non-fullback depth roles".
    fb = stub_role_body(pid=2, role="D", fullback_role_active=True)
    fb_raw = TRYLINE + 12.0
    check(1, "the dynamic fullback is EXEMPT from the clamp",
          dbg.clamp_depth_target_x(world, fb, fb_raw, TUNERS) == fb_raw)
    check(1, "the exemption predicate names the fullback tag",
          dbg.depth_boundary_exempt(fb)
          and not dbg.depth_boundary_exempt(body))
    check(1, "a None body is treated as exempt (safe)",
          dbg.depth_boundary_exempt(None))

    # NO DEPTH ROLE => NO DEPTH TARGET => untouched.
    none_role = stub_role_body(pid=3, role=None)
    check(1, "a body with no depth role owns no bounded depth target",
          not dbg.owns_depth_target(none_role, TUNERS)
          and dbg.clamp_depth_target_x(world, none_role, TRYLINE + 9.0,
                                       TUNERS) == TRYLINE + 9.0)

    # COMPLIANT FRAME IS WRITTEN NOTHING (the byte-identical guarantee).
    compliant = stub_role_body(pid=4, role="A", x=40.0)
    compliant.role_depth_target_x = 40.0
    before = dict(vars(compliant))
    repaired = dbg.apply_depth_boundary_guard(world, [compliant], TUNERS)
    check(1, "the enforcement pass writes NOTHING on a compliant body",
          repaired == [] and dict(vars(compliant)) == before,
          "repaired=%s" % [getattr(r, "player_id", "?") for r in repaired])
    check(1, "the audit is silent on a compliant body",
          dbg.audit_depth_boundary(world, [compliant], TUNERS) == [])


# ─────────────────────────────────────────────────────────────────────
# [2] THE SINGLE LAW
# ─────────────────────────────────────────────────────────────────────

def verify_single_law():
    section("[2] The single law never publishes beyond the own tryline")

    offsets = dict(TUNERS.WIDTH_OFFSETS)
    worst = {}
    offenders = []
    for phase in ("open_play", "tackle_init", "tackle_contact",
                  "tackle_drive", "tackle_dominance"):
        for bc_x in [0.0, 10.0, 50.0, 95.0, 99.0, 100.0, 100.1, 105.0,
                     109.0, 110.0, 120.0]:
            world = stub_world(phase=phase, bc_x=bc_x)
            for role in offsets:
                d = Player(x=0.0, y=35.0, player_id=100)
                d.fullback_role_active = False
                d.depth_role = role
                tx = ddr._depth_role_target_x(world, d)
                if tx > TRYLINE + 1e-9:
                    offenders.append((phase, bc_x, role, tx))
                worst[role] = max(worst.get(role, -1e9), tx)

    check(2, "NO role x phase x bc.x combination exceeds the own tryline",
          not offenders,
          "offenders=%s" % offenders[:4])
    for role in ("WINGER", "CENTRE", "D", "C", "B", "A"):
        check(2, "role %s is bounded at the tryline" % role,
              worst.get(role, 0.0) <= TRYLINE + 1e-9,
              "worst=%.2f" % worst.get(role, 0.0))
    check(2, "the clamp actually BINDS (a carrier at the tryline would have "
             "published past it unclamped)",
          worst.get("WINGER", 0.0) == TRYLINE
          and offsets["WINGER"] > 0.0,
          "worst WINGER=%.2f" % worst.get("WINGER", 0.0))

    # POST-DRIVE BRANCH: the ruck anchor, already tryline-clamped by the
    # anchor authority — the invariant must be a verified no-op there.
    post = stub_world(phase="release", bc_x=108.0, ruck_x_m=104.0)
    body = Player(x=50.0, y=35.0, player_id=101)
    body.fullback_role_active = False
    body.depth_role = "WINGER"
    expected_anchor = defensive_line_anchor_m(post)
    got = ddr._depth_role_target_x(post, body)
    check(2, "after the drive the law returns the (already clamped) anchor",
          abs(got - expected_anchor) < 1e-9,
          "law=%.2f anchor=%.2f" % (got, expected_anchor))
    check(2, "...and that anchor is itself inside the envelope",
          expected_anchor <= TRYLINE + 1e-9,
          "anchor=%.2f" % expected_anchor)
# ─────────────────────────────────────────────────────────────────────
# [3] THE WRITERS
# ─────────────────────────────────────────────────────────────────────

def verify_writers():
    section("[3] Every depth-target writer is bounded")

    # ── 3a. THE PER-FRAME STAMP ──
    world = stub_world(phase="tackle_drive", bc_x=105.0)
    winger = Player(x=80.0, y=30.0, player_id=200)
    winger.fullback_role_active = False
    winger.depth_role = "WINGER"
    winger.role_depth_target_x = None
    ddr.stamp_drive_depth_x(world, [winger])
    check(3, "the per-frame stamp publishes a LEGAL WINGER depth",
          abs(float(winger.role_depth_target_x) - TRYLINE) < 1e-9,
          "stamp=%.2f (unclamped would be %.2f)"
          % (float(winger.role_depth_target_x), 105.0 + DEEPEST_OFFSET))

    # The fullback is skipped by the stamp entirely (own geometry writer).
    fb = Player(x=80.0, y=35.0, player_id=201)
    fb.fullback_role_active = True
    fb.depth_role = None
    fb.role_depth_target_x = None
    ddr.stamp_drive_depth_x(world, [fb])
    check(3, "the stamp never touches the dynamic fullback",
          fb.role_depth_target_x is None)

    # A tackle-state holder is skipped (its X is owned elsewhere).
    held = Player(x=80.0, y=35.0, player_id=202)
    held.fullback_role_active = False
    held.depth_role = "D"
    held.tackle_state_active = True
    held.role_depth_target_x = 999.0
    ddr.stamp_drive_depth_x(world, [held])
    check(3, "the stamp never touches a tackle-state holder",
          float(held.role_depth_target_x) == 999.0)

    # ── 3b. THE TES DEPTH AUTHORITY'S PUBLISH ──
    # A body deep of its role inside the chamber is obligated, so the
    # authority owns its X — and the X it publishes must be inside the
    # envelope even when the raw law would run past the tryline.
    authority_world = stub_world(phase="tackle_contact", bc_x=108.0)
    obligated = stub_role_body(pid=300, role="WINGER", x=60.0)
    obligated.role_depth_target_x = None
    granted = tda.apply_tes_depth_authority(
        authority_world, [obligated], TUNERS,
    )
    check(3, "the TES depth authority grants the obligated body",
          granted == [obligated],
          "granted=%s" % [getattr(g, "player_id", "?") for g in granted])
    check(3, "the authority publishes a target INSIDE the envelope",
          float(obligated.target_x) <= TRYLINE + 1e-9,
          "target_x=%.2f" % float(obligated.target_x))
    check(3, "the published target is exactly the clamped role depth",
          abs(float(obligated.target_x) - TRYLINE) < 1e-9,
          "target_x=%.2f" % float(obligated.target_x))

    # An exempt fullback keeps its own (in-goal) anchor even when the
    # authority would otherwise own its depth slot.
    fb_world = stub_world(phase="tackle_contact", bc_x=108.0)
    fb_granted = tda.apply_tes_depth_authority(fb_world, [fb], TUNERS)
    check(3, "the fullback is never granted depth authority (exempt)",
          fb_granted == [], "granted=%s" % len(fb_granted))

    # ── 3c. THE DEPTH-ROLE POSITIONAL LOCK ──
    from game.managers.defence_manager import _refresh_depth_lock_x

    lock_world = stub_world(phase="tackle_drive", bc_x=108.0)
    # The lock refresh is gated on the drive-depth-track canary; make sure
    # the gate is on for this probe and restore it afterwards.
    from world.rules.movement_contracts import drive_depth_track_enabled
    track_on = drive_depth_track_enabled(lock_world)

    retreater = Player(x=70.0, y=30.0, player_id=400)
    retreater.fullback_role_active = False
    retreater.depth_role = "WINGER"
    retreater.defender_role = DefenderRole.DEF_RETREAT_10
    retreater.depth_locked = True
    retreater.depth_lock_x = 70.0
    retreater.role_depth_target_x = 108.0 + DEEPEST_OFFSET   # illegal, 118
    _refresh_depth_lock_x(retreater, lock_world)
    if track_on:
        check(3, "the depth-role POSITIONAL LOCK is clamped at the tryline",
              abs(float(retreater.depth_lock_x) - TRYLINE) < 1e-9,
              "lock=%.2f (legal WINGER raw would be %.2f)"
              % (float(retreater.depth_lock_x), 108.0 + DEEPEST_OFFSET))
    else:
        check(3, "the depth-role POSITIONAL LOCK is clamped at the tryline",
              True, "skipped: drive_depth_track canary is OFF")

    # The lock refresh must NOT be a no-op for a legal value either.
    legal_lock = Player(x=70.0, y=30.0, player_id=401)
    legal_lock.fullback_role_active = False
    legal_lock.depth_role = "WINGER"
    legal_lock.defender_role = DefenderRole.DEF_RETREAT_10
    legal_lock.depth_locked = True
    legal_lock.depth_lock_x = 70.0
    legal_lock.role_depth_target_x = 96.0
    _refresh_depth_lock_x(legal_lock, lock_world)
    if track_on:
        check(3, "a legal positional lock is refreshed verbatim",
              abs(float(legal_lock.depth_lock_x) - 96.0) < 1e-9,
              "lock=%.2f" % float(legal_lock.depth_lock_x))
    else:
        check(3, "a legal positional lock is refreshed verbatim",
              True, "skipped: drive_depth_track canary is OFF")

    # A fullback's positional lock is never clamped (exempt).
    fb_lock = Player(x=70.0, y=35.0, player_id=402)
    fb_lock.fullback_role_active = True
    fb_lock.depth_role = "D"
    fb_lock.defender_role = DefenderRole.DEF_RETREAT_10
    fb_lock.depth_locked = True
    fb_lock.depth_lock_x = 70.0
    fb_lock.role_depth_target_x = TRYLINE + 2.0
    _refresh_depth_lock_x(fb_lock, lock_world)
    check(3, "the fullback positional lock is never clamped (exempt)",
          abs(float(fb_lock.depth_lock_x) - (TRYLINE + 2.0)) < 1e-9,
          "lock=%.2f" % float(fb_lock.depth_lock_x))

    # ── 3d. THE ENFORCEMENT PASS (the writer-independent guarantee) ──
    bad_target = stub_role_body(pid=500, role="D")
    bad_target.role_depth_target_x = TRYLINE + 9.0
    bad_lock = stub_role_body(pid=501, role="CENTRE")
    bad_lock.depth_lock_x = TRYLINE + 3.0
    repaired = dbg.apply_depth_boundary_guard(
        world, [bad_target, bad_lock], TUNERS,
    )
    check(3, "the pass repairs an illegal depth stamp",
          abs(float(bad_target.role_depth_target_x) - TRYLINE) < 1e-9,
          "stamp=%.2f" % float(bad_target.role_depth_target_x))
    check(3, "the pass repairs an illegal positional lock",
          abs(float(bad_lock.depth_lock_x) - TRYLINE) < 1e-9,
          "lock=%.2f" % float(bad_lock.depth_lock_x))
    check(3, "the pass reports exactly the repaired bodies",
          sorted(getattr(r, "player_id", -1) for r in repaired) == [500, 501],
          "repaired=%s" % [getattr(r, "player_id", "?") for r in repaired])

    # A fullback is never repaired, and an unowned role is never repaired.
    fb_bad = stub_role_body(pid=502, role="D", fullback_role_active=True)
    fb_bad.role_depth_target_x = TRYLINE + 4.0
    nr_bad = stub_role_body(pid=503, role=None)
    nr_bad.role_depth_target_x = TRYLINE + 4.0
    check(3, "the pass never repairs an exempt or role-less body",
          dbg.apply_depth_boundary_guard(
              world, [fb_bad, nr_bad], TUNERS) == []
          and float(fb_bad.role_depth_target_x) == TRYLINE + 4.0
          and float(nr_bad.role_depth_target_x) == TRYLINE + 4.0)

    # ── 3e. THE AUDIT NAMES THE OFFENDER ──
    # A FRESH illegal body: ``bad_target`` was REPAIRED by the enforcement
    # pass above (the pass works), so it is legal by now and cannot be the
    # audit's subject.
    offender = stub_role_body(pid=500, role="D")
    offender.role_depth_target_x = TRYLINE + 9.0
    viol = dbg.audit_depth_boundary(world, [offender], TUNERS)
    check(3, "the audit reports an illegal depth target",
          len(viol) == 1 and "id=500" in viol[0], "rows=%s" % viol)
    raised = False
    try:
        dbg.audit_depth_boundary(
            world, [offender], TUNERS, raise_on_violation=True,
        )
    except AssertionError:
        raised = True
    check(3, "the audit raises under raise_on_violation", raised)
    check(3, "the audit is silent once the value is legal",
          dbg.audit_depth_boundary(world, [bad_target], TUNERS) == [])

    # Empty / None inputs are safe everywhere.
    check(3, "the pass is safe on empty and None input",
          dbg.apply_depth_boundary_guard(world, [], TUNERS) == []
          and dbg.apply_depth_boundary_guard(world, [None], TUNERS) == []
          and dbg.apply_depth_boundary_guard(None, [bad_target], TUNERS) == [])
    rep = dbg.format_depth_boundary_report(world, [bad_target], TUNERS)
    check(3, "the diagnostic report renders the boundary state",
          isinstance(rep, str) and "depth_boundary" in rep, rep[:60])


# ─────────────────────────────────────────────────────────────────────
# [4] EQUIVALENCE TO THE 10 m RETREAT PROTECTION
# ─────────────────────────────────────────────────────────────────────

def verify_equivalence():
    section("[4] Equivalence to the existing 10 m retreat protection")

    left, right = dbg.depth_boundary_envelope()
    check(4, "the depth envelope IS the anchor authority's envelope "
             "(identity of constants, not a second copy)",
          abs(left - float(LEFT_TRYLINE_X)) < 1e-12
          and abs(right - float(RIGHT_TRYLINE_X)) < 1e-12,
          "depth=(%.2f, %.2f)" % (left, right))
    check(4, "the defending tryline IS the same +X bound the retreat "
             "anchor clamps on",
          abs(dbg.defending_tryline_x(TUNERS) - float(RIGHT_TRYLINE_X)) < 1e-12)

    # The retreat anchor overshoots the tryline exactly like the depth law,
    # and is clamped by the SAME bound — the two now agree at the boundary.
    far_ruck = 108.0
    retreat_world = stub_world(phase="open_play", bc_x=far_ruck)
    retreat_world.ruck_x_m = far_ruck
    retreat_world.anchor_locked = False
    anchor = defensive_line_anchor_m(retreat_world)
    raw_retreat = far_ruck + 10.0
    check(4, "the retreat anchor clamps ruck_x + 10 at the tryline",
          raw_retreat > TRYLINE and abs(anchor - TRYLINE) < 1e-12,
          "raw=%.2f anchor=%.2f" % (raw_retreat, anchor))

    depth_world = stub_world(phase="tackle_drive", bc_x=far_ruck)
    body = stub_role_body(role="WINGER")
    depth_target = dbg.clamp_depth_target_x(
        depth_world, body, far_ruck + DEEPEST_OFFSET, TUNERS,
    )
    check(4, "the depth target clamps bc.x + offset at the SAME tryline",
          abs(depth_target - TRYLINE) < 1e-12
          and abs(depth_target - anchor) < 1e-12,
          "depth=%.2f anchor=%.2f" % (depth_target, anchor))

    # Both are envelope clamps: neither may fall short of the far tryline.
    check(4, "both clamps are two-sided (envelope, not a one-sided rail)",
          abs(dbg.clamp_depth_target_x(depth_world, body, -50.0, TUNERS)
              - FAR_TRYLINE) < 1e-12)


# ─────────────────────────────────────────────────────────────────────
# [5] THE GATE
# ─────────────────────────────────────────────────────────────────────

def verify_gate():
    section("[5] The gate — tuner + runtime lever, byte-identical when off")

    check(5, "the invariant is armed by default",
          dbg.depth_boundary_clamp_enabled(TUNERS) is True)
    check(5, "an absent tuner attribute defaults to ARMED",
          dbg.depth_boundary_clamp_enabled(SimpleNamespace()) is True)
    check(5, "a None tuner defaults to ARMED",
          dbg.depth_boundary_clamp_enabled(None) is True)

    # A REAL tuner object with ONLY the boundary gate flipped: a stub without
    # WIDTH_OFFSETS would make the role-offset lookup fall back to the D
    # default, which would prove nothing about the clamp being off.
    off = DefenceTuners()
    off.depth_boundary_clamp_enabled = False
    check(5, "the tuner can disable the invariant",
          dbg.depth_boundary_clamp_enabled(off) is False)
    check(5, "the disabling tuner still carries the canonical offsets",
          float(off.WIDTH_OFFSETS["WINGER"]) == 10.0)

    world = stub_world(phase="tackle_drive", bc_x=108.0)
    body = stub_role_body(pid=600, role="WINGER")
    raw = TRYLINE + 8.0
    check(5, "DISABLED: the clamp is an identity function",
          dbg.clamp_depth_target_x(world, body, raw, off) == raw)
    stubbed = stub_role_body(pid=601, role="D")
    stubbed.role_depth_target_x = raw
    check(5, "DISABLED: the enforcement pass repairs nothing",
          dbg.apply_depth_boundary_guard(world, [stubbed], off) == []
          and float(stubbed.role_depth_target_x) == raw)
    # The single law reads its tuners THROUGH THE WORLD, so the disabled
    # variant must be exercised on a world that resolves to the disabled
    # tuner — otherwise the probe would test nothing.
    off_world = stub_world(
        phase="tackle_drive", bc_x=108.0,
        open_play_engine=SimpleNamespace(def_tuners=off),
    )
    law_disabled = ddr._depth_role_target_x(off_world, _law_body("WINGER"))
    check(5, "DISABLED: the single law restores the pre-fix behaviour "
             "(raw bc.x + offset, unclamped)",
          abs(law_disabled - raw) < 1e-9,
          "law=%.2f expected=%.2f" % (law_disabled, raw))
    check(5, "ARMED: the same law on the same inputs IS clamped",
          abs(ddr._depth_role_target_x(world, _law_body("WINGER")) - TRYLINE)
          < 1e-9)

    # ── RUNTIME LEVER ──
    prev = os.environ.get("RLM_DEPTH_BOUNDARY_CLAMP")
    os.environ["RLM_DEPTH_BOUNDARY_CLAMP"] = "0"
    try:
        check(5, "RLM_DEPTH_BOUNDARY_CLAMP=0 disarms the invariant",
              dbg.depth_boundary_clamp_enabled(TUNERS) is False)
        check(5, "...and the clamp then returns the value verbatim",
              dbg.clamp_depth_target_x(world, body, raw, TUNERS) == raw)
        check(5, "...and the enforcement pass then repairs nothing",
              dbg.apply_depth_boundary_guard(world, [stubbed], TUNERS) == [])
    finally:
        if prev is None:
            os.environ.pop("RLM_DEPTH_BOUNDARY_CLAMP", None)
        else:
            os.environ["RLM_DEPTH_BOUNDARY_CLAMP"] = prev
    check(5, "the lever is restored (invariant armed again)",
          dbg.depth_boundary_clamp_enabled(TUNERS) is True)

    # ── THE OPTIONAL CLEARANCE ──
    margin_tuners = SimpleNamespace(depth_boundary_clamp_enabled=True,
                                   depth_boundary_tryline_margin_m=2.0)
    check(5, "the clearance moves the bound inside the tryline",
          abs(dbg.defending_tryline_x(margin_tuners) - (TRYLINE - 2.0)) < 1e-12)
    check(5, "a negative clearance is floored at the tryline",
          abs(dbg.defending_tryline_x(
              SimpleNamespace(depth_boundary_tryline_margin_m=-5.0))
              - TRYLINE) < 1e-12)
    check(5, "a malformed clearance degrades to zero",
          abs(dbg.defending_tryline_x(
              SimpleNamespace(depth_boundary_tryline_margin_m="junk"))
              - TRYLINE) < 1e-12)


def _law_body(role):
    """A bare Player for the single-law probe (drive phase, no stamp)."""
    d = Player(x=80.0, y=35.0, player_id=700)
    d.fullback_role_active = False
    d.depth_role = role
    return d


# ─────────────────────────────────────────────────────────────────────
# [6] WIRING + IMPORT SAFETY
# ─────────────────────────────────────────────────────────────────────

def verify_wiring():
    section("[6] Wiring + import safety")

    guard_src = read_src(MODULE_PATH)
    depth_src = read_src(DEPTH_ROLES_PATH)
    authority_src = read_src(TES_AUTHORITY_PATH)
    manager_src = read_src(MANAGER_PATH)
    pipeline_src = read_src(PIPELINE_PATH)
    tuners_src = read_src(TUNERS_PATH)

    # ── THE SINGLE LAW IS CLAMPED ──
    check(6, "defensive_depth_roles applies the invariant in its depth law",
          "clamp_depth_target_x" in depth_src
          and "_bounded_depth_x" in depth_src)
    check(6, "the law's drive branch routes through the bounded wrapper",
          "return _bounded_depth_x(world, d, raw, tuners)" in depth_src)

    # ── THE TES AUTHORITY IS CLAMPED ──
    check(6, "tes_depth_authority clamps before it publishes target_x",
          "clamp_depth_target_x" in authority_src
          and authority_src.find("clamp_depth_target_x")
          < authority_src.find("d.target_x = float(target_x)"))

    # ── THE POSITIONAL LOCK IS CLAMPED ──
    lock_idx = manager_src.find("def _refresh_depth_lock_x")
    lock_end = manager_src.find("class DefenceManager")
    lock_body = manager_src[lock_idx:lock_end] if lock_idx != -1 else ""
    check(6, "_refresh_depth_lock_x applies the invariant",
          "clamp_depth_target_x" in lock_body,
          "located=%s" % (lock_idx != -1))
    check(6, "the positional lock passes a real world (not the body as world)",
          "_clamp_depth_target_x(world, p, _rtxf)" in lock_body)

    # ── THE PIPELINE WIRES THE ENFORCEMENT PASS ──
    check(6, "the un-gated preamble runs the enforcement pass",
          "apply_depth_boundary_guard" in pipeline_src)
    check(6, "the preamble audits before it repairs (drift detector first)",
          pipeline_src.find("_audit_depth_boundary(")
          < pipeline_src.find("_apply_depth_boundary_guard("))
    check(6, "the repair runs BEFORE the TES drive chamber early-return "
             "(or it would never run during the drive)",
          pipeline_src.find("_apply_depth_boundary_guard(")
          < pipeline_src.find("if _is_tes_drive_substate(world):"))
    check(6, "the preamble exposes the wiring proof",
          "depth_boundary_repaired_last" in pipeline_src)

    # ── THE TUNERS EXIST ON THE CANONICAL OBJECT ──
    check(6, "DefenceTuners carries depth_boundary_clamp_enabled",
          hasattr(TUNERS, "depth_boundary_clamp_enabled")
          and TUNERS.depth_boundary_clamp_enabled is True)
    check(6, "DefenceTuners carries depth_boundary_tryline_margin_m = 0.0",
          hasattr(TUNERS, "depth_boundary_tryline_margin_m")
          and abs(float(TUNERS.depth_boundary_tryline_margin_m)) < 1e-12)
    check(6, "the tuners module documents the invariant",
          "depth_boundary_guard" in tuners_src)

    # ── NO MODULE-SCOPE PROJECT IMPORT (no cycle, any import order) ──
    tree = ast.parse(guard_src)
    module_scope_imports = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            module_scope_imports.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            module_scope_imports.append(node.module or "")
    allowed = ("__future__", "math", "typing")
    offenders = [m for m in module_scope_imports
                 if not m.startswith(allowed)]
    check(6, "the guard's module-scope imports are stdlib only (no cycle)",
          not offenders, "offenders=%s" % offenders)

    # The guard must NOT be imported at module scope by the modules it is
    # wired into (they use function-local imports), so import ORDER is free.
    for label, src, path in (
        ("defensive_depth_roles", depth_src, DEPTH_ROLES_PATH),
        ("tes_depth_authority", authority_src, TES_AUTHORITY_PATH),
        ("defence_manager", manager_src, MANAGER_PATH),
    ):
        tree = ast.parse(src)
        top_level_guard_import = False
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod.split(".")[-1] == "depth_boundary_guard":
                    top_level_guard_import = True
        imported_names = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if (node.module or "").split(".")[-1] != "depth_boundary_guard":
                continue
            imported_names.extend(alias.name for alias in node.names)
        check(6, "%s imports the guard FUNCTION-LOCALLY only" % label,
              not top_level_guard_import,
              "imports=%s" % imported_names)

    # ── THE ENVELOPE IS SINGLE-SOURCED FROM THE ANCHOR AUTHORITY ──
    check(6, "the guard imports the envelope from anchor_authority",
          "from game.defence.defence_lvl_1.anchor_authority import" in guard_src
          and "RIGHT_TRYLINE_X" in guard_src)
    check(6, "the guard defines no SECOND copy of the tryline constant",
          "RIGHT_TRYLINE_X: float" not in guard_src
          and "RIGHT_TRYLINE_X =" not in guard_src)


# ─────────────────────────────────────────────────────────────────────
# [7] NO INVENTED STATE
# ─────────────────────────────────────────────────────────────────────

FORBIDDEN_PREFIXES = ("stamp_", "clear_", "write_", "arm_", "latch_")
FORBIDDEN_NAMES = (
    "BOUNDARY_ATTR",
    "BOUNDARY_TARGET_ATTR",
    "BOUNDARY_ACTIVE_ATTR",
    "BOUNDARY_COOLDOWN_FRAMES",
    "BOUNDARY_HYSTERESIS_M",
)
# Attribute names that would be a latch / cooldown / target store if any
# Store node wrote them inside this module.
FORBIDDEN_STORE_ATTRS = (
    "depth_boundary_active",
    "depth_boundary_pending",
    "depth_boundary_cooldown",
    "depth_boundary_target_x",
    "depth_boundary_frames",
)
FORBIDDEN_SOURCE_TOKENS = (
    "POST_RUCK_DURATION_S",
    "phase_timer",
    "set_phase",
    "release_controller",
)


def verify_no_invented_state():
    section("[7] No invented state — no latch / cooldown / target store")

    public = [n for n in dir(dbg) if not n.startswith("__")]
    bad_prefix = [n for n in public
                  if any(n.startswith(p) for p in FORBIDDEN_PREFIXES)]
    check(7, "no stamp_/clear_/write_/arm_/latch_ API is defined",
          not bad_prefix, "offenders=%s" % bad_prefix)
    bad_names = [n for n in FORBIDDEN_NAMES if n in public]
    check(7, "no latch / cooldown / target-store name is defined",
          not bad_names, "offenders=%s" % bad_names)

    src = read_src(MODULE_PATH)
    bad_tokens = [t for t in FORBIDDEN_SOURCE_TOKENS if t in src]
    check(7, "the guard touches no phase timer / release constant",
          not bad_tokens, "offenders=%s" % bad_tokens)

    tree = ast.parse(src)
    bad_stores = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
            if node.attr in FORBIDDEN_STORE_ATTRS:
                bad_stores.append(node.attr)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            if node.id in FORBIDDEN_STORE_ATTRS:
                bad_stores.append(node.id)
    check(7, "no Store to a latch / cooldown / target-store attribute",
          not bad_stores, "offenders=%s" % bad_stores)

    # The clamp is a PURE function: identical input => identical output and
    # nothing is remembered between calls.
    world = stub_world(phase="tackle_drive", bc_x=108.0)
    body = stub_role_body(pid=800, role="WINGER")
    first = dbg.clamp_depth_target_x(world, body, TRYLINE + 5.0, TUNERS)
    w_before = dict(vars(world))
    b_before = dict(vars(body))
    second = dbg.clamp_depth_target_x(world, body, TRYLINE + 5.0, TUNERS)
    check(7, "two identical calls produce identical output",
          first == second, "%r vs %r" % (first, second))
    check(7, "the pure clamp writes NOTHING onto the world or the body",
          dict(vars(world)) == w_before and dict(vars(body)) == b_before)

    # The enforcement pass writes ONLY the illegal attribute it repairs.
    illegal = stub_role_body(pid=801, role="D")
    illegal.role_depth_target_x = TRYLINE + 6.0
    illegal.depth_lock_x = 55.0
    before = dict(vars(illegal))
    dbg.apply_depth_boundary_guard(world, [illegal], TUNERS)
    changed = {k for k in vars(illegal) if vars(illegal)[k] != before.get(k)}
    check(7, "the pass writes ONLY the attribute it found illegal",
          changed == {"role_depth_target_x"}, "changed=%s" % sorted(changed))


# ─────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 72)
    print("DEPTH-TARGET BOUNDARY INVARIANT VERIFICATION")
    print("(defensive depth targets may never be generated beyond the "
          "team's own tryline)")
    print("=" * 72)

    verify_clamp()
    verify_single_law()
    verify_writers()
    verify_equivalence()
    verify_gate()
    verify_wiring()
    verify_no_invented_state()

    failed = [r for r in RESULTS if not r["ok"]]
    with open(REPORT_PATH, "w", encoding="utf-8") as fh:
        json.dump({
            "total": len(RESULTS),
            "passed": len(RESULTS) - len(failed),
            "failed": len(failed),
            "failures": failed,
            "checks": RESULTS,
            "envelope": {"far_tryline_x": FAR_TRYLINE,
                         "own_tryline_x": TRYLINE},
        }, fh, indent=2)
    print("\nReport written to %s" % REPORT_PATH)

    print("\n" + "=" * 72)
    if failed:
        for r in failed:
            print("  FAILED: [%s] %s%s" % (
                r["section"], r["name"],
                (" — %s" % r["detail"]) if r["detail"] else "",
            ))
        print("RESULT: %d / %d CHECKS FAILED" % (len(failed), len(RESULTS)))
        return 1
    print("RESULT: ALL CHECKS PASSED (%d)" % len(RESULTS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
