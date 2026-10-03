"""
game/debug/defence_reactivity_audit.py — Defence Reactivity Audit Agent.

Diagnostic that validates defenders react correctly when the ball carrier
breaks the defensive line during OPEN_PLAY.  Called once per frame from
OpenPlayEngine.update() alongside the existing freeze audit.

Checks performed:
  1. LATE_DEPTH_SWITCH — defender remains PASSIVE after BC passed anchor
  2. STATIC_TARGET_AFTER_LINE_BREAK — target_x == x when BC is ahead
  3. TIMING_LOCK_POST_RUCK — retreat_delay/timing_locked still active when BC past anchor
  4. BAD_DECISION_ON_LINE_BREAK — decision arbiter returned HOLD/DRIFT when BC clearly ahead
"""

import math

from game.defence.defence_lvl_1.anchor_authority import audit_stable_depth


def audit_defence_reactivity(world):
    """Run defence reactivity checks for the current frame.

    Must be called once per frame after defence systems have run.
    Prints diagnostic messages to stdout when violations are detected.

    Args:
        world: GameWorldState with ball_carrier, defending_team, phase, sub_state, anchor_x.
    """
    phase = getattr(world, "phase", None)
    sub = getattr(world, "sub_state", None)
    bc = getattr(world, "ball_carrier", None)
    anchor_x = getattr(world, "defensive_line_anchor", None)

    if phase is None or sub is None:
        return
    phase_str = phase.value if hasattr(phase, "value") else str(phase)
    sub_str = sub.value if hasattr(sub, "value") else str(sub)

    if phase_str != "OPEN_PLAY" or sub_str != "open_play":
        return

    if bc is None or anchor_x is None:
        return

    # ── POST-RUCK GRACE WINDOW ───────────────────────────────────
    # During the first ~0.5s after a PTB the defence is legitimately
    # re-forming: attackers retarget support lines and defenders retreat
    # to depth, while defensive_line_anchor may still be 0.0 (unset).
    # Flagging "line break" violations here is noise — a 0-anchor means
    # "line not formed yet", not "BC broke the line".
    #
    # The clock derives from frames_since_ptb (cleared on PTB completion,
    # incremented each OPEN_PLAY frame).  The 0-anchor gate is INDEPENDENT
    # of the clock: while the line anchor is unset we cannot judge line
    # breaks, so skip all checks regardless of how long OPEN_PLAY has run.
    frames_since_ptb = getattr(world, "frames_since_ptb", None)
    if frames_since_ptb is not None:
        if frames_since_ptb < 30:
            return
    if not anchor_x or anchor_x <= 0.0:
        return

    # ── Compute retreat line for false-positive filtering ──
    ruck_x = getattr(world, "ruck_x_m", None)
    retreat_line_x = (ruck_x + 10.0) if ruck_x is not None else (bc.x + 10.0)

    defenders = getattr(world, "defending_team", [])

    from player.player import DefenderRole

    for p in defenders:
        role = getattr(p, "defender_role", None)
        # Skip markers and dummy half — they don't participate in line defence
        if role is not None and role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B, DefenderRole.DUMMY_HALF):
            continue
        # TASK I-B (2026-08-11): the dynamic fullback actor is exempt from
        # the depth-switch / line-break audit — it owns its own backfield
        # anchor (target_source="fullback") and is not a line-band holder.
        # Firing LATE_DEPTH_SWITCH on the fullback actor every frame was the
        # remaining spam after the engagement-state exemption landed.
        if getattr(p, "fullback_role_active", False):
            continue

        # ── FALSE-POSITIVE FILTER: DEF_RETREAT_10 defenders are intentionally
        # positioned at retreat_line_x during POST_RUCK_OPEN_PLAY / OPEN_PLAY.
        # Their target_x == current_x is EXPECTED behaviour (they've reached
        # the retreat line). Do not flag them.
        if role == DefenderRole.DEF_RETREAT_10:
            tx = getattr(p, "target_x", p.x)
            # If this defender is at or near the retreat line, skip all checks
            if p.x >= retreat_line_x - 0.5:
                continue

        # Check 1: If BC has passed the line +1m, a defender whose ENGAGEMENT
        # BAND role is AGGRESSIVE/BALANCED must NOT be classified PASSIVE.
        #
        # TASK A-2 (2026-08-10): the persistent engagement-state module owns
        # role_depth_class — the hysteresis/lock-protected band posture.
        # A PASSIVE band-role is now LEGITIMATE after a line break (role-lock
        # holds the line depth; PASSIVE cannot be re-entered repeatedly and the
        # ranked PRIMARY/SECONDARY promotion still puts CHASE-eligible
        # defenders on the BC).  Flagging every PASSIVE defender each frame
        # was the LATE_DEPTH_SWITCH spam.  Only a PASSIVE *depth_class* on a
        # defender whose stable band role is NOT PASSIVE is a true missed
        # line-break reaction.
        depth_class = getattr(p, "depth_class", "PASSIVE")
        # TASK G (2026-08-10): read the STABLE structural role from the
        # Anchor Authority — NEVER the transient band role.  The authority
        # also refuses to switch a locked depth.
        band_role = audit_stable_depth(world, p)
        if band_role not in (None, "PASSIVE"):
            if bc.x > anchor_x + 1.0 and depth_class == "PASSIVE":
                print(
                    f"[AGENT] LATE_DEPTH_SWITCH id={p.player_id} "
                    f"bc_x={bc.x:.2f} anchor_x={anchor_x:.2f} depth={depth_class} "
                    f"band={band_role}"
                )

        # Check 2: Once BC is past the line, target_x must be ahead of current_x
        if bc.x > anchor_x + 1.0:
            tx = getattr(p, "target_x", p.x)
            # Skip if defender is at retreat line (expected static target)
            if p.x >= retreat_line_x - 0.5:
                pass  # already skipped above for DEF_RETREAT_10
            elif abs(tx - p.x) < 0.1:
                print(
                    f"[AGENT] STATIC_TARGET_AFTER_LINE_BREAK id={p.player_id} "
                    f"x={p.x:.2f} tx={tx:.2f} bc_x={bc.x:.2f}"
                )

        # Check 3: No long retreat_delay / timing locks after line break
        if bc.x > anchor_x:
            rd = getattr(p, "retreat_delay", 0.0)
            tl = getattr(p, "timing_locked", False)
            if rd > 0.0 or tl:
                # Skip marking if we're in a PTB/RELEASE phase where
                # timing locks legitimately exist
                if not getattr(world, "open_play_engine", None) or \
                   getattr(world.open_play_engine, "_phase", "") not in \
                   ("RELEASE", "PTB_PREP", "PTB_EXEC", "PTB"):
                    print(
                        f"[AGENT] TIMING_LOCK_POST_RUCK id={p.player_id} "
                        f"delay={rd:.2f} locked={tl}"
                    )

        # Check 4: If decision == HOLD/DRIFT while BC is clearly ahead, flag it
        # Skip DEF_RETREAT_10 — they legitimately may have HOLD/DRIFT decisions
        if bc.x > anchor_x + 3.0 and role != DefenderRole.DEF_RETREAT_10:
            last_decision = getattr(p, "last_decision", "NONE")
            if last_decision in ("HOLD", "DRIFT"):
                print(
                    f"[AGENT] BAD_DECISION_ON_LINE_BREAK id={p.player_id} "
                    f"decision={last_decision} bc_x={bc.x:.2f} anchor_x={anchor_x:.2f}"
                )
