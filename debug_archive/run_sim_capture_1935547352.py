"""Run simulation for 20 seconds, capture key phase transitions."""
import subprocess, sys, time

p = subprocess.Popen([sys.executable, 'run.py'],
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                     text=True, encoding='utf-8')
time.sleep(20)
p.terminate()
out = p.stdout.read()
lines = out.split('\n')

# Filter for key phase transitions and game events
key_words = ['OPEN_PLAY', 'TACKLE', 'PTB', 'SUBSTATE', 'catch', 'tackle',
             'DRIVE', 'DOMINANCE', 'RELEASE', 'FREEZE', 'TES_', 'tackler',
             'ROLE_GOVERN', 'DEF DEBUG', '[DEF', 'BALL DEBUG', 'pre_tackle']
key_lines = [l for l in lines if any(k in l for k in key_words)]

print(f'Total lines: {len(lines)}, key lines: {len(key_lines)}')
print('=' * 60)
for l in key_lines:
    print(l)
print('=' * 60)
