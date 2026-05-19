/* ─── Plotter Control — Frontend Logic v2 ─── */

// ── State ────────────────────────────────────────────────────────────
const state = {
    connected: false,
    busy: false,
    currentSvgId: null,
    gcodeGenerated: false,
    jogStep: 10,
    calTool: 'pencil',
    settingsTool: 'pencil',
    pageWidth: 220,
    pageHeight: 220,
    pagePreset: '220mm',
    pageOffsetX: 0,
    pageOffsetY: 0,
    penOffsetX: 0,
    penOffsetY: 0,
    plotTool: 'pencil',
    ws: null,
    statusInterval: null,
    toolpath: [],
    showDraw: true,
    showTravel: false,
    showGrid: true,
    // Transform state
    transform: {
        scale: 1.0,
        rotate: 0,
        translate_x: 0,
        translate_y: 0,
        mirror_x: false,
        mirror_y: false,
        optimize: true,
        simplify: false,
    },
};

// ── Init ─────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    loadPorts();
    loadCalibration();
    loadSettings(state.settingsTool);
    initSideNav();
    initSidebarControls();
    initCalibration();
    initBedLevel();
    initPlot();
    initPageSize();
    initSettings();
    initLog();
    initScriptorium();
    initToon();
    initInk();
    initCanvasOverlays();
    initTransforms();
    connectWebSocket();
    startStatusPolling();
    drawEmptyCanvas();
});

// ── Side Nav ─────────────────────────────────────────────────────────
function initSideNav() {
    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', () => {
            document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
            document.querySelectorAll('.panel-content').forEach(p => p.classList.remove('active'));
            item.classList.add('active');
            document.getElementById('panel-' + item.dataset.panel).classList.add('active');
        });
    });
}

// ── Top Bar Controls ─────────────────────────────────────────────────
function initSidebarControls() {
    document.getElementById('btn-connect').addEventListener('click', toggleConnection);

    document.getElementById('btn-home').addEventListener('click', () => {
        api('/api/home', { method: 'POST' })
            .then(r => r.json())
            .then(data => {
                if (data.position) updatePosition(data.position);
                if (data.ok) toast('Homed all axes', 'success');
            });
    });

    document.getElementById('btn-estop').addEventListener('click', () => {
        api('/api/stop', { method: 'POST' })
            .then(() => toast('Emergency stop sent', 'error'));
    });

    document.querySelectorAll('.btn-jog-sm').forEach(btn => {
        btn.addEventListener('click', () => {
            const axis = btn.dataset.axis;
            const dir = parseInt(btn.dataset.dir);
            const step = parseFloat(document.getElementById('jog-step').value);
            jog(axis, dir * step);
        });
    });
}

function jog(axis, distance) {
    logSerial('tx', `${axis}${distance > 0 ? '+' : ''}${distance.toFixed(3)}`);
    api('/api/jog', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ axis, distance, speed: axis === 'Z' ? 300 : 1500 }),
    })
        .then(r => r.json())
        .then(data => {
            if (data.position) updatePosition(data.position);
            if (data.ok) logSerial('rx', 'OK');
        });
}

function updatePosition(pos) {
    if (pos.X !== undefined) {
        document.getElementById('pos-x').textContent = pos.X.toFixed(3);
    }
    if (pos.Y !== undefined) {
        document.getElementById('pos-y').textContent = pos.Y.toFixed(3);
    }
    if (pos.Z !== undefined) {
        document.getElementById('pos-z').textContent = pos.Z.toFixed(3);
        document.getElementById('cal-z-display').textContent = pos.Z.toFixed(3);
    }
}

// ── Connection ───────────────────────────────────────────────────────
function loadPorts() {
    api('/api/ports').then(r => r.json()).then(ports => {
        const sel = document.getElementById('port-select');
        sel.innerHTML = '<option value="">-- Select Port --</option>';
        ports.forEach(p => {
            const opt = document.createElement('option');
            opt.value = p.port;
            opt.textContent = p.port + ' — ' + p.description;
            sel.appendChild(opt);
        });
    });
}

function toggleConnection() {
    if (state.connected) {
        api('/api/serial/disconnect', { method: 'POST' })
            .then(() => { setConnected(false); toast('Disconnected', 'warn'); });
    } else {
        const port = document.getElementById('port-select').value;
        if (!port) return toast('Select a port first', 'warn');
        toast('Connecting...', 'info');
        api('/api/serial/connect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ port }),
        })
            .then(r => r.json())
            .then(data => {
                if (data.ok) { setConnected(true); toast('Connected', 'success'); }
                else toast(data.error || 'Connection failed', 'error');
            })
            .catch(() => toast('Connection failed', 'error'));
    }
}

function setConnected(val) {
    state.connected = val;
    const btn = document.getElementById('btn-connect');
    const badge = document.getElementById('conn-status');
    if (val) {
        btn.textContent = 'DISCONNECT';
        btn.classList.remove('btn-connect');
        btn.style.background = 'transparent';
        btn.style.color = 'var(--text-0)';
        btn.style.border = '1px solid var(--border-light)';
        badge.textContent = 'ONLINE';
        badge.className = 'conn-badge connected';
        logSerial('info', 'Printer connected');
    } else {
        btn.textContent = 'CONNECT';
        btn.classList.add('btn-connect');
        btn.style.background = '';
        btn.style.color = '';
        btn.style.border = '';
        badge.textContent = 'OFFLINE';
        badge.className = 'conn-badge disconnected';
        logSerial('info', 'Printer disconnected');
    }
}

// ── Calibration ──────────────────────────────────────────────────────
function initCalibration() {
    document.getElementById('cal-tool').addEventListener('change', e => {
        state.calTool = e.target.value;
    });

    document.getElementById('btn-cal-start').addEventListener('click', () => {
        if (!state.connected) return toast('Connect printer first', 'warn');
        api('/api/calibration/start', { method: 'POST' }).then(r => r.json()).then(data => {
            if (data.ok) {
                if (data.position) updatePosition(data.position);
                toast('At load position — insert pen, then tap Pen Loaded', 'success');
            }
        });
    });

    document.getElementById('btn-cal-pen-loaded').addEventListener('click', () => {
        if (!state.connected) return toast('Connect printer first', 'warn');
        api('/api/calibration/pen-loaded', { method: 'POST' }).then(r => r.json()).then(data => {
            if (data.ok) {
                if (data.position) updatePosition(data.position);
                toast('Pen raised — adjust Z until pen touches paper', 'success');
            }
        });
    });

    document.getElementById('btn-cal-test-dot').addEventListener('click', () => {
        if (!state.connected) return toast('Connect printer first', 'warn');
        api('/api/calibration/test-dot', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tool: state.calTool }),
        }).then(r => r.json()).then(data => {
            if (data.ok) toast('Test dot executed', 'success');
        });
    });

    document.querySelectorAll('.btn-z-step').forEach(btn => {
        btn.addEventListener('click', () => {
            if (!state.connected) return toast('Connect printer first', 'warn');
            const delta = parseFloat(btn.dataset.delta);
            api('/api/calibration/step', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ distance: delta }),
            }).then(r => r.json()).then(data => {
                if (data.position) updatePosition(data.position);
            });
        });
    });

    document.getElementById('btn-cal-save').addEventListener('click', () => {
        const z = parseFloat(document.getElementById('cal-z-display').textContent);
        if (isNaN(z)) return toast('No Z value to save', 'warn');
        const liftHeight = 5.0;
        api('/api/calibration/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                tool: state.calTool,
                pen_down_z: z,
                pen_up_z: z + liftHeight,
            }),
        }).then(r => r.json()).then(data => {
            if (data.ok) {
                toast(`Saved ${state.calTool}: pen_down_z=${z.toFixed(3)}`, 'success');
                loadCalibration();
            }
        });
    });

    // Pen offset: UI shows actual pen-to-hotend distance
    // Backend stores hotend position when pen is at bed center (110)
    // Conversion: pen_offset = 110 - stored_offset  /  stored_offset = 110 - pen_offset
    const BED_CENTER = 110;

    // Load offset into inputs when calibration data loads
    function loadPenOffsetInputs() {
        api('/api/calibration').then(r => r.json()).then(cal => {
            const data = cal[state.calTool];
            if (data) {
                const storedOx = data.offset_x || 0;
                const storedOy = data.offset_y || 0;
                document.getElementById('cal-offset-x').value = storedOx ? (BED_CENTER - storedOx).toFixed(1) : 0;
                document.getElementById('cal-offset-y').value = storedOy ? (BED_CENTER - storedOy).toFixed(1) : 0;
                showEffectiveArea(storedOx, storedOy);
            }
        });
    }
    loadPenOffsetInputs();

    // Reload offset inputs when switching cal tool
    const origCalToolHandler = document.getElementById('cal-tool').onchange;
    document.getElementById('cal-tool').addEventListener('change', () => {
        setTimeout(loadPenOffsetInputs, 100);
    });

    // "Read Current" — reads hotend position, computes pen offset (assumes pen is at bed center)
    document.getElementById('btn-cal-read-offset').addEventListener('click', () => {
        api('/api/status').then(r => r.json()).then(data => {
            if (!data.position || !data.position.X) return toast('No position — connect and home first', 'warn');
            const hx = parseFloat(data.position.X);
            const hy = parseFloat(data.position.Y);
            document.getElementById('cal-offset-x').value = (BED_CENTER - hx).toFixed(1);
            document.getElementById('cal-offset-y').value = (BED_CENTER - hy).toFixed(1);
            toast(`Pen offset read: X=${(BED_CENTER - hx).toFixed(1)} Y=${(BED_CENTER - hy).toFixed(1)}`, 'info');
        });
    });

    // "Save Offset" — converts pen offset to stored format and saves
    document.getElementById('btn-cal-save-offset').addEventListener('click', () => {
        const penOx = parseFloat(document.getElementById('cal-offset-x').value) || 0;
        const penOy = parseFloat(document.getElementById('cal-offset-y').value) || 0;
        const storedOx = BED_CENTER - penOx;
        const storedOy = BED_CENTER - penOy;
        api('/api/calibration/offset', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tool: state.calTool, offset_x: storedOx, offset_y: storedOy }),
        }).then(r => r.json()).then(data => {
            if (data.ok) {
                toast(`Saved ${state.calTool} pen offset: X=${penOx} Y=${penOy}`, 'success');
                loadCalibration();
                showEffectiveArea(storedOx, storedOy);
            }
        });
    });

    // "Clear" — sets offset to 0
    document.getElementById('btn-cal-clear-offset').addEventListener('click', () => {
        document.getElementById('cal-offset-x').value = 0;
        document.getElementById('cal-offset-y').value = 0;
        api('/api/calibration/offset', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tool: state.calTool, offset_x: 0, offset_y: 0 }),
        }).then(r => r.json()).then(data => {
            if (data.ok) {
                toast('Pen offset cleared', 'info');
                loadCalibration();
                showEffectiveArea(0, 0);
            }
        });
    });
}

// ── Bed Leveling ─────────────────────────────────────────────────────
function initBedLevel() {
    const btnStart = document.getElementById('btn-bed-level');
    const btnRepeat = document.getElementById('btn-bed-repeat');
    const btnNext = document.getElementById('btn-bed-next');
    const btnStop = document.getElementById('btn-bed-stop');
    const controls = document.getElementById('bed-level-controls');
    const statusEl = document.getElementById('bed-level-status');

    function showControls(corner, label) {
        controls.classList.remove('hidden');
        btnStart.classList.add('hidden');
        statusEl.textContent = `Corner ${corner + 1}/4: ${label}`;
    }

    function hideControls() {
        controls.classList.add('hidden');
        btnStart.classList.remove('hidden');
    }

    btnStart.addEventListener('click', () => {
        if (!state.connected) return toast('Connect printer first', 'warn');
        api('/api/bed-level', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'start', tool: state.calTool }),
        }).then(r => r.json()).then(data => {
            if (data.error) return toast(data.error, 'error');
            showControls(data.corner, data.label);
            toast(`Drawing at ${data.label}`, 'success');
        });
    });

    btnRepeat.addEventListener('click', () => {
        api('/api/bed-level', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'repeat', tool: state.calTool }),
        }).then(r => r.json()).then(data => {
            if (data.error) return toast(data.error, 'error');
            showControls(data.corner, data.label);
            toast(`Redrawing ${data.label}`, 'success');
        });
    });

    btnNext.addEventListener('click', () => {
        api('/api/bed-level', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'next', tool: state.calTool }),
        }).then(r => r.json()).then(data => {
            if (data.error) return toast(data.error, 'error');
            if (data.done) {
                hideControls();
                toast('Bed leveling complete — all 4 corners checked', 'success');
            } else {
                showControls(data.corner, data.label);
                toast(`Drawing at ${data.label}`, 'success');
            }
        });
    });

    btnStop.addEventListener('click', () => {
        api('/api/bed-level', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'stop', tool: state.calTool }),
        }).then(r => r.json()).then(data => {
            hideControls();
            toast('Bed leveling stopped', 'info');
        });
    });
}

function loadCalibration() {
    api('/api/calibration').then(r => r.json()).then(cal => {
        const list = document.getElementById('cal-saved-list');
        if (!Object.keys(cal).length) {
            list.innerHTML = '<div class="cal-saved-item">No calibrations saved yet.</div>';
            return;
        }
        list.innerHTML = '';
        for (const [tool, heights] of Object.entries(cal)) {
            const div = document.createElement('div');
            div.className = 'cal-saved-item';
            const ox = heights.offset_x || 0;
            const oy = heights.offset_y || 0;
            const penOx = 110 - ox;
            const penOy = 110 - oy;
            div.innerHTML = `
                <span class="tool-name">${tool}</span>
                <span class="height-values">
                    Down: ${heights.pen_down_z.toFixed(3)} mm · Up: ${heights.pen_up_z.toFixed(3)} mm<br>
                    Pen offset: X=${penOx.toFixed(1)} Y=${penOy.toFixed(1)} mm
                </span>`;
            list.appendChild(div);
        }

        // Update pen offset for current plot tool
        const plotCal = cal[state.plotTool];
        if (plotCal) {
            state.penOffsetX = 110 - (plotCal.offset_x || 110);
            state.penOffsetY = 110 - (plotCal.offset_y || 110);
        }
    });
}

function showEffectiveArea(ox, oy) {
    const bedX = 220, bedY = 220;
    const el = document.getElementById('effective-area-info');
    if (!el) return;
    if (ox === 0 && oy === 0) {
        el.style.display = 'block';
        el.textContent = `Effective area: ${bedX} x ${bedY} mm (full bed)`;
        return;
    }
    const cx = ox - bedX / 2;
    const cy = oy - bedY / 2;
    const physX = -cx;
    const physY = -cy;
    const effOx = Math.max(0, physX);
    const effOy = Math.max(0, physY);
    const effW = Math.min(bedX, bedX + physX) - effOx;
    const effH = Math.min(bedY, bedY + physY) - effOy;
    el.style.display = 'block';
    el.textContent = `Effective drawing area: ${effW.toFixed(0)} x ${effH.toFixed(0)} mm at (${effOx.toFixed(0)}, ${effOy.toFixed(0)})`;
}

// ── Plot Panel ───────────────────────────────────────────────────────
function initPlot() {
    const dropZone = document.getElementById('drop-zone');
    const svgInput = document.getElementById('svg-input');

    dropZone.addEventListener('click', () => svgInput.click());
    dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('dragover'); });
    dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
    dropZone.addEventListener('drop', e => {
        e.preventDefault();
        dropZone.classList.remove('dragover');
        if (e.dataTransfer.files.length) uploadSvg(e.dataTransfer.files[0]);
    });
    svgInput.addEventListener('change', e => {
        if (e.target.files.length) uploadSvg(e.target.files[0]);
    });

    document.getElementById('btn-convert').addEventListener('click', convertSvg);
    document.getElementById('btn-download-gcode').addEventListener('click', downloadGcode);
    document.getElementById('btn-print').addEventListener('click', startPrint);

    // Tool tabs
    document.querySelectorAll('.tool-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.tool-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            state.plotTool = tab.dataset.tool;
        });
    });

    // Test pattern buttons (grid)
    document.querySelectorAll('.test-grid .btn').forEach(btn => {
        btn.addEventListener('click', () => loadTestPattern(btn.dataset.pattern));
    });

    // Test text
    document.getElementById('btn-test-text').addEventListener('click', () => {
        const text = document.getElementById('test-text-value').value || 'Hello World';
        loadTestPattern('text', text);
    });

    // G-code drawer toggle
    document.getElementById('gcode-drawer-toggle').addEventListener('click', () => {
        document.getElementById('gcode-drawer').classList.toggle('open');
    });
}

function loadTestPattern(pattern, text) {
    const size = parseFloat(document.getElementById('test-size').value) || 80;
    const body = {
        pattern,
        size,
        tool: state.plotTool,
        page_width: state.pageWidth,
        page_height: state.pageHeight,
        page_offset_x: state.pageOffsetX,
        page_offset_y: state.pageOffsetY,
        ...getTransformParams(),
    };
    if (pattern === 'text') body.text = text || 'Hello World';

    api('/api/test-pattern', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    }).then(r => r.json()).then(data => {
        if (data.error) return toast(data.error, 'error');
        state.currentSvgId = data.id;
        document.getElementById('upload-info').classList.remove('hidden');
        document.getElementById('upload-info').innerHTML = `Test: <span class="filename">${pattern}</span> · ${data.stroke_count} strokes`;

        if (data.polylines) {
            state.toolpath = data.toolpath || [];
            drawPreview(data.polylines, state.toolpath);
            document.getElementById('preview-info').textContent = `${data.stroke_count} strokes`;
        }

        if (data.stats) updateStats(data.stats);
        if (data.has_gcode) {
            state.gcodeGenerated = true;
            document.getElementById('gcode-preview').textContent = data.gcode_preview || '';
            document.getElementById('gcode-lines').textContent = `${data.line_count} lines`;
            document.getElementById('btn-download-gcode').disabled = false;
            document.getElementById('btn-print').disabled = false;
            document.getElementById('btn-convert').disabled = true;
        } else {
            document.getElementById('btn-convert').disabled = false;
            document.getElementById('btn-print').disabled = true;
            document.getElementById('btn-download-gcode').disabled = true;
            state.gcodeGenerated = false;
        }
        toast(`Test pattern "${pattern}" loaded`, 'success');
    });
}

function uploadSvg(file) {
    if (!file.name.endsWith('.svg')) return toast('Only SVG files accepted', 'error');

    const fd = new FormData();
    fd.append('file', file);

    api('/api/upload', { method: 'POST', body: fd })
        .then(r => r.json())
        .then(data => {
            if (data.error) return toast(data.error, 'error');
            state.currentSvgId = data.id;
            state.toolpath = [];

            const info = document.getElementById('upload-info');
            info.classList.remove('hidden');
            info.innerHTML = `Uploaded: <span class="filename">${file.name}</span> · ${data.stroke_count} strokes`;

            document.getElementById('btn-convert').disabled = false;

            if (data.polylines) {
                drawPreview(data.polylines);
                document.getElementById('preview-info').textContent = `${data.stroke_count} strokes`;
            }

            toast('SVG uploaded', 'success');
        })
        .catch(() => toast('Upload failed', 'error'));
}

function convertSvg() {
    if (!state.currentSvgId) return;
    const tool = state.plotTool;

    api('/api/convert', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            id: state.currentSvgId,
            tool,
            page_width: state.pageWidth,
            page_height: state.pageHeight,
            page_offset_x: state.pageOffsetX,
            page_offset_y: state.pageOffsetY,
            ...getTransformParams(),
        }),
    })
        .then(r => r.json())
        .then(data => {
            if (data.error) return toast(data.error, 'error');

            document.getElementById('gcode-preview').textContent = data.gcode_preview || 'No preview available';
            document.getElementById('gcode-lines').textContent = `${data.line_count} lines`;
            document.getElementById('btn-download-gcode').disabled = false;
            document.getElementById('btn-print').disabled = false;
            state.gcodeGenerated = true;

            state.toolpath = data.toolpath || [];

            if (data.polylines) {
                drawPreview(data.polylines, state.toolpath);
            }

            if (data.stats) updateStats(data.stats);

            const result = document.getElementById('convert-result');
            result.classList.remove('hidden');
            result.textContent = `${data.line_count} G-code lines generated`;

            toast('G-code generated', 'success');
        })
        .catch(() => toast('Conversion failed', 'error'));
}

function downloadGcode() {
    if (!state.currentSvgId) return;
    window.location.href = `/api/download/${state.currentSvgId}`;
}

function startPrint() {
    if (!state.currentSvgId || !state.gcodeGenerated) return;
    if (!state.connected) return toast('Connect printer first', 'warn');

    document.getElementById('progress-container').classList.remove('hidden');
    document.getElementById('progress-fill').style.width = '0%';
    document.getElementById('progress-text').textContent = '0%';

    api('/api/print', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: state.currentSvgId }),
    })
        .then(r => r.json())
        .then(data => {
            if (data.ok) toast('Plotting started', 'success');
            else toast(data.error, 'error');
        })
        .catch(() => toast('Print failed', 'error'));
}

// ── Canvas Overlays ──────────────────────────────────────────────────
function initCanvasOverlays() {
    document.getElementById('btn-show-draw').addEventListener('click', function() {
        state.showDraw = !state.showDraw;
        this.classList.toggle('active');
        redrawCanvas();
    });

    document.getElementById('btn-show-travel').addEventListener('click', function() {
        state.showTravel = !state.showTravel;
        this.classList.toggle('active');
        redrawCanvas();
    });

    document.getElementById('btn-show-grid').addEventListener('click', function() {
        state.showGrid = !state.showGrid;
        this.classList.toggle('active');
        redrawCanvas();
    });

    document.getElementById('btn-zoom-fit').addEventListener('click', () => {
        redrawCanvas();
    });

    // Mouse position tracking
    const canvas = document.getElementById('preview-canvas');
    canvas.addEventListener('mousemove', e => {
        const rect = canvas.getBoundingClientRect();
        const scaleX = canvas.width / rect.width;
        const scaleY = canvas.height / rect.height;
        const cx = (e.clientX - rect.left) * scaleX;
        const cy = (e.clientY - rect.top) * scaleY;
        // Convert canvas coords to mm (approximate)
        const padding = 20;
        const bedW = state.pageWidth;
        const bedH = state.pageHeight;
        const drawW = canvas.width - padding * 2;
        const drawH = canvas.height - padding * 2;
        const s = Math.min(drawW / bedW, drawH / bedH);
        const mmX = ((cx - padding) / s).toFixed(1);
        const mmY = ((cy - padding) / s).toFixed(1);
        document.getElementById('cursor-pos').textContent = `${mmX}, ${mmY} mm`;
    });

    canvas.addEventListener('mouseleave', () => {
        document.getElementById('cursor-pos').textContent = '--';
    });
}

function redrawCanvas() {
    const canvas = document.getElementById('preview-canvas');
    const ctx = canvas.getContext('2d');
    if (state.toolpath.length > 0) {
        drawToolpath(ctx, canvas.width, canvas.height);
    } else if (state.currentSvgId) {
        // Will be redrawn from stored polylines — just redraw grid
        drawEmptyCanvas();
    } else {
        drawEmptyCanvas();
    }
}

// ── Transforms ───────────────────────────────────────────────────────
function initTransforms() {
    const sliders = {
        'tf-scale': { key: 'scale', valId: 'tf-scale-val', fmt: v => parseFloat(v).toFixed(1) },
        'tf-rotate': { key: 'rotate', valId: 'tf-rotate-val', fmt: v => `${v}°` },
        'tf-offset-x': { key: 'translate_x', valId: 'tf-offset-x-val', fmt: v => v },
        'tf-offset-y': { key: 'translate_y', valId: 'tf-offset-y-val', fmt: v => v },
    };

    for (const [sliderId, cfg] of Object.entries(sliders)) {
        const slider = document.getElementById(sliderId);
        slider.addEventListener('input', () => {
            const val = parseFloat(slider.value);
            state.transform[cfg.key] = val;
            document.getElementById(cfg.valId).textContent = cfg.fmt(slider.value);
        });
    }

    document.getElementById('tf-mirror-x').addEventListener('change', e => {
        state.transform.mirror_x = e.target.checked;
    });
    document.getElementById('tf-mirror-y').addEventListener('change', e => {
        state.transform.mirror_y = e.target.checked;
    });
    document.getElementById('tf-optimize').addEventListener('change', e => {
        state.transform.optimize = e.target.checked;
    });
    document.getElementById('tf-simplify').addEventListener('change', e => {
        state.transform.simplify = e.target.checked;
    });

    document.getElementById('btn-reset-transforms').addEventListener('click', () => {
        state.transform = { scale: 1.0, rotate: 0, translate_x: 0, translate_y: 0, mirror_x: false, mirror_y: false, optimize: true, simplify: false };
        document.getElementById('tf-scale').value = 1;
        document.getElementById('tf-scale-val').textContent = '1.0';
        document.getElementById('tf-rotate').value = 0;
        document.getElementById('tf-rotate-val').textContent = '0°';
        document.getElementById('tf-offset-x').value = 0;
        document.getElementById('tf-offset-x-val').textContent = '0';
        document.getElementById('tf-offset-y').value = 0;
        document.getElementById('tf-offset-y-val').textContent = '0';
        document.getElementById('tf-mirror-x').checked = false;
        document.getElementById('tf-mirror-y').checked = false;
        document.getElementById('tf-optimize').checked = true;
        document.getElementById('tf-simplify').checked = false;
        toast('Transforms reset', 'info');
    });
}

function getTransformParams() {
    return {
        scale: state.transform.scale,
        rotate: state.transform.rotate,
        translate_x: state.transform.translate_x,
        translate_y: state.transform.translate_y,
        mirror_x: state.transform.mirror_x,
        mirror_y: state.transform.mirror_y,
        optimize: state.transform.optimize,
        simplify: state.transform.simplify,
    };
}

// ── Stats ────────────────────────────────────────────────────────────
function updateStats(stats) {
    document.getElementById('stat-strokes').textContent = stats.stroke_count || '--';
    document.getElementById('stat-draw').textContent = stats.draw_distance_mm ? `${stats.draw_distance_mm}mm` : '--';
    document.getElementById('stat-travel').textContent = stats.travel_distance_mm ? `${stats.travel_distance_mm}mm` : '--';
    if (stats.estimated_time_s) {
        const mins = Math.floor(stats.estimated_time_s / 60);
        const secs = Math.round(stats.estimated_time_s % 60);
        document.getElementById('stat-time').textContent = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
    } else {
        document.getElementById('stat-time').textContent = '--';
    }
}

// ── Canvas Preview ───────────────────────────────────────────────────

const BED_SIZE = 220; // physical bed mm

function drawEmptyCanvas() {
    const canvas = document.getElementById('preview-canvas');
    const ctx = canvas.getContext('2d');
    drawBed(ctx, canvas.width, canvas.height);
}

function drawBed(ctx, w, h) {
    ctx.fillStyle = '#0d1117';
    ctx.fillRect(0, 0, w, h);

    const padding = 20;
    const drawW = w - padding * 2;
    const drawH = h - padding * 2;
    const scale = Math.min(drawW / BED_SIZE, drawH / BED_SIZE);

    // Grid lines (every 10mm)
    if (state.showGrid) {
        ctx.strokeStyle = '#1a1f2a';
        ctx.lineWidth = 0.5;
        for (let i = 0; i <= BED_SIZE; i += 10) {
            const x = padding + i * scale;
            ctx.beginPath(); ctx.moveTo(x, padding); ctx.lineTo(x, padding + BED_SIZE * scale); ctx.stroke();
        }
        for (let i = 0; i <= BED_SIZE; i += 10) {
            const y = padding + i * scale;
            ctx.beginPath(); ctx.moveTo(padding, y); ctx.lineTo(padding + BED_SIZE * scale, y); ctx.stroke();
        }
        // 50mm major grid
        ctx.strokeStyle = '#222838';
        ctx.lineWidth = 0.8;
        for (let i = 0; i <= BED_SIZE; i += 50) {
            const x = padding + i * scale;
            ctx.beginPath(); ctx.moveTo(x, padding); ctx.lineTo(x, padding + BED_SIZE * scale); ctx.stroke();
            const y = padding + i * scale;
            ctx.beginPath(); ctx.moveTo(padding, y); ctx.lineTo(padding + BED_SIZE * scale, y); ctx.stroke();
        }
    }

    // Effective area (pen-reachable zone) — dashed orange
    if (state.penOffsetX !== 0 || state.penOffsetY !== 0) {
        const effOx = Math.max(0, state.penOffsetX);
        const effOy = Math.max(0, state.penOffsetY);
        const effW = Math.min(BED_SIZE, BED_SIZE + state.penOffsetX) - effOx;
        const effH = Math.min(BED_SIZE, BED_SIZE + state.penOffsetY) - effOy;
        ctx.fillStyle = '#ff990008';
        ctx.fillRect(padding + effOx * scale, padding + effOy * scale, effW * scale, effH * scale);
        ctx.strokeStyle = '#ff990044';
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 4]);
        ctx.strokeRect(padding + effOx * scale, padding + effOy * scale, effW * scale, effH * scale);
        ctx.setLineDash([]);
    }

    // Page rectangle
    const pageX = padding + state.pageOffsetX * scale;
    const pageY = padding + state.pageOffsetY * scale;
    const pageW = state.pageWidth * scale;
    const pageH = state.pageHeight * scale;
    ctx.fillStyle = '#ffffff06';
    ctx.fillRect(pageX, pageY, pageW, pageH);
    ctx.strokeStyle = '#ffffff30';
    ctx.lineWidth = 1.5;
    ctx.strokeRect(pageX, pageY, pageW, pageH);

    // Page size label
    ctx.fillStyle = '#ffffff40';
    ctx.font = '8px "JetBrains Mono", monospace';
    ctx.fillText(`${state.pageWidth}x${state.pageHeight}mm`, pageX + 2, pageY + pageH - 3);

    // Bed outline (always on top)
    ctx.strokeStyle = '#3a4555';
    ctx.lineWidth = 2;
    ctx.strokeRect(padding, padding, BED_SIZE * scale, BED_SIZE * scale);

    // Labels
    ctx.fillStyle = '#3a4555';
    ctx.font = '9px "JetBrains Mono", monospace';
    ctx.fillText('(0,0)', padding - 2, padding - 4);
    ctx.fillText(`${BED_SIZE}x${BED_SIZE}`, padding + BED_SIZE * scale - 40, padding + BED_SIZE * scale + 12);

    return { padding, scale };
}

function drawPreview(polylines, toolpath) {
    const canvas = document.getElementById('preview-canvas');
    const ctx = canvas.getContext('2d');
    const { padding, scale } = drawBed(ctx, canvas.width, canvas.height);

    if (!polylines || !polylines.length) return;

    // If we have toolpath data (post-conversion), draw at actual bed coordinates
    if (toolpath && toolpath.length > 0) {
        drawToolpathOnCtx(ctx, padding, scale, toolpath);
        return;
    }

    // Raw SVG preview: auto-scale to fit within page area
    let allPts = polylines.flat();
    if (!allPts.length) return;

    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const [x, y] of allPts) {
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
    }
    const svgW = maxX - minX || 1;
    const svgH = maxY - minY || 1;

    // Fit SVG into page area
    const margin = 5;
    const areaW = state.pageWidth - margin * 2;
    const areaH = state.pageHeight - margin * 2;
    const fitScale = Math.min(areaW / svgW, areaH / svgH);
    const svgOffsetX = state.pageOffsetX + (state.pageWidth - svgW * fitScale) / 2 - minX * fitScale;
    const svgOffsetY = state.pageOffsetY + (state.pageHeight - svgH * fitScale) / 2 - minY * fitScale;

    const toCanvas = (px, py) => [
        padding + (px * fitScale + svgOffsetX) * scale,
        padding + (py * fitScale + svgOffsetY) * scale,
    ];

    // Bounding box
    ctx.strokeStyle = '#00e87b33';
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    const [bx, by] = toCanvas(minX, minY);
    const [bxe, bye] = toCanvas(maxX, maxY);
    ctx.strokeRect(bx, by, bxe - bx, bye - by);
    ctx.setLineDash([]);

    if (state.showDraw) {
        ctx.strokeStyle = '#00e87b88';
        ctx.lineWidth = 1.2;
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        for (const path of polylines) {
            if (path.length < 2) continue;
            ctx.beginPath();
            ctx.moveTo(...toCanvas(path[0][0], path[0][1]));
            for (let i = 1; i < path.length; i++) {
                ctx.lineTo(...toCanvas(path[i][0], path[i][1]));
            }
            ctx.stroke();
        }
    }
}

function drawToolpath(ctx, w, h) {
    const { padding, scale } = drawBed(ctx, w, h);
    if (!state.toolpath.length) return;
    drawToolpathOnCtx(ctx, padding, scale, state.toolpath);
}

function drawToolpathOnCtx(ctx, padding, scale, toolpath) {
    // Toolpath data is already in G-code coordinates (hotend mm)
    // Just scale from mm to canvas — no auto-scaling
    const toCanvas = (px, py) => [
        padding + px * scale,
        padding + py * scale,
    ];

    // Travel moves (dim dashed)
    if (state.showTravel) {
        ctx.strokeStyle = '#00e87b33';
        ctx.lineWidth = 0.6;
        ctx.setLineDash([3, 3]);
        for (const seg of toolpath) {
            if (seg.type !== 'travel' || seg.points.length < 2) continue;
            ctx.beginPath();
            ctx.moveTo(...toCanvas(seg.points[0][0], seg.points[0][1]));
            const last = seg.points[seg.points.length - 1];
            ctx.lineTo(...toCanvas(last[0], last[1]));
            ctx.stroke();
        }
        ctx.setLineDash([]);
    }

    // Draw moves (bright solid)
    if (state.showDraw) {
        ctx.strokeStyle = '#00e87b';
        ctx.lineWidth = 1.2;
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        for (const seg of toolpath) {
            if (seg.type !== 'draw' || seg.points.length < 2) continue;
            ctx.beginPath();
            ctx.moveTo(...toCanvas(seg.points[0][0], seg.points[0][1]));
            for (let i = 1; i < seg.points.length; i++) {
                ctx.lineTo(...toCanvas(seg.points[i][0], seg.points[i][1]));
            }
            ctx.stroke();
        }
    }
}

// ── Page Size ────────────────────────────────────────────────────────
const PAGE_PRESETS = {
    '220mm': [220, 220],
    'A4': [210, 297],
    'A5': [148, 210],
    'Letter': [216, 279],
    '4x6': [102, 152],
    '5x7': [127, 178],
};

function initPageSize() {
    const presetSel = document.getElementById('page-preset');
    const widthInput = document.getElementById('page-width');
    const heightInput = document.getElementById('page-height');
    const offsetXInput = document.getElementById('page-offset-x');
    const offsetYInput = document.getElementById('page-offset-y');

    api('/api/page-size').then(r => r.json()).then(data => {
        state.pageWidth = data.width;
        state.pageHeight = data.height;
        state.pagePreset = data.preset || '220mm';
        state.pageOffsetX = data.offset_x || 0;
        state.pageOffsetY = data.offset_y || 0;
        presetSel.value = state.pagePreset;
        widthInput.value = state.pageWidth;
        heightInput.value = state.pageHeight;
        offsetXInput.value = state.pageOffsetX;
        offsetYInput.value = state.pageOffsetY;
        widthInput.disabled = state.pagePreset !== 'Custom';
        heightInput.disabled = state.pagePreset !== 'Custom';
        // Auto-center if offsets are 0 and page is smaller than bed
        if (state.pageOffsetX === 0 && state.pageOffsetY === 0 &&
            (state.pageWidth < BED_SIZE || state.pageHeight < BED_SIZE)) {
            autoCenterPage();
            state.pageOffsetX = parseFloat(document.getElementById('page-offset-x').value);
            state.pageOffsetY = parseFloat(document.getElementById('page-offset-y').value);
        }
        drawEmptyCanvas();
    });

    presetSel.addEventListener('change', () => {
        const preset = presetSel.value;
        if (PAGE_PRESETS[preset]) {
            const [w, h] = PAGE_PRESETS[preset];
            widthInput.value = w;
            heightInput.value = h;
            widthInput.disabled = true;
            heightInput.disabled = true;
        } else {
            widthInput.disabled = false;
            heightInput.disabled = false;
        }
        autoCenterPage();
        savePageSize();
    });

    const onDimChange = () => {
        presetSel.value = 'Custom';
        widthInput.disabled = false;
        heightInput.disabled = false;
        autoCenterPage();
        savePageSize();
    };
    widthInput.addEventListener('change', onDimChange);
    heightInput.addEventListener('change', onDimChange);
    offsetXInput.addEventListener('change', savePageSize);
    offsetYInput.addEventListener('change', savePageSize);

    // Mark Page Outline
    document.getElementById('btn-mark-page').addEventListener('click', () => {
        if (!state.connected) return toast('Connect printer first', 'warn');
        savePageSize();  // ensure current values are saved
        api('/api/mark-page', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tool: state.plotTool }),
        }).then(r => r.json()).then(data => {
            if (data.ok) toast('Page outline marked — check corners', 'success');
            else if (data.error) toast(data.error, 'error');
        });
    });
}

function autoCenterPage() {
    const w = parseFloat(document.getElementById('page-width').value) || 220;
    const h = parseFloat(document.getElementById('page-height').value) || 220;
    document.getElementById('page-offset-x').value = Math.max(0, ((BED_SIZE - w) / 2)).toFixed(1);
    document.getElementById('page-offset-y').value = Math.max(0, ((BED_SIZE - h) / 2)).toFixed(1);
}

function savePageSize() {
    const w = parseFloat(document.getElementById('page-width').value) || 220;
    const h = parseFloat(document.getElementById('page-height').value) || 220;
    const preset = document.getElementById('page-preset').value;
    const ox = parseFloat(document.getElementById('page-offset-x').value) || 0;
    const oy = parseFloat(document.getElementById('page-offset-y').value) || 0;
    state.pageWidth = w;
    state.pageHeight = h;
    state.pagePreset = preset;
    state.pageOffsetX = ox;
    state.pageOffsetY = oy;
    drawEmptyCanvas();
    api('/api/page-size', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ width: w, height: h, preset, offset_x: ox, offset_y: oy }),
    });
}

// ── Settings ─────────────────────────────────────────────────────────
function initSettings() {
    document.querySelectorAll('.settings-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.settings-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            state.settingsTool = tab.dataset.settingsTool;
            loadSettings(state.settingsTool);
        });
    });

    document.getElementById('btn-save-settings').addEventListener('click', saveSettings);

    // Tool change park buttons
    document.getElementById('btn-park-goto').addEventListener('click', () => {
        api('/api/tool-change-park', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'goto', tool: state.settingsTool }),
        }).then(r => r.json()).then(data => {
            if (data.ok) toast('Moved to park position', 'success');
            else if (data.error) toast(data.error, 'error');
        });
    });
    document.getElementById('btn-park-save').addEventListener('click', () => {
        api('/api/tool-change-park', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'save', tool: state.settingsTool }),
        }).then(r => r.json()).then(data => {
            if (data.ok) {
                document.getElementById('set-p2-change-x').value = data.change_x;
                document.getElementById('set-p2-change-y').value = data.change_y;
                document.getElementById('set-p2-change-z').value = data.change_z;
                toast(`Park saved: X${data.change_x} Y${data.change_y} Z${data.change_z}`, 'success');
            } else if (data.error) toast(data.error, 'error');
        });
    });
}

function loadSettings(tool) {
    api(`/api/settings/${tool}`)
        .then(r => r.json())
        .then(data => {
            if (data.error) return;
            document.getElementById('set-draw-speed').value = data.movement.draw_speed;
            document.getElementById('set-travel-speed').value = data.movement.travel_speed;
            document.getElementById('set-lift-height').value = data.movement.lift_height;
            document.getElementById('set-wear-rate').value = data.movement.wear_rate;
            document.getElementById('set-wear-depth').value = data.movement.max_wear_depth;
            document.getElementById('set-water-enabled').checked = data.water.enabled;
            document.getElementById('set-cup-x').value = data.water.cup_x;
            document.getElementById('set-cup-y').value = data.water.cup_y;
            document.getElementById('set-cup-height').value = data.water.cup_height;
            document.getElementById('set-cup-diameter').value = data.water.cup_diameter;
            document.getElementById('set-dip-depth').value = data.water.dip_depth;
            document.getElementById('set-dip-time').value = data.water.dip_time;
            document.getElementById('set-dip-interval').value = data.water.dip_interval;
            document.getElementById('set-scrape-distance').value = data.water.scrape_distance;
            document.getElementById('set-scrape-speed').value = data.water.scrape_speed;
            document.getElementById('set-two-pass').checked = data.water.two_pass;
            if (data.water.pass2) {
                document.getElementById('set-p2-draw-speed').value = data.water.pass2.draw_speed;
                document.getElementById('set-p2-travel-speed').value = data.water.pass2.travel_speed;
                document.getElementById('set-p2-pen-down-z').value = data.water.pass2.pen_down_z;
                document.getElementById('set-p2-lift-height').value = data.water.pass2.lift_height;
                document.getElementById('set-p2-change-z').value = data.water.pass2.change_z;
                document.getElementById('set-p2-change-x').value = data.water.pass2.change_x;
                document.getElementById('set-p2-change-y').value = data.water.pass2.change_y;
            }

            // Fill settings
            if (data.fill) {
                document.getElementById('set-fill-enabled').checked = data.fill.enabled;
                document.getElementById('set-fill-type').value = data.fill.fill_type;
                document.getElementById('set-fill-spacing').value = data.fill.spacing;
                document.getElementById('set-fill-angle').value = data.fill.angle;
            }

            const waterPanel = document.getElementById('water-settings');
            const pass2Panel = document.getElementById('pass2-settings');
            waterPanel.style.display = tool === 'watercolor' ? 'block' : 'none';
            pass2Panel.style.display = tool === 'watercolor' ? 'block' : 'none';
        });
}

function saveSettings() {
    const tool = state.settingsTool;
    api(`/api/settings/${tool}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            movement: {
                draw_speed: parseFloat(document.getElementById('set-draw-speed').value),
                travel_speed: parseFloat(document.getElementById('set-travel-speed').value),
                lift_height: parseFloat(document.getElementById('set-lift-height').value),
                wear_rate: parseFloat(document.getElementById('set-wear-rate').value),
                max_wear_depth: parseFloat(document.getElementById('set-wear-depth').value),
            },
            water: {
                enabled: document.getElementById('set-water-enabled').checked,
                two_pass: document.getElementById('set-two-pass').checked,
                cup_x: parseFloat(document.getElementById('set-cup-x').value),
                cup_y: parseFloat(document.getElementById('set-cup-y').value),
                cup_height: parseFloat(document.getElementById('set-cup-height').value),
                cup_diameter: parseFloat(document.getElementById('set-cup-diameter').value),
                dip_depth: parseFloat(document.getElementById('set-dip-depth').value),
                dip_time: parseInt(document.getElementById('set-dip-time').value),
                dip_interval: parseInt(document.getElementById('set-dip-interval').value),
                scrape_distance: parseFloat(document.getElementById('set-scrape-distance').value),
                scrape_speed: parseFloat(document.getElementById('set-scrape-speed').value),
                pass2: {
                    draw_speed: parseFloat(document.getElementById('set-p2-draw-speed').value),
                    travel_speed: parseFloat(document.getElementById('set-p2-travel-speed').value),
                    pen_down_z: parseFloat(document.getElementById('set-p2-pen-down-z').value),
                    lift_height: parseFloat(document.getElementById('set-p2-lift-height').value),
                    change_z: parseFloat(document.getElementById('set-p2-change-z').value),
                    change_x: parseFloat(document.getElementById('set-p2-change-x').value),
                    change_y: parseFloat(document.getElementById('set-p2-change-y').value),
                },
            },
            fill: {
                enabled: document.getElementById('set-fill-enabled').checked,
                fill_type: document.getElementById('set-fill-type').value,
                spacing: parseFloat(document.getElementById('set-fill-spacing').value),
                angle: parseFloat(document.getElementById('set-fill-angle').value),
            },
        }),
    })
        .then(r => r.json())
        .then(data => {
            if (data.ok) {
                const msg = document.getElementById('settings-saved-msg');
                msg.classList.remove('hidden');
                msg.textContent = 'Saved.';
                setTimeout(() => msg.classList.add('hidden'), 2000);
                toast('Settings saved', 'success');
            }
        });
}

// ── Log Panel ────────────────────────────────────────────────────────
function initLog() {
    document.getElementById('btn-send-cmd').addEventListener('click', sendManualCommand);
    document.getElementById('serial-cmd').addEventListener('keydown', e => {
        if (e.key === 'Enter') sendManualCommand();
    });
}

function sendManualCommand() {
    const input = document.getElementById('serial-cmd');
    const cmd = input.value.trim();
    if (!cmd) return;
    if (!state.connected) return toast('Connect printer first', 'warn');

    logSerial('tx', cmd);
    api('/api/send-command', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command: cmd }),
    })
        .then(r => r.json())
        .then(data => {
            if (data.ok) logSerial('rx', 'OK');
            else logSerial('err', data.error || 'Command failed');
        })
        .catch(() => logSerial('err', 'Send failed'));

    input.value = '';
}

function logSerial(type, message) {
    const log = document.getElementById('serial-log');
    const now = new Date();
    const ts = now.toLocaleTimeString('en-US', { hour12: false });
    const dirSymbol = { tx: '>>', rx: '<<', info: '>', err: '!!' }[type] || '>';

    const line = document.createElement('div');
    line.className = `log-line ${type}`;
    line.innerHTML = `<span class="log-ts">${ts}</span><span class="log-dir">${dirSymbol}</span><span class="log-msg">${escapeHtml(message)}</span>`;
    log.appendChild(line);
    log.scrollTop = log.scrollHeight;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ── Toast System ─────────────────────────────────────────────────────
function toast(message, type = 'success') {
    const container = document.getElementById('toast-container');
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.textContent = message;
    container.appendChild(el);

    setTimeout(() => {
        el.classList.add('fadeout');
        setTimeout(() => el.remove(), 300);
    }, 3000);
}

// ── WebSocket ────────────────────────────────────────────────────────
function connectWebSocket() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${location.host}/ws`;
    state.ws = new WebSocket(wsUrl);

    state.ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === 'progress') {
            const pct = data.total > 0 ? Math.round((data.completed / data.total) * 100) : 0;
            document.getElementById('progress-fill').style.width = pct + '%';
            document.getElementById('progress-text').textContent =
                `${data.completed}/${data.total} (${pct}%)`;

            if (data.completed < 0) {
                document.getElementById('progress-text').textContent = `Error: ${data.info}`;
                document.getElementById('progress-fill').style.background = 'var(--red)';
            }
            if (data.completed >= data.total && data.total > 0) {
                document.getElementById('progress-text').textContent = 'Complete';
                toast('Plotting complete', 'success');
            }
        } else if (data.type === 'ink') {
            if (window._onInkStroke) window._onInkStroke(data.points);
        }
    };

    state.ws.onclose = () => {
        setTimeout(connectWebSocket, 3000);
    };
}

// ── Status Polling ───────────────────────────────────────────────────
function startStatusPolling() {
    state.statusInterval = setInterval(() => {
        if (!state.connected) return;
        api('/api/status')
            .then(r => r.json())
            .then(data => {
                if (data.position && Object.keys(data.position).length) {
                    updatePosition(data.position);
                }
            })
            .catch(() => {});
    }, 2000);
}

// ── Helpers ──────────────────────────────────────────────────────────
function api(url, opts = {}) {
    return fetch(url, opts);
}

// ── Scriptorium ─────────────────────────────────────────────────────
function initScriptorium() {
    const PX_PER_MM = 3.7795;
    const sc = (id) => document.getElementById(id);

    // Page size (independent from plot panel)
    const presets = PAGE_PRESETS;
    let scPageW = 210, scPageH = 297;

    const presetSel = sc('sc-page-preset');
    const widthInput = sc('sc-page-width');
    const heightInput = sc('sc-page-height');

    function applyPreset() {
        const p = presetSel.value;
        if (presets[p]) {
            const [w, h] = presets[p];
            widthInput.value = w;
            heightInput.value = h;
            widthInput.disabled = true;
            heightInput.disabled = true;
        } else {
            widthInput.disabled = false;
            heightInput.disabled = false;
        }
        scPageW = parseInt(widthInput.value);
        scPageH = parseInt(heightInput.value);
        scRender();
    }

    presetSel.addEventListener('change', applyPreset);
    widthInput.addEventListener('change', () => { presetSel.value = 'Custom'; widthInput.disabled = false; heightInput.disabled = false; scRender(); });
    heightInput.addEventListener('change', () => { presetSel.value = 'Custom'; widthInput.disabled = false; heightInput.disabled = false; scRender(); });

    // Swatches
    document.querySelectorAll('.sc-color-swatch').forEach(s => {
        s.addEventListener('click', () => {
            document.querySelectorAll('.sc-color-swatch').forEach(x => x.classList.remove('active'));
            s.classList.add('active');
            scRender();
        });
    });

    // Sliders with value display
    const sliders = [
        ['sc-border-weight', 'sc-border-weight-val'],
        ['sc-margin', 'sc-margin-val'],
        ['sc-title-size', 'sc-title-size-val'],
        ['sc-body-size', 'sc-body-size-val'],
        ['sc-drop-cap-size', 'sc-drop-cap-size-val'],
    ];
    sliders.forEach(([sliderId, valId]) => {
        const slider = sc(sliderId);
        const val = sc(valId);
        if (slider && val) {
            slider.addEventListener('input', () => { val.textContent = slider.value; scRender(); });
        }
    });

    // All other inputs
    const inputIds = [
        'sc-border-style', 'sc-title-text', 'sc-title-font',
        'sc-body-text', 'sc-body-font', 'sc-drop-cap', 'sc-folio',
        'sc-show-illumination', 'sc-show-ruled', 'sc-show-footer',
    ];
    inputIds.forEach(id => {
        const el = sc(id);
        if (el) {
            el.addEventListener('input', scRender);
            el.addEventListener('change', scRender);
        }
    });

    // Border generators
    function makeKnotBorder(x, y, w, h, ink, weight) {
        let paths = [];
        paths.push(`<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="none" stroke="${ink}" stroke-width="${weight*1.2}" rx="1"/>`);
        const i = 6;
        paths.push(`<rect x="${x+i}" y="${y+i}" width="${w-i*2}" height="${h-i*2}" fill="none" stroke="${ink}" stroke-width="${weight*0.6}" rx="1"/>`);

        const cornerKnot = `<path d="M0,0 L20,0 L20,20 L0,20" fill="none" stroke="${ink}" stroke-width="${weight}"/>
            <path d="M4,4 C4,12 12,4 12,12 C12,4 20,12 20,4" fill="none" stroke="${ink}" stroke-width="${weight*0.7}"/>
            <path d="M0,8 C8,8 8,16 16,16" fill="none" stroke="${ink}" stroke-width="${weight*0.5}"/>`;

        const cs = [[x,y,0],[x+w,y,90],[x+w,y+h,180],[x,y+h,270]];
        cs.forEach(([cx,cy,angle]) => {
            paths.push(`<g transform="translate(${cx},${cy}) rotate(${angle})">${cornerKnot}</g>`);
        });

        const knotSpacing = 28;
        for (let kx = x+18+knotSpacing; kx < x+w-18-knotSpacing/2; kx += knotSpacing) {
            const ky = y + 3;
            paths.push(`<path d="M${kx-5},${ky} C${kx-5},${ky+4} ${kx+5},${ky+4} ${kx+5},${ky}" fill="none" stroke="${ink}" stroke-width="${weight*0.5}"/>`);
            paths.push(`<path d="M${kx-5},${ky+6} C${kx-5},${ky+2} ${kx+5},${ky+2} ${kx+5},${ky+6}" fill="none" stroke="${ink}" stroke-width="${weight*0.5}"/>`);
            const kyb = y + h - 3;
            paths.push(`<path d="M${kx-5},${kyb} C${kx-5},${kyb-4} ${kx+5},${kyb-4} ${kx+5},${kyb}" fill="none" stroke="${ink}" stroke-width="${weight*0.5}"/>`);
            paths.push(`<path d="M${kx-5},${kyb-6} C${kx-5},${kyb-2} ${kx+5},${kyb-2} ${kx+5},${kyb-6}" fill="none" stroke="${ink}" stroke-width="${weight*0.5}"/>`);
        }
        for (let ky = y+18+knotSpacing; ky < y+h-18-knotSpacing/2; ky += knotSpacing) {
            const kxl = x + 3;
            paths.push(`<path d="M${kxl},${ky-5} C${kxl+4},${ky-5} ${kxl+4},${ky+5} ${kxl},${ky+5}" fill="none" stroke="${ink}" stroke-width="${weight*0.5}"/>`);
            paths.push(`<path d="M${kxl+6},${ky-5} C${kxl+2},${ky-5} ${kxl+2},${ky+5} ${kxl+6},${ky+5}" fill="none" stroke="${ink}" stroke-width="${weight*0.5}"/>`);
            const kxr = x + w - 3;
            paths.push(`<path d="M${kxr},${ky-5} C${kxr-4},${ky-5} ${kxr-4},${ky+5} ${kxr},${ky+5}" fill="none" stroke="${ink}" stroke-width="${weight*0.5}"/>`);
            paths.push(`<path d="M${kxr-6},${ky-5} C${kxr-2},${ky-5} ${kxr-2},${ky+5} ${kxr-6},${ky+5}" fill="none" stroke="${ink}" stroke-width="${weight*0.5}"/>`);
        }
        return paths.join('\n');
    }

    function makeVineBorder(x, y, w, h, ink, weight) {
        let paths = [], vines = [];
        paths.push(`<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="none" stroke="${ink}" stroke-width="${weight*1.5}"/>`);
        paths.push(`<rect x="${x+4}" y="${y+4}" width="${w-8}" height="${h-8}" fill="none" stroke="${ink}" stroke-width="${weight*0.5}" stroke-dasharray="3,3"/>`);

        let d = `M${x+20},${y+2}`;
        for (let vx = x+20; vx < x+w-20; vx += 24) {
            d += ` C${vx+6},${y-4} ${vx+12},${y+8} ${vx+24},${y+2}`;
            vines.push(`<circle cx="${vx+12}" cy="${y}" r="3" fill="none" stroke="${ink}" stroke-width="${weight*0.5}"/>`);
            vines.push(`<line x1="${vx+12}" y1="${y}" x2="${vx+12}" y2="${y-6}" stroke="${ink}" stroke-width="${weight*0.4}"/>`);
        }
        paths.push(`<path d="${d}" fill="none" stroke="${ink}" stroke-width="${weight}"/>`);

        let d2 = `M${x+20},${y+h-2}`;
        for (let vx = x+20; vx < x+w-20; vx += 24) {
            d2 += ` C${vx+6},${y+h+4} ${vx+12},${y+h-8} ${vx+24},${y+h-2}`;
            vines.push(`<circle cx="${vx+12}" cy="${y+h}" r="3" fill="none" stroke="${ink}" stroke-width="${weight*0.5}"/>`);
            vines.push(`<line x1="${vx+12}" y1="${y+h}" x2="${vx+12}" y2="${y+h+6}" stroke="${ink}" stroke-width="${weight*0.4}"/>`);
        }
        paths.push(`<path d="${d2}" fill="none" stroke="${ink}" stroke-width="${weight}"/>`);

        let d3 = `M${x+2},${y+20}`;
        for (let vy = y+20; vy < y+h-20; vy += 24) {
            d3 += ` C${x-4},${vy+6} ${x+8},${vy+12} ${x+2},${vy+24}`;
            vines.push(`<circle cx="${x}" cy="${vy+12}" r="3" fill="none" stroke="${ink}" stroke-width="${weight*0.5}"/>`);
        }
        paths.push(`<path d="${d3}" fill="none" stroke="${ink}" stroke-width="${weight}"/>`);

        let d4 = `M${x+w-2},${y+20}`;
        for (let vy = y+20; vy < y+h-20; vy += 24) {
            d4 += ` C${x+w+4},${vy+6} ${x+w-8},${vy+12} ${x+w-2},${vy+24}`;
            vines.push(`<circle cx="${x+w}" cy="${vy+12}" r="3" fill="none" stroke="${ink}" stroke-width="${weight*0.5}"/>`);
        }
        paths.push(`<path d="${d4}" fill="none" stroke="${ink}" stroke-width="${weight}"/>`);

        [[x,y],[x+w,y],[x+w,y+h],[x,y+h]].forEach(([cx,cy]) => {
            paths.push(`<circle cx="${cx}" cy="${cy}" r="8" fill="none" stroke="${ink}" stroke-width="${weight}"/>`);
            paths.push(`<circle cx="${cx}" cy="${cy}" r="4" fill="none" stroke="${ink}" stroke-width="${weight*0.6}"/>`);
            for (let a=0; a<360; a+=45) {
                const rad = a*Math.PI/180;
                paths.push(`<line x1="${cx}" y1="${cy}" x2="${cx+Math.cos(rad)*12}" y2="${cy+Math.sin(rad)*12}" stroke="${ink}" stroke-width="${weight*0.4}"/>`);
            }
        });
        return paths.join('\n') + vines.join('\n');
    }

    function makeGeometricBorder(x, y, w, h, ink, weight) {
        let paths = [];
        paths.push(`<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="none" stroke="${ink}" stroke-width="${weight*1.5}"/>`);
        paths.push(`<rect x="${x+5}" y="${y+5}" width="${w-10}" height="${h-10}" fill="none" stroke="${ink}" stroke-width="${weight*0.5}"/>`);
        paths.push(`<rect x="${x+2.5}" y="${y+2.5}" width="${w-5}" height="${h-5}" fill="none" stroke="${ink}" stroke-width="${weight*0.3}"/>`);

        const s = 14;
        for (let gx = x+s; gx < x+w-s; gx += s) {
            paths.push(`<polygon points="${gx},${y} ${gx+s/2},${y+5} ${gx+s},${y}" fill="none" stroke="${ink}" stroke-width="${weight*0.5}"/>`);
            paths.push(`<polygon points="${gx},${y+h} ${gx+s/2},${y+h-5} ${gx+s},${y+h}" fill="none" stroke="${ink}" stroke-width="${weight*0.5}"/>`);
        }
        for (let gy = y+s; gy < y+h-s; gy += s) {
            paths.push(`<polygon points="${x},${gy} ${x+5},${gy+s/2} ${x},${gy+s}" fill="none" stroke="${ink}" stroke-width="${weight*0.5}"/>`);
            paths.push(`<polygon points="${x+w},${gy} ${x+w-5},${gy+s/2} ${x+w},${gy+s}" fill="none" stroke="${ink}" stroke-width="${weight*0.5}"/>`);
        }

        [[x,y],[x+w,y],[x+w,y+h],[x,y+h]].forEach(([cx,cy]) => {
            const d = 12;
            paths.push(`<polygon points="${cx},${cy-d} ${cx+d},${cy} ${cx},${cy+d} ${cx-d},${cy}" fill="none" stroke="${ink}" stroke-width="${weight}"/>`);
            paths.push(`<polygon points="${cx},${cy-d/2} ${cx+d/2},${cy} ${cx},${cy+d/2} ${cx-d/2},${cy}" fill="none" stroke="${ink}" stroke-width="${weight*0.5}"/>`);
        });
        return paths.join('\n');
    }

    function makeArchBorder(x, y, w, h, ink, weight) {
        let paths = [];
        paths.push(`<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="none" stroke="${ink}" stroke-width="${weight*2}"/>`);
        paths.push(`<rect x="${x+6}" y="${y+6}" width="${w-12}" height="${h-12}" fill="none" stroke="${ink}" stroke-width="${weight*0.5}"/>`);

        const archW = 40, archH = 20, archCount = Math.floor((w-60)/archW);
        const archStart = x + (w - archCount*archW)/2;
        for (let i=0; i<archCount; i++) {
            const ax = archStart + i*archW;
            paths.push(`<path d="M${ax},${y+4} L${ax},${y+4+archH-8} Q${ax+archW/2},${y+4-8} ${ax+archW},${y+4+archH-8} L${ax+archW},${y+4}" fill="none" stroke="${ink}" stroke-width="${weight*0.6}"/>`);
            const by = y+h-4;
            paths.push(`<path d="M${ax},${by} L${ax},${by-archH+8} Q${ax+archW/2},${by+8} ${ax+archW},${by-archH+8} L${ax+archW},${by}" fill="none" stroke="${ink}" stroke-width="${weight*0.6}"/>`);
        }

        [[x,y],[x+w,y],[x+w,y+h],[x,y+h]].forEach(([cx,cy]) => {
            paths.push(`<circle cx="${cx}" cy="${cy}" r="10" fill="none" stroke="${ink}" stroke-width="${weight}"/>`);
            paths.push(`<circle cx="${cx}" cy="${cy}" r="5" fill="none" stroke="${ink}" stroke-width="${weight*0.5}"/>`);
            paths.push(`<circle cx="${cx}" cy="${cy}" r="2" fill="none" stroke="${ink}" stroke-width="${weight*0.5}"/>`);
        });
        return paths.join('\n');
    }

    function makeDoubleRuleBorder(x, y, w, h, ink, weight) {
        let paths = [];
        paths.push(`<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="none" stroke="${ink}" stroke-width="${weight*2}"/>`);
        paths.push(`<rect x="${x+4}" y="${y+4}" width="${w-8}" height="${h-8}" fill="none" stroke="${ink}" stroke-width="${weight}"/>`);
        paths.push(`<rect x="${x+8}" y="${y+8}" width="${w-16}" height="${h-16}" fill="none" stroke="${ink}" stroke-width="${weight*0.4}"/>`);

        [[x,y,1,1],[x+w,y,-1,1],[x+w,y+h,-1,-1],[x,y+h,1,-1]].forEach(([cx,cy,sx,sy]) => {
            paths.push(`<path d="M${cx+sx*4},${cy} L${cx+sx*18},${cy} M${cx},${cy+sy*4} L${cx},${cy+sy*18}" stroke="${ink}" stroke-width="${weight*1.5}"/>`);
            paths.push(`<path d="M${cx+sx*10},${cy+sy*2} L${cx+sx*2},${cy+sy*10}" stroke="${ink}" stroke-width="${weight*0.6}"/>`);
            paths.push(`<circle cx="${cx+sx*6}" cy="${cy+sy*6}" r="3" fill="none" stroke="${ink}" stroke-width="${weight}"/>`);
        });
        return paths.join('\n');
    }

    function makeBorder(style, x, y, w, h, ink, weight) {
        switch (style) {
            case 'knotwork': return makeKnotBorder(x,y,w,h,ink,weight);
            case 'vine': return makeVineBorder(x,y,w,h,ink,weight);
            case 'geometric': return makeGeometricBorder(x,y,w,h,ink,weight);
            case 'arch': return makeArchBorder(x,y,w,h,ink,weight);
            default: return makeDoubleRuleBorder(x,y,w,h,ink,weight);
        }
    }

    function makeIllumination(cx, cy, r, ink, weight) {
        let p = [];
        for (let a=0; a<360; a+=45) {
            const rad = a*Math.PI/180;
            p.push(`<line x1="${cx+Math.cos(rad)*(r-4)}" y1="${cy+Math.sin(rad)*(r-4)}" x2="${cx+Math.cos(rad)*r}" y2="${cy+Math.sin(rad)*r}" stroke="${ink}" stroke-width="${weight*0.5}"/>`);
        }
        for (let a=22.5; a<360; a+=45) {
            const rad = a*Math.PI/180;
            p.push(`<line x1="${cx+Math.cos(rad)*(r-6)}" y1="${cy+Math.sin(rad)*(r-6)}" x2="${cx+Math.cos(rad)*(r-2)}" y2="${cy+Math.sin(rad)*(r-2)}" stroke="${ink}" stroke-width="${weight*0.3}"/>`);
        }
        p.push(`<circle cx="${cx}" cy="${cy}" r="${r-8}" fill="none" stroke="${ink}" stroke-width="${weight*0.8}"/>`);
        p.push(`<circle cx="${cx}" cy="${cy}" r="${r-12}" fill="none" stroke="${ink}" stroke-width="${weight*0.4}"/>`);
        p.push(`<circle cx="${cx}" cy="${cy}" r="${r-16}" fill="none" stroke="${ink}" stroke-width="${weight*0.6}"/>`);
        for (let a=0; a<360; a+=60) {
            const rad = a*Math.PI/180;
            const px = cx + Math.cos(rad)*((r-16)/2);
            const py = cy + Math.sin(rad)*((r-16)/2);
            p.push(`<ellipse cx="${px}" cy="${py}" rx="${(r-16)*0.35}" ry="${(r-16)*0.2}" transform="rotate(${a},${px},${py})" fill="none" stroke="${ink}" stroke-width="${weight*0.4}"/>`);
        }
        return p.join('\n');
    }

    function wrapText(text, maxCharsPerLine) {
        const words = text.split(' ');
        const lines = [];
        let cur = '';
        words.forEach(w => {
            if ((cur + ' ' + w).trim().length <= maxCharsPerLine) {
                cur = (cur + ' ' + w).trim();
            } else {
                if (cur) lines.push(cur);
                cur = w;
            }
        });
        if (cur) lines.push(cur);
        return lines;
    }

    function escHtml(s) {
        return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }

    // Main render
    let lastPlotterSVG = '';

    function scRender() {
        const W = scPageW * PX_PER_MM;
        const H = scPageH * PX_PER_MM;
        const margin = parseFloat(sc('sc-margin').value) * PX_PER_MM;
        const ink = document.querySelector('.sc-color-swatch.active')?.dataset.color || '#1a0f08';
        const bw = parseFloat(sc('sc-border-weight').value);
        const borderStyle = sc('sc-border-style').value;
        const titleText = sc('sc-title-text').value || 'Codex';
        const titleSize = parseInt(sc('sc-title-size').value);
        const titleFont = sc('sc-title-font').value;
        const bodyText = sc('sc-body-text').value;
        const bodyFont = sc('sc-body-font').value;
        const bodySize = parseInt(sc('sc-body-size').value);
        const dropCap = sc('sc-drop-cap').value || 'H';
        const dropCapSize = parseInt(sc('sc-drop-cap-size').value);
        const folioNum = sc('sc-folio').value;
        const showIllumination = sc('sc-show-illumination').checked;
        const showRuledLines = sc('sc-show-ruled').checked;
        const showFooter = sc('sc-show-footer').checked;

        const bx = margin, by = margin, bw2 = W - margin*2, bh2 = H - margin*2;
        const contentX = bx + 18, contentY = by + 22;
        const contentW = bw2 - 36;

        const borderSVG = makeBorder(borderStyle, bx, by, bw2, bh2, ink, bw);

        let illumSVG = '';
        if (showIllumination) {
            const r = 22;
            [[bx+r,by+r],[bx+bw2-r,by+r],[bx+bw2-r,by+bh2-r],[bx+r,by+bh2-r]].forEach(([cx,cy]) => {
                illumSVG += makeIllumination(cx, cy, r, ink, bw);
            });
        }

        const titleY = contentY + titleSize + 8;
        const titleSVG = `<text x="${W/2}" y="${titleY}" text-anchor="middle" font-family="${titleFont}" font-size="${titleSize}" fill="${ink}" letter-spacing="2">${escHtml(titleText)}</text>`;

        const tlw = titleText.length * titleSize * 0.5;
        const flourishSVG = `
            <line x1="${W/2 - tlw/2}" y1="${titleY+8}" x2="${W/2 + tlw/2}" y2="${titleY+8}" stroke="${ink}" stroke-width="${bw*0.6}"/>
            <line x1="${W/2 - tlw/2 - 10}" y1="${titleY+8}" x2="${W/2 + tlw/2 + 10}" y2="${titleY+8}" stroke="${ink}" stroke-width="${bw*0.3}"/>
            <path d="M${W/2-tlw/2-10},${titleY+12} Q${W/2},${titleY+18} ${W/2+tlw/2+10},${titleY+12}" fill="none" stroke="${ink}" stroke-width="${bw*0.4}"/>
            <circle cx="${W/2}" cy="${titleY+18}" r="2.5" fill="none" stroke="${ink}" stroke-width="${bw*0.5}"/>
        `;

        const textStartY = titleY + 32;
        const dropCapW = dropCapSize * 0.65;
        const textIndent = contentX + dropCapW + 6;
        const lineHeight = bodySize * 1.55;

        let ruledSVG = '';
        if (showRuledLines) {
            const ruledEnd = by + bh2 - 40;
            for (let ly = textStartY; ly < ruledEnd; ly += lineHeight) {
                ruledSVG += `<line x1="${contentX}" y1="${ly}" x2="${contentX+contentW}" y2="${ly}" stroke="${ink}" stroke-width="0.3" opacity="0.25"/>`;
            }
        }

        const dropCapSVG = `<text x="${contentX}" y="${textStartY + dropCapSize * 0.8}" font-family="${titleFont}" font-size="${dropCapSize}" fill="${ink}">${escHtml(dropCap)}</text>`;

        const paragraphs = bodyText.split('\n\n');
        let allLines = [];

        if (paragraphs[0]) {
            const para0words = paragraphs[0].replace(new RegExp('^'+dropCap.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')), '').trim().split(' ');
            const shortMaxChars = Math.floor((contentX + contentW - textIndent) / (bodySize * 0.55));
            const fullMaxChars = Math.floor(contentW / (bodySize * 0.55));
            const droplinesCount = Math.ceil((dropCapSize * 0.85) / lineHeight);
            let cur = '';
            let firstParaLines = [];

            para0words.forEach(w => {
                const llen = firstParaLines.length < droplinesCount ? shortMaxChars : fullMaxChars;
                if ((cur + ' ' + w).trim().length <= llen) {
                    cur = (cur + ' ' + w).trim();
                } else {
                    if (cur) firstParaLines.push({ text: cur, short: firstParaLines.length < droplinesCount });
                    cur = w;
                }
            });
            if (cur) firstParaLines.push({ text: cur, short: firstParaLines.length < droplinesCount });
            allLines.push(...firstParaLines.map((l,i) => ({ ...l, y: textStartY + i * lineHeight })));
        }

        let nextY = textStartY + allLines.length * lineHeight + lineHeight * 0.5;
        for (let pi = 1; pi < paragraphs.length; pi++) {
            const maxChars = Math.floor(contentW / (bodySize * 0.55));
            const plines = wrapText(paragraphs[pi], maxChars);
            plines.forEach((l, i) => {
                allLines.push({ text: l, short: false, y: nextY + i * lineHeight });
            });
            nextY += plines.length * lineHeight + lineHeight * 0.6;
        }

        const bodySVG = allLines.map(l => {
            const lx = l.short ? textIndent : contentX;
            return `<text x="${lx}" y="${l.y}" font-family="${bodyFont}" font-size="${bodySize}" fill="${ink}">${escHtml(l.text)}</text>`;
        }).join('\n');

        let footerSVG = '';
        if (showFooter) {
            const fy = by + bh2 - 18;
            const fw = contentW / 3;
            footerSVG = `
                <path d="M${W/2-fw/2},${fy} Q${W/2-fw/4},${fy-8} ${W/2},${fy-2} Q${W/2+fw/4},${fy+6} ${W/2+fw/2},${fy}" fill="none" stroke="${ink}" stroke-width="${bw*0.5}"/>
                <circle cx="${W/2}" cy="${fy-5}" r="3" fill="none" stroke="${ink}" stroke-width="${bw*0.5}"/>
                <circle cx="${W/2-fw/2+5}" cy="${fy}" r="2" fill="none" stroke="${ink}" stroke-width="${bw*0.4}"/>
                <circle cx="${W/2+fw/2-5}" cy="${fy}" r="2" fill="none" stroke="${ink}" stroke-width="${bw*0.4}"/>
                <text x="${W/2}" y="${fy+12}" text-anchor="middle" font-family="'Cinzel', serif" font-size="9" fill="${ink}" letter-spacing="3">${escHtml(folioNum)}</text>
                <line x1="${W/2-30}" y1="${fy+13}" x2="${W/2-12}" y2="${fy+13}" stroke="${ink}" stroke-width="0.5"/>
                <line x1="${W/2+12}" y1="${fy+13}" x2="${W/2+30}" y2="${fy+13}" stroke="${ink}" stroke-width="0.5"/>
            `;
        }

        // Preview SVG (visual)
        const previewSVG = `<svg xmlns="http://www.w3.org/2000/svg" width="${Math.round(W)}" height="${Math.round(H)}" viewBox="0 0 ${Math.round(W)} ${Math.round(H)}">
  <defs><style>
    @import url('https://fonts.googleapis.com/css2?family=UnifrakturMaguntia&amp;family=Cinzel+Decorative:wght@400;700&amp;family=Cinzel:wght@400;600&amp;family=IM+Fell+English:ital@0;1&amp;display=swap');
  </style></defs>
  <rect width="${Math.round(W)}" height="${Math.round(H)}" fill="#fdf8ef"/>
  ${borderSVG}
  ${illumSVG}
  ${ruledSVG}
  ${titleSVG}
  ${flourishSVG}
  ${dropCapSVG}
  ${bodySVG}
  ${footerSVG}
</svg>`;

        sc('scriptorium-svg-container').innerHTML = previewSVG;

        // Plotter-safe SVG (stroke-only, mm dimensions)
        lastPlotterSVG = `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="${scPageW}mm" height="${scPageH}mm" viewBox="0 0 ${Math.round(W)} ${Math.round(H)}">
  <defs><style>
    @import url('https://fonts.googleapis.com/css2?family=UnifrakturMaguntia&amp;family=Cinzel+Decorative:wght@400;700&amp;family=Cinzel:wght@400;600&amp;family=IM+Fell+English:ital@0;1&amp;display=swap');
  </style></defs>
  ${borderSVG}
  ${illumSVG}
  ${ruledSVG}
  ${titleSVG}
  ${flourishSVG}
  ${dropCapSVG}
  ${bodySVG}
  ${footerSVG}
</svg>`;
    }

    // Send to Plotter
    sc('sc-send-to-plotter').addEventListener('click', () => {
        if (!lastPlotterSVG) return toast('Generate a page first', 'warn');
        const blob = new Blob([lastPlotterSVG], { type: 'image/svg+xml' });
        const file = new File([blob], 'scriptorium_page.svg', { type: 'image/svg+xml' });

        const fd = new FormData();
        fd.append('file', file);

        api('/api/upload', { method: 'POST', body: fd })
            .then(r => r.json())
            .then(data => {
                if (data.error) return toast(data.error, 'error');
                state.currentSvgId = data.id;
                state.toolpath = [];

                const info = document.getElementById('upload-info');
                info.classList.remove('hidden');
                info.innerHTML = `Uploaded: <span class="filename">scriptorium_page.svg</span> · ${data.stroke_count} strokes`;

                document.getElementById('btn-convert').disabled = false;

                if (data.polylines) {
                    drawPreview(data.polylines);
                    document.getElementById('preview-info').textContent = `${data.stroke_count} strokes`;
                }

                toast('Scriptorium SVG loaded — switch to Plot panel', 'success');

                // Switch to Plot panel
                document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
                document.querySelectorAll('.panel-content').forEach(p => p.classList.remove('active'));
                document.querySelector('.nav-item[data-panel="plot"]').classList.add('active');
                document.getElementById('panel-plot').classList.add('active');
            })
            .catch(() => toast('Upload failed', 'error'));
    });

    // Download SVG
    sc('sc-download-svg').addEventListener('click', () => {
        if (!lastPlotterSVG) return toast('Generate a page first', 'warn');
        const blob = new Blob([lastPlotterSVG], { type: 'image/svg+xml' });
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = 'scriptorium_page.svg';
        a.click();
        URL.revokeObjectURL(a.href);
    });

    // Initial render
    applyPreset();
}

// ── Toon Tracer ─────────────────────────────────────────────────────
function initToon() {
    const el = (id) => document.getElementById(id);

    // State
    let toonImageFile = null;
    let toonLastData = null;
    let toonPageW = 220, toonPageH = 220;

    // Elements
    const dropZone = el('toon-drop-zone');
    const fileInput = el('toon-image-input');
    const canvas = el('toon-canvas');
    const ctx = canvas.getContext('2d');
    const traceInfo = el('toon-trace-info');

    // Page size (independent from plot panel)
    const presetSel = el('toon-page-preset');
    const widthInput = el('toon-page-width');
    const heightInput = el('toon-page-height');

    function applyToonPreset() {
        const p = presetSel.value;
        if (PAGE_PRESETS[p]) {
            const [w, h] = PAGE_PRESETS[p];
            widthInput.value = w;
            heightInput.value = h;
            widthInput.disabled = true;
            heightInput.disabled = true;
        } else {
            widthInput.disabled = false;
            heightInput.disabled = false;
        }
        toonPageW = parseInt(widthInput.value);
        toonPageH = parseInt(heightInput.value);
    }

    presetSel.addEventListener('change', applyToonPreset);
    widthInput.addEventListener('change', () => { presetSel.value = 'Custom'; widthInput.disabled = false; heightInput.disabled = false; toonPageW = parseInt(widthInput.value); });
    heightInput.addEventListener('change', () => { presetSel.value = 'Custom'; widthInput.disabled = false; heightInput.disabled = false; toonPageH = parseInt(heightInput.value); });

    // Sliders with value display
    const sliders = [
        ['toon-canny-low', 'toon-canny-low-val'],
        ['toon-canny-high', 'toon-canny-high-val'],
        ['toon-blur', 'toon-blur-val'],
        ['toon-posterize', 'toon-posterize-val'],
        ['toon-epsilon', 'toon-epsilon-val'],
        ['toon-min-len', 'toon-min-len-val'],
    ];
    sliders.forEach(([sliderId, valId]) => {
        const slider = el(sliderId);
        const valSpan = el(valId);
        if (slider && valSpan) {
            slider.addEventListener('input', () => { valSpan.textContent = slider.value; });
        }
    });

    // Drop zone — click to browse
    dropZone.addEventListener('click', () => fileInput.click());

    // Drag-drop
    dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('dragover'); });
    dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('dragover');
        const file = e.dataTransfer.files[0];
        if (file && file.type.startsWith('image/')) {
            setImage(file);
        }
    });

    fileInput.addEventListener('change', () => {
        if (fileInput.files[0]) {
            setImage(fileInput.files[0]);
        }
    });

    function setImage(file) {
        toonImageFile = file;
        toonLastData = null;
        dropZone.querySelector('.drop-zone-content').innerHTML =
            `<div style="color:var(--green);font-weight:600">${file.name}</div>
             <div style="font-size:10px;color:var(--text-2);margin-top:4px">${(file.size / 1024).toFixed(1)} KB</div>`;
        el('btn-toon-trace').disabled = false;
        el('btn-toon-retrace').disabled = false;
        el('btn-toon-send').disabled = true;
        el('btn-toon-download').disabled = true;
        traceInfo.textContent = '';
        traceImage();
    }

    // Trace
    function traceImage() {
        if (!toonImageFile) return;

        toonPageW = parseInt(widthInput.value);
        toonPageH = parseInt(heightInput.value);

        const fd = new FormData();
        fd.append('file', toonImageFile);
        fd.append('canny_low', el('toon-canny-low').value);
        fd.append('canny_high', el('toon-canny-high').value);
        fd.append('blur', el('toon-blur').value);
        fd.append('posterize', el('toon-posterize').value);
        fd.append('epsilon', el('toon-epsilon').value);
        fd.append('min_contour_length', el('toon-min-len').value);
        fd.append('invert', el('toon-invert').checked ? 'true' : 'false');
        fd.append('page_width', toonPageW);
        fd.append('page_height', toonPageH);

        el('btn-toon-trace').disabled = true;
        el('btn-toon-retrace').disabled = true;
        traceInfo.textContent = 'Tracing...';

        api('/api/trace', { method: 'POST', body: fd })
            .then(r => r.json())
            .then(data => {
                if (data.error) {
                    toast(data.error, 'error');
                    traceInfo.textContent = '';
                    el('btn-toon-trace').disabled = false;
                    el('btn-toon-retrace').disabled = false;
                    return;
                }
                toonLastData = data;
                drawToonPreview(data);
                traceInfo.textContent = `${data.stroke_count} strokes | ${data.image_size[0]}x${data.image_size[1]}`;
                el('btn-toon-trace').disabled = false;
                el('btn-toon-retrace').disabled = false;
                el('btn-toon-send').disabled = false;
                el('btn-toon-download').disabled = false;
                toast(`Traced: ${data.stroke_count} strokes`, 'success');
            })
            .catch(err => {
                toast('Trace failed: ' + err.message, 'error');
                traceInfo.textContent = '';
                el('btn-toon-trace').disabled = false;
                el('btn-toon-retrace').disabled = false;
            });
    }

    // Preview drawing
    function drawToonPreview(data) {
        const cw = canvas.width;
        const ch = canvas.height;
        ctx.fillStyle = '#0a0c10';
        ctx.fillRect(0, 0, cw, ch);

        if (!data.polylines || !data.polylines.length) return;

        const imgW = data.image_size[0];
        const imgH = data.image_size[1];

        // Auto-fit polylines to canvas
        let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        for (const pl of data.polylines) {
            for (const [px, py] of pl) {
                if (px < minX) minX = px;
                if (py < minY) minY = py;
                if (px > maxX) maxX = px;
                if (py > maxY) maxY = py;
            }
        }
        const dw = maxX - minX || 1;
        const dh = maxY - minY || 1;
        const pad = 20;
        const scaleX = (cw - 2 * pad) / dw;
        const scaleY = (ch - 2 * pad) / dh;
        const scaleF = Math.min(scaleX, scaleY);
        const offX = pad + (cw - 2 * pad - dw * scaleF) / 2 - minX * scaleF;
        const offY = pad + (ch - 2 * pad - dh * scaleF) / 2 - minY * scaleF;

        ctx.strokeStyle = '#00e87b';
        ctx.lineWidth = 0.8;
        ctx.lineJoin = 'round';
        ctx.beginPath();
        for (const pl of data.polylines) {
            if (pl.length < 2) continue;
            ctx.moveTo(offX + pl[0][0] * scaleF, offY + pl[0][1] * scaleF);
            for (let i = 1; i < pl.length; i++) {
                ctx.lineTo(offX + pl[i][0] * scaleF, offY + pl[i][1] * scaleF);
            }
        }
        ctx.stroke();
    }

    // Button handlers
    el('btn-toon-trace').addEventListener('click', traceImage);
    el('btn-toon-retrace').addEventListener('click', traceImage);

    // Send to Plotter — SVG is already server-side in uploaded_svgs
    el('btn-toon-send').addEventListener('click', () => {
        if (!toonLastData) return toast('Trace an image first', 'warn');

        state.currentSvgId = toonLastData.id;
        state.toolpath = [];
        state.gcodeGenerated = false;

        // Parse the SVG for preview
        api(`/api/upload`, { method: 'POST' })
            .then(() => {
                // The SVG is already in uploaded_svgs, just trigger the preview
                const info = document.getElementById('upload-info');
                info.classList.remove('hidden');
                info.innerHTML = `Traced image — <span class="filename">${toonLastData.stroke_count} strokes</span>`;

                document.getElementById('btn-convert').disabled = false;
                document.getElementById('convert-result').classList.add('hidden');

                // Draw preview
                const previewPolylines = toonLastData.polylines;
                // Convert pixel polylines to the format expected by drawPreview
                // The SVG is already uploaded, so we use the normal convert flow
                api(`/api/convert`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        id: toonLastData.id,
                        tool: state.plotTool || 'pencil',
                        page_width: toonPageW,
                        page_height: toonPageH,
                    }),
                })
                    .then(r => r.json())
                    .then(convertData => {
                        if (convertData.error) return toast(convertData.error, 'error');
                        state.currentSvgId = convertData.id;
                        state.gcodeGenerated = true;

                        // Update preview
                        const preview = convertData.polylines;
                        drawPreview(preview, convertData.toolpath);

                        // Update stats
                        if (convertData.stats) {
                            document.getElementById('stat-strokes').textContent = convertData.stats.stroke_count;
                            document.getElementById('stat-draw').textContent = convertData.stats.draw_distance_mm.toFixed(1) + 'mm';
                            document.getElementById('stat-travel').textContent = convertData.stats.travel_distance_mm.toFixed(1) + 'mm';
                            document.getElementById('stat-time').textContent = Math.ceil(convertData.stats.estimated_time_s) + 's';
                        }

                        // G-code preview
                        if (convertData.gcode_preview) {
                            document.getElementById('gcode-preview').textContent = convertData.gcode_preview;
                            document.getElementById('gcode-lines').textContent = convertData.line_count + ' lines';
                        }

                        // Show result
                        const result = document.getElementById('convert-result');
                        result.classList.remove('hidden');
                        result.textContent = `Converted: ${convertData.stats?.stroke_count || '?'} strokes, ${convertData.line_count} G-code lines`;

                        // Switch to Plot panel
                        document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
                        document.querySelectorAll('.panel-content').forEach(p => p.classList.remove('active'));
                        document.querySelector('.nav-item[data-panel="plot"]').classList.add('active');
                        document.getElementById('panel-plot').classList.add('active');

                        document.getElementById('btn-print').disabled = false;
                        toast('Sent to Plotter — ready to print', 'success');
                    });
            });
    });

    // Download SVG
    el('btn-toon-download').addEventListener('click', () => {
        if (!toonLastData) return toast('Trace an image first', 'warn');
        window.location.href = `/api/trace-svg/${toonLastData.id}`;
    });
}

// ── Ink (Hand Writing) ──────────────────────────────────────────────
function initInk() {
    const el = (id) => document.getElementById(id);

    // State
    const strokes = [];          // [{points: [{x, y, pressure}]}]
    let currentStroke = null;
    let isDrawing = false;
    let inkPageW = 220, inkPageH = 220;

    // Elements
    const canvas = el('ink-canvas');
    const ctx = canvas.getContext('2d');
    const strokeInfo = el('ink-stroke-info');

    // Page size
    const presetSel = el('ink-page-preset');
    const widthInput = el('ink-page-width');
    const heightInput = el('ink-page-height');

    function applyInkPreset() {
        const p = presetSel.value;
        if (PAGE_PRESETS[p]) {
            const [w, h] = PAGE_PRESETS[p];
            widthInput.value = w;
            heightInput.value = h;
            widthInput.disabled = true;
            heightInput.disabled = true;
        } else {
            widthInput.disabled = false;
            heightInput.disabled = false;
        }
        inkPageW = parseInt(widthInput.value);
        inkPageH = parseInt(heightInput.value);
        resizeCanvas();
    }

    presetSel.addEventListener('change', applyInkPreset);
    widthInput.addEventListener('change', () => { presetSel.value = 'Custom'; widthInput.disabled = false; heightInput.disabled = false; inkPageW = parseInt(widthInput.value); resizeCanvas(); });
    heightInput.addEventListener('change', () => { presetSel.value = 'Custom'; widthInput.disabled = false; heightInput.disabled = false; inkPageH = parseInt(heightInput.value); resizeCanvas(); });

    // Slider value displays
    el('ink-stroke-width').addEventListener('input', function() { el('ink-stroke-width-val').textContent = this.value; });
    el('ink-smoothing').addEventListener('input', function() { el('ink-smoothing-val').textContent = this.value; });

    // Canvas sizing — fill available space, maintain page aspect ratio
    function resizeCanvas() {
        const wrap = canvas.parentElement;
        const maxW = wrap.clientWidth;
        const maxH = wrap.clientHeight || 500;
        const aspect = inkPageW / inkPageH;
        let cw, ch;
        if (maxW / maxH > aspect) {
            ch = maxH;
            cw = ch * aspect;
        } else {
            cw = maxW;
            ch = cw / aspect;
        }
        canvas.width = Math.round(cw);
        canvas.height = Math.round(ch);
        canvas.style.width = canvas.width + 'px';
        canvas.style.height = canvas.height + 'px';
        redraw();
    }

    // Observe panel becoming visible to resize canvas
    const observer = new MutationObserver(() => {
        if (el('panel-ink').classList.contains('active')) {
            setTimeout(resizeCanvas, 50);
        }
    });
    observer.observe(el('panel-ink'), { attributes: true, attributeFilter: ['class'] });

    // Drawing
    function getPos(e) {
        const rect = canvas.getBoundingClientRect();
        return {
            x: (e.clientX - rect.left) / rect.width * inkPageW,
            y: (e.clientY - rect.top) / rect.height * inkPageH,
            pressure: e.pressure || 0.5,
        };
    }

    canvas.addEventListener('pointerdown', (e) => {
        e.preventDefault();
        canvas.setPointerCapture(e.pointerId);
        isDrawing = true;
        currentStroke = { points: [getPos(e)] };
        // Draw dot
        const p = getPos(e);
        const baseWidth = parseFloat(el('ink-stroke-width').value);
        ctx.fillStyle = '#00e87b';
        ctx.beginPath();
        ctx.arc(p.x / inkPageW * canvas.width, p.y / inkPageH * canvas.height, baseWidth, 0, Math.PI * 2);
        ctx.fill();
    });

    canvas.addEventListener('pointermove', (e) => {
        if (!isDrawing || !currentStroke) return;
        e.preventDefault();
        const pt = getPos(e);
        const prev = currentStroke.points[currentStroke.points.length - 1];

        // Skip duplicate points
        if (Math.abs(pt.x - prev.x) < 0.1 && Math.abs(pt.y - prev.y) < 0.1) return;

        currentStroke.points.push(pt);

        // Draw segment
        const usePressure = el('ink-pressure').checked;
        const baseWidth = parseFloat(el('ink-stroke-width').value);
        const lw = usePressure ? baseWidth * (0.3 + pt.pressure * 1.4) : baseWidth;

        ctx.strokeStyle = '#00e87b';
        ctx.lineWidth = lw * (canvas.width / inkPageW);
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        ctx.beginPath();
        ctx.moveTo(prev.x / inkPageW * canvas.width, prev.y / inkPageH * canvas.height);
        ctx.lineTo(pt.x / inkPageW * canvas.width, pt.y / inkPageH * canvas.height);
        ctx.stroke();
    });

    canvas.addEventListener('pointerup', (e) => {
        if (!isDrawing || !currentStroke) return;
        isDrawing = false;
        if (currentStroke.points.length >= 2) {
            // Apply smoothing
            const sm = parseInt(el('ink-smoothing').value);
            if (sm > 0) {
                currentStroke.points = smoothPoints(currentStroke.points, sm);
            }
            strokes.push(currentStroke);
        }
        currentStroke = null;
        updateStrokeUI();
        redraw();
    });

    canvas.addEventListener('pointerleave', (e) => {
        if (!isDrawing || !currentStroke) return;
        isDrawing = false;
        if (currentStroke.points.length >= 2) {
            const sm = parseInt(el('ink-smoothing').value);
            if (sm > 0) {
                currentStroke.points = smoothPoints(currentStroke.points, sm);
            }
            strokes.push(currentStroke);
        }
        currentStroke = null;
        updateStrokeUI();
        redraw();
    });

    // Smoothing — simple moving average
    function smoothPoints(pts, radius) {
        if (pts.length <= radius * 2 + 1) return pts;
        const out = [pts[0]];
        for (let i = 1; i < pts.length - 1; i++) {
            let sx = 0, sy = 0, sp = 0, count = 0;
            for (let j = Math.max(0, i - radius); j <= Math.min(pts.length - 1, i + radius); j++) {
                sx += pts[j].x;
                sy += pts[j].y;
                sp += pts[j].pressure;
                count++;
            }
            out.push({ x: sx / count, y: sy / count, pressure: sp / count });
        }
        out.push(pts[pts.length - 1]);
        return out;
    }

    // Redraw all strokes
    function redraw() {
        ctx.fillStyle = '#0a0c10';
        ctx.fillRect(0, 0, canvas.width, canvas.height);

        // Draw page margin border
        const mx = 10 / inkPageW * canvas.width;
        const my = 10 / inkPageH * canvas.height;
        ctx.strokeStyle = '#1e232b';
        ctx.lineWidth = 1;
        ctx.strokeRect(mx, my, canvas.width - 2 * mx, canvas.height - 2 * my);

        const usePressure = el('ink-pressure').checked;
        const baseWidth = parseFloat(el('ink-stroke-width').value);

        for (const stroke of strokes) {
            if (stroke.points.length < 2) continue;
            ctx.strokeStyle = '#00e87b';
            ctx.lineCap = 'round';
            ctx.lineJoin = 'round';

            for (let i = 1; i < stroke.points.length; i++) {
                const prev = stroke.points[i - 1];
                const pt = stroke.points[i];
                const lw = usePressure ? baseWidth * (0.3 + pt.pressure * 1.4) : baseWidth;
                ctx.lineWidth = lw * (canvas.width / inkPageW);
                ctx.beginPath();
                ctx.moveTo(prev.x / inkPageW * canvas.width, prev.y / inkPageH * canvas.height);
                ctx.lineTo(pt.x / inkPageW * canvas.width, pt.y / inkPageH * canvas.height);
                ctx.stroke();
            }
        }
    }

    function updateStrokeUI() {
        const n = strokes.length;
        strokeInfo.textContent = n ? `${n} stroke${n > 1 ? 's' : ''}` : '';
        el('btn-ink-undo').disabled = n === 0;
        el('btn-ink-clear').disabled = n === 0;
        el('btn-ink-send').disabled = n === 0;
        el('btn-ink-download').disabled = n === 0;
    }

    // Undo
    el('btn-ink-undo').addEventListener('click', () => {
        strokes.pop();
        updateStrokeUI();
        redraw();
    });

    // Clear
    el('btn-ink-clear').addEventListener('click', () => {
        strokes.length = 0;
        updateStrokeUI();
        redraw();
    });

    // Build SVG from strokes
    function buildSVG() {
        const w = inkPageW;
        const h = inkPageH;
        let parts = [`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w} ${h}" width="${w}mm" height="${h}mm">`];
        const usePressure = el('ink-pressure').checked;
        const baseWidth = parseFloat(el('ink-stroke-width').value);

        for (const stroke of strokes) {
            if (stroke.points.length < 2) continue;
            // Build polyline points — flip Y for plotter (bottom-left origin)
            const pts = stroke.points.map(p => {
                const px = round2(p.x);
                const py = round2(h - p.y);  // flip Y
                return `${px},${py}`;
            }).join(' ');

            const sw = usePressure ? baseWidth * 0.8 : baseWidth;
            parts.push(`<polyline points="${pts}" fill="none" stroke="black" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round"/>`);
        }

        parts.push('</svg>');
        return parts.join('\n');
    }

    function round2(n) { return Math.round(n * 100) / 100; }

    // Send to Plotter
    el('btn-ink-send').addEventListener('click', () => {
        if (!strokes.length) return toast('Draw something first', 'warn');

        const svgStr = buildSVG();
        const blob = new Blob([svgStr], { type: 'image/svg+xml' });
        const file = new File([blob], 'handwriting.svg', { type: 'image/svg+xml' });

        const fd = new FormData();
        fd.append('file', file);

        api('/api/upload', { method: 'POST', body: fd })
            .then(r => r.json())
            .then(data => {
                if (data.error) return toast(data.error, 'error');
                state.currentSvgId = data.id;
                state.toolpath = [];

                // Convert
                return api('/api/convert', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        id: data.id,
                        tool: state.plotTool || 'pencil',
                        page_width: inkPageW,
                        page_height: inkPageH,
                    }),
                }).then(r => r.json());
            })
            .then(data => {
                if (data.error) return toast(data.error, 'error');
                state.currentSvgId = data.id;
                state.gcodeGenerated = true;

                drawPreview(data.polylines, data.toolpath);

                if (data.stats) {
                    el('stat-strokes').textContent = data.stats.stroke_count;
                    el('stat-draw').textContent = data.stats.draw_distance_mm.toFixed(1) + 'mm';
                    el('stat-travel').textContent = data.stats.travel_distance_mm.toFixed(1) + 'mm';
                    el('stat-time').textContent = Math.ceil(data.stats.estimated_time_s) + 's';
                }
                if (data.gcode_preview) {
                    el('gcode-preview').textContent = data.gcode_preview;
                    el('gcode-lines').textContent = data.line_count + ' lines';
                }

                const result = el('convert-result');
                result.classList.remove('hidden');
                result.textContent = `Converted: ${data.stats?.stroke_count || '?'} strokes, ${data.line_count} G-code lines`;

                // Switch to Plot panel
                document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
                document.querySelectorAll('.panel-content').forEach(p => p.classList.remove('active'));
                document.querySelector('.nav-item[data-panel="plot"]').classList.add('active');
                el('panel-plot').classList.add('active');
                el('btn-print').disabled = false;
                toast('Sent to Plotter — ready to print', 'success');
            })
            .catch(err => toast('Failed: ' + err.message, 'error'));
    });

    // Download SVG
    el('btn-ink-download').addEventListener('click', () => {
        if (!strokes.length) return toast('Draw something first', 'warn');
        const svgStr = buildSVG();
        const blob = new Blob([svgStr], { type: 'image/svg+xml' });
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = 'handwriting.svg';
        a.click();
        URL.revokeObjectURL(a.href);
    });

    // ── Wacom Slate integration ──────────────────────────────────
    const slateStatus = el('slate-status');
    const btnConnect = el('btn-slate-connect');
    const btnStop = el('btn-slate-stop');
    const btnSync = el('btn-slate-sync');
    const btnLoad = el('btn-slate-load');
    const pagesSelect = el('slate-pages-select');
    let slatePollId = null;

    // Global callback for WebSocket ink messages
    window._onInkStroke = (points) => {
        // Convert Wacom coords (21600×14700) → page mm
        const stroke = points.map(([x, y, p]) => ({
            x: x / 21600 * inkPageW,
            y: (1 - y / 14700) * inkPageH,
            pressure: p / 1024,
        }));
        strokes.push({ points: stroke });
        redraw();
        updateStrokeUI();
    };

    function setSlateStatus(text, color) {
        slateStatus.innerHTML = `<span style="color:var(--${color || 'text-2'})">${text}</span>`;
    }

    function slatePollStatus() {
        api('/api/ink/status').then(r => r.json()).then(data => {
            if (data.capturing) {
                setSlateStatus('Capturing...', 'green');
                btnConnect.disabled = true;
                btnStop.disabled = false;
            } else {
                setSlateStatus('Disconnected', 'text-2');
                btnConnect.disabled = false;
                btnStop.disabled = true;
                clearInterval(slatePollId);
                slatePollId = null;
            }
        });
    }

    // Connect & Capture
    btnConnect.addEventListener('click', () => {
        btnConnect.disabled = true;
        setSlateStatus('Connecting...', 'text-1');
        api('/api/ink/capture', { method: 'POST' })
            .then(r => r.json())
            .then(data => {
                if (data.error) {
                    toast(data.error, 'error');
                    setSlateStatus('Error', 'red');
                    btnConnect.disabled = false;
                    return;
                }
                setSlateStatus('Capturing...', 'green');
                btnStop.disabled = false;
                toast('Slate connected — draw on the pad', 'success');
                slatePollId = setInterval(slatePollStatus, 2000);
            })
            .catch(err => {
                toast('Failed to start capture: ' + err.message, 'error');
                setSlateStatus('Error', 'red');
                btnConnect.disabled = false;
            });
    });

    // Stop & Save
    btnStop.addEventListener('click', () => {
        btnStop.disabled = true;
        setSlateStatus('Saving...', 'text-1');
        api('/api/ink/stop', { method: 'POST' })
            .then(r => r.json())
            .then(data => {
                btnConnect.disabled = false;
                if (data.error) {
                    toast(data.error, 'error');
                    setSlateStatus('Disconnected', 'text-2');
                    return;
                }
                setSlateStatus('Saved', 'green');
                state.currentSvgId = data.id;
                state.gcodeGenerated = false;
                toast(`Captured ${data.stroke_count} points — ready to send`, 'success');

                // Auto-send to plotter pipeline
                api('/api/convert', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        id: data.id,
                        tool: state.plotTool || 'pencil',
                        page_width: inkPageW,
                        page_height: inkPageH,
                    }),
                })
                .then(r => r.json())
                .then(cdata => {
                    if (cdata.error) return toast(cdata.error, 'error');
                    state.currentSvgId = cdata.id;
                    state.gcodeGenerated = true;
                    drawPreview(cdata.polylines, cdata.toolpath);
                    if (cdata.stats) {
                        el('stat-strokes').textContent = cdata.stats.stroke_count;
                        el('stat-draw').textContent = cdata.stats.draw_distance_mm.toFixed(1) + 'mm';
                        el('stat-travel').textContent = cdata.stats.travel_distance_mm.toFixed(1) + 'mm';
                        el('stat-time').textContent = Math.ceil(cdata.stats.estimated_time_s) + 's';
                    }
                    if (cdata.gcode_preview) {
                        el('gcode-preview').textContent = cdata.gcode_preview;
                        el('gcode-lines').textContent = cdata.line_count + ' lines';
                    }
                    el('btn-print').disabled = false;
                    toast('Converted — switch to Plot to start', 'success');
                });
            })
            .catch(err => {
                toast('Stop failed: ' + err.message, 'error');
                setSlateStatus('Disconnected', 'text-2');
                btnConnect.disabled = false;
            });
    });

    // Sync Pages
    btnSync.addEventListener('click', () => {
        btnSync.disabled = true;
        toast('Syncing pages from device...', 'info');
        api('/api/ink/sync', { method: 'POST' })
            .then(r => r.json())
            .then(data => {
                btnSync.disabled = false;
                if (data.ok) {
                    toast('Sync started — check device. Load pages when done.', 'success');
                    // Enable load after a delay for sync to finish
                    setTimeout(() => { btnLoad.disabled = false; }, 5000);
                }
            })
            .catch(() => { btnSync.disabled = false; });
    });

    // Load Page
    btnLoad.addEventListener('click', () => {
        api('/api/ink/pages').then(r => r.json()).then(data => {
            const pages = data.pages || [];
            if (!pages.length) {
                toast('No synced pages found', 'warn');
                return;
            }
            pagesSelect.style.display = 'block';
            pagesSelect.innerHTML = '<option value="">-- Select Page --</option>' +
                pages.map(p => `<option value="${p}">${p}</option>`).join('');
        });
    });

    pagesSelect.addEventListener('change', () => {
        const filename = pagesSelect.value;
        if (!filename) return;
        api('/api/ink/load-page', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ filename }),
        })
        .then(r => r.json())
        .then(data => {
            if (data.error) return toast(data.error, 'error');
            state.currentSvgId = data.id;
            state.gcodeGenerated = false;
            toast(`Loaded page — ${data.stroke_count} strokes`, 'success');

            // Auto-convert
            return api('/api/convert', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    id: data.id,
                    tool: state.plotTool || 'pencil',
                    page_width: inkPageW,
                    page_height: inkPageH,
                }),
            }).then(r => r.json());
        })
        .then(cdata => {
            if (!cdata || cdata.error) return cdata && toast(cdata.error, 'error');
            state.currentSvgId = cdata.id;
            state.gcodeGenerated = true;
            drawPreview(cdata.polylines, cdata.toolpath);
            if (cdata.stats) {
                el('stat-strokes').textContent = cdata.stats.stroke_count;
                el('stat-draw').textContent = cdata.stats.draw_distance_mm.toFixed(1) + 'mm';
                el('stat-travel').textContent = cdata.stats.travel_distance_mm.toFixed(1) + 'mm';
                el('stat-time').textContent = Math.ceil(cdata.stats.estimated_time_s) + 's';
            }
            if (cdata.gcode_preview) {
                el('gcode-preview').textContent = cdata.gcode_preview;
                el('gcode-lines').textContent = cdata.line_count + ' lines';
            }
            el('btn-print').disabled = false;
            toast('Page loaded & converted — switch to Plot', 'success');
        });
    });
}
