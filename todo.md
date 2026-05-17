# Pen Plotter — Implementation TODO

## Stage 1: Pencil (Foundation)

- [ ] `config.py` — Printer & tool profile system
  - [ ] Bed size defaults (220x220)
  - [ ] TOML profile loader
  - [ ] Calibration data (calibration.json) read/write
  - [ ] Profile CRUD (load, save, list)
- [ ] `profiles/pencil.toml` — Pencil tool settings
- [ ] `profiles/pen.toml` — Pen tool settings
- [ ] `profiles/watercolor.toml` — Watercolor tool settings
- [ ] `gcode.py` — SVG → G-code engine
  - [ ] SVG parsing (paths, lines, rects, circles, polylines)
  - [ ] Bezier flattening to polylines
  - [ ] G-code generation with calibrated Z values
  - [ ] Nearest-neighbor path optimization
  - [ ] Water dip sequence insertion (Stage 3)
- [ ] `serial_conn.py` — PySerial wrapper
  - [ ] Connect/disconnect
  - [ ] Line-by-line send with `ok` flow control
  - [ ] Command queue with progress tracking
  - [ ] Emergency stop (M112)
  - [ ] Position reporting (M114)
- [ ] `app.py` — Flask server
  - [ ] SVG upload endpoint
  - [ ] SVG → G-code conversion endpoint
  - [ ] Calibration API (get, save, step, test-dot)
  - [ ] Settings API (get/save per tool)
  - [ ] Serial connect/disconnect
  - [ ] Jog controls
  - [ ] Print / Stop commands
  - [ ] Status endpoint
  - [ ] WebSocket for real-time progress
- [ ] `static/index.html` — Web UI structure
- [ ] `static/style.css` — Styling
- [ ] `static/app.js` — Frontend logic
  - [ ] Calibration tab (Z jog, test dot, save)
  - [ ] SVG upload & preview (canvas rendering)
  - [ ] Settings panel (per-tool, editable)
  - [ ] Printer controls (connect, home, jog, start, stop)
  - [ ] Progress bar
- [ ] `requirements.txt` — Dependencies
- [ ] Test: Flask app starts
- [ ] Test: SVG upload → G-code generation
- [ ] Test: Connect to printer, jog controls

## Stage 2: Pen (Servo Upgrade)
- [ ] Servo M280 commands in G-code engine
- [ ] Pen pressure calibration wizard
- [ ] Multi-pass drawing support

## Stage 3: Watercolor + Auto Water Dip
- [ ] Auto-dip sequence in G-code engine
- [ ] Multi-layer / color separation workflow
- [ ] Brush tool profile with blotting
