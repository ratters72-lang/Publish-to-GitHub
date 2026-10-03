with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Verify the 5 insertion points
checks = [
    (1560, '_prepare_release', 'bc = self.ball_carrier'),
    (1562, '_prepare_release', 'return'),
    (1749, '_update_release', '"""'),
    (1751, '_update_release', 'print('),
    (1935, '_update_ptb_prep', '"""'),
    (1938, '_update_ptb_prep', '# PTB gate'),
    (2003, '_update_ptb_exec', '"""'),
    (2006, '_update_ptb_exec', '# PTB gate'),
    (2117, '_update_ptb', '"""'),
    (2120, '_update_ptb', 'dh = self.dh'),
]

for idx, method, expected_content in checks:
    line_num = idx
    actual = lines[line_num - 1].rstrip()
    status = 'OK' if expected_content in actual else 'MISMATCH'
    print(f"L{line_num} [{status}] (expect '{expected_content}'): {actual}")
