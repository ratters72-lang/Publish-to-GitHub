"""
Audit Step 02 — Compression & Lateral Behaviour
================================================

Subsystems audited:
  - defensive_timing.py → compression activation, decay, lateral hesitation
  - defensive_role_spacing.py → lane offsets, compression factor application
  - defensive_behaviour_unified.py → timing gates, retreat hesitation interaction

Captures per-frame data from every OPEN_PLAY frame with a BC, then analyses:
  Part 1: Compression activation consistency
  Part 2: Lane offset smoothness
  Part 3: Lateral hesitation correctness
  Part 4: Retreat hesitation correctness
  Part 5: Timing locked interaction
  Part 6: Integration stability

Usage:
    python tmp/audit_step02_compression.py

Output: Console report with pass/fail per part + recommendations.
"""

import sys
import os
import math

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# ──────────────────────────────────────────────────────────────────────────
# GLOBAL CAPTURE BUFFER
# ──────────────────────────────────────────────────────────────────────────
FRAME_LOG = []  # per-frame summary dicts


def capture_open_play_frame(world, defenders):
    """Capture per-frame data after original _apply_open_play_unified runs.

    Collects everything needed for all 6 audit parts from the existing
    per-defender attributes set by timing/role_spacing.
    """
    bc = world.ball_carrier
    fr = world.first_receiver
    anchor_x = getattr(world, "defensive_line_anchor", 0.0)

    frame_data = {
        'frame': world.frame_count,
        'sub_state': world.sub_state.value if hasattr(world.sub_state, 'value') else str(world.sub_state),
        'role_governs': getattr(world, 'role_governs', False),
        'bc_x': bc.x if bc else None,
        'bc_y': bc.y if bc else None,
        'bc_vx': bc.vx if bc else None,
        'fr_x': fr.x if fr else None,
        'fr_y': fr.y if fr else None,
        'anchor_x': anchor_x,
        # Compression tuner values (capture once for analysis reference)
        'compression_activation_distance_m': 15.0,      # from tuners
        'compression_threat_window_m': 10.0,
        'compression_duration_s': 0.5,
        # Per-defender data
        'defenders': [],
    }

    # BC proximity to anchor for compression activation analysis
    if bc is not None and anchor_x > 0:
        bc_to_anchor = anchor_x - bc.x
        frame_data['bc_to_anchor'] = bc_to_anchor
        frame_data['bc_close'] = 0 < bc_to_anchor < frame_data['compression_activation_distance_m']
        if fr is not None:
            fr_dist = abs(fr.x - bc.x)
            frame_data['fr_threat'] = fr_dist < frame_data['compression_threat_window_m']
        else:
            frame_data['fr_threat'] = False
        frame_data['should_compress'] = frame_data['bc_close'] and frame_data['fr_threat']
    else:
        frame_data['bc_to_anchor'] = None
        frame_data['bc_close'] = False
        frame_data['fr_threat'] = False
        frame_data['should_compress'] = False

    for d in defenders:
        d_data = {
            'player_id': d.player_id,

            # Position
            'x': d.x,
            'y': d.y,

            # Target
            'target_x': d.target_x if hasattr(d, 'target_x') else d.x,
            'target_y': d.target_y if hasattr(d, 'target_y') else d.y,

            # Velocity
            'vx': d.vx,
            'vy': d.vy,
            'move_speed': d.move_speed if hasattr(d, 'move_speed') else 0.0,

            # Depth & lane
            'depth_class': getattr(d, 'depth_class', 'N/A'),
            'lane_index': getattr(d, 'lane_index', 0),
            'defender_role': str(getattr(d, 'defender_role', 'None')),

            # Part 1 — Compression activation
            'compression_active': getattr(d, 'compression_active', False),
            'compression_timer': getattr(d, 'compression_timer', 0.0),

            # Part 3 — Lateral hesitation
            'lateral_hesitation': getattr(d, 'lateral_hesitation', 0.0),

            # Part 4 — Retreat hesitation
            'retreat_delay': getattr(d, 'retreat_delay', 0.0),
            'step_stagger': getattr(d, 'step_stagger', 0.0),

            # Part 5 — Timing interaction
            'timing_locked': getattr(d, 'timing_locked', False),

            # Gates
            'frozen': getattr(d, 'frozen', False),
            'freeze_timer': getattr(d, 'freeze_timer', 0.0),
        }
        frame_data['defenders'].append(d_data)

    FRAME_LOG.append(frame_data)


# ──────────────────────────────────────────────────────────────────────────
# PATCH: intercept _apply_open_play_unified after original runs
# ──────────────────────────────────────────────────────────────────────────
import game.defence.defence_lvl_1.defensive_behaviour_unified as db_mod

_original_open_play = db_mod.DefensiveBehaviourUnified._apply_open_play_unified


def _patched_open_play(self, world, defenders):
    """Call original, then capture frame data."""
    _original_open_play(self, world, defenders)
    # Only capture if we're in OPEN_PLAY substate (not role_governs, not BC None)
    if getattr(world, 'sub_state', None) is not None:
        from world.world_state import GameSubState
        if world.sub_state == GameSubState.OPEN_PLAY and not getattr(world, 'role_governs', False):
            capture_open_play_frame(world, defenders)


db_mod.DefensiveBehaviourUnified._apply_open_play_unified = _patched_open_play

# ──────────────────────────────────────────────────────────────────────────
# SIMULATION RUNNER (same pattern as audit_step01_live)
# ──────────────────────────────────────────────────────────────────────────
import pygame

os.environ['SDL_VIDEODRIVER'] = 'dummy'
pygame.display.init()
pygame.display.set_mode((1, 1))

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

clock = pygame.time.Clock()
FPS = 60

world = WorldState()
ball = Ball(None)
world.ball = ball

# Create a Field for the kickoff engine (1920x1080 for correct scaling)
field = Field(1920, 1080)

open_play = OpenPlayEngine(world)
world.open_play = open_play
world.open_play_engine = open_play

kickoff_engine = KickoffEngine(world, ball, field)


def reset_ball_and_world():
    kickoff_engine.reset_for_kickoff()


def kickoff():
    kickoff_engine.kickoff()


# ── Setup ──
reset_ball_and_world()
apply_receiving_kickoff_formation(world, field)
apply_kicking_team_formation(world, field)
world.ball_owner = "NONE"
world.ball_carrier = None
world.ball.possessed_by = None
kickoff()

# ── Run for 25 seconds (captures ~1-2 tackle cycles) ──
TARGET_SECONDS = 25
TARGET_FRAMES = TARGET_SECONDS * 60

print(f"[AUDIT] Running for {TARGET_SECONDS}s ({TARGET_FRAMES} frames)...")

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

# ── Save capture log ──
import json
with open('tmp/audit_step02_capture.json', 'w') as f:
    json.dump(FRAME_LOG, f, indent=1, default=str)
print(f"[AUDIT] Captured {len(FRAME_LOG)} frames to tmp/audit_step02_capture.json")

# ═══════════════════════════════════════════════════════════════════════
# ANALYSIS — ALL 6 PARTS
# ═══════════════════════════════════════════════════════════════════════

print(f"\n{'='*75}")
print("  AUDIT STEP 02 — COMPRESSION & LATERAL BEHAVIOUR ANALYSIS")
print(f"{'='*75}\n")

# ── Filter to valid OPEN_PLAY frames with BC ──
valid_frames = [f for f in FRAME_LOG
                if f['bc_x'] is not None
                and not f['role_governs']
                and f['bc_to_anchor'] is not None]

print(f"Total captured frames: {len(FRAME_LOG)}")
print(f"Valid OPEN_PLAY frames with BC: {len(valid_frames)}")
print()

if not valid_frames:
    print("ERROR: No valid frames captured. Exiting.")
    sys.exit(1)


# ═══════════════════════════════════════════════════════════════════════
# PART 1 — COMPRESSION ACTIVATION AUDIT
# ═══════════════════════════════════════════════════════════════════════
print(f"{'─'*75}")
print("  PART 1 — COMPRESSION ACTIVATION AUDIT")
print(f"{'─'*75}")

# For each frame, check if any defender has compression_active
frames_with_compression = 0
frames_compression_should_trigger = 0
frames_compression_missed = 0     # should trigger but didn't
frames_compression_false_positive = 0  # triggered when shouldn't

compression_flicker_frames = []   # frames where compression toggles on/off
prev_any_active = False
prev_frame_num = None

activation_bc_distances = []      # bc_to_anchor when compression first activated
deactivation_bc_distances = []

for f in valid_frames:
    any_active = any(d['compression_active'] for d in f['defenders'])
    should = f.get('should_compress', False)

    if any_active:
        frames_with_compression += 1
    if should:
        frames_compression_should_trigger += 1
    if should and not any_active:
        frames_compression_missed += 1
    if any_active and not should:
        frames_compression_false_positive += 1

    # Detect flicker: transition from active to inactive and back rapidly
    if prev_frame_num is not None and f['frame'] == prev_frame_num + 1:
        if prev_any_active != any_active:
            bc_dist = f['bc_to_anchor']
            compression_flicker_frames.append({
                'frame': f['frame'],
                'bc_dist': bc_dist,
                'trans': 'off->on' if any_active else 'on->off',
            })

    prev_any_active = any_active
    prev_frame_num = f['frame']

# Detect first-activation distances
for i, f in enumerate(valid_frames):
    if i == 0:
        continue
    prev = valid_frames[i - 1]
    prev_any = any(d['compression_active'] for d in prev['defenders'])
    curr_any = any(d['compression_active'] for d in f['defenders'])
    if not prev_any and curr_any:
        activation_bc_distances.append(f['bc_to_anchor'])
    if prev_any and not curr_any:
        deactivation_bc_distances.append(prev['bc_to_anchor'])

print(f"  Frames with compression active:     {frames_with_compression}/{len(valid_frames)}")
print(f"  Frames where compression should:    {frames_compression_should_trigger}")
print(f"  Compression missed (should->didn't): {frames_compression_missed}")
print(f"  Compression false positive:          {frames_compression_false_positive}")
print(f"  Flicker transitions:                {len(compression_flicker_frames)}")
print(f"  Activation distances (BC->anchor):  {activation_bc_distances[:10]}")
print(f"  Deactivation distances:             {deactivation_bc_distances[:10]}")

early_activations = [d for d in activation_bc_distances if d is not None and d > 13.0]
late_activations = [d for d in activation_bc_distances if d is not None and d < 2.0]
print(f"  Activations >13m (too early?):      {len(early_activations)}")
print(f"  Activations <2m (too late?):        {len(late_activations)}")

# Check FR false negatives
fr_missed = []
for f in valid_frames:
    if f.get('bc_close', False) and not f.get('fr_threat', True) and not f.get('should_compress', False):
        fr_missed.append(f)
print(f"  Frames where BC close but FR threat fails: {len(fr_missed)}")

compression_pass = (frames_compression_missed == 0
                    and len(compression_flicker_frames) < 5
                    and len(early_activations) == 0)
print(f"  PART 1 RESULT: {'PASS' if compression_pass else 'FAIL'}")
print()

if compression_flicker_frames:
    print(f"  Flicker details (first 10):")
    for fl in compression_flicker_frames[:10]:
        print(f"     frame={fl['frame']} bc_dist={fl['bc_dist']:.2f} trans={fl['trans']}")
    print()


# ═══════════════════════════════════════════════════════════════════════
# PART 2 — LANE OFFSET AUDIT
# ═══════════════════════════════════════════════════════════════════════
print(f"{'─'*75}")
print("  PART 2 — LANE OFFSET AUDIT")
print(f"{'─'*75}")

lane_jumps = []
lane_increases = []
lane_crossings = []
prev_offsets = {}

for f in valid_frames:
    fr_y = f['fr_y'] or f.get('bc_y', 35.0)
    for d in f['defenders']:
        li = d['lane_index']
        pc = d['player_id']
        cf = d.get('compression_timer', 0.0)
        actual_offset = d['target_y'] - fr_y

        if pc in prev_offsets:
            prev_off = prev_offsets[pc]
            delta = abs(actual_offset - prev_off)
            if delta > 0.5:
                lane_jumps.append({
                    'frame': f['frame'],
                    'player': pc,
                    'prev_off': prev_off,
                    'curr_off': actual_offset,
                    'delta': delta,
                })

            if cf > 0.05:
                if abs(actual_offset) > abs(prev_off) + 0.3:
                    lane_increases.append({
                        'frame': f['frame'],
                        'player': pc,
                        'prev_off': prev_off,
                        'curr_off': actual_offset,
                        'cf': cf,
                    })

        prev_offsets[pc] = actual_offset

    # Detect potential lane crossings
    defs_sorted = sorted(f['defenders'], key=lambda d: d['player_id'])
    for i in range(len(defs_sorted)):
        for j in range(i + 1, len(defs_sorted)):
            d1 = defs_sorted[i]
            d2 = defs_sorted[j]
            if (d1['target_y'] - fr_y) * (d2['target_y'] - fr_y) < 0:
                if abs(d1['y'] - d2['y']) < 1.0 and d1['lane_index'] != d2['lane_index']:
                    lane_crossings.append(f['frame'])

print(f"  Lane jumps (>0.5m/frame):            {len(lane_jumps)}")
print(f"  Unexpected offset increases:          {len(lane_increases)}")
print(f"  Potential lane crossings:             {len(set(lane_crossings))}")

if lane_jumps:
    print(f"  Lane jump details (first 10):")
    for lj in lane_jumps[:10]:
        print(f"     frame={lj['frame']} p#{lj['player']} "
              f"prev={lj['prev_off']:.2f} curr={lj['curr_off']:.2f} d={lj['delta']:.2f}")

# Check CF progression smoothness
if len(valid_frames) >= 10:
    sample_defenders = {}
    for f in valid_frames:
        for d in f['defenders']:
            pc = d['player_id']
            if pc not in sample_defenders:
                sample_defenders[pc] = []
            sample_defenders[pc].append({
                'frame': f['frame'],
                'cf': d.get('compression_timer', 0.0),
                'offset': d['target_y'] - (f['fr_y'] or 35.0),
            })

    cf_jumps = []
    for pc, samples in sample_defenders.items():
        for i in range(1, len(samples)):
            prev = samples[i - 1]
            curr = samples[i]
            cf_delta = curr['cf'] - prev['cf']
            if abs(cf_delta) > 0.1:
                cf_jumps.append({
                    'frame': curr['frame'],
                    'player': pc,
                    'cf_delta': cf_delta,
                })

    print(f"  CF jumps (>0.1/frame):                {len(cf_jumps)}")
    if cf_jumps:
        print(f"  CF jump details (first 10):")
        for cj in cf_jumps[:10]:
            print(f"     frame={cj['frame']} p#{cj['player']} d={cj['cf_delta']:.3f}")

lane_offset_pass = (len(lane_jumps) < 10 and len(lane_increases) < 10
                    and len(set(lane_crossings)) < 10)
print(f"  PART 2 RESULT: {'PASS' if lane_offset_pass else 'FAIL'}")
print()


# ═══════════════════════════════════════════════════════════════════════
# PART 3 — LATERAL HESITATION AUDIT
# ═══════════════════════════════════════════════════════════════════════
print(f"{'─'*75}")
print("  PART 3 — LATERAL HESITATION AUDIT")
print(f"{'─'*75}")

hesitation_by_depth = {}

for f in valid_frames:
    for d in f['defenders']:
        dc = d['depth_class']
        lh = d['lateral_hesitation']
        if dc not in hesitation_by_depth:
            hesitation_by_depth[dc] = {'active_frames': 0, 'total_frames': 0, 'max_duration': 0}
        hesitation_by_depth[dc]['total_frames'] += 1
        if lh > 0:
            hesitation_by_depth[dc]['active_frames'] += 1
            if lh > hesitation_by_depth[dc]['max_duration']:
                hesitation_by_depth[dc]['max_duration'] = lh

for dc in sorted(hesitation_by_depth.keys()):
    h = hesitation_by_depth[dc]
    pct = (h['active_frames'] / max(1, h['total_frames'])) * 100
    max_dur = h['max_duration']
    flag = " WARN" if dc in ('PRIMARY', 'SECONDARY') and pct > 5 else ""
    print(f"  {dc:12s}: hesitation {h['active_frames']:4d}/{h['total_frames']:4d} frames "
          f"({pct:5.1f}%)  max_duration={max_dur:.3f}s{flag}")

# Check lateral hesitation blocking Y movement
lh_blocks_y = 0
for f in valid_frames:
    for d in f['defenders']:
        if d['lateral_hesitation'] > 0 and d.get('compression_active', False):
            if abs(d['target_y'] - d['y']) > 0.1:
                lh_blocks_y += 1

print(f"  Lateral hesitation not freezing Y:         {lh_blocks_y} instances")

lat_hes_pass = (hesitation_by_depth.get('PRIMARY', {}).get('active_frames', 0)
                / max(1, hesitation_by_depth.get('PRIMARY', {}).get('total_frames', 1)) < 0.1
                and hesitation_by_depth.get('SECONDARY', {}).get('active_frames', 0)
                / max(1, hesitation_by_depth.get('SECONDARY', {}).get('total_frames', 1)) < 0.15)
print(f"  PART 3 RESULT: {'PASS' if lat_hes_pass else 'FAIL'}")
print()


# ═══════════════════════════════════════════════════════════════════════
# PART 4 — RETREAT HESITATION AUDIT
# ═══════════════════════════════════════════════════════════════════════
print(f"{'─'*75}")
print("  PART 4 — RETREAT HESITATION AUDIT")
print(f"{'─'*75}")

retreat_by_depth = {}
retreat_freeze_x = 0
for f in valid_frames:
    for d in f['defenders']:
        dc = d['depth_class']
        rd = d['retreat_delay']
        if dc not in retreat_by_depth:
            retreat_by_depth[dc] = {'active_frames': 0, 'total_frames': 0, 'max_duration': 0}
        retreat_by_depth[dc]['total_frames'] += 1
        if rd > 0:
            retreat_by_depth[dc]['active_frames'] += 1
            if rd > retreat_by_depth[dc]['max_duration']:
                retreat_by_depth[dc]['max_duration'] = rd
            if abs(d['target_x'] - d['x']) < 0.1:
                retreat_freeze_x += 1

for dc in sorted(retreat_by_depth.keys()):
    r = retreat_by_depth[dc]
    pct = (r['active_frames'] / max(1, r['total_frames'])) * 100
    max_dur = r['max_duration']
    flag = " WARN" if dc in ('PRIMARY', 'SECONDARY') and pct > 0 else ""
    print(f"  {dc:12s}: retreat {r['active_frames']:4d}/{r['total_frames']:4d} frames "
          f"({pct:5.1f}%)  max_duration={max_dur:.3f}s{flag}")

print(f"  Retreat hesitation freezing X:              {retreat_freeze_x} instances")

for dc in ('PRIMARY', 'SECONDARY'):
    r = retreat_by_depth.get(dc, {})
    if r.get('active_frames', 0) > 0:
        print(f"  ERROR: {dc} has {r['active_frames']} retreat frames - should be 0!")

retreat_pass = (retreat_by_depth.get('PRIMARY', {}).get('active_frames', 0) == 0
                and retreat_by_depth.get('SECONDARY', {}).get('active_frames', 0) == 0)
print(f"  PART 4 RESULT: {'PASS' if retreat_pass else 'FAIL'}")
print()


# ═══════════════════════════════════════════════════════════════════════
# PART 5 — TIMING INTERACTION AUDIT
# ═══════════════════════════════════════════════════════════════════════
print(f"{'─'*75}")
print("  PART 5 — TIMING INTERACTION AUDIT")
print(f"{'─'*75}")

locked_by_depth = {}
locked_during_compression = 0
for f in valid_frames:
    for d in f['defenders']:
        dc = d['depth_class']
        locked = d['timing_locked']
        if dc not in locked_by_depth:
            locked_by_depth[dc] = {'locked_frames': 0, 'total_frames': 0}
        locked_by_depth[dc]['total_frames'] += 1
        if locked:
            locked_by_depth[dc]['locked_frames'] += 1
            if d.get('compression_active', False):
                locked_during_compression += 1

for dc in sorted(locked_by_depth.keys()):
    l = locked_by_depth[dc]
    pct = (l['locked_frames'] / max(1, l['total_frames'])) * 100
    flag = " ERROR" if dc in ('PRIMARY', 'SECONDARY') and l['locked_frames'] > 0 else ""
    print(f"  {dc:12s}: locked {l['locked_frames']:4d}/{l['total_frames']:4d} frames "
          f"({pct:5.1f}%){flag}")

print(f"  Locked frames during compression:           {locked_during_compression}")

passive_locked_with_comp = 0
for f in valid_frames:
    for d in f['defenders']:
        if d['depth_class'] == 'PASSIVE' and d['timing_locked'] and d.get('compression_active', False):
            passive_locked_with_comp += 1
print(f"  PASSIVE locked while compressing:           {passive_locked_with_comp}")

timing_pass = (locked_by_depth.get('PRIMARY', {}).get('locked_frames', 0) == 0
               and locked_by_depth.get('SECONDARY', {}).get('locked_frames', 0) == 0
               and passive_locked_with_comp < 10)
print(f"  PART 5 RESULT: {'PASS' if timing_pass else 'FAIL'}")
print()


# ═══════════════════════════════════════════════════════════════════════
# PART 6 — INTEGRATION AUDIT
# ═══════════════════════════════════════════════════════════════════════
print(f"{'─'*75}")
print("  PART 6 — INTEGRATION AUDIT")
print(f"{'─'*75}")

zigzag_frames = 0
frozen_frames = 0
overshoot_frames = 0
early_collapse = 0
late_collapse = 0
prev_y_by_player = {}

for f in valid_frames:
    bcx = f['bc_x']
    for d in f['defenders']:
        pc = d['player_id']
        dc = d['depth_class']

        # Zig-zag (lateral jitter)
        if pc in prev_y_by_player:
            y_delta = abs(d['y'] - prev_y_by_player[pc]['y'])
            if y_delta > 0.3:
                zigzag_frames += 1

        # Freeze (defender not moving despite target being different)
        if not d['frozen'] and not d['timing_locked']:
            dx_to_target = d['target_x'] - d['x']
            dy_to_target = d['target_y'] - d['y']
            if abs(d['vx']) < 0.01 and abs(d['vy']) < 0.01:
                if abs(dx_to_target) > 0.5 or abs(dy_to_target) > 0.5:
                    frozen_frames += 1

        # Overshoot (PRIMARY/SECONDARY target_x > bc.x)
        if dc in ('PRIMARY', 'SECONDARY') and d['target_x'] > bcx:
            overshoot_frames += 1

        # Early/late collapse
        if dc in ('PRIMARY', 'SECONDARY') and f['bc_to_anchor'] is not None:
            bc_dist = f['bc_to_anchor']
            cf = d.get('compression_timer', 0.0)
            if bc_dist > 10.0 and cf > 0.5:
                early_collapse += 1
            if bc_dist < 3.0 and cf < 0.3 and bc_dist > 0:
                late_collapse += 1

        prev_y_by_player[pc] = {'y': d['y']}

print(f"  Zig-zag instances (>0.3m Y/frame):          {zigzag_frames}")
print(f"  Frozen frames (not moving to target):       {frozen_frames}")
print(f"  Overshoot (PRIMARY/SECONDARY target>bc.x):  {overshoot_frames}")
print(f"  Early collapse (compressed >10m out):        {early_collapse}")
print(f"  Late collapse (not compressed <3m):          {late_collapse}")

integration_pass = (zigzag_frames < 20
                    and frozen_frames < len(valid_frames) * 0.1
                    and overshoot_frames < 50
                    and early_collapse < 10
                    and late_collapse < 10)
print(f"  PART 6 RESULT: {'PASS' if integration_pass else 'FAIL'}")
print()


# ═══════════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════════
print(f"{'='*75}")
print("  AUDIT STEP 02 - OVERALL SUMMARY")
print(f"{'='*75}")
print(f"  Part 1 - Compression Activation:      {'PASS' if compression_pass else 'FAIL'}")
print(f"  Part 2 - Lane Offset:                 {'PASS' if lane_offset_pass else 'FAIL'}")
print(f"  Part 3 - Lateral Hesitation:          {'PASS' if lat_hes_pass else 'FAIL'}")
print(f"  Part 4 - Retreat Hesitation:          {'PASS' if retreat_pass else 'FAIL'}")
print(f"  Part 5 - Timing Interaction:          {'PASS' if timing_pass else 'FAIL'}")
print(f"  Part 6 - Integration:                 {'PASS' if integration_pass else 'FAIL'}")

all_pass = all([compression_pass, lane_offset_pass, lat_hes_pass,
                retreat_pass, timing_pass, integration_pass])
print(f"\n  OVERALL: {'ALL PASS' if all_pass else 'SOME PARTS FAIL - see above'}")
print()

# ═══════════════════════════════════════════════════════════════════════
# RECOMMENDATIONS
# ═══════════════════════════════════════════════════════════════════════
print(f"{'='*75}")
print("  RECOMMENDATIONS")
print(f"{'='*75}\n")

if not compression_pass:
    print("  Part 1 (Compression Activation):")
    if frames_compression_missed > 0:
        print("    - FR threat check failing: compression_threat_window_m(10.0) may be")
        print("      too narrow for fast FR movement. Consider increasing to 15.0 or")
        print("      removing the FR threat check entirely.")
    if early_activations:
        print("    - Compression activating too early (>13m from anchor).")
        print("      Consider reducing compression_activation_distance_m from 15.0 to 10.0")
        print("      to match real rugby compression timing.")
    if compression_flicker_frames:
        print("    - Compression flickering detected. Root cause may be FR lateral")
        print("      movement causing fr_threat to toggle. Add hysteresis: require")
        print("      FR threat to be True for >2 frames before deactivating.")
    print()

if not lane_offset_pass:
    print("  Part 2 (Lane Offset):")
    if lane_jumps:
        print("    - Lane offset jumping >0.5m/frame detected. Likely caused by")
        print("      compression flicker -> lane_offset = li*2.0*(1-cf) jumps when")
        print("      cf toggles. Fix compression stability first.")
    if lane_increases:
        print("    - Lane offset increasing unexpectedly during compression. This may")
        print("      be the lateral hesitation override kicking in:")
        print("      'if lateral_state > 0 and compression_factor > 0: lane_offset = li*2.0'.")
        print("      When hesitation decays, offset snaps back to compressed value.")
    print()

if not lat_hes_pass:
    print("  Part 3 (Lateral Hesitation):")
    if hesitation_by_depth.get('PRIMARY', {}).get('active_frames', 0) > 0:
        print("    - PRIMARY defenders should rarely get lateral hesitation.")
        print("      Check: PRIMARY overrides timing_locked but may not clear lateral_hesitation.")
    print()

if not retreat_pass:
    print("  Part 4 (Retreat Hesitation):")
    if retreat_by_depth.get('PRIMARY', {}).get('active_frames', 0) > 0:
        print("    - PRIMARY defenders should NEVER have retreat_delay > 0.")
        print("      Check: timing._update_retreat_hesitation sets retreat_delay=0 only for")
        print("      PRIMARY/SECONDARY, ordering matters.")
    print()

if not timing_pass:
    print("  Part 5 (Timing Interaction):")
    if locked_by_depth.get('PRIMARY', {}).get('locked_frames', 0) > 0:
        print("    - PRIMARY timing_locked > 0 - should be impossible.")
        print("      Check: behaviour_unified Gate 3 clears timing_locked for PRIMARY/SECONDARY,")
        print("      but step_stagger might not be cleared?")
    if passive_locked_with_comp > 0:
        print("    - PASSIVE timing_locked while compression_active - this means")
        print("      defenders are frozen in place while supposedly compressing.")
        print("      May cause 'freeze then jump' behaviour when lock releases.")
    print()

if not integration_pass:
    print("  Part 6 (Integration):")
    if zigzag_frames > 0:
        print("    - Zig-zag lateral movement detected. Likely caused by compression")
        print("      flicker + hesitation decay oscillation. Stability pass needed.")
    if overshoot_frames > 0:
        print(f"    - PRIMARY/SECONDARY overshoot (target_x > bc.x): {overshoot_frames} instances.")
        print("      Lead-point projection (bc.x + bc.vx * 0.3) causes target_x to be ahead of BC.")
        print("      Consider: cap lead_point at bc.x - 0.5 to prevent overshoot.")
    if frozen_frames > 0:
        print(f"    - Frozen frames: {frozen_frames}. Defenders not moving to target.")
        print("      May be timing_locked + PASSIVE freeze interaction.")
    print()

print(f"{'='*75}")
print("  AUDIT COMPLETE")
print(f"{'='*75}")
