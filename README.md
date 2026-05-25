# Plotter

Turn any Marlin-compatible 3D printer into a pen plotter. Upload SVGs, write cursive text, trace images, or draw freehand on a Wacom Slate — all from your browser.

Single Python file backend. No build step. No framework. Plug in a pen and draw.

## What It Does

- **SVG plotting** — upload any SVG, it auto-fits to your page and draws it stroke by stroke
- **Cursive writing** — Hershey and Script Simplex fonts with automatic word-wrap and paragraph support
- **Image tracing** — convert photos to plotter-ready SVGs via edge detection
- **Live drawing** — draw on a Wacom Bamboo Slate and watch it plot in real time over BLE
- **Handwriting OCR** — capture ink strokes from the Slate, recognize handwriting with vision LLMs
- **Watercolor** — two-pass mode: dry pencil guide, then wet brush retrace with automatic water cup dips

## Quick Start

```bash
pip install -r requirements.txt
python app.py
```

Open `http://localhost:5000`. If a printer is connected, it auto-detects and connects. Pick a tool, load some content, hit Plot.

## Workflow

The UI walks through 5 steps:

1. **Setup** — connect to printer, calibrate tool Z height and XY offset, level the bed
2. **Create** — upload SVG, generate test patterns, write text, trace images, or draw freehand
3. **Prepare** — adjust transforms (scale, rotate, mirror), convert to G-code, preview toolpath
4. **Plot** — stream G-code to printer with real-time progress via WebSocket
5. **Config** — edit tool profiles, page size, watercolor settings

## Text & Typography

The Scriptorium panel generates plotter-ready text with two fonts:

- **Hershey** — single-stroke Gothic, clean and technical
- **Script Simplex** — cursive, lowercase letters chained into continuous strokes

Both fonts are vector-only (no fills), designed for pen plotters. Text is rendered in page-space with automatic word-wrap:

- Measures word widths in mm to wrap within page margins
- Supports multi-paragraph text with `\n` line breaks
- Configurable font size, margin, and line spacing
- Cursive defaults to 2.0x line spacing to prevent descender overlap

```json
POST /api/test-pattern
{
  "pattern": "text",
  "text": "Hello world",
  "font": "cursive",
  "font_size": 5,
  "page_width": 148,
  "page_height": 210
}
```

## Image Tracing

Upload a photo and convert it to a plotter-ready SVG via an OpenCV pipeline:

1. Resize, grayscale, bilateral filter
2. Posterize to reduce gray levels
3. Canny edge detection
4. Find and simplify contours
5. Scale pixel coords to page mm, flip Y for printer coords

Configurable: Canny thresholds, blur, posterize levels, contour simplification (epsilon), invert for dark-on-light images.

## Live Plot (Wacom Slate)

Draw on a Wacom Bamboo Slate with a real pen and the plotter traces your strokes in real time over Bluetooth LE.

```
Slate pen down → BLE stream → capture.py → POST /stream/stroke
  → app.py maps coords to bed mm → G-code → serial queue → plotter draws
```

Each completed stroke (pen up) is immediately converted to G-code and queued. The plotter draws stroke by stroke as you write.

## Ink Capture & OCR

Capture freehand drawings from the Slate, then run OCR on handwritten content:

1. **Capture** — stream BLE strokes from Slate, accumulate into SVG
2. **OCR** — render SVG to PNG, detect text vs drawing regions via connected components, send to vision LLM chain (NVIDIA NIM → ZhipuAI → Ollama)
3. Returns transcribed text with region classification

## SVG Pipeline

```
Upload SVG
  → parse (svgpathtools — path, rect, circle, ellipse, line, polyline, polygon)
  → flatten curves (64 segments each)
  → simplify (Ramer-Douglas-Peucker, optional)
  → fill generation (hatch/crosshatch, optional)
  → user transforms (rotate → mirror)
  → optimize path order (nearest-neighbor, optional)
  → auto-scale & center to effective drawing area
  → apply pen offset + page offset
  → micro-move filter (skip < 0.2mm G1 moves)
  → G-code output
```

The pipeline accounts for the physical pen offset from the hotend. It computes the effective drawing area (where the pen can actually reach) and auto-fits content within it. The pen XY offset and Z calibration are stored per-tool in `calibration.json`, overriding the TOML base values.

## Calibration

Each tool (pencil, pen, watercolor) has independent calibration stored in `calibration.json`:

- **Z height** — jog Z down until pen contacts paper, save as pen-down height
- **XY offset** — distance from hotend to pen tip (measured with a ruler or calculated from known position)
- **Bed leveling** — guided 4-point L-mark workflow

The effective drawing area is computed from the bed size minus the pen offset. With a -40, -45mm offset on a 220mm bed, the pen can reach X: 0–180mm, Y: 0–175mm.

## Tool Profiles

Three profiles in `profiles/`:

| Profile | Draw Speed | Travel Speed | Lift Height | Notes |
|---------|-----------|-------------|-------------|-------|
| Pencil  | 1500 mm/min | 3000 mm/min | 5.0 mm | Graphite wear compensation |
| Pen     | 1200 mm/min | 3000 mm/min | 3.0 mm | Default tool |
| Watercolor | 1000 mm/min | 2500 mm/min | 5.0 mm | Two-pass, water cup dips |

Settings include: draw/travel speed, lift height, graphite wear rate + max depth, water cup position/dip/scrape, fill/hatch config. Editable via UI or TOML files directly.

## Watercolor Two-Pass

1. **Pass 1** — draw full image with pencil (guide layer)
2. **Pause** — printer parks at tool change position, `M0` wait
3. **Swap** — replace pencil with wet brush, press resume
4. **Pass 2** — retrace all paths with wet brush at slower speed, with periodic water cup dips (lower into cup, dwell, scrape excess, return to drawing)

## Page Presets

| Preset | Size (mm) |
|--------|-----------|
| 220mm  | 220 × 220 (full bed) |
| A4     | 210 × 297 |
| A5     | 148 × 210 |
| Letter | 216 × 279 |
| 4×6    | 102 × 152 |
| 5×7    | 127 × 178 |
| Custom | any dimensions |

Pages auto-center on the bed. Fine-tune with X/Y offset. **Mark Page Outline** draws L-shaped corner marks to verify alignment before plotting.

## Serial Communication

- Thread-safe PySerial with `"ok"` acknowledgment flow — one command at a time
- Auto-connect on startup to cached serial port
- 10-second ack timeout per command with diagnostic logging
- Auto-reconnect and park if serial drops mid-print
- Emergency stop sends `M112` immediately

## Architecture

```
plotter/
├── app.py                  Flask server (~45 endpoints), WebSocket, ink streaming, OCR
├── serial_conn.py          Thread-safe PySerial — streaming, live mode, jog, e-stop
├── gcode.py                SVG pipeline — parse → transform → optimize → G-code
├── config.py               Dataclass models, TOML profiles, calibration/page persistence
├── font.py                 Hershey + Script Simplex cursive vector fonts
├── calibration.json        Per-tool Z + XY offset overrides (gitignored)
├── page_size.json          Saved page dimensions
├── serial_port.txt         Cached serial port for auto-reconnect
├── profiles/
│   ├── pencil.toml
│   ├── pen.toml
│   └── watercolor.toml
├── static/
│   ├── index.html          Single-page UI
│   ├── style.css
│   └── app/
│       ├── state.js        Reactive pub/sub store
│       ├── main.js         Entry point
│       ├── api.js          Fetch wrapper
│       ├── websocket.js    WS connection + progress
│       ├── router.js       5-step navigation
│       ├── steps/          Per-step panels (setup, create, prepare, plot, config)
│       ├── creators/       Content generators (svg-upload, scriptorium, ink-drawing,
│       │                    toon-tracer, test-patterns, handwriting-ocr, slate-controls)
│       ├── components/     Reusable widgets (canvas-preview, status-bar, page-size)
│       └── lib/            Utilities (toast, slider, drop-zone, escape)
└── output/                 Generated SVG + G-code (auto-cleaned after 7 days)
```

## Requirements

- Python 3.10+
- Marlin-compatible 3D printer (tested on Ender 3)
- Serial connection (USB, 250000 baud)
- For OCR: NVIDIA NIM API key, ZhipuAI key, or local Ollama with a vision model

```
flask
flask-sock
pyserial
svgpathtools
numpy
scipy
opencv-python
svgwrite
toml
requests
```

## License

MIT
