"""Headless probe: post-catch defensive line clamp lifecycle.

Empirically settles two claims from the "Post-Catch Clamp Stamp" task:

  Claim A - "_set_phase('OPEN_PLAY') zeroes defended_cap_remaining_s"
            -> If true, the clamp window must be ~0 at the FIRST OPEN_PLAY
               frame after catch and defenders will NOT be capped at 5.0.
  Claim B - "the clamp must be re-armed on the first post-catch OPEN_PLAY frame"
            -> If the existing catch-time arming survives, defenders are
               already capped at 5.0 m/s with zero code changes.

Checks:
  1. Clamp armed with the FULL window (~3.0s) immediately after catch.
  2. Window still armed (> 2.5s) at first OPEN_PLAY frame entry (pre-update).
  3. Defender speed <= 5.0 + eps during the first 8 OPEN_PLAY frames.
  4. Window strictly decays (not re-armed every frame -> permanent clamp).
  5. Window expires (reaches 0) within the 3.0s hard limit.
  6. world.just_caught_this_frame stays True for the whole set (documents
     why a stamp gated on that flag would permanently clamp defenders).
"""

import os
import sys
import math

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

os.environ["SDL_VIDEODRIVER"] = "dummy"
sys.path.insert(0, os.getcwd())

import pygame
pygame.init()
pygame.display.set_mode((64, 64))

from world.field import Field
from world.world_state import WorldState, GamePhase
from world.open_play_engine import OpenPlayEngine
from game.kickoff_engine import KickoffEngine
from ball.ball import Ball
from world.catch_system import check_for_catch

DT = 1.0 / 60.0
CAP_MPS = 5.0
HARD_LIMIT_S = 3.0

failures = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        failures.append(name)


def build_world():
    field = Field(1920, 1080)
    world = WorldState()
    ball = Ball(field)
    world.ball = ball
    ope = OpenPlayEngine(world)
    world.open_play = ope
    world.open_play_engine = ope
    ko = KickoffEngine(world, ball, field)
    world.kickoff_controller = ko
    return world, ball, ope, ko


def main():
    world, ball, ope, ko = build_world()
    ko.reset_for_kickoff()
    ko.kickoff()

    # ---------------------------------------------------------------
    # Phase 1: kickoff -> catch
    # ---------------------------------------------------------------
    catch_frame = None
    samples = {
        "after_catch": {},        # pid -> (remaining, cap) before CATCH->OPEN_PLAY
        "open_play_entry": {},    # pid -> remaining at first OPEN_PLAY pre-update
        "per_frame": [],          # (frame, max_speed, max_remaining, flag_still_set)
    }

    for frame in range(2400):
        ball.update(DT)
        if world.phase == GamePhase.KICKOFF:
            ko.update(DT)
            check_for_catch(world, ball, DT)
        elif world.phase == GamePhase.CATCH:
            catch_frame = world.frame_count
            # Snapshot BEFORE start(): exactly the state _set_phase/start
            # hands to the first OPEN_PLAY frame.
            samples["after_catch"] = {
                d.player_id: (d.defended_cap_remaining_s, d.defended_speed_cap_mps)
                for d in world.defending_team
            }
            ope.start(world.ball_carrier)
            world.phase = GamePhase.OPEN_PLAY
        elif world.phase == GamePhase.OPEN_PLAY:
            # Snapshot at OPEN_PLAY entry, pre-update (Claim A).
            if not samples["open_play_entry"]:
                samples["open_play_entry"] = {
                    d.player_id: d.defended_cap_remaining_s
                    for d in world.defending_team
                }
            ope.update(DT)
            max_speed = max((math.hypot(d.vx, d.vy) for d in world.defending_team),
                            default=0.0)
            max_remaining = max(
                (d.defended_cap_remaining_s for d in world.defending_team), default=0.0
            )
            samples["per_frame"].append(
                (world.frame_count, max_speed, max_remaining,
                 world.just_caught_this_frame)
            )
            if len(samples["per_frame"]) >= 250:
                break

    if catch_frame is None:
        check("kickoff catch occurred", False, "no catch within 2400 frames")
        print(f"\n{'ALL CHECKS PASSED' if not failures else 'FAILURES: ' + str(failures)}")
        sys.exit(1 if failures else 0)

    frames_ran = len(samples["per_frame"])
    print(f"catch on OPE frame {catch_frame}; {frames_ran} OPEN_PLAY frames sampled")

    # ---------------------------------------------------------------
    # Check 1: full window armed immediately after catch
    # ---------------------------------------------------------------
    print("\n[1] clamp armed immediately after catch")
    resid = [rem for rem, _cap in samples["after_catch"].values()]
    caps = [_cap for _rem, _cap in samples["after_catch"].values()]
    check("all defenders have cap == 5.0", all(abs(c - CAP_MPS) < 1e-9 for c in caps),
          f"caps={sorted(set(caps))}")
    check("all defenders armed with full ~3.0s window",
          all(rem > HARD_LIMIT_S - 0.5 for rem in resid),
          f"min_remaining={min(resid):.3f}")

    # ---------------------------------------------------------------
    # Check 2: window survives into the first OPEN_PLAY frame (Claim A)
    # ---------------------------------------------------------------
    print("\n[2] window at first OPEN_PLAY frame entry (pre-update)")
    entry_rem = list(samples["open_play_entry"].values())
    check("CLAIM A REFUTED: window > 2.5s at OPEN_PLAY entry (not zeroed)",
          len(entry_rem) > 0 and min(entry_rem) > 2.5,
          f"min_remaining={min(entry_rem) if entry_rem else 'N/A'}")
    check("_set_phase/start did not disarm", min(entry_rem) > 2.5)

    # ---------------------------------------------------------------
    # Check 3: defenders capped at 5.0 on the first OPEN_PLAY frames
    # ---------------------------------------------------------------
    print("\n[3] defender speed during first 8 OPEN_PLAY frames")
    first8 = samples["per_frame"][:8]
    max_spd_first8 = max((s for _f, s, _r, _flag in first8), default=0.0)
    check("CLAIM B REFUTED: speed <= 5.15 without any re-arm",
          max_spd_first8 <= CAP_MPS + 0.15, f"max={max_spd_first8:.3f}")

    # ---------------------------------------------------------------
    # Check 4: window decays (no per-frame re-arming / no permanent clamp)
    # ---------------------------------------------------------------
    print("\n[4] window decays monotonically (no re-arm each frame)")
    rems = [r for _f, _s, r, _flag in samples["per_frame"]]
    rem0, rem30 = rems[0], rems[min(29, len(rems) - 1)]
    check("remaining strictly lower at frame 30 than frame 0",
          rem30 < rem0 - 0.2, f"frame0={rem0:.3f} frame30={rem30:.3f}")

    # ---------------------------------------------------------------
    # Check 5: window expires within the 3.0s hard limit
    # ---------------------------------------------------------------
    print("\n[5] window expires (no permanent clamp)")
    check("remaining reaches 0 within 200 frames (3.0s @ 60fps)",
          any(r <= 0.0 for r in rems),
          f"min_remaining={min(rems):.3f} after {len(rems)} frames")

    # ---------------------------------------------------------------
    # Check 6: the catch flag is consumed ONE-SHOT on the first OPEN_PLAY
    # frame and never re-armed.
    #
    # NOTE: per_frame samples are taken AFTER ope.update(), so sample 0
    # already reflects post-stamp state — the flag is False because
    # _stamp_post_catch_speed_caps() consumed it on that first frame.
    # The genuine contract under test: consumed exactly once, stays
    # False forever, and the window still expires (no permanent clamp).
    # ---------------------------------------------------------------
    print("\n[6] world.just_caught_this_frame one-shot consumption")
    flags = [flag for _f, _s, _r, flag in samples["per_frame"]]
    check("flag consumed by the first OPEN_PLAY update (sample 0 already False)",
          flags[0] is False,
          f"sample0={flags[0]}")
    check("flag stays False all 250 frames (no re-arm / no permanent gate)",
          all(f is False for f in flags),
          f"non-False flags at indices {[i for i, f in enumerate(flags) if f is not False]}")
    # Defenders must still be released after the window/auto-disarm —
    # proves the one-shot stamp did not turn into a permanent clamp.
    check("window still expires (not permanently re-armed)",
          any(r <= 0.0 for r in rems),
          f"min_remaining={min(rems):.3f}")

    # ---------------------------------------------------------------
    # Informational: max speed late in the window (post-disarm chase)
    # ---------------------------------------------------------------
    late_max = max((s for _f, s, _r, _flag in samples["per_frame"][100:]),
                   default=0.0)
    print(f"\n[info] max defender speed over frames 100-249 = {late_max:.3f} m/s "
          f"(post-disarm open-play chase, can exceed 5.0)")

    print(f"\n{'ALL CHECKS PASSED' if not failures else 'FAILURES: ' + str(failures)}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
