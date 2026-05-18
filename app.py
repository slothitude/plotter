"""Pen Plotter — Flask web application."""

import math
import os
import uuid
from pathlib import Path

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
ws_clients: list = []


# ── WebSocket ────────────────────────────────────────────────────────

def _broadcast_progress(completed, total, info):
    """Broadcast progress to all connected WebSocket clients."""
    msg = {
        "type": "progress",
        "completed": completed,
        "total": total,
        "info": info,
    }
    for ws in ws_clients[:]:
        try:
            ws.send_json(msg)
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
    return send_from_directory("static", path)


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
        "busy": serial._sending,
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

    if file_id not in uploaded_svgs:
        return jsonify({"error": "SVG not found — upload first"}), 404

    transform_kwargs = _get_transform_params(data)

    try:
        gc, polylines, toolpath, stats, meta = gcode.svg_to_gcode(
            uploaded_svgs[file_id], tool_name, **transform_kwargs
        )
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

    if pattern == "circle":
        r = size / 2
        cx, cy = 50, 50
        pts = " ".join(f"{cx + r * math.cos(2*math.pi*i/64)},{cy + r * math.sin(2*math.pi*i/64)}" for i in range(65))
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
        page_width = float(data.get("page_width", config.PRINTER_BED_X))
        page_height = float(data.get("page_height", config.PRINTER_BED_Y))
        page_offset_x = float(data.get("page_offset_x", 0))
        page_offset_y = float(data.get("page_offset_y", 0))
        scale = 2.0
        spacing = 2.0
        max_width = (page_width - 20) / 1
        # Bypass SVG — generate polylines directly from Hershey font
        import font
        strokes = font.text_to_strokes(text, x=10, y=40, scale=scale, spacing=spacing, max_width=max_width)
        polylines = [gcode.Polyline(points=s) for s in strokes if len(s) >= 2]

        tool = data.get("tool", "pencil")
        transform_kwargs = _get_transform_params(data)
        profile = config.load_profile(tool)
        gc, toolpath_data, meta = gcode.polylines_to_gcode(polylines, profile, **transform_kwargs)

        # Compute stats
        travel_segs = []
        for seg in toolpath_data:
            if seg["type"] == "travel" and len(seg["points"]) >= 2:
                travel_segs.append((tuple(seg["points"][0]), tuple(seg["points"][-1])))
        preview_polylines = []
        for seg in toolpath_data:
            if seg["type"] == "draw" and len(seg["points"]) >= 2:
                preview_polylines.append(gcode.Polyline(points=[tuple(p) for p in seg["points"]]))
        stats = gcode.compute_stats(preview_polylines, travel_segs, profile.movement.draw_speed, profile.movement.travel_speed)

        preview = [[(round(p[0], 2), round(p[1], 2)) for p in pl.points] for pl in preview_polylines]

        file_id = uuid.uuid4().hex[:8]
        generated_gcode[file_id] = gc
        gcode_path = config.OUTPUT_DIR / f"{file_id}.gcode"
        with open(gcode_path, "w") as f:
            f.write(gc)
        line_count = len([l for l in gc.splitlines() if l.strip() and not l.strip().startswith(";")])
        return jsonify({
            "id": file_id,
            "polylines": preview,
            "stroke_count": len(preview_polylines),
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

    config.save_calibration(tool, pen_down_z, pen_up_z)
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
                "cup_x": profile.water.cup_x,
                "cup_y": profile.water.cup_y,
                "cup_height": profile.water.cup_height,
                "cup_diameter": profile.water.cup_diameter,
                "dip_depth": profile.water.dip_depth,
                "dip_time": profile.water.dip_time,
                "dip_interval": profile.water.dip_interval,
                "scrape_distance": profile.water.scrape_distance,
                "scrape_speed": profile.water.scrape_speed,
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


# ── Main ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Pen Plotter — http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=True, threaded=True)
