class BallCarrierIntent:
    """Decides RUN / PASS / KICK / SCOOT from perception.

    STAGE 2 REBUILD (reconnect anticipation): this proxy was previously
    instantiated on OpenPlayEngine but never called — the live BC path used
    pure geometry (AttackGeometry + RunLogic).  It now consumes the
    perception dict produced by AttackPerception.compute(bc) each OPEN_PLAY
    frame, gated by AttackStructureTuners.anticipation_enabled.

    The output is stamped on ``bc._anticipation_intent`` and drives the
    SCOOT escape branch in AttackController.  PASS / KICK are recorded for
    the Stage 7 decision engine (no pass/kick execution exists yet).

    Thresholds are tuner-driven so a detached caller (no tuners) degrades
    to the documented defaults.
    """

    # Defaults (mirror AttackStructureTuners values).
    _DEFAULT_SCOOT_DIST_M = 2.0
    _DEFAULT_PASS_DIST_M = 5.0
    _DEFAULT_KICK_DIST_M = 10.0
    _DEFAULT_KICK_TRYLINE_M = 20.0

    def decide(self, bc, per, tuners=None):
        d = per.get("nearest_defender")
        try:
            dist = float(per.get("nearest_defender_dist", 99.0))
        except (TypeError, ValueError):
            dist = 99.0
        try:
            tryline = float(per.get("tryline_dist", 110.0))
        except (TypeError, ValueError):
            tryline = 110.0

        scoot_d = self._tuner(tuners, "anticip_scoot_dist_m", self._DEFAULT_SCOOT_DIST_M)
        pass_d = self._tuner(tuners, "anticip_pass_dist_m", self._DEFAULT_PASS_DIST_M)
        kick_d = self._tuner(tuners, "anticip_kick_dist_m", self._DEFAULT_KICK_DIST_M)
        kick_try = self._tuner(tuners, "anticip_kick_tryline_m", self._DEFAULT_KICK_TRYLINE_M)

        # SCOOT: inside the tight escape/offload radius + a defender on top.
        if d is not None and dist < scoot_d:
            return "SCOOT"
        # PASS: pressured but not yet in the collision window.
        if d is not None and dist < pass_d:
            return "PASS"
        # KICK: deep in the opponent half with a defender closing.
        if d is not None and dist < kick_d and tryline < kick_try:
            return "KICK"
        return "RUN"

    @staticmethod
    def _tuner(tuners, name, default):
        if tuners is None:
            return default
        v = getattr(tuners, name, None)
        return default if v is None else float(v)
