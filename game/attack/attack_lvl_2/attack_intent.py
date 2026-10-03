class AttackIntent:
    """Team-level attacking intent (Stage 2 reconnect).

    Previously an unimplemented stub returning ``"ATTACK"`` unconditionally.
    Now produces a real team intent from world state each frame (gated by
    AttackStructureTuners.anticipation_enabled).  Determines the overall
    attack posture for the side, stamped on ``world._attack_intent`` and
    consumed by the Stage 7 decision engine.

    Postures:
      SET_DEFENCE — no ball carrier yet (kickoff/loose); the side is
                    organising rather than attacking.
      ESCAPE      — the BC is in the post-PTB escape window (forced scoot).
      MOMENTUM    — line break / open-field carry; commit numbers forward.
      ATTACK      — standard structured attack.
    """

    def decide(self, world):
        bc = getattr(world, "ball_carrier", None)
        if bc is None:
            return "SET_DEFENCE"

        # Post-PTB escape window (mirrors AttackController's escape gate).
        esc_frames = getattr(world, "escape_window_frames", 20)
        seconds_since_ptb = getattr(world, "seconds_since_ptb", None)
        if seconds_since_ptb is not None and seconds_since_ptb < (esc_frames / 60.0):
            return "ESCAPE"
        if getattr(world, "frames_since_ptb", 0) < esc_frames:
            return "ESCAPE"

        # Open-field breakaway: the BC is clear of the bulk of the defence.
        defenders = getattr(world, "defending_team", []) or []
        if defenders:
            nearest = min(
                ((bc.x - d.x) ** 2 + (bc.y - d.y) ** 2) ** 0.5
                for d in defenders
            )
            if nearest > 8.0:
                return "MOMENTUM"

        return "ATTACK"
