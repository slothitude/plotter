"""Single-stroke Hershey font for pen plotter text rendering."""

# Simple Hershey-style vector font: each char is a list of strokes.
# Each stroke is a list of (x, y) points. pen up between strokes.
# Characters are ~6 units wide, 8 units tall. Origin at bottom-left.
CHARS = {
    'A': [[(0,0),(3,8),(6,0)],[(1,3),(5,3)]],
    'B': [[(0,0),(0,8),(4,8),(6,7),(6,5),(4,4),(0,4)],[(4,4),(6,3),(6,1),(4,0),(0,0)]],
    'C': [[(6,7),(4,8),(2,8),(0,6),(0,2),(2,0),(4,0),(6,1)]],
    'D': [[(0,0),(0,8),(4,8),(6,6),(6,2),(4,0),(0,0)]],
    'E': [[(6,8),(0,8),(0,0),(6,0)],[(0,4),(5,4)]],
    'F': [[(6,8),(0,8),(0,0)],[(0,4),(5,4)]],
    'G': [[(6,7),(4,8),(2,8),(0,6),(0,2),(2,0),(4,0),(6,2),(6,4),(3,4)]],
    'H': [[(0,0),(0,8)],[(6,0),(6,8)],[(0,4),(6,4)]],
    'I': [[(1,8),(5,8)],[(3,8),(3,0)],[(1,0),(5,0)]],
    'J': [[(2,8),(6,8)],[(4,8),(4,2),(2,0),(0,0)]],
    'K': [[(0,0),(0,8)],[(6,8),(0,3)],[(3,3),(6,0)]],
    'L': [[(0,8),(0,0),(6,0)]],
    'M': [[(0,0),(0,8),(3,4),(6,8),(6,0)]],
    'N': [[(0,0),(0,8),(6,0),(6,8)]],
    'O': [[(0,2),(0,6),(2,8),(4,8),(6,6),(6,2),(4,0),(2,0),(0,2)]],
    'P': [[(0,0),(0,8),(4,8),(6,7),(6,5),(4,4),(0,4)]],
    'Q': [[(0,2),(0,6),(2,8),(4,8),(6,6),(6,2),(4,0),(2,0),(0,2)],[(4,0),(6,-2)]],
    'R': [[(0,0),(0,8),(4,8),(6,7),(6,5),(4,4),(0,4)],[(3,4),(6,0)]],
    'S': [[(6,7),(4,8),(2,8),(0,7),(0,5),(2,4),(4,4),(6,3),(6,1),(4,0),(2,0),(0,1)]],
    'T': [[(0,8),(6,8)],[(3,8),(3,0)]],
    'U': [[(0,8),(0,2),(2,0),(4,0),(6,2),(6,8)]],
    'V': [[(0,8),(3,0),(6,8)]],
    'W': [[(0,8),(1.5,0),(3,4),(4.5,0),(6,8)]],
    'X': [[(0,8),(6,0)],[(6,8),(0,0)]],
    'Y': [[(0,8),(3,4)],[(6,8),(3,4),(3,0)]],
    'Z': [[(0,8),(6,8),(0,0),(6,0)]],
    '0': [[(0,2),(0,6),(2,8),(4,8),(6,6),(6,2),(4,0),(2,0),(0,2)]],
    '1': [[(2,7),(4,8),(4,0)],[(1,0),(6,0)]],
    '2': [[(0,7),(2,8),(4,8),(6,7),(6,5),(0,0),(6,0)]],
    '3': [[(0,8),(6,8),(6,5),(3,4)],[(3,4),(6,3),(6,1),(4,0),(0,0)]],
    '4': [[(0,8),(0,3),(6,3)],[(6,8),(6,0)]],
    '5': [[(6,8),(0,8),(0,5),(4,5),(6,4),(6,1),(4,0),(0,0)]],
    '6': [[(4,8),(2,8),(0,6),(0,2),(2,0),(4,0),(6,2),(6,4),(4,5),(0,5)]],
    '7': [[(0,8),(6,8),(3,0)]],
    '8': [[(0,6),(2,8),(4,8),(6,6),(4,4),(2,4),(0,2),(2,0),(4,0),(6,2)]],
    '9': [[(6,4),(4,5),(2,4),(0,2),(0,1),(2,0),(4,0),(6,2),(6,6),(4,8),(2,8)]],
    ' ': [],
    '!': [[(3,3),(3,8)],[(3,0),(3,1)]],
    '.': [[(3,0),(3,0.5)]],
    ',': [[(3,0),(3,-1)]],
    '-': [[(1,4),(5,4)]],
    '+': [[(1,4),(5,4)],[(3,2),(3,6)]],
    '/': [[(0,0),(6,8)]],
    ':': [[(3,6),(3,6.5)],[(3,1.5),(3,2)]],
    '(': [[(4,8),(2,7),(0,5),(0,3),(2,1),(4,0)]],
    ')': [[(2,8),(4,7),(6,5),(6,3),(4,1),(2,0)]],
}

CHAR_WIDTH = 7.0
CHAR_HEIGHT = 8.0


def text_to_strokes(text: str, x: float = 0, y: float = 0,
                    scale: float = 3.0, spacing: float = 1.5,
                    max_width: float | None = None) -> list[list[tuple[float, float]]]:
    """Convert text string to list of strokes (polylines).

    Args:
        text: String to render (uppercase supported)
        x, y: Starting position (bottom-left of first line)
        scale: Character size multiplier
        spacing: Extra space between characters (in scaled units)
        max_width: If set, auto-wrap text to fit this width (in scaled units).
                   Manual newlines are always respected.

    Returns:
        List of strokes, each stroke is a list of (x, y) tuples
    """
    text = text.upper()
    all_strokes = []
    line_height = CHAR_HEIGHT * scale * 1.5  # line gap = 0.5x height

    # Split on manual newlines first
    raw_lines = text.split('\n')

    # Word-wrap each line if max_width is set
    lines = []
    for raw_line in raw_lines:
        if not max_width or not raw_line:
            lines.append(raw_line)
            continue
        words = raw_line.split(' ')
        current_line = ''
        for word in words:
            if not word:
                continue
            test_line = (current_line + ' ' + word).strip() if current_line else word
            test_width = len(test_line) * (CHAR_WIDTH * scale + spacing) - spacing
            if test_width > max_width and current_line:
                lines.append(current_line)
                current_line = word
            else:
                current_line = test_line
        if current_line:
            lines.append(current_line)

    for i, line in enumerate(lines):
        cx = x
        ly = y + i * line_height
        for ch in line:
            char_strokes = CHARS.get(ch, CHARS.get('?', []))
            for stroke in char_strokes:
                scaled = [(cx + px * scale, ly + (CHAR_HEIGHT - py) * scale) for px, py in stroke]
                all_strokes.append(scaled)
            cx += CHAR_WIDTH * scale + spacing

    return all_strokes
