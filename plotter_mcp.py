"""Plotter MCP Server — controls the pen plotter web app via its Flask API.

Runs as a stdio MCP server via FastMCP. All calls go to http://localhost:5000.
"""

from typing import Optional

import requests
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("plotter")

BASE_URL = "http://localhost:5000"


def _api(method: str, path: str, **kwargs):
    """Call Flask API and return parsed JSON."""
    url = f"{BASE_URL}{path}"
    r = requests.request(method, url, timeout=30, **kwargs)
    r.raise_for_status()
    try:
        return r.json()
    except Exception:
        return {"status": r.text.strip()}


# ── Core Control ────────────────────────────────────────────────────────────────

@mcp.tool()
def plotter_status() -> dict:
    """Get plotter connection status, XYZ position, and current state."""
    return _api("GET", "/api/status")


@mcp.tool()
def plotter_list_ports() -> dict:
    """List available serial ports for connecting to the plotter."""
    return _api("GET", "/api/ports")


@mcp.tool()
def plotter_connect(port: str) -> dict:
    """Connect to the plotter on a specific serial port.

    Args:
        port: Serial port name (e.g. "COM3", "/dev/ttyUSB0")
    """
    return _api("POST", "/api/serial/connect", json={"port": port})


@mcp.tool()
def plotter_disconnect() -> dict:
    """Disconnect from the plotter."""
    return _api("POST", "/api/serial/disconnect")


@mcp.tool()
def plotter_home() -> dict:
    """Home all axes — move to origin position."""
    return _api("POST", "/api/home")


@mcp.tool()
def plotter_jog(axis: str, distance: float, speed: Optional[float] = None) -> dict:
    """Jog (manual move) an axis by a distance.

    Args:
        axis: Axis to move ("X", "Y", or "Z")
        distance: Distance in mm (negative for opposite direction)
        speed: Optional feed rate in mm/min
    """
    payload = {"axis": axis.upper(), "distance": distance}
    if speed is not None:
        payload["speed"] = speed
    return _api("POST", "/api/jog", json=payload)


@mcp.tool()
def plotter_stop() -> dict:
    """Emergency stop — sends M112 to immediately halt the plotter."""
    return _api("POST", "/api/stop")


@mcp.tool()
def plotter_send_command(command: str) -> dict:
    """Send a raw G-code command to the plotter.

    Args:
        command: G-code command string (e.g. "G28", "M114")
    """
    return _api("POST", "/api/send-command", json={"command": command})


# ── File Pipeline ───────────────────────────────────────────────────────────────

@mcp.tool()
def plotter_upload_svg(file_path: str) -> dict:
    """Upload an SVG file for plotting.

    Args:
        file_path: Absolute path to the SVG file
    """
    with open(file_path, "rb") as f:
        return _api("POST", "/api/upload", files={"file": f})


@mcp.tool()
def plotter_convert(
    file_id: str,
    tool: str = "pencil",
    scale: Optional[float] = None,
    rotate: Optional[float] = None,
    flip_x: Optional[bool] = None,
    flip_y: Optional[bool] = None,
    center: Optional[bool] = None,
    optimize: Optional[bool] = None,
    simplify: Optional[float] = None,
    fill: Optional[bool] = None,
    fill_type: Optional[str] = None,
    fill_spacing: Optional[float] = None,
    fill_angle: Optional[float] = None,
) -> dict:
    """Convert an uploaded SVG to G-code with optional transforms.

    Args:
        file_id: SVG file ID from upload
        tool: Tool profile name (pencil, pen, watercolor)
        scale: Scale factor
        rotate: Rotation angle in degrees
        flip_x: Flip horizontally
        flip_y: Flip vertically
        center: Center on page
        optimize: Optimize tool travel path
        simplify: RDP simplification tolerance (mm)
        fill: Enable fill/hatching
        fill_type: Fill pattern type (e.g. "hatch", "crosshatch")
        fill_spacing: Distance between fill lines (mm)
        fill_angle: Fill line angle in degrees
    """
    payload = {"id": file_id, "tool": tool}
    if scale is not None:
        payload["scale"] = scale
    if rotate is not None:
        payload["rotate"] = rotate
    if flip_x is not None:
        payload["flip_x"] = flip_x
    if flip_y is not None:
        payload["flip_y"] = flip_y
    if center is not None:
        payload["center"] = center
    if optimize is not None:
        payload["optimize"] = optimize
    if simplify is not None:
        payload["simplify"] = simplify
    if fill is not None:
        payload["fill"] = fill
    if fill_type is not None:
        payload["fill_type"] = fill_type
    if fill_spacing is not None:
        payload["fill_spacing"] = fill_spacing
    if fill_angle is not None:
        payload["fill_angle"] = fill_angle
    return _api("POST", "/api/convert", json=payload)


@mcp.tool()
def plotter_test_pattern(
    pattern: str,
    size: float = 40,
    tool: str = "pencil",
    page_width: Optional[float] = None,
    page_height: Optional[float] = None,
) -> dict:
    """Generate a geometric test pattern for the plotter.

    Args:
        pattern: Pattern type (circle, grid, spiral, star, calibration, concentric_circles, diagonal_lines, zigzag)
        size: Pattern size in mm
        tool: Tool profile name (pencil, pen, watercolor)
        page_width: Optional page width override (mm)
        page_height: Optional page height override (mm)
    """
    payload = {"pattern": pattern, "size": size, "tool": tool}
    if page_width is not None:
        payload["page_width"] = page_width
    if page_height is not None:
        payload["page_height"] = page_height
    return _api("POST", "/api/test-pattern", json=payload)


@mcp.tool()
def plotter_trace_image(
    file_path: str,
    method: str = "canny",
    blur: Optional[float] = None,
    threshold_low: Optional[float] = None,
    threshold_high: Optional[float] = None,
    invert: Optional[bool] = None,
) -> dict:
    """Convert a raster image to SVG via edge detection for plotting.

    Args:
        file_path: Absolute path to the image file (PNG, JPG, BMP)
        method: Edge detection method ("canny" or "threshold")
        blur: Gaussian blur sigma (reduces noise)
        threshold_low: Lower Canny threshold
        threshold_high: Upper Canny threshold
        invert: Invert the detected edges
    """
    with open(file_path, "rb") as f:
        payload = {"method": method}
        if blur is not None:
            payload["blur"] = str(blur)
        if threshold_low is not None:
            payload["threshold_low"] = str(threshold_low)
        if threshold_high is not None:
            payload["threshold_high"] = str(threshold_high)
        if invert is not None:
            payload["invert"] = str(invert).lower()
        return _api("POST", "/api/trace", files={"file": f}, data=payload)


@mcp.tool()
def plotter_print(file_id: str) -> dict:
    """Start printing a G-code file on the plotter.

    Args:
        file_id: G-code file ID from convert/test-pattern
    """
    return _api("POST", "/api/print", json={"id": file_id})


@mcp.tool()
def plotter_download_gcode(file_id: str) -> dict:
    """Download G-code content for a file.

    Args:
        file_id: G-code file ID
    """
    r = requests.get(f"{BASE_URL}/api/download/{file_id}", timeout=30)
    r.raise_for_status()
    return {"gcode": r.text}


@mcp.tool()
def plotter_convert_pass2(file_id: str, tool: str = "watercolor") -> dict:
    """Regenerate watercolor pass 2 G-code with current calibration values.

    Call this after recalibrating the brush Z to regenerate pass 2 with updated heights.

    Args:
        file_id: SVG file ID from a previous watercolor convert
        tool: Tool profile name (default: watercolor)
    """
    return _api("POST", "/api/convert-pass2", json={"id": file_id, "tool": tool})


@mcp.tool()
def plotter_print_raw(gcode: str) -> dict:
    """Send raw G-code string directly to the printer for immediate execution.

    Args:
        gcode: G-code string to send (newline-separated commands)
    """
    return _api("POST", "/api/print-raw", json={"gcode": gcode})


# ── Calibration ─────────────────────────────────────────────────────────────────


@mcp.tool()
def plotter_calibration_start() -> dict:
    """Move to calibration position: home, then move to center (X110 Y110 Z20) for pen loading."""
    return _api("POST", "/api/calibration/start")


@mcp.tool()
def plotter_calibration_pen_loaded() -> dict:
    """Raise Z by 5mm after loading the pen/brush into the holder."""
    return _api("POST", "/api/calibration/pen-loaded")


@mcp.tool()
def plotter_calibration_step(distance: float = -0.1) -> dict:
    """Jog Z in fine steps during calibration. Returns current position.

    Args:
        distance: Z distance in mm (negative = down, default -0.1)
    """
    return _api("POST", "/api/calibration/step", json={"distance": distance})


@mcp.tool()
def plotter_calibration_test_dot(tool: str = "pencil") -> dict:
    """Test pen contact: lower pen, dwell 1 second, raise. Used during Z calibration.

    Args:
        tool: Tool profile name for Z heights (pencil, pen, watercolor)
    """
    return _api("POST", "/api/calibration/test-dot", json={"tool": tool})


# ── Page & Bed ──────────────────────────────────────────────────────────────────


@mcp.tool()
def plotter_mark_page(tool: str = "pencil") -> dict:
    """Draw L-marks at the 4 corners of the page for alignment verification.

    Args:
        tool: Tool profile name for Z heights
    """
    return _api("POST", "/api/mark-page", json={"tool": tool})


@mcp.tool()
def plotter_bed_level(action: str = "start", tool: str = "pencil") -> dict:
    """Bed leveling helper — move to corners to check bed level.

    Args:
        action: "start" to begin, "next" for next corner
        tool: Tool profile name for Z heights
    """
    return _api("POST", "/api/bed-level", json={"action": action, "tool": tool})


@mcp.tool()
def plotter_tool_change_park(action: str = "goto", tool: str = "watercolor") -> dict:
    """Move to tool change park position or save current position as park.

    Args:
        action: "goto" to move to park position, "save" to set current position as park
        tool: Tool profile name (uses pass2 change_x/y/z for park position)
    """
    return _api("POST", "/api/tool-change-park", json={"action": action, "tool": tool})


# ── Live Plot / Capture ─────────────────────────────────────────────────────────

@mcp.tool()
def plotter_live_start(tool: str = "pencil") -> dict:
    """Start live plot mode — strokes from Wacom Slate go directly to plotter.

    Args:
        tool: Tool profile for live plotting (pencil, pen, watercolor)
    """
    return _api("POST", "/api/ink/live-start", json={"tool": tool})


@mcp.tool()
def plotter_live_stop() -> dict:
    """Stop live plot mode."""
    return _api("POST", "/api/ink/live-stop")


@mcp.tool()
def plotter_start_capture() -> dict:
    """Start Wacom Slate BLE capture — records strokes from the slate."""
    return _api("POST", "/api/ink/capture")


@mcp.tool()
def plotter_stop_capture() -> dict:
    """Stop Wacom Slate BLE capture and save captured strokes as SVG."""
    return _api("POST", "/api/ink/stop")


@mcp.tool()
def plotter_ocr(file_id: str) -> dict:
    """Transcribe handwriting from an ink capture SVG using vision LLM.

    Args:
        file_id: SVG file ID from capture
    """
    return _api("POST", "/api/ink/ocr", json={"id": file_id})


@mcp.tool()
def plotter_ink_status() -> dict:
    """Check Wacom Slate capture status — is capture running, stroke count."""
    return _api("GET", "/api/ink/status")


@mcp.tool()
def plotter_ink_sync() -> dict:
    """Download stored pages from Wacom device via BLE."""
    return _api("POST", "/api/ink/sync")


@mcp.tool()
def plotter_ink_pages() -> dict:
    """List stored pages downloaded from Wacom device."""
    return _api("GET", "/api/ink/pages")


# ── Settings ────────────────────────────────────────────────────────────────────

@mcp.tool()
def plotter_get_settings(tool: str) -> dict:
    """Get tool profile settings.

    Args:
        tool: Tool profile name (pencil, pen, watercolor)
    """
    return _api("GET", f"/api/settings/{tool}")


@mcp.tool()
def plotter_list_profiles() -> dict:
    """List available tool profiles."""
    return _api("GET", "/api/profiles")


@mcp.tool()
def plotter_update_settings(tool: str, settings: dict) -> dict:
    """Update tool profile settings (movement, water, fill parameters).

    Args:
        tool: Tool profile name (pencil, pen, watercolor)
        settings: Dict with sections to update, e.g. {"movement": {"draw_speed": 1000}, "water": {"enabled": true}}
    """
    return _api("POST", f"/api/settings/{tool}", json=settings)


@mcp.tool()
def plotter_cleanup() -> dict:
    """Delete all generated files in output/ and clear in-memory stores."""
    return _api("POST", "/api/cleanup")


@mcp.tool()
def plotter_get_calibration() -> dict:
    """Get current calibration data (Z heights, XY offsets) for all tools."""
    return _api("GET", "/api/calibration")


@mcp.tool()
def plotter_save_calibration(
    tool: str,
    pen_down_z: float,
    pen_up_z: float,
) -> dict:
    """Save Z height calibration for a tool.

    Args:
        tool: Tool profile name
        pen_down_z: Z height when pen is down (drawing)
        pen_up_z: Z height when pen is up (traveling)
    """
    return _api("POST", "/api/calibration/save", json={
        "tool": tool,
        "pen_down_z": pen_down_z,
        "pen_up_z": pen_up_z,
    })


@mcp.tool()
def plotter_save_offset(
    tool: str,
    offset_x: float,
    offset_y: float,
) -> dict:
    """Save XY offset calibration for a tool.

    Args:
        tool: Tool profile name
        offset_x: X offset in mm
        offset_y: Y offset in mm
    """
    return _api("POST", "/api/calibration/offset", json={
        "tool": tool,
        "offset_x": offset_x,
        "offset_y": offset_y,
    })


@mcp.tool()
def plotter_get_page_size() -> dict:
    """Get current page size configuration."""
    return _api("GET", "/api/page-size")


@mcp.tool()
def plotter_set_page_size(
    width: Optional[float] = None,
    height: Optional[float] = None,
    preset: Optional[str] = None,
) -> dict:
    """Set page size for plotting.

    Args:
        width: Page width in mm
        height: Page height in mm
        preset: Named preset (e.g. "A4", "A5", "Letter")
    """
    payload = {}
    if width is not None:
        payload["width"] = width
    if height is not None:
        payload["height"] = height
    if preset is not None:
        payload["preset"] = preset
    return _api("POST", "/api/page-size", json=payload)


if __name__ == "__main__":
    mcp.run(transport="stdio")
