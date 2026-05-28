#!/usr/bin/env python3
"""Map Wacom Slate reactive area to printer bed.

Moves the pen across a grid at Z = pen_down + 2mm.
At each position, checks if the Slate sends fresh hover data.
Prints a text heatmap of the detection area.
"""

import requests
import time
import sys

BASE = "http://localhost:5000"
PEN_DOWN_Z = 21.9
HOVER_Z = PEN_DOWN_Z + 2.0  # 2mm above pen-down

def gcode(cmd):
    r = requests.post(f"{BASE}/api/send-command", json={"command": cmd}, timeout=10)
    r.raise_for_status()
    return r.json()

def pos():
    r = requests.get(f"{BASE}/api/status", timeout=5)
    r.raise_for_status()
    return r.json().get("position", {})

def hover():
    r = requests.get(f"{BASE}/api/hover/position", timeout=5)
    r.raise_for_status()
    return r.json()

def move_abs(x, y, z=None, speed=3000):
    """Move to absolute position. If z is None, keep current Z."""
    cmd = f"G1 X{x:.1f} Y{y:.1f}"
    if z is not None:
        cmd += f" Z{z:.1f}"
    cmd += f" F{speed}"
    gcode("G90")
    gcode(cmd)
    gcode("M400")

def check_hover_fresh(wait=1.5):
    """Return True if hover data was updated within `wait` seconds."""
    t_before = time.time()
    time.sleep(wait)
    h = hover()
    t_hover = h.get("hover_time", 0)
    return t_hover > t_before, h

def main():
    print("=" * 60)
    print("Wacom Slate Reactive Area Mapper")
    print("=" * 60)

    # Check connected
    st = requests.get(f"{BASE}/api/status", timeout=5).json()
    if not st.get("connected"):
        print("ERROR: Plotter not connected")
        sys.exit(1)

    cap = hover().get("capture", {})
    if not cap.get("connected"):
        print("ERROR: Capture not running")
        sys.exit(1)

    # Grid config — sweep effective pen area
    # Pen offset: -40, -45 → hotend range: X 40-220, Y 45-220 (pen: X 0-180, Y 0-175)
    # Use hotend coords for moves, label as pen coords
    PEN_OFFSET_X = 40
    PEN_OFFSET_Y = 45

    GRID_STEP = 20   # mm between grid points
    PEN_X_MIN = 0
    PEN_X_MAX = 180
    PEN_Y_MIN = 0
    PEN_Y_MAX = 175

    print(f"\nGrid: pen X {PEN_X_MIN}-{PEN_X_MAX}, Y {PEN_Y_MIN}-{PEN_Y_MAX}, step {GRID_STEP}mm")
    print(f"Pen offset: -{PEN_OFFSET_X}, -{PEN_OFFSET_Y}")
    print(f"Hover Z: {HOVER_Z} (pen_down {PEN_DOWN_Z} + 2mm)")
    print()

    # Home
    print("Homing...")
    gcode("G28")
    time.sleep(15)

    # Raise to safe Z, move to start
    move_abs(PEN_X_MIN + PEN_OFFSET_X, PEN_Y_MIN + PEN_OFFSET_Y, z=35, speed=3000)
    time.sleep(3)

    # Lower to hover height
    move_abs(PEN_X_MIN + PEN_OFFSET_X, PEN_Y_MIN + PEN_OFFSET_Y, z=HOVER_Z, speed=500)
    time.sleep(2)

    p = pos()
    print(f"Start: X={p.get('X')} Y={p.get('Y')} Z={p.get('Z')}")
    print()

    # Build grid
    xs = list(range(PEN_X_MIN, PEN_X_MAX + 1, GRID_STEP))
    ys = list(range(PEN_Y_MIN, PEN_Y_MAX + 1, GRID_STEP))

    print(f"Grid: {len(xs)}x{len(ys)} = {len(xs)*len(ys)} points")
    print()

    # Sweep — Y rows, alternating X direction (boustrophedon)
    grid = {}
    total = len(xs) * len(ys)
    done = 0

    for yi, pen_y in enumerate(ys):
        hotend_y = pen_y + PEN_OFFSET_Y
        if yi % 2 == 0:
            x_range = xs
        else:
            x_range = list(reversed(xs))

        for pen_x in x_range:
            hotend_x = pen_x + PEN_OFFSET_X
            move_abs(hotend_x, hotend_y, speed=3000)
            time.sleep(0.5)  # let move settle

            fresh, h = check_hover_fresh(wait=1.5)
            wx = h["hover"][2] if h.get("hover") else None
            wy = h["hover"][3] if h.get("hover") else None

            grid[(pen_x, pen_y)] = {
                "live": fresh,
                "wacom_x": wx,
                "wacom_y": wy,
            }
            done += 1
            marker = "#" if fresh else "."
            sys.stdout.write(marker)
            sys.stdout.flush()

        # End of row
        print(f"  row Y={pen_y} ({done}/{total})")

    # Park
    move_abs(110, 110, z=35, speed=1500)
    print("\nParked.")

    # Print text heatmap
    print("\n" + "=" * 60)
    print("REACTIVE AREA MAP (pen coords)")
    print("# = hover detected, . = no hover")
    print("=" * 60)

    # Header
    header = "Y\\X"
    for x in xs:
        header += f"{x:>4}"
    print(header)
    print("-" * len(header))

    for pen_y in reversed(ys):  # print top-to-bottom
        row = f"{pen_y:>3}"
        for pen_x in xs:
            cell = grid.get((pen_x, pen_y), {})
            row += "   #" if cell.get("live") else "   ."
        print(row)

    # Print wacom coord map
    print("\n" + "=" * 60)
    print("WACOM COORDS AT EACH POINT")
    print("=" * 60)
    header = "Y\\X"
    for x in xs:
        header += f"  {x:>5}"
    print(header[:80])
    print("-" * 80)

    for pen_y in reversed(ys):
        row = f"{pen_y:>3}"
        for pen_x in xs:
            cell = grid.get((pen_x, pen_y), {})
            if cell.get("wacom_x") is not None and cell.get("live"):
                row += f" {cell['wacom_x']:>5}"
            else:
                row += "    --"
        print(row[:120])

    # Stats
    live_count = sum(1 for v in grid.values() if v["live"])
    total_count = len(grid)
    print(f"\nDetection: {live_count}/{total_count} points ({100*live_count/total_count:.0f}%)")

    # Find bounding box of detected area
    live_x = [k[0] for k, v in grid.items() if v["live"]]
    live_y = [k[1] for k, v in grid.items() if v["live"]]
    if live_x:
        print(f"Detected pen area: X {min(live_x)}-{max(live_x)}mm, Y {min(live_y)}-{max(live_y)}mm")
        print(f"Detected area size: {max(live_x)-min(live_x)}x{max(live_y)-min(live_y)}mm")

    # Wacom range
    wx_vals = [v["wacom_x"] for v in grid.values() if v["live"] and v["wacom_x"] is not None]
    wy_vals = [v["wacom_y"] for v in grid.values() if v["live"] and v["wacom_y"] is not None]
    if wx_vals:
        print(f"Wacom coord range: X {min(wx_vals)}-{max(wx_vals)}, Y {min(wy_vals)}-{max(wy_vals)}")


if __name__ == "__main__":
    main()
