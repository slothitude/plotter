# Pen Plotter — Implementation TODO

## Stage 1: Pencil (Foundation) ✅ DONE

## Stage 2: Pen (Servo Upgrade) ✅ DONE

## Stage 3: Watercolor + Auto Water Dip ✅ DONE

## Manga Plotter Toolkit

- [x] Core generators (panels, tone, bubbles, SFX, speed lines, effects)
- [x] Layer system with per-layer feed rates (border→outline→detail→tone→effect→text)
- [x] Panel presets (2-row, 3-panel, 4-grid, 2-3, L-shape, manga-1)
- [x] Dot tone with vectorized point-in-polygon + boustrophedon sort
- [x] Speech bubble shapes (round, oval, square, cloud, thought, shout) + tails
- [x] SFX lettering with rotation
- [x] Gradient tone generation
- [x] Line tone and crosshatch tone
- [x] Frontend manga-tools.js UI
- [x] API dispatch endpoint `POST /api/manga/generate`
- [x] Pipeline: compile → text_polylines → convert → G-code
- [x] Test panel detection with realistic strokes (8/8 tests: clean, wobbly, multi-stroke merging, close/far panels, degenerate, clamping, min-size)
- [x] Test compile-page flow (multi-layer: border + tone + effect + text)
- [x] Integration test: multi-layer page via compile_page (2 panels, speed lines, bubble, dot tone, SFX)
- [x] Edge case: empty/zero-area panels (empty page, no children, zero-area polygon, <3 points)
- [x] Edge case: tone on tiny region (1mm² → 4 dots, no crash)
- [x] Fix: compile_page now converts tone `bounds` to `polygon` when missing (falls back to panel bounds)
