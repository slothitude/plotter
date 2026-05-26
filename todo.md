# Pen Plotter — Implementation TODO

## Stage 1: Pencil (Foundation) ✅ DONE

## Stage 2: Pen (Servo Upgrade) ✅ DONE

## Stage 3: Watercolor + Auto Water Dip ✅ DONE

## Manga Plotter Toolkit ✅ DONE

## Code Audit Fixes

### Critical
- [x] #1 wcStep reset on new SVG/convert — added `wcStep: 0` to all reset points (create.js, prepare.js)

### High
- [x] #2 _points_in_polygon replaced with ray-casting algorithm — works for concave polygons now
- [x] #3 Page dimension defaults to configured page size (not 220x220 bed) for manga convert

### Medium
- [x] #4 generate_rain — moved random.uniform inside loop for varying line lengths
- [x] #5 _jagged_ellipse — changed to range(n) + close, no triple point
- [x] #6 compile_page — shallow-copies child dicts, no longer mutates caller
- [x] #7 _emit_stroke_pass — resolves per-layer draw speed via config.LAYER_SPEEDS
- [x] #8 app.py default layer — changed `0` to `""` to match Polyline.layer str type
- [x] #9 Stop handler — re-reads getState() inside .then() callback instead of stale capture
- [x] #10 Layer sort — documented that stable sort preserves optimization within groups

### Low
- [x] #11 Print stats now include "status" field: completed/stopped/error
- [x] #12 _print_start_time cleared to None in finally block
- [ ] #13 G0 XY moves not tracked for distance (dormant — current G-code uses G1 for draws)
- [ ] #14 Wear compensation XYZ moves bypass pen-state detection (dormant, wear_rate=0)
- [ ] #15 Stats fields cross-thread unsynchronized (safe under CPython GIL)
- [ ] #16 twoPassId2 stored in state but never read (dead data — kept for potential future use)
- [ ] #17 No intermediate busy state during pass 2 regeneration
- [ ] #18 No input validation on bounds length before tuple unpacking
- [x] #19 generate_impact_burst — clamped n_points to minimum 3
- [x] #20 Extracted _boustrophedon_sort helper, removed duplication
- [x] #21 Gradient tone — precomputed cos/sin tables, no per-dot trig calls
- [x] #22 convert-pass2 — added guard for manga IDs (None SVG path)
- [ ] #23 Simplification tolerance 0.3mm risks collapsing tiny dot tone at high LPI
