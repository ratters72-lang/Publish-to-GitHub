import io
import os
import sys
import contextlib

sys.path.insert(0, os.getcwd())
import probe_goal_line_outcomes as p

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    mods = p._engine_imports()
    world, ball, ope, ko, col = p._boot_world(mods)
    ok = p._advance_to_open_play(world, ball, ope, ko, col, mods)
    world.ruck_x_m = p.RUCK_X_START
    world.ruck_y_m = p.RUCK_Y_MID
    try:
        from game.defence.defence_lvl_1.anchor_authority import (
            reset_anchor_authority,
        )
        reset_anchor_authority(world)
    except Exception as exc:
        print("reset failed", exc)
    start_x = p.RUCK_X_START + p.BC_START_OFFSET_M
    for _ in range(p.PRE_ATTACK_FRAMES):
        p._position_carrier(world, start_x, p.RUCK_Y_MID, ball)
        p._step_full(world, ball, ope, ko, col, mods)
    bc = world.ball_carrier

xs = []
ys = []
for d in (world.defending_team or []):
    xs.append(round(float(d.x), 2))
    ys.append(round(float(d.y), 2))
print("booted", ok, "phase", world.phase)
print("ruck", round(world.ruck_x_m, 2), round(world.ruck_y_m, 2),
      "anchor", round(p._anchor_x(world), 2))
print("bc", round(float(bc.x), 2), round(float(bc.y), 2))
print("def_x", sorted(xs))
print("def_y", sorted(ys))
gaps = sorted(round(p._closest_defender_gap(world, bc), 2) for _ in [0])
print("closest", gaps)
print("sub_state", getattr(world, "sub_state", None),
      "pursuit", getattr(world, "in_pursuit_mode", None),
      "tes", getattr(world, "tes_active", None))
print("def_line_width_m", getattr(world, "def_line_width_m", None))
print("n_defenders", len(world.defending_team or []))
print("defence_ran", getattr(world, "defence_ran_this_frame", None),
      "defence_enabled", getattr(world, "defence_enabled", None),
      "anchor_locked", getattr(world, "anchor_locked", None),
      "frames_since_ptb", getattr(world, "frames_since_ptb", None))
_dteam = world.defending_team or []
_ateam = getattr(world, "attacking_team", None) or []
_dids = {id(d) for d in _dteam}
_aids = {id(d) for d in _ateam}
print("teams: defending_n=%d attacking_n=%d shared=%d"
      % (len(_dteam), len(_ateam), len(_dids & _aids)))
print("bc_in_defending", id(bc) in _dids, "bc_in_attacking", id(bc) in _aids)
print("world.defending_team_is_players_first12",
      _ateam and all(d is world.players[i]
                     for i, d in enumerate(_dteam)
                     if i < len(world.players)))
print("ball_player_team", getattr(getattr(world, "ball", None),
                                  "possessed_by", None))
print("team_of_bc", getattr(bc, "team", None),
      "team_of_d201", getattr(_dteam[0], "team", None) if _dteam else None)
print("rows: pid role x y tx ty state src frozen ftimer locked "
      "move_speed vx writer")
for d in (world.defending_team or []):
    print("  %s %s %s %s %s %s %s %s %s %s %s %s %s %s" % (
        getattr(d, "player_id", None),
        getattr(d, "depth_role", None),
        round(float(getattr(d, "x", 0.0)), 2),
        round(float(getattr(d, "y", 0.0)), 2),
        None if getattr(d, "target_x", None) is None
        else round(float(d.target_x), 2),
        None if getattr(d, "target_y", None) is None
        else round(float(d.target_y), 2),
        getattr(getattr(d, "movement_state", None), "name", None),
        getattr(d, "target_source", None),
        getattr(d, "frozen", None),
        None if getattr(d, "freeze_timer", None) is None
        else round(float(d.freeze_timer), 2),
        getattr(d, "timing_locked", None),
        None if getattr(d, "move_speed", None) is None
        else round(float(d.move_speed), 2),
        None if getattr(d, "vx", None) is None
        else round(float(d.vx), 2),
        getattr(d, "movement_writer", None)))
