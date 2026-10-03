with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace non-ASCII Unicode characters commonly found in comments with ASCII equivalents
# These were previously inside docstrings but are now outside due to prior stripping
replacements = {
    '\u2020': '->',    # DAGGER → ->
    '\u2014': '--',    # EM DASH → --
    '\u2013': '-',     # EN DASH → -
    '\u2192': '->',    # RIGHT ARROW → ->
    '\u2190': '<-',    # LEFT ARROW → <-
    '\u2191': '^',     # UP ARROW → ^
    '\u2193': 'v',     # DOWN ARROW → v
    '\u2018': "'",     # LEFT SINGLE QUOTATION MARK → '
    '\u2019': "'",     # RIGHT SINGLE QUOTATION MARK → '
    '\u201c': '"',     # LEFT DOUBLE QUOTATION MARK → "
    '\u201d': '"',     # RIGHT DOUBLE QUOTATION MARK → "
    '\u2022': '*',     # BULLET → *
    '\u2026': '...',   # HORIZONTAL ELLIPSIS → ...
    '\u2032': "'",     # PRIME → '
    '\u2033': '"',     # DOUBLE PRIME → "
    '\u00a0': ' ',     # NO-BREAK SPACE → space
    '\u00bb': '>>',    # RIGHT-POINTING DOUBLE ANGLE → >>
    '\u00ab': '<<',    # LEFT-POINTING DOUBLE ANGLE → <<
    '\u2666': '<>',    # BLACK DIAMOND SUIT → <>
    '\u25ca': '<>',    # LOZENGE → <>
    '\u25c6': '<>',    # BLACK DIAMOND → <>
    '\u2502': '|',     # BOX DRAWINGS LIGHT VERTICAL → |
    '\u2500': '-',     # BOX DRAWINGS LIGHT HORIZONTAL → -
    '\u2550': '=',     # BOX DRAWINGS DOUBLE HORIZONTAL → =
    '\u2551': '|',     # BOX DRAWINGS DOUBLE VERTICAL → |
    '\u2563': '|',     # BOX DRAWINGS DOUBLE VERTICAL RIGHT → |
    '\u2560': '|',     # BOX DRAWINGS DOUBLE VERTICAL LEFT → |
    '\u255a': '+',     # BOX DRAWINGS DOUBLE UP AND RIGHT → +
    '\u2557': '+',     # BOX DRAWINGS DOUBLE DOWN AND LEFT → +
    '\u2554': '+',     # BOX DRAWINGS DOUBLE DOWN AND RIGHT → +
    '\u255d': '+',     # BOX DRAWINGS DOUBLE UP AND LEFT → +
    '\u2514': '+',     # BOX DRAWINGS LIGHT UP AND RIGHT → +
    '\u2510': '+',     # BOX DRAWINGS LIGHT DOWN AND LEFT → +
    '\u250c': '+',     # BOX DRAWINGS LIGHT DOWN AND RIGHT → +
    '\u2518': '+',     # BOX DRAWINGS LIGHT UP AND LEFT → +
    '\u2524': '+',     # BOX DRAWINGS LIGHT VERTICAL AND LEFT → +
    '\u251c': '+',     # BOX DRAWINGS LIGHT VERTICAL AND RIGHT → +
    '\u2610': 'o',     # BALLOT BOX → o
    '\u25a0': '#',     # BLACK SQUARE → #
    '\u25cb': 'o',     # WHITE CIRCLE → o
    '\u2588': '#',     # FULL BLOCK → #
    '\u2592': '#',     # MEDIUM SHADE → #
    '\u2591': '#',     # LIGHT SHADE → #
}

for old, new in replacements.items():
    content = content.replace(old, new)

with open('world/open_play_engine.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Done - replaced all non-ASCII Unicode glyphs with ASCII equivalents")
