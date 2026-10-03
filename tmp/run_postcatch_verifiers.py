import subprocess
import sys

verifiers = [
    ("TES_DRIVE", "verify_tes_drive_fix.py"),
    ("DEPTH", "verify_depth_stability.py"),
    ("MOVEMENT_CHAIN", "verify_movement_chain.py"),
]

out_lines = []
for name, script in verifiers:
    out_lines.append(f"===== {name} =====")
    try:
        result = subprocess.run(
            [sys.executable, script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        tail = "\n".join((result.stdout + result.stderr).strip().splitlines()[-6:])
        out_lines.append(tail if tail else "<no output>")
        out_lines.append(f"[EXIT {result.returncode}]")
    except Exception as exc:  # noqa: BLE001
        out_lines.append(f"<error {exc}>")
    out_lines.append("")

with open("tmp/postcatch_verifiers.out", "w", encoding="utf-8") as f:
    f.write("\n".join(out_lines))
print("done")
