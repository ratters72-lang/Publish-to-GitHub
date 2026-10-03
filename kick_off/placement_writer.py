"""Kickoff placement writer — consumes the FB placement stamp (2026-08-11).

ORDERING BARRIER
----------------
The kickoff FB selection flow is:

    select FB  ->  emit world.fb_placement_stamp  ->  apply(world)  ->  movement/render

The stamp is emitted IMMEDIATELY after selection (inside
KickoffEngine._assign_kickoff_fullback), then this writer consumes it
synchronously BEFORE any movement or render consumer reads positions.
The placement is one-shot per sequence: ``apply()`` hard-places the actor,
zeroes its velocity, pins its target, publishes the world fullback fields,
and clears the stamp — so a second call in the same sequence is a no-op and
a re-selection within the same frame can never race a consumer.

The hard-place is legal here because it runs during the KICKOFF setup /
first in-flight frame, when all kicking-team players are still frozen and
no physics integration has consumed positions yet.
"""


def apply(world):
    """Consume ``world.fb_placement_stamp`` and hard-place the fullback.

    One-shot per sequence: once applied, ``fb_placement_applied`` is True
    and the stamp is cleared, so later calls are no-ops.  Returns True if
    a placement was applied this call, False otherwise.

    The stamp is a dict::

        {"player_id": int, "x": float, "y": float}

    The actor is located by ``player_id`` from ``world.kicking_team`` (the
    fullback is always a kicking-team defender).
    """
    stamp = getattr(world, "fb_placement_stamp", None)
    if not stamp:
        return False
    if getattr(world, "fb_placement_applied", False):
        # Already placed this sequence — never re-place mid-flight.
        world.fb_placement_stamp = None
        return False

    player_id = stamp.get("player_id")
    tx = stamp.get("x")
    ty = stamp.get("y")
    if player_id is None or tx is None or ty is None:
        world.fb_placement_stamp = None
        return False

    actor = None
    for p in getattr(world, "kicking_team", ()) or ():
        if getattr(p, "player_id", None) == player_id:
            actor = p
            break
    if actor is None:
        world.fb_placement_stamp = None
        return False

    # Hard-place the actor at the stamp position.
    actor.x = tx
    actor.y = ty
    actor.vx = 0.0
    actor.vy = 0.0
    actor.vel_x = 0.0
    actor.vel_y = 0.0
    actor.target_x = tx
    actor.target_y = ty

    # Publish the world fullback fields in lockstep with the placement so
    # every later consumer (audit, render, movement) reads the placed anchor.
    world.fullback_actor_id = actor.player_id
    world.fullback_anchor_x = tx
    world.fullback_anchor_y = ty

    world.fb_placement_applied = True
    world.fb_placement_stamp = None
    return True
