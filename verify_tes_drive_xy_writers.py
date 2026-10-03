"""verify_tes_drive_xy_writers.py — contract verifier for the X/Y write trace.

Asserts the mechanics of ``game.debug.tes_xy_write_trace`` (the diagnostic that
attributes every X/Y write on a drive-chamber DEF_LINE_READY / DEF_CHASE body
to the authority that made it):

  1. install() patches ``Player.__setattr__``; uninstall() restores it.
  2. A write on a DEF_LINE_READY body in a TES drive sub-state IS recorded
     and attributed to the calling function/module.
  3. Both axes are captured (``p.x = …`` and ``p.y = …``).
  4. Out-of-scope writes are NOT recorded: wrong role, wrong phase, ball
     carrier.
  5. The record carries the TES-tackler / attached disambiguators read from
     the live world, and ``summarise`` splits genuine-line from tackler.
  6. disarm() stops recording; an uninstalled trace is inert.

Runs as a standalone script (exit 0 = pass, 1 = fail).
"""

from __future__ import annotations

import sys

from player.player import DefenderRole, Player
from game.debug import tes_xy_write_trace as trace


# ─────────────────────────────────────────────────────────────────────
# STUB WORLD
# ─────────────────────────────────────────────────────────────────────

class _StubWorld:
    def __init__(self, phase="TACKLE_DRIVE"):
        self._phase = phase
        self.frame_count = 7
        self.ball_carrier = None
        self.defending_team = []
        self.tackler_ids = set()
        self.primary_tackler_id = None
        self.secondary_tacklers = []
        self.open_play_engine = self   # _phase_label prefers ope._phase


def _mk(pid, role=DefenderRole.DEF_LINE_READY, x=10.0, y=20.0, world=None):
    p = Player(x, y, pid)
    p.defender_role = role
    if world is not None:
        p._world_ref = world
    return p


# ── Writes issued from THIS module (so attribution is deterministic) ──
def _write_from_glide(p, dx):
    p.x = p.x + dx


def _write_from_attach(p, dy):
    p.y = p.y + dy


_PASS = 0
_FAIL = 0


def _check(cond, label):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}")


# ─────────────────────────────────────────────────────────────────────

def part1_install():
    print("[1] install / uninstall")
    _check(trace.install(), "1.1 install() patches")
    _check(trace.is_installed(), "1.2 is_installed() True")
    _check(Player.__setattr__ is trace._traced_setattr,
           "1.3 Player.__setattr__ is the traced wrapper")
    trace.uninstall()
    _check(not trace.is_installed(), "1.4 uninstall() clears installed")
    _check(Player.__setattr__ is object.__setattr__,
           "1.5 Player.__setattr__ restored to object.__setattr__")


def part2_scope_and_axis():
    print("[2] in-scope writes are recorded and attributed, both axes")
    world = _StubWorld("TACKLE_DRIVE")
    p = _mk(900, world=world)
    trace.install()
    trace.reset()
    trace.set_fallback_world(world)
    trace.arm()

    _write_from_glide(p, 0.5)
    recs = trace.records()
    _check(len(recs) == 1, f"2.1 one X record ({len(recs)})")
    if recs:
        r = recs[0]
        _check(r["axis"] == "X", "2.2 axis X")
        _check(r["role"] == "DEF_LINE_READY", "2.3 role recorded")
        _check(r["phase"] == "TACKLE_DRIVE", "2.4 phase recorded")
        _check("_write_from_glide" in r["authority"],
               f"2.5 authority attributed ({r['authority']})")
        _check(abs(r["delta"] - 0.5) < 1e-9, "2.6 delta recorded")

    _write_from_attach(p, -0.25)
    recs = trace.records()
    _check(len(recs) == 2, f"2.7 second write recorded ({len(recs)})")
    if len(recs) >= 2:
        r = recs[1]
        _check(r["axis"] == "Y", "2.8 axis Y")
        _check("_write_from_attach" in r["authority"],
               "2.9 Y write attributed to its caller")
        _check(abs(r["delta"] + 0.25) < 1e-9, "2.10 Y delta recorded")

    # A fallback world (no _world_ref) must still work.
    p2 = _mk(901)                       # no world ref -> fallback
    _write_from_glide(p2, 0.1)
    _check(len(trace.records()) == 3, "2.11 fallback world used when unset")
    return world


def part3_out_of_scope():
    print("[3] out-of-scope writes are NOT recorded")
    world = _StubWorld("TACKLE_DRIVE")
    trace.reset()
    trace.set_fallback_world(world)
    trace.arm()

    tackler = _mk(910, role=DefenderRole.TACKLER_1, world=world)
    _write_from_glide(tackler, 0.4)
    _check(len(trace.records()) == 0, "3.1 TACKLER_1 role ignored")

    line = _mk(911, world=world)
    world._phase = "OPEN_PLAY"
    _write_from_glide(line, 0.4)
    _check(len(trace.records()) == 0, "3.2 OPEN_PLAY phase ignored")
    world._phase = "TACKLE_INIT"
    _write_from_glide(line, 0.4)
    _check(len(trace.records()) == 0, "3.3 TACKLE_INIT phase ignored")

    world._phase = "TACKLE_DOMINANCE"
    world.ball_carrier = line
    _write_from_glide(line, 0.4)
    _check(len(trace.records()) == 0, "3.4 ball carrier ignored")
    world.ball_carrier = None

    # No world at all -> phase unknown -> ignored.
    orphan = _mk(912)
    trace.set_fallback_world(None)
    _write_from_glide(orphan, 0.4)
    _check(len(trace.records()) == 0, "3.5 no world -> phase unknown -> ignored")
    trace.set_fallback_world(world)

    # A real in-scope write after all the exclusions still lands.
    _write_from_glide(line, 0.4)
    _check(len(trace.records()) == 1, "3.6 in-scope write still recorded")


def part4_scope_flags_and_summary():
    print("[4] TES-tackler / attached disambiguators + summarise()")
    world = _StubWorld("TACKLE_DRIVE")
    trace.reset()
    trace.set_fallback_world(world)
    trace.arm()

    # A body that is BOTH DEF_LINE_READY-tagged AND a registered TES tackler.
    t = _mk(920, world=world)
    world.tackler_ids = {920}
    _write_from_glide(t, 0.3)
    r = trace.records()[-1]
    _check(r["tes_tackler"] is True, "4.1 tes_tackler flag True")
    _check(r["attached"] is False, "4.2 attached flag False")

    # A genuine line body.
    line = _mk(921, world=world)
    _write_from_glide(line, 0.3)
    _check(trace.records()[-1]["tes_tackler"] is False, "4.3 genuine line body")

    # An attached joiner.
    a = _mk(922, world=world)
    a.tes_attached = True
    _write_from_attach(a, 0.3)
    _check(trace.records()[-1]["attached"] is True, "4.4 attached flag True")

    s = trace.summarise()
    glide_auth = "verify_tes_drive_xy_writers.py:_write_from_glide"
    attach_auth = "verify_tes_drive_xy_writers.py:_write_from_attach"
    _check(s["total"] == 3, f"4.5 summarise total ({s['total']})")
    _check(s["by_axis"].get("X") == 2 and s["by_axis"].get("Y") == 1,
           f"4.6 by_axis split ({s['by_axis']})")
    # ``by_scope`` groups per AUTHORITY (the call site).  The glide caller
    # wrote one TES-tackler-tagged body and one genuine line body; the attach
    # caller wrote one attached body.
    _g = s["by_scope"].get(glide_auth, {})
    _a = s["by_scope"].get(attach_auth, {})
    _check(_g.get("line") == 1 and _g.get("tackler") == 1,
           f"4.7 glide caller splits line/tackler ({_g})")
    _check(_a.get("attached") == 1,
           f"4.8 attach caller marks attached ({_a})")
    _check(sum(s["by_authority_axis"].values()) == 3,
           "4.9 by_authority_axis sums to total")


def part5_disarm_and_inert():
    print("[5] disarm() stops recording; an uninstalled trace is inert")
    world = _StubWorld("TACKLE_DRIVE")
    trace.reset()
    trace.set_fallback_world(world)
    trace.arm()
    line = _mk(930, world=world)
    _write_from_glide(line, 0.2)
    n = len(trace.records())
    _check(n == 1, "5.1 armed trace records")

    trace.disarm()
    _write_from_glide(line, 0.2)
    _check(len(trace.records()) == n, "5.2 disarm() stops recording")

    trace.install()
    trace.arm()
    trace.uninstall()
    _write_from_glide(line, 0.2)
    _check(len(trace.records()) == n, "5.3 uninstalled trace is inert")
    _check(not trace.is_recording(), "5.4 uninstall() forces recording off")


def main():
    print("=== verify_tes_drive_xy_writers ===")
    try:
        part1_install()
        part2_scope_and_axis()
        part3_out_of_scope()
        part4_scope_flags_and_summary()
        part5_disarm_and_inert()
    finally:
        trace.disarm()
        trace.uninstall()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
