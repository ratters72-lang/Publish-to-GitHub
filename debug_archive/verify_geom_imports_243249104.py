import sys
sys.path.insert(0, 'c:/Users/User/Desktop/RLM')

from game.attack.attack_lvl_1.attack_geometry import AttackGeometry
print("Import OK — AttackGeometry is available")
print("Methods:", [m for m in dir(AttackGeometry) if not m.startswith('_')])
