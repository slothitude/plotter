"""Pen Plotter — Flask web application."""

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


# ── Upload & Convert ────────────────────────────────────────────────

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

    page_width = float(data.get("page_width", config.PRINTER_BED_X))
    page_height = float(data.get("page_height", config.PRINTER_BED_Y))
    page_offset_x = float(data.get("page_offset_x", 0))
    page_offset_y = float(data.get("page_offset_y", 0))

    try:
        gc, polylines = gcode.svg_to_gcode(uploaded_svgs[file_id], tool_name)
        # Regenerate G-code with page dimensions
        profile = config.load_profile(tool_name)
        gc = gcode.polylines_to_gcode(polylines, profile, bed_x=page_width, bed_y=page_height,
                                      page_offset_x=page_offset_x, page_offset_y=page_offset_y)
        generated_gcode[file_id] = gc

        # Save G-code file
        gcode_path = config.OUTPUT_DIR / f"{file_id}.gcode"
        with open(gcode_path, "w") as f:
            f.write(gc)

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
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/test-pattern", methods=["POST"])
def test_pattern():
    """Generate a test SVG pattern."""
    import math
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

    elif pattern == "text":
        text = data.get("text", "HELLO")
        page_width = float(data.get("page_width", config.PRINTER_BED_X))
        page_height = float(data.get("page_height", config.PRINTER_BED_Y))
        page_offset_x = float(data.get("page_offset_x", 0))
        page_offset_y = float(data.get("page_offset_y", 0))
        scale = 2.0
        spacing = 2.0
        # Calculate max_width: page width minus 20mm margin, converted to font units
        max_width = (page_width - 20) / 1  # already in mm = scaled units
        # Bypass SVG — generate polylines directly from Hershey font
        import font
        strokes = font.text_to_strokes(text, x=10, y=40, scale=scale, spacing=spacing, max_width=max_width)
        polylines = [gcode.Polyline(points=s) for s in strokes if len(s) >= 2]
        preview = [[(round(p[0], 2), round(p[1], 2)) for p in pl.points] for pl in polylines]
        # Generate G-code directly from polylines
        tool = data.get("tool", "pencil")
        profile = config.load_profile(tool)
        gc = gcode.polylines_to_gcode(polylines, profile, bed_x=page_width, bed_y=page_height,
                                      page_offset_x=page_offset_x, page_offset_y=page_offset_y)
        file_id = uuid.uuid4().hex[:8]
        generated_gcode[file_id] = gc
        gcode_path = config.OUTPUT_DIR / f"{file_id}.gcode"
        with open(gcode_path, "w") as f:
            f.write(gc)
        line_count = len([l for l in gc.splitlines() if l.strip() and not l.strip().startswith(";")])
        return jsonify({
            "id": file_id,
            "polylines": preview,
            "stroke_count": len(polylines),
            "has_gcode": True,
            "gcode_preview": gc[:2000],
            "gcode_file": f"/api/download/{file_id}",
            "line_count": line_count,
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

    # Parse for preview
    polylines = gcode.parse_svg(str(out_path))
    preview = [[(round(p[0], 2), round(p[1], 2)) for p in pl.points] for pl in polylines]
    return jsonify({"id": file_id, "polylines": preview, "stroke_count": len(polylines)})


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

    if not serial.is_connected:
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
        serial.send_command(f"G91 ; Relative positioning")
        serial.send_command(f"G1 {axis}{distance:.3f} F{speed}")
        serial.send_command(f"G90 ; Absolute positioning")
        pos = serial.get_position()
        return jsonify({"ok": True, "position": pos})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/home", methods=["POST"])
def home():
    if not serial.is_connected:
        return jsonify({"error": "Printer not connected"}), 400
    try:
        serial.send_command("G28")
        pos = serial.get_position()
        return jsonify({"ok": True, "position": pos})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Calibration ──────────────────────────────────────────────────────

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
                "blot_x": profile.water.blot_x,
                "blot_y": profile.water.blot_y,
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
