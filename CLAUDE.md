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
| `font.py` | Hershey single-stroke vector font + Script Simplex cursive for text patterns |
| `manga.py` | Manga panel generators — panels, speed lines, tone fills, speech bubbles, SFX, effects |
| `plotter_mcp.py` | FastMCP stdio server — exposes plotter API as MCP tools for Claude Code |

### Frontend (static/)

Single HTML/JS/CSS — no framework, no bundler. Modular structure:

- `state.js` — Central reactive pub/sub store (connection, position, tool, page, transforms, workflow state)
- `main.js` — Entry point, initialization orchestration
- `api.js` — Fetch wrapper, `websocket.js` — WS connection + progress
- `router.js` — 5-step navigation (Setup → Create → Prepare → Plot → Config)
- `steps/` — Per-step panel logic (setup, create, prepare, plot, config)
- `creators/` — Content generators (svg-upload, test-patterns, scriptorium, ink-drawing, toon-tracer, handwriting-ocr, slate-controls)
- `components/` — Reusable widgets (canvas-preview, status-bar, page-size)
- `log-drawer.js` — G-code command log panel
- `lib/` — Utilities (toast, slider, drop-zone, escape)

### Data Flow: SVG → Plot

```
Upload SVG → /api/upload → gcode.py parse SVG → flatten beziers (64 segments)
→ optional simplify/optimize → /api/convert applies transforms → G-code file
→ /api/print → serial_conn.py streams G-code line-by-line with "ok" acknowledgment
→ WebSocket broadcasts progress to browser
```

### Data Flow: Text → Plot

```
Scriptorium UI → font.py text_to_strokes() or text_to_cursive() → SVG saved to output/
→ stored in uploaded_svgs → standard Convert/Print pipeline
→ app.py forces simplify=True (0.3mm tolerance) + gcode.py skips <0.2mm micro-moves
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

### Text/Font System

- `font.py`: `text_to_strokes()` (standard Hershey) and `text_to_cursive()` (Script Simplex, chained lowercase)
- Cursive connects lowercase letters into continuous strokes via `_build_cursive_chains()`
- Both share `CHAR_HEIGHT=9`; font sizing: `scale = font_size / CHAR_HEIGHT`
- Generated text goes through the same SVG → Convert → Print pipeline as uploaded SVGs

### G-code Point Optimization

Two layers of micro-move reduction in `gcode.py` `polylines_to_gcode()`:
1. RDP simplification (`simplify_polyline`, tolerance 0.3mm for text) — reduces curve points
2. Min-distance filter (`MIN_MOVE_DIST = 0.2mm`) — skips sub-threshold G1 moves in emission loop, always keeps last point of each polyline

### Watercolor Two-Pass

When enabled: Pass 1 draws with pencil (guide layer), M0 pause for tool swap, Pass 2 retraces with wet brush including periodic water cup dips.

### Ink/OCR Pipeline

`POST /api/ink/ocr` — SVG → PNG (OpenCV auto-crop) → region detection (text vs drawing) → vision LLM chain (NVIDIA NIM → ZhipuAI → Ollama).

### Manga Pipeline

`manga.py` + `static/app/creators/manga-tools.js`. Dispatched via `POST /api/manga/generate` with an `action` field. Generates `Polyline` objects (with `.layer` str for speed-based layering) stored directly in `text_polylines` — no SVG round-trip. `uploaded_svgs[svg_id]` is set to `None` for manga content; the convert endpoint skips SVG parsing when the value is `None`.

## Key Gotchas

- Flask **must run with `debug=False, use_reloader=False`** — the watchdog reloader fork locks the COM port (PermissionError on reconnect)
- Global `serial` object in app.py is the single SerialConnection instance
- Two in-memory stores: `uploaded_svgs` (id→filepath) and `text_polylines` (id→polylines) — both lost on restart
- `serial_port.txt` caches last-used port for auto-reconnect
- `config.SAFE_Z` is **30mm** — all travel/pen-up moves use this or higher; connect and home both raise to SAFE_Z
- G28 doesn't wait for motion to complete — Marlin sends "ok" before homing finishes. Wait 10-15s after G28 before sending movement commands
- The `_stroke_ended` flag must be sent even with empty points, otherwise strokes merge
- `calibration.json` OVERRIDES TOML profile values — always edit calibration.json for offset/Z changes
- `output/` directory is cleaned of files older than 7 days on startup — don't store persistent files there
- Cursive Y coordinates can be negative (descenders) up to ~20 (ascenders) — wider range than standard Hershey
- Manga content sets `uploaded_svgs[svg_id] = None` — convert endpoint must check `is not None` before treating it as an SVG path
- `calibration_confirmed=True` is required on print/convert/live-start calls via the MCP server — this is a safety gate, not optional
