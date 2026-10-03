"""
Audit Step 1 — Live integration runner for DefensiveRoleSpacing.
Patches the sim, runs 15s, captures per-frame data, dumps analysis.
"""

import sys
import os
import math

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# ── Patch OpenPlayEngine._apply_open_play_unified to capture data ──
from world.world_state import WorldState, GameSubState

FRAME_LOG = []  # per-frame capture buffer

def make_patch():
    import game.defence.defence_lvl_1.defensive_behaviour_unified as db_mod
    original = db_mod.DefensiveBehaviourUnified._apply_open_play_unified
    
    def patched_method(self, world, defenders):
        # Call original first
        original(self, world, defenders)
        # Then capture
        _capture_frame(world, defenders)
    
    db_mod.DefensiveBehaviourUnified._apply_open_play_unified = patched_method


def _capture_frame(world, defenders):
    bc = world.ball_carrier
    fr = world.first_receiver
    anchor = world.defensive_line_anchor

    frame_data = {
        'frame': world.frame_count,
        'sub_state': world.sub_state.value if hasattr(world.sub_state, 'value') else str(world.sub_state),
        'role_governs': world.role_governs,
        'bc_x': bc.x if bc else None,
        'bc_y': bc.y if bc else None,
        'bc_vx': bc.vx if bc else None,
        'fr_x': fr.x if fr else None,
        'fr_y': fr.y if fr else None,
        'anchor_x': anchor,
        'defenders': []
    }

    for d in defenders:
        d_data = {
            'player_id': d.player_id,
            'x': d.x, 'y': d.y,
            'target_x': d.target_x, 'target_y': d.target_y,
            'depth_class': getattr(d, 'depth_class', 'N/A'),
            'depth_role': getattr(d, 'depth_role', 'N/A'),
            'defender_role': str(getattr(d, 'defender_role', 'None')),
            'lane_index': getattr(d, 'lane_index', 0),
            'frozen': d.frozen,
            'timing_locked': getattr(d, 'timing_locked', False),
            'compression_active': getattr(d, 'compression_active', False),
            'compression_timer': getattr(d, 'compression_timer', 0.0),
            'lateral_hesitation': getattr(d, 'lateral_hesitation', 0.0),
            'retreat_delay': getattr(d, 'retreat_delay', 0.0),
            'step_stagger': getattr(d, 'step_stagger', 0.0),
            'vx': d.vx, 'vy': d.vy,
        }
        frame_data['defenders'].append(d_data)

    FRAME_LOG.append(frame_data)

    # Every 60 frames print a summary line
    if world.frame_count % 60 == 0 and bc is not None:
        print(f"[LIVE] frame={world.frame_count} bc=({bc.x:.1f},{bc.y:.1f}) vx={bc.vx:.1f} anchor={anchor:.1f}")
        for d in defenders:
            dc = getattr(d, 'depth_class', '?')
            dr = str(getattr(d, 'defender_role', 'None'))
            li = getattr(d, 'lane_index', 0)
            print(f"  d#{d.player_id} dc={dc} r={dr} li={li} "
                  f"pos=({d.x:.1f},{d.y:.1f}) target=({d.target_x:.1f},{d.target_y:.1f})")


# Apply patch
make_patch()

# ── Now import and run normally ──
import pygame

from world.field import Field
from world.world_state import WorldState, GameWorldState, GameSubState
from world.open_play_engine import OpenPlayEngine
from kick_off.kickoff_formations import (
    apply_receiving_kickoff_formation,
    apply_kicking_team_formation,
)
from game.kickoff_engine import KickoffEngine
from ball.ball import Ball
from game.physics.collision_system import CollisionSystem

collision_system = CollisionSystem()

# Headless mode — no display
os.environ['SDL_VIDEODRIVER'] = 'dummy'
pygame.display.init()
pygame.display.set_mode((1, 1))

clock = pygame.time.Clock()
FPS = 60

world = WorldState()
ball = Ball(None)
world.ball = ball

# ── Create a minimal Field (needed by kickoff_engine) ──
# Dummy screen dimensions — won't be rendered in headless mode
field_dummy = Field(800, 600)

open_play = OpenPlayEngine(world)
world.open_play = open_play
world.open_play_engine = open_play

kickoff_engine = KickoffEngine(world, ball, field_dummy)


def reset_ball_and_world():
    kickoff_engine.reset_for_kickoff()


def kickoff():
    kickoff_engine.kickoff()


# ── Setup ──
reset_ball_and_world()
apply_receiving_kickoff_formation(world, field_dummy)
apply_kicking_team_formation(world, field_dummy)
world.ball_owner = "NONE"
world.ball_carrier = None
world.ball.possessed_by = None
kickoff()

# ── Run for 15 seconds ──
TARGET_SECONDS = 15
TARGET_FRAMES = TARGET_SECONDS * 60

print(f"[AUDIT] Running for {TARGET_SECONDS}s...")

running = True
while running:
    dt = clock.tick(FPS) / 1000.0

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False

    ball.update(dt)
    kickoff_engine.update(dt)
    open_play.update(dt)

    if not world.in_kickoff and not world.tes_active:
        collision_system.resolve_all(world.players, world=world)

    world.frame_count += 1

    if world.frame_count >= TARGET_FRAMES:
        running = False

# ── Write capture log ──
import json
with open('tmp/audit_step01_capture.json', 'w', encoding='utf-8') as f:
    json.dump(FRAME_LOG, f, indent=1, default=str)
print(f"[AUDIT] Captured {len(FRAME_LOG)} frames to tmp/audit_step01_capture.json")

# ── Analysis ──
print(f"\n{'='*60}")
print("LIVE CAPTURE ANALYSIS")
print(f"{'='*60}")

# Filter to OPEN_PLAY frames with BC
open_play_frames = [f for f in FRAME_LOG 
                    if f['sub_state'] == 'open_play' 
                    and f['bc_x'] is not None
                    and not f['role_governs']]

print(f"OPEN_PLAY frames with BC: {len(open_play_frames)}")

# Analyse PRIMARY target_x
primary_target_xs = []
primary_bc_delta = []
primary_lead_point_active = 0
primary_lead_point_inactive = 0
for f in open_play_frames:
    bcx = f['bc_x']
    bcvx = f['bc_vx']
    for d in f['defenders']:
        if d['depth_class'] == 'PRIMARY' and not d['frozen'] and not d['timing_locked']:
            primary_target_xs.append(d['target_x'])
            primary_bc_delta.append(d['target_x'] - bcx)
            if bcvx > 0.5:
                expected_lead = bcx + bcvx * 0.3
                if abs(d['target_x'] - expected_lead) < 0.1:
                    primary_lead_point_active += 1
                else:
                    primary_lead_point_inactive += 1

if primary_target_xs:
    print(f"\nPRIMARY target_x stats ({len(primary_target_xs)} samples):")
    print(f"  min: {min(primary_target_xs):.2f}")
    print(f"  max: {max(primary_target_xs):.2f}")
    print(f"  mean: {sum(primary_target_xs)/len(primary_target_xs):.2f}")
    print(f"  delta from bc.x: min={min(primary_bc_delta):.2f} mean={sum(primary_bc_delta)/len(primary_bc_delta):.2f} max={max(primary_bc_delta):.2f}")
    print(f"  lead_point active: {primary_lead_point_active}")
    print(f"  lead_point inactive: {primary_lead_point_inactive}")
    overshoot = sum(1 for d in primary_bc_delta if d > 0)
    print(f"  times target_x > bc.x: {overshoot}/{len(primary_bc_delta)}")
    if len(primary_bc_delta) > 0:
        overshoot_pct = overshoot / len(primary_bc_delta) * 100
        print(f"  >> Overshoot (target_x > bc.x): {overshoot_pct:.1f}%")

# Analyse PASSIVE target_x
passive_target_xs = []
passive_anchor_delta = []
for f in open_play_frames:
    anchor = f['anchor_x']
    for d in f['defenders']:
        if d['depth_class'] == 'PASSIVE' and not d['frozen'] and not d['timing_locked']:
            passive_target_xs.append(d['target_x'])
            passive_anchor_delta.append(d['target_x'] - anchor)

if passive_target_xs:
    print(f"\nPASSIVE target_x stats ({len(passive_target_xs)} samples):")
    print(f"  min: {min(passive_target_xs):.2f}")
    print(f"  max: {max(passive_target_xs):.2f}")
    print(f"  mean: {sum(passive_target_xs)/len(passive_target_xs):.2f}")
    print(f"  delta from anchor: min={min(passive_anchor_delta):.2f} mean={sum(passive_anchor_delta)/len(passive_anchor_delta):.2f} max={max(passive_anchor_delta):.2f}")
    anom = sum(1 for d in passive_anchor_delta if abs(d) > 0.5)
    print(f"  times target_x != anchor: {anom}/{len(passive_anchor_delta)}")

# Analyse lane offsets
lane_offsets = []
for f in open_play_frames:
    fr_y = f['fr_y'] or f['bc_y']
    for d in f['defenders']:
        dc = d['depth_class']
        if dc in ('PRIMARY', 'SECONDARY') and not d['frozen'] and not d['timing_locked']:
            li = d['lane_index']
            actual_offset = d['target_y'] - fr_y
            lane_offsets.append((li, actual_offset, d['target_y']))

if lane_offsets:
    print(f"\nLane offset analysis ({len(lane_offsets)} samples):")
    by_lane = {}
    for li, act_offset, ty in lane_offsets:
        by_lane.setdefault(li, []).append((act_offset, ty))
    for li in sorted(by_lane.keys()):
        samples = by_lane[li]
        mean_off = sum(s[0] for s in samples) / len(samples)
        mean_ty = sum(s[1] for s in samples) / len(samples)
        print(f"  lane_index={li}: mean_offset={mean_off:.2f} mean_ty={mean_ty:.2f} ({len(samples)} samples)")

# Freeze/timing state counts
frozen_count = sum(1 for f in open_play_frames 
                   for d in f['defenders'] if d['frozen'])
timing_locked_count = sum(1 for f in open_play_frames 
                          for d in f['defenders'] if d['timing_locked'])
print(f"\nState counts:")
print(f"  frozen defender-frames: {frozen_count}")
print(f"  timing_locked defender-frames: {timing_locked_count}")

# Marker analysis
marker_samples = []
for f in open_play_frames:
    bcx = f['bc_x']
    bcy = f['bc_y']
    for d in f['defenders']:
        dr = d['defender_role']
        if 'MARKER' in dr:
            marker_samples.append((d['x'], d['y'], d['target_x'], d['target_y'], bcx, bcy))

if marker_samples:
    print(f"\nMarker analysis ({len(marker_samples)} samples):")
    tx_vals = [m[2] for m in marker_samples]
    ty_vals = [m[3] for m in marker_samples]
    print(f"  target_x: min={min(tx_vals):.2f} max={max(tx_vals):.2f} mean={sum(tx_vals)/len(tx_vals):.2f}")
    print(f"  target_y: min={min(ty_vals):.2f} max={max(ty_vals):.2f} mean={sum(ty_vals)/len(ty_vals):.2f}")

# DH analysis
dh_samples = []
for f in open_play_frames:
    bcx = f['bc_x']
    bcy = f['bc_y']
    for d in f['defenders']:
        dr = d['defender_role']
        if 'DUMMY_HALF' in dr:
            dh_samples.append((d['x'], d['y'], d['target_x'], d['target_y'], bcx, bcy))

if dh_samples:
    print(f"\nDH analysis ({len(dh_samples)} samples):")
    tx_vals = [m[2] for m in dh_samples]
    ty_vals = [m[3] for m in dh_samples]
    bcx_vals = [m[4] for m in dh_samples]
    bcy_vals = [m[5] for m in dh_samples]
    print(f"  target_x: min={min(tx_vals):.2f} max={max(tx_vals):.2f} mean={sum(tx_vals)/len(tx_vals):.2f}")
    print(f"  target_y: min={min(ty_vals):.2f} max={max(ty_vals):.2f} mean={sum(ty_vals)/len(ty_vals):.2f}")
    # Check target vs bc.x - 1.0
    deltas = [tx_vals[i] - (bcx_vals[i] - 1.0) for i in range(len(tx_vals))]
    print(f"  delta from bc.x - 1.0: min={min(deltas):.2f} max={max(deltas):.2f} mean={sum(deltas)/len(deltas):.2f}")

print(f"\n{'='*60}")
print("AUDIT COMPLETE")
print(f"{'='*60}")
