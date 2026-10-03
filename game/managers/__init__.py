"""game.managers -- Macro decision managers (macro->micro control layer).

One macro brain per side:

  - DefenceManager  (game/managers/defence_manager.py)
      Sole authority for ALL defensive order issuance. Consolidates the
      frame preamble (depth-roles -> spacing -> width), the OPEN_PLAY
      dispatch, TES-drive line behaviour for non-tacklers, PTB-era
      retreat orders, marker assignment, fullback re-assertion, the
      tackle-state gate sweep and the ruck-adjacent pin.

  - AttackManager   (game/managers/attack_manager.py)
      Sole authority for attacking orders: ball-carrier steering
      (via AttackController) and support-runner orders.

Authority boundary (ENGINE LAW):
  Managers issue movement/role orders ONLY. They never own TES attach
  geometry, drive LERP, dominance pushback, ball-carrier/tackler
  movement during a tackle, PTBManager DH movement, phase transitions,
  freeze flags (apply_phase_invariants stays in OpenPlayEngine), or
  collision physics.

Sole-authority review rule:
  Any new defensive behaviour added OUTSIDE DefenceManager fails
  review. The movement-writer audit tags (manager:defence* /
  manager:attack*) make this machine-checkable.
"""
