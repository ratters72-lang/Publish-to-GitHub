"""Headless verification: run 300 frames, capture [SUBSTATE] and PTB logs."""
import sys
import os
import io
from contextlib import redirect_stdout, redirect_stderr

# Add project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Suppress pygame banner
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'
import pygame
pygame.display.set_mode((1, 1))  # minimal init for headless

from world.field import Field
from world.world_state import WorldState, GameWorldState, GameSubState
from world.open_play_engine import OpenPlayEngine, OpenPlayConfig
from player.player import Player, PlayerState, DefenderRole, Team as PlayerTeam
from ball.ball import Ball

# Create minimal world
field = Field(1920, 1080)
world = WorldState()
ball = Ball(field)
world.ball = ball

# Create minimal teams
att = []
for i in range(13):
    p = Player(30.0, 5 + i * 5, player_id=100 + i)
    p.team = PlayerTeam.RECEIVING
    att.append(p)

deff = []
for i in range(13):
    p = Player(40.0, 5 + i * 5, player_id=200 + i)
    p.team = PlayerTeam.KICKING
    deff.append(p)

world.attacking_team = att
world.defending_team = deff
world.players = att + deff
world.attacking_direction = 1
world.defending_left = False

bc = att[0]
bc.state = PlayerState.BALL_CARRIER
bc.x = 50.0
bc.y = 30.0
bc.vx = 2.0
bc.vy = 0.0
world.ball_carrier = bc

ball.possessed_by = bc
ball.x_m = bc.x
ball.y_m = bc.y

# Position one defender close enough for tackle
tackler = deff[0]
tackler.x = 52.0
tackler.y = 31.0
tackler.vx = -1.0
tackler.vy = 0.0

open_play = OpenPlayEngine(world)
world.open_play = open_play
open_play.start_open_play(bc, att, deff)

# Capture output
capture = io.StringIO()

with redirect_stdout(capture), redirect_stderr(capture):
    for frame in range(300):
        dt = 1.0 / 60.0
        # Update ball physics
        ball.update(dt)
        # Run open play
        open_play.update(dt)
        # Run player updates for non-frozen players
        for p in world.players:
            if not p.frozen:
                p.update(dt, world.attacking_direction)
        world.frame_count += 1

output = capture.getvalue()

# ANALYZE: print key events
print("=== PTB SURVIVAL ANALYSIS ===")
ptb_frames = []
contamination = []

for i, line in enumerate(output.split('\n')):
    line_s = line.strip()
    if '[SUBSTATE]' in line_s:
        print(line_s)
        if 'PTB' in line_s or 'TACKLE_COMPLETE' in line_s:
            ptb_frames.append(i)
    if 'PTB FREEZE' in line_s:
        print(line_s)
    if 'PTB SOLID' in line_s:
        print(line_s)
    if 'PTB MARKERS' in line_s:
        print(line_s)
    if 'PTB TIMER' in line_s:
        print(line_s)
    if 'POST_RUCK ENTER' in line_s:
        print(line_s)
    if 'OPEN_PLAY MODE' in line_s:
        print(line_s)
        # Check if OPEN_PLAY appears in same frame region as PTB
    if 'TACKLE_INITIATED' in line_s:
        print(line_s)

print("\n=== VERDICT ===")
# Check if PTB survived more than 1 frame
ptb_substate_count = output.count('[SUBSTATE]')
ptb_start_count = output.count('→ PTB')
ptb_done_count = output.count('PTB_DONE')
post_ruck_count = output.count('POST_RUCK ENTER')
tackle_complete_count = output.count('→ TACKLE_COMPLETE')
open_play_after = output.count('OPEN_PLAY MODE')

print(f"TACKLE_COMPLETE transitions: {tackle_complete_count}")
print(f"PTB transitions: {ptb_start_count}")
print(f"POST_RUCK_OPEN_PLAY transitions: {ptb_done_count}")
print(f"POST_RUCK ENTER logs: {post_ruck_count}")
print(f"OPEN_PLAY MODE executions: {open_play_after}")

# Check: if OPEN_PLAY MODE prints happened WHILE sub_state was PTB,
# that's contamination. We can only check via the [SUBSTATE] ordering.
# The key check: did TACKLE_COMPLETE → PTB happen, and was a subsequent
# TACKLE_INITIATED from PTB? That would be the bug.
# 
# Looking at the output order: we expect:
# TACKLE_CONTACT → TACKLE_COMPLETE → PTB → [PTB runs] → POST_RUCK → OPEN_PLAY
# If we see TACKLE_INITIATED between PTB and POST_RUCK, the bug is active.

ptb_after_tackle = False
contaminated = False
for line in output.split('\n'):
    if '→ PTB' in line:
        ptb_after_tackle = True
    if ptb_after_tackle and 'TACKLE_INITIATED' in line:
        contaminated = True
    if ptb_after_tackle and '→ OPEN_PLAY' in line:
        ptb_after_tackle = False

if contaminated:
    print("❌ CONTAMINATION: Tackle re-initiated AFTER PTB — _tackle_started is still being cleared!")
elif ptb_done_count > 0:
    print("✅ PTB survived without contamination — _tackle_started fix is working!")
else:
    print("⚠️  No PTB cycle completed (DH never picked up ball or no tackle happened)")
