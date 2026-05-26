"""Manga Plotter Toolkit — panel layout, speed lines, screen tones, speech bubbles, effects.

Every generator returns list[Polyline] with layer tags. The compile-page function
walks a JSON page description and produces layered polylines in one shot.
"""

import math
import random
from dataclasses import dataclass

import numpy as np

import gcode
import font


# ── Layer constants ─────────────────────────────────────────────────
LAYER_BORDER  = "border"
LAYER_OUTLINE = "outline"
LAYER_DETAIL  = "detail"
LAYER_TONE    = "tone"
LAYER_EFFECT  = "effect"
LAYER_TEXT    = "text"


# ════════════════════════════════════════════════════════════════════
#  TOOL 1: PANEL LAYOUT
# ════════════════════════════════════════════════════════════════════

def generate_panels(page_desc: dict) -> list[gcode.Polyline]:
    """Generate panel borders, gutters, and bleed marks from page description."""
    pw = page_desc.get("page_width", 180)
    ph = page_desc.get("page_height", 175)
    bleed = page_desc.get("bleed", 3)
    panels = page_desc.get("panels", [])

    polylines: list[gcode.Polyline] = []

    if not panels:
        # Single panel = full page border
        polylines.append(_rect(bleed, bleed, pw - bleed, ph - bleed, LAYER_BORDER))
        return polylines

    for panel in panels:
        b = panel.get("bounds", [bleed, bleed, pw - bleed, ph - bleed])
        x0, y0, x1, y1 = b

        # Panel border rectangle
        polylines.append(_rect(x0, y0, x1, y1, LAYER_BORDER))

        # Bleed marks at corners (L-shapes extending past panel edge)
        ml = bleed  # mark length
        for cx, cy in [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]:
            dx = -ml if cx == x0 else ml
            dy = -ml if cy == y0 else ml
            # Horizontal tick
            polylines.append(gcode.Polyline(
                points=[(cx, cy), (cx + dx, cy)], layer=LAYER_BORDER))
            # Vertical tick
            polylines.append(gcode.Polyline(
                points=[(cx, cy), (cx, cy + dy)], layer=LAYER_BORDER))

    return polylines


# ── Panel presets ──────────────────────────────────────────────────

PANEL_PRESETS = {
    "2-row": lambda pw, ph, bl, g=4: _layout_rows(pw, ph, bl, [0.5, 0.5]),
    "3-panel": lambda pw, ph, bl, g=4: _layout_rows(pw, ph, bl, [0.33, 0.34, 0.33]),
    "4-grid": lambda pw, ph, bl, g=4: _layout_grid(pw, ph, bl, 2, 2),
    "2-3": lambda pw, ph, bl, g=4: _layout_2_3(pw, ph, bl),
    "L-shape": lambda pw, ph, bl, g=4: _layout_l_shape(pw, ph, bl),
    "manga-1": lambda pw, ph, bl, g=4: _layout_manga1(pw, ph, bl),
}


def apply_preset(preset: str, pw: float, ph: float, bleed: float = 3) -> list[dict]:
    """Return list of panel bounds dicts for a named preset."""
    gutter = 4
    if preset in PANEL_PRESETS:
        return PANEL_PRESETS[preset](pw, ph, bleed, gutter)
    return []


def _layout_rows(pw, ph, bl, ratios):
    gutter = 4
    panels = []
    y = bl
    for i, r in enumerate(ratios):
        h = (ph - 2 * bl - gutter * (len(ratios) - 1)) * r
        panels.append({"bounds": [bl, y, pw - bl, y + h]})
        y += h + gutter
    return panels


def _layout_grid(pw, ph, bl, rows, cols):
    gutter = 4
    panels = []
    w = (pw - 2 * bl - gutter * (cols - 1)) / cols
    h = (ph - 2 * bl - gutter * (rows - 1)) / rows
    for r in range(rows):
        for c in range(cols):
            x0 = bl + c * (w + gutter)
            y0 = bl + r * (h + gutter)
            panels.append({"bounds": [x0, y0, x0 + w, y0 + h], "children": []})
    return panels


def _layout_2_3(pw, ph, bl, gutter=4):
    mid_y = ph / 2
    # Top half = one wide panel
    panels = [{"bounds": [bl, bl, pw - bl, mid_y - gutter / 2], "children": []}]
    # Bottom half = three columns
    w = (pw - 2 * bl - gutter * 2) / 3
    y0 = mid_y + gutter / 2
    for c in range(3):
        x0 = bl + c * (w + gutter)
        panels.append({"bounds": [x0, y0, x0 + w, ph - bl], "children": []})
    return panels


def _layout_l_shape(pw, ph, bl, gutter=4):
    # Large panel on left, two stacked on right
    mid_x = pw * 0.6
    mid_y = ph / 2
    panels = [
        {"bounds": [bl, bl, mid_x - gutter / 2, ph - bl], "children": []},
        {"bounds": [mid_x + gutter / 2, bl, pw - bl, mid_y - gutter / 2], "children": []},
        {"bounds": [mid_x + gutter / 2, mid_y + gutter / 2, pw - bl, ph - bl], "children": []},
    ]
    return panels


def _layout_manga1(pw, ph, bl, gutter=4):
    """Classic manga: tall narrow left + wide right top + wide right bottom."""
    left_w = pw * 0.35
    right_top_h = ph * 0.45
    panels = [
        {"bounds": [bl, bl, left_w - gutter / 2, ph - bl], "children": []},
        {"bounds": [left_w + gutter / 2, bl, pw - bl, bl + right_top_h], "children": []},
        {"bounds": [left_w + gutter / 2, bl + right_top_h + gutter, pw - bl, ph - bl], "children": []},
    ]
    return panels


# ════════════════════════════════════════════════════════════════════
#  TOOL 2: SPEED LINES + EFFECTS
# ════════════════════════════════════════════════════════════════════

def generate_speed_lines(params: dict) -> list[gcode.Polyline]:
    """Generate speed lines (radial, parallel, or focus)."""
    bounds = params.get("bounds", [0, 0, 180, 175])
    origin = params.get("origin", [90, 87])
    count = params.get("count", 20)
    length_range = params.get("length", [20, 60])
    style = params.get("style", "radial")
    jitter = params.get("jitter", 0.5)
    taper = params.get("taper", False)

    ox, oy = origin
    polylines: list[gcode.Polyline] = []

    if style == "radial":
        polylines = _radial_speed_lines(bounds, ox, oy, count, length_range, jitter, taper)
    elif style == "parallel":
        angle = params.get("angle", 0)
        polylines = _parallel_speed_lines(bounds, angle, count, length_range, jitter)
    elif style == "focus":
        polylines = _focus_lines(bounds, ox, oy, count, length_range, jitter)

    return polylines


def _radial_speed_lines(bounds, ox, oy, count, length_range, jitter, taper):
    """Lines radiating from a vanishing point, clipped to bounds."""
    x0, y0, x1, y1 = bounds
    polylines = []
    for _ in range(count):
        angle = random.uniform(0, 2 * math.pi)
        length = random.uniform(length_range[0], length_range[1])
        ex = ox + math.cos(angle) * length
        ey = oy + math.sin(angle) * length
        # Clip endpoint to bounds
        ex = max(x0, min(x1, ex))
        ey = max(y0, min(y1, ey))
        # Jitter start slightly
        sx = ox + random.uniform(-jitter, jitter)
        sy = oy + random.uniform(-jitter, jitter)
        polylines.append(gcode.Polyline(
            points=[(sx, sy), (ex, ey)], layer=LAYER_EFFECT))
    return polylines


def _parallel_speed_lines(bounds, angle, count, length_range, jitter):
    """Parallel lines across a panel at a given angle."""
    x0, y0, x1, y1 = bounds
    bw, bh = x1 - x0, y1 - y0
    polylines = []
    rad = math.radians(angle)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    # Perpendicular direction for spacing
    perp_x, perp_y = -sin_a, cos_a
    diag = math.hypot(bw, bh)
    for i in range(count):
        t = (i + 0.5) / count
        offset = (t - 0.5) * diag
        cx = (x0 + x1) / 2 + perp_x * offset + random.uniform(-jitter, jitter)
        cy = (y0 + y1) / 2 + perp_y * offset + random.uniform(-jitter, jitter)
        half = random.uniform(length_range[0], length_range[1]) / 2
        sx, sy = cx - cos_a * half, cy - sin_a * half
        ex, ey = cx + cos_a * half, cy + sin_a * half
        # Clip to bounds
        sx, sy = max(x0, min(x1, sx)), max(y0, min(y1, sy))
        ex, ey = max(x0, min(x1, ex)), max(y0, min(y1, ey))
        polylines.append(gcode.Polyline(
            points=[(sx, sy), (ex, ey)], layer=LAYER_EFFECT))
    return polylines


def _focus_lines(bounds, ox, oy, count, length_range, jitter):
    """Short lines around a central point (shock, emphasis)."""
    x0, y0, x1, y1 = bounds
    polylines = []
    for _ in range(count):
        angle = random.uniform(0, 2 * math.pi)
        dist = random.uniform(5, 15)
        length = random.uniform(length_range[0], length_range[1])
        sx = ox + math.cos(angle) * dist
        sy = oy + math.sin(angle) * dist
        ex = sx + math.cos(angle) * length
        ey = sy + math.sin(angle) * length
        sx, sy = max(x0, min(x1, sx)), max(y0, min(y1, sy))
        ex, ey = max(x0, min(x1, ex)), max(y0, min(y1, ey))
        polylines.append(gcode.Polyline(
            points=[(sx, sy), (ex, ey)], layer=LAYER_EFFECT))
    return polylines


def generate_impact_burst(params: dict) -> list[gcode.Polyline]:
    """Irregular star polygon — explosion/impact effect."""
    cx = params.get("cx", 50)
    cy = params.get("cy", 50)
    radius = params.get("radius", 20)
    n_points = params.get("points", 8)
    irregularity = params.get("irregularity", 0.3)

    pts = []
    for i in range(n_points * 2):
        angle = math.pi * i / n_points
        r = radius * (1 + random.uniform(-irregularity, irregularity))
        pts.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    pts.append(pts[0])  # close

    return [gcode.Polyline(points=pts, layer=LAYER_EFFECT)]


def generate_rain(params: dict) -> list[gcode.Polyline]:
    """Angled line field clipped to panel bounds."""
    bounds = params.get("bounds", [0, 0, 180, 175])
    angle = params.get("angle", 75)
    density = params.get("density", 30)

    x0, y0, x1, y1 = bounds
    bw, bh = x1 - x0, y1 - y0
    rad = math.radians(angle)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    length = random.uniform(8, 18)
    polylines = []

    for _ in range(density):
        sx = random.uniform(x0, x1)
        sy = random.uniform(y0, y1)
        ex = sx + cos_a * length
        ey = sy + sin_a * length
        # Clip
        ex = max(x0, min(x1, ex))
        ey = max(y0, min(y1, ey))
        polylines.append(gcode.Polyline(
            points=[(sx, sy), (ex, ey)], layer=LAYER_EFFECT))

    return polylines


def generate_emotion_lines(params: dict) -> list[gcode.Polyline]:
    """Short lines radiating from a point (shock, embarrassment)."""
    cx = params.get("cx", 50)
    cy = params.get("cy", 50)
    radius = params.get("radius", 15)
    count = params.get("count", 12)

    polylines = []
    for i in range(count):
        angle = 2 * math.pi * i / count + random.uniform(-0.1, 0.1)
        inner_r = radius * 0.3
        outer_r = radius * (0.8 + random.uniform(0, 0.4))
        sx = cx + inner_r * math.cos(angle)
        sy = cy + inner_r * math.sin(angle)
        ex = cx + outer_r * math.cos(angle)
        ey = cy + outer_r * math.sin(angle)
        polylines.append(gcode.Polyline(
            points=[(sx, sy), (ex, ey)], layer=LAYER_EFFECT))
    return polylines


# ════════════════════════════════════════════════════════════════════
#  TOOL 3: SCREEN TONE SYSTEM
# ════════════════════════════════════════════════════════════════════

def generate_tone(params: dict) -> list[gcode.Polyline]:
    """Generate screen tone fill. Dispatches by style."""
    style = params.get("style", "line")
    polygon = params.get("polygon", [])
    if len(polygon) < 3:
        return []

    if style == "line":
        return generate_line_tone(polygon, params.get("spacing", 2.0), params.get("angle", 45))
    elif style == "dot":
        return generate_dot_tone(polygon, params.get("lpi", 40), params.get("dot_size", 0.4))
    elif style == "gradient":
        return generate_gradient_tone(
            polygon, params.get("lpi", 30),
            params.get("dot_size_start", 0.2), params.get("dot_size_end", 0.8),
            params.get("direction", "left-right"))
    elif style == "crosshatch":
        lines = generate_line_tone(polygon, params.get("spacing", 2.0), params.get("angle", 45))
        lines.extend(generate_line_tone(polygon, params.get("spacing", 2.0), params.get("angle", 45) + 90))
        return lines
    return []


def generate_line_tone(polygon, spacing=2.0, angle=45):
    """Line screen tone — reuses existing hatch fill from gcode.py."""
    fills = gcode.generate_hatch_fill(polygon, spacing, angle)
    for pl in fills:
        pl.layer = LAYER_TONE
    return fills


def generate_dot_tone(polygon, lpi=40, dot_size=0.4):
    """Dot screen tone with vectorised point-in-polygon + boustrophedon sort."""
    if len(polygon) < 3:
        return []

    # Bounding box
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    spacing = 25.4 / lpi  # mm between dots (25.4mm = 1 inch)

    # Vectorised grid
    gx = np.arange(min_x + spacing / 2, max_x, spacing)
    gy = np.arange(min_y + spacing / 2, max_y, spacing)
    grid_x, grid_y = np.meshgrid(gx, gy)

    # Flatten for point-in-polygon test
    pts_x = grid_x.ravel()
    pts_y = grid_y.ravel()

    # Vectorised point-in-polygon (cross-product method)
    inside = _points_in_polygon(pts_x, pts_y, polygon)

    # Filter to inside points
    ix = pts_x[inside]
    iy = pts_y[inside]

    if len(ix) == 0:
        return []

    # Boustrophedon sort: sort by row (y), alternate direction per row
    row_indices = np.round((iy - min_y) / spacing).astype(int)
    order = np.lexsort((ix, row_indices))  # sort by y, then x

    # Flip every other row for snake traversal
    rows_unique = np.unique(row_indices)
    final_order = []
    for row in rows_unique:
        mask = row_indices[order] == row
        row_pos = np.where(mask)[0]
        if len(row_pos) == 0:
            continue
        row_ixs = order[row_pos]
        if row % 2 == 1:
            row_ixs = row_ixs[::-1]
        final_order.extend(row_ixs.tolist())

    # Generate tiny circle polylines for each dot
    polylines = []
    n_circle = 6
    circle_angles = [2 * math.pi * i / n_circle for i in range(n_circle + 1)]
    cos_a = [math.cos(a) for a in circle_angles]
    sin_a = [math.sin(a) for a in circle_angles]

    for idx in final_order:
        cx, cy = float(ix[idx]), float(iy[idx])
        pts = [(cx + dot_size * cos_a[i], cy + dot_size * sin_a[i]) for i in range(n_circle + 1)]
        polylines.append(gcode.Polyline(points=pts, layer=LAYER_TONE))

    return polylines


def generate_gradient_tone(polygon, lpi=30, dot_size_start=0.2, dot_size_end=0.8,
                           direction="left-right"):
    """Dot tone with varying dot size for gradient effect."""
    if len(polygon) < 3:
        return []

    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = max_x - min_x
    span_y = max_y - min_y

    spacing = 25.4 / lpi

    gx = np.arange(min_x + spacing / 2, max_x, spacing)
    gy = np.arange(min_y + spacing / 2, max_y, spacing)
    grid_x, grid_y = np.meshgrid(gx, gy)

    pts_x = grid_x.ravel()
    pts_y = grid_y.ravel()
    inside = _points_in_polygon(pts_x, pts_y, polygon)
    ix = pts_x[inside]
    iy = pts_y[inside]

    if len(ix) == 0:
        return []

    # Sort boustrophedon
    row_indices = np.round((iy - min_y) / spacing).astype(int)
    order = np.lexsort((ix, row_indices))
    rows_unique = np.unique(row_indices)
    final_order = []
    for row in rows_unique:
        mask = row_indices[order] == row
        row_pos = np.where(mask)[0]
        if len(row_pos) == 0:
            continue
        row_ixs = order[row_pos]
        if row % 2 == 1:
            row_ixs = row_ixs[::-1]
        final_order.extend(row_ixs.tolist())

    polylines = []
    n_circle = 6
    circle_angles = [2 * math.pi * i / n_circle for i in range(n_circle + 1)]

    for idx in final_order:
        cx, cy = float(ix[idx]), float(iy[idx])
        # Calculate gradient position
        if direction == "left-right":
            t = (cx - min_x) / span_x if span_x > 0 else 0.5
        elif direction == "top-bottom":
            t = (cy - min_y) / span_y if span_y > 0 else 0.5
        elif direction == "radial":
            dx = (cx - (min_x + max_x) / 2) / (span_x / 2) if span_x > 0 else 0
            dy = (cy - (min_y + max_y) / 2) / (span_y / 2) if span_y > 0 else 0
            t = min(1.0, math.hypot(dx, dy))
        else:
            t = 0.5
        dot_size = dot_size_start + t * (dot_size_end - dot_size_start)

        pts = [(cx + dot_size * math.cos(a), cy + dot_size * math.sin(a)) for a in circle_angles]
        polylines.append(gcode.Polyline(points=pts, layer=LAYER_TONE))

    return polylines


def _points_in_polygon(px, py, polygon):
    """Vectorised point-in-polygon test using cross-product method.

    Returns boolean array. Works for convex and simple concave polygons.
    """
    n = len(polygon)
    inside = np.ones(len(px), dtype=bool)
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        # Cross product: (edge × point) >= 0 means point is on the left side
        cross = (x2 - x1) * (py - y1) - (y2 - y1) * (px - x1)
        inside &= (cross >= 0)
    return inside


# ════════════════════════════════════════════════════════════════════
#  TOOL 4: SPEECH BUBBLES + TEXT
# ════════════════════════════════════════════════════════════════════

def generate_bubble(params: dict) -> list[gcode.Polyline]:
    """Generate speech bubble with text. Returns bubble outline + text polylines."""
    cx = params.get("cx", 50)
    cy = params.get("cy", 50)
    text = params.get("text", "")
    shape = params.get("shape", "ellipse")
    tail_dir = params.get("tail", "")
    font_style = params.get("font", "hershey")
    font_size = params.get("font_size", 5)
    padding = params.get("padding", 4)

    polylines: list[gcode.Polyline] = []

    # Measure text to determine bubble size
    text_w, text_h = 0, 0
    if text:
        scale = font_size / font.CHAR_HEIGHT
        text_w, text_h = font.measure_text(text, scale=scale, spacing=scale * 1.0,
                                           font_style=font_style)

    # Bubble sizing with padding
    min_w = max(text_w + 2 * padding, 12)
    min_h = max(text_h + 2 * padding, 8)

    # Generate bubble shape
    if shape == "ellipse":
        a = min_w / 2
        b = min_h / 2
        bubble_pts = _ellipse_points(cx, cy, a, b, 32)
        polylines.append(gcode.Polyline(points=bubble_pts, layer=LAYER_BORDER))
    elif shape == "rounded-rect":
        r = min(3, min_w / 4, min_h / 4)
        x0, y0 = cx - min_w / 2, cy - min_h / 2
        bubble_pts = _rounded_rect_pts(x0, y0, min_w, min_h, r)
        polylines.append(gcode.Polyline(points=bubble_pts, layer=LAYER_BORDER))
    elif shape == "jagged":
        a = min_w / 2
        b = min_h / 2
        bubble_pts = _jagged_ellipse(cx, cy, a, b, 24, 1.5)
        polylines.append(gcode.Polyline(points=bubble_pts, layer=LAYER_BORDER))
    elif shape == "whisper":
        a = min_w / 2
        b = min_h / 2
        pts = _ellipse_points(cx, cy, a, b, 32)
        # Convert to dashed: alternating draw/lift
        dash_len = 3
        for i in range(0, len(pts) - 1, dash_len * 2):
            end = min(i + dash_len, len(pts) - 1)
            polylines.append(gcode.Polyline(
                points=pts[i:end + 1], layer=LAYER_BORDER))
    elif shape == "thought":
        # Chain of circles
        radii = [3, 4, min(min_w, min_h) / 2]
        if tail_dir:
            # Position circles along tail direction
            dx, dy = _tail_direction(tail_dir)
            offsets = [8, 4, 0]
        else:
            dx, dy = 0, 1
            offsets = [8, 4, 0]
        for r, off in zip(radii, offsets):
            tcx = cx - dx * off
            tcy = cy - dy * off
            pts = _ellipse_points(tcx, tcy, r, r, 16)
            polylines.append(gcode.Polyline(points=pts, layer=LAYER_BORDER))
    else:
        # Default ellipse
        a = min_w / 2
        b = min_h / 2
        bubble_pts = _ellipse_points(cx, cy, a, b, 32)
        polylines.append(gcode.Polyline(points=bubble_pts, layer=LAYER_BORDER))

    # Tail
    if tail_dir and shape not in ("thought", "whisper"):
        tail_pts = _bubble_tail(cx, cy, min_w / 2, min_h / 2, tail_dir)
        if tail_pts:
            polylines.append(gcode.Polyline(points=tail_pts, layer=LAYER_BORDER))

    # Text — auto-fit inside bubble
    if text:
        scale = font_size / font.CHAR_HEIGHT
        max_attempts = 5
        for attempt in range(max_attempts):
            tw, th = font.measure_text(text, scale=scale, spacing=scale * 1.0,
                                       font_style=font_style)
            if tw <= min_w - padding and th <= min_h - padding:
                break
            scale *= 0.85

        # Center text at (cx, cy)
        text_x = cx - tw / 2
        text_y = cy - th / 2

        if font_style == "cursive":
            strokes = font.text_to_cursive(text, x=text_x, y=text_y, scale=scale,
                                           spacing=scale * 1.3, max_width=min_w - padding)
        else:
            strokes = font.text_to_strokes(text, x=text_x, y=text_y, scale=scale,
                                           spacing=scale * 1.0, max_width=min_w - padding,
                                           align="center")

        for stroke in strokes:
            polylines.append(gcode.Polyline(points=stroke, layer=LAYER_TEXT))

    return polylines


def generate_sfx(params: dict) -> list[gcode.Polyline]:
    """Large display text for sound effects."""
    text = params.get("text", "BOOM")
    size = params.get("size", 20)
    angle = params.get("angle", 0)
    style = params.get("font", "cursive")

    scale = size / font.CHAR_HEIGHT
    polylines = []

    if style == "cursive":
        strokes = font.text_to_cursive(text, x=0, y=0, scale=scale, spacing=scale * 1.3)
    else:
        strokes = font.text_to_strokes(text, x=0, y=0, scale=scale, spacing=scale * 1.0)

    # Rotate if needed
    if angle != 0:
        rad = math.radians(angle)
        cos_a, sin_a = math.cos(rad), math.sin(rad)
        rotated = []
        for stroke in strokes:
            pts = [(px * cos_a - py * sin_a, px * sin_a + py * cos_a) for px, py in stroke]
            rotated.append(pts)
        strokes = rotated

    for stroke in strokes:
        polylines.append(gcode.Polyline(points=stroke, layer=LAYER_TEXT))

    return polylines


# ════════════════════════════════════════════════════════════════════
#  COMPILE-PAGE: Walk JSON tree → merged polylines
# ════════════════════════════════════════════════════════════════════

def compile_page(page_desc: dict) -> list[gcode.Polyline]:
    """Walk the manga_page.json tree and compile all elements into layered polylines."""
    all_polylines: list[gcode.Polyline] = []

    # Panel borders
    all_polylines.extend(generate_panels(page_desc))

    # Per-panel children
    for panel in page_desc.get("panels", []):
        for child in panel.get("children", []):
            child_type = child.get("type", "")
            if child_type == "speed_lines":
                child["bounds"] = child.get("bounds", panel.get("bounds", [0, 0, 180, 175]))
                all_polylines.extend(generate_speed_lines(child))
            elif child_type == "tone":
                if not child.get("polygon") and child.get("bounds"):
                    b = child["bounds"]
                    child["polygon"] = [(b[0], b[1]), (b[2], b[1]), (b[2], b[3]), (b[0], b[3])]
                elif not child.get("polygon"):
                    pw = page_desc.get("page_width", 180)
                    ph = page_desc.get("page_height", 175)
                    b = panel.get("bounds", [0, 0, pw, ph])
                    child["polygon"] = [(b[0], b[1]), (b[2], b[1]), (b[2], b[3]), (b[0], b[3])]
                all_polylines.extend(generate_tone(child))
            elif child_type == "bubble":
                all_polylines.extend(generate_bubble(child))
            elif child_type == "effect":
                name = child.get("name", "")
                if name == "impact_burst":
                    all_polylines.extend(generate_impact_burst(child))
                elif name == "rain":
                    all_polylines.extend(generate_rain(child))
                elif name == "emotion":
                    all_polylines.extend(generate_emotion_lines(child))
            elif child_type == "sfx":
                # Offset SFX to panel center
                bounds = panel.get("bounds", [0, 0, 180, 175])
                cx = (bounds[0] + bounds[2]) / 2
                cy = (bounds[1] + bounds[3]) / 2
                sfx_pls = generate_sfx(child)
                # Offset to panel center
                for pl in sfx_pls:
                    pl.points = [(x + cx, y + cy) for x, y in pl.points]
                all_polylines.extend(sfx_pls)

    return all_polylines


# ════════════════════════════════════════════════════════════════════
#  SLATE IMPORT: detect panels from freehand strokes
# ════════════════════════════════════════════════════════════════════

def detect_panels(strokes: list[list[tuple[float, float]]],
                  page_w: float = 180, page_h: float = 175) -> list[dict]:
    """Detect rectangular regions in freehand BLE strokes from Slate.

    Groups strokes by proximity, finds bounding boxes, returns panel bounds.
    """
    if not strokes:
        return []

    # Compute bounding box for each stroke
    stroke_bounds = []
    for stroke in strokes:
        if len(stroke) < 2:
            continue
        xs = [p[0] for p in stroke]
        ys = [p[1] for p in stroke]
        stroke_bounds.append((min(xs), min(ys), max(xs), max(ys)))

    if not stroke_bounds:
        return []

    # Simple clustering: merge overlapping bounding boxes
    clusters = []
    used = [False] * len(stroke_bounds)

    for i, (x0, y0, x1, y1) in enumerate(stroke_bounds):
        if used[i]:
            continue
        cluster = [x0, y0, x1, y1]
        used[i] = True
        changed = True
        while changed:
            changed = False
            for j, (bx0, by0, bx1, by1) in enumerate(stroke_bounds):
                if used[j]:
                    continue
                # Check overlap
                if (bx0 <= cluster[2] + 10 and bx1 >= cluster[0] - 10 and
                        by0 <= cluster[3] + 10 and by1 >= cluster[1] - 10):
                    cluster[0] = min(cluster[0], bx0)
                    cluster[1] = min(cluster[1], by0)
                    cluster[2] = max(cluster[2], bx1)
                    cluster[3] = max(cluster[3], by1)
                    used[j] = True
                    changed = True
        clusters.append(cluster)

    # Convert to page coordinates and return as panel dicts
    panels = []
    bleed = 3
    for x0, y0, x1, y1 in clusters:
        # Snap to page bounds
        x0 = max(bleed, min(page_w - bleed, x0))
        y0 = max(bleed, min(page_h - bleed, y0))
        x1 = max(x0 + 10, min(page_w - bleed, x1))
        y1 = max(y0 + 10, min(page_h - bleed, y1))
        panels.append({"bounds": [round(x0, 1), round(y0, 1),
                                  round(x1, 1), round(y1, 1)], "children": []})

    return panels


# ════════════════════════════════════════════════════════════════════
#  GEOMETRY HELPERS
# ════════════════════════════════════════════════════════════════════

def _rect(x0, y0, x1, y1, layer=""):
    """Rectangle polyline."""
    return gcode.Polyline(
        points=[(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)],
        layer=layer)


def _ellipse_points(cx, cy, a, b, n=32):
    """Points on an ellipse."""
    return [(cx + a * math.cos(2 * math.pi * i / n),
             cy + b * math.sin(2 * math.pi * i / n)) for i in range(n + 1)]


def _rounded_rect_pts(x, y, w, h, r, n_corner=6):
    """Rounded rectangle points."""
    pts = []
    corners = [
        (x + r, y, x, y + r, math.pi, 1.5 * math.pi),
        (x, y + h - r, x + r, y + h, 1.5 * math.pi, 2 * math.pi),
        (x + w - r, y + h, x + w, y + h - r, 0, 0.5 * math.pi),
        (x + w - r, y, x + w, y + r, -0.5 * math.pi, 0),
    ]
    for ccx, ccy, _, _, start_a, end_a in corners:
        for i in range(n_corner + 1):
            a = start_a + (end_a - start_a) * i / n_corner
            pts.append((ccx + r * math.cos(a), ccy + r * math.sin(a)))
    pts.append(pts[0])
    return pts


def _jagged_ellipse(cx, cy, a, b, n=24, spike=1.5):
    """Ellipse with random outward spikes for jagged/excited bubble."""
    pts = []
    for i in range(n + 1):
        angle = 2 * math.pi * i / n
        r_offset = random.uniform(-spike, spike)
        ea = a + r_offset
        eb = b + r_offset
        pts.append((cx + ea * math.cos(angle), cy + eb * math.sin(angle)))
    pts.append(pts[0])
    return pts


def _tail_direction(tail_dir: str) -> tuple[float, float]:
    """Convert tail direction string to (dx, dy) unit vector."""
    dirs = {
        "bottom": (0, 1), "bottom-left": (-0.7, 0.7), "bottom-right": (0.7, 0.7),
        "top": (0, -1), "top-left": (-0.7, -0.7), "top-right": (0.7, -0.7),
        "left": (-1, 0), "right": (1, 0),
    }
    dx, dy = dirs.get(tail_dir, (0, 1))
    mag = math.hypot(dx, dy)
    return dx / mag, dy / mag


def _bubble_tail(cx, cy, a, b, tail_dir, length=10):
    """Generate tail points from bubble edge toward speaker."""
    dx, dy = _tail_direction(tail_dir)
    # Start at ellipse edge in tail direction
    # Parametric ellipse: point on edge in direction (dx, dy)
    # t where (a*cos(t), b*sin(t)) is closest to direction
    angle = math.atan2(dy * a, dx * b)  # correct for ellipse
    edge_x = cx + a * math.cos(angle)
    edge_y = cy + b * math.sin(angle)
    # End point
    end_x = edge_x + dx * length
    end_y = edge_y + dy * length
    return [(edge_x, edge_y), (end_x, end_y)]
