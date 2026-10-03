"""Kickoff formation positions — NRL-legal starting positions.

FIELD: x ∈ [0, 120], y ∈ [0, 70]
  Deadball L=0  Tryline L=10  Halfway=60  Tryline R=110  Deadball R=120
  Top sideline y=0  Centre y=35  Bottom sideline y=70

KICKING_TEAM: behind the ball on halfway — since the kick goes LEFT,
  "behind" means RIGHT of halfway (x > 60).
RECEIVING_TEAM: on the LEFT side where the ball lands (x < 60).

This module owns ALL kickoff placement logic. No other module
should hardcode kickoff positions.

KICKOFF FB OVERRIDE (2026-08-11):
  - The flat kicking line sits at formation_line_x = halfway + 2.0 (62 m).
  - The kicking team fields 13: jersey 1 is the designated fullback,
    placed at (formation_line_x, centre + side_sign * 3.0) — the FB stays
    ON the flat line, offset 3 m toward the kick side.
  - The other 12 (kicker + 11) stand ON the flat line: kicker at the
    centre slot, 5 defenders on the SHORT side and 6 on the KICK side
    (the side indicated by side_sign), spaced 5 m apart from centre.
  - side_sign = sign(target_y - centre); +1 means the kick lands toward
    +Y (bottom sideline).  Defaults to +1 when target_y == centre.
  - Assignments are deterministic: driven by jersey_number (stable
    identity), never by list order.

RECEIVING KICKOFF LAYOUT (finalized custom NRL reception grid):
  The receiving team is placed by the SAME stable identity — its
  permanent jersey_number (1-13) — never by list order.  Every slot is a
  HARDCODED ABSOLUTE field coordinate measured from the dead-ball line
  (X=0) and the top sideline (Y=0):
    X : dead-ball line = 0, receiving tryline = 10, halfway = 60
    Y : top sideline  = 0, centre = 35, bottom sideline = 70
  See RECEIVING_KICKOFF_LAYOUT / apply_receiving_kickoff_formation() for
  the exact grid (deep in-goal rams, the try-line wall & sweeper, the
  25 m edge covers, and the advanced 4-man screen).  The permanent
  ``hemisphere`` attribute is still stamped from POSITION_HEMISPHERE_MAP
  for downstream consumers, but placement itself is absolute.
"""

from player.player import DefenderRole

# Kicking team stands this far behind halfway (metres).  Sole source of
# truth for the formation line X; the kickoff fullback occupies the SAME
# line (no separate depth offset).
# 2026-09-09: 5.0 -> 2.0.  The 5 m line sat the future defenders too deep,
# so the spacing/pursuit writers fought on the first OPEN_PLAY frames before
# the Stage-11 no-tackle/no-defence gate stabilised.  2 m keeps them strictly
# behind the ball but close enough that the chase closes sooner.
KICKING_LINE_OFFSET_M: float = 2.0

# Line defenders (non-kicker, non-FB) stand 1 m further back from the
# kicker/FB flat line so the PRE_KICK rush does not hit the halfway-line
# stop-and-go before the kick fires.  The kicker must be at the ball and
# the FB placement is a verified contract, so only the 11 line defenders
# get this extra runway.
KICKOFF_LINE_DEFENDER_BACK_OFFSET_M: float = 1.0

# KICKOFF ROLE JERSEYS (2026-08-11) — deterministic identity, never list
# order.  Jersey 1 = first defender created (player #200) = the kickoff
# fullback.  Jersey 13 = last defender = the designated kicker (centre
# slot on the flat line).
KICKOFF_FB_JERSEY: int = 1
KICKER_JERSEY_NUMBER: int = 13

# KICKOFF FB LATERAL OFFSET (2026-08-11): the fullback stands 3 m from
# field centre toward the kick side.
KICKOFF_FB_LATERAL_OFFSET_M: float = 3.0

# Flat-line lateral spacing: the 12 line players are spread 5 m apart
# from centre (6 per side would span 5..65 m — inside the [0, 70]
# sidelines with margin).
KICKOFF_LINE_SPACING_M: float = 5.0

# Sideline margin guard: formation Y must stay inside [margin, 70-margin]
# so a sideline-clamped side never spills off the field.
SIDELINE_MARGIN_M: float = 5.0

# ── RECEIVING KICKOFF LAYOUT (finalized custom NRL reception grid) ─────
# jersey_number -> (x, y), ABSOLUTE field coordinates measured from the
# dead-ball line (X=0) and the TOP sideline (Y=0):
#   X : dead-ball line = 0, receiving tryline = 10, halfway = 60
#   Y : top sideline  = 0, centre = 35, bottom sideline = 70
#
# 1. DEEP IN-GOAL RAMS & RETRIEVERS (X = 1.0 .. 5.0)
#   #8  left prop    : (1.0, 15.0)   15 m from the top sideline
#   #10 right prop   : (1.0, 55.0)   15 m from the bottom sideline
#   #6  five-eighth  : (5.0, 25.0)   25 m from the top sideline
#   #7  halfback     : (5.0, 45.0)   25 m from the bottom sideline
#
# 2. THE TRY-LINE WALL & SWEEPER (X = 10.0 .. 15.0)
#   #2  left winger  : (10.0,  5.0)  5 m from the top touchline
#   #5  right winger : (10.0, 65.0)  5 m from the bottom touchline
#   #1  fullback     : (15.0, 35.0)  midfield, dead centre
#
# 3. THE 25 m EDGE COVERS (X = 25.0)
#   #11 left second row  : (25.0,  5.0)  5 m from the top sideline
#   #12 right second row : (25.0, 65.0)  5 m from the bottom sideline
#
# 4. THE ADVANCED 4-MAN SCREEN (exactly at X = 45.0)
#   #3  left centre  : (45.0,  5.0)  5 m from the top sideline
#   #9  hooker       : (45.0, 25.0)  25 m from the top sideline
#   #13 lock         : (45.0, 45.0)  25 m from the bottom sideline
#   #4  right centre : (45.0, 65.0)  5 m from the bottom sideline
RECEIVING_KICKOFF_LAYOUT: dict[int, tuple[float, float]] = {
    # 1. Deep in-goal rams & retrievers (X = 1.0 .. 5.0)
    8:  (1.0, 15.0),
    10: (1.0, 55.0),
    6:  (5.0, 25.0),
    7:  (5.0, 45.0),
    # 2. The try-line wall & sweeper (X = 10.0 .. 15.0)
    2:  (10.0, 5.0),
    5:  (10.0, 65.0),
    1:  (15.0, 35.0),
    # 3. The 25 m edge covers (X = 25.0)
    11: (25.0, 5.0),
    12: (25.0, 65.0),
    # 4. The advanced 4-man screen (exactly at X = 45.0)
    3:  (45.0, 5.0),
    9:  (45.0, 25.0),
    13: (45.0, 45.0),
    4:  (45.0, 65.0),
}


def _clamp_line_y(y: float, field) -> float:
    """Clamp a flat-line Y inside the legal playing width with margin.

    The field's centre_m is 35 and width_m is 70, so the legal band is
    [0, 70]; we additionally honour SIDELINE_MARGIN_M so extreme side
    biases never place a defender on the touchline.
    """
    low = SIDELINE_MARGIN_M
    high = field.width_m - SIDELINE_MARGIN_M
    return max(low, min(high, y))


def apply_kicking_team_formation(world, field, formation_line_x=None, side_sign=1):
    """
    Place the kicking team on a flat line ``formation_line_x`` metres.

    Ball is at x=60 (halfway); the kick goes LEFT (toward x=0).  The
    kicking team stands in a flat line at ``formation_line_x`` (default
    halfway + KICKING_LINE_OFFSET_M = 62 m) with:

      - jersey 1 (KICKOFF_FB_JERSEY) at
        (formation_line_x, centre + side_sign * KICKOFF_FB_LATERAL_OFFSET_M)
      - jersey 13 (KICKER_JERSEY_NUMBER) — the kicker — at the CENTRE
        slot (formation_line_x, centre)
      - the remaining 11 line defenders split 5 short-side / 6 kick-side
        around the kicker at KICKOFF_LINE_SPACING_M spacing, counted
        outward from centre in stable jersey order, and sit 1 m further
        back (formation_line_x + KICKOFF_LINE_DEFENDER_BACK_OFFSET_M) so
        the pre-kick rush has runway before the halfway line

    All players are FROZEN until on_ball_kicked() unfreezes them.
    Stamps world.kickoff_kicker_id and world.kickoff_side_sign so the FB
    assigner, the audit, and the verifiers share one deterministic
    geometry.
    """
    team = world.kicking_team
    if not team:
        return

    f = field
    if formation_line_x is None:
        formation_line_x = f.halfway_m + KICKING_LINE_OFFSET_M

    # Normalise the side sign: +1 = kick toward +Y (bottom sideline),
    # -1 = toward -Y (top sideline).  Default +1 on centre.
    side = 1 if side_sign >= 0 else -1
    world.kickoff_side_sign = side

    centre_y = f.centre_m
    fb_y = _clamp_line_y(
        centre_y + side * KICKOFF_FB_LATERAL_OFFSET_M, f
    )
    kicker_y = centre_y

    # ── RESOLVE THE 12-MAN FLAT LINE (kicker + 11 others) ──
    # Stable identity ordering via jersey_number (never list order).
    # The kicker occupies the centre slot; from centre outward, the
    # SHORT side receives 5 defenders and the KICK side receives 6.
    n_kick_side = 6
    n_short_side = 5
    kick_side_jerseys = []
    short_side_jerseys = []
    kicker_actor = None
    fb_actor = None
    for p in team:
        jersey = getattr(p, "jersey_number", 0)
        if jersey == KICKER_JERSEY_NUMBER:
            kicker_actor = p
        elif jersey == KICKOFF_FB_JERSEY:
            fb_actor = p
        elif len(kick_side_jerseys) < n_kick_side:
            kick_side_jerseys.append(p)
        else:
            short_side_jerseys.append(p)

    # ── APPLY POSITIONS ──
    # Fullback (jersey 1): on the flat line, 3 m toward the kick side.
    if fb_actor is not None:
        fb_actor.x = formation_line_x
        fb_actor.y = fb_y
        fb_actor.target_x = fb_actor.x
        fb_actor.target_y = fb_actor.y
        fb_actor.defender_role = DefenderRole.LINE
        fb_actor.frozen = True

    # Kicker: centre slot.
    if kicker_actor is not None:
        kicker_actor.is_kicker = True
        kicker_actor.x = formation_line_x
        kicker_actor.y = kicker_y
        kicker_actor.target_x = kicker_actor.x
        kicker_actor.target_y = kicker_actor.y
        kicker_actor.defender_role = DefenderRole.LINE
        kicker_actor.frozen = True
        world.kickoff_kicker_id = kicker_actor.player_id

    # Line defenders: 5 short side (opposite side_sign) at slots
    # 1 .. 5 counting outward; 6 kick side at slots 1 .. 6.
    # Line defenders sit 1 m further back than the kicker/FB flat line so
    # the PRE_KICK rush has runway before the halfway-line stop-and-go.
    line_defender_x = formation_line_x + KICKOFF_LINE_DEFENDER_BACK_OFFSET_M
    for slot, p in enumerate(short_side_jerseys, start=1):
        slot_y = _clamp_line_y(
            centre_y - side * slot * KICKOFF_LINE_SPACING_M, f
        )
        p.x = line_defender_x
        p.y = slot_y
        p.target_x = p.x
        p.target_y = p.y
        p.defender_role = DefenderRole.LINE
        p.frozen = True

    for slot, p in enumerate(kick_side_jerseys, start=1):
        slot_y = _clamp_line_y(
            centre_y + side * slot * KICKOFF_LINE_SPACING_M, f
        )
        p.x = line_defender_x
        p.y = slot_y
        p.target_x = p.x
        p.target_y = p.y
        p.defender_role = DefenderRole.LINE
        p.frozen = True


def apply_receiving_kickoff_formation(world, field):
    """Place the receiving team on the finalized custom NRL reception grid.

    Every body is placed by its PERMANENT jersey_number (1-13), never by
    list order, so the formation is deterministic regardless of roster
    order.  Every slot is a HARDCODED ABSOLUTE field coordinate measured
    from the dead-ball line (X=0) and the TOP sideline (Y=0):

      X : dead-ball line = 0, receiving tryline = 10, halfway = 60
      Y : top sideline  = 0, centre = 35, bottom sideline = 70

      1. DEEP IN-GOAL RAMS & RETRIEVERS (X = 1.0 .. 5.0)
         #8  left prop    (1.0, 15.0)   15 m from the top sideline
         #10 right prop   (1.0, 55.0)   15 m from the bottom sideline
         #6  five-eighth  (5.0, 25.0)   25 m from the top sideline
         #7  halfback     (5.0, 45.0)   25 m from the bottom sideline

      2. THE TRY-LINE WALL & SWEEPER (X = 10.0 .. 15.0)
         #2  left winger  (10.0,  5.0)  5 m from the top touchline
         #5  right winger (10.0, 65.0)  5 m from the bottom touchline
         #1  fullback     (15.0, 35.0)  midfield, dead centre

      3. THE 25 m EDGE COVERS (X = 25.0)
         #11 left second row  (25.0,  5.0)  5 m from the top sideline
         #12 right second row (25.0, 65.0)  5 m from the bottom sideline

      4. THE ADVANCED 4-MAN SCREEN (exactly at X = 45.0)
         #3  left centre  (45.0,  5.0)  5 m from the top sideline
         #9  hooker       (45.0, 25.0)  25 m from the top sideline
         #13 lock         (45.0, 45.0)  25 m from the bottom sideline
         #4  right centre (45.0, 65.0)  5 m from the bottom sideline

    The permanent ``hemisphere`` attribute is still stamped here from
    POSITION_HEMISPHERE_MAP (the static registry) for downstream
    consumers, but placement itself is absolute and roster-order
    independent.  All players are FROZEN until on_ball_kicked() unfreezes
    them.
    """
    team = world.receiving_team
    if not team:
        return

    # LAZY IMPORT (no module-load cycle): the static hemisphere registry
    # lives in the OPE; importing it here only runs at call time, long
    # after every module has finished loading.
    from world.open_play_engine import POSITION_HEMISPHERE_MAP

    for p in team:
        jersey = getattr(p, "jersey_number", 0)

        # Stamp the permanent hemisphere from the static registry so the
        # attribute exists for every receiving body.
        if getattr(p, "hemisphere", None) is None:
            p.hemisphere = POSITION_HEMISPHERE_MAP.get(jersey)

        spec = RECEIVING_KICKOFF_LAYOUT.get(jersey)
        if spec is None:
            # Unknown jersey: leave the body where it stands (defensive).
            tx, ty = p.x, p.y
        else:
            tx, ty = spec

        p.x = float(tx)
        p.y = float(ty)
        p.target_x = p.x
        p.target_y = p.y
        p.defender_role = DefenderRole.LINE
        p.frozen = True
