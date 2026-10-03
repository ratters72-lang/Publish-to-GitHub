import math


class RunTuning:
    # SIDELINE
    SIDELINE_WEIGHT = 0.55
    SIDELINE_CURVE = 1.25   # exponent for danger curve

    # DEFENDER AVOIDANCE
    DEF_FRONT_WEIGHT = 0.85
    DEF_SIDE_WEIGHT = 0.45
    DEF_FRONT_CURVE = 1.4
    DEF_SIDE_CURVE = 1.1

    # LANES
    LANE_WEIGHT = 0.35
    LANE_CURVE = 1.2

    # GAPS
    GAP_WEIGHT = 0.75
    GAP_CURVE = 1.35

    # BACKWARD PENALTY
    BACKWARD_WEIGHT = 1.3
    BACKWARD_CURVE = 1.5

    # MOMENTUM
    MOMENTUM_SPEED_CURVE = 1.3
    MOMENTUM_ACCEL_CURVE = 1.4

    # How momentum affects behaviours
    MOMENTUM_GAP_BOOST = 0.6
    MOMENTUM_AVOID_REDUCTION = 0.5
    MOMENTUM_LANE_STABILITY = 0.4


def apply_curve(value, exponent):
    if value <= 0:
        return 0.0
    return value ** exponent


class RunLogic:
    """BC steering behaviour: blends geometry into a single run vector.

    Phase-4C-3: Pure behaviour layer — no smoothing, no inertia, no state.
    Smoothing is applied by AttackController.apply_vector().
    """

    def compute_run_vector(self, bc, geom):
        """
        Blend all geometry layers into a unit movement vector.

        Parameters
        ----------
        bc : Player
            The ball carrier (used only for context, no mutation).
        geom : dict
            Pre-computed geometry with keys:
                "forward": { forward_vec, backward_penalty }
                "sideline": { sideline_danger, sideline_angle }
                "def_cone": { def_front, def_side, nearest_def_angle }
                "lane": { lane_score, offset_y, left_lane_vec, right_lane_vec }
                "gap": { gap_angle, gap_score }

        Returns
        -------
        (float, float)
            Normalised unit vector (run_x, run_y).
        """

        # === Momentum extraction ===
        vx, vy = bc.vx, bc.vy
        speed = (vx*vx + vy*vy) ** 0.5
        accel = bc.accel if hasattr(bc, "accel") else 0.0

        # Normalised momentum factors
        speed_factor = apply_curve(min(speed / 7.0, 1.0), RunTuning.MOMENTUM_SPEED_CURVE)
        accel_factor = apply_curve(min(max(accel, 0.0) / 4.0, 1.0), RunTuning.MOMENTUM_ACCEL_CURVE)

        # === 1. Base forward vector ===
        fx, fy = geom["forward"]["forward_vec"]
        run_x = fx
        run_y = fy

        # === 2. Sideline avoidance ===
        sd = apply_curve(geom["sideline"]["sideline_danger"], RunTuning.SIDELINE_CURVE)
        sx, sy = geom["sideline"]["sideline_angle"]
        run_x += RunTuning.SIDELINE_WEIGHT * sd * sx
        run_y += RunTuning.SIDELINE_WEIGHT * sd * sy

        # === 3. Defender cone avoidance ===
        if geom["def_cone"]["def_front"]:
            ax, ay = geom["def_cone"]["nearest_def_angle"]
            strength = apply_curve(1.0, RunTuning.DEF_FRONT_CURVE)

            # High speed → shallower avoidance
            strength *= (1.0 - RunTuning.MOMENTUM_AVOID_REDUCTION * speed_factor)

            run_x -= RunTuning.DEF_FRONT_WEIGHT * strength * ax
            run_y -= RunTuning.DEF_FRONT_WEIGHT * strength * ay
        elif geom["def_cone"]["def_side"]:
            ax, ay = geom["def_cone"]["nearest_def_angle"]
            strength = apply_curve(1.0, RunTuning.DEF_SIDE_CURVE)

            strength *= (1.0 - RunTuning.MOMENTUM_AVOID_REDUCTION * speed_factor)

            run_x -= RunTuning.DEF_SIDE_WEIGHT * strength * ax
            run_y -= RunTuning.DEF_SIDE_WEIGHT * strength * ay
        # def_back ignored — no jitter

        # === 4. Lane drift ===
        lane_score = apply_curve(geom["lane"]["lane_score"], RunTuning.LANE_CURVE)

        # High speed → less lane drift (stability)
        lane_score *= (1.0 - RunTuning.MOMENTUM_LANE_STABILITY * speed_factor)

        offset_y = geom["lane"]["offset_y"]

        if offset_y < 0:
            lx, ly = geom["lane"]["right_lane_vec"]  # drift down
        else:
            lx, ly = geom["lane"]["left_lane_vec"]   # drift up

        run_x += RunTuning.LANE_WEIGHT * lane_score * lx
        run_y += RunTuning.LANE_WEIGHT * lane_score * ly

        # === 5. Gap targeting ===
        gscore = apply_curve(geom["gap"]["gap_score"], RunTuning.GAP_CURVE)

        # Acceleration → harder gap commit
        gscore += RunTuning.MOMENTUM_GAP_BOOST * accel_factor
        gscore = min(gscore, 1.0)

        gx, gy = geom["gap"]["gap_angle"]
        run_x += RunTuning.GAP_WEIGHT * gscore * gx
        run_y += RunTuning.GAP_WEIGHT * gscore * gy

        # === 6. Backwards penalty ===
        bp = apply_curve(geom["forward"]["backward_penalty"], RunTuning.BACKWARD_CURVE)
        run_x += RunTuning.BACKWARD_WEIGHT * bp  # push forward

        # === 7. Normalise ===
        mag = (run_x * run_x + run_y * run_y) ** 0.5
        if mag > 0:
            run_x /= mag
            run_y /= mag

        return (run_x, run_y)
