class SupportRunnerIntent:
    """Support-runner behaviour (Stage 2 reconnect).

    Previously an unimplemented stub returning ``"SUPPORT"`` unconditionally.
    Consumed by AttackManager.update() when AttackStructureTuners.
    anticipation_enabled is on, so support runners make a forward-looking
    posture choice instead of pure chase.

    Output is stamped on ``runner._anticipation_intent``.  Execution of the
    chosen lane is the existing support authority's job (structured lanes or
    legacy chase); this layer only decides *what the runner is doing* and
    hands it to the Stage 7 decision engine.

    Postures:
      FLAT   — sit shallow to take an offload when the BC is pressured.
      SWEEP  — stay wide/deep as a sideline outlet when the BC is near a
               touchline.
      SUPPORT— standard support depth behind the BC (the common case).
      RUN    — break into open space when there is no pressure.
    """

    def decide(self, runner, bc, per):
        # Nearest defender to the BC determines the support posture.
        d = per.get("nearest_defender")
        try:
            dist = float(per.get("nearest_defender_dist", 99.0))
        except (TypeError, ValueError):
            dist = 99.0
        try:
            sideline = float(per.get("sideline_dist", 35.0))
        except (TypeError, ValueError):
            sideline = 35.0

        # FLAT: the BC is being hit — stay shallow for the offload.
        if d is not None and dist < 5.0:
            return "FLAT"
        # SWEEP: the BC is running toward a touchline — hold the wide outlet.
        if sideline < 8.0:
            return "SWEEP"
        # RUN: the BC is in clear open space — break for a support line.
        if d is None or dist > 12.0:
            return "RUN"
        return "SUPPORT"
