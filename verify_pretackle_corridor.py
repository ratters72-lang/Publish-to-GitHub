"""verify_pretackle_corridor.py — SPEED-AUTHORITY CONSOLIDATION contract.

Run:  python verify_pretackle_corridor.py

Locks in the pre-tackle corridor as the SINGLE speed authority for a
committed tackler's final approach, and the TES outcome system as the
single FINAL CONSUMER of that speed.  Every check below corresponds to a
defect the speed-authority audit (probe_speed_authority.py) actually
measured on a real kickoff->catch->tackle cycle:

  1  RADIUS HIERARCHY      corridor radius >= slowdown band + margin, so a
                           band change can never be silently capped.
  2  SINGLE-SOURCED TUNERS the pre_tackle_* / pretackle_* aliases are one
                           canonical pair -- no independent read may drift.
  3  MEMBERSHIP            a committed owner inside the radius latches; a
                           body outside does not; at most ONE per side.
  4  STICKY INCUMBENT      [FIX A] a latched owner KEEPS ownership when the
                           TACKLE-STATE GATE stamps tackle_state_active, when
                           the structured pipeline sets freeze_timer, and
                           when frozen -- the three gates that open exactly
                           in the final metres.  It is released by leaving
                           the radius or losing the committed role.
  5  YIELDING WRITERS      is_corridor_owned is True for a body carrying a
                           live COMMIT / PLANTED authority state, which is
                           what makes every yielding writer yield on the
                           first frame the corridor opens.
  6  CONTACT HANDOFF       stamp_handoff records the contact velocity and
                           contact_velocity returns it when the handoff gate
                           is on; live velocity when off; live velocity when
                           no stamp exists (byte-identical for a body that
                           never entered the corridor).
  7  TES CONSUMER          [game/tes/tes.py] _make_ctx applies the supplied
                           velocity override for the primary tackler and
                           leaves every unstamped body (the attacker) alone.
  8  CANARY                the master gate off makes the corridor a strict
                           no-op -- the pre-consolidation pipeline back.
  9  DETERMINISM           identical inputs produce identical membership.
 10  SOURCE GUARDS          the two rival writers measured by the audit now
                           yield to the corridor, and the phase-authority
                           table owns every corridor source.
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import math
import sys

import pygame

pygame.init()
pygame.display.set_mode((1, 1))

from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1 import pretackle_corridor as C

_FAILURES: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    """Record one contract check."""
    mark = "PASS" if ok else "FAIL"
    suffix = f"  ({detail})" if detail else ""
    print(f"  [{mark}] {label}{suffix}")
    if not ok:
        _FAILURES.append(label)


class _Role:
    def __init__(self, name):
        self.name = name


class _Body:
    """Minimal defender stub — duck-typed exactly like the corridor API."""

    def __init__(self, pid, x, y, role="DEF_LINE_READY",
                 tackle_role="PRIMARY"):
        self.player_id = pid
        self.x = float(x)
        self.y = float(y)
        self.vx = 0.0
        self.vy = 0.0
        self.target_x = float(x)
        self.target_y = float(y)
        self.move_speed = 0.0
        self.frozen = False
        self.freeze_timer = 0.0
        self.defender_role = _Role(role)
        self.tackle_role = tackle_role
        self.fullback_role_active = False
        self.tackle_state_active = False
        self._tackle_commit_active = False
        self.state = "IDLE"
        self.facing = 0.0
        self.rot = 0.0
        self.mass_kg = 90.0
        self.stamina = 1.0
        self.aggression = 0.5
        self.tackle_skill = 0.5
        self.offload_skill = 0.5
        self.strength = 0.5


class _Carrier:
    def __init__(self, x, y):
        self.x = float(x)
        self.y = float(y)


class _World:
    def __init__(self):
        self.frame_count = 0
        self.ball_carrier = None


# ─────────────────────────────────────────────────────────────────────
# 1 + 2  RADIUS HIERARCHY / SINGLE-SOURCED TUNERS
# ─────────────────────────────────────────────────────────────────────

def _check_tuners(t: DefenceTuners) -> None:
    print("\n1 + 2  RADIUS HIERARCHY / SINGLE-SOURCED TUNERS")
    t.assert_radii()
    check("assert_radii() accepts the shipped tuner set", True)

    check("pre_tackle_slowdown_band_m aliases pretackle_slowdown_band_m",
          t.pre_tackle_slowdown_band_m == t.pretackle_slowdown_band_m,
          f"{t.pre_tackle_slowdown_band_m}")
    check("pre_tackle_slowdown_scale aliases pretackle_slowdown_scale",
          t.pre_tackle_slowdown_scale == t.pretackle_slowdown_scale,
          f"{t.pre_tackle_slowdown_scale}")

    base = C.corridor_radius_m(t)
    check("corridor radius >= band + margin",
          base >= C.band_m(t) + C.corridor_margin_m(t) - 1e-9,
          f"r={base:.2f} band={C.band_m(t):.2f}")

    # Widening the band must widen the corridor (the band is a floor, never
    # silently capped by a stale radius tuner).
    saved = t.pretackle_slowdown_band_m
    try:
        t.pretackle_corridor_radius_m = 1.0
        t.pretackle_slowdown_band_m = 9.0
        widened = C.corridor_radius_m(t)
        check("a widened band widens the corridor radius",
              widened >= 9.0 + C.corridor_margin_m(t) - 1e-9,
              f"r={widened:.2f}")
    finally:
        t.pretackle_slowdown_band_m = saved
        t.pretackle_corridor_radius_m = 4.5


# ─────────────────────────────────────────────────────────────────────
# 3 + 4  MEMBERSHIP / STICKY INCUMBENT
# ─────────────────────────────────────────────────────────────────────

def _check_membership(t: DefenceTuners) -> None:
    print("\n3  MEMBERSHIP (one owner per side, radius-bounded)")
    world = _World()
    bc = _Carrier(50.0, 30.0)
    world.ball_carrier = bc
    r = C.corridor_radius_m(t)

    owner = _Body(1, 50.0 + r - 0.5, 30.0)          # inside
    outside = _Body(2, 50.0 + r + 2.0, 30.0)        # outside
    owners = C.enter_corridor(world, [owner, outside], bc, t)
    check("a committed owner inside the radius latches", owner in owners)
    check("a committed body outside the radius does not latch",
          outside not in owners)
    check("is_corridor_owned agrees with membership",
          C.is_corridor_owned(owner) and not C.is_corridor_owned(outside))
    check("the membership streak counts from 1 on the first frame",
          C.corridor_frames(owner) == 1, f"{C.corridor_frames(owner)}")

    # Two committed bodies on the SAME side: only one may own.
    a = _Body(11, 50.0 + r - 1.0, 30.0)
    b = _Body(12, 50.0 + r - 2.0, 30.0)
    owners2 = C.enter_corridor(world, [a, b], bc, t)
    check("at most ONE owner per side of the carrier lane",
          len(owners2) == 1, f"owners={[o.player_id for o in owners2]}")

    # Release by leaving the radius.
    owner.x = 50.0 + r + 5.0
    owners3 = C.enter_corridor(world, [owner], bc, t)
    check("leaving the radius releases ownership",
          owner not in owners3 and C.corridor_frames(owner) == 0)

    print("\n4  STICKY INCUMBENT [FIX A] — the gates that open in the "
          "final metres")
    # Establish ownership first...
    d = _Body(21, 50.0 + r - 1.0, 30.0)
    C.enter_corridor(world, [d], bc, t)
    check("baseline: the body is owned before any gate opens",
          C.is_corridor_owned(d))

    # ...then open each final-approach gate in turn.  Ownership MUST hold.
    d.tackle_state_active = True
    C.enter_corridor(world, [d], bc, t)
    check("ownership SURVIVES tackle_state_active (the tackle-state gate "
          "opens inside the tackle radius)", C.is_corridor_owned(d))

    d.tackle_state_active = False
    d.freeze_timer = 0.4
    C.enter_corridor(world, [d], bc, t)
    check("ownership SURVIVES a live freeze_timer (the structured hold "
          "branch)", C.is_corridor_owned(d))

    d.freeze_timer = 0.0
    d.frozen = True
    C.enter_corridor(world, [d], bc, t)
    check("ownership SURVIVES frozen", C.is_corridor_owned(d))

    # Release paths still work with the gates open.
    d.frozen = False
    d.tackle_role = "PASSIVE"
    d._tackle_commit_active = False
    C.enter_corridor(world, [d], bc, t)
    check("losing the committed role DOES release ownership",
          not C.is_corridor_owned(d))

    d.tackle_role = "PRIMARY"
    d.x = 50.0 + r + 5.0
    C.enter_corridor(world, [d], bc, t)
    check("leaving the radius DOES release ownership",
          not C.is_corridor_owned(d))

    # A body that was NEVER latched must not be admitted by the bypass.
    fresh = _Body(22, 50.0 + 1.0, 30.0)
    fresh.tackle_state_active = True
    owners4 = C.enter_corridor(world, [fresh], bc, t)
    check("a never-latched body is NOT admitted by the incumbent bypass",
          fresh not in owners4)


# ─────────────────────────────────────────────────────────────────────
# 5  YIELDING WRITERS — is_corridor_owned via the live state
# ─────────────────────────────────────────────────────────────────────

def _check_owned_state() -> None:
    print("\n5  YIELDING WRITERS — ownership via the live authority state")
    committed = _Body(31, 40.0, 30.0)
    committed.pre_tackle_state = _Role("COMMIT")
    check("a COMMIT body reads as corridor-owned", C.is_corridor_owned(committed))

    planted = _Body(32, 40.0, 30.0)
    planted.pre_tackle_state = _Role("PLANTED")
    check("a PLANTED body reads as corridor-owned",
          C.is_corridor_owned(planted))

    plain = _Body(33, 40.0, 30.0)
    plain.pre_tackle_state = _Role("NONE")
    check("a NONE body does NOT read as corridor-owned",
          not C.is_corridor_owned(plain))


# ─────────────────────────────────────────────────────────────────────
# 6  CONTACT HANDOFF
# ─────────────────────────────────────────────────────────────────────

def _check_handoff(t: DefenceTuners) -> None:
    print("\n6  CONTACT HANDOFF — the corridor stamps, TES consumes")
    d = _Body(41, 46.4, 30.2)
    d.vx, d.vy = 1.28, -0.4
    C.stamp_handoff(d, "COMMIT", 0.25)
    check("stamp records vx", abs(d.pretackle_contact_vx - 1.28) < 1e-9)
    check("stamp records vy", abs(d.pretackle_contact_vy + 0.4) < 1e-9)
    check("stamp records the speed magnitude",
          abs(d.pretackle_contact_speed_mps - math.hypot(1.28, 0.4)) < 1e-9)
    check("stamp records the plant state",
          d.pretackle_plant_state == "COMMIT")
    check("stamp records the approach angle",
          abs(d.pretackle_approach_angle_rad - 0.25) < 1e-9)

    vx, vy = C.contact_velocity(d, t)
    check("contact_velocity returns the STAMP when the handoff gate is on",
          abs(vx - 1.28) < 1e-9 and abs(vy + 0.4) < 1e-9)

    # A later writer moves the live body; the contract must be unmoved.
    d.vx, d.vy = 9.9, 9.9
    vx2, vy2 = C.contact_velocity(d, t)
    check("the contract SURVIVES a writer that moves the live velocity "
          "after the stamp", abs(vx2 - 1.28) < 1e-9 and abs(vy2 + 0.4) < 1e-9)

    unstamped = _Body(42, 40.0, 30.0)
    unstamped.vx, unstamped.vy = 3.0, -1.0
    vx3, vy3 = C.contact_velocity(unstamped, t)
    check("an unstamped body returns its LIVE velocity (byte-identical)",
          abs(vx3 - 3.0) < 1e-9 and abs(vy3 + 1.0) < 1e-9)

    saved = getattr(t, "pretackle_handoff_enabled", True)
    try:
        t.pretackle_handoff_enabled = False
        vx4, vy4 = C.contact_velocity(d, t)
        check("handoff gate OFF returns the live velocity",
              abs(vx4 - 9.9) < 1e-9 and abs(vy4 - 9.9) < 1e-9)
    finally:
        t.pretackle_handoff_enabled = saved


# ─────────────────────────────────────────────────────────────────────
# 7  TES CONSUMER — _make_ctx applies the override
# ─────────────────────────────────────────────────────────────────────

def _check_tes_consumer() -> None:
    print("\n7  TES CONSUMER [game/tes/tes.py] — the primary's contact "
          "velocity")
    from game.tes.tes import _make_ctx

    body = _Body(51, 40.0, 30.0)
    body.vx, body.vy = 0.0, 0.0          # live body already zeroed

    plain = _make_ctx(body)
    check("without an override _make_ctx uses the live velocity",
          abs(plain.vx) < 1e-9 and abs(plain.vy) < 1e-9)

    overridden = _make_ctx(body, velocity=(2.5, -1.5))
    check("with an override _make_ctx uses the supplied contact velocity",
          abs(overridden.vx - 2.5) < 1e-9 and abs(overridden.vy + 1.5) < 1e-9)
    check("the override also drives the derived momentum",
          abs(overridden.momentum
              - overridden.mass * math.hypot(2.5, 1.5)) < 1e-6)


# ─────────────────────────────────────────────────────────────────────
# 8  CANARY
# ─────────────────────────────────────────────────────────────────────

def _check_canary(t: DefenceTuners) -> None:
    print("\n8  CANARY — gate off restores the pre-consolidation pipeline")
    world = _World()
    bc = _Carrier(50.0, 30.0)
    d = _Body(61, 50.0 + C.corridor_radius_m(t) - 1.0, 30.0)

    saved = getattr(t, "pretackle_corridor_owns_speed", True)
    try:
        t.pretackle_corridor_owns_speed = False
        owners = C.enter_corridor(world, [d], bc, t)
        check("gate off -> enter_corridor is a strict no-op", owners == [])
        check("gate off -> is_corridor_owned is always False",
              not C.is_corridor_owned(d))
    finally:
        t.pretackle_corridor_owns_speed = saved


# ─────────────────────────────────────────────────────────────────────
# 9  DETERMINISM
# ─────────────────────────────────────────────────────────────────────

def _check_determinism(t: DefenceTuners) -> None:
    print("\n9  DETERMINISM — identical inputs, identical membership")
    world = _World()
    bc = _Carrier(50.0, 30.0)
    r = C.corridor_radius_m(t)

    def _run():
        bodies = [
            _Body(71, 50.0 + r - 1.0, 29.0),
            _Body(72, 50.0 + r - 1.2, 31.0),
            _Body(73, 50.0 + r - 1.4, 29.5),
        ]
        return [d.player_id
                for d in C.enter_corridor(world, bodies, bc, t)]

    first, second = _run(), _run()
    check("two identical frames produce identical owners",
          first == second, f"{first} == {second}")


# ─────────────────────────────────────────────────────────────────────
# 10  SOURCE GUARDS — the rival writers yield; the table owns the sources
# ─────────────────────────────────────────────────────────────────────

def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _check_source_guards() -> None:
    print("\n10 SOURCE GUARDS — every measured rival writer yields")

    dbu = _read("game/defence/defence_lvl_1/defensive_behaviour_unified.py")
    # FINDING 1: the non-marker freeze-timer hold zeroed a corridor body's
    # velocity and pinned it while the collision system pushed it away.
    marker = 'd.target_source = "struct_freeze_timer"'
    idx = dbu.find(marker)
    check('the struct_freeze_timer writer exists (audit target found)',
          idx >= 0)
    if idx >= 0:
        window = dbu[max(0, idx - 900):idx]
        check("the freeze-timer branch YIELDS to the corridor "
              "(no length-away check, exact guard present)",
              "if _is_corridor_owned(d):" in window
              and "continue" in window,
              "guard within the preceding branch")

    guard = _read("game/defence/defence_lvl_1/phase_authority_guard.py")
    for src in ("pre_tackle", "tackle_engage_guard",
                "line_break_static_guard", "seed_hold",
                "catch_x_authority"):
        check(f"phase table owns the OPEN_PLAY source {src!r}",
              f'"{src}"' in guard)
    check("phase table matches the composite 'band:' prefix",
          '"band"' in guard and "_OPEN_PLAY_OWNED_PREFIXES" in guard)

    tes = _read("game/tes/tes.py")
    check("TES reads the corridor contract at the primary handoff",
          "_corridor_contact_velocity(defender" in tes)
    check("TES releases the corridor latch at the handoff",
          "_clear_corridor(defender)" in tes)


def main() -> int:
    print("=" * 74)
    print("verify_pretackle_corridor — SPEED-AUTHORITY CONSOLIDATION contract")
    print("=" * 74)

    t = DefenceTuners()
    _check_tuners(t)
    _check_membership(t)
    _check_owned_state()
    _check_handoff(t)
    _check_tes_consumer()
    _check_canary(t)
    _check_determinism(t)
    _check_source_guards()

    print("\n" + "=" * 74)
    if _FAILURES:
        print(f"FAILED ({len(_FAILURES)}):")
        for name in _FAILURES:
            print(f"  - {name}")
        return 1
    print("ALL CHECKS PASSED — the pre-tackle corridor is the sole speed "
          "authority for the final approach, and the TES outcome system is "
          "its sole consumer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
