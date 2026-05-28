#!/usr/bin/env python3
"""Map Wacom Slate hover detection range.

Moves the pen up in 5mm Z steps, shifting XY each time.
At each position, checks if NEW hover data arrives from the Slate
(using timestamp, not wacom coord comparison).
Stops when no new hover arrives within the settle window.
"""

import requests
import time
import sys

BASE = "http://localhost:5000"

def get_hover():
    """Get latest hover position + timestamp from Slate."""
    r = requests.get(f"{BASE}/api/hover/position", timeout=5)
    r.raise_for_status()
    return r.json()

def get_position():
    """Get current plotter XYZ position."""
    r = requests.get(f"{BASE}/api/status", timeout=5)
    r.raise_for_status()
    d = r.json()
    return d.get("position", {})

def send_gcode(cmd):
    """Send raw G-code command."""
    r = requests.post(f"{BASE}/api/send-command", json={"command": cmd}, timeout=10)
    r.raise_for_status()
    return r.json()

def main():
    print("=" * 60)
    print("Wacom Slate Hover Range Mapper")
    print("=" * 60)

    # Check plotter is connected
    status = requests.get(f"{BASE}/api/status", timeout=5).json()
    if not status.get("connected"):
        print("ERROR: Plotter not connected")
        sys.exit(1)

    # Check capture is running
    hover_data = get_hover()
    cap = hover_data.get("capture", {})
    if not cap.get("connected"):
        print("ERROR: Capture not running (need capture.py for hover data)")
        sys.exit(1)

    pos = status.get("position", {})
    print(f"\nStarting position: X={pos.get('X','?')} Y={pos.get('Y','?')} Z={pos.get('Z','?')}")
    print(f"Capture live_mode={cap.get('live_mode')}, pen_down={cap.get('pen_down')}")
    print()

    # Configuration
    Z_START = 21.9    # pen touch height
    Z_STEP = 5.0      # go up 5mm each step
    XY_STEP = 5.0     # shift XY each step
    SETTLE_TIME = 5.0  # seconds to wait for hover data after move
    MAX_STEPS = 10     # safety limit

    # Home first
    print("Homing plotter...")
    send_gcode("G28")
    print("  Waiting 15s for homing to complete...")
    time.sleep(15)

    # Move to start position using absolute G-code
    START_X = 100
    START_Y = 100
    print(f"Moving pen to starting position X={START_X} Y={START_Y} Z={Z_START}...")

    # Raise to safe Z for XY travel
    send_gcode("G90")
    send_gcode("G1 Z35 F1500")
    send_gcode("M400")
    time.sleep(2)

    # Move to starting XY
    send_gcode(f"G1 X{START_X} Y{START_Y} F3000")
    send_gcode("M400")
    time.sleep(3)

    # Lower to touch height
    send_gcode(f"G1 Z{Z_START} F500")
    send_gcode("M400")
    time.sleep(2)

    pos = get_position()
    print(f"At start: X={pos.get('X')} Y={pos.get('Y')} Z={pos.get('Z')}")
    print()

    # Read initial hover timestamp
    print("Confirming hover at touch height...")
    time.sleep(2)
    h0 = get_hover()
    t_before = h0.get("hover_time", 0)
    print(f"  Hover time before move: {t_before:.1f}, wacom={h0.get('hover')}")

    # Nudge XY slightly to trigger fresh hover
    send_gcode("G91")
    send_gcode("G1 X2 Y2 F500")
    send_gcode("G90")
    send_gcode("M400")
    time.sleep(3)

    h1 = get_hover()
    t_after = h1.get("hover_time", 0)
    fresh = t_after > t_before
    print(f"  Hover time after nudge: {t_after:.1f}, wacom={h1.get('hover')}")
    print(f"  Fresh data: {fresh}")
    if not fresh:
        print("WARNING: No fresh hover at touch height — capture may not be sending hover data")
        print("Continuing anyway...\n")
    else:
        print(f"  Hover confirmed at Z={Z_START}\n")

    # Test loop
    results = []
    current_z = Z_START
    prev_hover_time = t_after

    for step in range(1, MAX_STEPS + 1):
        target_z = current_z + Z_STEP
        print(f"--- Step {step}: Z={target_z:.1f} ---")

        # Raise Z (relative)
        send_gcode("G91")
        send_gcode(f"G1 Z{Z_STEP} F500")
        send_gcode("G90")
        send_gcode("M400")
        time.sleep(2)

        # Read position and shift XY (absolute)
        pos = get_position()
        cur_z = float(pos.get("Z", 0))
        cur_x = float(pos.get("X", 0))
        cur_y = float(pos.get("Y", 0))
        new_x = cur_x + XY_STEP
        new_y = cur_y + XY_STEP

        send_gcode(f"G1 X{new_x:.1f} Y{new_y:.1f} F1000")
        send_gcode("M400")
        time.sleep(1)

        pos = get_position()
        cur_z = float(pos.get("Z", 0))
        cur_x = float(pos.get("X", 0))
        cur_y = float(pos.get("Y", 0))
        print(f"  Position: X={cur_x:.1f} Y={cur_y:.1f} Z={cur_z:.1f}")

        # Wait for fresh hover data
        print(f"  Waiting {SETTLE_TIME}s for hover data...")
        t_check_before = time.time()
        time.sleep(SETTLE_TIME)

        # Read hover
        h = get_hover()
        t_hover = h.get("hover_time", 0)
        hover = h.get("hover")
        wx = hover[2] if hover else None
        wy = hover[3] if hover else None

        # Fresh = hover timestamp updated after we started waiting
        fresh = t_hover > t_check_before

        if fresh:
            status_str = "LIVE"
        else:
            age = time.time() - t_hover if t_hover > 0 else 999
            status_str = f"STALE (data {age:.0f}s old)"

        result = {
            "step": step,
            "z": cur_z,
            "x": cur_x,
            "y": cur_y,
            "wacom_x": wx,
            "wacom_y": wy,
            "hover_time": t_hover,
            "status": status_str,
            "live": fresh,
        }
        results.append(result)

        print(f"  Wacom: x={wx} y={wy} | hover_time={t_hover:.1f}")
        print(f"  Result: {status_str}")

        if not fresh:
            print(f"\n  >> Hover lost at Z={cur_z:.1f}")
            break

        prev_hover_time = t_hover
        current_z = cur_z

    # Summary
    print("\n" + "=" * 60)
    print("HOVER RANGE RESULTS")
    print("=" * 60)
    print(f"{'Z':>8} | {'Status':>25} | {'Wacom X':>8} | {'Wacom Y':>8}")
    print("-" * 60)

    # Add initial confirmed reading
    print(f"{Z_START:>8.1f} | {'BASELINE (confirmed)':>25} | {'—':>8} | {'—':>8}")

    for r in results:
        wx_str = f"{r['wacom_x']:.0f}" if r['wacom_x'] is not None else "None"
        wy_str = f"{r['wacom_y']:.0f}" if r['wacom_y'] is not None else "None"
        marker = " <-- LOST" if not r['live'] else ""
        print(f"{r['z']:>8.1f} | {r['status']:>25} | {wx_str:>8} | {wy_str:>8}{marker}")

    # Find max working Z
    live_steps = [r for r in results if r['live']]
    if live_steps:
        max_z = max(r['z'] for r in live_steps)
        print(f"\nMax hover detection Z: {max_z:.1f}mm")
        print(f"Hover range: {max_z - Z_START:.1f}mm above pen touch ({Z_START}mm)")
    else:
        print(f"\nHover only works at Z={Z_START} (touch height)")

    # Park at safe height
    print("\nParking pen...")
    send_gcode("G90")
    send_gcode("G1 Z35 F1500")
    print("Done.")


if __name__ == "__main__":
    main()
