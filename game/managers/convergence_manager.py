"""convergence_manager.py -- ConvergenceManager: sole BC-closing authority
(STAGE 3D).

One macro brain for defensive CONVERGENCE -- every writer that aims a
defender at the ball carrier (structured-close step-10 targets, MMDL
CHASE/SLIDE/DRIFT mode targets, tackler-commit forward collision points)
converges here under DefenceManager ownership, mirroring the Stage 3B/3C
law: managers own decisions, players execute movement only.

Authority contracts:
  C0 -- PURE COMPUTE + PUBLISH.  ``apply()`` never writes
       ``d.target_x`` / ``d.target_y`` itself; it publishes
       ``world.frame_convergence_targets = {id(d): (tx, ty)}`` for the
       execution layer to consume.  One dict per frame, one target per
       defender -- dual-write fights become structurally impossible.
  C1 -- FRAME-DICT CONSUMER.  Where line-shape inputs are needed the
       manager reads the published frame spacing / width dicts (Stage 3C
       authority outputs); it NEVER recomputes spacing or width.
  C2 -- TES FREEZE COMPLIANCE.  Frozen defenders never receive a
       convergence entry.
  C3 -- ROLE GATE.  Only PRIMARY/SECONDARY closers (tackle_role) are
       eligible; markers/DH/fullback/TES participants are exempt (their
       own writers own their targets).

The offsets mirror the DBU tackler-commit contract so behaviour is
unchanged when writers route through this manager.
"""

import math

__all__ = [
    "ConvergenceManager",
    "PRIMARY_X_OFFSET_M",
    "SECONDARY_X_OFFSET_M",
    "SECONDARY_BAND_Y_OFFSET_M",
]

# BC-forward collision offsets (identical to DBU tackler-commit constants).
PRIMARY_X_OFFSET_M: float = 1.0    # PRIMARY closes 1 m ahead of the carrier
SECONDARY_X_OFFSET_M: float = 2.0  # SECONDARY closes 2 m ahead
# Lateral offset applied to SECONDARY closers by persistent depth_side.
SECONDARY_BAND_Y_OFFSET_M: float = 0.5


class ConvergenceManager:
    """Sole compute+publish authority for ball-carrier convergence targets.

    Owned by DefenceManager (one instance per defending side).  Pure
    compute: ``apply()`` publishes ``world.frame_convergence_targets``
    and returns it; it never writes defender attributes directly.
    """

    def __init__(self, tuners):
        self.tuners = tuners

    # ------------------------------------------------------------------
    # READ API (pure consumers)
    # ------------------------------------------------------------------

    def read_target(self, world, d):
        """Frame convergence point for *d*, or None when not committed."""
        dct = getattr(world, "frame_convergence_targets", None) or {}
        return dct.get(id(d))

    def is_committed(self, world, d) -> bool:
        """True when *d* holds an authoritative convergence entry."""
        return self.read_target(world, d) is not None

    # ------------------------------------------------------------------
    # APPLY -- compute + publish (pure)
    # ------------------------------------------------------------------

    def apply(self, world, defenders, bc):
        """Compute + publish BC-convergence points for committed closers.

        Args:
            world: WorldState (frame dicts published onto it).
            defenders: Iterable of Player objects forming the defence.
            bc: Ball carrier (or None).

        Returns the published ``{id(d): (tx, ty)}`` dict.
        """
        targets: dict = {}
        if bc is not None:
            for d in defenders:
                if d is None or d is bc:
                    continue
                if getattr(d, "frozen", False):
                    continue                      # C2: frozen never moves
                # C3 — closers only: a defender is convergence-eligible when
                # EITHER the lane system committed it (tackle_role) OR the
                # ranked depth classifier promoted it (depth_class).  One
                # dict serves tackler-commit, structured-close AND MMDL.
                role = getattr(d, "tackle_role", "PASSIVE")
                dclass = getattr(d, "depth_class", "PASSIVE")
                lane_committed = role in ("PRIMARY", "SECONDARY")
                class_committed = dclass in ("PRIMARY", "SECONDARY")
                if not (lane_committed or class_committed):
                    continue                      # C3: closers only
                if getattr(d, "fullback_role_active", False):
                    continue                      # FB exclusion (engine law)

                ahead = d.x > bc.x
                primary = lane_committed and role == "PRIMARY"
                if not lane_committed:
                    primary = dclass == "PRIMARY"
                # FRONT-HOLD (2026-10-03): a defender the carrier is running
                # TOWARD (it sits AHEAD of the carrier) holds the corridor in
                # FRONT of the carrier on the carrier's own lane instead of
                # back-pedalling to bc.x - offset (behind the carrier).  The
                # body holds its ground; the corridor authority
                # (pre_tackle_authority) then owns the final approach and
                # plants it flat-footed.  Gated by the shared master tuner so
                # the legacy geometry is byte-identical when off.
                if ahead and bool(getattr(
                    self.tuners, "pretackle_front_hold_enabled", True
                )):
                    front = float(getattr(
                        self.tuners, "pretackle_front_offset_m", 0.6))
                    bracket = float(getattr(
                        self.tuners, "pretackle_front_lateral_offset_m", 0.3))
                    side = getattr(d, "depth_side", None)
                    _sd = (side if side in (-1, 1)
                           else (-1 if d.y < bc.y else 1))
                    tx = max(float(d.x), float(bc.x) + front)
                    ty = float(bc.y) + bracket * _sd
                elif primary:
                    tx = bc.x - (PRIMARY_X_OFFSET_M if ahead
                                 else -PRIMARY_X_OFFSET_M)
                    ty = bc.y
                else:
                    tx = bc.x - (SECONDARY_X_OFFSET_M if ahead
                                 else -SECONDARY_X_OFFSET_M)
                    side = getattr(d, "depth_side", None)
                    ty = bc.y + (SECONDARY_BAND_Y_OFFSET_M * side
                                 if side in (-1, 1) else 0.0)

                # Guard against NaN/inf targets (would corrupt movement).
                if not all(
                    isinstance(v, (int, float)) and math.isfinite(v)
                    for v in (tx, ty)
                ):
                    tx, ty = bc.x, bc.y

                targets[id(d)] = (tx, ty)

        world.frame_convergence_targets = targets
        return targets
