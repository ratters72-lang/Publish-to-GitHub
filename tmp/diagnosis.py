"""Diagnosis summary of the POST_RUCK freeze."""

print("""
OBSERVATION:
  - Fix #1 (gate) was applied: POST_RUCK_OPEN_PLAY now allowed in attack_controller.py
  - Fix #4 (velocity zero) was applied: bc.vx = bc.vy = 0 removed on transition
  - Freeze STILL persists

ROOT CAUSE:
  _update_post_ruck_open_play() NEVER calls apply_vector() for the BC.
  It only calls it ONCE on the transition to OPEN_PLAY:
  
      if all_compliant or self._phase_timer >= max_retreat_time:
          self._post_ruck_marker_cleanup()
          bc = self.world.ball_carrier
          if bc is not None:
              self.attack_controller.apply_vector(bc, ...)  # <-- only on EXIT
          self._set_phase("OPEN_PLAY", ...)
  
  During the phase (up to 2s), NO code moves the BC:
  - _update_defence only calls Player.update(dt) for defending_team (not BC)
  - POST_RUCK handler only does marker freeze + defender retreat
  - No per-frame apply_vector() call exists

COMPARISON:
  _update_ptb():      calls apply_vector(dh, ...) every frame  --> DH moves
  _update_open_play(): calls apply_vector(bc, ...) every frame --> BC moves
  _update_post_ruck_open_play(): NEVER per frame               --> BC freezes

FIX:
  Add `self.attack_controller.apply_vector(bc, ...)` every frame
  in _update_post_ruck_open_play(), exactly like _update_ptb and _update_open_play do.
""")
