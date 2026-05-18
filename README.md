# Plotter

Web-controlled pen plotter built on a 3D printer. Upload SVGs, convert to G-code, and draw with pencil, pen, or watercolor — all from a browser.

Flask backend + Hershey vector font + serial streaming to any Marlin-compatible printer.

## Quick Start

```bash
pip install -r requirements.txt
python app.py
```

Open `http://localhost:5000`. Pick a serial port, hit Connect. You're in.

## Full Workflow

1. **Calibrate** the tool (Z height + XY offset) — see [Calibration](#calibration)
2. **Position the page** on the bed — see [Page Positioning](#page-positioning)
3. **Upload an SVG** (drag-and-drop or click)
4. **Adjust transforms** — scale, rotate, mirror, offset
5. **Convert** to G-code
6. **Start Plot** — watch it draw with real-time progress

## Calibration

Each tool (pencil, pen, watercolor) stores its own calibration in `calibration.json`.

### Z Height

1. Select tool tab in the Calibration panel
2. Click **Start Calibration** — printer moves to bed center (Z20 X110 Y110)
3. Insert pen, click **Pen Loaded** — Z raises 5mm for clearance
4. Jog Z down using step buttons (1mm → 0.1mm → 0.01mm) until the pen just touches paper
5. Click **Save Pen-Down Height** — saves current Z as contact height
6. **Test Dot** — lowers pen for 1 second to verify contact

### Pen XY Offset

Accounts for the pen tip being offset from the hotend mount point.

**Read Current** — move the pen tip to bed center (110, 110), then click this. It reads the hotend position and calculates the offset automatically.

**Manual Input** — enter X/Y offset values directly, then **Save Offset**.

The effective drawing area shrinks to account for the offset. The UI shows the resulting area.

### Bed Leveling

The Calibration panel includes a guided 4-point bed leveling workflow: **Start** → **Next** (moves to next corner) → adjust knobs → **Repeat** or **Stop**.

## Page Positioning

### Presets

| Preset | Size (mm) |
|--------|-----------|
| 220mm  | 220 × 220 (full bed) |
| A4     | 210 × 297 |
| A5     | 148 × 210 |
| Letter | 216 × 279 |
| 4x6    | 102 × 152 |
| 5x7    | 127 × 178 |
| Custom | any dimensions |

Selecting a preset auto-centers the page on the bed.

### Offsets

Fine-tune page placement with X/Y offset fields — distance from bed edge to page edge.

### Mark Page Outline

Draws L-shaped corner marks (10mm arms) at all four corners using the current tool's Z calibration. Use this to verify page alignment before plotting.

## Tool Profiles

Three profiles ship in `profiles/`:

| Profile | Draw Speed | Travel Speed | Lift Height | Water |
|---------|-----------|-------------|-------------|-------|
| Pencil  | 1500 mm/min | 3000 mm/min | 5.0 mm | Off |
| Pen     | 1200 mm/min | 3000 mm/min | 3.0 mm | Off |
| Watercolor | 1000 mm/min | 2500 mm/min | 5.0 mm | On (two-pass) |

All settings are editable from the Settings panel per tool:

- **Movement** — draw speed, travel speed, lift height
- **Graphite Wear** — Z drop per meter drawn, max depth cap (for pencils that wear down)
- **Water/Brush** — cup position, dip depth/time/interval, scrape distance/speed
- **Pass 2** — wet brush speeds, park position for tool swap
- **Fill/Hatch** — enabled, type (hatch or crosshatch), spacing, angle

Profiles are TOML files — edit directly or use the UI.

## Watercolor Two-Pass

When watercolor two-pass is enabled, plotting happens in two phases:

### Pass 1 — Dry Pencil

Draws the full image with pencil at normal speed. No water. Creates the guide layer.

### Tool Change

Printer raises to safe Z (default 50mm), moves to park position (default X110 Y110), and pauses with `M0`. The display shows "Swap pencil for wet brush." Swap the tool and press resume on the printer.

### Pass 2 — Wet Brush Retrace

Retraces the same paths with the wet brush at slower speed (default 800 mm/min). Periodic water dips occur every N segments:

1. Raise to safe Z
2. Move to cup position
3. Lower into cup (dip depth)
4. Dwell (dip time)
5. Raise to cup rim
6. Scrape sideways to remove excess water
7. Raise to safe Z, return to drawing

Configure dip interval, depth, dwell time, and scrape in the watercolor profile settings.

## Tool Change

The park position is configurable per tool in profile settings (Pass 2 section):

- **change_z** — safe height during swap (default 50mm)
- **change_x / change_y** — park position (default 110, 110 = bed center)

**Go to Park** — moves to the configured park position now.

**Save Current** — saves the current position as the new park position.

## SVG Pipeline

```
SVG file
  ↓ Parse (svgpathtools)
  ↓ Flatten curves (64 segments each)
  ↓ Simplify (Ramer-Douglas-Peucker, optional)
  ↓ Fill generation (hatch/crosshatch, optional)
  ↓ User transforms (scale → rotate → mirror → translate)
  ↓ Optimize path (nearest-neighbor, optional)
  ↓ Auto-scale & center to effective area
  ↓ Apply pen offset + page offset
  ↓ Generate G-code
  ↓ Stream to printer
```

### Supported SVG Elements

`<path>`, `<rect>` (with rounded corners), `<circle>`, `<ellipse>`, `<line>`, `<polyline>`, `<polygon>`

### Fill Generation

For closed shapes (first point ≈ last point), generates hatch or crosshatch fill lines at configurable spacing and angle. The algorithm rotates the polygon, runs scanline intersection, then rotates back.

## Test Patterns

Built-in patterns for calibration and testing — no SVG needed:

| Pattern | Description |
|---------|-------------|
| Circle | 65-point circle |
| Square | Rectangle with rounded corners |
| Grid | 5×5 grid |
| Star | 5-point star |
| Spiral | 3-turn Archimedean spiral |
| Crosshair | Cross lines with center circle |
| Text | Hershey vector font, auto-wrapped |

Select a tool, set size (default 80mm), and hit generate. Text pattern supports multi-line input with word wrapping and alignment (left/center/right).

## API Reference

### Serial

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/ports` | List available serial ports |
| POST | `/api/serial/connect` | Connect to printer — `{port, baudrate}` |
| POST | `/api/serial/disconnect` | Disconnect from printer |
| GET | `/api/status` | Connection status + printer position |
| POST | `/api/send-command` | Send raw G-code — `{command}` |

### Upload & Convert

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/upload` | Upload SVG — returns preview polylines |
| POST | `/api/convert` | Convert SVG to G-code with transforms |
| POST | `/api/test-pattern` | Generate test pattern G-code |
| GET | `/api/download/<file_id>` | Download generated G-code file |

### Print Control

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/print` | Start plotting a G-code file |
| POST | `/api/stop` | Emergency stop (M112) |

### Jog & Motion

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/jog` | Jog axis — `{axis, distance, speed}` |
| POST | `/api/home` | Home all axes |

### Calibration

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/calibration/start` | Move to calibration position |
| POST | `/api/calibration/pen-loaded` | Raise Z 5mm after pen loading |
| GET | `/api/calibration` | Get saved calibration data |
| POST | `/api/calibration/save` | Save Z heights — `{tool, pen_down_z, pen_up_z}` |
| POST | `/api/calibration/offset` | Save pen XY offset — `{tool, offset_x, offset_y}` |
| POST | `/api/calibration/step` | Jog Z for calibration — `{delta}` |
| POST | `/api/calibration/test-dot` | Test pen contact (lower, dwell 1s, raise) |

### Page

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/page-size` | Get saved page size |
| POST | `/api/page-size` | Save page size — `{width, height, preset, offset_x, offset_y}` |
| POST | `/api/mark-page` | Draw corner marks at page boundaries |

### Bed Leveling

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/bed-level` | Control leveling — `{action: start/next/repeat/stop}` |

### Tool Change

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/tool-change-park` | `{action: goto}` or `{action: save}` |

### Settings

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/profiles` | List available tool profiles |
| GET | `/api/settings/<tool_name>` | Get tool settings |
| POST | `/api/settings/<tool_name>` | Save tool settings |

### WebSocket

| Endpoint | Description |
|----------|-------------|
| `WS /ws` | Real-time progress — `{"type":"progress", "completed":N, "total":T}` |

## File Layout

```
plotter/
├── app.py              Flask server + all API routes + WebSocket
├── config.py           Settings models, defaults, profile loading
├── gcode.py            SVG pipeline — parse → transform → G-code
├── font.py             Hershey vector font (A-Z, a-z, 0-9, punctuation)
├── serial_conn.py      PySerial wrapper — connect, stream, jog
├── requirements.txt    flask, flask-sock, pyserial, svgpathtools, numpy, toml
├── calibration.json    Per-tool Z heights + XY offsets (auto-generated)
├── page_size.json      Saved page dimensions + offsets (auto-generated)
├── serial_port.txt     Last used serial port (auto-generated)
├── static/
│   ├── index.html      Web UI
│   └── app.js          Frontend logic
├── profiles/
│   ├── pencil.toml     Pencil profile
│   ├── pen.toml        Pen profile
│   └── watercolor.toml Watercolor profile
└── output/             Generated SVG + G-code files
```
