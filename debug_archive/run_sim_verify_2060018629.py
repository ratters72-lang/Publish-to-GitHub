"""Run simulation, capture defender positions to verify they advance."""
import subprocess, sys, time, os

# Use UTF-8 for output
env = os.environ.copy()
env['PYTHONIOENCODING'] = 'utf-8'

p = subprocess.Popen([sys.executable, 'run.py'],
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                     text=True, encoding='utf-8', env=env)
time.sleep(25)
p.terminate()
out = p.stdout.read()
lines = out.split('\n')

# Show key transitions
key_words = ['SUBSTATE', 'DRIVE', 'catch', 'OPEN_PLAY', 'TACKLE', 'PTB', 'RELEASE', 
             'ROLE_GOVERN', 'TES_UNLOCK', 'DIRECTION']
for l in lines:
    if any(k in l for k in key_words):
        print(l)

# Also check for BALL DEBUG lines near end to see defender positions
ball_lines = [l for l in lines if 'BALL DEBUG' in l]
print(f'\n=== Last BALL DEBUG lines ({len(ball_lines)} total) ===')
for l in ball_lines[-5:]:
    print(l)

print(f'\nTotal lines: {len(lines)}')
