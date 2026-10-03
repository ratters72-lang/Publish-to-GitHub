import io, re

with io.open('world/open_play_engine.py', 'r', encoding='utf-8-sig') as f:
    src = f.read()

old_core = src.count(
    'world_ctx["frozen_ids"] = [p.player_id for p in self.world.players]'
)
calls = re.findall(r'self\._stamp_tes_freeze_mask\((True|False)\)', src)
defs = len(re.findall(r'def _stamp_tes_freeze_mask', src))

print(f"old_core_remaining={old_core}")
print(f"helper_defs={defs} calls={len(calls)} args={calls}")

with io.open('game/defence/defence_lvl_1/ruck_adjacent_pin.py', 'r', encoding='utf-8-sig') as f:
    pin = f.read()
print(f"fx4_status_follow={'FOLLOW' in pin}")
print(f"fx1a_join_band={'_join_band_m' in src}")
print(f"fx1b_assist_lunge={'SECONDARY ASSIST LUNGE' in src}")
