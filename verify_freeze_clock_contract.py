"""verify_freeze_clock_contract.py -- the freeze clock's law.

THE CONTRACT
------------
``freeze_timer`` is declared as a duration but was a LEVEL: it was written
by the miss stun, ``_handle_broken_tackle`` and marker assignment, read by
the ``struct_freeze_timer`` pin and ~40 gates, and never decremented -- so
the only thing that could release a held defender was a lifecycle clear at
RELEASE.

The freeze clock (``world/open_play_engine.decay_freeze_timers``) is the ONE
decrement site.  Its law, asserted here:

  C1  the lever name and default;
  C2  one tick reduces a live timer by exactly dt;
  C3  a DISARMED lever leaves the field byte-identical (the rollback);
  C4  the value reaches exactly 0.0 after ceil(V/dt) ticks and never goes
      negative -- the latch becomes an envelope;
  C5  a body already at 0.0 is NEVER written (so the disarmed path and the
      no-timer path stay bit-identical);
  C6  a body without the attribute is skipped, not crashed;
  C7  dt <= 0 is a no-op;
  C8  the engine calls the clock ONCE per frame and BEFORE the phase
      dispatch (a structural check on the source, so a future refactor that
      drops or reorders the call fails here rather than silently restoring
      the permanent latch);
  C9  THE EXPIRY HAND-BACK -- the tick that takes the timer to zero must
      also pin the body's target to its own position, and the running tick
      and the disarmed lever must not.  See the MEASURED note below.

Run:  python verify_freeze_clock_contract.py
"""

import os
import re
import sys

sys.path.insert(0, ".")

import world.open_play_engine as ope

ENGINE_SRC = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "world", "open_play_engine.py"
)

DT = 1.0 / 60.0
FAILURES = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s  %s" % (name, detail))
        FAILURES.append(name)


class FakePlayer:
    """Minimal stand-in: only ``freeze_timer`` is read or written."""

    def __init__(self, timer=0.0):
        self.freeze_timer = timer


class BarePlayer:
    """A body with no ``freeze_timer`` attribute at all."""


class MovingPlayer:
    """A body with a position and a target, for the expiry hand-back."""

    def __init__(self, timer=0.5, x=10.0, y=20.0, tx=40.0, ty=60.0):
        self.freeze_timer = timer
        self.x = x
        self.y = y
        self.target_x = tx
        self.target_y = ty


class FakeWorld:
    def __init__(self, players):
        self.players = players


def main() -> int:
    print("=" * 78)
    print("FREEZE CLOCK CONTRACT")
    print("=" * 78)

    # ── C1: the lever ─────────────────────────────────────────────────
    check("C1_env_var_name", ope.FREEZE_CLOCK_ENV_VAR == "RLM_TES_FREEZE_CLOCK",
          "got %r" % (ope.FREEZE_CLOCK_ENV_VAR,))
    saved = os.environ.pop(ope.FREEZE_CLOCK_ENV_VAR, None)
    try:
        check("C1_default_on", ope.freeze_clock_enabled() is True)

        # ── C2: one tick == dt ────────────────────────────────────────
        p = FakePlayer(0.5)
        world = FakeWorld([p])
        ticked = ope.decay_freeze_timers(world, DT)
        check("C2_one_tick_subtracts_dt", abs(p.freeze_timer - (0.5 - DT)) < 1e-12,
              "got %.12f" % p.freeze_timer)
        check("C2_returns_ticked_count", ticked == 1, "got %r" % (ticked,))

        # ── C4: reaches exactly 0 and stops there ─────────────────────
        p = FakePlayer(0.5)
        world = FakeWorld([p])
        ticks = 0
        while p.freeze_timer > 0.0 and ticks < 1000:
            ope.decay_freeze_timers(world, DT)
            ticks += 1
        expected = int(0.5 / DT) + (0 if abs(0.5 / DT - round(0.5 / DT)) < 1e-9 else 1)
        check("C4_reaches_exactly_zero", p.freeze_timer == 0.0,
              "got %.12f after %d ticks" % (p.freeze_timer, ticks))
        check("C4_ticks_match_ceil", ticks <= expected + 1,
              "took %d ticks, expected ~%d" % (ticks, expected))

        # never negative, however many extra ticks we apply
        for _ in range(50):
            ope.decay_freeze_timers(world, DT)
        check("C4_never_negative", p.freeze_timer == 0.0,
              "got %.12f" % p.freeze_timer)

        # ── C5: a zero timer is not written ──────────────────────────
        z = FakePlayer(0.0)
        world = FakeWorld([z])
        ticked = ope.decay_freeze_timers(world, DT)
        check("C5_zero_body_not_written", ticked == 0 and z.freeze_timer == 0.0,
              "ticked=%r value=%r" % (ticked, z.freeze_timer))

        # ── C6: a body without the attribute is skipped ──────────────
        live = FakePlayer(0.4)
        world = FakeWorld([BarePlayer(), live])
        ticked = ope.decay_freeze_timers(world, DT)
        check("C6_bare_body_skipped", ticked == 1 and abs(live.freeze_timer - (0.4 - DT)) < 1e-12,
              "ticked=%r value=%.12f" % (ticked, live.freeze_timer))

        # ── C7: dt <= 0 is a no-op ───────────────────────────────────
        p = FakePlayer(0.3)
        world = FakeWorld([p])
        check("C7_zero_dt_noop",
              ope.decay_freeze_timers(world, 0.0) == 0 and p.freeze_timer == 0.3)
        check("C7_negative_dt_noop",
              ope.decay_freeze_timers(world, -0.5) == 0 and p.freeze_timer == 0.3)

        # ── C9: THE EXPIRY HAND-BACK ─────────────────────────────────
        # A MEASURED regression (stall audit: 20 stalled body-frames
        # disarmed vs 420 with a bare decay, plus a new out-of-field-target
        # failure) showed that releasing the step-7 pin without re-pinning
        # hands the body back holding a STALE, often out-of-field target at
        # zero speed -- the exact shape the stall audit forbids.  Expiry
        # must therefore leave the body SELF-CONSISTENT.
        m = MovingPlayer(timer=DT * 0.5, x=10.0, y=20.0, tx=40.0, ty=60.0)
        world = FakeWorld([m])
        ope.decay_freeze_timers(world, DT)
        check("C9_expiry_zeroes_timer", m.freeze_timer == 0.0,
              "got %.12f" % m.freeze_timer)
        check("C9_expiry_pins_target_to_position",
              (m.target_x, m.target_y) == (10.0, 20.0),
              "target=(%r, %r)" % (m.target_x, m.target_y))

        # a NON-expiry tick must NOT touch the target
        m = MovingPlayer(timer=0.5, x=10.0, y=20.0, tx=40.0, ty=60.0)
        world = FakeWorld([m])
        ope.decay_freeze_timers(world, DT)
        check("C9_running_tick_leaves_target_alone",
              (m.target_x, m.target_y) == (40.0, 60.0),
              "target=(%r, %r)" % (m.target_x, m.target_y))

        # and the disarmed lever must not pin either
        os.environ[ope.FREEZE_CLOCK_ENV_VAR] = "0"
        m = MovingPlayer(timer=DT * 0.5, x=10.0, y=20.0, tx=40.0, ty=60.0)
        world = FakeWorld([m])
        ope.decay_freeze_timers(world, DT)
        check("C9_disarmed_does_not_pin",
              (m.target_x, m.target_y) == (40.0, 60.0)
              and m.freeze_timer == DT * 0.5,
              "target=(%r, %r) timer=%r"
              % (m.target_x, m.target_y, m.freeze_timer))
        os.environ.pop(ope.FREEZE_CLOCK_ENV_VAR, None)

        # ── C3: the disarmed lever is byte-identical ─────────────────
        os.environ[ope.FREEZE_CLOCK_ENV_VAR] = "0"
        check("C3_disarmed_reader_false", ope.freeze_clock_enabled() is False)
        p = FakePlayer(0.5)
        world = FakeWorld([p])
        ticked = ope.decay_freeze_timers(world, DT)
        check("C3_disarmed_leaves_value",
              ticked == 0 and p.freeze_timer == 0.5,
              "ticked=%r value=%r" % (ticked, p.freeze_timer))
        # the same for the other documented spellings
        for off in ("false", "no", "off", ""):
            os.environ[ope.FREEZE_CLOCK_ENV_VAR] = off
            check("C3_disarmed_spelling_%r" % off,
                  ope.freeze_clock_enabled() is False)
        # and an explicit on
        for on in ("1", "true", "yes"):
            os.environ[ope.FREEZE_CLOCK_ENV_VAR] = on
            check("C3_armed_spelling_%r" % on,
                  ope.freeze_clock_enabled() is True)
    finally:
        os.environ.pop(ope.FREEZE_CLOCK_ENV_VAR, None)
        if saved is not None:
            os.environ[ope.FREEZE_CLOCK_ENV_VAR] = saved

    # ── C8: the engine wires it once, before the dispatch ────────────
    with open(ENGINE_SRC, "r", encoding="utf-8") as fh:
        src = fh.read()
    call = "decay_freeze_timers(self.world, dt)"
    n_calls = src.count(call)
    check("C8_called_once", n_calls == 1, "found %d call sites" % n_calls)
    idx_call = src.find(call)
    idx_dispatch = src.find("# Dispatch\n        phase_map = {")
    check("C8_call_before_dispatch", 0 <= idx_call < idx_dispatch,
          "call@%d dispatch@%d" % (idx_call, idx_dispatch))
    # Nothing else may decrement the field.  The lookbehind is required:
    # ``marker_freeze_timer -= dt`` is a DIFFERENT field and must not be
    # mistaken for a second clock on this one.
    strays = re.findall(r"(?<![A-Za-z0-9_])freeze_timer\s*-=", src)
    check("C8_no_decrement_anywhere_else", len(strays) == 0,
          "found %d unexpected '-=' write(s) of freeze_timer" % len(strays))
    # sanity: the marker clock's own decrement IS still there
    check("C8_marker_clock_untouched",
          src.count("d.marker_freeze_timer -= dt") == 1,
          "the marker clock's decrement was disturbed")

    print("")
    print("-" * 78)
    if FAILURES:
        print("FAILED %d check(s): %s" % (len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
