# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running

```bash
pip install -r requirements.txt
python app.py          # Flask on :5000
```

No build step, no tests, no linting. Single-process Flask app with WebSocket via flask-sock.

## Architecture

Single-page app controlling a pen plotter (Marlin-compatible 3D printer) through a browser.

### Backend (Python)

| File | Role |
|------|------|
| `app.py` | Flask server, all API routes (~45 endpoints), WebSocket, ink streaming, OCR pipeline |
| `serial_conn.py` | Thread-safe PySerial wrapper — G-code streaming with "ok" ack flow, live plot mode, jog, e-stop |
| `gcode.py` | SVG pipeline — parse → flatten curves → simplify (RDP) → fill → transform → optimize → G-code |
| `config.py` | Dataclass models for tool profiles, TOML loader, calibration/page persistence (JSON) |
| `font.py` | Hershey single-stroke vector font for text patterns |

### Frontend (static/)

Single HTML/JS/CSS — no framework, no bundler. `app.js` (~2700 lines) manages all panels and state.

### Data Flow: SVG → Plot

```
Upload SVG → app.py /api/upload → gcode.py parse SVG → flatten beziers (64 segments)
→ optional simplify/optimize → /api/convert applies transforms → G-code file
→ /api/print → serial_conn.py streams G-code line-by-line with "ok" acknowledgment
→ WebSocket broadcasts progress to browser
```

### Data Flow: Live Plot (Slate → Plotter)

External `capture.py` (in `C:\Users\aaron\penz\`) streams BLE strokes via POST `/stream/stroke`.
Each completed stroke is immediately converted to G-code and queued for serial output.

```
Slate BLE → capture.py → POST /stream/stroke {points, stroke_end}
→ app.py maps coords (portrait A5: swap X/Y, rotate 180°) → _stroke_to_gcode()
→ serial_conn.queue_stroke() → _live_send_loop drains queue to printer
```

### Serial Communication

- Thread-safe with `self._lock` — only one operation at a time
- File streaming and live mode are mutually exclusive (`_sending` / `_live_sending` flags)
- "ok" acknowledgment flow: send line → wait for "ok" → send next
- Emergency stop sends M112 immediately

### Tool Profiles

TOML files in `profiles/` (pencil, pen, watercolor). Each contains movement speeds, Z heights, water settings, fill/hatch config. Editable via UI or directly. Per-tool calibration (Z heights + XY offset) stored in `calibration.json`.

### Coordinate System

- Printer bed: 220×220mm, origin at front-left
- Page positioned via offset from bed edge
- Pen XY offset shifts effective drawing area
- Wacom Slate A5 portrait mapping: `x_mm = (1 - wacom_y/14700) * page_w`, `y_mm = wacom_x/21600 * page_h`

### Watercolor Two-Pass

When enabled: Pass 1 draws with pencil (guide layer), M0 pause for tool swap, Pass 2 retraces with wet brush including periodic water cup dips.

### Ink/OCR Pipeline

`POST /api/ink/ocr` — SVG → PNG (OpenCV auto-crop) → region detection (text vs drawing) → vision LLM chain (NVIDIA NIM → ZhipuAI → Ollama).

## Key Gotchas

- Global `serial` object in app.py is the single SerialConnection instance
- `uploaded_svgs` dict is in-memory only — lost on restart
- `serial_port.txt` caches last-used port for auto-reconnect
- `config.SAFE_Z` (20mm) is the minimum travel height — all pen-up moves use this or higher
- The `_stroke_ended` flag must be sent even with empty points, otherwise strokes merge
