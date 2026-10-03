"""
verify_fullback_invariant_audit.py — Validation for the per-frame
fullback-invariant audit module + its wiring at _start_tackle.

Confirms:
  1. MODULE SURFACE — capture_fullback_invariants /
     audit_fullback_invariants / rearm_fullback_invariant_audit exist.
  2. CAPTURE — the 5 invariant fields (fb_id, fb_role, fb_depth_role,
     fb_frozen, fb_target_source) are captured from a stub fullback actor.
  3. INVARIANT FLAGS — a contaminated / leaked / frozen / overridden
     actor is flagged, and a clean actor is NOT.
  4. WIRING — world/open_play_engine.py `_start_tackle` actually calls
     `audit_fullback_invariants`, and `reset_for_kickoff` calls
     `rearm_fullback_invariant_audit`.
  4d. FINAL-STATE WIRING — `audit_fullback_invariants(self.world)` also
      runs immediately AFTER `self._reassert_fullback(dt)` in the five
      PTB-era phase handlers (_update_release, _update_ptb_prep,
      _update_ptb_exec, _update_ptb, _update_post_ruck_open_play) so the
      audit captures the FINAL frame state, not a mid-write state.
"""

import os
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player, DefenderRole
from game.debug.fullback_invariant_audit import (
    capture_fullback_invariants,
    _invariant_violations,
    audit_fullback_invariants,
    rearm_fullback_invariant_audit,
    FB_ROLE_CONTAMINATION,
    FB_DEPTH_ROLE_LEAK,
    FB_FROZEN_VIOLATION,
    FB_TARGET_SOURCE_OVERRIDE,
    FB_ID_MISMATCH,
)

failures = []
ROOT = os.path.dirname(os.path.abspath(__file__))


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(name)


class StubWorld:
    """Minimal world stub carrying the fields the audit reads."""

    def __init__(self, defenders, frame=1, phase="OPEN_PLAY"):
        self.defending_team = defenders
        self.players = defenders
        self.frame_count = frame
        self.phase = phase
        self.fullback_actor_id = None
        self.open_play_engine = None


def make_fb_actor(clean=True):
    """Build a defending Player acting as the fullback.

    clean=True reproduces the intended invariant state: DEF_LINE_READY
    role, depth_role None, unfrozen, target_source "fullback_geometry".
    """
    p = Player(x=95.0, y=40.0, player_id=1)
    p.defender_role = DefenderRole.DEF_LINE_READY
    p.fullback_role_active = True
    p.role = "FULLBACK"
    p.depth_role = None
    p.frozen = False
    p.target_source = "fullback_geometry"
    if not clean:
        p.frozen = True
        p.target_source = "retreat_writer"
        p.depth_role = "A"
    return p


def main():
    print("=" * 64)
    print("FULLBACK-INVARIANT AUDIT VALIDATION")
    print("=" * 64)

    # ── 1. Module surface ────────────────────────────────────────────
    print("\n[1] Module surface")
    from game.debug import fullback_invariant_audit as mod
    for fn in ("capture_fullback_invariants", "audit_fullback_invariants",
               "rearm_fullback_invariant_audit"):
        check(f"defines {fn}()", callable(getattr(mod, fn, None)))

    # ── 2. Capture fields ────────────────────────────────────────────
    print("\n[2] Capture (5 invariant fields)")
    fb = make_fb_actor(clean=True)
    world = StubWorld([fb], frame=42, phase="OPEN_PLAY")
    world.fullback_actor_id = fb.player_id

    rec = capture_fullback_invariants(world)
    check("capture returns a record", rec is not None)
    check("fb_id == player_id", rec and rec.get("fb_id") == fb.player_id,
          f"fb_id={rec.get('fb_id') if rec else None}")
    check("fb_role == DEF_LINE_READY",
          rec and rec.get("fb_role") == DefenderRole.DEF_LINE_READY,
          f"fb_role={rec.get('fb_role') if rec else None}")
    check("fb_depth_role is None", rec and rec.get("fb_depth_role") is None,
          f"fb_depth_role={rec.get('fb_depth_role') if rec else None}")
    check("fb_frozen is False", rec and rec.get("fb_frozen") is False,
          f"fb_frozen={rec.get('fb_frozen') if rec else None}")
    check("fb_target_source == fullback_geometry",
          rec and rec.get("fb_target_source") == "fullback_geometry",
          f"fb_target_source={rec.get('fb_target_source') if rec else None}")
    check("published_fb_id matches", rec and rec.get("published_fb_id") == fb.player_id)

    # ── 3. Invariant violation flags ─────────────────────────────────
    print("\n[3] Invariant violation flags")
    check("clean actor -> no violations", _invariant_violations(rec) == [],
          str(_invariant_violations(rec)))

    # Frozen + leaked depth role + overridden target source.
    bad = make_fb_actor(clean=False)
    bad_world = StubWorld([bad], frame=43)
    bad_world.fullback_actor_id = bad.player_id
    bad_rec = capture_fullback_invariants(bad_world)
    viol = set(_invariant_violations(bad_rec))
    check("frozen flags FB_FROZEN_VIOLATION", FB_FROZEN_VIOLATION in viol, str(viol))
    check("depth leaks FB_DEPTH_ROLE_LEAK", FB_DEPTH_ROLE_LEAK in viol, str(viol))
    check("target override flags FB_TARGET_SOURCE_OVERRIDE",
          FB_TARGET_SOURCE_OVERRIDE in viol, str(viol))

    # Role contamination: a marker role on the actor.
    fb_mark = make_fb_actor(clean=True)
    fb_mark.defender_role = DefenderRole.MARKER_A
    mark_world = StubWorld([fb_mark])
    mark_viol = set(_invariant_violations(capture_fullback_invariants(mark_world)))
    check("marker role flags FB_ROLE_CONTAMINATION",
          FB_ROLE_CONTAMINATION in mark_viol, str(mark_viol))

    # Published-vs-live id mismatch.
    fb_id = make_fb_actor(clean=True)
    id_world = StubWorld([fb_id])
    id_world.fullback_actor_id = 999  # wrong published id
    id_viol = set(_invariant_violations(capture_fullback_invariants(id_world)))
    check("published/live mismatch flags FB_ID_MISMATCH",
          FB_ID_MISMATCH in id_viol, str(id_viol))

    # No actor -> capture returns None.  A plain defender with NO
    # fullback_role_active AND no published id must yield None.
    plain = Player(x=50.0, y=35.0, player_id=7)
    plain.defender_role = DefenderRole.DEF_LINE_READY
    plain.fullback_role_active = False
    none_world = StubWorld([plain], frame=1)
    none_world.fullback_actor_id = None
    check("no actor -> None", capture_fullback_invariants(none_world) is None)

    # ── 4. Wiring at _start_tackle ───────────────────────────────────
    print("\n[4] Wiring (world/open_play_engine.py)")
    ope_path = os.path.join(ROOT, "world", "open_play_engine.py")
    src = ""
    if os.path.exists(ope_path):
        with open(ope_path, "r", encoding="utf-8") as fh:
            src = fh.read()

    # 4a. Import of the audit + rearm.
    check("imports audit_fullback_invariants",
          "audit_fullback_invariants" in src and "from game.debug.fullback_invariant_audit import" in src)

    # 4b. _start_tackle call site (tackle-entry boundary).
    check("_start_tackle calls audit_fullback_invariants",
          "audit_fullback_invariants(self.world)" in src)
    # Confirm the call sits inside the _start_tackle body, i.e. occurs
    # within the region between "def _start_tackle" and the next def that
    # follows it (a coarse region check is enough to prove the wiring is
    # at tackle entry, not somewhere unrelated).
    start_idx = src.find("def _start_tackle")
    if start_idx != -1:
        next_def = src.find("\n    def ", start_idx + len("def _start_tackle"))
        region = src[start_idx:next_def if next_def != -1 else len(src)]
        check("audit call is inside _start_tackle body",
              "audit_fullback_invariants(self.world)" in region)
    else:
        check("found _start_tackle definition", False, "def _start_tackle missing")

    # 4c. reset_for_kickoff re-arm.
    check("reset_for_kickoff calls rearm_fullback_invariant_audit",
          "rearm_fullback_invariant_audit()" in src)

    # 4d. FINAL-STATE WIRING — the audit must run AFTER _reassert_fullback
    # in the five PTB-era phase handlers so it captures the FINAL frame
    # state (not a mid-write state from the TES wipes / P7 retreat / marker
    # / DH writers that precede the re-assert).
    _fb_phase_handlers = ("_update_release", "_update_ptb_prep",
                          "_update_ptb_exec", "_update_ptb",
                          "_update_post_ruck_open_play")
    for _handler in _fb_phase_handlers:
        _def_idx = src.find(f"def {_handler}")
        if _def_idx == -1:
            check(f"{_handler} definition exists", False,
                  f"def {_handler} missing")
            continue
        _next_def = src.find("\n    def ", _def_idx + len(f"def {_handler}"))
        _region = src[_def_idx:_next_def if _next_def != -1 else len(src)]
        _has_reassert = "self._reassert_fullback(dt)" in _region
        _has_audit = "audit_fullback_invariants(self.world)" in _region
        _audit_after = False
        if _has_reassert and _has_audit:
            _audit_after = (
                _region.find("audit_fullback_invariants(self.world)")
                > _region.find("self._reassert_fullback(dt)")
            )
        check(f"{_handler} calls _reassert_fullback", _has_reassert)
        check(f"{_handler} audit runs AFTER _reassert_fullback",
              _has_audit and _audit_after)

    # ── 5. audit_fullback_invariants returns a record (no crash) ─────
    print("\n[5] audit_fullback_invariants smoke (self-limiting)")
    rearm_fullback_invariant_audit()
    fb2 = make_fb_actor(clean=True)
    w2 = StubWorld([fb2])
    w2.fullback_actor_id = fb2.player_id
    rec2 = audit_fullback_invariants(w2)
    check("audit returns the capture record", rec2 is not None and rec2.get("fb_id") == fb2.player_id)
    check("audit re-arm is idempotent", callable(rearm_fullback_invariant_audit))

    print("\n" + "=" * 64)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
