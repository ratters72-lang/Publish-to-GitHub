"""tmp_fbap_ab.py — in-process A/B of the FB admission levers.

Imports probe_fb_admission_predicates and re-runs the rollout with the FB
levers rebound, so the effect of each lever on FB ADMISSION / ARMING can be
attributed without spawning processes (the levers are read as module globals
at call time).

Prints a comparison table. Read-only against the engine.
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

import probe_fb_admission_predicates as P
from game.defence.defence_lvl_1 import fullback_role as fbr

SEEDS = [int(s) for s in os.environ.get(
    "RLM_FBAP_AB_SEEDS", "20260827,1,2,3,4,5").split(",") if s.strip()]
FRAMES = int(os.environ.get("RLM_FBAP_AB_FRAMES", "1500"))
P.FRAMES = FRAMES
P.SEEDS = SEEDS


def run_config(label, candidacy, persistence, latch):
    """Run the rollout set with the three FB levers at the given values."""
    fbr.FULLBACK_TACKLE_CANDIDACY = bool(candidacy)
    fbr.FULLBACK_ROLE_PERSISTENCE = bool(persistence)
    fbr.FB_BREAK_LATCH = bool(latch)

    near = 0
    not_adm = 0
    adm_role = 0
    adm_guard = 0
    arms = 0

    # Suppress engine chatter: discard writes while the TEE is inactive.
    saved = sys.stdout
    P._TEE.active = False
    sys.stdout = P._TEE
    try:
        for seed in SEEDS:
            lg = P.run_rollout(seed)
            near += lg["frames_near"]
            not_adm += lg["near_and_not_admitted"]
            adm_role += lg["admits"]["role"]
            adm_guard += lg["admits"]["guard"]
            arms += len(lg["arm_events"])
    finally:
        sys.stdout = saved

    return {
        "label": label,
        "candidacy": candidacy,
        "persistence": persistence,
        "latch": latch,
        "near": near,
        "not_adm": not_adm,
        "adm_role": adm_role,
        "adm_guard": adm_guard,
        "arms": arms,
    }


def main():
    configs = [
        ("baseline (all on)", 1, 1, 1),
        ("candidacy OFF", 0, 1, 1),
        ("role-persist OFF", 1, 0, 1),
        ("break-latch OFF", 1, 1, 0),
    ]
    rows = [run_config(*c) for c in configs]

    print("=" * 100)
    print("FB ADMISSION LEVER A/B  (seeds=%s, %d frames each)" % (SEEDS, FRAMES))
    print("=" * 100)
    print("%-20s %6s %7s %7s %7s %6s"
          % ("config", "cand", "near", "notAdm", "guardAdm", "arms"))
    print("-" * 100)
    for r in rows:
        print("%-20s %6d %7d %7d %7d %6d"
              % (r["label"], r["candidacy"], r["near"], r["not_adm"],
                 r["adm_guard"], r["arms"]))
    print("")
    print("  guardsAdm = FB frames admitted to candidacy via the engage guard")
    print("  arms      = _arm_tackler_role() calls on the tagged FB")
    print("  notAdm    = near FB frames rejected from candidacy")
    print("")
    print("  INTERPRETATION:")
    print("   * candidacy OFF does NOT change guardAdm or the guard's radial/")
    print("     facing rejection (the guard is independent of the lane pool);")
    print("     it only removes the lane-stamp route, so arms/PRIMARY drop.")
    print("   * role-persist OFF can withdraw can_acquire_tackle on a CHASE FB,")
    print("     which removes the emergency-gate route to a lane stamp.")
    print("   * break-latch OFF changes only the break verdict, not the guard.")


if __name__ == "__main__":
    main()
