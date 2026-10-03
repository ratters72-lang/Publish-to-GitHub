import sys, os
sys.path.insert(0, '.')
import game.tes.tes_state
print("game.tes.tes_state OK")

import game.tes.tes_context
print("game.tes.tes_context OK")

import game.tes.tes
print("game.tes.tes OK")

import game.tes.tes_dominance
print("game.tes.tes_dominance OK")

import player.collision_body
print("player.collision_body OK")

import world.open_play_engine
print("world.open_play_engine OK")

print("\n=== ALL RUNTIME IMPORTS OK ===")
