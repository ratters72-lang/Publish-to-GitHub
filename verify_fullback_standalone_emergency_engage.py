"""Verify lane-first, standalone fullback emergency engage selection."""

from types import SimpleNamespace

from game.defence.defence_lvl_1 import (
    defensive_behaviour_lvl1 as lvl1,
    fullback_role as fb_role,
    pretackle_classifier as classifier,
)
from game.defence.defence_lvl_1.defensive_behaviour_unified import (
    _try_primary_then_fullback,
)


def main() -> int:
    lane_primary = SimpleNamespace(tackle_role="PRIMARY")
    lane_secondary = SimpleNamespace(tackle_role="SECONDARY")
    emergency_fb = SimpleNamespace(fullback_role_active=True)
    world = SimpleNamespace(tes_active=False)
    calls = []
    original_select = classifier.select_primary_candidate
    original_try = lvl1.try_pre_tackle
    original_authority = fb_role.fullback_emergency_engage_authority_active

    def select_candidate(_world, defenders, _bc):
        defenders = list(defenders or ())
        if lane_primary in defenders and lane_secondary not in defenders:
            return lane_primary
        if lane_secondary in defenders:
            return lane_secondary
        return None

    classifier.select_primary_candidate = select_candidate
    fb_role.fullback_emergency_engage_authority_active = lambda *_: True
    try:
        lvl1.try_pre_tackle = lambda _w, d, _bc, _t: calls.append(d) or d is lane_primary
        winner = _try_primary_then_fullback(
            world, [lane_primary, lane_secondary], [emergency_fb],
            object(), object(),
        )
        if winner is not lane_primary or calls != [lane_primary]:
            print("FAIL: successful lane PRIMARY did not win alone")
            return 1

        calls.clear()
        world.tes_active = False

        def lane_started_tes_then_declined(_w, defender, _bc, _tuners):
            calls.append(defender)
            if defender is lane_primary:
                world.tes_active = True
            return False

        lvl1.try_pre_tackle = lane_started_tes_then_declined
        winner = _try_primary_then_fullback(
            world, [lane_primary, lane_secondary], [emergency_fb],
            object(), object(),
        )
        if winner is not None or calls != [lane_primary]:
            print("FAIL: FB fallback ran after lane attempt activated TES")
            return 1

        calls.clear()
        world.tes_active = False
        lvl1.try_pre_tackle = lambda _w, d, _bc, _t: calls.append(d) or d is emergency_fb
        winner = _try_primary_then_fullback(
            world, [lane_primary, lane_secondary], [emergency_fb],
            object(), object(),
        )
        if (
            winner is not emergency_fb
            or calls != [lane_primary, lane_secondary, emergency_fb]
        ):
            print("FAIL: FB was not attempted only after lane PRIMARY failed")
            return 1

        calls.clear()
        world.tes_active = True
        winner = _try_primary_then_fullback(
            world, [lane_primary], [emergency_fb], object(), object(),
        )
        if winner is not None or calls:
            print("FAIL: a second tackle candidate ran while TES was active")
            return 1
    finally:
        classifier.select_primary_candidate = original_select
        lvl1.try_pre_tackle = original_try
        fb_role.fullback_emergency_engage_authority_active = original_authority

    print("PASS: lane PRIMARY wins first; FB is sole fallback; TES blocks repeats")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
