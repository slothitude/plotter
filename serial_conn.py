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
        self._position = {}

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
            self._serial = serial.Serial(port, baudrate, timeout=1)
            time.sleep(5)
            self._drain()
            return True
        except serial.SerialException as e:
            self._serial = None
            raise ConnectionError(f"Failed to connect to {port}: {e}")

    def disconnect(self):
        self._stop_requested = True
        self._sending = False
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

    def send_command(self, cmd: str, wait: bool = True) -> str:
        """Send G-code. Fire and forget — no waiting for ok."""
        if not self.is_connected:
            raise ConnectionError("Not connected to printer")
        with self._lock:
            self._serial.write((cmd.strip() + "\n").encode())
            self._serial.flush()
        return ""

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
                        while time.time() < deadline:
                            line = self._serial.readline()
                            if not line:
                                continue
                            decoded = line.decode(errors="ignore").strip()
                            if "ok" in decoded.lower():
                                break
                            # Parse position if firmware sends it
                            if decoded.startswith("X:"):
                                self._parse_position(decoded)
                except Exception:
                    break
                self._completed_commands += 1
                if self._progress_callback:
                    self._progress_callback(self._completed_commands, self._total_commands, self._current_file)
        except Exception as e:
            if self._progress_callback:
                self._progress_callback(-1, self._total_commands, str(e))
        finally:
            self._sending = False

    def stop(self):
        self._stop_requested = True
        self._queue.clear()
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

    @property
    def status(self) -> dict:
        return {
            "connected": self.is_connected,
            "busy": self._sending,
            "current_file": self._current_file,
            "progress": {
                "completed": self._completed_commands,
                "total": self._total_commands,
            } if self._sending else None,
            "position": self._position.copy(),
        }
