import sys
sys.path.insert(0, '.')
sys.path.insert(0, 'world')

from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners

tuners = DefenceTuners()
print(f'primary_depth={tuners.primary_depth_threshold_m} secondary_depth={tuners.secondary_depth_threshold_m}')

# Kickoff scenario: defenders at ~62m (kickoff formation), ball at 20m
# expected_x = 20 - role_offset (~5m for role C) = 15m
# dx = |62 - 15| = 47m
# With old thresholds (5m/10m) -> PASSIVE for all
# With new thresholds (50m/80m) -> PRIMARY for all
dx = abs(62 - (20 - 5.0))
print(f'\nKickoff scenario: dx={dx:.1f}m')
print(f'  old: dx <= 5 -> PRIMARY? {dx <= 5}  dx <= 10 -> SECONDARY? {dx <= 10}')
print(f'  new: dx <= {tuners.primary_depth_threshold_m} -> PRIMARY? {dx <= tuners.primary_depth_threshold_m}')

# Open play scenario: defender at 30m, ball at 20m (closer)
dx2 = abs(30 - (20 - 3.0))
print(f'\nOpen play scenario: dx={dx2:.1f}m')
print(f'  old PRIMARY? {dx2 <= 5}  old SECONDARY? {dx2 <= 10}')
print(f'  new PRIMARY? {dx2 <= tuners.primary_depth_threshold_m}')

# Edge case: defender very far back (70m) with ball far forward (80m)
dx3 = abs(70 - (80 - 10.0))
print(f'\nFar behind scenario: dx={dx3:.1f}m')
print(f'  new PRIMARY? {dx3 <= tuners.primary_depth_threshold_m}')
print(f'  new SECONDARY? {dx3 <= tuners.secondary_depth_threshold_m}')

# All should be classified PRIMARY now
assert dx <= tuners.primary_depth_threshold_m, f'Kickoff dx={dx} should be PRIMARY'
assert dx2 <= tuners.primary_depth_threshold_m, f'Open play dx={dx2} should be PRIMARY'
print('\nAll assertions passed.')
