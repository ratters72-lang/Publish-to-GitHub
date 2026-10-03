"""
Fixes for OpenPlayEngine to ensure TACKLE_CONTACT always transitions through to completion.
"""
import sys
sys.path.insert(0, 'c:/Users/User/Desktop/rlm')

from world.open_play_engine import OpenPlayEngine
from world.world_state import GameSubState

# Monkey-patch methods we want to add safety guards to

def _update_tackle_contact_patched(self, dt: float):
    """Patched version with max-duration fallback."""
    ctx = self._build_tackle_context(dt)
    
    # ═══════════════════════════════════════════════════════════
    # MAX-DURATION FALLBACK for TACKLE_CONTACT
    #
    # If the tackle pipeline has been in contact for more than 2
    # seconds, force-complete the tackle.  This prevents the
    # engine from deadlocking in TACKLE_CONTACT if TES never
    # reaches TACKLE_COMPLETE (e.g. timer math edge case, stats
    # all zeros, etc.).
    #
    # Without this guard, a single frame glitch that prevents
    # TES from dispatching TACKLE_COMPLETE creates a permanent
    # deadlock.
    # ═══════════════════════════════════════════════════════════
    max_contact_duration = 2.0  # seconds before force-complete
    
    if not hasattr(self, '_contact_timer'):
        self._contact_timer = 0.0
    
    # Reset contact timer if we just entered TACKLE_CONTACT this frame
    # (We check if pipeline was in a non-engaging state on the previous frame)
    if self.tackle_pipeline.phase in ('idle', 'approaching'):
        pass  # Keep existing timer — handled by condition below
    
    result = self.tackle_pipeline.execute(ctx)
    
    if result.get("tackle_complete"):
        # Success path — normal transition
        self._contact_timer = 0.0
        self._apply_tackle_complete(result)
    else:
        # Not complete — check if we've been stuck too long
        pipe = self.tackle_pipeline
        if pipe.phase in ('engaging',) or (hasattr(pipe.tes, 'phase') and 
            pipe.tes.phase.value in ('dominance_settling', 'tackle_complete', 'contact')):
            self._contact_timer += dt
        
        # ═══ FORCE-COMPLETE after max_duration ═══
        if self._contact_timer >= max_contact_duration and not result.get("tackle_complete"):
            print(f"⚠ FORCE-COMPLETE TACKLE after {self._contact_timer:.1f}s in TACKLE_CONTACT")
            self._contact_timer = 0.0
            # Build emergency outcome
            result["tackle_complete"] = True
            result["ruck_x_m"] = self.world.ruck_x_m
            result["ruck_y_m"] = self.world.ruck_y_m
            result["tackle_number"] = self.world.tackle_count + 1
            self._apply_tackle_complete(result)
    
    # Always run defence updates + player movement for non-frozen
    self.defence_controller.update(self.world, self.world.defending_team)
    for p in self.world.attacking_team + self.world.defending_team:
        if not p.frozen:
            p.update(dt)

def _apply_tackle_complete(self, result: dict):
    """Extract the tackle-complete transitions into a helper method."""
    import math
    from player.player import PlayerState, DefenderRole
    from world.defence_utils import assign_markers_and_retreat
    
    self.world.set_ruck(
        result.get("ruck_x_m", self.world.ruck_x_m),
        result.get("ruck_y_m", self.world.ruck_y_m),
        "TACKLE_COMPLETE"
    )
    self.world.tackle_count = result.get("tackle_number", self.world.tackle_count + 1)

    bc = self.ball_carrier
    if bc:
        bc.vx = 0.0; bc.vy = 0.0
        bc.state = PlayerState.TACKLED
        self.world.freeze_bc("TACKLE_PIPELINE")
        bc.set_pos(self.world.ruck_x_m, self.world.ruck_y_m, tag="TACKLE_COMPLETE")
        ball = getattr(self.world, "ball", None)
        if ball is not None:
            ball.x_m = self.world.ruck_x_m
            ball.y_m = self.world.ruck_y_m
            ball.vx = 0.0
            ball.vy = 0.0

    self.world.ruck_cycle.start()
    self._ruck_settle_timer = 0.0
    self._ptb_started = False
    self._ptb_timer = 0.0
    assign_markers_and_retreat(self.world)

    # Snap markers to clean positions
    for d in self.world.defending_team:
        if d.defender_role == DefenderRole.MARKER_A:
            d.x = self.world.ruck_x_m + 1.0
            d.y = self.world.ruck_y_m - 0.0
            d.target_x = d.x
            d.target_y = d.y
            d.vx = 0.0
            d.vy = 0.0
            d.frozen = True
        elif d.defender_role == DefenderRole.MARKER_B:
            d.x = self.world.ruck_x_m + 2.5
            d.y = self.world.ruck_y_m + 0.0
            d.target_x = d.x
            d.target_y = d.y
            d.vx = 0.0
            d.vy = 0.0
            d.frozen = True

    # ═══ TACKLE RESOLVE SNAPSHOT ═══
    _ma = next((d for d in self.world.defending_team if d.defender_role == DefenderRole.MARKER_A), None)
    _mb = next((d for d in self.world.defending_team if d.defender_role == DefenderRole.MARKER_B), None)
    ma_str = f"({_ma.x:.2f},{_ma.y:.2f})" if _ma else "(?,?)"
    mb_str = f"({_mb.x:.2f},{_mb.y:.2f})" if _mb else "(?,?)"
    print(
        f"TACKLE_RESOLVE: BC=({bc.x:.2f},{bc.y:.2f}) "
        f"ruck=({self.world.ruck_x_m:.2f},{self.world.ruck_y_m:.2f}) "
        f"markerA={ma_str} markerB={mb_str} "
        f"state={self.world.game_state}/{self.world.sub_state}"
    )

    self.world.set_sub_state(GameSubState.RUCK_FORMING, "TACKLE_RESOLVE")

    # Clear defender targets on ruck entry
    for d in self.world.defending_team:
        if not d.frozen:
            d.target_x = d.x
            d.target_y = d.y
            d.vx = 0.0
            d.vy = 0.0


def apply_patches():
    """Apply all safety patches to OpenPlayEngine."""
    OpenPlayEngine._update_tackle_contact = _update_tackle_contact_patched
    OpenPlayEngine._apply_tackle_complete = _apply_tackle_complete
    
    # Also patch PTB to have a fallback
    orig_ptb = OpenPlayEngine._update_ptb
    def _update_ptb_patched(self, dt):
        """Patched PTB with MAX duration fallback."""
        max_ptb_duration = 5.0  # seconds
        if not hasattr(self, '_ptb_max_timer'):
            self._ptb_max_timer = 0.0
        
        # Call original
        orig_ptb(self, dt)
        
        # Track PTB duration
        if self.world.sub_state == GameSubState.PTB:
            self._ptb_max_timer += dt
            if self._ptb_max_timer >= max_ptb_duration:
                print(f"⚠ FORCE PTB COMPLETE after {self._ptb_max_timer:.1f}s")
                self._ptb_max_timer = 0.0
                self._force_ptb_complete()
        else:
            self._ptb_max_timer = 0.0
    
    OpenPlayEngine._update_ptb = _update_ptb_patched
    
    # Patch ruck_forming with fallback
    orig_ruck = OpenPlayEngine._update_ruck_forming
    def _update_ruck_forming_patched(self, dt):
        """Patched RUCK_FORMING with MAX duration fallback."""
        max_ruck_duration = 5.0  # seconds
        if not hasattr(self, '_ruck_max_timer'):
            self._ruck_max_timer = 0.0
        
        # Call original
        orig_ruck(self, dt)
        
        # Track ruck forming duration
        if self.world.sub_state == GameSubState.RUCK_FORMING:
            self._ruck_max_timer += dt
            if self._ruck_max_timer >= max_ruck_duration:
                print(f"⚠ FORCE RUCK COMPLETE after {self._ruck_max_timer:.1f}s")
                self._ruck_max_timer = 0.0
                self._ptb_started = True
                self._ptb_timer = 0.0
                self.dh_controller.start_pickup()
                self.world.set_sub_state(GameSubState.PTB, "RUCK_FORCE")
        else:
            self._ruck_max_timer = 0.0
    
    OpenPlayEngine._update_ruck_forming = _update_ruck_forming_patched
    
    # Patch post-ruck with fallback
    orig_post = OpenPlayEngine._update_post_ruck_open_play
    def _update_post_ruck_open_play_patched(self, dt):
        """Patched POST_RUCK_OPEN_PLAY with MAX duration fallback."""
        max_post_ruck_duration = 5.0
        if not hasattr(self, '_post_ruck_max_timer'):
            self._post_ruck_max_timer = 0.0
        
        orig_post(self, dt)
        
        if self.world.sub_state == GameSubState.POST_RUCK_OPEN_PLAY:
            self._post_ruck_max_timer += dt
            if self._post_ruck_max_timer >= max_post_ruck_duration:
                print(f"⚠ FORCE POST_RUCK → OPEN_PLAY after {self._post_ruck_max_timer:.1f}s")
                self._post_ruck_max_timer = 0.0
                self.world.game_state = GameWorldState.OPEN_PLAY
                self.world.set_sub_state(GameSubState.OPEN_PLAY, "POST_RUCK_FORCE")
                self.world.ruck_cycle.reset()
                self._post_ruck_timer = 0.0
        else:
            self._post_ruck_max_timer = 0.0
    
    from world.world_state import GameWorldState
    OpenPlayEngine._update_post_ruck_open_play = _update_post_ruck_open_play_patched


if __name__ == '__main__':
    print("Patch module loaded. Call apply_patches() to install safety guards.")
