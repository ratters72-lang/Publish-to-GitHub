# Plan: Patch _update_contact and _update_drive to compute actual velocities
# from position deltas, so DRIVE exit gating uses real movement not zero.
#
# Changes:
# 1. _update_contact: replace velocity zeroing with position save,
#    then compute actual velocity from delta before waveform update
# 2. _update_drive: save positions before force application,
#    compute velocity from delta + move waveform update to after velocity compute
