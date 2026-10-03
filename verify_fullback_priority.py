"""
verify_fullback_priority.py — Validation for the designated-FB priority
+ central replacement band rules (2026-08-11).

JERSEY-1 PRIORITY:
  A. Jersey 1 (KICKOFF_FB_JERSEY) is the fullback EVERY frame when
     eligible and not the kicker — KICKOFF and OPEN_PLAY.  The distance
     gate is waived (survives the kickoff-catch anchor snap).
  B. When jersey 1 becomes a marker / tackler / frozen, the FB role hands
     to the DEEPEST CENTRAL defender (furthest +X inside y in [20, 50]).
  C. Sideline defenders (outside the central band) never become the
     fullback.
  D. When jersey 1 returns to eligibility (marker duty ends), it
     re-takes the role.
  E. Line-number integrity: the FB actor's depth_role is cleared (renders
     as FB, never a stale line role), and assign_depth_roles() keeps the
     actor out of the line lanes.

Exits 1 on any failed assertion (fails loudly, CI-style).
"""

import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player, DefenderRole
from game.defence.defence_lvl_1.fullback_role import (
    assign_fullback_role,
    KICKOFF_FB_JERSEY,
    FULLBACK_CENTRAL_BAND_M,
    FIELD_CENTRE_Y,
    _in_fallback_corridor as _in_band,
)
from game.defence.defence_lvl_1.defensive_depth_roles import assign_depth_roles

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(name)


class FakeBall:
    def __init__(self, x, y):
        self.x = x
        self.y = y


def make_players(n, base_x=65.0, base_y=20.0, gap_y=5.0):
    """Build n defenders.  Jersey 1 = players[0], others get jerseys 2..n."""
    players = []
    for i in range(n):
        p = Player(x=base_x, y=base_y + i * gap_y, player_id=200 + i)
        p.jersey_number = i + 1
        p.defender_role = DefenderRole.DEF_LINE_READY
        p.is_defender = True
        players.append(p)
    return players


def main():
    print("=" * 64)
    print("FULLBACK PRIORITY / CENTRAL BAND VALIDATION")
    print("=" * 64)

    # ── A. Jersey 1 wins OPEN_PLAY assignment (distance gate waived) ──
    print("\n[A] Jersey-1 persistent priority (OPEN_PLAY)")
    players = make_players(6, base_x=65.0, base_y=17.0, gap_y=5.0)
    # Jersey 1 sits on the flat line (65, 17); the post-catch anchor is far
    # away (70 + depth) — well beyond MAX_ANCHOR_DISTANCE_M raw distance
    # from jersey 1.  The priority must still pick jersey 1.
    actor, _ = assign_fullback_role(
        players, FakeBall(35.0, 35.0), 70.0,
        in_kickoff=False,
    )
    check(
        "jersey 1 selected as FB in OPEN_PLAY despite distance",
        actor is not None and getattr(actor, "jersey_number", None) == KICKOFF_FB_JERSEY,
        f"jersey={getattr(actor, 'jersey_number', None) if actor else None}",
    )

    # At most one active.
    n_active = sum(1 for p in players if getattr(p, "fullback_role_active", False))
    check("exactly one active fullback", n_active == 1, f"count={n_active}")

    # ── B. Jersey 1 unavailable → deepest central defender ──
    print("\n[B] Replacement = deepest CENTRAL defender")
    for p in players:
        p.fullback_role_active = False
        p.role = None
    players[0].defender_role = DefenderRole.MARKER_A  # jersey 1 = marker
    # Push a SIDELINE defender deepest-right (y = 62 — outside the band).
    sideline = players[5]
    sideline.x = 105.0
    sideline.y = 62.0
    # Deepest CENTRAL candidate: players[3] at (100, 32).
    players[3].x = 100.0
    players[3].y = 32.0
    actor2, _ = assign_fullback_role(
        players, FakeBall(35.0, 35.0), 70.0,
        in_kickoff=False,
    )
    check(
        "sideline defender never selected as FB",
        actor2 is not sideline,
        f"actor={actor2.player_id if actor2 else None}",
    )
    check(
        "deepest central defender selected",
        actor2 is players[3],
        f"actor={actor2.player_id if actor2 else None}",
    )

    # ── C. Corridor bound exactness ──
    print("\n[C] Central corridor (y in [20, 50])")
    p_ok = Player(x=70.0, y=FIELD_CENTRE_Y + FULLBACK_CENTRAL_BAND_M - 0.01, player_id=900)
    p_ok2 = Player(x=70.0, y=FIELD_CENTRE_Y - FULLBACK_CENTRAL_BAND_M + 0.01, player_id=901)
    p_out1 = Player(x=70.0, y=FIELD_CENTRE_Y + FULLBACK_CENTRAL_BAND_M + 0.2, player_id=902)
    p_out2 = Player(x=70.0, y=FIELD_CENTRE_Y - FULLBACK_CENTRAL_BAND_M - 0.2, player_id=903)
    check("inside band accepted", _in_band(p_ok) and _in_band(p_ok2))
    check("outside band rejected", not _in_band(p_out1) and not _in_band(p_out2))

    # ── D. Jersey 1 re-takes role when it becomes available again ──
    print("\n[D] Jersey 1 re-claims on availability")
    players2 = make_players(5, base_x=65.0, base_y=20.0, gap_y=5.0)
    players2[0].defender_role = DefenderRole.MARKER_A
    actor3a, _ = assign_fullback_role(
        players2, FakeBall(35.0, 35.0), 70.0,
        in_kickoff=False,
    )
    check(
        "replacement active while jersey 1 is marker",
        actor3a is not None and actor3a is not players2[0],
        f"actor={actor3a.player_id if actor3a else None}",
    )
    # Marker duty ends → DEF_LINE_READY → jersey 1 re-takes.
    players2[0].defender_role = DefenderRole.DEF_LINE_READY
    actor3b, _ = assign_fullback_role(
        players2, FakeBall(35.0, 35.0), 70.0,
        in_kickoff=False,
    )
    check(
        "jersey 1 re-claims FB after marker duty ends",
        actor3b is players2[0],
        f"actor={actor3b.player_id if actor3b else None}",
    )

    # ── E. FB depth_role hygiene + line-number integrity ──
    print("\n[E] FB role-depth hygiene")
    players3 = make_players(6, base_x=65.0, base_y=20.0, gap_y=5.0)
    players3[0].depth_role = "D"
    actor4, _ = assign_fullback_role(
        players3, FakeBall(35.0, 35.0), 70.0,
        in_kickoff=False,
    )
    check(
        "FB actor depth_role cleared (not stale D)",
        actor4 is not None and getattr(actor4, "depth_role", None) is None,
        f"depth_role={getattr(actor4, 'depth_role', None)}",
    )
    check(
        "FB renders as FB via depth_role_tag",
        actor4 is not None and actor4.depth_role_tag == "FB",
        f"depth_role_tag={actor4.depth_role_tag}",
    )
    check(
        "FB renders as FB via role_tag",
        actor4 is not None and actor4.role_tag == "FB",
        f"role_tag={actor4.role_tag}",
    )

    class FullbackWorld:
        ruck_x_m = 60.0
        ruck_y_m = 35.0

    assign_depth_roles(players3, FullbackWorld())
    fb_actor = next(p for p in players3 if getattr(p, "fullback_role_active", False))
    check(
        "FB excluded from depth-role lanes",
        getattr(fb_actor, "depth_role", None) is None,
        f"depth_role={getattr(fb_actor, 'depth_role', None)}",
    )
    line_roles = [getattr(p, "depth_role", None) for p in players3 if p is not fb_actor]
    check(
        "remaining 5 defenders all get line roles",
        all(r is not None for r in line_roles),
        str(line_roles),
    )

    print("\n" + "=" * 64)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
