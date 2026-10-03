"""KickoffEngine — owns all kickoff setup, kick, ball physics, and chase logic.

Phase 1: pure extraction from run.py with zero behavioural changes.
Phase 2: own ball physics during kickoff.
Phase 3: own chase phase (KO_CHASE).
"""

from enum import Enum, auto
from world.world_state import GameWorldState, GameSubState
from world.ruck_state import RuckState
from kick_off.kickoff_formations import (
    apply_receiving_kickoff_formation,
    apply_kicking_team_formation,
)
from player.player import Player, PlayerState, Team as PlayerTeam
from player.movement_constants import (
    MASTER_MOVEMENT_SCALER,
    VELOCITY_DELTA_MAX,
    EMA_SMOOTHING_FACTOR,
)
import math
import random


class KOPhase(Enum):
    SETUP = auto()
    KICK = auto()
    IN_AIR = auto()
    CHASE = auto()
    CATCH_ATTEMPT = auto()
    CATCH_CONFIRMED = auto()
    EXIT = auto()


class KickoffEngine:
    def __init__(self, world, ball, field):
        self.world = world
        self.ball = ball
        self.field = field
        self.phase = KOPhase.SETUP
        self.chase_timer = 0.0
        self.wait_timer = 0.0
        self.catch_delay = 0.0

    def create_teams(self):
        """Create two teams of 13 players each on the halfway line."""
        kickers = []
        receivers = []

        self.world.attacking_direction = 1
        self.world.defending_left = False

        for i in range(13):
            y = 5 + i * (self.field.width_m - 10) / 12
            p = Player(self.field.halfway_m, y, player_id=200 + i)
            p.team = PlayerTeam.KICKING
            p.facing = math.pi
            p.state = PlayerState.IDLE
            kickers.append(p)

        for i in range(13):
            y = 5 + i * (self.field.width_m - 10) / 12
            p = Player(self.field.halfway_m, y, player_id=100 + i)
            p.team = PlayerTeam.RECEIVING
            p.facing = 0
            p.state = PlayerState.IDLE
            receivers.append(p)

        self.world.kicking_team = kickers
        self.world.receiving_team = receivers
        self.world.attacking_team = receivers
        self.world.defending_team = kickers
        self.world.players = kickers + receivers

    def reset_for_kickoff(self):
        """Reset ball, world, teams, and apply formations for a new kickoff."""
        b = self.ball
        w = self.world

        # ═══ KICKOFF ISOLATION (Phase 5) ═══
        w.in_kickoff = True

        b.x_m = self.field.halfway_m
        b.y_m = self.field.centre_m
        b.z_m = 1.0
        b.vx = b.vy = b.vz = 0.0
        b.grounded = False
        b.kicked = False
        b.possessed_by = None

        # Continuous OPEN_PLAY from frame 0 — defence logic is always live
        w.game_state = GameWorldState.OPEN_PLAY
        w.sub_state = GameSubState.OPEN_PLAY
        w.ball_live = False
        w.ball_in_play = False
        w.ball_carrier = None
        w.kickoff_landing_x = 0.0
        w.kickoff_landing_y = 0.0

        w.tackle_count = 0
        w.ruck_state = RuckState.NONE
        w.ruck_timer = 0.0
        w.just_caught_this_frame = False
        w.frame_count = 0
        w.just_left_kickoff_timer = 0.0

        w.primary_catcher = None

        self.chase_timer = 0.0
        self.wait_timer = 0.0
        self.catch_delay = 0.0

        self.create_teams()

        for p in w.players:
            p.reset_for_kickoff(world=w)

        apply_receiving_kickoff_formation(w, self.field)
        apply_kicking_team_formation(w, self.field)

        for p in w.receiving_team:
            p.team = PlayerTeam.RECEIVING
        for p in w.kicking_team:
            p.team = PlayerTeam.KICKING

    def update(self, dt):
        w = self.world

        if not w.in_kickoff:
            return

        # ═══ KICKOFF ISOLATION (Phase 5) — belt-and-suspenders ═══
        w.in_kickoff = True

        if self.phase == KOPhase.SETUP:
            # SETUP does nothing yet — kickoff() handles setup
            self.phase = KOPhase.KICK

        elif self.phase == KOPhase.KICK:
            # ball has just been kicked — move to IN_AIR
            self.phase = KOPhase.IN_AIR

        elif self.phase == KOPhase.IN_AIR:
            # Minimal catcher approach: move primary toward landing point
            self._approach_landing(w.primary_catcher, dt)
            # Transition to chase when ball is descending.
            if self.ball.vz < 0:
                self.phase = KOPhase.CHASE

        elif self.phase == KOPhase.CHASE:
            self.chase_timer += dt

            # Minimal catcher approach: continue moving toward landing point
            self._approach_landing(w.primary_catcher, dt)

            primary = w.primary_catcher
            if primary is not None:
                dx = w.kickoff_landing_x - primary.x
                dy = w.kickoff_landing_y - primary.y
                dist = math.hypot(dx, dy)

                # must chase for at least 0.25s
                if dist < 0.5 and self.chase_timer >= 0.25:
                    self.phase = KOPhase.CATCH_ATTEMPT
                    self.wait_timer = 0.0

        elif self.phase == KOPhase.CATCH_ATTEMPT:
            # Minimal catcher approach: creep under the ball
            self._approach_landing(w.primary_catcher, dt, speed=2.0)
            self.wait_timer += dt

            # must wait under ball for 0.15s
            if self.wait_timer >= 0.15 and self.ball.z_m <= 0.2:
                self.phase = KOPhase.CATCH_CONFIRMED
                self.catch_delay = 0.0

        elif self.phase == KOPhase.CATCH_CONFIRMED:
            self.catch_delay += dt

            # tiny catch animation window
            if self.catch_delay >= 0.12:
                primary = w.primary_catcher
                b = self.ball

                b.possessed_by = primary
                w.ball_carrier = primary
                w.attacking_team = w.receiving_team
                w.defending_team = w.kicking_team
                w.ruck_x_m = primary.x
                w.ruck_y_m = primary.y
                w.ball_live = True
                w.ball_in_play = True

                self.phase = KOPhase.EXIT

        elif self.phase == KOPhase.EXIT:
            w.in_kickoff = False
            # game_state is already OPEN_PLAY, sub_state already OPEN_PLAY
            # No state change needed — defence was running continuously

    def kickoff(self):
        """Execute the kickoff: set formations, kick the ball.
        
        game_state stays OPEN_PLAY (set during reset_for_kickoff).
        Only ball trajectory and catcher-approach run during ball flight;
        defence uses continuous OPEN_PLAY behaviour.
        """
        w = self.world
        b = self.ball
        f = self.field

        w.game_state = GameWorldState.OPEN_PLAY
        w.sub_state = GameSubState.OPEN_PLAY

        apply_receiving_kickoff_formation(w, f)
        apply_kicking_team_formation(w, f)

        for p in w.receiving_team:
            p.team = PlayerTeam.RECEIVING
        for p in w.kicking_team:
            p.team = PlayerTeam.KICKING

        forward_dir = -1

        kick_distance = random.uniform(50.0, 60.0) * 0.9
        target_x = b.x_m + forward_dir * kick_distance
        target_y = random.uniform(10.0, f.width_m - 10.0)

        sx = b.x_m
        sy = b.y_m
        dx = target_x - sx
        dy = target_y - sy
        distance = math.hypot(dx, dy)
        direction_deg = math.degrees(math.atan2(dy, dx))

        g = 9.8
        T = 2.8
        horizontal_speed = distance / T
        vertical_speed = 0.5 * g * T
        power = math.hypot(horizontal_speed, vertical_speed)
        angle_rad = math.atan2(vertical_speed, horizontal_speed)
        angle_deg = math.degrees(angle_rad)

        angle_deg += random.uniform(-2.0, 2.0)
        power *= random.uniform(0.95, 1.05)
        power = min(power, 28.0)

        print(
            f"AI KICK: dist={distance:.2f}m, T≈{T:.2f}s, "
            f"power={power:.2f}, angle={angle_deg:.2f}, dir={direction_deg:.2f}"
        )

        b.kick(power, angle_deg, direction_deg)

        landing_x = b.landing_x_m
        landing_y = b.landing_y_m
        w.kickoff_landing_x = landing_x
        w.kickoff_landing_y = landing_y

        primary = None
        best_d2 = 999999.0
        for p in w.receiving_team:
            dx = p.x - landing_x
            dy = p.y - landing_y
            d2 = dx * dx + dy * dy
            if d2 < best_d2:
                best_d2 = d2
                primary = p

        if primary is not None:
            primary.catch_x = landing_x
            primary.catch_y = landing_y
            primary.catch_time = None
            w.primary_catcher = primary
            print(f"[KICKOFF] Primary catcher = Player #{primary.player_id}")
            print("[KICKOFF] Primary catcher set, waiting for chase phase.")

        self.phase = KOPhase.KICK
        print(f"[KICKOFF] Landing point = ({landing_x:.1f}, {landing_y:.1f})")

    def _approach_landing(self, player, dt, speed=5.0):
        """Minimal catcher-approach: lerp primary catcher toward landing point."""
        if player is None:
            return
        lx = self.world.kickoff_landing_x
        ly = self.world.kickoff_landing_y
        dx = lx - player.x
        dy = ly - player.y
        dist = math.hypot(dx, dy)
        if dist < 0.1:
            return
        nx = dx / dist
        ny = dy / dist
        player.vx = nx * speed
        player.vy = ny * speed
        player.x += player.vx * dt
        player.y += player.vy * dt
        from player.movement import update_facing_unified
        update_facing_unified(player, dt)
