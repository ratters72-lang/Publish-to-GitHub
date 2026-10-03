# game/tes/tes_outcomes.py
from .tes_state import TackleResult

class TESOutcome:
    """Stores the final computed result of the tackle engagement."""
    def __init__(self):
        self.tackle_result = TackleResult.NEUTRAL
        self.attacker_dominance = 0.0
        self.defender_dominance = 0.0
        self.drive_distance_m = 0.0
        self.tackle_duration_s = 0.0
        self.ptb_time_s = 0.0
        
        self.offload_success = False
        self.strip_success = False
        self.penalty_committed = False
        self.six_again_awarded = False
        self.line_break = False
        
        self.tackle_number = 0
        self.raw = {}
