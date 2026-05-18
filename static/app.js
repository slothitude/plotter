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
