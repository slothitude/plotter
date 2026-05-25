"""Pen Plotter — Flask web application."""

# Fix: WMI query can hang on Windows, blocking numpy/scipy imports
import platform
platform._wmi = type('M', (), {'exec_query': lambda self, q: None})()

import base64
import json
import math
import os
import shutil
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

import cv2
import numpy as np
import requests
import svgwrite
from flask import Flask, request, jsonify, send_from_directory, send_file
from flask_sock import Sock

import config
import gcode
import serial_conn

app = Flask(__name__, static_folder="static")
sock = Sock(app)

# Global state
serial = serial_conn.SerialConnection()
SERIAL_PORT_FILE = Path(__file__).parent / "serial_port.txt"


def _ensure_connected():
    """Auto-connect to the saved serial port if not already connected."""
    if serial.is_connected:
        return True
    port = SERIAL_PORT_FILE.read_text().strip() if SERIAL_PORT_FILE.exists() else None
    if not port:
        # Try first available port
        ports = serial_conn.SerialConnection.list_ports()
        if ports:
            port = ports[0]["port"]
    if port:
        try:
            serial.connect(port)
            return True
        except Exception:
            return False
    return False
uploaded_svgs: dict[str, str] = {}  # id -> file path
generated_gcode: dict[str, str] = {}  # id -> gcode string
text_polylines: dict[str, list] = {}  # id -> polylines (from text patterns, no SVG round-trip)
ws_clients: list = []

# Ink / Slate state
_slate_process: subprocess.Popen | None = None
_ink_strokes: list = []  # accumulated BLE streamed strokes from capture.py
PENZ_DIR = r"C:\Users\aaron\penz"

# Live plot state
_live_plot_active = False
_live_plot_profile = None
_live_stroke_points: list = []  # accumulate mm points for current stroke


def _wacom_to_bed(wacom_x, wacom_y, page_w, page_h):
    """Map Wacom Slate A5 portrait coords to plotter bed mm.

    Slate is portrait: 21600 (X) x 14700 (Y).
    Same mapping as the JS fix: swap X/Y and rotate 180°.
    """
    x_mm = (1 - wacom_y / 14700) * page_w
    y_mm = wacom_x / 21600 * page_h
    return round(x_mm, 3), round(y_mm, 3)


def _stroke_to_gcode(points_mm, profile):
    """Convert a list of (x_mm, y_mm) points to G-code lines for one stroke."""
    if len(points_mm) < 2:
        return []
    ht = profile.height
    mv = profile.movement
    lines = []
    x0, y0 = points_mm[0]
    lines.append(f"G0 X{x0:.3f} Y{y0:.3f} F{mv.travel_speed:.0f}")
    lines.append(f"G1 Z{ht.pen_down_z:.3f} F3000")
    for x, y in points_mm[1:]:
        lines.append(f"G1 X{x:.3f} Y{y:.3f} F{mv.draw_speed:.0f}")
    lines.append(f"G1 Z{ht.pen_up_z:.3f} F3000")
    return lines


# ── WebSocket ────────────────────────────────────────────────────────

def _broadcast_progress(completed, total, info):
    """Broadcast progress to all connected WebSocket clients."""
    msg = json.dumps({
        "type": "progress",
        "completed": completed,
        "total": total,
        "info": info,
    })
    for ws in ws_clients[:]:
        try:
            ws.send(msg)
        except Exception:
            ws_clients.remove(ws)


def _broadcast_ink(points):
    """Broadcast ink stroke data to all connected WebSocket clients."""
    msg = json.dumps({"type": "ink", "points": points})
    for ws in ws_clients[:]:
        try:
            ws.send(msg)
        except Exception:
            ws_clients.remove(ws)


@sock.route("/ws")
def websocket(ws):
    ws_clients.append(ws)
    try:
        while True:
            ws.receive()  # Keep alive
    except Exception:
        pass
    finally:
        if ws in ws_clients:
            ws_clients.remove(ws)


# ── Web UI ───────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/<path:path>")
def static_files(path):
    resp = send_from_directory("static", path)
    if path.endswith(".js"):
        resp.headers["Content-Type"] = "text/javascript; charset=utf-8"
    return resp


# ── Serial ───────────────────────────────────────────────────────────

@app.route("/api/ports", methods=["GET"])
def list_ports():
    return jsonify(serial.list_ports())


@app.route("/api/serial/connect", methods=["POST"])
def serial_connect():
    data = request.json or {}
    port = data.get("port")
    baudrate = data.get("baudrate", 250000)
    if not port:
        return jsonify({"error": "port required"}), 400
    try:
        serial.connect(port, baudrate)
        SERIAL_PORT_FILE.write_text(port)
        return jsonify({"ok": True, "port": port})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/serial/disconnect", methods=["POST"])
def serial_disconnect():
    serial.disconnect()
    return jsonify({"ok": True})


@app.route("/api/status", methods=["GET"])
def get_status():
    pos = {}
    if serial.is_connected and not serial._sending:
        try:
            pos = serial.get_position()
        except Exception:
            pass
    return jsonify({
        "connected": serial.is_connected,
        "busy": serial._sending or serial._live_sending,
        "live_plot": _live_plot_active,
        "position": pos,
    })


@app.route("/api/send-command", methods=["POST"])
def send_command():
    """Send a raw G-code command and return the response."""
    if not serial.is_connected:
        return jsonify({"error": "Printer not connected"}), 400
    data = request.json or {}
    command = data.get("command", "").strip()
    if not command:
        return jsonify({"error": "command required"}), 400
    try:
        serial.send_command(command)
        return jsonify({"ok": True, "command": command})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Upload & Convert ────────────────────────────────────────────────

def _get_transform_params(data: dict) -> dict:
    """Extract transform parameters from request data."""
    return {
        "optimize": bool(data.get("optimize", True)),
        "simplify": bool(data.get("simplify", False)),
        "simplify_tolerance": float(data.get("simplify_tolerance", 0.1)),
        "user_scale": float(data.get("scale", 1.0)),
        "user_rotate": float(data.get("rotate", 0.0)),
        "user_translate_x": float(data.get("translate_x", 0.0)),
        "user_translate_y": float(data.get("translate_y", 0.0)),
        "mirror_x": bool(data.get("mirror_x", False)),
        "mirror_y": bool(data.get("mirror_y", False)),
        "bed_x": float(data.get("page_width", config.PRINTER_BED_X)),
        "bed_y": float(data.get("page_height", config.PRINTER_BED_Y)),
        "page_offset_x": float(data.get("page_offset_x", 0)),
        "page_offset_y": float(data.get("page_offset_y", 0)),
    }


@app.route("/api/upload", methods=["POST"])
def upload_svg():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    f = request.files["file"]
    if not f.filename.endswith(".svg"):
        return jsonify({"error": "Only SVG files accepted"}), 400

    file_id = uuid.uuid4().hex[:8]
    out_path = config.OUTPUT_DIR / f"{file_id}.svg"
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    f.save(out_path)
    uploaded_svgs[file_id] = str(out_path)

    # Parse for preview
    try:
        polylines = gcode.parse_svg(str(out_path))
        preview = []
        for pl in polylines:
            preview.append([(round(p[0], 2), round(p[1], 2)) for p in pl.points])
        return jsonify({"id": file_id, "polylines": preview, "stroke_count": len(polylines)})
    except Exception as e:
        return jsonify({"id": file_id, "error": str(e)})


@app.route("/api/convert", methods=["POST"])
def convert_svg():
    data = request.json or {}
    file_id = data.get("id")
    tool_name = data.get("tool", "pencil")

    if file_id not in uploaded_svgs and file_id not in text_polylines:
        return jsonify({"error": "SVG not found — upload first"}), 404

    transform_kwargs = _get_transform_params(data)

    try:
        if file_id in uploaded_svgs:
            gc, polylines, toolpath, stats, meta = gcode.svg_to_gcode(
                uploaded_svgs[file_id], tool_name, **transform_kwargs
            )
        else:
            # Direct polyline conversion (text patterns — no SVG round-trip)
            raw_polylines = text_polylines[file_id]
            profile = config.load_profile(tool_name)

            # Safety: refuse if tool not calibrated
            if profile.height.pen_down_z == 0.0:
                raise ValueError(
                    f"Tool '{tool_name}' is not calibrated (pen_down_z=0). "
                    "Run calibration first."
                )

            # Flip Y for correct text orientation on physical print.
            # Text is pre-positioned in page-space (Y-down, origin top-left).
            # Printer bed is Y-up, so we reflect: y_bed = bed_y - y_screen.
            bed_y = transform_kwargs.get("bed_y", config.PRINTER_BED_Y)
            flipped = [gcode.Polyline(points=[(x, bed_y - y) for x, y in pl.points],
                                       layer=pl.layer)
                       for pl in raw_polylines]

            # Text polylines: force simplification to reduce micro-moves.
            # Text is already in final page-space — disable auto-centering/scaling
            # so the layout we computed is preserved exactly.
            # Negate rotation to compensate for Y-flip (Y-flip reverses rotation direction).
            transform_kwargs = {
                **transform_kwargs,
                "simplify": True,
                "simplify_tolerance": 0.3,
                "user_rotate": -transform_kwargs.get("user_rotate", 0.0),
                "user_scale": transform_kwargs.get("user_scale", 1.0),
            }

            gc, toolpath, meta = gcode.polylines_to_gcode(
                flipped, profile, **transform_kwargs
            )
            # Extract stats from toolpath
            travel_segs = []
            for seg in toolpath:
                if seg["type"] == "travel" and len(seg["points"]) >= 2:
                    travel_segs.append((tuple(seg["points"][0]), tuple(seg["points"][-1])))
            preview_polylines = []
            for seg in toolpath:
                if seg["type"] == "draw" and len(seg["points"]) >= 2:
                    preview_polylines.append(gcode.Polyline(
                        points=[tuple(p) for p in seg["points"]],
                        layer=seg.get("layer", 0),
                    ))
            stats = gcode.compute_stats(
                preview_polylines, travel_segs,
                profile.movement.draw_speed, profile.movement.travel_speed,
            )
            polylines = preview_polylines

        generated_gcode[file_id] = gc

        # Save G-code file
        gcode_path = config.OUTPUT_DIR / f"{file_id}.gcode"
        with open(gcode_path, "w") as f:
            f.write(gc)

        profile = config.load_profile(tool_name)
        preview = []
        for pl in polylines:
            preview.append([(round(p[0], 2), round(p[1], 2)) for p in pl.points])

        line_count = len([l for l in gc.splitlines() if l.strip() and not l.strip().startswith(";")])
        return jsonify({
            "id": file_id,
            "gcode_preview": gc[:2000],
            "gcode_file": f"/api/download/{file_id}",
            "line_count": line_count,
            "polylines": preview,
            "toolpath": toolpath,
            "pen_offset_x": profile.height.offset_x,
            "pen_offset_y": profile.height.offset_y,
            "effective_area": meta.get("effective_area"),
            "stats": {
                "stroke_count": stats.stroke_count,
                "point_count": stats.point_count,
                "draw_distance_mm": stats.draw_distance_mm,
                "travel_distance_mm": stats.travel_distance_mm,
                "estimated_time_s": stats.estimated_time_s,
                "bounds": stats.bounds,
            },
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/test-pattern", methods=["POST"])
def test_pattern():
    """Generate a test SVG pattern."""
    data = request.json or {}
    pattern = data.get("pattern", "circle")
    size = float(data.get("size", 80))

    svg_parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">']

    if pattern in ("circle", "circles"):
        r = size / 2
        cx, cy = 50, 50
        # Concentric circles
        for ring in range(1, 4):
            ri = r * ring / 3
            pts = " ".join(f"{cx + ri * math.cos(2*math.pi*i/64)},{cy + ri * math.sin(2*math.pi*i/64)}" for i in range(65))
            svg_parts.append(f'<polyline points="{pts}" fill="none" stroke="black"/>')

    elif pattern == "square":
        x, y = 50 - size/2, 50 - size/2
        svg_parts.append(f'<rect x="{x}" y="{y}" width="{size}" height="{size}" fill="none" stroke="black"/>')

    elif pattern == "grid":
        step = size / 5
        start = 50 - size/2
        end = 50 + size/2
        for i in range(6):
            p = start + i * step
            svg_parts.append(f'<line x1="{p}" y1="{start}" x2="{p}" y2="{end}" stroke="black"/>')
            svg_parts.append(f'<line x1="{start}" y1="{p}" x2="{end}" y2="{p}" stroke="black"/>')

    elif pattern == "star":
        cx, cy = 50, 50
        r_outer = size / 2
        r_inner = size / 5
        pts = []
        for i in range(10):
            angle = math.pi / 2 + 2 * math.pi * i / 10
            r = r_outer if i % 2 == 0 else r_inner
            pts.append(f"{cx + r * math.cos(angle)},{cy - r * math.sin(angle)}")
        pts.append(pts[0])
        svg_parts.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="black"/>')

    elif pattern == "spiral":
        cx, cy = 50, 50
        pts = []
        turns = 3
        n_pts = 128
        for i in range(n_pts + 1):
            t = i / n_pts
            angle = turns * 2 * math.pi * t
            r = (size / 2) * t
            pts.append(f"{cx + r * math.cos(angle)},{cy + r * math.sin(angle)}")
        svg_parts.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="black"/>')

    elif pattern == "crosshatch":
        step = size / 8
        start = 50 - size / 2
        end = 50 + size / 2
        # Horizontal and vertical lines (grid)
        for i in range(9):
            p = start + i * step
            svg_parts.append(f'<line x1="{start}" y1="{p}" x2="{end}" y2="{p}" stroke="black"/>')
            svg_parts.append(f'<line x1="{p}" y1="{start}" x2="{p}" y2="{end}" stroke="black"/>')
        # Diagonal lines
        for i in range(-8, 9):
            offset = i * step
            x1 = max(start, start + offset)
            y1 = max(start, start - offset)
            x2 = min(end, end + offset)
            y2 = min(end, end - offset)
            if x1 < end and y1 < end:
                svg_parts.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="black"/>')

    elif pattern == "wave":
        pts = " ".join(f"{50 - size/2 + size*i/100},{50 + size/4 * math.sin(4 * 2*math.pi*i/100)}" for i in range(101))
        svg_parts.append(f'<polyline points="{pts}" fill="none" stroke="black"/>')
        pts2 = " ".join(f"{50 - size/2 + size*i/100},{50 + size/4 * math.cos(4 * 2*math.pi*i/100)}" for i in range(101))
        svg_parts.append(f'<polyline points="{pts2}" fill="none" stroke="black"/>')

    elif pattern == "crosshair":
        cx, cy = 50, 50
        r = size / 2
        # Cross lines
        svg_parts.append(f'<line x1="{cx - r}" y1="{cy}" x2="{cx + r}" y2="{cy}" stroke="black"/>')
        svg_parts.append(f'<line x1="{cx}" y1="{cy - r}" x2="{cx}" y2="{cy + r}" stroke="black"/>')
        # Small circle at center
        cr = size / 10
        cpts = " ".join(f"{cx + cr * math.cos(2*math.pi*i/32)},{cy + cr * math.sin(2*math.pi*i/32)}" for i in range(33))
        svg_parts.append(f'<polyline points="{cpts}" fill="none" stroke="black"/>')

    elif pattern == "text":
        text = data.get("text", "HELLO")
        if len(text) > 5000:
            return jsonify({"error": "Text too long (max 5000 characters)"}), 400
        font_style = data.get("font", "hershey")
        font_size = float(data.get("font_size", 25))
        import font as _font

        # Page/bed dimensions for layout
        bed_x = float(data.get("page_width", config.PRINTER_BED_X))
        bed_y = float(data.get("page_height", config.PRINTER_BED_Y))
        margin = float(data.get("text_margin", 10.0))   # mm margin around page
        line_spacing_factor = float(data.get("line_spacing", 1.4))  # multiplier on char height

        scale = font_size / _font.CHAR_HEIGHT  # char height in mm
        char_h_mm = _font.CHAR_HEIGHT * scale

        # Cursive needs wider inter-character spacing than hershey
        if font_style == "cursive":
            char_spacing = scale * 1.3
        else:
            char_spacing = scale * 1.0

        line_height = char_h_mm * line_spacing_factor
        usable_w = bed_x - 2 * margin
        usable_h = bed_y - 2 * margin

        # ── Word-wrap: split text into lines that fit in usable_w ──
        def _measure_word(word, _scale, _spacing):
            """Measure rendered width of a single word in mm."""
            strokes = _font.text_to_strokes(word, x=0, y=0, scale=_scale, spacing=_spacing)
            if not strokes:
                return 0.0
            xs = [p[0] for s in strokes for p in s]
            return (max(xs) - min(xs)) if xs else 0.0

        def _word_width(word):
            return _measure_word(word, scale, char_spacing)

        space_w = _word_width("i") * 0.8   # approximate space width

        raw_paragraphs = text.split("\n")
        lines = []  # list of line strings
        for para in raw_paragraphs:
            if para.strip() == "":
                lines.append("")   # blank line → paragraph break
                continue
            words = para.split()
            if not words:
                lines.append("")
                continue
            current = words[0]
            current_w = _word_width(words[0])
            for word in words[1:]:
                ww = _word_width(word)
                if current_w + space_w + ww <= usable_w:
                    current += " " + word
                    current_w += space_w + ww
                else:
                    lines.append(current)
                    current = word
                    current_w = ww
            lines.append(current)

        # ── Render each line at its correct Y offset ──
        all_strokes = []
        y_cursor = margin   # start at top margin (Y-down screen coords; flipped later)

        for line_text in lines:
            if y_cursor + char_h_mm > bed_y - margin:
                break   # out of vertical space — stop rather than overflow
            if line_text.strip() == "":
                y_cursor += line_height * 0.6   # smaller gap for blank paragraph break
                continue

            if font_style == "cursive":
                line_strokes = _font.text_to_cursive(
                    line_text, x=margin, y=y_cursor, scale=scale, spacing=char_spacing
                )
            else:
                line_strokes = _font.text_to_strokes(
                    line_text, x=margin, y=y_cursor, scale=scale, spacing=char_spacing
                )
            all_strokes.extend(line_strokes)
            y_cursor += line_height

        if not all_strokes:
            return jsonify({"error": "No strokes generated"}), 400

        polylines = [gcode.Polyline(points=s) for s in all_strokes if len(s) >= 2]

        if not polylines:
            return jsonify({"error": "No strokes generated"}), 400

        file_id = uuid.uuid4().hex[:8]
        text_polylines[file_id] = polylines

        preview = [[(round(p[0], 2), round(p[1], 2)) for p in pl.points] for pl in polylines]
        return jsonify({
            "id": file_id,
            "polylines": preview,
            "stroke_count": len(polylines),
            "line_count_rendered": len([l for l in lines if l.strip()]),
        })

    svg_parts.append('</svg>')
    svg_content = "\n".join(svg_parts)

    # Save and process like a normal upload
    file_id = uuid.uuid4().hex[:8]
    out_path = config.OUTPUT_DIR / f"{file_id}.svg"
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        f.write(svg_content)
    uploaded_svgs[file_id] = str(out_path)

    # For geometric patterns, also generate G-code with transforms
    tool = data.get("tool", "pencil")
    transform_kwargs = _get_transform_params(data)

    try:
        gc, polylines, toolpath_data, stats, meta = gcode.svg_to_gcode(
            str(out_path), tool, **transform_kwargs
        )
        generated_gcode[file_id] = gc
        gcode_path = config.OUTPUT_DIR / f"{file_id}.gcode"
        with open(gcode_path, "w") as f:
            f.write(gc)

        preview = [[(round(p[0], 2), round(p[1], 2)) for p in pl.points] for pl in polylines]
        line_count = len([l for l in gc.splitlines() if l.strip() and not l.strip().startswith(";")])

        profile = config.load_profile(tool)
        return jsonify({
            "id": file_id,
            "polylines": preview,
            "stroke_count": len(polylines),
            "has_gcode": True,
            "gcode_preview": gc[:2000],
            "gcode_file": f"/api/download/{file_id}",
            "line_count": line_count,
            "toolpath": toolpath_data,
            "pen_offset_x": profile.height.offset_x,
            "pen_offset_y": profile.height.offset_y,
            "effective_area": meta.get("effective_area"),
            "stats": {
                "stroke_count": stats.stroke_count,
                "point_count": stats.point_count,
                "draw_distance_mm": stats.draw_distance_mm,
                "travel_distance_mm": stats.travel_distance_mm,
                "estimated_time_s": stats.estimated_time_s,
                "bounds": stats.bounds,
            },
        })
    except Exception as e:
        # Fallback: just return preview without gcode
        polylines = gcode.parse_svg(str(out_path))
        preview = [[(round(p[0], 2), round(p[1], 2)) for p in pl.points] for pl in polylines]
        return jsonify({"id": file_id, "polylines": preview, "stroke_count": len(polylines), "error": str(e)})


@app.route("/api/download/<file_id>")
def download_gcode(file_id):
    gcode_path = config.OUTPUT_DIR / f"{file_id}.gcode"
    if not gcode_path.exists():
        return jsonify({"error": "G-code not found"}), 404
    return send_file(gcode_path, as_attachment=True, download_name=f"plot_{file_id}.gcode")


# ── Toon Tracer ──────────────────────────────────────────────────────

@app.route("/api/trace", methods=["POST"])
def trace_image():
    """Image-to-SVG edge tracing via OpenCV pipeline."""
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Empty filename"}), 400

    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"):
        return jsonify({"error": "Unsupported image format"}), 400

    # Parameters with defaults
    canny_low = int(request.form.get("canny_low", 50))
    canny_high = int(request.form.get("canny_high", 150))
    blur = int(request.form.get("blur", 9))
    posterize_levels = int(request.form.get("posterize", 8))
    epsilon = float(request.form.get("epsilon", 1.5))
    min_contour_length = int(request.form.get("min_contour_length", 10))
    invert = request.form.get("invert", "false") == "true"
    page_width = float(request.form.get("page_width", 220))
    page_height = float(request.form.get("page_height", 220))

    # Read image from bytes
    img_bytes = np.frombuffer(f.read(), dtype=np.uint8)
    img = cv2.imdecode(img_bytes, cv2.IMREAD_COLOR)
    if img is None:
        return jsonify({"error": "Could not decode image"}), 400

    img_h, img_w = img.shape[:2]

    # Resize if longest side > 1500px
    max_dim = 1500
    if max(img_w, img_h) > max_dim:
        scale = max_dim / max(img_w, img_h)
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        img_h, img_w = img.shape[:2]

    # Grayscale
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Invert if needed (light-on-dark images)
    if invert:
        gray = cv2.bitwise_not(gray)

    # Bilateral filter — smooth while preserving edges
    d = blur if blur % 2 == 1 else blur + 1
    gray = cv2.bilateralFilter(gray, d, 75, 75)

    # Posterize — reduce to N gray levels for cleaner edges
    if posterize_levels > 1:
        step = 256 / posterize_levels
        gray = np.floor(gray / step) * step
        gray = gray.astype(np.uint8)

    # Canny edge detection
    edges = cv2.Canny(gray, canny_low, canny_high)

    # Find contours
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    # Filter and simplify contours
    polylines = []
    for contour in contours:
        if len(contour) < min_contour_length:
            continue
        approx = cv2.approxPolyDP(contour, epsilon, closed=False)
        if len(approx) < 2:
            continue
        polylines.append(approx.reshape(-1, 2))

    if not polylines:
        return jsonify({"error": "No edges detected — try adjusting parameters"}), 400

    # Scale pixel coords → mm (fit into page with 10mm margin)
    margin = 10
    fit_w = page_width - 2 * margin
    fit_h = page_height - 2 * margin
    scale_x = fit_w / img_w
    scale_y = fit_h / img_h
    scale_f = min(scale_x, scale_y)

    offset_x = margin + (fit_w - img_w * scale_f) / 2
    offset_y = margin + (fit_h - img_h * scale_f) / 2

    # Build SVG via svgwrite
    file_id = uuid.uuid4().hex[:8]
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    svg_path = config.OUTPUT_DIR / f"trace_{file_id}.svg"

    dwg = svgwrite.Drawing(
        str(svg_path),
        size=(f"{page_width}mm", f"{page_height}mm"),
        viewBox=f"0 0 {page_width} {page_height}",
    )

    for pts in polylines:
        if len(pts) < 2:
            continue
        # Flip Y axis (image origin top-left → plotter origin bottom-left)
        mm_pts = [
            (round(offset_x + px * scale_f, 3), round(page_height - (offset_y + py * scale_f), 3))
            for px, py in pts
        ]
        dwg.add(dwg.polyline(mm_pts, stroke="black", fill="none", stroke_width=0.3))

    dwg.save()

    # Register in uploaded_svgs so existing convert/plot pipeline works
    uploaded_svgs[file_id] = str(svg_path)

    # Build polyline data for canvas preview (pixel coords)
    preview_polylines = []
    for pts in polylines:
        preview_polylines.append([(int(px), int(py)) for px, py in pts])

    return jsonify({
        "id": file_id,
        "polylines": preview_polylines,
        "stroke_count": len(polylines),
        "image_size": [img_w, img_h],
        "svg_file": f"/api/trace-svg/{file_id}",
    })


@app.route("/api/trace-svg/<file_id>")
def download_trace_svg(file_id):
    svg_path = config.OUTPUT_DIR / f"trace_{file_id}.svg"
    if not svg_path.exists():
        return jsonify({"error": "SVG not found"}), 404
    return send_file(svg_path, as_attachment=True, download_name=f"trace_{file_id}.svg")


# ── Ink / Wacom Slate ────────────────────────────────────────────────

@app.route("/stream/stroke", methods=["POST"])
def ink_stream():
    """Receive stroke batches from capture.py (Wacom BLE capture subprocess)."""
    global _ink_strokes, _live_stroke_points
    data = request.json or {}
    points = data.get("points", [])
    stroke_end = data.get("stroke_end", False)
    if not points and not stroke_end:
        return jsonify({"status": "ok"})
    if points:
        _ink_strokes.append(points)
        print(f"  INK: {len(points)} pts, ws_clients={len(ws_clients)}", flush=True)
        _broadcast_ink(points)

        # Live plot: map points to bed mm and accumulate
        if _live_plot_active and _live_plot_profile and serial.is_connected:
            page = config.load_page_size()
            pw, ph = page["width"], page["height"]
            for pt in points:
                if len(pt) >= 2:
                    mx, my = _wacom_to_bed(pt[0], pt[1], pw, ph)
                    _live_stroke_points.append((mx, my))

    # Stroke complete: generate G-code and queue for plotter
    if stroke_end and _live_plot_active and _live_stroke_points and serial.is_connected:
        gcode_lines = _stroke_to_gcode(_live_stroke_points, _live_plot_profile)
        if gcode_lines:
            serial.queue_stroke(gcode_lines)
            print(f"  LIVE PLOT: queued stroke ({len(gcode_lines)} lines)", flush=True)
        _live_stroke_points = []

    return jsonify({"status": "ok"})


@app.route("/api/ink/live-start", methods=["POST"])
def ink_live_start():
    """Start live plot mode: plotter homes and waits for strokes."""
    global _live_plot_active, _live_plot_profile
    if not serial.is_connected:
        return jsonify({"error": "Plotter not connected"}), 400
    if _live_plot_active:
        return jsonify({"error": "Live plot already active"}), 400
    data = request.json or {}
    tool = data.get("tool", "pencil")
    try:
        _live_plot_profile = config.load_profile(tool)
    except FileNotFoundError:
        return jsonify({"error": f"Profile '{tool}' not found"}), 404
    if _live_plot_profile.height.pen_down_z == 0.0:
        return jsonify({"error": f"Tool '{tool}' not calibrated (pen_down_z=0). Run calibration first."}), 400
    try:
        serial.start_live_mode(config.SAFE_Z)
        _live_plot_active = True
        return jsonify({"ok": True, "message": "Live plot started"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ink/live-stop", methods=["POST"])
def ink_live_stop():
    """Stop live plot mode: flush pending stroke and park plotter."""
    global _live_plot_active, _live_plot_profile, _live_stroke_points
    if not _live_plot_active:
        return jsonify({"ok": True, "message": "Live plot not active"})
    # Flush any in-progress stroke
    if _live_stroke_points and serial.is_connected:
        gcode_lines = _stroke_to_gcode(_live_stroke_points, _live_plot_profile)
        if gcode_lines:
            serial.queue_stroke(gcode_lines)
    _live_stroke_points = []
    _live_plot_active = False
    _live_plot_profile = None
    try:
        serial.stop_live_mode(config.SAFE_Z)
    except Exception:
        pass
    return jsonify({"ok": True, "message": "Live plot stopped"})


@app.route("/api/ink/capture", methods=["POST"])
def ink_capture():
    """Spawn capture.py as subprocess to stream live BLE pen data."""
    global _slate_process, _ink_strokes
    if _slate_process and _slate_process.poll() is None:
        return jsonify({"error": "Capture already running"}), 400
    _ink_strokes.clear()
    _slate_process = subprocess.Popen(
        ["python", "capture.py", "--api", "http://localhost:5000"],
        cwd=PENZ_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return jsonify({"ok": True, "pid": _slate_process.pid})


@app.route("/api/ink/stop", methods=["POST"])
def ink_stop():
    """Stop capture subprocess and generate SVG from accumulated strokes."""
    global _slate_process, _ink_strokes
    if _slate_process and _slate_process.poll() is None:
        _slate_process.terminate()
        try:
            _slate_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _slate_process.kill()

    stroke_count = sum(len(s) for s in _ink_strokes)
    if stroke_count == 0:
        _slate_process = None
        return jsonify({"error": "No strokes captured"}), 400

    # Generate SVG from accumulated strokes
    page = config.load_page_size()
    pw, ph = page["width"], page["height"]

    file_id = uuid.uuid4().hex[:8]
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    svg_path = config.OUTPUT_DIR / f"ink_{file_id}.svg"

    dwg = svgwrite.Drawing(
        str(svg_path),
        size=(f"{pw}mm", f"{ph}mm"),
        viewBox=f"0 0 {pw} {ph}",
    )

    for batch in _ink_strokes:
        if len(batch) < 2:
            continue
        # Wacom Slate A5 portrait: swap X/Y + rotate 180 (same as _wacom_to_bed)
        pts = []
        for pt in batch:
            if len(pt) >= 2:
                x = round((1 - pt[1] / 14700) * pw, 3)
                y = round(pt[0] / 21600 * ph, 3)
                pts.append((x, y))
        if len(pts) >= 2:
            dwg.add(dwg.polyline(pts, stroke="black", fill="none", stroke_width=0.5))

    dwg.save()
    uploaded_svgs[file_id] = str(svg_path)
    _ink_strokes.clear()
    _slate_process = None

    return jsonify({"id": file_id, "stroke_count": stroke_count})


@app.route("/api/ink/status", methods=["GET"])
def ink_status():
    """Return current capture status."""
    capturing = _slate_process is not None and _slate_process.poll() is None
    pid = _slate_process.pid if _slate_process else None
    return jsonify({"capturing": capturing, "pid": pid})


@app.route("/api/ink/sync", methods=["POST"])
def ink_sync():
    """Spawn sync.py to download stored pages from Wacom device."""
    proc = subprocess.Popen(
        ["python", "sync.py"],
        cwd=PENZ_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return jsonify({"ok": True, "pid": proc.pid})


@app.route("/api/ink/pages", methods=["GET"])
def ink_pages():
    """List synced SVG pages from penz/data/pages/."""
    pages_dir = os.path.join(PENZ_DIR, "data", "pages")
    if not os.path.isdir(pages_dir):
        return jsonify({"pages": []})
    files = sorted(
        [f for f in os.listdir(pages_dir) if f.endswith(".svg")],
        reverse=True,
    )
    return jsonify({"pages": files})


@app.route("/api/ink/load-page", methods=["POST"])
def ink_load_page():
    """Copy a synced SVG page to the plotter output dir and register it."""
    data = request.json or {}
    filename = data.get("filename", "")
    if not filename:
        return jsonify({"error": "filename required"}), 400

    src = os.path.join(PENZ_DIR, "data", "pages", filename)
    if not os.path.isfile(src):
        return jsonify({"error": "Page not found"}), 404

    file_id = uuid.uuid4().hex[:8]
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    dst = config.OUTPUT_DIR / f"ink_{file_id}.svg"
    shutil.copy2(src, str(dst))

    uploaded_svgs[file_id] = str(dst)

    # Count strokes
    try:
        polylines = gcode.parse_svg(str(dst))
        if not polylines:
            return jsonify({"id": file_id, "stroke_count": 0, "warning": "Page has no strokes"})
        return jsonify({"id": file_id, "stroke_count": len(polylines)})
    except Exception:
        return jsonify({"id": file_id, "stroke_count": 0, "warning": "Could not parse page"})


# ── Ink OCR ──────────────────────────────────────────────────────────

NIM_API_KEY = os.environ.get("NIM_API_KEY", "")
NIM_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NIM_MODEL = "microsoft/phi-4-multimodal-instruct"
ZHIPU_API_KEY = os.environ.get("RABBIT_LLM_KEY", "")
ZHIPU_VISION_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
ZHIPU_VISION_MODEL = "glm-4.5v"
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://192.168.0.33:11434/v1/chat/completions")
OLLAMA_VISION_MODEL = os.environ.get("OLLAMA_VISION_MODEL", "minicpm-v")


def _svg_to_png(svg_path: str) -> bytes:
    """Render SVG polylines to a white PNG image."""
    tree = ET.parse(svg_path)
    root = tree.getroot()
    ns = {"svg": "http://www.w3.org/2000/svg"}

    # Determine viewBox scale
    vb = root.get("viewBox", "")
    if vb:
        parts = vb.replace(",", " ").split()
        vb_w, vb_h = float(parts[2]), float(parts[3])
    else:
        vb_w, vb_h = 148.0, 210.0

    # Raw Wacom coords (> 1000) → scale to 2048px; mm-space → 150 dpi
    if vb_w > 1000:
        scale = 2048 / max(vb_w, vb_h)
        target_w, target_h = int(vb_w * scale), int(vb_h * scale)
    else:
        dpi = 150
        target_w, target_h = int(vb_w * dpi / 25.4), int(vb_h * dpi / 25.4)

    img = np.ones((target_h, target_w, 3), dtype=np.uint8) * 255

    for pl in root.iter("{http://www.w3.org/2000/svg}polyline"):
        pts_str = pl.get("points", "")
        if not pts_str.strip():
            continue
        pts = []
        for pair in pts_str.strip().split():
            x, y = pair.split(",")
            px = int(float(x) / vb_w * target_w)
            py = int(float(y) / vb_h * target_h)
            pts.append([px, py])
        if len(pts) >= 2:
            cv2.polylines(img, [np.array(pts)], False, (0, 0, 0), 2, cv2.LINE_AA)

    # Auto-crop to ink content with padding
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, 250, 255, cv2.THRESH_BINARY_INV)
    coords = cv2.findNonZero(mask)
    if coords is not None:
        x, y, w, h = cv2.boundingRect(coords)
        pad = max(20, int(min(w, h) * 0.05))
        x0, y0 = max(0, x - pad), max(0, y - pad)
        x1, y1 = min(img.shape[1], x + w + pad), min(img.shape[0], y + h + pad)
        img = img[y0:y1, x0:x1]

    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise RuntimeError("PNG encode failed")
    return buf.tobytes()


def _detect_regions(svg_path: str, img_w: int, img_h: int) -> list[dict]:
    """Detect text vs drawing regions by analyzing connected components in the rendered ink.

    Returns list of {type: "text"|"drawing", bbox: [x,y,w,h], hint: str}.
    """
    # Render a quick grayscale mask of just the ink
    tree = ET.parse(svg_path)
    root = tree.getroot()
    vb = root.get("viewBox", "")
    if vb:
        parts = vb.replace(",", " ").split()
        vb_w, vb_h = float(parts[2]), float(parts[3])
    else:
        vb_w, vb_h = 148.0, 210.0

    mask = np.zeros((img_h, img_w), dtype=np.uint8)
    for pl in root.iter("{http://www.w3.org/2000/svg}polyline"):
        pts_str = pl.get("points", "")
        if not pts_str.strip():
            continue
        pts = []
        for pair in pts_str.strip().split():
            x, y = pair.split(",")
            px = int(float(x) / vb_w * img_w)
            py = int(float(y) / vb_h * img_h)
            pts.append([px, py])
        if len(pts) >= 2:
            cv2.polylines(mask, [np.array(pts)], False, 255, 2, cv2.LINE_AA)

    # Dilate to connect nearby ink into blobs
    kern = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
    dilated = cv2.dilate(mask, kern, iterations=2)

    # Find connected components
    n_labels, labels, stats_arr, centroids = cv2.connectedComponentsWithStats(dilated)
    regions = []
    for i in range(1, n_labels):  # skip background (0)
        rx, ry, rw, rh = stats_arr[i, :4]
        area = stats_arr[i, cv2.CC_STAT_AREA]
        if area < 100:  # skip tiny specks
            continue

        # Classify: dense small components with low aspect = text, large spread = drawing
        aspect = rw / max(rh, 1)
        density = area / max(rw * rh, 1)

        # Count original ink pixels in this region
        component_mask = (labels == i).astype(np.uint8) * 255
        ink_pixels = cv2.countNonZero(cv2.bitwise_and(mask, component_mask))
        stroke_density = ink_pixels / max(area, 1)

        # Heuristic:
        #   Text: many thin strokes, high stroke density, compact
        #   Drawing: larger area, lower density, often more spread
        if rw > img_w * 0.4 and rh > img_h * 0.4:
            # Huge region spanning most of page — mixed or full-page content
            rtype = "mixed"
        elif stroke_density > 0.15 and max(rw, rh) < max(img_w, img_h) * 0.35:
            rtype = "text"
        else:
            rtype = "drawing"

        regions.append({
            "type": rtype,
            "bbox": [int(rx), int(ry), int(rw), int(rh)],
            "area": int(area),
            "stroke_density": round(stroke_density, 3),
        })

    # Sort top-to-bottom, left-to-right (reading order)
    regions.sort(key=lambda r: (r["bbox"][1], r["bbox"][0]))
    return regions


def _call_vision_llm(image_b64: str, prompt: str) -> str:
    """Call vision LLM with base64 PNG, return transcribed text.

    Provider chain: NVIDIA NIM → ZhipuAI glm-4.5v → Ollama.
    """
    content = [
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
        {"type": "text", "text": prompt},
    ]
    base_payload = {
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 8192,
        "temperature": 0.2,
    }

    providers = [
        {
            "name": "NVIDIA NIM",
            "url": NIM_URL,
            "model": NIM_MODEL,
            "headers": {
                "Authorization": f"Bearer {NIM_API_KEY}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            "timeout": 90,
        },
        {
            "name": "ZhipuAI vision",
            "url": ZHIPU_VISION_URL,
            "model": ZHIPU_VISION_MODEL,
            "headers": {
                "Authorization": f"Bearer {ZHIPU_API_KEY}",
                "Content-Type": "application/json",
            },
            "timeout": 120,
        },
        {
            "name": "Ollama",
            "url": OLLAMA_URL,
            "model": OLLAMA_VISION_MODEL,
            "headers": {"Content-Type": "application/json"},
            "timeout": 600,
        },
    ]

    last_err = None
    for prov in providers:
        # Skip providers with no API key configured
        auth = prov["headers"].get("Authorization", "")
        if auth.endswith("Bearer ") or auth == "":
            continue
        payload = {**base_payload, "model": prov["model"]}
        for attempt in range(2):
            try:
                resp = requests.post(
                    prov["url"], json=payload, headers=prov["headers"], timeout=prov["timeout"],
                )
                if resp.status_code == 429:
                    time.sleep(10 * (attempt + 1))
                    continue
                resp.raise_for_status()
                data = resp.json()
                msg = data["choices"][0]["message"]
                text = msg.get("content", "") or msg.get("reasoning", "") or ""
                if text and "forgot to attach" not in text.lower() and "provide the image" not in text.lower():
                    return text
                # Model didn't see the image — skip to next provider
                break
            except Exception as e:
                last_err = e
                if attempt == 0:
                    time.sleep(3)
                else:
                    break

    raise RuntimeError(f"All vision providers failed. Last error: {last_err}")


@app.route("/api/ink/ocr", methods=["POST"])
def ink_ocr():
    """OCR a captured ink SVG using a vision LLM with region-aware prompting."""
    data = request.json or {}
    file_id = data.get("id")
    if not file_id:
        return jsonify({"error": "id required"}), 400

    svg_path = uploaded_svgs.get(file_id) or str(config.OUTPUT_DIR / f"ink_{file_id}.svg")
    if not os.path.isfile(svg_path):
        return jsonify({"error": "SVG not found"}), 404

    try:
        png_bytes = _svg_to_png(svg_path)
        img_b64 = base64.b64encode(png_bytes).decode()
    except Exception as e:
        return jsonify({"error": f"SVG render failed: {e}"}), 500

    # Detect regions to build a context hint
    try:
        img_arr = cv2.imdecode(np.frombuffer(png_bytes, np.uint8), cv2.IMREAD_COLOR)
        ih, iw = img_arr.shape[:2]
        regions = _detect_regions(svg_path, iw, ih)
    except Exception:
        regions = []

    # Build region hint for the LLM
    hint_parts = []
    n_text = sum(1 for r in regions if r["type"] == "text")
    n_draw = sum(1 for r in regions if r["type"] == "drawing")
    n_mixed = sum(1 for r in regions if r["type"] == "mixed")

    if regions:
        if n_text:
            hint_parts.append(f"{n_text} text region{'s' if n_text > 1 else ''}")
        if n_draw:
            hint_parts.append(f"{n_draw} drawing/figure region{'s' if n_draw > 1 else ''}")
        if n_mixed:
            hint_parts.append(f"{n_mixed} mixed region{'s' if n_mixed > 1 else ''}")

    region_hint = ""
    if hint_parts:
        region_hint = (
            f"\n\nAutomated region analysis detected: {', '.join(hint_parts)}. "
            "Use this as a guide — trust your own visual judgment over these hints."
        )

    prompt = (
        "Analyze this handwritten page. Do the following:\n"
        "1. Transcribe all handwritten text exactly as written, preserving line breaks and layout.\n"
        "2. If there are any drawings, diagrams, or sketches, describe them in [brackets] "
        "at the approximate position they appear relative to the text.\n"
        "3. If a region contains both text and drawings, transcribe the text and describe the drawing together.\n"
        "Output the full transcription. Use [drawing: description] for any non-text content."
        + region_hint
    )

    try:
        text = _call_vision_llm(img_b64, prompt)
    except Exception as e:
        return jsonify({"error": f"OCR failed: {e}"}), 500

    return jsonify({"ok": True, "text": text, "regions": regions})


# ── Print ────────────────────────────────────────────────────────────

@app.route("/api/print", methods=["POST"])
def start_print():
    data = request.json or {}
    file_id = data.get("id")
    if file_id not in generated_gcode:
        return jsonify({"error": "G-code not found — convert first"}), 404

    if not _ensure_connected():
        return jsonify({"error": "Printer not connected"}), 400

    try:
        serial.send_gcode_file(
            generated_gcode[file_id],
            filename=file_id,
            progress_callback=_broadcast_progress,
        )
        return jsonify({"ok": True, "message": "Printing started"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/stop", methods=["POST"])
def stop_print():
    serial.stop()
    return jsonify({"ok": True})


# ── Jog ──────────────────────────────────────────────────────────────

@app.route("/api/jog", methods=["POST"])
def jog():
    if not serial.is_connected:
        return jsonify({"error": "Printer not connected"}), 400
    data = request.json or {}
    axis = data.get("axis", "").upper()
    distance = float(data.get("distance", 1))
    speed = int(data.get("speed", 1500))

    if axis not in ("X", "Y", "Z"):
        return jsonify({"error": "Axis must be X, Y, or Z"}), 400

    try:
        if axis in ("X", "Y"):
            pos = serial.get_position()
            current_z = float(pos.get("Z", 0))
            if current_z < config.SAFE_Z:
                serial.send_command(f"G90 ; Absolute positioning")
                serial.send_command(f"G1 Z{config.SAFE_Z:.3f} F1500 ; Raise to safe travel height")
        serial.send_command(f"G91 ; Relative positioning")
        serial.send_command(f"G1 {axis}{distance:.3f} F{speed}")
        serial.send_command(f"G90 ; Absolute positioning")
        pos = serial.get_position()
        return jsonify({"ok": True, "position": pos})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/home", methods=["POST"])
def home():
    """Park at water cup position (origin) instead of full endstop home."""
    if not serial.is_connected:
        return jsonify({"error": "Printer not connected"}), 400
    try:
        serial.send_command(f"G1 Z{config.SAFE_Z:.3f} F3000")        # lift Z first
        serial.send_command("G28")                                    # home to establish position
        serial.send_command(f"G1 Z{config.SAFE_Z:.3f} F3000")        # raise to safe
        serial.send_command("G1 X0.000 Y0.000 F3000")                 # move to water cup
        serial.send_command("G1 Z0.000 F300")                         # lower to rest
        pos = serial.get_position()
        return jsonify({"ok": True, "position": pos})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Calibration ──────────────────────────────────────────────────────

@app.route("/api/calibration/start", methods=["POST"])
def calibration_start():
    """Move to calibration position: home, then Z20 X110 Y110 for pen loading."""
    if not serial.is_connected:
        return jsonify({"error": "Printer not connected"}), 400
    try:
        serial.send_command("G28 ; Home all axes")
        serial.send_command("G90 ; Absolute positioning")
        serial.send_command(f"G1 Z{config.SAFE_Z:.3f} F3000 ; Raise Z first")
        serial.send_command("G1 X110.000 Y110.000 F3000 ; Move to center")
        serial.send_command("G1 Z20.000 F500 ; Lower to pen-load height")
        pos = serial.get_position()
        return jsonify({"ok": True, "position": pos})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/calibration/pen-loaded", methods=["POST"])
def calibration_pen_loaded():
    """Raise Z by 5mm after pen is loaded."""
    if not serial.is_connected:
        return jsonify({"error": "Printer not connected"}), 400
    try:
        serial.send_command("G91 ; Relative positioning")
        serial.send_command("G1 Z5.000 F500 ; Raise 5mm for clearance")
        serial.send_command("G90 ; Absolute positioning")
        pos = serial.get_position()
        return jsonify({"ok": True, "position": pos})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/calibration", methods=["GET"])
def get_calibration():
    return jsonify(config.load_calibration())


@app.route("/api/calibration/save", methods=["POST"])
def save_calibration_endpoint():
    data = request.json or {}
    tool = data.get("tool")
    pen_down_z = float(data.get("pen_down_z", 0))
    pen_up_z = float(data.get("pen_up_z", pen_down_z + 5))

    if not tool:
        return jsonify({"error": "tool required"}), 400

    # Preserve existing offsets when saving Z calibration
    cal = config.load_calibration()
    existing = cal.get(tool, {})
    config.save_calibration(tool, pen_down_z, pen_up_z,
                            existing.get("offset_x", 0.0),
                            existing.get("offset_y", 0.0))
    return jsonify({"ok": True, "tool": tool, "pen_down_z": pen_down_z, "pen_up_z": pen_up_z})


@app.route("/api/calibration/offset", methods=["POST"])
def save_calibration_offset():
    data = request.json or {}
    tool = data.get("tool")
    if not tool:
        return jsonify({"error": "tool required"}), 400
    cal = config.load_calibration()
    ox = float(data.get("offset_x", 0))
    oy = float(data.get("offset_y", 0))
    if tool in cal:
        config.save_calibration(tool, cal[tool]["pen_down_z"], cal[tool]["pen_up_z"], ox, oy)
    else:
        config.save_calibration(tool, 0.0, 5.0, ox, oy)
    return jsonify({"ok": True, "tool": tool, "offset_x": ox, "offset_y": oy})


@app.route("/api/calibration/step", methods=["POST"])
def calibration_step():
    """Jog Z for calibration. Returns current Z position."""
    if not serial.is_connected:
        return jsonify({"error": "Printer not connected"}), 400
    data = request.json or {}
    distance = float(data.get("distance", -0.1))

    try:
        serial.send_command("G91")
        serial.send_command(f"G1 Z{distance:.3f} F300")
        serial.send_command("G90")
        pos = serial.get_position()
        return jsonify({"ok": True, "position": pos})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/calibration/test-dot", methods=["POST"])
def test_dot():
    """Lower pen, pause, raise — to test contact."""
    if not serial.is_connected:
        return jsonify({"error": "Printer not connected"}), 400
    data = request.json or {}
    tool = data.get("tool", "pencil")
    try:
        profile = config.load_profile(tool)
        ht = profile.height
        serial.send_command(f"G1 Z{ht.pen_up_z:.3f} F500")
        serial.send_command(f"G1 Z{ht.pen_down_z:.3f} F300")
        serial.send_command("G4 P1000")  # Dwell 1 second
        serial.send_command(f"G1 Z{ht.pen_up_z:.3f} F500")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Mark Page Outline ─────────────────────────────────────────────────

@app.route("/api/mark-page", methods=["POST"])
def mark_page():
    """Draw L-marks at the 4 corners of the page so user can verify alignment."""
    if not serial.is_connected:
        return jsonify({"error": "Printer not connected"}), 400

    data = request.json or {}
    tool = data.get("tool", "pencil")

    try:
        profile = config.load_profile(tool)
    except FileNotFoundError:
        return jsonify({"error": f"Profile '{tool}' not found"}), 404
    if profile.height.pen_down_z == 0.0:
        return jsonify({"error": f"Tool '{tool}' not calibrated. Run calibration first."}), 400

    page = config.load_page_size()
    pw = page["width"]
    ph = page["height"]
    pox = page.get("offset_x", 0)
    poy = page.get("offset_y", 0)

    ht = profile.height
    mv = profile.movement

    # Compute pen offset (same math as gcode.py)
    pen_ox = ht.offset_x
    pen_oy = ht.offset_y
    if pen_ox != 0 or pen_oy != 0:
        pen_phys_x = -(pen_ox - config.PRINTER_BED_X / 2)
        pen_phys_y = -(pen_oy - config.PRINTER_BED_Y / 2)
    else:
        pen_phys_x, pen_phys_y = 0, 0

    # Page corners in pen-space → convert to hotend-space
    corners_pen = [
        (pox, poy),                  # front-left
        (pox + pw, poy),             # front-right
        (pox + pw, poy + ph),        # back-right
        (pox, poy + ph),             # back-left
    ]

    safe_z = config.SAFE_Z
    pen_down_z = ht.pen_down_z
    arm = 10.0  # mm mark length

    try:
        serial.send_command(f"G90 ; Absolute positioning")
        serial.send_command(f"G1 Z{safe_z:.3f} F{mv.travel_speed:.0f} ; Safe Z")

        for i, (cpx, cpy) in enumerate(corners_pen):
            # Convert to hotend coords
            hx = cpx - pen_phys_x
            hy = cpy - pen_phys_y

            # Draw L-shape at corner
            dx = arm if i in (0, 3) else -arm  # mark inward
            dy = arm if i in (0, 1) else -arm

            serial.send_command(f"G0 X{hx:.3f} Y{hy:.3f} F{mv.travel_speed:.0f} ; Corner {i+1}")
            serial.send_command(f"G1 Z{pen_down_z:.3f} F300 ; Pen down")
            serial.send_command(f"G1 X{hx + dx:.3f} Y{hy:.3f} F500 ; Mark X")
            serial.send_command(f"G1 X{hx + dx:.3f} Y{hy + dy:.3f} F500 ; Mark Y")
            serial.send_command(f"G1 Z{safe_z:.3f} F300 ; Pen up")

        serial.send_command("G0 X0 Y0 F3000 ; Return home")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Bed Leveling ─────────────────────────────────────────────────────

bed_level_state: dict | None = None

CORNER_LABELS = ["Front-Left", "Front-Right", "Back-Right", "Back-Left"]
ARM = 15.0  # mm length of each L-shape arm


@app.route("/api/bed-level", methods=["POST"])
def bed_level():
    global bed_level_state

    if not serial.is_connected:
        return jsonify({"error": "Printer not connected"}), 400

    data = request.json or {}
    action = data.get("action", "start")
    tool = data.get("tool", "pencil")

    try:
        profile = config.load_profile(tool)
    except FileNotFoundError:
        return jsonify({"error": f"Profile '{tool}' not found"}), 404
    if profile.height.pen_down_z == 0.0:
        return jsonify({"error": f"Tool '{tool}' not calibrated. Run calibration first."}), 400

    pen_down_z = profile.height.pen_down_z
    safe_z = config.SAFE_Z

    if action == "start":
        page = config.load_page_size()
        w = page["width"]
        h = page["height"]
        ox = page.get("offset_x", 0)
        oy = page.get("offset_y", 0)

        corners = [
            (ox, oy, ox + ARM, oy, ox, oy + ARM),           # FL: right → up
            (ox + w, oy, ox + w - ARM, oy, ox + w, oy + ARM),  # FR: left → up
            (ox + w, oy + h, ox + w - ARM, oy + h, ox + w, oy + h - ARM),  # BR: left → down
            (ox, oy + h, ox + ARM, oy + h, ox, oy + h - ARM),  # BL: right → down
        ]

        bed_level_state = {"corner": 0, "corners": corners}
        serial.send_command(f"G90")
        _draw_l_shape(corners[0], pen_down_z, safe_z)
        return jsonify({"corner": 0, "label": CORNER_LABELS[0]})

    elif action == "next":
        if bed_level_state is None:
            return jsonify({"error": "No bed level session — start first"}), 400
        bed_level_state["corner"] += 1
        idx = bed_level_state["corner"]
        if idx > 3:
            serial.send_command(f"G1 Z{safe_z:.3f} F300")
            bed_level_state = None
            return jsonify({"done": True})
        serial.send_command(f"G90")
        _draw_l_shape(bed_level_state["corners"][idx], pen_down_z, safe_z)
        return jsonify({"corner": idx, "label": CORNER_LABELS[idx]})

    elif action == "repeat":
        if bed_level_state is None:
            return jsonify({"error": "No bed level session — start first"}), 400
        idx = bed_level_state["corner"]
        serial.send_command(f"G90")
        _draw_l_shape(bed_level_state["corners"][idx], pen_down_z, safe_z)
        return jsonify({"corner": idx, "label": CORNER_LABELS[idx]})

    elif action == "stop":
        serial.send_command(f"G1 Z{safe_z:.3f} F300")
        serial.send_command("G28")
        bed_level_state = None
        return jsonify({"ok": True})

    return jsonify({"error": f"Unknown action: {action}"}), 400


def _draw_l_shape(corner, pen_down_z, safe_z):
    """Draw an L-shape at a corner. corner = (cx, cy, arm1_ex, arm1_ey, arm2_ex, arm2_ey)."""
    cx, cy, ax1, ay1, ax2, ay2 = corner
    serial.send_command(f"G1 Z{safe_z:.3f} F300")
    serial.send_command(f"G0 X{cx:.3f} Y{cy:.3f} F3000")
    serial.send_command(f"G1 Z{pen_down_z:.3f} F300")
    serial.send_command(f"G1 X{ax1:.3f} Y{ay1:.3f} F500")
    serial.send_command(f"G1 X{ax2:.3f} Y{ay2:.3f} F500")
    serial.send_command(f"G1 Z{safe_z:.3f} F300")


# ── Tool Change Park ─────────────────────────────────────────────────

@app.route("/api/tool-change-park", methods=["POST"])
def tool_change_park():
    """Move to the tool change park position, or save current position as park."""
    if not serial.is_connected:
        return jsonify({"error": "Printer not connected"}), 400

    data = request.json or {}
    action = data.get("action", "goto")
    tool = data.get("tool", "watercolor")

    try:
        profile = config.load_profile(tool)
    except FileNotFoundError:
        return jsonify({"error": f"Profile '{tool}' not found"}), 404

    p2 = profile.water.pass2

    if action == "goto":
        try:
            serial.send_command(f"G90 ; Absolute positioning")
            serial.send_command(f"G1 Z{p2.change_z:.3f} F3000 ; Raise to tool change height")
            serial.send_command(f"G1 X{p2.change_x:.3f} Y{p2.change_y:.3f} F3000 ; Move to park")
            pos = serial.get_position()
            return jsonify({"ok": True, "position": pos})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    elif action == "save":
        try:
            pos = serial.get_position()
            x = round(float(pos.get("X", 0)), 1)
            y = round(float(pos.get("Y", 0)), 1)
            z = round(float(pos.get("Z", 0)), 1)
            profile.water.pass2.change_x = x
            profile.water.pass2.change_y = y
            profile.water.pass2.change_z = z
            config.save_profile(tool, profile)
            return jsonify({"ok": True, "change_x": x, "change_y": y, "change_z": z})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    return jsonify({"error": f"Unknown action: {action}"}), 400


# ── Settings ─────────────────────────────────────────────────────────

@app.route("/api/settings/<tool_name>", methods=["GET"])
def get_settings(tool_name):
    try:
        profile = config.load_profile(tool_name)
        return jsonify({
            "tool": tool_name,
            "name": profile.name,
            "movement": {
                "draw_speed": profile.movement.draw_speed,
                "travel_speed": profile.movement.travel_speed,
                "lift_height": profile.movement.lift_height,
                "wear_rate": profile.movement.wear_rate,
                "max_wear_depth": profile.movement.max_wear_depth,
            },
            "height": {
                "pen_down_z": profile.height.pen_down_z,
                "pen_up_z": profile.height.pen_up_z,
            },
            "water": {
                "enabled": profile.water.enabled,
                "two_pass": profile.water.two_pass,
                "cup_x": profile.water.cup_x,
                "cup_y": profile.water.cup_y,
                "cup_height": profile.water.cup_height,
                "cup_diameter": profile.water.cup_diameter,
                "dip_depth": profile.water.dip_depth,
                "dip_time": profile.water.dip_time,
                "dip_interval": profile.water.dip_interval,
                "scrape_distance": profile.water.scrape_distance,
                "scrape_speed": profile.water.scrape_speed,
                "pass2": {
                    "draw_speed": profile.water.pass2.draw_speed,
                    "travel_speed": profile.water.pass2.travel_speed,
                    "pen_down_z": profile.water.pass2.pen_down_z,
                    "lift_height": profile.water.pass2.lift_height,
                    "change_z": profile.water.pass2.change_z,
                    "change_x": profile.water.pass2.change_x,
                    "change_y": profile.water.pass2.change_y,
                },
            },
            "fill": {
                "enabled": profile.fill.enabled,
                "fill_type": profile.fill.fill_type,
                "spacing": profile.fill.spacing,
                "angle": profile.fill.angle,
            },
        })
    except FileNotFoundError:
        return jsonify({"error": f"Profile '{tool_name}' not found"}), 404


@app.route("/api/settings/<tool_name>", methods=["POST"])
def save_settings(tool_name):
    data = request.json or {}
    try:
        profile = config.load_profile(tool_name)
    except FileNotFoundError:
        profile = config.ToolProfile(name=tool_name.title())

    if "movement" in data:
        for k, v in data["movement"].items():
            if hasattr(profile.movement, k):
                setattr(profile.movement, k, float(v))
    if "water" in data:
        for k, v in data["water"].items():
            if k == "enabled":
                profile.water.enabled = bool(v)
            elif k == "two_pass":
                profile.water.two_pass = bool(v)
            elif k == "pass2":
                for pk, pv in v.items():
                    if hasattr(profile.water.pass2, pk):
                        setattr(profile.water.pass2, pk, float(pv))
            elif hasattr(profile.water, k):
                setattr(profile.water, k, float(v) if isinstance(v, (int, float)) else v)
    if "fill" in data:
        for k, v in data["fill"].items():
            if k == "enabled":
                profile.fill.enabled = bool(v)
            elif hasattr(profile.fill, k):
                setattr(profile.fill, k, float(v) if isinstance(v, (int, float)) else v)

    profile.recalc_pen_up()
    config.save_profile(tool_name, profile)
    return jsonify({"ok": True})


@app.route("/api/profiles", methods=["GET"])
def list_profiles():
    return jsonify(config.list_profiles())


# ── Page Size ────────────────────────────────────────────────────────

@app.route("/api/page-size", methods=["GET"])
def get_page_size():
    return jsonify(config.load_page_size())


@app.route("/api/page-size", methods=["POST"])
def set_page_size():
    data = request.json or {}
    width = float(data.get("width", 220))
    height = float(data.get("height", 220))
    preset = data.get("preset", "custom")
    offset_x = float(data.get("offset_x", 0))
    offset_y = float(data.get("offset_y", 0))
    config.save_page_size(width, height, preset, offset_x, offset_y)
    return jsonify({"ok": True, "width": width, "height": height, "preset": preset,
                    "offset_x": offset_x, "offset_y": offset_y})


# ── Output Cleanup ───────────────────────────────────────────────────

OUTPUT_MAX_AGE_DAYS = 7


def _cleanup_output():
    """Remove generated files older than OUTPUT_MAX_AGE_DAYS."""
    if not config.OUTPUT_DIR.exists():
        return
    cutoff = time.time() - OUTPUT_MAX_AGE_DAYS * 86400
    removed = 0
    for f in config.OUTPUT_DIR.iterdir():
        if f.is_file() and f.stat().st_mtime < cutoff:
            f.unlink()
            removed += 1
    if removed:
        print(f"Cleaned {removed} old files from output/")


@app.route("/api/cleanup", methods=["POST"])
def cleanup_output():
    """Delete all generated files in output/."""
    removed = 0
    if config.OUTPUT_DIR.exists():
        for f in config.OUTPUT_DIR.iterdir():
            if f.is_file():
                f.unlink()
                removed += 1
    uploaded_svgs.clear()
    generated_gcode.clear()
    return jsonify({"ok": True, "removed": removed})


# ── Main ─────────────────────────────────────────────────────────────

@app.route("/api/shutdown", methods=["POST"])
def shutdown():
    """Shut down the Flask server."""
    func = request.environ.get("werkzeug.server.shutdown")
    if func is not None:
        func()
    else:
        os._exit(0)
    return jsonify({"ok": True})


if __name__ == "__main__":
    _cleanup_output()
    print("Pen Plotter — http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=True, threaded=True)
