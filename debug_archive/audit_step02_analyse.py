"""
Audit Step 02 — Analysis Only (reads captured JSON from audit_step02_compression.py)
"""

import json
import sys
import math

try:
    with open('tmp/audit_step02_capture.json', 'r') as f:
        FRAME_LOG = json.load(f)
except FileNotFoundError:
    with open('tmp/audit_step02_capture.json', 'r') as f:
        FRAME_LOG = json.load(f)

print(f"\n{'='*75}")
print("  AUDIT STEP 02 — COMPRESSION & LATERAL BEHAVIOUR ANALYSIS")
print(f"{'='*75}\n")

# Filter to valid OPEN_PLAY frames with BC
valid_frames = [f for f in FRAME_LOG
                if f.get('bc_x') is not None
                and not f.get('role_governs', False)
                and f.get('bc_to_anchor') is not None]

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

frames_with_compression = 0
frames_compression_should_trigger = 0
frames_compression_missed = 0
frames_compression_false_positive = 0

compression_flicker_frames = []
prev_any_active = False
prev_frame_num = None

activation_bc_distances = []
deactivation_bc_distances = []

for f in valid_frames:
    any_active = any(d.get('compression_active', False) for d in f['defenders'])
    should = f.get('should_compress', False)

    if any_active:
        frames_with_compression += 1
    if should:
        frames_compression_should_trigger += 1
    if should and not any_active:
        frames_compression_missed += 1
    if any_active and not should:
        frames_compression_false_positive += 1

    if prev_frame_num is not None and f['frame'] == prev_frame_num + 1:
        if prev_any_active != any_active:
            compression_flicker_frames.append({
                'frame': f['frame'],
                'bc_dist': f.get('bc_to_anchor'),
                'trans': 'off->on' if any_active else 'on->off',
            })

    prev_any_active = any_active
    prev_frame_num = f['frame']

for i, f in enumerate(valid_frames):
    if i == 0:
        continue
    prev = valid_frames[i - 1]
    prev_any = any(d.get('compression_active', False) for d in prev['defenders'])
    curr_any = any(d.get('compression_active', False) for d in f['defenders'])
    if not prev_any and curr_any:
        activation_bc_distances.append(f.get('bc_to_anchor'))
    if prev_any and not curr_any:
        deactivation_bc_distances.append(prev.get('bc_to_anchor'))

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
    fr_y = f.get('fr_y') or f.get('bc_y', 35.0)
    for d in f['defenders']:
        li = d.get('lane_index', 0)
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
                    'prev_off': round(prev_off, 2),
                    'curr_off': round(actual_offset, 2),
                    'delta': round(delta, 2),
                })

            if cf > 0.05:
                if abs(actual_offset) > abs(prev_off) + 0.3:
                    lane_increases.append({
                        'frame': f['frame'],
                        'player': pc,
                        'prev_off': round(prev_off, 2),
                        'curr_off': round(actual_offset, 2),
                        'cf': round(cf, 3),
                    })

        prev_offsets[pc] = actual_offset

    defs_sorted = sorted(f['defenders'], key=lambda d: d['player_id'])
    for i in range(len(defs_sorted)):
        for j in range(i + 1, len(defs_sorted)):
            d1 = defs_sorted[i]
            d2 = defs_sorted[j]
            if (d1['target_y'] - fr_y) * (d2['target_y'] - fr_y) < 0:
                if abs(d1['y'] - d2['y']) < 1.0 and d1.get('lane_index', 0) != d2.get('lane_index', 0):
                    lane_crossings.append(f['frame'])

print(f"  Lane jumps (>0.5m/frame):            {len(lane_jumps)}")
print(f"  Unexpected offset increases:          {len(lane_increases)}")
print(f"  Potential lane crossings:             {len(set(lane_crossings))}")

if lane_jumps:
    print(f"  Lane jump details (first 10):")
    for lj in lane_jumps[:10]:
        print(f"     frame={lj['frame']} p#{lj['player']} "
              f"prev={lj['prev_off']} curr={lj['curr_off']} d={lj['delta']}")

# CF progression
sample_defenders = {}
for f in valid_frames:
    for d in f['defenders']:
        pc = d['player_id']
        if pc not in sample_defenders:
            sample_defenders[pc] = []
        sample_defenders[pc].append({
            'frame': f['frame'],
            'cf': d.get('compression_timer', 0.0),
        })

cf_jumps = []
for pc, samples in sample_defenders.items():
    for i in range(1, len(samples)):
        prev_cf = samples[i - 1]['cf']
        curr_cf = samples[i]['cf']
        cf_delta = curr_cf - prev_cf
        if abs(cf_delta) > 0.1:
            cf_jumps.append({
                'frame': samples[i]['frame'],
                'player': pc,
                'cf_delta': round(cf_delta, 3),
            })

print(f"  CF jumps (>0.1/frame):                {len(cf_jumps)}")
if cf_jumps:
    print(f"  CF jump details (first 10):")
    for cj in cf_jumps[:10]:
        print(f"     frame={cj['frame']} p#{cj['player']} d={cj['cf_delta']}")

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
        dc = d.get('depth_class', 'N/A')
        lh = d.get('lateral_hesitation', 0.0)
        if dc not in hesitation_by_depth:
            hesitation_by_depth[dc] = {'active_frames': 0, 'total_frames': 0, 'max_duration': 0.0}
        hesitation_by_depth[dc]['total_frames'] += 1
        if lh > 0:
            hesitation_by_depth[dc]['active_frames'] += 1
            if lh > hesitation_by_depth[dc]['max_duration']:
                hesitation_by_depth[dc]['max_duration'] = lh

for dc in sorted(hesitation_by_depth.keys()):
    h = hesitation_by_depth[dc]
    pct = (h['active_frames'] / max(1, h['total_frames'])) * 100
    flag = "  WARN" if dc in ('PRIMARY', 'SECONDARY') and pct > 5 else ""
    print(f"  {dc:12s}: hesitation {h['active_frames']:4d}/{h['total_frames']:4d} frames "
          f"({pct:5.1f}%)  max_duration={h['max_duration']:.3f}s{flag}")

lh_blocks_y = 0
for f in valid_frames:
    for d in f['defenders']:
        if d.get('lateral_hesitation', 0) > 0 and d.get('compression_active', False):
            if abs(d['target_y'] - d['y']) > 0.1:
                lh_blocks_y += 1
print(f"  Lateral hesitation not freezing Y:         {lh_blocks_y} instances")

lat_hes_pass = (
    hesitation_by_depth.get('PRIMARY', {}).get('active_frames', 0)
    / max(1, hesitation_by_depth.get('PRIMARY', {}).get('total_frames', 1)) < 0.1
    and hesitation_by_depth.get('SECONDARY', {}).get('active_frames', 0)
    / max(1, hesitation_by_depth.get('SECONDARY', {}).get('total_frames', 1)) < 0.15
)
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
        dc = d.get('depth_class', 'N/A')
        rd = d.get('retreat_delay', 0.0)
        if dc not in retreat_by_depth:
            retreat_by_depth[dc] = {'active_frames': 0, 'total_frames': 0, 'max_duration': 0.0}
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
    flag = "  WARN" if dc in ('PRIMARY', 'SECONDARY') and pct > 0 else ""
    print(f"  {dc:12s}: retreat {r['active_frames']:4d}/{r['total_frames']:4d} frames "
          f"({pct:5.1f}%)  max_duration={r['max_duration']:.3f}s{flag}")

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
        dc = d.get('depth_class', 'N/A')
        locked = d.get('timing_locked', False)
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
    flag = "  ERROR" if dc in ('PRIMARY', 'SECONDARY') and l['locked_frames'] > 0 else ""
    print(f"  {dc:12s}: locked {l['locked_frames']:4d}/{l['total_frames']:4d} frames "
          f"({pct:5.1f}%){flag}")

print(f"  Locked frames during compression:           {locked_during_compression}")

passive_locked_with_comp = 0
for f in valid_frames:
    for d in f['defenders']:
        if d.get('depth_class') == 'PASSIVE' and d.get('timing_locked', False) and d.get('compression_active', False):
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
        dc = d.get('depth_class', 'N/A')

        # Zig-zag
        if pc in prev_y_by_player:
            y_delta = abs(d['y'] - prev_y_by_player[pc]['y'])
            if y_delta > 0.3:
                zigzag_frames += 1

        # Freeze
        if not d.get('frozen', False) and not d.get('timing_locked', False):
            dx_to_target = d['target_x'] - d['x']
            dy_to_target = d['target_y'] - d['y']
            if abs(d.get('vx', 0)) < 0.01 and abs(d.get('vy', 0)) < 0.01:
                if abs(dx_to_target) > 0.5 or abs(dy_to_target) > 0.5:
                    frozen_frames += 1

        # Overshoot
        if dc in ('PRIMARY', 'SECONDARY') and d['target_x'] > bcx:
            overshoot_frames += 1

        # Early/late collapse
        if dc in ('PRIMARY', 'SECONDARY') and f.get('bc_to_anchor') is not None:
            bc_dist = f['bc_to_anchor']
            cf = d.get('compression_timer', 0.0)
            if bc_dist is not None and bc_dist > 10.0 and cf > 0.5:
                early_collapse += 1
            if bc_dist is not None and bc_dist < 3.0 and cf < 0.3 and bc_dist > 0:
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
print("  AUDIT STEP 02 — OVERALL SUMMARY")
print(f"{'='*75}")
print(f"  Part 1 — Compression Activation:      {'PASS' if compression_pass else 'FAIL'}")
print(f"  Part 2 — Lane Offset:                 {'PASS' if lane_offset_pass else 'FAIL'}")
print(f"  Part 3 — Lateral Hesitation:          {'PASS' if lat_hes_pass else 'FAIL'}")
print(f"  Part 4 — Retreat Hesitation:          {'PASS' if retreat_pass else 'FAIL'}")
print(f"  Part 5 — Timing Interaction:          {'PASS' if timing_pass else 'FAIL'}")
print(f"  Part 6 — Integration:                 {'PASS' if integration_pass else 'FAIL'}")

all_pass = all([compression_pass, lane_offset_pass, lat_hes_pass,
                retreat_pass, timing_pass, integration_pass])
result = "ALL PASS" if all_pass else "SOME PARTS FAIL - see above"
print(f"\n  OVERALL: {result}")
print()

# ═══════════════════════════════════════════════════════════════════════
# RECOMMENDATIONS
# ═══════════════════════════════════════════════════════════════════════
print(f"{'='*75}")
print("  FINDINGS & RECOMMENDATIONS")
print(f"{'='*75}\n")

if not compression_pass:
    print("  Part 1 (Compression Activation):")
    if frames_compression_missed > 0:
        print(f"    - {frames_compression_missed}x compression missed when it should have triggered.")
        print("      FR threat check: compression_threat_window_m(10.0) may be too narrow.")
        print("      Consider increasing to 15.0 or removing the FR threat check.")
    if early_activations:
        print(f"    - {len(early_activations)}x early activation (>13m from anchor).")
        print("      Reduce compression_activation_distance_m from 15.0 to 10.0.")
    if compression_flicker_frames:
        print(f"    - {len(compression_flicker_frames)} flicker transitions.")
        print("      Likely FR threat toggling. Add hysteresis: require FR threat >2 frames.")
    print()

if not lane_offset_pass:
    print("  Part 2 (Lane Offset):")
    if lane_jumps:
        print(f"    - {len(lane_jumps)} lane jumps >0.5m/frame.")
        print("      Root cause: compression flicker -> lane_offset = li*2.0*(1-cf) jumps.")
    if lane_increases:
        print(f"    - {len(lane_increases)} unexpected offset increases during compression.")
        print("      Likely lateral hesitation override: 'if lat_state>0: offset = li*2.0'.")
        print("      When hesitation decays, offset snaps back — causing Y jump.")
    print()

if not lat_hes_pass:
    print("  Part 3 (Lateral Hesitation):")
    pr = hesitation_by_depth.get('PRIMARY', {})
    if pr.get('active_frames', 0) > 0:
        print(f"    - PRIMARY: {pr['active_frames']}/{pr['total_frames']} frames hesitating.")
        print("      PRIMARY overrides timing_locked but may not clear lateral_hesitation.")
    print()

if not retreat_pass:
    print("  Part 4 (Retreat Hesitation):")
    for dc in ('PRIMARY', 'SECONDARY'):
        r = retreat_by_depth.get(dc, {})
        if r.get('active_frames', 0) > 0:
            print(f"    - {dc}: {r['active_frames']} retreat frames (should be 0).")
    print()

if not timing_pass:
    print("  Part 5 (Timing Interaction):")
    for dc in ('PRIMARY', 'SECONDARY'):
        l = locked_by_depth.get(dc, {})
        if l.get('locked_frames', 0) > 0:
            print(f"    - {dc}: {l['locked_frames']} locked frames (should be 0).")
    if passive_locked_with_comp > 0:
        print(f"    - PASSIVE: {passive_locked_with_comp}x locked while compressing.")
        print("      Defenders frozen while supposedly compressing — freeze->jump risk.")
    print()

if not integration_pass:
    print("  Part 6 (Integration):")
    if zigzag_frames > 0:
        print(f"    - {zigzag_frames} zig-zag instances.")
    if overshoot_frames > 0:
        print(f"    - {overshoot_frames} PRIMARY/SECONDARY overshoots (target_x > bc.x).")
        print("      Lead-point projection: bc.x + bc.vx*0.3 overshoots BC position.")
        print("      Consider: cap lead_point at bc.x - 0.5 to prevent overshoot.")
    if frozen_frames > 0:
        print(f"    - {frozen_frames} frozen frames (not moving to target).")
    print()

print(f"{'='*75}")
print("  AUDIT COMPLETE")
print(f"{'='*75}")
