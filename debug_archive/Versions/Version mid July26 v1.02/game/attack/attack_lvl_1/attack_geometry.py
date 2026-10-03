class AttackGeometry:
    """Pure geometric facts about field, defenders, gaps, sidelines."""

    def __init__(self, ope):
        self.ope = ope
        self.world = ope.world

    def compute_sideline_distance(self, bc):
        return min(bc.y, 70 - bc.y)

    def compute_tryline_distance(self, bc):
        return 110 - bc.x

    def nearest_defender(self, bc):
        closest = None
        cd = 9999.0
        for d in self.world.defending_team:
            dist = ((bc.x - d.x)**2 + (bc.y - d.y)**2)**0.5
            if dist < cd:
                cd = dist
                closest = d
        return closest, cd

    def gap_vector(self):
        tes = getattr(self.ope, "tes_ctx", None)
        return getattr(tes, "gap_vector", None)

    # ------------------------------------------------------------------
    # Phase‑4C‑1  Sideline Geometry
    # ------------------------------------------------------------------
    def sideline_geometry(self, bc):
        """Measure proximity to both sidelines and compute inward push.

        Sidelines are at y=0 (top) and y=70 (bottom).
        Returns normalised danger (0 = safe, 1 = edge) and an inward
        unit vector so BC never runs off-field (hard clamp).
        Tackle/catch can override via soft clamp.
        """
        FIELD_WIDTH = 70

        # Distance to nearest sideline
        dist_top = bc.y                      # distance to y=0
        dist_bottom = FIELD_WIDTH - bc.y      # distance to y=70
        sideline_dist = min(dist_top, dist_bottom)

        # Sideline danger score (0 = safe → 1 = danger)
        SAFE_CORRIDOR = 8.0   # metres of safe space from the edge
        sideline_danger = max(0.0, 1.0 - sideline_dist / SAFE_CORRIDOR)

        # Sideline angle — push toward the centre of the field (y=35)
        if dist_top < dist_bottom:
            # Closer to top sideline → push DOWN (+Y)
            sideline_angle = (0.0, 1.0)
        else:
            # Closer to bottom sideline → push UP (-Y)
            sideline_angle = (0.0, -1.0)

        return {
            "sideline_dist": sideline_dist,
            "sideline_danger": sideline_danger,
            "sideline_angle": sideline_angle,
        }

    # ------------------------------------------------------------------
    # Phase‑4C‑1  Defender Cone Geometry
    # ------------------------------------------------------------------
    def defender_cone(self, bc, defenders):
        """Classify the nearest defender's position relative to BC.

        Forward direction is +X.  Defenders behind BC are ignored
        (no jitter).  Returns distance, angle (normalised), and
        front/side/back booleans so RunLogic can angle away from
        frontal threats.
        """
        nearest = None
        nearest_dist = 999.0
        nearest_angle = (0.0, 0.0)

        for d in defenders:
            dx = d.x - bc.x
            dy = d.y - bc.y
            dist = (dx * dx + dy * dy) ** 0.5
            if dist < nearest_dist:
                nearest = d
                nearest_dist = dist
                nearest_angle = (dx / dist, dy / dist)

        # Cone classification (BC forward is +X)
        front = nearest_angle[0] > 0.3    # defender in front
        side = -0.3 <= nearest_angle[0] <= 0.3   # defender to the side
        back = nearest_angle[0] < -0.3    # defender behind

        return {
            "nearest_def": nearest,
            "nearest_def_dist": nearest_dist,
            "nearest_def_angle": nearest_angle,
            "def_front": front,
            "def_side": side,
            "def_back": back,
        }

    # ------------------------------------------------------------------
    # Phase‑4C‑1  Forward Corridor Geometry
    # ------------------------------------------------------------------
    def forward_corridor(self, bc):
        """Measure forward alignment and compute a backward penalty.

        Forward direction is +X (toward the right tryline at x=110).
        BC gets a backward penalty when its velocity points in the
        -X direction.  This prevents BC from running backwards except
        in rare forced cases (sideline trap + defender front).
        """
        # Forward direction is +X
        forward_vec = (1.0, 0.0)

        # Backwards penalty (BC should not run backwards)
        vx, vy = bc.vx, bc.vy
        speed = (vx * vx + vy * vy) ** 0.5
        if speed > 0:
            dir_x = vx / speed
            backward_penalty = max(0.0, -dir_x)  # only if pointing backwards
        else:
            backward_penalty = 0.0

        return {
            "forward_vec": forward_vec,
            "backward_penalty": backward_penalty,
        }

    # ------------------------------------------------------------------
    # Phase-4C-2  Lane Geometry
    # ------------------------------------------------------------------
    def lane_geometry(self, bc):
        """
        Phase-4C-2: Lane geometry for BC.
        Defines left/right lanes and lane width based on field Y-axis.
        """
        TOP = 0.0
        BOTTOM = 70.0

        # Centre line of field
        centre_y = (TOP + BOTTOM) * 0.5  # 35.0

        # BC offset from centre
        offset_y = bc.y - centre_y

        # Define lane width (half-width)
        LANE_HALF_WIDTH = 8.0  # 16m total lane width

        # Left lane: above BC (toward TOP)
        left_lane_vec = (0.0, -1.0)
        # Right lane: below BC (toward BOTTOM)
        right_lane_vec = (0.0, 1.0)

        # Lane score: prefer being near centre, penalise hugging sidelines
        dist_top = bc.y - TOP
        dist_bottom = BOTTOM - bc.y
        sideline_dist = min(dist_top, dist_bottom)
        SAFE = 8.0
        sideline_penalty = max(0.0, 1.0 - sideline_dist / SAFE)

        # Centre preference: closer to centre_y is better
        centre_dist = abs(offset_y)
        CENTRE_MAX = 20.0
        centre_penalty = min(1.0, centre_dist / CENTRE_MAX)

        lane_score = 1.0 - 0.5 * sideline_penalty - 0.5 * centre_penalty

        return {
            "left_lane_vec": left_lane_vec,
            "right_lane_vec": right_lane_vec,
            "lane_half_width": LANE_HALF_WIDTH,
            "lane_score": lane_score,
            "centre_y": centre_y,
            "offset_y": offset_y,
        }

    # ------------------------------------------------------------------
    # Phase-4C-2  Gap Geometry
    # ------------------------------------------------------------------
    def gap_geometry(self, bc, defenders):
        """
        Phase-4C-2: Gap geometry for BC.
        Finds a simple 'best gap' based on nearest two defenders.
        """
        if len(defenders) < 2:
            # Not enough defenders to define a gap; fall back to forward
            return {
                "gap_angle": (1.0, 0.0),
                "gap_depth": 10.0,
                "gap_width": 10.0,
                "gap_score": 0.5,
            }

        # Sort defenders by x (forward direction)
        sorted_defs = sorted(defenders, key=lambda d: d.x)

        # Take two nearest in front of BC (or closest overall)
        d1 = sorted_defs[0]
        d2 = sorted_defs[1]

        # Midpoint between defenders = gap centre
        gap_x = (d1.x + d2.x) * 0.5
        gap_y = (d1.y + d2.y) * 0.5

        # Vector from BC to gap centre
        dx = gap_x - bc.x
        dy = gap_y - bc.y
        dist = (dx*dx + dy*dy) ** 0.5
        if dist > 0:
            gap_angle = (dx / dist, dy / dist)
        else:
            gap_angle = (1.0, 0.0)

        # Gap width = distance between defenders
        wx = d2.x - d1.x
        wy = d2.y - d1.y
        gap_width = (wx*wx + wy*wy) ** 0.5

        # Gap depth = how far forward the gap is from BC
        gap_depth = max(0.0, gap_x - bc.x)

        # Simple gap score: prefer wide, forward gaps
        WIDTH_SCALE = 20.0
        DEPTH_SCALE = 30.0
        width_score = min(1.0, gap_width / WIDTH_SCALE)
        depth_score = min(1.0, gap_depth / DEPTH_SCALE)
        gap_score = 0.5 * width_score + 0.5 * depth_score

        return {
            "gap_angle": gap_angle,
            "gap_depth": gap_depth,
            "gap_width": gap_width,
            "gap_score": gap_score,
            "gap_center": (gap_x, gap_y),
        }
