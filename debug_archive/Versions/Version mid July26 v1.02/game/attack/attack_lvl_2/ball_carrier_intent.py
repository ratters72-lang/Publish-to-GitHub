class BallCarrierIntent:
    """Decides RUN / PASS / KICK based on perception."""

    def decide(self, bc, per):
        d = per["nearest_defender"]
        dist = per["nearest_defender_dist"]

        if d and dist < 5.0:
            return "PASS"
        if d and dist < 10.0:
            return "KICK"
        return "RUN"
