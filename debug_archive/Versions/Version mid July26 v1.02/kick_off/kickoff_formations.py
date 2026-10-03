"""Kickoff formation positions — NRL-legal starting positions.

FIELD: x ∈ [0, 120], y ∈ [0, 70]
  Deadball L=0  Tryline L=10  Halfway=60  Tryline R=110  Deadball R=120
  Top sideline y=0  Centre y=35  Bottom sideline y=70

KICKING_TEAM: behind the ball on halfway — since the kick goes LEFT,
  "behind" means RIGHT of halfway (x > 60).
RECEIVING_TEAM: on the LEFT side where the ball lands (x < 60).

This module owns ALL kickoff placement logic. No other module
should hardcode kickoff positions.
"""

from player.player import DefenderRole


def apply_kicking_team_formation(world, field):
    """
    Place the kicking team 2m behind halfway in a single flat line.

    Ball is at x=60 (halfway).  Kick goes LEFT (toward x=0).
    Kicking team stands in a flat line at x = halfway + 2 = 62m,
    spread evenly across the field width.

    All players are FROZEN until on_ball_kicked() unfreezes them.
    """
    team = world.kicking_team
    if not team:
        return

    line_x = field.halfway_m + 2   # 62m = 2m behind halfway
    n = len(team)
    spacing_y = field.width_m / (n + 1)   # 70/14 = 5.0m

    for i, p in enumerate(team):
        p.x = line_x
        p.y = spacing_y * (i + 1)

        p.target_x = p.x
        p.target_y = p.y
        p.defender_role = DefenderRole.LINE
        p.frozen = True


def apply_receiving_kickoff_formation(world, field):
    """
    Place the receiving team in a 3‑group reception formation.

    Line 1 — 2m in from left deadball (x=2), 5 players evenly spaced
    between y=5 and y=45:
        A1 (2.0, 5.0)  A2 (2.0, 15.0)  A3 (2.0, 25.0)  A4 (2.0, 35.0)  A5 (2.0, 45.0)

    Line 2 — 12m line (x=12), 4 players evenly spaced laterally
    across y ∈ [8, 62] (two each side of centre):
        B1 (12.0, 8.0)  B2 (12.0, 26.0)  B3 (12.0, 44.0)  B4 (12.0, 62.0)

    Line 3 — 35m line (x=35), 4 players evenly spaced laterally
    across y ∈ [6, 64]:
        C1 (35.0, 6.0)  C2 (35.0, 25.3)  C3 (35.0, 44.7)  C4 (35.0, 64.0)

    All players FROZEN until on_ball_kicked() unfreezes them.
    """
    team = world.receiving_team
    if not team:
        return

    group_a = [(2.0, 5.0), (2.0, 15.0), (2.0, 25.0), (2.0, 35.0), (2.0, 45.0)]
    group_b = [(12.0, 8.0), (12.0, 26.0), (12.0, 44.0), (12.0, 62.0)]
    group_c = [(35.0, 6.0), (35.0, 25.3), (35.0, 44.7), (35.0, 64.0)]

    formation = group_a + group_b + group_c

    for p, (tx, ty) in zip(team, formation):
        p.x = tx
        p.y = ty
        p.target_x = tx
        p.target_y = ty
        p.defender_role = DefenderRole.LINE
        p.frozen = True
