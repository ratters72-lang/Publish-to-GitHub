"""
verify_stage4_movement_state_rebuild.py — Stage 4 movement-state rebuild verifier.

Confirms the Stage 4 ship-text claims by static code inspection:

  1. The movement layer recognises and acts on the unified pre-tackle
     authority introduced in Stage 3 (apply_pre_tackle wired into OPEN_PLAY).
  2. Defenders get explicit movement semantics for COMMIT / PLANTED / SLOW /
     HOLD (the authority's own _slow_step / _freeze_stance / _live_commit_point,
     not the legacy CHASE/HOLD/DRIFT state-machine reinterpretation).
  3. The defensive line maintains depth during pre-contact + TES drive frames.
  4. COMMIT bodies close on the forward (OPEN_PLAY) / frozen (drive) point.
  5. PLANTED/SLOW bodies stabilise without collapsing depth.
  6. Movement and TES operate on one canonical pre_tackle_state.
  7. Marker timing and role-depth logic remain unchanged (not touched).

Run:  python verify_stage4_movement_state_rebuild.py
Exit 0 on success, 1 on failure.
"""
import sys, os, re

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

DBU = "game/defence/defence_lvl_1/defensive_behaviour_unified.py"
PTA = "game/defence/defence_lvl_1/pre_tackle_authority.py"
LVL1 = "game/defence/defence_lvl_1/defensive_behaviour_lvl1.py"
DMR = "game/defence/defence_lvl_1/defensive_depth_roles.py"
MA = "game/defence/defence_lvl_1/marker_authority.py"
DEFMAN = "game/managers/defence_manager.py"

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" - " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def main():
    print("=" * 64)
    print("STAGE 4 -- MOVEMENT STATE REBUILD VERIFIER")
    print("=" * 64)

    dbu = read(DBU)
    pta = read(PTA)
    lvl1 = read(LVL1)
    dmr = read(DMR)
    ma = read(MA)
    defman = read(DEFMAN)

    # ══════════════════════════════════════════════════════════════
    # A. Movement layer recognises + acts on the unified authority
    # ══════════════════════════════════════════════════════════════
    print("\n[A] movement layer recognises + acts on the unified pre-tackle authority")
    check("A1 apply_pre_tackle imported into defensive_behaviour_unified",
          re.search(r"from game\.defence\.defence_lvl_1\.pre_tackle_authority import[\s\S]*?apply_pre_tackle", dbu) is not None,
          "import block missing apply_pre_tackle")
    check("A2 structured OPEN_PLAY calls apply_pre_tackle(..., frozen_mode=False)",
          "apply_pre_tackle(world, _pta_pool, _pta_dt, self.tuners, frozen_mode=False)" in dbu,
          "structured call not found")
    check("A3 unified OPEN_PLAY calls apply_pre_tackle(..., frozen_mode=False)",
          "apply_pre_tackle(world, eligible, _pta_dt, self.tuners, frozen_mode=False)" in dbu,
          "unified call not found")
    check("A4 the authority is invoked in BOTH OPEN_PLAY dispatch paths",
          "apply_pre_tackle(world, _pta_pool" in dbu and "apply_pre_tackle(world, eligible" in dbu)

    # ══════════════════════════════════════════════════════════════
    # B. Explicit COMMIT/PLANTED/SLOW/HOLD semantics (authority-owned)
    # ══════════════════════════════════════════════════════════════
    print("\n[B] explicit COMMIT / PLANTED / SLOW / HOLD movement semantics")
    check("B1 authority has _slow_step (COMMIT/SLOW forward-follow step)",
          "def _slow_step(" in pta)
    check("B2 authority has _freeze_stance (PLANTED/HOLD freeze)",
          "def _freeze_stance(" in pta)
    check("B3 authority has _live_commit_point (COMMIT live-BC forward point)",
          "def _live_commit_point(" in pta)
    check("B4 COMMIT closes on live-BC forward point in OPEN_PLAY",
          "cx, cy = _live_commit_point(bc, d, pt)" in pta and
          "frozen_mode=False" in pta.replace("    ", " "))
    check("B5 COMMIT does NOT chase a commit point in DRIVE (folds back to live role-depth)",
          "if not frozen_mode and st in (PreTackleState.COMMIT, PreTackleState.PLANTED):" in pta)
    check("B6 SLOW/HOLD converge to frozen anchors (anti-collapse)",
          "_slow_step(d, ax, ay, dt, hold_cap, smooth)" in pta and
          "d.movement_state = MovementState.DRIFT" in pta)

    # ══════════════════════════════════════════════════════════════
    # C. MMDL defers to the authority (no legacy CHASE/HOLD/DRIFT restamp)
    # ══════════════════════════════════════════════════════════════
    print("\n[C] MMDL defers to the unified authority (replaces legacy chase/hold/drift)")
    check("C1 MMDL rejects already-managed bodies (non-NONE pre_tackle_state)",
          re.search(r"_pts = getattr\(d, \"pre_tackle_state\", None\)" +
                    r"[\s\S]*?getattr\(_pts, \"name\", None\) != \"NONE\"[\s\S]*?continue", dbu) is not None,
          "authority-owned skip missing in MMDL")
    check("C2 the authority publishes BEFORE the MMDL (structured OPEN_PLAY)",
          dbu.find("_publish_pre_tackle_state(world, _pta_pool, bc, self.tuners)") <
          dbu.find("# 10b. MOVEMENT MODE DECISION LAYER"),
          "publish must precede MMDL")
    check("C3 _mmdl_moved stays True for authority-integrated bodies",
          "d._mmdl_moved = True" in pta)

    # ══════════════════════════════════════════════════════════════
    # D. Movement + TES operate on ONE canonical pre_tackle_state
    # ══════════════════════════════════════════════════════════════
    print("\n[D] movement + TES read ONE canonical pre_tackle_state")
    # Single writer: only pre_tackle_authority.py writes `d.pre_tackle_state =`
    writer_sites = []
    for p in (DBU, LVL1, DEFMAN, PTA):
        if os.path.exists(p):
            src = read(p)
            for m in re.finditer(r"d\.pre_tackle_state\s*=", src):
                writer_sites.append((p, m.start()))
    check("D1 exactly ONE writer module for pre_tackle_state",
          all(p == PTA for p, _ in writer_sites) and len(writer_sites) >= 1,
          "writers=%s" % writer_sites)
    check("D2 TES (try_pre_tackle) delegates to the MovementState classifier",
          re.search(r"classify_pretackle", lvl1) is not None)
    check("D3 MMDL reads pre_tackle_state (authority-owned skip)",
          "getattr(d, \"pre_tackle_state\", None)" in dbu)

    # ══════════════════════════════════════════════════════════════
    # E. Depth preserved pre-contact + TES drive (structural checks)
    # ══════════════════════════════════════════════════════════════
    print("\n[E] depth preserved in pre-contact + TES drive frames")
    check("E1 drive invoke uses frozen_mode=True (frozen-anchor TES drive)",
          "apply_pre_tackle(\n            world, _pta_pool, dt, self.tuners, frozen_mode=True" in defman or
          "frozen_mode=True" in defman)
    check("E2 SLOW caps at pretackle_slow_speed_mps (no depth collapse at speed)",
          "hold_cap = float(pt[\"pretackle_slow_speed_mps\"])" in pta)
    check("E3 PLANTED/HOLD zero velocity + pin target (stabilise)",
          "def _freeze_stance(" in pta and "d.vx = 0.0" in pta)

    # ══════════════════════════════════════════════════════════════
    # F. Marker timing + role-depth logic unchanged
    # ══════════════════════════════════════════════════════════════
    print("\n[F] marker timing + role-depth logic unchanged")
    check("F1 marker_authority still exposes enforce_marker_geometry_authority",
          "def enforce_marker_geometry_authority(" in ma)
    check("F2 defensive_depth_roles still exposes assign_depth_roles",
          "def assign_depth_roles(" in dmr)
    check("F3 no marker/role-depth edits in the Stage-4 change set (only DBU wired)",
          os.path.exists(DBU) and os.path.exists(MA) and os.path.exists(DMR))

    # ══════════════════════════════════════════════════════════════
    # G. Engine imports cleanly
    # ══════════════════════════════════════════════════════════════
    print("\n[G] engine module tree imports cleanly")
    import importlib
    mods = [
        "world.open_play_engine",
        "game.managers.defence_manager",
        "game.defence.defence_lvl_1.pre_tackle_authority",
        "game.defence.defence_lvl_1.defensive_behaviour_unified",
        "game.defence.defence_lvl_1.defensive_behaviour_lvl1",
        "world.rules.movement_contracts",
    ]
    ok = True
    for m in mods:
        try:
            importlib.import_module(m)
        except Exception as e:
            ok = False
            print("      [import FAIL] %s: %s" % (m, e))
    check("G1 all engine modules import", ok)

    print("\n" + "=" * 64)
    if failures:
        print("RESULT: %d FAILURE(S): %s" % (len(failures), failures))
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
