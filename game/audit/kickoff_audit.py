"""
game/audit/kickoff_audit.py — Kickoff pipeline contamination audit.

Audits the full kickoff → catch → pursuit → structured → OPEN_PLAY
pipeline for state leakage and contamination.  Run standalone by
passing a captured WorldState + defender list, or wire into the
simulation loop for continuous monitoring.

Usage:
    from game.audit.kickoff_audit import audit_kickoff_pipeline
    audit_kickoff_pipeline(world, defenders)
"""

import math
from world.world_state import GamePhase, GameSubState


def audit_kickoff_pipeline(world, defenders):
    """
    Perform a full audit of kickoff → catch → pursuit → structured → OPEN_PLAY.
    Prints violations for any contamination detected.

    Parameters
    ----------
    world : WorldState
        The current world snapshot.
    defenders : list[Player]
        List of defender Player objects (typically world.defending_team).
    """
    print("\n=== KICKOFF PIPELINE AUDIT START ===")

    # ------------------------------------------------------------
    # SECTION 1 — STATE FLAGS
    # ------------------------------------------------------------
    print("\n[STATE FLAGS]")

    # 1.1 Kickoff flag
    if world.phase == GamePhase.OPEN_PLAY and world.in_kickoff:
        print("ERROR: OPEN_PLAY running while in_kickoff=True")

    # 1.2 Pursuit mode
    if world.phase == GamePhase.OPEN_PLAY and world.in_pursuit_mode:
        print("WARN: Structured defence running while pursuit_mode=True")

    # 1.3 Sub-state correctness
    valid_chain = [
        GameSubState.KICKOFF_IN_AIR,
        GameSubState.OPEN_PLAY,
    ]
    if world.sub_state not in valid_chain:
        print(f"WARN: Unexpected sub_state during kickoff pipeline: {world.sub_state}")

    # ------------------------------------------------------------
    # SECTION 2 — WORLD VARIABLES
    # ------------------------------------------------------------
    print("\n[WORLD VARIABLES]")

    # 2.1 defensive_line_anchor
    if world.in_kickoff and world.defensive_line_anchor is None:
        print("ERROR: Kickoff chase requires defensive_line_anchor")

    if (not world.in_kickoff) and world.phase == GamePhase.OPEN_PLAY:
        if world.defensive_line_anchor is None:
            print("ERROR: OPEN_PLAY missing defensive_line_anchor")

    # 2.2 ruck_x_m
    if world.in_kickoff and world.ruck_x_m is None:
        print("ERROR: Kickoff missing ruck_x_m (landing zone)")

    if world.phase == GamePhase.OPEN_PLAY and world.ruck_x_m is None:
        print("ERROR: OPEN_PLAY missing ruck_x_m (should be BC.x)")

    # 2.3 first_receiver
    if world.in_kickoff and world.first_receiver is None:
        print("ERROR: Kickoff missing first_receiver (catcher)")

    if (not world.in_kickoff) and world.phase == GamePhase.OPEN_PLAY:
        # FR must be recomputed from attacking team
        if world.first_receiver is None:
            print("WARN: OPEN_PLAY missing first_receiver")

    # ------------------------------------------------------------
    # SECTION 3 — PER-PLAYER VARIABLES
    # ------------------------------------------------------------
    print("\n[PLAYER VARIABLES]")

    for d in defenders:
        # 3.1 lane_index
        if world.phase == GamePhase.OPEN_PLAY and world.in_kickoff:
            print(f"ERROR: Defender {d.player_id} has kickoff lane_index during OPEN_PLAY")

        # 3.2 target_x
        if world.phase == GamePhase.OPEN_PLAY:
            if hasattr(d, "target_x") and d.target_x == world.defensive_line_anchor:
                print(f"WARN: Defender {d.player_id} still using kickoff anchor_x target")

        # 3.3 move_speed
        if world.phase == GamePhase.OPEN_PLAY and d.move_speed > d.max_speed_mps:
            print(f"WARN: Defender {d.player_id} has kickoff chase speed in OPEN_PLAY")

    # ------------------------------------------------------------
    # SECTION 4 — GEOMETRY
    # ------------------------------------------------------------
    print("\n[GEOMETRY]")

    if world.phase == GamePhase.OPEN_PLAY:
        # Geometry must be OFF in OPEN_PLAY
        if world.defensive_line_anchor is None:
            print("ERROR: Geometry disabled but anchor_x missing")
        # But lane_y must NOT be kickoff geometry
        # (Kickoff uses landing_y convergence)
        # (OPEN_PLAY uses FR lane)
        # We detect contamination by checking if lane_y is near landing_y
        landing_y = getattr(world, "kickoff_landing_y", None)
        if landing_y is not None:
            for d in defenders:
                if abs(d.y - landing_y) < 3.0:
                    print(f"WARN: Defender {d.player_id} still near kickoff landing_y")

    # ------------------------------------------------------------
    # SECTION 5 — MOVEMENT SYSTEM
    # ------------------------------------------------------------
    print("\n[MOVEMENT]")

    bc = world.ball_carrier
    if bc:
        # 5.1 BC teleport detection
        if hasattr(bc, "prev_x"):
            if abs(bc.x - bc.prev_x) > 1.0:  # >1m per frame = teleport
                print(f"ERROR: BC teleport detected: {bc.prev_x} → {bc.x}")

        bc.prev_x = bc.x

    # ------------------------------------------------------------
    # SECTION 5B — DEFENDER SPEED AUDIT
    # ------------------------------------------------------------
    print("\n[SPEED AUDIT]")

    for d in defenders:
        # 1. Kickoff chase speed leaking into OPEN_PLAY
        if world.phase == GamePhase.OPEN_PLAY and d.move_speed > d.max_speed_mps:
            print(f"ERROR: Defender {d.player_id} has illegal speed > max_speed_mps in OPEN_PLAY "
                  f"(move_speed={d.move_speed:.2f}, max={d.max_speed_mps:.2f})")

        # 2. TES freeze contamination (speed forced to 0)
        if world.phase == GamePhase.OPEN_PLAY and d.move_speed == 0.0:
            # Only legal if defender is frozen inside ruck bubble
            if not getattr(d, "frozen", False):
                print(f"ERROR: Defender {d.player_id} has move_speed=0.0 in OPEN_PLAY without TES freeze")

        # 3. Stale kickoff movement vectors (vx/vy too large)
        if world.phase == GamePhase.OPEN_PLAY:
            spd = math.hypot(getattr(d, "vx", 0.0), getattr(d, "vy", 0.0))
            if spd > d.max_speed_mps * 1.1:  # >10% above max = kickoff contamination
                print(f"ERROR: Defender {d.player_id} has stale kickoff velocity "
                      f"(vx={d.vx:.2f}, vy={d.vy:.2f}, speed={spd:.2f})")

        # 4. Governor speed ramp misfire (speed too high when BC far away)
        if world.phase == GamePhase.OPEN_PLAY and not world.in_pursuit_mode:
            bc = world.ball_carrier
            if bc:
                dist = abs(d.x - bc.x)
                if dist > 20.0 and d.move_speed > d.max_speed_mps * 0.6:
                    print(f"WARN: Defender {d.player_id} has excessive speed at large BC distance "
                          f"(dist={dist:.1f}, speed={d.move_speed:.2f})")

        # 5. Speed scaling mismatch (target_x/y too close but speed too high)
        if world.phase == GamePhase.OPEN_PLAY:
            dx = d.target_x - d.x
            dy = d.target_y - d.y
            dist = math.hypot(dx, dy)
            if dist < 5.0 and d.move_speed > d.max_speed_mps * 0.75:
                print(f"WARN: Defender {d.player_id} has high speed despite close target "
                      f"(dist={dist:.2f}, speed={d.move_speed:.2f})")

    # ------------------------------------------------------------
    # SECTION 6 — DEFENCE / TES INTERACTION
    # ------------------------------------------------------------
    print("\n[DEFENCE / TES]")

    # 6.1 Tackler roles must exist in structured mode
    if world.phase == GamePhase.OPEN_PLAY and not world.in_pursuit_mode:
        for d in defenders:
            if getattr(d, "tackle_role", None) is None:
                print(f"ERROR: Defender {d.player_id} missing tackle_role in structured OPEN_PLAY")

    # 6.2 TES freeze contamination
    for d in defenders:
        if world.phase == GamePhase.OPEN_PLAY and d.frozen:
            print(f"ERROR: Defender {d.player_id} frozen during OPEN_PLAY (TES contamination)")

    print("\n=== KICKOFF PIPELINE AUDIT END ===\n")
