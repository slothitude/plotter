"""Pen Plotter — SVG to G-code conversion engine."""

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from svgpathtools import (
    parse_path,
    Line,
    CubicBezier,
    QuadraticBezier,
    Arc,
    Path as SvgPath,
    svg2paths,
    Document,
)

import config


# ── Data structures ─────────────────────────────────────────────────

@dataclass
class Polyline:
    """A series of connected (x, y) points forming a stroke."""
    points: list[tuple[float, float]]
    layer: int = 0
    color: str = "#00e87b"


@dataclass
class PlotStats:
    """Statistics about a generated plot."""
    stroke_count: int = 0
    point_count: int = 0
    draw_distance_mm: float = 0.0
    travel_distance_mm: float = 0.0
    estimated_time_s: float = 0.0
    bounds: dict = None  # {min_x, min_y, max_x, max_y}

    def __post_init__(self):
        if self.bounds is None:
            self.bounds = {"min_x": 0, "min_y": 0, "max_x": 0, "max_y": 0}


# ── SVG Parsing ─────────────────────────────────────────────────────

def _flatten_curve(path_obj, num_segments: int = 64) -> list[tuple[float, float]]:
    """Flatten an svgpathtools path object into line segments."""
    points = []
    for i in range(num_segments + 1):
        t = i / num_segments
        pt = path_obj.point(t)
        points.append((pt.real, pt.imag))
    return points


def _rounded_rect_points(x, y, w, h, rx, ry, n_corner=8):
    """Generate points for a rounded rectangle."""
    pts = []
    # Clamp radii
    rx = min(rx, w / 2)
    ry = min(ry, h / 2)

    corners = [
        (x + rx, y, x, y + ry, math.pi, 1.5 * math.pi),        # top-left
        (x, y + h - ry, x + rx, y + h, 1.5 * math.pi, 2 * math.pi),  # bottom-left
        (x + w - rx, y + h, x + w, y + h - ry, 0, 0.5 * math.pi),    # bottom-right
        (x + w - rx, y, x + w, y + ry, -0.5 * math.pi, 0),           # top-right
    ]
    for cx, cy, ex, ey, start_a, end_a in corners:
        for i in range(n_corner + 1):
            t = i / n_corner
            a = start_a + t * (end_a - start_a)
            pts.append((cx + rx * math.cos(a), cy + ry * math.sin(a)))

    # Close
    pts.append(pts[0])
    return pts


def _parse_svg_paths(svg_path: str) -> list[Polyline]:
    """Parse an SVG file and extract all paths as polylines."""
    paths, _ = svg2paths(svg_path)
    polylines = []

    for path in paths:
        if len(path) == 0:
            continue

        all_points = []
        for segment in path:
            if isinstance(segment, Line):
                start = (segment.start.real, segment.start.imag)
                end = (segment.end.real, segment.end.imag)
                if not all_points:
                    all_points.append(start)
                all_points.append(end)

            elif isinstance(segment, (CubicBezier, QuadraticBezier, Arc)):
                seg_pts = _flatten_curve(segment)
                if not all_points:
                    all_points.append(seg_pts[0])
                else:
                    if (abs(seg_pts[0][0] - all_points[-1][0]) < 0.01 and
                            abs(seg_pts[0][1] - all_points[-1][1]) < 0.01):
                        seg_pts = seg_pts[1:]
                all_points.extend(seg_pts)
            else:
                seg_pts = _flatten_curve(segment)
                if not all_points:
                    all_points.append(seg_pts[0])
                else:
                    seg_pts = seg_pts[1:]
                all_points.extend(seg_pts)

        if len(all_points) >= 2:
            polylines.append(Polyline(points=all_points))

    return polylines


def _parse_svg_basic_shapes(svg_path: str) -> list[Polyline]:
    """Parse basic SVG shapes (rect, circle, ellipse, line, polyline, polygon)."""
    polylines = []
    try:
        doc = Document(svg_path)
    except Exception:
        return polylines

    tree = doc.tree
    ns = {"svg": "http://www.w3.org/2000/svg"}

    # Rectangles
    for rect in tree.iter("{http://www.w3.org/2000/svg}rect"):
        x = float(rect.get("x", 0))
        y = float(rect.get("y", 0))
        w = float(rect.get("width", 0))
        h = float(rect.get("height", 0))
        rx = float(rect.get("rx", 0))
        ry = float(rect.get("ry", rx))
        if w > 0 and h > 0:
            if rx > 0 or ry > 0:
                pts = _rounded_rect_points(x, y, w, h, rx, ry)
            else:
                pts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]
            polylines.append(Polyline(points=pts))

    # Circles
    for circle in tree.iter("{http://www.w3.org/2000/svg}circle"):
        cx = float(circle.get("cx", 0))
        cy = float(circle.get("cy", 0))
        r = float(circle.get("r", 0))
        if r > 0:
            pts = []
            for i in range(65):
                angle = 2 * math.pi * i / 64
                pts.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
            polylines.append(Polyline(points=pts))

    # Ellipses
    for ellipse in tree.iter("{http://www.w3.org/2000/svg}ellipse"):
        cx = float(ellipse.get("cx", 0))
        cy = float(ellipse.get("cy", 0))
        rx = float(ellipse.get("rx", 0))
        ry = float(ellipse.get("ry", 0))
        if rx > 0 and ry > 0:
            pts = []
            for i in range(65):
                angle = 2 * math.pi * i / 64
                pts.append((cx + rx * math.cos(angle), cy + ry * math.sin(angle)))
            polylines.append(Polyline(points=pts))

    # Lines
    for line in tree.iter("{http://www.w3.org/2000/svg}line"):
        x1 = float(line.get("x1", 0))
        y1 = float(line.get("y1", 0))
        x2 = float(line.get("x2", 0))
        y2 = float(line.get("y2", 0))
        polylines.append(Polyline(points=[(x1, y1), (x2, y2)]))

    # Polylines & Polygons
    for pl in list(tree.iter("{http://www.w3.org/2000/svg}polyline")) + \
              list(tree.iter("{http://www.w3.org/2000/svg}polygon")):
        points_str = pl.get("points", "")
        if not points_str:
            continue
        pts = []
        for pair in re.findall(r'[\d.eE+-]+,[\d.eE+-]+', points_str):
            x, y = pair.split(",")
            pts.append((float(x), float(y)))
        tag = pl.tag.split("}")[-1] if "}" in pl.tag else pl.tag
        if tag == "polygon" and len(pts) >= 3:
            pts.append(pts[0])
        if len(pts) >= 2:
            polylines.append(Polyline(points=pts))

    return polylines


def parse_svg(svg_path: str) -> list[Polyline]:
    """Parse an SVG file into polylines (paths + basic shapes)."""
    path_polylines = _parse_svg_paths(svg_path)
    shape_polylines = _parse_svg_basic_shapes(svg_path)
    return path_polylines + shape_polylines


# ── Path Optimization ───────────────────────────────────────────────

def _distance(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    return math.hypot(p2[0] - p1[0], p2[1] - p1[1])


def optimize_path(polylines: list[Polyline]) -> list[Polyline]:
    """Reorder polylines using nearest-neighbor to minimize travel."""
    if len(polylines) <= 1:
        return polylines

    remaining = list(range(len(polylines)))
    ordered = []
    current_pos = (0.0, 0.0)

    while remaining:
        best_idx = None
        best_dist = float("inf")
        best_reversed = False

        for idx in remaining:
            pl = polylines[idx]
            d = _distance(current_pos, pl.points[0])
            if d < best_dist:
                best_dist = d
                best_idx = idx
                best_reversed = False
            d = _distance(current_pos, pl.points[-1])
            if d < best_dist:
                best_dist = d
                best_idx = idx
                best_reversed = True

        pl = polylines[best_idx]
        if best_reversed:
            pl = Polyline(points=list(reversed(pl.points)), layer=pl.layer, color=pl.color)
        ordered.append(pl)
        current_pos = pl.points[-1]
        remaining.remove(best_idx)

    return ordered


# ── Simplification ──────────────────────────────────────────────────

def _perpendicular_distance(point, line_start, line_end):
    """Distance from point to line segment."""
    dx = line_end[0] - line_start[0]
    dy = line_end[1] - line_start[1]
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return _distance(point, line_start)
    t = max(0, min(1, ((point[0] - line_start[0]) * dx + (point[1] - line_start[1]) * dy) / length_sq))
    proj_x = line_start[0] + t * dx
    proj_y = line_start[1] + t * dy
    return _distance(point, (proj_x, proj_y))


def simplify_polyline(points: list[tuple[float, float]], tolerance: float = 0.1) -> list[tuple[float, float]]:
    """Ramer-Douglas-Peucker simplification."""
    if len(points) <= 2:
        return points

    max_dist = 0
    max_idx = 0
    for i in range(1, len(points) - 1):
        d = _perpendicular_distance(points[i], points[0], points[-1])
        if d > max_dist:
            max_dist = d
            max_idx = i

    if max_dist > tolerance:
        left = simplify_polyline(points[:max_idx + 1], tolerance)
        right = simplify_polyline(points[max_idx:], tolerance)
        return left[:-1] + right
    else:
        return [points[0], points[-1]]


# ── Stats ───────────────────────────────────────────────────────────

def compute_stats(polylines: list[Polyline], travel_segments: list[tuple] = None,
                  draw_speed: float = 1500.0, travel_speed: float = 3000.0) -> PlotStats:
    """Compute plot statistics."""
    if not polylines:
        return PlotStats()

    stroke_count = len(polylines)
    point_count = sum(len(pl.points) for pl in polylines)

    # Draw distance
    draw_dist = 0.0
    for pl in polylines:
        for i in range(1, len(pl.points)):
            draw_dist += _distance(pl.points[i - 1], pl.points[i])

    # Travel distance
    travel_dist = 0.0
    if travel_segments:
        for seg in travel_segments:
            travel_dist += _distance(seg[0], seg[1])
    else:
        prev_end = (0.0, 0.0)
        for pl in polylines:
            travel_dist += _distance(prev_end, pl.points[0])
            prev_end = pl.points[-1]

    # Bounds
    all_pts = [p for pl in polylines for p in pl.points]
    bounds = {
        "min_x": min(p[0] for p in all_pts),
        "min_y": min(p[1] for p in all_pts),
        "max_x": max(p[0] for p in all_pts),
        "max_y": max(p[1] for p in all_pts),
    }

    # Time estimate (mm / mm/min = minutes, convert to seconds)
    draw_time = (draw_dist / draw_speed) * 60 if draw_speed > 0 else 0
    travel_time = (travel_dist / travel_speed) * 60 if travel_speed > 0 else 0
    estimated_time = draw_time + travel_time

    return PlotStats(
        stroke_count=stroke_count,
        point_count=point_count,
        draw_distance_mm=round(draw_dist, 1),
        travel_distance_mm=round(travel_dist, 1),
        estimated_time_s=round(estimated_time, 1),
        bounds=bounds,
    )


# ── Fill Generation ─────────────────────────────────────────────────

def _is_closed(points: list[tuple[float, float]], tolerance: float = 0.1) -> bool:
    """Check if a polyline is closed (first point ≈ last point)."""
    if len(points) < 3:
        return False
    return _distance(points[0], points[-1]) <= tolerance


def _polygon_bounds(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    """Return (min_x, min_y, max_x, max_y) for a set of points."""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _intersect_scanlines(edges_x1, edges_y1, edges_x2, edges_y2, scan_y):
    """Vectorized scanline intersection: find x-coordinates where horizontal
    lines at y=scan_y cross each edge segment. Returns sorted x array."""
    # Edge goes from (x1,y1) to (x2,y2) — shape (N,)
    dy = edges_y2 - edges_y1
    # Avoid division by zero for horizontal edges
    valid = np.abs(dy) > 1e-10
    t = np.where(valid, (scan_y - edges_y1) / np.where(valid, dy, 1.0), -1.0)
    # t must be in [0, 1) — intersection is within the segment
    hit = valid & (t >= 0.0) & (t < 1.0)
    x_hit = edges_x1 + t * (edges_x2 - edges_x1)
    return np.sort(x_hit[hit])


def generate_hatch_fill(points: list[tuple[float, float]], spacing: float,
                        angle: float) -> list[Polyline]:
    """Generate hatch fill polylines for a closed polygon.

    Strategy: rotate the polygon so the hatch angle becomes horizontal,
    sweep horizontal scanlines computing edge intersections, pair up
    intersections into line segments, then rotate back.
    """
    if len(points) < 3 or spacing <= 0:
        return []

    rad = math.radians(-angle)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    cos_b, sin_b = math.cos(-rad), math.sin(-rad)  # inverse rotation

    # Rotate polygon so hatch direction is horizontal
    rotated = [(p[0] * cos_a - p[1] * sin_a, p[0] * sin_a + p[1] * cos_a) for p in points]
    min_x, min_y, max_x, max_y = _polygon_bounds(rotated)

    # Build edge arrays for vectorised intersection
    n = len(rotated) - 1  # last == first for closed poly
    ex1 = np.array([rotated[i][0] for i in range(n)])
    ey1 = np.array([rotated[i][1] for i in range(n)])
    ex2 = np.array([rotated[i + 1][0] for i in range(n)])
    ey2 = np.array([rotated[i + 1][1] for i in range(n)])

    # Sweep scanlines
    fills: list[Polyline] = []
    y = min_y + spacing * 0.5
    while y < max_y:
        xs = _intersect_scanlines(ex1, ey1, ex2, ey2, y)
        # Pair up: 0-1, 2-3, ...
        for i in range(0, len(xs) - 1, 2):
            x0, x1_val = float(xs[i]), float(xs[i + 1])
            if x1_val - x0 < 0.01:
                continue
            # Rotate back to original coordinate space
            p0 = (x0 * cos_b - y * sin_b, x0 * sin_b + y * cos_b)
            p1 = (x1_val * cos_b - y * sin_b, x1_val * sin_b + y * cos_b)
            fills.append(Polyline(points=[p0, p1]))
        y += spacing

    return fills


def apply_fill(polylines: list[Polyline], fill_config) -> list[Polyline]:
    """Insert fill polylines before each closed outline polyline."""
    if not fill_config or not fill_config.enabled:
        return polylines

    result: list[Polyline] = []
    for pl in polylines:
        if _is_closed(pl.points):
            # Generate hatch fill
            hatch_lines = generate_hatch_fill(pl.points, fill_config.spacing, fill_config.angle)
            result.extend(hatch_lines)
            # Crosshatch: second layer at angle + 90
            if fill_config.fill_type == "crosshatch":
                cross_lines = generate_hatch_fill(pl.points, fill_config.spacing, fill_config.angle + 90)
                result.extend(cross_lines)
        result.append(pl)
    return result


# ── G-code Generation ───────────────────────────────────────────────

def _water_dip_gcode(profile: config.ToolProfile) -> list[str]:
    """Generate G-code for a water/brush dip + rim scrape sequence."""
    w = profile.water
    lines = []
    dip_z = w.cup_height - w.dip_depth

    safe_z = max(config.SAFE_Z, profile.height.pen_up_z)
    lines.append(f"; --- Water dip ---")
    lines.append(f"G1 Z{safe_z:.3f} F{profile.movement.travel_speed:.0f}")
    lines.append(f"G0 X{w.cup_x:.3f} Y{w.cup_y:.3f}")
    lines.append(f"G1 Z{dip_z:.3f} F500")
    lines.append(f"G4 P{w.dip_time}")
    lines.append(f"G1 Z{w.cup_height:.3f} F500")
    lines.append(f"G1 X{w.cup_x + w.scrape_distance:.3f} F{w.scrape_speed:.0f}")
    lines.append(f"G1 Z{safe_z:.3f} F500")
    lines.append(f"; --- End water dip ---")
    return lines


def polylines_to_gcode(
    polylines: list[Polyline],
    profile: config.ToolProfile,
    bed_x: float = config.PRINTER_BED_X,
    bed_y: float = config.PRINTER_BED_Y,
    page_offset_x: float = 0.0,
    page_offset_y: float = 0.0,
    optimize: bool = True,
    simplify: bool = False,
    simplify_tolerance: float = 0.1,
    user_scale: float = 1.0,
    user_rotate: float = 0.0,
    user_translate_x: float = 0.0,
    user_translate_y: float = 0.0,
    mirror_x: bool = False,
    mirror_y: bool = False,
) -> tuple[str, list[dict]]:
    """Convert polylines to G-code string. Returns (gcode_str, toolpath_data).

    toolpath_data is a list of dicts: {type: "draw"|"travel", points: [[x,y],...], layer: int}
    """
    if not polylines:
        return "", []

    mv = profile.movement
    ht = profile.height
    wt = profile.water

    # 1. Apply simplification
    if simplify:
        simplified = []
        for pl in polylines:
            pts = simplify_polyline(pl.points, simplify_tolerance)
            if len(pts) >= 2:
                simplified.append(Polyline(points=pts, layer=pl.layer, color=pl.color))
        polylines = simplified

    # 2. Apply user transforms: scale → rotate → mirror → translate
    if user_scale != 1.0 or user_rotate != 0.0 or mirror_x or mirror_y or user_translate_x != 0.0 or user_translate_y != 0.0:
        transformed = []
        cos_r = math.cos(math.radians(user_rotate))
        sin_r = math.sin(math.radians(user_rotate))
        for pl in polylines:
            new_pts = []
            for px, py in pl.points:
                # Scale
                x, y = px * user_scale, py * user_scale
                # Rotate
                rx = x * cos_r - y * sin_r
                ry = x * sin_r + y * cos_r
                # Mirror
                if mirror_x:
                    rx = -rx
                if mirror_y:
                    ry = -ry
                # Translate
                rx += user_translate_x
                ry += user_translate_y
                new_pts.append((rx, ry))
            transformed.append(Polyline(points=new_pts, layer=pl.layer, color=pl.color))
        polylines = transformed

    # 3. Optimize path order
    if optimize:
        polylines = optimize_path(polylines)

    # 4. Calculate bounding box, auto-scale to fit effective area, center
    all_pts = [p for pl in polylines for p in pl.points]
    min_x = min(p[0] for p in all_pts)
    max_x = max(p[0] for p in all_pts)
    min_y = min(p[1] for p in all_pts)
    max_y = max(p[1] for p in all_pts)
    svg_w = max_x - min_x
    svg_h = max_y - min_y

    # Compute pen offset correction and effective drawing area
    pen_raw_ox = ht.offset_x
    pen_raw_oy = ht.offset_y

    if pen_raw_ox == 0.0 and pen_raw_oy == 0.0:
        # No offset — full bed available
        pen_cx, pen_cy = 0.0, 0.0
        eff_w, eff_h = bed_x, bed_y
        eff_ox, eff_oy = 0.0, 0.0
    else:
        # Correction: stored_offset - center
        pen_cx = pen_raw_ox - bed_x / 2
        pen_cy = pen_raw_oy - bed_y / 2
        # Physical pen position relative to hotend
        pen_phys_x = -pen_cx
        pen_phys_y = -pen_cy
        # Effective area: where the pen can actually reach on the bed
        eff_ox = max(0.0, pen_phys_x)
        eff_oy = max(0.0, pen_phys_y)
        eff_w = min(bed_x, bed_x + pen_phys_x) - eff_ox
        eff_h = min(bed_y, bed_y + pen_phys_y) - eff_oy

    margin = 10.0
    available = eff_w - 2 * margin, eff_h - 2 * margin
    if svg_w > 0 and svg_h > 0:
        scale = min(available[0] / svg_w, available[1] / svg_h)
    else:
        scale = 1.0

    scaled_w = svg_w * scale
    scaled_h = svg_h * scale
    # Center within effective area
    offset_x = eff_ox + (eff_w - scaled_w) / 2 - min_x * scale
    offset_y = eff_oy + (eff_h - scaled_h) / 2 - min_y * scale

    def transform(px, py):
        return (round(px * scale + offset_x + page_offset_x, 3),
                round(py * scale + offset_y + page_offset_y, 3))

    # 5. Generate G-code + collect toolpath
    lines = []
    toolpath = []

    lines.append("; Pen Plotter G-code")
    lines.append(f"; Tool: {profile.name}")
    lines.append(f"; Original SVG: {svg_w:.1f} x {svg_h:.1f} mm")
    lines.append(f"; Scaled to: {scaled_w:.1f} x {scaled_h:.1f} mm (scale {scale:.4f})")
    if user_scale != 1.0:
        lines.append(f"; User scale: {user_scale:.2f}")
    if user_rotate != 0.0:
        lines.append(f"; User rotate: {user_rotate:.1f} deg")
    lines.append("")
    lines.append("G28 ; Home all axes")
    lines.append("G90 ; Absolute positioning")
    safe_z = max(config.SAFE_Z, ht.pen_up_z)
    lines.append(f"G1 Z{safe_z:.3f} F{mv.travel_speed:.0f} ; Safe travel height")
    lines.append("")

    total_segments = sum(len(pl.points) - 1 for pl in polylines)
    segment_count = 0
    prev_pos = (0.0, 0.0)
    cumulative_draw_dist = 0.0

    for pl_idx, polyline in enumerate(polylines):
        if len(polyline.points) < 2:
            continue

        lines.append(f"; Stroke {pl_idx + 1}")

        # Water dip check
        if wt.enabled and segment_count > 0 and segment_count % wt.dip_interval == 0:
            lines.extend(_water_dip_gcode(profile))

        # Move to start (pen up) — travel move
        sx, sy = transform(*polyline.points[0])
        lines.append(f"G0 X{sx:.3f} Y{sy:.3f} ; Travel to start")

        # Record travel move in toolpath
        travel_pts = [[round(prev_pos[0], 2), round(prev_pos[1], 2)], [sx, sy]]
        toolpath.append({"type": "travel", "points": travel_pts, "layer": polyline.layer})
        prev_pos = (sx, sy)

        # Pen down — with wear compensation
        if mv.wear_rate > 0:
            wear = cumulative_draw_dist / 1000.0 * mv.wear_rate
            if mv.max_wear_depth > 0:
                wear = min(wear, mv.max_wear_depth)
            wear_z = ht.pen_down_z - wear
        else:
            wear_z = ht.pen_down_z
        lines.append(f"G1 Z{wear_z:.3f} F{mv.travel_speed:.0f} ; Pen down")

        # Draw — collect draw move
        draw_pts = [[sx, sy]]
        for i in range(1, len(polyline.points)):
            px, py = transform(*polyline.points[i])
            if mv.wear_rate > 0:
                seg_dist = math.hypot(px - prev_pos[0], py - prev_pos[1])
                cumulative_draw_dist += seg_dist
                wear = cumulative_draw_dist / 1000.0 * mv.wear_rate
                if mv.max_wear_depth > 0:
                    wear = min(wear, mv.max_wear_depth)
                seg_z = ht.pen_down_z - wear
                lines.append(f"G1 X{px:.3f} Y{py:.3f} Z{seg_z:.3f} F{mv.draw_speed:.0f}")
            else:
                lines.append(f"G1 X{px:.3f} Y{py:.3f} F{mv.draw_speed:.0f}")
            draw_pts.append([px, py])
            segment_count += 1
            prev_pos = (px, py)

        toolpath.append({"type": "draw", "points": draw_pts, "layer": polyline.layer})

        # Pen up to safe travel height
        lines.append(f"G1 Z{safe_z:.3f} F{mv.travel_speed:.0f} ; Pen up (safe Z)")

    lines.append("")
    lines.append("; Park")
    lines.append(f"G1 Z{safe_z:.3f} F{mv.travel_speed:.0f} ; Safe Z")
    lines.append("G0 X0 Y0 ; Home position")
    lines.append("M84 ; Disable motors")

    return "\n".join(lines), toolpath, {
        "effective_area": {"x": round(eff_ox, 1), "y": round(eff_oy, 1),
                           "width": round(eff_w, 1), "height": round(eff_h, 1)},
    }


# ── High-level API ──────────────────────────────────────────────────

def svg_to_gcode(svg_path: str, tool_name: str, **kwargs) -> tuple[str, list[Polyline], list[dict], PlotStats, dict]:
    """Full pipeline: SVG file → optimized G-code + polylines + toolpath + stats + meta."""
    polylines = parse_svg(svg_path)

    profile = config.load_profile(tool_name)
    polylines = apply_fill(polylines, profile.fill)
    gcode_str, toolpath, meta = polylines_to_gcode(polylines, profile, **kwargs)

    # Compute stats from toolpath data
    travel_segs = []
    for seg in toolpath:
        if seg["type"] == "travel" and len(seg["points"]) >= 2:
            travel_segs.append((tuple(seg["points"][0]), tuple(seg["points"][-1])))

    # Get the actual polylines as they were after transforms (for preview)
    # Re-extract from toolpath draw segments
    preview_polylines = []
    for seg in toolpath:
        if seg["type"] == "draw" and len(seg["points"]) >= 2:
            preview_polylines.append(Polyline(
                points=[tuple(p) for p in seg["points"]],
                layer=seg.get("layer", 0),
            ))

    stats = compute_stats(
        preview_polylines,
        travel_segments=travel_segs,
        draw_speed=profile.movement.draw_speed,
        travel_speed=profile.movement.travel_speed,
    )

    return gcode_str, preview_polylines, toolpath, stats, meta
