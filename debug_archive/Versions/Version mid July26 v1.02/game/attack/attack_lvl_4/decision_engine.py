import math

class DecisionEngine:
    def __init__(self, world):
        self.world = world

    def update(self, dt: float) -> None:
        bc = self.world.ball_carrier
        if bc is None:
            return

        # Find nearest defender
        closest_d = None
        closest_dist = 9999.0
        for d in self.world.defending_team:
            dist = math.hypot(bc.x - d.x, bc.y - d.y)
            if dist < closest_dist:
                closest_dist = dist
                closest_d = d

        # Phase 3: simple, stable intent logic
        if closest_dist < 5.0:
            bc.intent = "PASS"
        elif closest_dist < 10.0:
            bc.intent = "KICK"
        else:
            bc.intent = "RUN"
