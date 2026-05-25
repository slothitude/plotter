"""Pen Plotter — PySerial wrapper for printer communication."""

import threading
import time
from collections import deque
from typing import Optional, Callable

import serial
import serial.tools.list_ports


class SerialConnection:

    def __init__(self):
        self._lock = threading.Lock()
        self._serial: Optional[serial.Serial] = None
        self._queue: deque[str] = deque()
        self._sending = False
        self._sender_thread: Optional[threading.Thread] = None
        self._progress_callback: Optional[Callable] = None
        self._total_commands = 0
        self._completed_commands = 0
        self._stop_requested = False
        self._current_file: Optional[str] = None
        self._last_port: Optional[str] = None
        self._position = {}
        # Live plot mode
        self._stroke_queue: deque[str] = deque()
        self._live_sending = False
        self._live_thread: Optional[threading.Thread] = None

    # ── Connection ──────────────────────────────────────────────────

    @staticmethod
    def list_ports() -> list[dict]:
        ports = []
        for p in serial.tools.list_ports.comports():
            ports.append({"port": p.device, "description": p.description, "hwid": p.hwid})
        return ports

    def connect(self, port: str, baudrate: int = 250000) -> bool:
        if self._serial and self._serial.is_open:
            self.disconnect()
        try:
            self._serial = serial.Serial(port, baudrate, timeout=1, write_timeout=5)
            self._last_port = port
            time.sleep(5)
            self._drain()
            return True
        except serial.SerialException as e:
            self._serial = None
            raise ConnectionError(f"Failed to connect to {port}: {e}")

    def disconnect(self):
        self._stop_requested = True
        self._sending = False
        self._live_sending = False
        # Wait for threads to finish before closing serial
        if self._sender_thread and self._sender_thread.is_alive():
            self._sender_thread.join(timeout=3)
        if self._live_thread and self._live_thread.is_alive():
            self._live_thread.join(timeout=3)
        if self._serial and self._serial.is_open:
            try:
                self._serial.close()
            except Exception:
                pass
        self._serial = None

    @property
    def is_connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    # ── Low-level ───────────────────────────────────────────────────

    def _drain(self):
        """Throw away everything in the receive buffer."""
        try:
            if self._serial and self._serial.is_open and self._serial.in_waiting:
                self._serial.read(self._serial.in_waiting)
        except Exception:
            pass

    def _parse_position(self, line: str) -> bool:
        """Parse X:0.00 Y:0.00 Z:0.00 E:0.00 from M114 response."""
        if not line.startswith("X:"):
            return False
        # M114 outputs: X:n Y:n Z:n E:n Count X:n Y:n Z:n
        # Only parse the position part before "Count"
        pos_part = line.split("Count")[0]
        pos = {}
        for part in pos_part.split():
            if ":" in part:
                axis, val = part.split(":", 1)
                axis = axis.upper()
                if axis in ("X", "Y", "Z", "E") and val:
                    try:
                        pos[axis] = float(val)
                    except ValueError:
                        pass
        if ("X" in pos and "Y" in pos and
            -300 < pos.get("X", 0) < 300 and
            -300 < pos.get("Y", 0) < 300 and
            -50 < pos.get("Z", 0) < 300):
            self._position = pos
            return True
        return False

    def send_command(self, cmd: str) -> str:
        """Send a raw G-code command (fire and forget, no ack wait)."""
        if not self.is_connected:
            raise ConnectionError("Not connected to printer")
        with self._lock:
            self._serial.write((cmd.strip() + "\n").encode())
            self._serial.flush()
        return ""

    def _send_direct(self, cmd: str):
        """Send a command using the existing lock (for use after _send_loop)."""
        with self._lock:
            self._serial.write((cmd.strip() + "\n").encode())
            self._serial.flush()

    def get_position(self) -> dict:
        """Send M114, retry up to 3 times if firmware doesn't flush."""
        if not self.is_connected:
            return {}
        with self._lock:
            for _ in range(3):
                try:
                    self._drain()
                    self._position = {}
                    self._serial.write(b"M114\nM400\n")
                    self._serial.flush()
                    deadline = time.time() + 2
                    while time.time() < deadline:
                        line = self._serial.readline()
                        if not line:
                            break
                        if self._parse_position(line.decode(errors="ignore").strip()):
                            # Consume remaining response lines
                            while time.time() < deadline:
                                if not self._serial.readline():
                                    break
                            return self._position.copy()
                except Exception:
                    pass
            return self._position.copy()

    # ── Queue / Streaming ───────────────────────────────────────────

    def send_gcode_file(self, gcode: str, filename: str = "plot",
                        progress_callback: Optional[Callable] = None):
        if self._sending:
            raise RuntimeError("Already sending. Stop current job first.")
        if self._live_sending:
            raise RuntimeError("Live plot mode active. Stop it first.")
        lines = [l.strip() for l in gcode.splitlines() if l.strip() and not l.strip().startswith(";")]
        self._queue = deque(lines)
        self._total_commands = len(lines)
        self._completed_commands = 0
        self._stop_requested = False
        self._current_file = filename
        self._progress_callback = progress_callback
        self._sending = True
        self._sender_thread = threading.Thread(target=self._send_loop, daemon=True)
        self._sender_thread.start()

    def _send_loop(self):
        try:
            while self._queue and not self._stop_requested:
                cmd = self._queue.popleft()
                if cmd.startswith(";") or not cmd:
                    self._completed_commands += 1
                    continue
                try:
                    with self._lock:
                        self._serial.write((cmd + "\n").encode())
                        self._serial.flush()
                        # Wait for "ok" acknowledgment from firmware
                        deadline = time.time() + 10
                        got_ok = False
                        while time.time() < deadline:
                            line = self._serial.readline()
                            if not line:
                                continue
                            decoded = line.decode(errors="ignore").strip()
                            if "ok" in decoded.lower():
                                got_ok = True
                                break
                            # Parse position if firmware sends it
                            if decoded.startswith("X:"):
                                self._parse_position(decoded)
                        if not got_ok:
                            print(f"  ACK TIMEOUT after cmd: {cmd[:60]}", flush=True)
                except Exception as _e:
                    print(f"  SERIAL ERROR in _send_loop: {_e}", flush=True)
                    break
                self._completed_commands += 1
                if self._progress_callback:
                    self._progress_callback(self._completed_commands, self._total_commands, self._current_file)
        except Exception as e:
            if self._progress_callback:
                self._progress_callback(-1, self._total_commands, str(e))
        finally:
            self._sending = False
            # Park after plot finishes or is stopped
            if not self._stop_requested:
                try:
                    if not self._serial or not self._serial.is_open:
                        print("  Serial lost — attempting reconnect for park...", flush=True)
                        self.connect(self._last_port)
                    self._send_direct("G90")
                    self._send_direct(f"G1 Z{20:.3f} F3000")
                    self._send_direct("G0 X0.000 Y0.000 F3000")
                    self._send_direct("M84")
                except Exception as _e:
                    print(f"  Park failed: {_e}", flush=True)

    def stop(self):
        self._stop_requested = True
        self._queue.clear()
        self._stroke_queue.clear()
        self._live_sending = False
        if self.is_connected:
            try:
                with self._lock:
                    self._serial.write(b"M112\n")
                    self._serial.flush()
                    time.sleep(0.5)
                    self._serial.write(b"M999\n")
                    self._serial.flush()
            except Exception:
                pass
        self._sending = False

    # ── Live Plot Mode ──────────────────────────────────────────────────

    def start_live_mode(self, safe_z: float):
        """Enter live plotting mode: home, raise to safe Z, start sender thread."""
        if self._sending:
            raise RuntimeError("Already sending a file. Stop it first.")
        if self._live_sending:
            return  # already in live mode
        self._stroke_queue.clear()
        self._stop_requested = False
        # Home and prepare
        with self._lock:
            self._serial.write(b"G28\n")
            self._serial.flush()
            self._drain()
            time.sleep(1)
            self._serial.write(b"G90\n")
            self._serial.flush()
            self._serial.write(f"G1 Z{safe_z:.3f} F3000\n".encode())
            self._serial.flush()
            self._drain()
        self._live_sending = True
        self._live_thread = threading.Thread(target=self._live_send_loop, daemon=True)
        self._live_thread.start()

    def stop_live_mode(self, safe_z: float):
        """Exit live mode: park plotter and stop sender thread."""
        self._stop_requested = True
        self._live_sending = False
        if self._live_thread:
            self._live_thread.join(timeout=5)
            self._live_thread = None
        if self.is_connected:
            try:
                with self._lock:
                    self._serial.write(f"G1 Z{safe_z:.3f} F3000\n".encode())
                    self._serial.flush()
                    self._serial.write(b"G28\n")
                    self._serial.flush()
            except Exception:
                pass
        self._stroke_queue.clear()
        self._stop_requested = False

    def queue_stroke(self, gcode_lines: list[str]):
        """Queue a complete stroke's G-code for live sending."""
        self._stroke_queue.extend(gcode_lines)

    def _live_send_loop(self):
        """Send queued G-code lines one at a time, waiting for 'ok' ack."""
        idle_since = time.time()
        while not self._stop_requested and self._live_sending:
            if not self._stroke_queue:
                if time.time() - idle_since > 300:  # 5min idle timeout
                    break
                time.sleep(0.05)
                continue
            idle_since = time.time()
            cmd = self._stroke_queue.popleft()
            if not cmd or cmd.startswith(";"):
                continue
            try:
                with self._lock:
                    self._serial.write((cmd + "\n").encode())
                    self._serial.flush()
                    deadline = time.time() + 10
                    while time.time() < deadline:
                        line = self._serial.readline()
                        if not line:
                            continue
                        decoded = line.decode(errors="ignore").strip()
                        if "ok" in decoded.lower():
                            break
                        if decoded.startswith("X:"):
                            self._parse_position(decoded)
            except Exception:
                break
        self._live_sending = False

    @property
    def status(self) -> dict:
        return {
            "connected": self.is_connected,
            "busy": self._sending or self._live_sending,
            "live_plot": self._live_sending,
            "current_file": self._current_file,
            "progress": {
                "completed": self._completed_commands,
                "total": self._total_commands,
            } if self._sending else None,
            "position": self._position.copy(),
        }
