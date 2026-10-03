"""orders.py -- Macro-manager order contract (Stage 2 foundation).

The single write path between the macro brains (DefenceManager /
AttackManager) and the player executors.

STAGE 2 (this landing): managers stamp ORDER TAGS via apply_order() so
every manager-issued write is attributable in the movement-writer audit.
Subsystems keep their rich internal interfaces; the managers call them
at the exact frame positions they previously ran inline.

STAGE 4 (later): every non-physics target/mode/speed write migrates onto
explicit PlayerOrder objects routed through apply_order(). Players then
accept roles, target positions, movement modes and speeds -- nothing
else. No thinking, no heuristics, no independent scripts.

Sole-authority audit rule:
  During OPEN_PLAY any defensive target write whose player.movement_writer
  tag is not "manager:defence*" is a SOLE-AUTHORITY VIOLATION. Same for
  "manager:attack*" on the attacking side. New behaviour outside the
  owning manager fails review automatically.
"""

from dataclasses import dataclass
from typing import Optional

# Order tags stamped into player.movement_writer by each manager.
ORDER_TAG_DEFENCE = "manager:defence"
ORDER_TAG_ATTACK = "manager:attack"


@dataclass
class PlayerOrder:
    """One macro order for one player executor.

    Fields left as None are "no change" -- apply_order() only writes
    what the issuing manager explicitly commanded.
    """

    role: Optional[object] = None        # DefenderRole / depth-role string
    target_x: Optional[float] = None     # ordered X position (metres)
    target_y: Optional[float] = None     # ordered Y position (metres)
    speed_mps: Optional[float] = None    # ordered move speed (m/s)
    frozen: Optional[bool] = None        # freeze command (OPE remains the
                                         # sole freeze authority at phase
                                         # boundaries; this field exists for
                                         # Stage 4 order migration)


def apply_order(player, order: Optional[PlayerOrder] = None,
                tag: str = ORDER_TAG_DEFENCE, **fields) -> None:
    """Apply a macro order to a player executor (single write path).

    Accepts either a PlayerOrder instance or keyword fields (or both;
    keywords win). Only provided fields are written. Stamps
    ``player.movement_writer`` with the issuing manager tag so the
    sole-authority audit can attribute every write to its manager.

    This function NEVER integrates physics and NEVER decides anything --
    it is the last step of macro->micro control.
    """
    if order is not None:
        for name in ("role", "target_x", "target_y", "speed_mps", "frozen"):
            value = getattr(order, name)
            if value is not None and name not in fields:
                fields[name] = value

    role = fields.get("role")
    if role is not None:
        player.defender_role = role

    tx = fields.get("target_x")
    if tx is not None:
        player.target_x = float(tx)

    ty = fields.get("target_y")
    if ty is not None:
        player.target_y = float(ty)

    spd = fields.get("speed_mps")
    if spd is not None:
        player.move_speed = float(spd)

    frozen = fields.get("frozen")
    if frozen is not None:
        player.frozen = bool(frozen)

    player.movement_writer = tag
