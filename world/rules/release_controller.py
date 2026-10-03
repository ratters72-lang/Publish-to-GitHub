"""
world/rules/release_controller.py — Phase-ownership-aware role controller.

Enforces the single-authority movement rules per the compliance checklist:
  OPEN_PLAY         — open-play controller (unfreeze all, assign roles)
  TACKLE_INIT       — TES freeze engine owns (_update_defenders_tes_phases)
  TACKLE_CONTACT    — TES freeze engine owns
  TACKLE_DRIVE      — TES freeze engine owns
  TACKLE_DOMINANCE  — TES freeze engine owns
  RELEASE           — this file handles RELEASE freeze enforcement
  PTB_PREP/EXEC/PTB — PTBManager / OPE inline

RELEASE freeze rules:
  Attackers:
    Movers: BC + DH
    Everyone else: frozen (vx=0, vy=0, target pinned to current position)
  Defenders:
    Movers: MARKER_A + MARKER_B + DEF_RETREAT_10 + DH (defensive DH)
    Everyone else: frozen
"""
print(__file__)

from player.player import Player, DefenderRole


def ai_tes_controller(world, dh: Player | None, phase: str,
                       primary: Player | None = None,
                       joiners: list[Player] | None = None) -> None:
    """
    Phase-ownership-aware controller.

    OPEN_PLAY: fix attack_roles, fix None defender_roles, unfreeze all.
    RELEASE: enforce RELEASE freeze invariants — only permitted roles move.
    All other phases: no-op (TES freeze engine owns them).
    """
    # ── RELEASE: enforce freeze invariants ──────────────────────────
    if phase == "RELEASE":
        _enforce_release_freeze(world, dh)
        return

    # ── OPEN_PLAY: fix roles + unfreeze all ─────────────────────────
    if phase != "OPEN_PLAY":
        return  # TES phases handled by _update_defenders_tes_phases()

    bc = world.ball_carrier

    # 1. Attackers — assign correct attack_role
    for p in world.attacking_team:
        if p is bc:
            p.attack_role = "ball_carrier"
        elif p is dh:
            p.attack_role = "dummy_half"
        else:
            p.attack_role = "support_runner"

    # 2. Defenders — default None → DEF_LINE_READY
    for p in world.defending_team:
        if p.defender_role is None:
            p.defender_role = DefenderRole.DEF_LINE_READY

    # 3. Unfreeze everything — OPEN_PLAY has no freeze
    for p in world.players:
        p.frozen = False


def _enforce_release_freeze(world, dh: Player | None) -> None:
    """
    Enforce RELEASE freeze invariants — TARGET/VELOCITY + MOVER RE-ARM.

    Pins targets for non-movers (vx=0, target pinned to current pos) AND
    explicitly re-arms the movers (frozen=False, freeze_timer=0) so no
    TES-era freeze residue survives onto a RELEASE mover.  The TES freeze
    authority (``apply_phase_invariants`` during TACKLE_INIT, where
    DEF_RETREAT_10 / DEF_LINE_READY are ``must_be_frozen``) can leave a
    mover's ``frozen`` flag stuck at True when its role resolution lags
    the ``_prepare_release`` conversion — a stuck mover is never integrated
    by the retreat writer and becomes STUCK_NOT_FROZEN (vx=0, target != x),
    unable to join the defensive geometry.

    Attackers: movers = BC + DH
    Defenders: movers = MARKER_A + MARKER_B + DEF_RETREAT_10 + DH
    """
    bc = world.ball_carrier

    # Build set of players allowed to move during RELEASE
    allowed_movers = set()

    # Attackers
    for p in world.attacking_team:
        if p is bc:
            allowed_movers.add(p)
        elif p is dh:
            allowed_movers.add(p)

    # Defenders
    for p in world.defending_team:
        if p is dh:
            allowed_movers.add(p)
        elif p.defender_role in (
            DefenderRole.MARKER_A,
            DefenderRole.MARKER_B,
            DefenderRole.DEF_RETREAT_10,
        ):
            allowed_movers.add(p)

    # Pin targets for non-movers; MOVER RE-ARM + non-mover pin.
    for p in world.players:
        # FULLBACK HARD-EXCLUDE (2026-08-27): the dynamic fullback is a
        # backfield anchor owned by fullback_role.apply_fullback_geometry.
        # It must NEVER be pinned to its current position by the RELEASE
        # freeze writer -- regardless of its defender_role.  Hard-skip it
        # before the mover check so a transient role drift cannot pin it.
        if getattr(p, "fullback_role_active", False):
            continue
        if p in allowed_movers:
            # TES FREEZE CLEAR (2026-09-01): the TES-era freeze flag (set
            # by apply_phase_invariants during TACKLE_INIT when
            # DEF_RETREAT_10 / DEF_LINE_READY were must_be_frozen) can
            # survive onto a RELEASE mover when role resolution lags the
            # _prepare_release conversion.  A mover that stays frozen is
            # never integrated by the retreat writer and becomes
            # STUCK_NOT_FROZEN (vx=0, target != x) — it can't join the
            # defensive geometry.  Force-clear it here so every mover is
            # unfrozen before the RELEASE writers drive it.
            p.frozen = False
            p.freeze_timer = 0.0
            continue
        p.target_x = p.x
        p.target_y = p.y
        p.vx = 0.0
        p.vy = 0.0
