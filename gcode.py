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


# ── SVG Parsing ─────────────────────────────────────────────────────

def _flatten_curve(path_obj, num_segments: int = 64) -> list[tuple[float, float]]:
    """Flatten an svgpathtools path object into line segments."""
    points = []
    for i in range(num_segments + 1):
        t = i / num_segments
        pt = path_obj.point(t)
        points.append((pt.real, pt.imag))
    return points


def _parse_svg_paths(svg_path: str) -> list[Polyline]:
    """Parse an SVG file and extract all paths as polylines."""
    paths, _ = svg2paths(svg_path)
    polylines = []

    for path in paths:
        if len(path) == 0:
            continue

        # Build a continuous polyline for this path
        all_points = []
        for segment in path:
            if isinstance(segment, Line):
                start = (segment.start.real, segment.start.imag)
                end = (segment.end.real, segment.end.imag)
                if not all_points:
                    all_points.append(start)
                all_points.append(end)

            elif isinstance(segment, (CubicBezier, QuadraticBezier, Arc)):
                # Approximate curve with line segments
                seg_pts = _flatten_curve(segment)
                if not all_points:
                    all_points.append(seg_pts[0])
                else:
                    # Skip first point if it's close to the last one (continuity)
                    if (abs(seg_pts[0][0] - all_points[-1][0]) < 0.01 and
                            abs(seg_pts[0][1] - all_points[-1][1]) < 0.01):
                        seg_pts = seg_pts[1:]
                all_points.extend(seg_pts)
            else:
                # Fallback: flatten whatever it is
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
        if w > 0 and h > 0:
            polylines.append(Polyline(points=[
                (x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)
            ]))

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
        # Close polygon
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
            # Try forward
            d = _distance(current_pos, pl.points[0])
            if d < best_dist:
                best_dist = d
                best_idx = idx
                best_reversed = False
            # Try reversed
            d = _distance(current_pos, pl.points[-1])
            if d < best_dist:
                best_dist = d
                best_idx = idx
                best_reversed = True

        pl = polylines[best_idx]
        if best_reversed:
            pl = Polyline(points=list(reversed(pl.points)))
        ordered.append(pl)
        current_pos = pl.points[-1]
        remaining.remove(best_idx)

    return ordered


# ── G-code Generation ───────────────────────────────────────────────

def _water_dip_gcode(profile: config.ToolProfile) -> list[str]:
    """Generate G-code for a water/brush dip sequence."""
    w = profile.water
    lines = []
    approach_z = w.cup_height + 2.0  # Clear above cup rim
    dip_z = w.cup_height - w.dip_depth

    lines.append(f"; --- Water dip ---")
    lines.append(f"G1 Z{profile.height.pen_up_z:.3f} F{profile.movement.travel_speed:.0f}")
    lines.append(f"G0 X{w.cup_x:.3f} Y{w.cup_y:.3f}")
    lines.append(f"G1 Z{approach_z:.3f} F500")  # Lower to just above rim
    lines.append(f"G1 Z{dip_z:.3f} F500")       # Dip into water
    lines.append(f"G4 P{w.dip_time}")            # Dwell
    lines.append(f"G1 Z{profile.height.pen_up_z:.3f} F500")  # Lift out
    # Blot excess water
    lines.append(f"G0 X{w.blot_x:.3f} Y{w.blot_y:.3f}")
    blot_z = profile.height.pen_down_z + 1.0
    lines.append(f"G1 Z{blot_z:.3f} F500")
    lines.append(f"G4 P200")
    lines.append(f"G1 Z{profile.height.pen_up_z:.3f} F500")
    lines.append(f"; --- End water dip ---")
    return lines


def polylines_to_gcode(
    polylines: list[Polyline],
    profile: config.ToolProfile,
    bed_x: float = config.PRINTER_BED_X,
    bed_y: float = config.PRINTER_BED_Y,
) -> str:
    """Convert polylines to G-code string."""
    if not polylines:
        return ""

    mv = profile.movement
    ht = profile.height
    wt = profile.water

    # Calculate bounding box of all points for centering/scaling
    all_pts = [p for pl in polylines for p in pl.points]
    min_x = min(p[0] for p in all_pts)
    max_x = max(p[0] for p in all_pts)
    min_y = min(p[1] for p in all_pts)
    max_y = max(p[1] for p in all_pts)
    svg_w = max_x - min_x
    svg_h = max_y - min_y

    # Scale to fit bed with 10mm margin
    margin = 10.0
    available = bed_x - 2 * margin, bed_y - 2 * margin
    if svg_w > 0 and svg_h > 0:
        scale = min(available[0] / svg_w, available[1] / svg_h)
    else:
        scale = 1.0

    # Center offset
    scaled_w = svg_w * scale
    scaled_h = svg_h * scale
    offset_x = (bed_x - scaled_w) / 2 - min_x * scale
    offset_y = (bed_y - scaled_h) / 2 - min_y * scale

    def transform(px, py):
        return round(px * scale + offset_x, 3), round(py * scale + offset_y, 3)

    lines = []
    lines.append("; Pen Plotter G-code")
    lines.append(f"; Tool: {profile.name}")
    lines.append(f"; Original SVG: {svg_w:.1f} x {svg_h:.1f} mm")
    lines.append(f"; Scaled to: {scaled_w:.1f} x {scaled_h:.1f} mm (scale {scale:.4f})")
    lines.append("")
    lines.append("G28 ; Home all axes")
    lines.append("G90 ; Absolute positioning")
    lines.append(f"G1 Z{ht.pen_up_z:.3f} F{mv.travel_speed:.0f} ; Pen up")
    lines.append("")

    total_segments = sum(len(pl.points) - 1 for pl in polylines)
    segment_count = 0

    for pl_idx, polyline in enumerate(polylines):
        if len(polyline.points) < 2:
            continue

        lines.append(f"; Stroke {pl_idx + 1}")

        # Water dip check (before each stroke)
        if wt.enabled and segment_count > 0 and segment_count % wt.dip_interval == 0:
            lines.extend(_water_dip_gcode(profile))

        # Move to start (pen up)
        sx, sy = transform(*polyline.points[0])
        lines.append(f"G0 X{sx:.3f} Y{sy:.3f} ; Travel to start")

        # Pen down
        lines.append(f"G1 Z{ht.pen_down_z:.3f} F{mv.travel_speed:.0f} ; Pen down")

        # Draw
        for i in range(1, len(polyline.points)):
            px, py = transform(*polyline.points[i])
            lines.append(f"G1 X{px:.3f} Y{py:.3f} F{mv.draw_speed:.0f}")
            segment_count += 1

        # Pen up
        lines.append(f"G1 Z{ht.pen_up_z:.3f} F{mv.travel_speed:.0f} ; Pen up")

    # Final water dip if needed
    if wt.enabled and segment_count % wt.dip_interval != 0:
        pass  # already dipped at last interval

    lines.append("")
    lines.append("; Park")
    lines.append(f"G1 Z{ht.pen_up_z + 10:.3f} F{mv.travel_speed:.0f} ; Lift clear")
    lines.append("G0 X0 Y0 ; Home position")
    lines.append("M84 ; Disable motors")

    return "\n".join(lines)


# ── High-level API ──────────────────────────────────────────────────

def svg_to_gcode(svg_path: str, tool_name: str) -> tuple[str, list[Polyline]]:
    """Full pipeline: SVG file → optimized G-code string + polylines for preview."""
    polylines = parse_svg(svg_path)
    optimized = optimize_path(polylines)
    profile = config.load_profile(tool_name)
    gcode = polylines_to_gcode(optimized, profile)
    return gcode, optimized
