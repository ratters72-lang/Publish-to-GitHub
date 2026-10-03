import os

# === STALE-SPEC PATTERNS ======================================================
PATTERNS = {
    "OLD_FULLBACK_SIGN": [
        "line_anchor -",
        "anchor_x = defensive_line_x -",
        "anchor = defensive_line_anchor -",
    ],
    "OLD_FULLBACK_DEPTH": [
        "+ 3",
        "+3.0",
        "depth_m = 3",
        "+ 5",
        "+5.0",
        "depth_m = 5",
    ],
    "OLD_FALLBACK_SELECTOR": [
        "min(defenders",
        "min(eligible",
        "min(relaxed",
        "fallback_fullback",
    ],
    "OLD_ANCHOR_LEAK": [
        "defensive_line_anchor = 0",
        "world.defensive_line_anchor = 0",
        "anchor = 0.0",
    ],
    "OLD_ANCHOR_SOURCE": [
        "anchor = defensive_line_anchor",
        "anchor_x = defensive_line_anchor",
        "world.fullback_anchor",
        "publish_fullback",
    ],
    "OLD_GEOMETRY_ORDERING": [
        "sorted(defenders, key=lambda d: d.y",
        "_apply_step_up_to_active",
        "sorted_defs = sorted(",
        "_order_by_y",
    ],
    "OLD_TACKLER_STAMPING": [
        "is_tackler = True",
        "movement_mode = None",
        "movement_mode = MOVING",
        "collision_priority = LOW",
    ],
    "OLD_TES_ARTIFACTS": [
        "clear_tes",
        "tes_active",
        "tes_joiner",
        "tes_marker",
    ],
    "OLD_FIELD_GEOMETRY": [
        "0..120",
        "120",
        "field_x <= 0",
        "field_x >= 120",
    ],
    "OLD_PUBLICATION_PATHS": [
        "publish_defensive_state",
        "publish_fullback",
        "world.fullback_anchor_x",
        "world.defensive_line_anchor",
    ],
    "OLD_RETREAT_LOGIC": [
        "retreat_target",
        "retreat_dir",
        "offside",
        "10m",
    ],
    "ATTACK_BLEED_IN_DEF": [
        "FOLLOW bc.x",
        "DRIVE raw",
        "DOMINANCE push",
        "momentum_clamp",
    ],
}

# === AUDIT ====================================================================
def audit_file(path):
    results = []
    try:
        with open(path, "r", errors="ignore") as f:
            lines = f.readlines()
    except Exception:
        return results

    for i, line in enumerate(lines, start=1):
        for category, patterns in PATTERNS.items():
            for p in patterns:
                if p in line:
                    results.append((category, i, line.strip()))
    return results


def run_audit():
    print("\n=== STALE-SPEC AUDIT START ===\n")
    for root, dirs, files in os.walk("."):
        for fname in files:
            if fname.endswith(".py"):
                path = os.path.join(root, fname)
                hits = audit_file(path)
                if hits:
                    print(f"\n--- {path} ---")
                    for category, line_no, text in hits:
                        print(f"[{category}] line {line_no}: {text}")
    print("\n=== AUDIT COMPLETE ===\n")


if __name__ == "__main__":
    run_audit()
