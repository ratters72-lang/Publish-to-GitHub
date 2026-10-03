"""Throwaway scan: enumerate every line-membership consumer reading the FB tag."""
import os
import re

ROOTS = ("game", "world", "player")
PAT = re.compile(r"fullback_role_active|fullback_actor_id")
DEF_PAT = re.compile(
    r"^\s*def\s+(is_line_body|is_spacing_eligible|assign_depth_roles|"
    r"_line_defenders|publish_line_population|line_population|"
    r"is_width_eligible|is_span_eligible|run_frame_preamble|"
    r"is_formation_eligible|assign_formation_slots|"
    r"select_lane_tacklers|apply_pre_tackle|"
    r"assign_fullback_role|update_fullback_absorb_state)\b"
)


def main() -> None:
    hits = 0
    defs = []
    for root in ROOTS:
        for dirpath, _dirnames, filenames in os.walk(root):
            if "debug_archive" in dirpath:
                continue
            for fn in filenames:
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(dirpath, fn).replace("\\", "/")
                try:
                    lines = open(path, encoding="utf-8", errors="replace").readlines()
                except OSError:
                    continue
                for n, line in enumerate(lines, 1):
                    if PAT.search(line):
                        hits += 1
                        print(f"{path}:{n}: {line.rstrip()}")
                    m = DEF_PAT.match(line)
                    if m:
                        defs.append(f"{path}:{n}: {m.group(1)}")
    print("\n--- DEFINITIONS ---")
    for d in sorted(set(defs)):
        print(d)
    print(f"\ntotal tag hits: {hits}")


if __name__ == "__main__":
    main()
