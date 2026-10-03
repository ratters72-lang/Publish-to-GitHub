"""verify_writer_priority_bands.py — writer-conflict priority-band verifier.

Proves the lateral (Y, 0.3 m) / depth (X, 0.5 m) band law:

  1. A competing writer whose target_y is within 0.3 m of the dominant
     writer's target_y is IGNORED (its Y write does not land).
  2. A competing writer whose target_x is within 0.5 m of the dominant
     writer's target_x is IGNORED (its X write does not land).
  3. A competing writer that differs on BOTH axes beyond the bands is a
     genuinely different signal and the STRONGEST (highest-priority) one
     survives.
  4. Per-axis independence: a depth challenger inside the depth band cannot
     discard a legitimate out-of-band lateral correction (the
     spacing-owns-Y / width-owns-X split is preserved).
  5. Exempt bodies (frozen, committed tacklers, pre-tackle COMMIT,
     tackle-state holders) are never banded.
  6. Feature gate: disabled -> no writes, byte-identical legacy behaviour.
  7. Determinism: identical proposals -> identical winner.
  8. Zero displacement / first-writer-on-tie resolves deterministically.
"""

from __future__ import annotations

import sys

from game.defence.defence_lvl_1 import writer_priority_bands as wpb

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  [PASS] {label}")
    else:
        FAILURES.append(label)
        print(f"  [FAIL] {label} {detail}")


# ── STUBS ────────────────────────────────────────────────────────────

class StubPlayer:
    """Minimal Player stand-in with the fields the band law reads."""

    def __init__(self, pid=1, x=50.0, y=35.0, tx=50.0, ty=35.0):
        self.player_id = pid
        self.x = x
        self.y = y
        self.target_x = tx
        self.target_y = ty
        self.target_source = None
        self.frozen = False
        self.freeze_timer = 0.0
        self.tackle_state_active = False
        self._tackle_commit_active = False
        self.fullback_role_active = False
        self.defender_role = None
        self.depth_role = "B"


class StubWorld:
    def __init__(self, frame=1):
        self.frame_count = frame
        self.defending_team = []
        self.ball_carrier = None


class StubTuners:
    def __init__(self, enabled=True):
        self.writer_priority_bands_enabled = enabled


def _fresh(enabled=True, frame=1, tx=50.0, ty=35.0):
    world = StubWorld(frame)
    d = StubPlayer(tx=tx, ty=ty)
    world.defending_team = [d]
    tuners = StubTuners(enabled)
    wpb.begin_frame(world, [d])
    return world, d, tuners


def _propose(world, d, src, prio, tx=None, ty=None):
    if tx is not None:
        wpb.note(world, d, wpb.AXIS_X, tx, src, prio)
    if ty is not None:
        wpb.note(world, d, wpb.AXIS_Y, ty, src, prio)


# ── 1. LATERAL BAND (Y) ──────────────────────────────────────────────

def test_lateral_band():
    print("\n[1] lateral band: challenger within 0.3 m in Y is IGNORED")
    world, d, tuners = _fresh()
    _propose(world, d, "mmdl_CHASE", 60, tx=50.0, ty=35.0)
    _propose(world, d, "retreat_to_lane", 30, tx=50.0, ty=35.25)
    wpb.resolve(world, [d], tuners)
    check("1.1 dominant Y survives (35.0)", abs(d.target_y - 35.0) < 1e-9,
          f"got {d.target_y}")
    rows = wpb.suppressed_rows(world)
    y_rows = [r for r in rows if r["axis"] == wpb.AXIS_Y]
    check("1.2 retreat Y write suppressed", any(
        r["source"] == "retreat_to_lane" for r in y_rows), f"rows={rows}")

    # 0.31 m apart -> outside the band -> strongest still wins (same prio
    # order: mmdl 60 > retreat 30, so mmdl still wins).
    world2, d2, t2 = _fresh()
    _propose(world2, d2, "mmdl_CHASE", 60, tx=50.0, ty=35.0)
    _propose(world2, d2, "retreat_to_lane", 30, tx=50.0, ty=35.31)
    wpb.resolve(world2, [d2], t2)
    rows2 = [r for r in wpb.suppressed_rows(world2) if r["axis"] == wpb.AXIS_Y]
    check("1.3 >0.3 m is NOT band-suppressed (priority decides)",
          not any(r["source"] == "retreat_to_lane" for r in rows2),
          f"rows={rows2}")
    check("1.4 dominant (mmdl) still wins the Y axis",
          abs(d2.target_y - 35.0) < 1e-9, f"got {d2.target_y}")


# ── 2. DEPTH BAND (X) ────────────────────────────────────────────────

def test_depth_band():
    print("\n[2] depth band: challenger within 0.5 m in X is IGNORED")
    world, d, tuners = _fresh()
    _propose(world, d, "tackler_commit", 1000, tx=50.0, ty=35.0)
    _propose(world, d, "structured_close", 40, tx=50.4, ty=35.0)
    wpb.resolve(world, [d], tuners)
    check("2.1 dominant X survives (50.0)", abs(d.target_x - 50.0) < 1e-9,
          f"got {d.target_x}")
    rows = wpb.suppressed_rows(world)
    x_rows = [r for r in rows if r["axis"] == wpb.AXIS_X]
    check("2.2 structured_close X write suppressed", any(
        r["source"] == "structured_close" for r in x_rows), f"rows={rows}")

    world2, d2, t2 = _fresh()
    _propose(world2, d2, "tackler_commit", 1000, tx=50.0, ty=35.0)
    _propose(world2, d2, "structured_close", 40, tx=50.6, ty=35.0)
    wpb.resolve(world2, [d2], t2)
    rows2 = [r for r in wpb.suppressed_rows(world2) if r["axis"] == wpb.AXIS_X]
    check("2.3 >0.5 m is NOT band-suppressed",
          not any(r["source"] == "structured_close" for r in rows2),
          f"rows={rows2}")
    check("2.4 strongest (tackler_commit 1000) still wins X",
          abs(d2.target_x - 50.0) < 1e-9, f"got {d2.target_x}")


# ── 3. STRONGEST SURVIVES / PER-AXIS INDEPENDENCE ────────────────────

def test_strongest_and_axis_independence():
    print("\n[3] strongest signal survives; axes arbitrate independently")
    # Per-axis independence: the strongest X writer and the strongest Y
    # writer are DIFFERENT sources, and an in-band X challenger must not
    # disable the (out-of-band) Y correction.
    world, d, tuners = _fresh()
    wpb.note(world, d, wpb.AXIS_X, 50.2, "tackler_commit", 1000)
    wpb.note(world, d, wpb.AXIS_Y, 41.0, "mmdl_SLIDE", 60)
    wpb.note(world, d, wpb.AXIS_X, 50.1, "structured_close", 40)
    wpb.note(world, d, wpb.AXIS_Y, 35.0, "retreat_to_lane", 30)
    wpb.resolve(world, [d], tuners)
    check("3.1 Y takes the strongest Y signal (41.0)", abs(d.target_y - 41.0) < 1e-9,
          f"got {d.target_y}")
    check("3.2 X takes the strongest X signal (50.2)", abs(d.target_x - 50.2) < 1e-9,
          f"got {d.target_x}")
    check("3.3 in-band X challenger did not disable the Y outcome",
          abs(d.target_y - 41.0) < 1e-9, f"got {d.target_y}")
    rows3 = [r for r in wpb.suppressed_rows(world) if r["axis"] == wpb.AXIS_X]
    check("3.4 in-band X challenger suppressed on X only",
          any(r["source"] == "structured_close" for r in rows3)
          and not any(r["source"] == "mmdl_SLIDE" for r in rows3),
          f"rows={rows3}")

    # Higher-priority challenger displaced beyond the band takes the axis.
    world2, d2, t2 = _fresh()
    _propose(world2, d2, "retreat_to_lane", 30, tx=50.0, ty=35.0)
    _propose(world2, d2, "tackler_commit", 1000, tx=50.0, ty=38.0)
    wpb.resolve(world2, [d2], t2)
    check("3.5 commit (prio 1000) beats retreat (prio 30) on Y",
          abs(d2.target_y - 38.0) < 1e-9, f"got {d2.target_y}")


# ── 4. EXEMPT BODIES ─────────────────────────────────────────────────

def test_exempt():
    print("\n[4] exempt bodies are never banded")
    world, d, tuners = _fresh()
    d.frozen = True
    _propose(world, d, "mmdl_CHASE", 60, tx=50.0, ty=35.0)
    _propose(world, d, "retreat_to_lane", 30, tx=50.0, ty=35.05)
    out = wpb.resolve(world, [d], tuners)
    check("4.1 frozen body not resolved", out == [], f"out={out}")
    check("4.2 frozen target untouched", abs(d.target_y - 35.0) < 1e-9,
          f"got {d.target_y}")

    d2 = StubPlayer()
    d2.tackle_state_active = True
    check("4.3 tackle-state holder ineligible",
          wpb.is_band_eligible(d2) is False)

    d3 = StubPlayer()
    d3.freeze_timer = 0.4
    check("4.4 freeze-timer body ineligible",
          wpb.is_band_eligible(d3) is False)

    d4 = StubPlayer()
    d4.frozen = False
    check("4.5 plain line body eligible",
          wpb.is_band_eligible(d4) is True)


# ── 5. FEATURE GATE ──────────────────────────────────────────────────

def test_gate():
    print("\n[5] feature gate: disabled -> no band write")
    world, d, tuners = _fresh(enabled=False)
    _propose(world, d, "mmdl_CHASE", 60, tx=50.0, ty=35.0)
    _propose(world, d, "retreat_to_lane", 30, tx=50.0, ty=35.2)
    out = wpb.resolve(world, [d], tuners)
    check("5.1 disabled resolve returns nothing", out == [], f"out={out}")
    check("5.2 target_y unchanged by band",
          abs(d.target_y - 35.0) < 1e-9, f"got {d.target_y}")

    world.writer_priority_bands_enabled = False
    check("5.3 world override wins",
          wpb.writer_priority_bands_enabled(tuners, world) is False)

    w2 = StubWorld()
    check("5.4 default arming is True",
          wpb.writer_priority_bands_enabled(StubTuners(True), w2) is True)


# ── 6. DETERMINISM + BANDS ───────────────────────────────────────────

def test_determinism_and_bands():
    print("\n[6] determinism, band constants, source priority")
    check("6.1 lateral band is 0.3 m", wpb.LATERAL_BAND_M == 0.3,
          str(wpb.LATERAL_BAND_M))
    check("6.2 depth band is 0.5 m", wpb.DEPTH_BAND_M == 0.5,
          str(wpb.DEPTH_BAND_M))
    check("6.3 lateral_band_m override",
          wpb.lateral_band_m(type("W", (), {"lateral_band_m": 1.5})()) == 1.5)
    check("6.4 depth_band_m override",
          wpb.depth_band_m(type("W", (), {"depth_band_m": 2.5})()) == 2.5)

    results = []
    for _ in range(3):
        world, d, tuners = _fresh()
        _propose(world, d, "mmdl_CHASE", 60, tx=50.0, ty=35.0)
        _propose(world, d, "retreat_to_lane", 30, tx=50.2, ty=35.1)
        _propose(world, d, "seed_hold", 800, tx=50.1, ty=35.2)
        wpb.resolve(world, [d], tuners)
        results.append((round(d.target_x, 6), round(d.target_y, 6)))
    check("6.5 identical inputs -> identical winner",
          len(set(results)) == 1, str(results))

    check("6.6 commit source maps to the top tier",
          wpb.priority_for_source("tackler_commit") == wpb.PRIORITY_COMMIT)
    check("6.7 mode-decision source maps to the MMDL tier",
          wpb.priority_for_source("mmdl_CHASE") == wpb.PRIORITY_MMDL)
    check("6.8 unknown source falls back",
          wpb.priority_for_source("mystery_writer") == wpb.PRIORITY_FALLBACK)
    check("6.9 seed-hold outranks role movement",
          wpb.PRIORITY_SEED_HOLD > wpb.PRIORITY_ROLE_MOVEMENT)

    # Same-source re-proposal keeps the last value (net stage write).
    world2, d2, t2 = _fresh()
    wpb.note(world2, d2, wpb.AXIS_Y, 35.0, "mmdl_CHASE", 60)
    wpb.note(world2, d2, wpb.AXIS_Y, 39.0, "mmdl_CHASE", 60)
    wpb.resolve(world2, [d2], t2)
    check("6.10 same source: last value wins (net write)",
          abs(d2.target_y - 39.0) < 1e-9, f"got {d2.target_y}")


# ── 7. WINNER-FLIP HYSTERESIS (2026-09-09) ──────────────────────────

def test_winner_hysteresis():
    print("\n[7] winner-flip hysteresis: near-equivalent challenger cannot steal an axis")
    # Frame 1: spacing (X=50.1, disp 0.1) beats width (X=50.0, disp 0.0).
    world = StubWorld(frame=1)
    d = StubPlayer(x=50.0, y=35.0, tx=50.0, ty=35.0)
    world.defending_team = [d]
    tuners = StubTuners()
    wpb.begin_frame(world, [d])
    _propose(world, d, "spacing", 50, tx=50.1)
    _propose(world, d, "width", 50, tx=50.0)
    wpb.resolve(world, [d], tuners)
    check("7.1 frame1 winner = spacing (larger displacement)",
          abs(d.target_x - 50.1) < 1e-9, f"got {d.target_x}")

    # Frame 2: spacing X=50.0, width X=50.1.  WITHOUT hysteresis width would
    # win (larger displacement); WITH the flip band (0.25) spacing RETAINS
    # the axis because 50.1 is within 0.25 m of 50.0.
    world.frame_count = 2
    wpb.begin_frame(world, [d])
    _propose(world, d, "spacing", 50, tx=50.0)
    _propose(world, d, "width", 50, tx=50.1)
    wpb.resolve(world, [d], tuners)
    check("7.2 frame2 spacing RETAINS (hysteresis blocks the flip)",
          abs(d.target_x - 50.0) < 1e-9, f"got {d.target_x}")

    # Frame 3: width X=51.0 (> 0.25 m from spacing's 50.0) -> hands over.
    world.frame_count = 3
    wpb.begin_frame(world, [d])
    _propose(world, d, "spacing", 50, tx=50.0)
    _propose(world, d, "width", 50, tx=51.0)
    wpb.resolve(world, [d], tuners)
    check("7.3 frame3 genuinely different target hands over to width",
          abs(d.target_x - 51.0) < 1e-9, f"got {d.target_x}")


# ── 8. PIPELINE INTEGRATION ──────────────────────────────────────────

def test_pipeline_wiring():
    print("\n[7] pipeline wiring + flag presence")
    import inspect
    from game.managers import defence_manager as dm
    from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners

    src = inspect.getsource(dm.DefenceManager.update_open_play)
    check("7.1 begin_frame wired", "_wpb_begin(world, defenders)" in src)
    check("7.2 capture wraps behaviour", '_wpb_capture(world, defenders, "behaviour")' in src)
    check("7.3 capture wraps seed_hold", '_wpb_capture(world, defenders, "seed_hold")' in src)
    check("7.4 resolve wired before integration",
          "_wpb_resolve(world, defenders, self.tuners, ope=ope)" in src
          and src.index("_wpb_resolve") < src.index("# ---- Apply movement ----"))
    tuners = DefenceTuners()
    check("7.5 tuner flag present, default True",
          getattr(tuners, "writer_priority_bands_enabled", None) is True)


# ── MAIN ─────────────────────────────────────────────────────────────

def main() -> int:
    print("=" * 68)
    print("WRITER PRIORITY BANDS — lateral 0.3 m (Y) / depth 0.5 m (X)")
    print("=" * 68)
    test_lateral_band()
    test_depth_band()
    test_strongest_and_axis_independence()
    test_exempt()
    test_gate()
    test_determinism_and_bands()
    test_winner_hysteresis()
    test_pipeline_wiring()
    print("\n" + "=" * 68)
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}):")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
