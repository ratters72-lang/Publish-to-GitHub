"""
world/rules/ai_context.py — Phase-gated context for AI defence/agents.

SINGLE SOURCE OF TRUTH for what each phase allows the AI to do.
All AI agents (defence_pipeline, retreat_agent, defence_reactivity_agent,
freeze_audit) MUST read this file to determine permitted actions per frame.

Usage:

    from world.rules.ai_context import get_phase_context

    ctx = get_phase_context(current_phase_str)
    if not ctx["defence_pipeline"] and not ctx["retreat_agent"]:
        return  # AI does nothing this frame

    if ctx["can_modify_frozen"]:
        # okay to touch player.frozen
        pass

Invariant: the keys in AI_CONTEXT must exactly match the internal _phase
values used by OpenPlayEngine (OPEN_PLAY, TACKLE_INIT, TACKLE_CONTACT,
TACKLE_DRIVE, TACKLE_DOMINANCE, RELEASE, PTB_PREP, PTB_EXEC, PTB,
POST_RUCK_OPEN_PLAY).
"""

# ──────────────────────────────────────────────────────────────────────
# AI_CONTEXT — Phase → what AI systems are ALLOWED to do
# ──────────────────────────────────────────────────────────────────────
AI_CONTEXT: dict[str, dict[str, bool]] = {
    # ──────────────────────────────────────────────────────────────────
    # TRUE OPEN PLAY: full defence + attack pipeline
    # ──────────────────────────────────────────────────────────────────
    "OPEN_PLAY": {
        "defence_pipeline": True,    # run DefensiveBehaviourUnified + geometry
        "retreat_agent":    False,   # no DEF_RETREAT_10 specific retreat
        "attack_pipeline":  True,    # run attack controller + support runners
        "can_modify_targets": True,  # set target_x/target_y
        "can_modify_velocity": True, # set vx/vy (via Player.update)
        "can_modify_frozen":  False, # DO NOT touch .frozen in AI
    },

    # ──────────────────────────────────────────────────────────────────
    # TES PHASES: tackle engagement owns everything
    # ──────────────────────────────────────────────────────────────────
    "TACKLE_INIT": {
        "defence_pipeline": False,
        "retreat_agent":    False,
        "attack_pipeline":  False,
        "can_modify_targets": False,
        "can_modify_velocity": False,
        "can_modify_frozen":  False,
    },
    "TACKLE_CONTACT": {
        "defence_pipeline": False,
        "retreat_agent":    False,
        "attack_pipeline":  False,
        "can_modify_targets": False,
        "can_modify_velocity": False,
        "can_modify_frozen":  False,
    },
    "TACKLE_DRIVE": {
        "defence_pipeline": False,
        "retreat_agent":    False,
        "attack_pipeline":  False,
        "can_modify_targets": False,
        "can_modify_velocity": False,
        "can_modify_frozen":  False,
    },
    "TACKLE_DOMINANCE": {
        "defence_pipeline": False,
        "retreat_agent":    False,
        "attack_pipeline":  False,
        "can_modify_targets": False,
        "can_modify_velocity": False,
        "can_modify_frozen":  False,
    },

    # ──────────────────────────────────────────────────────────────────
    # RELEASE + PTB: TES/PTBManager own freeze + DH movement
    # ──────────────────────────────────────────────────────────────────
    "RELEASE": {
        "defence_pipeline": False,
        "retreat_agent":    False,
        "attack_pipeline":  False,
        "can_modify_targets": False,
        "can_modify_velocity": False,
        "can_modify_frozen":  False,
    },
    "PTB_PREP": {
        "defence_pipeline": False,
        "retreat_agent":    False,
        "attack_pipeline":  False,
        "can_modify_targets": False,
        "can_modify_velocity": False,
        "can_modify_frozen":  False,
    },
    "PTB_EXEC": {
        "defence_pipeline": False,
        "retreat_agent":    False,
        "attack_pipeline":  False,
        "can_modify_targets": False,
        "can_modify_velocity": False,
        "can_modify_frozen":  False,
    },
    "PTB": {
        "defence_pipeline": False,
        "retreat_agent":    False,
        "attack_pipeline":  False,
        "can_modify_targets": False,
        "can_modify_velocity": False,
        "can_modify_frozen":  False,
    },

    # ──────────────────────────────────────────────────────────────────
    # POST_RUCK_OPEN_PLAY: retreat‑only re‑entry
    # ──────────────────────────────────────────────────────────────────
    "POST_RUCK_OPEN_PLAY": {
        "defence_pipeline": False,   # NO full line AI here
        "retreat_agent":    True,    # ONLY DEF_RETREAT_10 retreat logic
        "attack_pipeline":  True,    # BC continues moving under attack controller
        "can_modify_targets": True,  # retreat target_x behind ruck
        "can_modify_velocity": True, # move toward retreat target
        "can_modify_frozen":  False, # still don't clear .frozen
    },
}


def get_phase_context(phase_str: str) -> dict[str, bool]:
    """Return AI context dict for *phase_str*.

    Falls back to OPEN_PLAY for unknown phases (belt-and-suspenders).
    """
    return AI_CONTEXT.get(phase_str, AI_CONTEXT["OPEN_PLAY"])


def is_phase_ai_allowed(phase_str: str) -> bool:
    """Return True if ANY AI system should run in *phase_str*.

    Convenience wrapper for the common "should I shut down entirely?" pattern.
    """
    ctx = get_phase_context(phase_str)
    return ctx["defence_pipeline"] or ctx["retreat_agent"] or ctx["attack_pipeline"]
