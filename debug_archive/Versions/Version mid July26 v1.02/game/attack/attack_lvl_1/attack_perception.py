class AttackPerception:
    """High-level perception: combines geometry into actionable facts."""

    def __init__(self, geometry):
        self.geometry = geometry

    def compute(self, bc):
        sideline_dist = self.geometry.compute_sideline_distance(bc)
        tryline_dist = self.geometry.compute_tryline_distance(bc)
        nearest_def, nearest_dist = self.geometry.nearest_defender(bc)
        gap_vec = self.geometry.gap_vector()

        return {
            "sideline_dist": sideline_dist,
            "tryline_dist": tryline_dist,
            "nearest_defender": nearest_def,
            "nearest_defender_dist": nearest_dist,
            "gap_vector": gap_vec,
        }
