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
import threading
import time
import uuid
import defusedxml.ElementTree as ET
from pathlib import Path

import cv2
import numpy as np
import requests
import svgwrite
from flask import Flask, request, jsonify, send_from_directory, send_file
from flask_sock import Sock

import config
import gcode
import manga
import serial_conn

app = Flask(__name__, static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10MB upload limit
sock = Sock(app)

# Global state
serial = serial_conn.SerialConnection()
SERIAL_PORT_FILE = Path(__file__).parent / "serial_port.txt"

logger = app.logger


def _safe_error(e: Exception, context: str = "Operation") -> str:
    """Log full exception, return safe message for client."""
    logger.exception("%s failed", context)
    # Preserve known user-facing errors, sanitize unexpected ones
    msg = str(e)
    if any(kw in msg for kw in ("not calibrated", "not found", "not connected",
                                  "No file", "Only SVG", "Invalid", "Too complex",
                                  "No strokes", "SVG too complex")):
        return msg
    return f"{context} failed"


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
            time.sleep(1)
            _raise_to_safe()
            return True
        except Exception:
            return False
    return False


def _run_in_thread(fn, *args, **kwargs):
    """Run a blocking serial operation in a background thread."""
    threading.Thread(target=fn, args=args, kwargs=kwargs, daemon=True).start()


def _raise_to_safe():
    """MANDATORY after any (re)connect: raise Z to SAFE_Z, then home.

    After E-stop, M999, or Flask restart, Marlin's reported position may
    not match physical reality. We MUST raise Z FIRST (so the pen clears
    the bed/paper) and THEN home — homing while pen is down would drag it.

    Sequence: raise Z → G28 → raise Z again (G28 may leave Z at max)
    """
    if not serial.is_connected:
        return None
    # Step 1: RAISE FIRST — get pen clear of bed before any other moves
    serial.send_command(f"G1 Z{config.SAFE_Z:.3f} F3000")
    time.sleep(2)
    # Step 2: HOME — establishes known physical position
    serial.send_command("G28")
    time.sleep(12)
    # Step 3: Raise to exact SAFE_Z (G28 may leave Z higher)
    serial.send_command(f"G1 Z{config.SAFE_Z:.3f} F3000")
    time.sleep(1)
    return serial.get_position()


uploaded_svgs: dict[str, str] = {}  # id -> file path
generated_gcode: dict[str, str] = {}  # id -> gcode string
text_polylines: dict[str, list] = {}  # id -> polylines (from text patterns, no SVG round-trip)
wc_context: dict[str, dict] = {}  # id -> {tool, transform_kwargs} for two-pass reconversion
ws_clients: list = []
MAX_STORE_SIZE = 100

# Thread lock for all shared state mutations
_stores_lock = threading.Lock()


def _trim_stores():
    """Evict oldest entries if stores exceed MAX_STORE_SIZE. Must hold _stores_lock."""
    for store in (uploaded_svgs, generated_gcode, text_polylines, wc_context):
        while len(store) > MAX_STORE_SIZE:
            oldest = next(iter(store))
            store.pop(oldest)

# Ink / Slate state
_slate_process: subprocess.Popen | None = None
_ink_strokes: list = []  # accumulated BLE streamed strokes from capture.py
PENZ_DIR = r"C:\Users\aaron\penz"

# Live plot state (accessed from Flask threads + serial thread)
_live_lock = threading.Lock()
_live_plot_active = False
_live_plot_profile = None
_live_stroke_points: list = []  # accumulate mm points for current stroke
_jog_mode_active = False
_jog_pen_down = False
_jog_tool = "pencil"

# Proximity calibration state (hover-align page offset)
_prox_cal_lock = threading.Lock()
_prox_cal_active = False
_prox_cal_step = 0         # 0=idle, 1-3=collecting reference points
_prox_cal_ref_points = []  # list of {hotend_x, hotend_y, wacom_x, wacom_y}
_prox_hover_buffer = []    # accumulated hover samples [(wacom_x, wacom_y, bed_x, bed_y), ...]
_last_hover_pos = None     # latest hover position [bed_x, bed_y, wacom_x, wacom_y]
_last_stroke_pos = None    # latest stroke position (pen down with pressure)
_capture_status = {
    "connected": False,
    "live_mode": False,
    "pen_down": False,
    "last_heartbeat": 0,
}
_PROX_REF_POSITIONS = [
    {"hx": 60,  "hy": 60,  "label": "Front-Left"},
    {"hx": 190, "hy": 60,  "label": "Front-Right"},
    {"hx": 60,  "hy": 190, "label": "Back-Left"},
]


def _wacom_to_bed(wacom_x, wacom_y, page_w, page_h):
    """Map Wacom Slate A5 portrait coords to plotter bed mm.

    Slate is portrait: 21600 (X) x 14700 (Y).
    Same mapping as the JS fix: swap X/Y and rotate 180°.
    """
    x_mm = (1 - wacom_y / 14700) * page_w
    y_mm = wacom_x / 21600 * page_h
    return round(x_mm, 3), round(y_mm, 3)


def _pressure_to_speed(pressure, base_speed):
    """Map pressure (0-4095) to feed rate. Higher pressure = slower = bolder."""
    PRESSURE_MIN_SPEED = 600    # mm/min at max pressure (slow = bold)
    PRESSURE_MAX_SPEED = 2000   # mm/min at min pressure (fast = light)
    PRESSURE_CEILING = 4095     # 12-bit max
    if pressure <= 0:
        return base_speed
    ratio = min(pressure / PRESSURE_CEILING, 1.0)
    return PRESSURE_MIN_SPEED + (PRESSURE_MAX_SPEED - PRESSURE_MIN_SPEED) * (1.0 - ratio)


def _stroke_to_gcode(points_mm, profile):
    """Convert a list of (x_mm, y_mm[, pressure]) points to G-code lines for one stroke.

    Pressure-responsive: maps 12-bit pressure to pen feed rate with EMA smoothing.
    """
    if len(points_mm) < 2:
        return []
    ht = profile.height
    mv = profile.movement
    lines = []
    x0, y0 = points_mm[0][0], points_mm[0][1]
    lines.append(f"G0 X{x0:.3f} Y{y0:.3f} F{mv.travel_speed:.0f}")
    lines.append(f"G1 Z{ht.pen_down_z:.3f} F3000")
    ema_speed = None
    for pt in points_mm[1:]:
        x, y = pt[0], pt[1]
        p = pt[2] if len(pt) >= 3 else 2048
        raw_speed = _pressure_to_speed(p, mv.draw_speed)
        # Exponential moving average (alpha=0.3) to smooth speed transitions
        if ema_speed is None:
            ema_speed = raw_speed
        else:
            ema_speed = 0.3 * raw_speed + 0.7 * ema_speed
        lines.append(f"G1 X{x:.3f} Y{y:.3f} F{ema_speed:.0f}")
    lines.append(f"G1 Z{ht.pen_up_z:.3f} F3000")
    return lines


def _generate_illustration(x, y, w, h):
    """Generate pen-plotter-friendly vector illustration (house, tree, sun).

    All coords are absolute, scaled to fit within (x, y, w, h).
    Returns list of polylines (each a list of (x, y) tuples).
    """
    strokes = []

    # House — centered in left portion of box
    hx = x + w * 0.05
    hy = y + h * 0.35
    hw = w * 0.45
    hh = h * 0.50
    # Walls
    strokes.append([(hx, hy + hh), (hx, hy), (hx + hw, hy), (hx + hw, hy + hh)])
    # Roof
    strokes.append([(hx - w * 0.03, hy), (hx + hw / 2, hy - h * 0.18), (hx + hw + w * 0.03, hy)])
    # Door
    dw, dh = hw * 0.22, hh * 0.45
    dx = hx + hw / 2 - dw / 2
    dy = hy + hh - dh
    strokes.append([(dx, dy), (dx, dy + dh), (dx + dw, dy + dh), (dx + dw, dy)])
    # Window
    ww, wh = hw * 0.2, hh * 0.2
    wx = hx + hw * 0.12
    wy = hy + hh * 0.2
    strokes.append([(wx, wy), (wx + ww, wy), (wx + ww, wy + wh), (wx, wy + wh), (wx, wy)])

    # Tree — right side
    tx = x + w * 0.72
    tw = w * 0.12
    # Trunk
    t_base = y + h * 0.85
    t_top = y + h * 0.50
    strokes.append([(tx, t_base), (tx, t_top), (tx + tw, t_top), (tx + tw, t_base)])
    # Foliage (stacked triangles)
    cx = tx + tw / 2
    strokes.append([
        (cx - w * 0.14, t_top + h * 0.02),
        (cx, t_top - h * 0.20),
        (cx + w * 0.14, t_top + h * 0.02),
    ])
    strokes.append([
        (cx - w * 0.10, t_top - h * 0.08),
        (cx, t_top - h * 0.30),
        (cx + w * 0.10, t_top - h * 0.08),
    ])

    # Sun — top right
    sx, sy = x + w * 0.82, y + h * 0.12
    sr = min(w, h) * 0.08
    # Circle (16-segment approximation)
    pts = []
    for i in range(17):
        a = 2 * math.pi * i / 16
        pts.append((sx + sr * math.cos(a), sy + sr * math.sin(a)))
    strokes.append(pts)
    # Rays
    for i in range(8):
        a = 2 * math.pi * i / 8
        strokes.append([
            (sx + sr * 1.3 * math.cos(a), sy + sr * 1.3 * math.sin(a)),
            (sx + sr * 1.8 * math.cos(a), sy + sr * 1.8 * math.sin(a)),
        ])

    return strokes


# ── WebSocket ────────────────────────────────────────────────────────

def _broadcast_progress(completed, total, info):
    """Broadcast progress to all connected WebSocket clients."""
    msg = json.dumps({
        "type": "progress",
        "completed": completed,
        "total": total,
        "info": info,
        "stats": serial.last_print_stats,
    })
    with _stores_lock:
        clients = ws_clients[:]
    for ws in clients:
        try:
            ws.send(msg)
        except Exception:
            with _stores_lock:
                try:
                    ws_clients.remove(ws)
                except ValueError:
                    pass


def _broadcast_ink(points):
    """Broadcast ink stroke data to all connected WebSocket clients."""
    msg = json.dumps({"type": "ink", "points": points})
    with _stores_lock:
        clients = ws_clients[:]
    for ws in clients:
        try:
            ws.send(msg)
        except Exception:
            with _stores_lock:
                try:
                    ws_clients.remove(ws)
                except ValueError:
                    pass


def _broadcast_event(event_type, payload=None):
    """Broadcast a typed event to all connected WebSocket clients."""
    data = {"type": "ink_event", "event": event_type}
    if payload:
        data.update(payload)
    msg = json.dumps(data)
    with _stores_lock:
        clients = ws_clients[:]
    for ws in clients:
        try:
            ws.send(msg)
        except Exception:
            with _stores_lock:
                try:
                    ws_clients.remove(ws)
                except ValueError:
                    pass


def _broadcast_stroke_complete(points_mm):
    """Broadcast completed stroke points to browser for canvas overlay."""
    msg = json.dumps({"type": "ink_stroke_complete", "points": points_mm})
    with _stores_lock:
        clients = ws_clients[:]
    for ws in clients:
        try:
            ws.send(msg)
        except Exception:
            with _stores_lock:
                try:
                    ws_clients.remove(ws)
                except ValueError:
                    pass


@sock.route("/ws")
def websocket(ws):
    with _stores_lock:
        ws_clients.append(ws)
    try:
        while True:
            ws.receive()  # Keep alive
    except Exception:
        pass
    finally:
        with _stores_lock:
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
        time.sleep(1)
        _raise_to_safe()
        return jsonify({"ok": True, "port": port})
    except Exception as e:
        return jsonify({"error": _safe_error(e, "Operation")}), 500


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
        "last_print": serial.last_print_stats,
    })


@app.route("/api/send-command", methods=["POST"])
def send_command():
    """Send a G-code command and return the response."""
    if not serial.is_connected:
        return jsonify({"error": "Printer not connected"}), 400
    data = request.json or {}
    command = data.get("command", "").strip()
    if not command:
        return jsonify({"error": "command required"}), 400
    if "\n" in command or "\r" in command:
        return jsonify({"error": "Multi-line commands not allowed"}), 400
    # Only allow safe movement/control commands
    allowed_prefixes = ("G0", "G1", "G4", "G28", "G90", "G91", "M84", "M112", "M114", "M400")
    cmd_upper = command.upper().split(";")[0].strip()
    if not any(cmd_upper.startswith(p) for p in allowed_prefixes):
        return jsonify({"error": f"Command not allowed: {command.split()[0]}"}), 400
    try:
        serial.send_command(command)
        position = {}
        if data.get("wait"):
            time.sleep(0.3)
            serial.send_command("M400")
            time.sleep(0.3)
            position = serial.get_position()
        return jsonify({"ok": True, "command": command, "position": position})
    except Exception as e:
        return jsonify({"error": _safe_error(e, "Operation")}), 500


# ── Upload & Convert ────────────────────────────────────────────────

def _get_transform_params(data: dict) -> dict:
    """Extract transform parameters from request data with validation."""
    def _clamp(val, lo, hi, default):
        try:
            v = float(val)
        except (TypeError, ValueError):
            return default
        return max(lo, min(hi, v))

    return {
        "optimize": bool(data.get("optimize", True)),
        "simplify": bool(data.get("simplify", False)),
        "simplify_tolerance": _clamp(data.get("simplify_tolerance", 0.1), 0.01, 10.0, 0.1),
        "user_scale": _clamp(data.get("scale", 1.0), 0.01, 100.0, 1.0),
        "user_rotate": _clamp(data.get("rotate", 0.0), -360.0, 360.0, 0.0),
        "user_translate_x": _clamp(data.get("translate_x", 0.0), -500.0, 500.0, 0.0),
        "user_translate_y": _clamp(data.get("translate_y", 0.0), -500.0, 500.0, 0.0),
        "mirror_x": bool(data.get("mirror_x", False)),
        "mirror_y": bool(data.get("mirror_y", False)),
        "no_fit": bool(data.get("no_fit", False)),
        "bed_x": _clamp(data.get("page_width", config.PRINTER_BED_X), 10, 500, config.PRINTER_BED_X),
        "bed_y": _clamp(data.get("page_height", config.PRINTER_BED_Y), 10, 500, config.PRINTER_BED_Y),
        "page_offset_x": _clamp(data.get("page_offset_x", 0), 0, 500, 0),
        "page_offset_y": _clamp(data.get("page_offset_y", 0), 0, 500, 0),
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
    with _stores_lock:
        uploaded_svgs[file_id] = str(out_path)
        _trim_stores()

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
        two_pass = False
        id_pass2 = None
        if file_id in uploaded_svgs and uploaded_svgs[file_id] is not None:
            gc, polylines, toolpath, stats, meta = gcode.svg_to_gcode(
                uploaded_svgs[file_id], tool_name, **transform_kwargs
            )
            # Two-pass watercolor: store separate G-code entries
            if meta.get("two_pass"):
                two_pass = True
                pass2_gc = meta["pass2_gcode"]
                id_pass2 = f"{file_id}_pass2"
                with _stores_lock:
                    generated_gcode[id_pass2] = pass2_gc
                gc = meta["pass1_gcode"]  # primary is now pass1
                # Also save pass2 G-code file
                gcode_path_p2 = config.OUTPUT_DIR / f"{id_pass2}.gcode"
                with open(gcode_path_p2, "w") as f:
                    f.write(pass2_gc)
                # Store context for pass2 reconversion after recalibration
                with _stores_lock:
                    wc_context[file_id] = {
                        "tool": tool_name,
                        "transform_kwargs": transform_kwargs,
                    }
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
            # Use page height (not bed height) to avoid shifting manga coordinates.
            page_size = config.load_page_size()
            bed_y = transform_kwargs.get("bed_y", page_size.get("height", config.PRINTER_BED_Y))
            bed_x = transform_kwargs.get("bed_x", page_size.get("width", config.PRINTER_BED_X))
            transform_kwargs["bed_x"] = bed_x
            transform_kwargs["bed_y"] = bed_y
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
                "no_fit": True,
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
                        layer=seg.get("layer", ""),
                    ))
            stats = gcode.compute_stats(
                preview_polylines, travel_segs,
                profile.movement.draw_speed, profile.movement.travel_speed,
            )
            polylines = preview_polylines

        with _stores_lock:
            generated_gcode[file_id] = gc
            _trim_stores()

        # Save G-code file
        gcode_path = config.OUTPUT_DIR / f"{file_id}.gcode"
        with open(gcode_path, "w") as f:
            f.write(gc)

        profile = config.load_profile(tool_name)
        preview = []
        for pl in polylines:
            preview.append([(round(p[0], 2), round(p[1], 2)) for p in pl.points])

        line_count = len([l for l in gc.splitlines() if l.strip() and not l.strip().startswith(";")])
        resp = {
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
        }
        if two_pass:
            resp["two_pass"] = True
            resp["id_pass2"] = id_pass2
        return jsonify(resp)
    except Exception as e:
        return jsonify({"error": _safe_error(e, "Operation")}), 500


@app.route("/api/convert-pass2", methods=["POST"])
def convert_pass2():
    """Regenerate watercolor pass 2 G-code with current calibration values.

    Called after user recalibrates Z for the brush tool, so pass 2 uses
    the updated pen_down_z from calibration.json.
    """
    data = request.json or {}
    file_id = data.get("id")
    tool_name = data.get("tool", "watercolor")

    if file_id not in wc_context:
        return jsonify({"error": "No watercolor context found — convert first"}), 404

    ctx = wc_context[file_id]
    saved_tool = ctx.get("tool", tool_name)
    transform_kwargs = ctx["transform_kwargs"]

    # Reload profile (picks up updated calibration.json)
    profile = config.load_profile(saved_tool)
    if profile.height.pen_down_z == 0.0:
        return jsonify({"error": f"Tool '{saved_tool}' not calibrated (pen_down_z=0). Run calibration first."}), 400

    if file_id not in uploaded_svgs:
        return jsonify({"error": "Original SVG not found"}), 404

    if uploaded_svgs.get(file_id) is None:
        return jsonify({"error": "Two-pass watercolor not supported for text/manga polylines"}), 400

    try:
        # Re-parse SVG and re-run the full pipeline to get polylines + transform
        polylines = gcode.parse_svg(uploaded_svgs[file_id])
        polylines = gcode.apply_fill(polylines, profile.fill)

        # Simplify
        if transform_kwargs.get("simplify"):
            simplified = []
            for pl in polylines:
                pts = gcode.simplify_polyline(pl.points, transform_kwargs.get("simplify_tolerance", 0.1))
                if len(pts) >= 2:
                    simplified.append(gcode.Polyline(points=pts, layer=pl.layer, color=pl.color))
            polylines = simplified

        # Rotate/mirror
        user_rotate = transform_kwargs.get("user_rotate", 0.0)
        mirror_x = transform_kwargs.get("mirror_x", False)
        mirror_y = transform_kwargs.get("mirror_y", False)
        if user_rotate != 0.0 or mirror_x or mirror_y:
            import math as _math
            transformed = []
            cos_r = _math.cos(_math.radians(user_rotate))
            sin_r = _math.sin(_math.radians(user_rotate))
            for pl in polylines:
                new_pts = []
                for px, py in pl.points:
                    rx = px * cos_r - py * sin_r
                    ry = px * sin_r + py * cos_r
                    if mirror_x: rx = -rx
                    if mirror_y: ry = -ry
                    new_pts.append((rx, ry))
                transformed.append(gcode.Polyline(points=new_pts, layer=pl.layer, color=pl.color))
            polylines = transformed

        if transform_kwargs.get("optimize", True):
            polylines = gcode.optimize_path(polylines)

        # Run through polylines_to_gcode to get the transform + pass2 gcode
        result = gcode.polylines_to_gcode(polylines, profile, **transform_kwargs)
        # Two-pass mode returns (pass1_str, pass2_str, toolpath, meta)
        if profile.water.enabled and profile.water.two_pass:
            _, pass2_gc, toolpath, meta = result
        else:
            # Fallback: profile changed since initial convert
            return jsonify({"error": "Water tool profile no longer has two-pass enabled"}), 400

        id_pass2 = f"{file_id}_pass2"
        with _stores_lock:
            generated_gcode[id_pass2] = pass2_gc

        # Save pass2 file
        gcode_path_p2 = config.OUTPUT_DIR / f"{id_pass2}.gcode"
        with open(gcode_path_p2, "w") as f:
            f.write(pass2_gc)

        line_count = len([l for l in pass2_gc.splitlines() if l.strip() and not l.strip().startswith(";")])

        return jsonify({
            "id": id_pass2,
            "line_count": line_count,
            "ok": True,
        })
    except Exception as e:
        return jsonify({"error": _safe_error(e, "Operation")}), 500


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

    elif pattern == "border":
        page_w = float(data.get("page_width", 180))
        page_h = float(data.get("page_height", 175))
        margin = float(data.get("border_margin", 5))
        inset = float(data.get("border_inset", 10))
        # SVG viewBox mapped to page mm
        svg_parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {page_w} {page_h}">']
        # Outer box
        svg_parts.append(f'<rect x="{margin}" y="{margin}" width="{page_w - 2*margin}" height="{page_h - 2*margin}" fill="none" stroke="black"/>')
        # Inner box
        m2 = margin + inset
        svg_parts.append(f'<rect x="{m2}" y="{m2}" width="{page_w - 2*m2}" height="{page_h - 2*m2}" fill="none" stroke="black"/>')
        # Corner diagonals
        svg_parts.append(f'<line x1="{margin}" y1="{margin}" x2="{m2}" y2="{m2}" stroke="black"/>')
        svg_parts.append(f'<line x1="{page_w - margin}" y1="{margin}" x2="{page_w - m2}" y2="{m2}" stroke="black"/>')
        svg_parts.append(f'<line x1="{page_w - margin}" y1="{page_h - margin}" x2="{page_w - m2}" y2="{page_h - m2}" stroke="black"/>')
        svg_parts.append(f'<line x1="{margin}" y1="{page_h - margin}" x2="{m2}" y2="{page_h - m2}" stroke="black"/>')
        # Edge tick marks (3 per edge)
        for i in range(1, 4):
            frac = i / 4.0
            x = margin + frac * (page_w - 2 * margin)
            svg_parts.append(f'<line x1="{x}" y1="{margin}" x2="{x}" y2="{m2}" stroke="black"/>')
            svg_parts.append(f'<line x1="{x}" y1="{page_h - margin}" x2="{x}" y2="{page_h - m2}" stroke="black"/>')
            y = margin + frac * (page_h - 2 * margin)
            svg_parts.append(f'<line x1="{margin}" y1="{y}" x2="{m2}" y2="{y}" stroke="black"/>')
            svg_parts.append(f'<line x1="{page_w - margin}" y1="{y}" x2="{page_w - m2}" y2="{y}" stroke="black"/>')
        svg_parts.append('</svg>')
        svg_content = "\n".join(svg_parts)
        svg_id = uuid.uuid4().hex[:8]
        svg_path = f"output/{svg_id}.svg"
        with open(svg_path, "w") as f:
            f.write(svg_content)
        with _stores_lock:
            uploaded_svgs[svg_id] = svg_path
        polylines = gcode.parse_svg(svg_path)
        return jsonify({
            "id": svg_id,
            "polylines": [[[round(x, 2), round(y, 2)] for x, y in pl.points] for pl in polylines],
            "stroke_count": len(polylines),
        })

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

        # Cursive needs wider spacing than hershey
        if font_style == "cursive":
            char_spacing = scale * 1.3
            line_spacing_factor = max(line_spacing_factor, 2.0)
        else:
            char_spacing = scale * 1.0

        line_height = char_h_mm * line_spacing_factor
        usable_w = bed_x - 2 * margin
        usable_h = bed_y - 2 * margin

        # ── Picture zone (optional inline illustration) ──
        has_picture = all(k in data for k in ("picture_x", "picture_y", "picture_w", "picture_h"))
        if has_picture:
            pic_x = float(data["picture_x"])
            pic_y = float(data["picture_y"])
            pic_w = float(data["picture_w"])
            pic_h = float(data["picture_h"])
            pic_side = data.get("picture_side", "right")
            pic_gap = 5.0  # mm gap between text and picture
        else:
            pic_x = pic_y = pic_w = pic_h = 0
            pic_side = "right"
            pic_gap = 0

        def _line_bounds(y_cur):
            """Return (x_start, available_width) for a text line at y_cur."""
            if not has_picture:
                return (margin, usable_w)
            line_bottom = y_cur + char_h_mm
            pic_top = pic_y
            pic_bottom = pic_y + pic_h
            # Check if this line overlaps the picture zone vertically
            if line_bottom > pic_top and y_cur < pic_bottom:
                if pic_side == "right":
                    return (margin, pic_x - margin - pic_gap)
                else:
                    x_start = pic_x + pic_w + pic_gap
                    return (x_start, bed_x - margin - x_start)
            return (margin, usable_w)

        # ── Word-wrap with picture zone awareness ──
        _render_fn = _font.text_to_cursive if font_style == "cursive" else _font.text_to_strokes

        def _measure_line(line_str):
            """Render a candidate line at origin and return its actual bounding-box width."""
            strokes = _render_fn(line_str, x=0, y=0, scale=scale, spacing=char_spacing)
            if not strokes:
                return 0.0
            xs = [p[0] for s in strokes for p in s]
            return (max(xs) - min(xs)) if xs else 0.0

        def _advance_past_picture(y, avail_w):
            """Skip ahead past the narrow picture zone if a word doesn't fit."""
            while avail_w < usable_w and y + line_height < bed_y - margin:
                y += line_height
                _, avail_w = _line_bounds(y)
            return y, avail_w

        raw_paragraphs = text.split("\n")
        layout_lines = []  # list of (text, x_start, avail_w) tuples
        y_wrap = margin    # track Y cursor through wrapping pass

        for para in raw_paragraphs:
            if para.strip() == "":
                layout_lines.append(("", margin, usable_w))
                y_wrap += line_height * 0.6
                continue
            words = para.split()
            if not words:
                layout_lines.append(("", margin, usable_w))
                y_wrap += line_height * 0.6
                continue

            # Greedy wrap using whole-line measurement
            x_start, avail_w = _line_bounds(y_wrap)
            current_words = [words[0]]
            # If first word exceeds narrow width, skip ahead to full-width line
            if _measure_line(words[0]) > avail_w:
                y_wrap, avail_w = _advance_past_picture(y_wrap, avail_w)
                x_start, _ = _line_bounds(y_wrap)
            for word in words[1:]:
                candidate = " ".join(current_words + [word])
                if _measure_line(candidate) <= avail_w:
                    current_words.append(word)
                else:
                    layout_lines.append((" ".join(current_words), x_start, avail_w))
                    y_wrap += line_height
                    x_start, avail_w = _line_bounds(y_wrap)
                    current_words = [word]
                    # Oversized word — skip to full-width line
                    if _measure_line(word) > avail_w:
                        y_wrap, avail_w = _advance_past_picture(y_wrap, avail_w)
                        x_start, _ = _line_bounds(y_wrap)
            layout_lines.append((" ".join(current_words), x_start, avail_w))
            y_wrap += line_height

        # ── Render each layout line at its (x_start, y_cursor) ──
        all_strokes = []
        y_cursor = margin   # start at top margin (Y-down screen coords; flipped later)
        rendered_count = 0

        for line_text, x_start, avail_w in layout_lines:
            if y_cursor + char_h_mm > bed_y - margin:
                break   # out of vertical space
            if line_text.strip() == "":
                y_cursor += line_height * 0.6   # paragraph break
                continue

            if font_style == "cursive":
                line_strokes = _font.text_to_cursive(
                    line_text, x=x_start, y=y_cursor, scale=scale, spacing=char_spacing
                )
            else:
                line_strokes = _font.text_to_strokes(
                    line_text, x=x_start, y=y_cursor, scale=scale, spacing=char_spacing
                )
            all_strokes.extend(line_strokes)
            y_cursor += line_height
            rendered_count += 1

        # ── Append illustration strokes if picture zone defined ──
        if has_picture:
            all_strokes.extend(_generate_illustration(pic_x, pic_y, pic_w, pic_h))
            # Thin border around picture zone
            all_strokes.append([
                (pic_x, pic_y), (pic_x + pic_w, pic_y),
                (pic_x + pic_w, pic_y + pic_h), (pic_x, pic_y + pic_h),
                (pic_x, pic_y),
            ])

        if not all_strokes:
            return jsonify({"error": "No strokes generated"}), 400

        polylines = [gcode.Polyline(points=s) for s in all_strokes if len(s) >= 2]

        if not polylines:
            return jsonify({"error": "No strokes generated"}), 400

        file_id = uuid.uuid4().hex[:8]
        with _stores_lock:
            text_polylines[file_id] = polylines
        return jsonify({
            "id": file_id,
            "polylines": preview,
            "stroke_count": len(polylines),
            "line_count_rendered": rendered_count,
        })

    svg_parts.append('</svg>')
    svg_content = "\n".join(svg_parts)

    # Save and process like a normal upload
    file_id = uuid.uuid4().hex[:8]
    out_path = config.OUTPUT_DIR / f"{file_id}.svg"
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        f.write(svg_content)
    with _stores_lock:
        uploaded_svgs[file_id] = str(out_path)

    # For geometric patterns, also generate G-code with transforms
    tool = data.get("tool", "pencil")
    transform_kwargs = _get_transform_params(data)

    try:
        gc, polylines, toolpath_data, stats, meta = gcode.svg_to_gcode(
            str(out_path), tool, **transform_kwargs
        )
        with _stores_lock:
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
    if not re.match(r'^[a-zA-Z0-9_-]+$', file_id):
        return jsonify({"error": "Invalid file ID"}), 400
    gcode_path = config.OUTPUT_DIR / f"{file_id}.gcode"
    resolved = gcode_path.resolve()
    if not str(resolved).startswith(str(config.OUTPUT_DIR.resolve())):
        return jsonify({"error": "Invalid file ID"}), 400
    if not resolved.exists():
        return jsonify({"error": "G-code not found"}), 404
    return send_file(resolved, as_attachment=True, download_name=f"plot_{file_id}.gcode")


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
    with _stores_lock:
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
    if not re.match(r'^[a-zA-Z0-9_-]+$', file_id):
        return jsonify({"error": "Invalid file ID"}), 400
    svg_path = config.OUTPUT_DIR / f"trace_{file_id}.svg"
    resolved = svg_path.resolve()
    if not str(resolved).startswith(str(config.OUTPUT_DIR.resolve())):
        return jsonify({"error": "Invalid file ID"}), 400
    if not resolved.exists():
        return jsonify({"error": "SVG not found"}), 404
    return send_file(resolved, as_attachment=True, download_name=f"trace_{file_id}.svg")


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

    # Jog mode: redirect strokes to jog endpoint logic
    if _jog_mode_active and points and serial.is_connected:
        page = config.load_page_size()
        pw, ph = page["width"], page["height"]
        pt = points[-1]
        if len(pt) >= 2:
            mx, my = _wacom_to_bed(pt[0], pt[1], pw, ph)
            pressure = pt[2] if len(pt) >= 3 else 2048
            speed = _pressure_to_speed(pressure, 1500)
            try:
                profile = config.load_profile(_jog_tool)
                cal = config.load_calibration()
                pen_off_x = cal.get("offset_x", 0)
                pen_off_y = cal.get("offset_y", 0)
                hx = mx - pen_off_x
                hy = my - pen_off_y
                if _jog_pen_down:
                    serial.send_command(f"G1 X{hx:.3f} Y{hy:.3f} Z{profile.height.pen_down_z:.3f} F{speed:.0f}")
                else:
                    serial.send_command(f"G0 X{hx:.3f} Y{hy:.3f} Z{config.SAFE_Z} F{profile.movement.travel_speed:.0f}")
                _broadcast_event("jog", {"x": mx, "y": my, "pen_down": _jog_pen_down})
            except Exception:
                pass
        return jsonify({"status": "ok"})

    if points:
        global _last_stroke_pos
        with _live_lock:
            _ink_strokes.append(points)
        # Store last stroke point (raw wacom + pressure)
        last_pt = points[-1]
        if len(last_pt) >= 2:
            page = config.load_page_size()
            pw, ph = page["width"], page["height"]
            mx, my = _wacom_to_bed(last_pt[0], last_pt[1], pw, ph)
            p = last_pt[2] if len(last_pt) >= 3 else 0
            _last_stroke_pos = [mx, my, p, last_pt[0], last_pt[1]]
        print(f"  INK: {len(points)} pts, ws_clients={len(ws_clients)}", flush=True)
        _broadcast_ink(points)

        # Live plot: map points to bed mm and accumulate
        with _live_lock:
            if _live_plot_active and _live_plot_profile and serial.is_connected:
                page = config.load_page_size()
                pw, ph = page["width"], page["height"]
                for pt in points:
                    if len(pt) >= 2:
                        mx, my = _wacom_to_bed(pt[0], pt[1], pw, ph)
                        pressure = pt[2] if len(pt) >= 3 else 2048
                        _live_stroke_points.append((mx, my, pressure))

    # Stroke complete: generate G-code and queue for plotter
    with _live_lock:
        if stroke_end and _live_plot_active and _live_stroke_points and serial.is_connected:
            gcode_lines = _stroke_to_gcode(_live_stroke_points, _live_plot_profile)
            if gcode_lines:
                serial.queue_stroke(gcode_lines)
                print(f"  LIVE PLOT: queued stroke ({len(gcode_lines)} lines)", flush=True)
            # Broadcast completed stroke for canvas overlay
            _broadcast_stroke_complete(_live_stroke_points)
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
        return jsonify({"error": _safe_error(e, "Operation")}), 500


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


@app.route("/stream/event", methods=["POST"])
def ink_event():
    """Handle events from capture.py (button presses, etc.)."""
    global _jog_pen_down
    data = request.json or {}
    event_type = data.get("type")
    if event_type == "button":
        if _prox_cal_active:
            # During proximity calibration, button = capture current hover reading
            return _prox_cal_capture_point()
        elif _jog_mode_active:
            # In jog mode, button toggles pen up/down
            _jog_pen_down = not _jog_pen_down
            state_str = "down" if _jog_pen_down else "up"
            print(f"  JOG BUTTON → pen {state_str}", flush=True)
            _broadcast_event("jog", {"pen_toggle": True, "pen_down": _jog_pen_down})
        elif _live_plot_active:
            # In live plot mode, button toggles pause/resume
            if serial.is_paused:
                serial.resume()
                _broadcast_event("button", {"action": "resume"})
                print("  SLATE BUTTON → resume", flush=True)
            else:
                serial.pause()
                _broadcast_event("button", {"action": "pause"})
                print("  SLATE BUTTON → pause", flush=True)
    return jsonify({"status": "ok"})


@app.route("/stream/hover", methods=["POST"])
def ink_hover():
    """Receive hover (p=0) points from capture.py for calibration."""
    data = request.json or {}
    points = data.get("points", [])
    if not points:
        return jsonify({"status": "ok"})

    page = config.load_page_size()
    pw, ph = page["width"], page["height"]
    bed_points = []
    for pt in points:
        if len(pt) >= 2:
            mx, my = _wacom_to_bed(pt[0], pt[1], pw, ph)
            bed_points.append((mx, my))

    # Broadcast for UI visualization (last point only)
    if bed_points:
        global _last_hover_pos
        last_raw = points[-1] if points else None
        _last_hover_pos = [bed_points[-1][0], bed_points[-1][1], last_raw[0] if last_raw else 0, last_raw[1] if last_raw else 0]
        _broadcast_event("hover", {"points": bed_points[-1]})

    # Accumulate for calibration if active
    with _prox_cal_lock:
        if _prox_cal_active:
            for i, pt in enumerate(points):
                if len(pt) >= 2 and i < len(bed_points):
                    _prox_hover_buffer.append((pt[0], pt[1], bed_points[i][0], bed_points[i][1]))
            # Keep only last 200 samples
            if len(_prox_hover_buffer) > 200:
                _prox_hover_buffer[:] = _prox_hover_buffer[-200:]

    return jsonify({"status": "ok"})


@app.route("/stream/heartbeat", methods=["POST"])
def ink_heartbeat():
    """Receive heartbeat from capture.py — indicates it's alive and in live mode."""
    global _capture_status
    data = request.json or {}
    _capture_status = {
        "connected": True,
        "live_mode": data.get("live", False),
        "pen_down": data.get("pen_down", False),
        "last_heartbeat": time.time(),
    }
    return jsonify({"status": "ok"})


@app.route("/api/hover/position", methods=["GET"])
def get_hover_position():
    """Get the latest hover and stroke positions from the Slate."""
    hover = list(_last_hover_pos) if _last_hover_pos else None
    stroke = list(_last_stroke_pos) if _last_stroke_pos else None
    # Include capture status with 15s staleness check
    now = time.time()
    cs = dict(_capture_status)
    if now - cs["last_heartbeat"] > 15:
        cs["connected"] = False
        cs["live_mode"] = False
        cs["pen_down"] = False
    return jsonify({"hover": hover, "stroke": stroke, "capture": cs})


# ── Proximity Calibration (Hover-Align) ─────────────────────────────

def _prox_cal_capture_point():
    """Capture current hover reading as calibration reference point."""
    with _prox_cal_lock:
        if not _prox_cal_active:
            return jsonify({"error": "Calibration not active"}), 400
        if len(_prox_hover_buffer) < 5:
            return jsonify({"error": "Not enough hover samples — keep pen near tip"}), 400

        # Average the last N samples, discarding outliers
        samples = _prox_hover_buffer[-50:]
        wx_vals = [s[0] for s in samples]
        wy_vals = [s[1] for s in samples]
        # Median filter: keep samples within 1 std dev of median
        import statistics
        wx_med = statistics.median(wx_vals)
        wy_med = statistics.median(wy_vals)
        wx_std = statistics.stdev(wx_vals) if len(wx_vals) > 1 else 100
        wy_std = statistics.stdev(wy_vals) if len(wy_vals) > 1 else 100
        filtered_wx = [x for x in wx_vals if abs(x - wx_med) < max(wx_std, 200)]
        filtered_wy = [y for y in wy_vals if abs(y - wy_med) < max(wy_std, 200)]
        if not filtered_wx or not filtered_wy:
            return jsonify({"error": "Hover data too noisy — hold steady"}), 400

        avg_wx = sum(filtered_wx) / len(filtered_wx)
        avg_wy = sum(filtered_wy) / len(filtered_wy)

        ref = _PROX_REF_POSITIONS[_prox_cal_step - 1]
        _prox_cal_ref_points.append({
            "hotend_x": ref["hx"],
            "hotend_y": ref["hy"],
            "wacom_x": avg_wx,
            "wacom_y": avg_wy,
        })

        captured = _prox_cal_step
        total_refs = len(_prox_cal_ref_points)
        print(f"  PROX CAL: Point {captured}/3 captured (wacom {avg_wx:.0f},{avg_wy:.0f})", flush=True)
        _prox_hover_buffer.clear()

        _broadcast_event("calibration", {
            "action": "captured",
            "step": captured,
            "total": 3,
        })

    # If all 3 points captured, auto-finish
    if total_refs >= 3:
        return _prox_cal_finish()

    return jsonify({"ok": True, "step": captured, "total": 3, "message": f"Point {captured}/3 captured"})


def _prox_cal_finish():
    """Compute page offset from 3 captured reference points and save."""
    global _prox_cal_active, _prox_cal_step, _prox_cal_ref_points
    cal = config.load_calibration()
    pen_off_x = cal.get("offset_x", 0)
    pen_off_y = cal.get("offset_y", 0)
    page = config.load_page_size()
    pw, ph = page["width"], page["height"]

    offsets = []
    for rp in _prox_cal_ref_points:
        # Where the pen is (in bed coords)
        pen_bed_x = rp["hotend_x"] + pen_off_x
        pen_bed_y = rp["hotend_y"] + pen_off_y
        # Where the Wacom thinks the pen is (in bed coords via current page mapping)
        wacom_bed_x = (1 - rp["wacom_y"] / 14700) * pw
        wacom_bed_y = (rp["wacom_x"] / 21600) * ph
        # Offset = actual pen position - wacom-mapped position
        offsets.append((pen_bed_x - wacom_bed_x, pen_bed_y - wacom_bed_y))

    avg_ox = sum(o[0] for o in offsets) / len(offsets)
    avg_oy = sum(o[1] for o in offsets) / len(offsets)

    # Compute residuals (consistency check)
    residuals = [(o[0] - avg_ox, o[1] - avg_oy) for o in offsets]
    max_residual = max(math.sqrt(r[0]**2 + r[1]**2) for r in residuals)

    # Save computed page offset to page_size.json
    page_path = Path(__file__).parent / "page_size.json"
    page_data = {"width": pw, "height": ph, "preset": page.get("preset", "A5"),
                 "offset_x": round(avg_ox, 1), "offset_y": round(avg_oy, 1)}
    page_data.update({k: v for k, v in page.items() if k not in page_data})
    page_data["offset_x"] = round(avg_ox, 1)
    page_data["offset_y"] = round(avg_oy, 1)
    with open(page_path, "w") as f:
        json.dump(page_data, f, indent=2)

    # Park plotter
    try:
        serial.send_command(f"G0 Z{config.SAFE_Z} F3000")
        serial.send_command("G0 X0 Y0 F3000")
    except Exception:
        pass

    print(f"  PROX CAL: DONE offset=({avg_ox:.1f}, {avg_oy:.1f}) max_residual={max_residual:.1f}mm", flush=True)

    _prox_cal_active = False
    _prox_cal_step = 0
    _prox_cal_ref_points = []
    _prox_hover_buffer.clear()

    _broadcast_event("calibration", {
        "action": "finished",
        "offset_x": round(avg_ox, 1),
        "offset_y": round(avg_oy, 1),
        "residuals": [round(math.sqrt(r[0]**2 + r[1]**2), 1) for r in residuals],
        "max_residual": round(max_residual, 1),
    })

    return jsonify({
        "ok": True,
        "offset_x": round(avg_ox, 1),
        "offset_y": round(avg_oy, 1),
        "max_residual": round(max_residual, 1),
        "residuals": [round(math.sqrt(r[0]**2 + r[1]**2), 1) for r in residuals],
    })


@app.route("/api/proximity-calibration/start", methods=["POST"])
def prox_cal_start():
    """Start hover-align calibration: homes plotter, enters calibration mode."""
    global _prox_cal_active, _prox_cal_step, _prox_cal_ref_points, _prox_hover_buffer
    if not serial.is_connected:
        return jsonify({"error": "Plotter not connected"}), 400
    if _prox_cal_active:
        return jsonify({"error": "Calibration already active"}), 400

    # Home and raise to safe Z
    _raise_to_safe()

    _prox_cal_active = True
    _prox_cal_step = 0
    _prox_cal_ref_points = []
    _prox_hover_buffer.clear()

    print("  PROX CAL: started, plotter homed", flush=True)
    _broadcast_event("calibration", {"action": "started"})
    return jsonify({"ok": True, "message": "Calibration started — press Next Point"})


@app.route("/api/proximity-calibration/next-point", methods=["POST"])
def prox_cal_next_point():
    """Move plotter to next reference position and wait for hover alignment."""
    global _prox_cal_step, _prox_hover_buffer
    with _prox_cal_lock:
        if not _prox_cal_active:
            return jsonify({"error": "Calibration not active"}), 400
        if _prox_cal_step >= 3:
            return jsonify({"error": "All points already positioned"}), 400

        _prox_cal_step += 1
        ref = _PROX_REF_POSITIONS[_prox_cal_step - 1]
        _prox_hover_buffer.clear()

    # Move plotter to reference position
    try:
        serial.send_command("G90")
        serial.send_command(f"G0 X{ref['hx']:.1f} Y{ref['hy']:.1f} Z{config.SAFE_Z} F3000")
    except Exception as e:
        return jsonify({"error": _safe_error(e, "Move failed")}), 500

    print(f"  PROX CAL: moved to point {_prox_cal_step}/3 ({ref['label']})", flush=True)
    _broadcast_event("calibration", {
        "action": "move",
        "step": _prox_cal_step,
        "total": 3,
        "hotend_x": ref["hx"],
        "hotend_y": ref["hy"],
        "label": ref["label"],
    })

    return jsonify({
        "ok": True,
        "step": _prox_cal_step,
        "total": 3,
        "hotend_x": ref["hx"],
        "hotend_y": ref["hy"],
        "label": ref["label"],
    })


@app.route("/api/proximity-calibration/capture", methods=["POST"])
def prox_cal_capture():
    """Capture current hover reading as calibration reference point (UI trigger)."""
    return _prox_cal_capture_point()


@app.route("/api/proximity-calibration/finish", methods=["POST"])
def prox_cal_finish_endpoint():
    """Finish calibration early (normally auto-finished after 3 points)."""
    if not _prox_cal_active:
        return jsonify({"error": "Calibration not active"}), 400
    if len(_prox_cal_ref_points) < 3:
        return jsonify({"error": f"Need 3 points, have {len(_prox_cal_ref_points)}"}), 400
    return _prox_cal_finish()


@app.route("/api/proximity-calibration/cancel", methods=["POST"])
def prox_cal_cancel():
    """Cancel calibration and park plotter."""
    global _prox_cal_active, _prox_cal_step, _prox_cal_ref_points
    _prox_cal_active = False
    _prox_cal_step = 0
    _prox_cal_ref_points = []
    _prox_hover_buffer.clear()
    try:
        serial.send_command(f"G0 Z{config.SAFE_Z} F3000")
        serial.send_command("G0 X0 Y0 F3000")
    except Exception:
        pass
    print("  PROX CAL: cancelled", flush=True)
    _broadcast_event("calibration", {"action": "cancelled"})
    return jsonify({"ok": True, "message": "Calibration cancelled"})


@app.route("/api/ink/jog-start", methods=["POST"])
def ink_jog_start():
    """Enter jog mode: Slate taps move plotter instead of drawing."""
    global _jog_mode_active, _jog_tool, _jog_pen_down
    if not serial.is_connected:
        return jsonify({"error": "Plotter not connected"}), 400
    data = request.json or {}
    _jog_tool = data.get("tool", "pencil")
    _jog_pen_down = False
    _jog_mode_active = True
    print(f"  JOG MODE: started (tool={_jog_tool})", flush=True)
    return jsonify({"ok": True, "message": "Jog mode started"})


@app.route("/api/ink/jog-stop", methods=["POST"])
def ink_jog_stop():
    """Exit jog mode."""
    global _jog_mode_active
    _jog_mode_active = False
    print("  JOG MODE: stopped", flush=True)
    return jsonify({"ok": True, "message": "Jog mode stopped"})


@app.route("/api/ink/jog", methods=["POST"])
def ink_jog():
    """Move plotter to Slate-tapped position. Button toggles pen up/down."""
    global _jog_pen_down
    data = request.json or {}
    points = data.get("points", [])
    if not _jog_mode_active or not serial.is_connected:
        return jsonify({"status": "ok"})

    # Button toggle: pen up/down
    if data.get("type") == "button":
        _jog_pen_down = not _jog_pen_down
        state_str = "down" if _jog_pen_down else "up"
        print(f"  JOG: pen {state_str}", flush=True)
        return jsonify({"status": "ok", "pen_down": _jog_pen_down})

    # Move to tapped position
    if not points:
        return jsonify({"status": "ok"})
    pt = points[-1]  # use last point in batch
    if len(pt) < 2:
        return jsonify({"status": "ok"})

    page = config.load_page_size()
    pw, ph = page["width"], page["height"]
    mx, my = _wacom_to_bed(pt[0], pt[1], pw, ph)
    pressure = pt[2] if len(pt) >= 3 else 2048
    speed = _pressure_to_speed(pressure, 1500)

    try:
        profile = config.load_profile(_jog_tool)
    except FileNotFoundError:
        return jsonify({"error": f"Profile '{_jog_tool}' not found"}), 404

    cal = config.load_calibration()
    pen_off_x = cal.get("offset_x", 0)
    pen_off_y = cal.get("offset_y", 0)
    hx = mx - pen_off_x
    hy = my - pen_off_y

    if _jog_pen_down:
        serial.send_command(f"G1 X{hx:.3f} Y{hy:.3f} Z{profile.height.pen_down_z:.3f} F{speed:.0f}")
    else:
        serial.send_command(f"G0 X{hx:.3f} Y{hy:.3f} Z{config.SAFE_Z} F{profile.movement.travel_speed:.0f}")

    _broadcast_event("jog", {"x": mx, "y": my, "pen_down": _jog_pen_down})
    return jsonify({"status": "ok"})


@app.route("/api/ink/capture", methods=["POST"])
def ink_capture():
    """Spawn capture.py as subprocess to stream live BLE pen data."""
    global _slate_process, _ink_strokes
    if _slate_process and _slate_process.poll() is None:
        return jsonify({"error": "Capture already running"}), 400
    with _live_lock:
        _ink_strokes.clear()
    _slate_process = subprocess.Popen(
        ["python", "capture.py", "--api", "http://localhost:5000"],
        cwd=PENZ_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
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

    with _live_lock:
        ink_snapshot = list(_ink_strokes)
        _ink_strokes.clear()
    stroke_count = sum(len(s) for s in ink_snapshot)
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

    for batch in ink_snapshot:
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
    with _stores_lock:
        uploaded_svgs[file_id] = str(svg_path)
    _slate_process = None

    return jsonify({"id": file_id, "stroke_count": stroke_count})


@app.route("/api/ink/status", methods=["GET"])
def ink_status():
    """Return current capture status."""
    capturing = _slate_process is not None and _slate_process.poll() is None
    pid = _slate_process.pid if _slate_process else None
    # Include heartbeat-based capture status
    now = time.time()
    cs = dict(_capture_status)
    if now - cs["last_heartbeat"] > 15:
        cs["connected"] = False
        cs["live_mode"] = False
        cs["pen_down"] = False
    return jsonify({"capturing": capturing, "pid": pid, "capture": cs})


@app.route("/api/ink/sync", methods=["POST"])
def ink_sync():
    """Spawn sync.py to download stored pages from Wacom device."""
    proc = subprocess.Popen(
        ["python", "sync.py"],
        cwd=PENZ_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
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

    # Prevent path traversal: resolve and verify it stays within pages dir
    pages_dir = Path(PENZ_DIR) / "data" / "pages"
    src = (pages_dir / filename).resolve()
    if not str(src).startswith(str(pages_dir.resolve())):
        return jsonify({"error": "Invalid path"}), 403
    if not src.is_file():
        return jsonify({"error": "Page not found"}), 404

    file_id = uuid.uuid4().hex[:8]
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    dst = config.OUTPUT_DIR / f"ink_{file_id}.svg"
    shutil.copy2(src, str(dst))

    with _stores_lock:
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

@app.route("/api/print-raw", methods=["POST"])
def print_raw():
    """Send raw G-code string directly to the printer."""
    data = request.json or {}
    raw = data.get("gcode", "")
    if not raw:
        return jsonify({"error": "gcode required"}), 400
    if not _ensure_connected():
        return jsonify({"error": "Printer not connected"}), 400
    # Validate each line for safety
    BED_MAX = 220
    Z_MIN, Z_MAX = -1, 50
    blocked_prefixes = ("M104", "M106", "M140", "M303", "M999", "M503", "M500")
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith(";"):
            continue
        cmd = line.split(";")[0].strip().upper()
        if any(cmd.startswith(b) for b in blocked_prefixes):
            return jsonify({"error": f"Blocked command: {cmd.split()[0]}"}), 400
        # Check coordinate bounds
        for part in cmd.split():
            try:
                if part.startswith("X"):
                    val = float(part[1:])
                    if val < 0 or val > BED_MAX:
                        return jsonify({"error": f"X{val} out of bounds (0-{BED_MAX})"}), 400
                elif part.startswith("Y"):
                    val = float(part[1:])
                    if val < 0 or val > BED_MAX:
                        return jsonify({"error": f"Y{val} out of bounds (0-{BED_MAX})"}), 400
                elif part.startswith("Z"):
                    val = float(part[1:])
                    if val < Z_MIN or val > Z_MAX:
                        return jsonify({"error": f"Z{val} out of bounds ({Z_MIN}-{Z_MAX})"}), 400
            except ValueError:
                continue
    try:
        serial.send_gcode_file(raw, filename="raw", progress_callback=_broadcast_progress)
        return jsonify({"ok": True, "message": "Printing raw G-code"})
    except Exception as e:
        return jsonify({"error": _safe_error(e, "Operation")}), 500


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
        return jsonify({"error": _safe_error(e, "Operation")}), 500


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
        elif axis == "Z":
            pos = serial.get_position()
            current_z = float(pos.get("Z", 0))
            current_x = float(pos.get("X", 0))
            target_z = current_z + distance
            if target_z < 0:
                return jsonify({"error": "Z would go below 0"}), 400
            # Cup at X=0 — if X < 30, Z must stay at SAFE_Z or above
            if current_x < 30 and target_z < config.SAFE_Z:
                return jsonify({"error": f"Z must stay ≥ {config.SAFE_Z} when X < 30 (cup clearance)"}), 400
        serial.send_command(f"G91 ; Relative positioning")
        serial.send_command(f"G1 {axis}{distance:.3f} F{speed}")
        serial.send_command(f"G90 ; Absolute positioning")
        serial.send_command("M400 ; Wait for moves to complete")
        pos = serial.get_position()
        return jsonify({"ok": True, "position": pos})
    except Exception as e:
        return jsonify({"error": _safe_error(e, "Operation")}), 500


@app.route("/api/home", methods=["POST"])
def home():
    """Park at water cup position (origin) instead of full endstop home."""
    if not serial.is_connected:
        return jsonify({"error": "Printer not connected"}), 400

    def _do_home():
        try:
            _raise_to_safe()
            time.sleep(1.5)
            serial.send_command("G28")
            time.sleep(12)
            _raise_to_safe()
            time.sleep(1.5)
            serial.send_command("G1 X0.000 Y0.000 F3000")
            time.sleep(3)
            serial.send_command(f"G1 Z{config.SAFE_Z:.3f} F300")
            time.sleep(1.5)
            serial.get_position()
        except Exception:
            pass

    _run_in_thread(_do_home)
    return jsonify({"ok": True, "message": "Homing started"})


# ── Calibration ──────────────────────────────────────────────────────

@app.route("/api/calibration/start", methods=["POST"])
def calibration_start():
    """Move to calibration position: home, then Z20 X110 Y110 for pen loading."""
    if not serial.is_connected:
        return jsonify({"error": "Printer not connected"}), 400

    def _do_cal():
        try:
            _raise_to_safe()
            time.sleep(1.5)
            serial.send_command("G28 ; Home all axes")
            time.sleep(12)
            _raise_to_safe()
            time.sleep(1)
            serial.send_command("G1 X110.000 Y110.000 F3000 ; Move to center")
            serial.send_command("G1 Z20.000 F500 ; Lower to pen-load height")
        except Exception:
            pass

    _run_in_thread(_do_cal)
    return jsonify({"ok": True, "message": "Calibration move started"})


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
        return jsonify({"error": _safe_error(e, "Operation")}), 500


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
    _raise_to_safe()
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
        return jsonify({"error": _safe_error(e, "Operation")}), 500


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
        return jsonify({"error": _safe_error(e, "Operation")}), 500


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

    # Pen offset: hotend = bed_pos - offset (same as gcode.py transform)
    pen_dx = ht.offset_x
    pen_dy = ht.offset_y

    # Effective pen area (same math as gcode.py)
    eff_min_x = max(0, pen_dx)
    eff_max_x = min(config.PRINTER_BED_X, config.PRINTER_BED_X + pen_dx)
    eff_min_y = max(0, pen_dy)
    eff_max_y = min(config.PRINTER_BED_Y, config.PRINTER_BED_Y + pen_dy)

    # Page corners clamped to effective area (pen can't reach beyond this)
    x0 = max(pox, eff_min_x)
    y0 = max(poy, eff_min_y)
    x1 = min(pox + pw, eff_max_x)
    y1 = min(poy + ph, eff_max_y)

    corners_bed = [
        (x0, y0),         # front-left
        (x1, y0),         # front-right
        (x1, y1),         # back-right
        (x0, y1),         # back-left
    ]

    safe_z = config.SAFE_Z
    pen_down_z = ht.pen_down_z
    arm = 10.0  # mm mark length

    try:
        def _do_mark():
            _raise_to_safe()
            time.sleep(0.5)
            serial.send_command(f"G90 ; Absolute positioning")

            for i, (bx, by) in enumerate(corners_bed):
                hx = round(bx - pen_dx, 3)
                hy = round(by - pen_dy, 3)
                dx = arm if i in (0, 3) else -arm
                dy = arm if i in (0, 1) else -arm

                serial.send_command(f"G0 X{hx:.3f} Y{hy:.3f} F{mv.travel_speed:.0f} ; Corner {i+1}")
                time.sleep(1)
                serial.send_command(f"G1 Z{pen_down_z:.3f} F300 ; Pen down")
                time.sleep(0.5)
                serial.send_command(f"G1 X{hx + dx:.3f} Y{hy:.3f} F500 ; Mark X")
                time.sleep(0.5)
                serial.send_command(f"G1 X{hx + dx:.3f} Y{hy + dy:.3f} F500 ; Mark Y")
                time.sleep(0.5)
                serial.send_command(f"G1 Z{safe_z:.3f} F300 ; Pen up")
                time.sleep(0.5)

            serial.send_command("G0 X0 Y0 F3000 ; Return home")

        _run_in_thread(_do_mark)
        return jsonify({"ok": True, "message": "Marking page"})
    except Exception as e:
        return jsonify({"error": _safe_error(e, "Operation")}), 500


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
            return jsonify({"error": _safe_error(e, "Operation")}), 500

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
            return jsonify({"error": _safe_error(e, "Operation")}), 500

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
    text_polylines.clear()
    wc_context.clear()
    return jsonify({"ok": True, "removed": removed})


# ── Main ─────────────────────────────────────────────────────────────

@app.route("/api/shutdown", methods=["POST"])
def shutdown():
    """Shut down the Flask server. Restricted to localhost only."""
    if request.remote_addr not in ("127.0.0.1", "::1", "localhost"):
        return jsonify({"error": "Forbidden"}), 403
    func = request.environ.get("werkzeug.server.shutdown")
    if func is not None:
        func()
    else:
        os._exit(0)
    return jsonify({"ok": True})


# ── Manga Toolkit ────────────────────────────────────────────────────

@app.route("/api/manga/generate", methods=["POST"])
def manga_generate():
    """Unified manga generation endpoint.

    Dispatches by 'action' field:
      compile-page  — full manga_page.json → all polylines, layered
      panels        — page dims + preset → panel border polylines
      speed-lines   — bounds, origin, count → speed line polylines
      tone          — polygon, style, density → tone fill polylines
      bubble        — position, text, shape → bubble + text polylines
      effect        — name + params → effect polylines
      sfx           — text, size, angle → SFX lettering
      detect-panels — slate stroke data → detected panel bounds
      presets       — list available layout presets
    """
    data = request.get_json(force=True)
    action = data.get("action", "")

    try:
        if action == "compile-page":
            polylines = manga.compile_page(data.get("page", {}))
        elif action == "panels":
            polylines = manga.generate_panels(data)
        elif action == "panels-preset":
            preset = data.get("preset", "4-grid")
            pw = data.get("page_width", 180)
            ph = data.get("page_height", 175)
            bleed = data.get("bleed", 3)
            panels = manga.apply_preset(preset, pw, ph, bleed)
            return jsonify({"ok": True, "panels": panels})
        elif action == "speed-lines":
            polylines = manga.generate_speed_lines(data)
        elif action == "tone":
            polylines = manga.generate_tone(data)
        elif action == "bubble":
            polylines = manga.generate_bubble(data)
        elif action == "effect":
            name = data.get("name", "")
            if name == "impact_burst":
                polylines = manga.generate_impact_burst(data)
            elif name == "rain":
                polylines = manga.generate_rain(data)
            elif name == "emotion":
                polylines = manga.generate_emotion_lines(data)
            else:
                return jsonify({"ok": False, "error": f"Unknown effect: {name}"}), 400
        elif action == "sfx":
            polylines = manga.generate_sfx(data)
        elif action == "detect-panels":
            strokes = data.get("strokes", [])
            pw = data.get("page_width", 180)
            ph = data.get("page_height", 175)
            panels = manga.detect_panels(strokes, pw, ph)
            return jsonify({"ok": True, "panels": panels})
        elif action == "presets":
            return jsonify({"ok": True, "presets": list(manga.PANEL_PRESETS.keys())})
        else:
            return jsonify({"ok": False, "error": f"Unknown action: {action}"}), 400

        # Convert polylines to JSON-serializable format
        pl_data = []
        for pl in polylines:
            pl_data.append({
                "points": pl.points,
                "layer": pl.layer,
                "color": pl.color,
            })

        # Store in uploaded_svgs for the standard convert/print pipeline
        svg_id = f"manga-{uuid.uuid4().hex[:8]}"
        # Save as text polylines (bypass SVG) — store Polyline objects directly
        with _stores_lock:
            text_polylines[svg_id] = polylines
            uploaded_svgs[svg_id] = None  # no SVG file for manga

        return jsonify({
            "ok": True,
            "svg_id": svg_id,
            "polylines": pl_data,
            "stroke_count": len(polylines),
        })

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    _cleanup_output()
    if _ensure_connected():
        print(f"Pen Plotter — http://localhost:5000  (auto-connected)")
    else:
        print("Pen Plotter — http://localhost:5000  (no printer detected)")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True, use_reloader=False)
