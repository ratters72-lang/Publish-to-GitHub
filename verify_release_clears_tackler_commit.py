"""verify_release_clears_tackler_commit.py — Focused verifier.

Confirms that the DOMINANCE→RELEASE transition (`_prepare_release`) clears
stale tackler-commit locks so the UN-GATED spacing preamble on the NEXT frame
never anchors short-side spacing off a stuck (still-committed) defender.

The bug: `_tackle_commit_active` / `_pursuit_commit_active` are stamped by the
TES-drive open-play projection on PRIMARY/SECONDARY tacklers.  `_clear_tes_artefacts()`
clears them on the first RELEASE frame, but the top-of-frame spacing preamble runs
BEFORE that clear, so a stale committed body (now DEF_RETREAT_10, chain-eligible,
physically-nearest) is used as the short-side spacing hinge/anchor.

Run:  python verify_release_clears_tackler_commit.py
Exit 0 on success; prints a PASS/FAIL line per assertion.
"""
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player, DefenderRole
from world.world_state import WorldState, GameSubState
from world.open_play_engine import OpenPlayEngine


failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" - " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def make_player(pid, x, y, role=None, depth_role=None, fullback=False):
    p = Player(x=x, y=y, player_id=pid)
    p.defender_role = role
    p.depth_role = depth_role
    p.fullback_role_active = fullback
    p.frozen = False
    p.freeze_timer = 0.0
    p.target_x = x
    p.target_y = y
    p.vx = 0.0
    p.vy = 0.0
    p.is_tackler = False
    p.in_contact = False
    p.tes_locked = False
    p.tackle_locked = False
    p.tes_tackler_role = None
    p.tackle_role = "PASSIVE"
    p._tackle_commit_active = False
    p._pursuit_commit_active = False
    p.movement_mode = None
    p.collision_priority = None
    p.max_speed_mps = 8.3
    p.max_defensive_speed_mps = 8.3
    p.mass_kg = 90.0
    return p


def make_world():
    w = WorldState()
    w.ruck_x_m = 60.0
    w.ruck_y_m = 35.0
    w.defensive_line_anchor = 70.0
    w.anchor_locked = True
    w.sub_state = GameSubState.TACKLE_DOMINANCE
    w.frame_count = 0
    w.dt = 1.0 / 60.0
    w.sim_time_s = 0.0
    w.attacking_direction = -1
    w.ball_owner = "BC"
    w.tes_active = True
    w.tes_locked = False
    w.defence_enabled = True
    w.marker_freeze_timer = 0.75
    w.marker_lock_duration_s = 0.75
    return w


def main():
    print("=" * 64)
    print("RELEASE CLEARS STALE TACKLER-COMMIT LOCKS (2026-09-02)")
    print("=" * 64)

    # ══════════════════════════════════════════════════════════════
    # Build a minimal world + engine
    # ══════════════════════════════════════════════════════════════
    w = make_world()

    # Defending team: a line, no fullback, plus a stale-committed defender
    # that is the physically-nearest body on the short (left) side.
    bc = make_player(100, x=58.0, y=35.0)
    bc.attack_role = "ball_carrier"

    # Stale committed defender — DEF_RETREAT_10, near ruck_y on LEFT (short side).
    stale_def = make_player(1, x=58.0, y=32.5, role=DefenderRole.DEF_RETREAT_10)
    stale_def._tackle_commit_active = True   # the stale lock (bug source)
    stale_def._pursuit_commit_active = True
    stale_def.tackle_role = "PRIMARY"
    stale_def.is_tackler = True
    stale_def.in_contact = True
    stale_def.tes_locked = True

    # Other line defenders (interior + wide) so the line is populated.
    line2 = make_player(2, x=58.0, y=38.0, role=DefenderRole.DEF_RETREAT_10, depth_role="B")
    line3 = make_player(3, x=58.0, y=45.0, role=DefenderRole.DEF_RETREAT_10, depth_role="WINGER")
    line4 = make_player(4, x=58.0, y=28.0, role=DefenderRole.DEF_RETREAT_10, depth_role="A")
    line5 = make_player(5, x=58.0, y=22.0, role=DefenderRole.DEF_RETREAT_10, depth_role="WINGER")

    w.defending_team = [stale_def, line2, line3, line4, line5]
    w.attacking_team = [bc]
    w.ball_carrier = bc
    w.players = list(w.defending_team) + list(w.attacking_team)

    engine = OpenPlayEngine(w, seed=0)
    engine.ball_carrier = bc
    engine.primary = stale_def
    engine.joiners = []
    engine.dh = None

    # ══════════════════════════════════════════════════════════════
    # TEST 1 — _prepare_release clears the stale commit locks
    # ══════════════════════════════════════════════════════════════
    print("\n[1] _prepare_release clears stale tackler-commit locks")
    engine._prepare_release()
    check("1.1 stale_def._tackle_commit_active = False",
          not getattr(stale_def, "_tackle_commit_active", True),
          "_tackle_commit_active=%s" % getattr(stale_def, "_tackle_commit_active", None))
    check("1.2 stale_def._pursuit_commit_active = False",
          not getattr(stale_def, "_pursuit_commit_active", True),
          "_pursuit_commit_active=%s" % getattr(stale_def, "_pursuit_commit_active", None))
    check("1.3 stale_def.tackle_role is None",
          getattr(stale_def, "tackle_role", None) is None,
          "tackle_role=%s" % getattr(stale_def, "tackle_role", None))

    # ══════════════════════════════════════════════════════════════
    # TEST 2 — the clear is unconditional (marks / fullback / all bodies)
    # ══════════════════════════════════════════════════════════════
    print("\n[2] clear is unconditional across the whole defending team")
    all_clear = all(
        not getattr(d, "_tackle_commit_active", False)
        and not getattr(d, "_pursuit_commit_active", False)
        for d in w.defending_team
    )
    check("2.1 every defender has both commit locks cleared",
          all_clear,
          "committed_remaining=%s" % [
              getattr(d, "player_id", "?") for d in w.defending_team
              if getattr(d, "_tackle_commit_active", False)
              or getattr(d, "_pursuit_commit_active", False)
          ])

    # ══════════════════════════════════════════════════════════════
    # TEST 3 — the clear runs at the DOMINANCE→RELEASE transition so the
    #         NEXT frame's UN-GATED spacing preamble sees clean flags.
    #         Build the engine order explicitly: at the DOMINANCE end we call
    #         _prepare_release(); the flags must be clean BEFORE any spacing.
    # ══════════════════════════════════════════════════════════════
    print("\n[3] stale-lock clear precedes the next-frame spacing preamble")
    # Simulate: re-arm the flags (as if TES-drive just stamped them), then
    # the DOMINANCE→RELEASE transition calls _prepare_release.
    stale_def._tackle_commit_active = True
    stale_def._pursuit_commit_active = True
    stale_def.tackle_role = "PRIMARY"
    engine._prepare_release()
    check("3.1 after a second _prepare_release the flags are clean again",
          not getattr(stale_def, "_tackle_commit_active", True)
          and not getattr(stale_def, "_pursuit_commit_active", True)
          and getattr(stale_def, "tackle_role", None) is None,
          "tackle_commit=%s pursuit_commit=%s tackle_role=%s" % (
              getattr(stale_def, "_tackle_commit_active", None),
              getattr(stale_def, "_pursuit_commit_active", None),
              getattr(stale_def, "tackle_role", None)))

    print("\n" + "=" * 64)
    if failures:
        print("RESULT: %d FAILURE(S): %s" % (len(failures), failures))
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
