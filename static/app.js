/* ─── Plotter Control — Frontend Logic ─── */

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
    ws: null,
    statusInterval: null,
};

// ── Init ─────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    loadPorts();
    loadCalibration();
    loadSettings(state.settingsTool);
    initTabs();
    initSidebarControls();
    initCalibration();
    initPlot();
    initPageSize();
    initSettings();
    connectWebSocket();
    startStatusPolling();
    drawEmptyCanvas();
});

// ── Tabs ─────────────────────────────────────────────────────────────
function initTabs() {
    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(tc => tc.classList.remove('active'));
            tab.classList.add('active');
            document.getElementById('tab-' + tab.dataset.tab).classList.add('active');
        });
    });
}

// ── Sidebar Controls ─────────────────────────────────────────────────
function initSidebarControls() {
    // Connection
    document.getElementById('btn-connect').addEventListener('click', toggleConnection);

    // Home
    document.getElementById('btn-home').addEventListener('click', () => {
        api('/api/home', { method: 'POST' })
            .then(r => r.json())
            .then(data => {
                if (data.position) updatePosition(data.position);
                if (data.ok) flash('Homed all axes', 'accent');
            });
    });

    // E-Stop
    document.getElementById('btn-estop').addEventListener('click', () => {
        api('/api/stop', { method: 'POST' })
            .then(() => flash('Emergency stop sent', 'danger'));
    });

    // Top bar jog buttons
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
    api('/api/jog', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ axis, distance, speed: axis === 'Z' ? 300 : 1500 }),
    })
        .then(r => r.json())
        .then(data => {
            if (data.position) updatePosition(data.position);
        });
}

function updatePosition(pos) {
    if (pos.X !== undefined) {
        document.getElementById('pos-x').textContent = pos.X.toFixed(3);
        document.getElementById('cal-ox').textContent = pos.X.toFixed(3);
    }
    if (pos.Y !== undefined) {
        document.getElementById('pos-y').textContent = pos.Y.toFixed(3);
        document.getElementById('cal-oy').textContent = pos.Y.toFixed(3);
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
            .then(() => setConnected(false));
    } else {
        const port = document.getElementById('port-select').value;
        if (!port) return;
        api('/api/serial/connect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ port }),
        })
            .then(r => r.json())
            .then(data => {
                if (data.ok) setConnected(true);
                else flash(data.error || 'Connection failed', 'danger');
            })
            .catch(() => flash('Connection failed', 'danger'));
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
    } else {
        btn.textContent = 'CONNECT';
        btn.classList.add('btn-connect');
        btn.style.background = '';
        btn.style.color = '';
        btn.style.border = '';
        badge.textContent = 'OFFLINE';
        badge.className = 'conn-badge disconnected';
    }
}

// ── Calibration ──────────────────────────────────────────────────────
function initCalibration() {
    document.getElementById('cal-tool').addEventListener('change', e => {
        state.calTool = e.target.value;
    });

    document.getElementById('btn-cal-start').addEventListener('click', () => {
        if (!state.connected) return flash('Connect printer first', 'warn');
        api('/api/home', { method: 'POST' }).then(() => {
            jog('X', 110);
            setTimeout(() => jog('Y', 110), 500);
            flash('Homed and moved to center', 'accent');
        });
    });

    document.getElementById('btn-cal-test-dot').addEventListener('click', () => {
        if (!state.connected) return flash('Connect printer first', 'warn');
        api('/api/calibration/test-dot', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tool: state.calTool }),
        }).then(r => r.json()).then(data => {
            if (data.ok) flash('Test dot executed', 'accent');
        });
    });

    // Z step buttons
    document.querySelectorAll('.btn-z-step').forEach(btn => {
        btn.addEventListener('click', () => {
            if (!state.connected) return flash('Connect printer first', 'warn');
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
        if (isNaN(z)) return flash('No Z value to save', 'warn');
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
                flash(`Saved ${state.calTool}: pen_down_z=${z.toFixed(3)}`, 'accent');
                loadCalibration();
            }
        });
    });

    document.getElementById('btn-cal-save-offset').addEventListener('click', () => {
        const ox = parseFloat(document.getElementById('cal-ox').textContent);
        const oy = parseFloat(document.getElementById('cal-oy').textContent);
        if (isNaN(ox) || isNaN(oy)) return flash('No position to save — connect and home first', 'warn');
        api('/api/calibration/offset', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tool: state.calTool, offset_x: ox, offset_y: oy }),
        }).then(r => r.json()).then(data => {
            if (data.ok) {
                flash(`Saved ${state.calTool} offset: X=${ox}, Y=${oy}`, 'accent');
                loadCalibration();
            }
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
            div.innerHTML = `
                <span class="tool-name">${tool}</span>
                <span class="height-values">
                    Down: ${heights.pen_down_z.toFixed(3)} mm · Up: ${heights.pen_up_z.toFixed(3)} mm<br>
                    Offset: X=${ox.toFixed(1)} Y=${oy.toFixed(1)} mm
                </span>`;
            list.appendChild(div);
        }
    });
}

// ── Plot Tab ─────────────────────────────────────────────────────────
function initPlot() {
    const dropZone = document.getElementById('drop-zone');
    const svgInput = document.getElementById('svg-input');

    dropZone.addEventListener('click', () => svgInput.click());

    dropZone.addEventListener('dragover', e => {
        e.preventDefault();
        dropZone.classList.add('dragover');
    });

    dropZone.addEventListener('dragleave', () => {
        dropZone.classList.remove('dragover');
    });

    dropZone.addEventListener('drop', e => {
        e.preventDefault();
        dropZone.classList.remove('dragover');
        if (e.dataTransfer.files.length) {
            uploadSvg(e.dataTransfer.files[0]);
        }
    });

    svgInput.addEventListener('change', e => {
        if (e.target.files.length) uploadSvg(e.target.files[0]);
    });

    document.getElementById('btn-convert').addEventListener('click', convertSvg);
    document.getElementById('btn-download-gcode').addEventListener('click', downloadGcode);
    document.getElementById('btn-print').addEventListener('click', startPrint);

    // Test patterns
    ['circle', 'square', 'grid', 'star'].forEach(p => {
        document.getElementById(`btn-test-${p}`).addEventListener('click', () => loadTestPattern(p));
    });
    document.getElementById('btn-test-text').addEventListener('click', () => {
        document.querySelector('.test-text-input').classList.toggle('hidden');
    });
    document.getElementById('btn-test-text-go').addEventListener('click', () => {
        const text = document.getElementById('test-text-value').value || 'HELLO';
        loadTestPattern('text', text);
    });
}

function loadTestPattern(pattern, text) {
    const size = parseFloat(document.getElementById('test-size').value) || 80;
    const body = {
        pattern,
        size,
        page_width: state.pageWidth,
        page_height: state.pageHeight,
        page_offset_x: state.pageOffsetX,
        page_offset_y: state.pageOffsetY,
    };
    if (pattern === 'text') body.text = text || 'HELLO';
    api('/api/test-pattern', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    }).then(r => r.json()).then(data => {
        if (data.error) return flash(data.error, 'danger');
        state.currentSvgId = data.id;
        document.getElementById('upload-info').classList.remove('hidden');
        document.getElementById('upload-info').innerHTML = `Test: <span class="filename">${pattern}</span> · ${data.stroke_count} strokes`;
        if (data.polylines) {
            drawPreview(data.polylines);
            document.getElementById('preview-info').textContent = `${data.stroke_count} strokes`;
        }
        if (data.has_gcode) {
            // Text patterns come pre-converted
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
        flash(`Test pattern "${pattern}" loaded`, 'accent');
    });
}

function uploadSvg(file) {
    if (!file.name.endsWith('.svg')) return flash('Only SVG files accepted', 'danger');

    const fd = new FormData();
    fd.append('file', file);

    api('/api/upload', { method: 'POST', body: fd })
        .then(r => r.json())
        .then(data => {
            if (data.error) return flash(data.error, 'danger');
            state.currentSvgId = data.id;

            // Show upload info
            const info = document.getElementById('upload-info');
            info.classList.remove('hidden');
            info.innerHTML = `Uploaded: <span class="filename">${file.name}</span> · ${data.stroke_count} strokes`;

            // Enable convert button
            document.getElementById('btn-convert').disabled = false;

            // Draw preview
            if (data.polylines) {
                drawPreview(data.polylines);
                document.getElementById('preview-info').textContent = `${data.stroke_count} strokes`;
            }

            flash('SVG uploaded', 'accent');
        })
        .catch(() => flash('Upload failed', 'danger'));
}

function convertSvg() {
    if (!state.currentSvgId) return;
    const tool = document.getElementById('plot-tool').value;

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
        }),
    })
        .then(r => r.json())
        .then(data => {
            if (data.error) return flash(data.error, 'danger');

            // Show G-code
            document.getElementById('gcode-preview').textContent = data.gcode_preview || 'No preview available';
            document.getElementById('gcode-lines').textContent = `${data.line_count} lines`;
            document.getElementById('btn-download-gcode').disabled = false;
            document.getElementById('btn-print').disabled = false;
            state.gcodeGenerated = true;

            // Update preview with transformed polylines
            if (data.polylines) {
                drawPreview(data.polylines);
            }

            // Show convert result
            const result = document.getElementById('convert-result');
            result.classList.remove('hidden');
            result.textContent = `${data.line_count} G-code lines generated`;

            flash('G-code generated', 'accent');
        })
        .catch(() => flash('Conversion failed', 'danger'));
}

function downloadGcode() {
    if (!state.currentSvgId) return;
    window.location.href = `/api/download/${state.currentSvgId}`;
}

function startPrint() {
    if (!state.currentSvgId || !state.gcodeGenerated) return;
    if (!state.connected) return flash('Connect printer first', 'warn');

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
            if (data.ok) flash('Plotting started', 'accent');
            else flash(data.error, 'danger');
        })
        .catch(() => flash('Print failed', 'danger'));
}

// ── Canvas Preview ───────────────────────────────────────────────────
function drawEmptyCanvas() {
    const canvas = document.getElementById('preview-canvas');
    const ctx = canvas.getContext('2d');
    drawGrid(ctx, canvas.width, canvas.height);
}

function drawGrid(ctx, w, h) {
    // Background
    ctx.fillStyle = '#0d1117';
    ctx.fillRect(0, 0, w, h);

    // Use page dimensions
    const bedW = state.pageWidth;
    const bedH = state.pageHeight;
    const padding = 20;
    const drawW = w - padding * 2;
    const drawH = h - padding * 2;
    const scale = Math.min(drawW / bedW, drawH / bedH);

    ctx.strokeStyle = '#1e2530';
    ctx.lineWidth = 0.5;

    // Grid lines every 10mm
    for (let i = 0; i <= bedW; i += 10) {
        const x = padding + i * scale;
        ctx.beginPath();
        ctx.moveTo(x, padding);
        ctx.lineTo(x, padding + bedH * scale);
        ctx.stroke();
    }
    for (let i = 0; i <= bedH; i += 10) {
        const y = padding + i * scale;
        ctx.beginPath();
        ctx.moveTo(padding, y);
        ctx.lineTo(padding + bedW * scale, y);
        ctx.stroke();
    }

    // Bed border
    ctx.strokeStyle = '#2a3545';
    ctx.lineWidth = 1.5;
    ctx.strokeRect(padding, padding, bedW * scale, bedH * scale);

    // Origin marker
    ctx.fillStyle = '#2a3545';
    ctx.font = '9px monospace';
    ctx.fillText('(0,0)', padding - 2, padding - 4);
    ctx.fillText(`${bedW}x${bedH}mm`, padding + bedW * scale - 40, padding + bedH * scale + 12);

    return { padding, scale, bedW, bedH };
}

function drawPreview(polylines) {
    const canvas = document.getElementById('preview-canvas');
    const ctx = canvas.getContext('2d');
    const { padding, scale, bedW, bedH } = drawGrid(ctx, canvas.width, canvas.height);

    if (!polylines || !polylines.length) return;

    // Find bounds of all points for auto-scaling within bed
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
    const margin = 10;
    const availableW = bedW - margin * 2;
    const availableH = bedH - margin * 2;
    const fitScale = Math.min(availableW / svgW, availableH / svgH);
    const offsetX = (bedW - svgW * fitScale) / 2 - minX * fitScale;
    const offsetY = (bedH - svgH * fitScale) / 2 - minY * fitScale;

    // Bounding box
    ctx.strokeStyle = '#00e87b33';
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    const bx = padding + (minX * fitScale + offsetX) * scale;
    const by = padding + (minY * fitScale + offsetY) * scale;
    const bw = svgW * fitScale * scale;
    const bh = svgH * fitScale * scale;
    ctx.strokeRect(bx, by, bw, bh);
    ctx.setLineDash([]);

    // Draw paths
    ctx.strokeStyle = '#00e87b';
    ctx.lineWidth = 1.2;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';

    for (const path of polylines) {
        if (path.length < 2) continue;
        ctx.beginPath();
        const firstX = padding + (path[0][0] * fitScale + offsetX) * scale;
        const firstY = padding + (path[0][1] * fitScale + offsetY) * scale;
        ctx.moveTo(firstX, firstY);
        for (let i = 1; i < path.length; i++) {
            const px = padding + (path[i][0] * fitScale + offsetX) * scale;
            const py = padding + (path[i][1] * fitScale + offsetY) * scale;
            ctx.lineTo(px, py);
        }
        ctx.stroke();
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

    // Load saved page size
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
        drawEmptyCanvas();
    });

    // Preset dropdown change
    presetSel.addEventListener('change', () => {
        const preset = presetSel.value;
        if (PAGE_PRESETS[preset]) {
            const [w, h] = PAGE_PRESETS[preset];
            widthInput.value = w;
            heightInput.value = h;
            widthInput.disabled = true;
            heightInput.disabled = true;
            savePageSize();
        } else {
            widthInput.disabled = false;
            heightInput.disabled = false;
            savePageSize();
        }
    });

    // W/H input change → switch to Custom
    const onDimChange = () => {
        presetSel.value = 'Custom';
        widthInput.disabled = false;
        heightInput.disabled = false;
        savePageSize();
    };
    widthInput.addEventListener('change', onDimChange);
    heightInput.addEventListener('change', onDimChange);

    // Offset inputs
    offsetXInput.addEventListener('change', savePageSize);
    offsetYInput.addEventListener('change', savePageSize);
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
    // Settings tabs
    document.querySelectorAll('.settings-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.settings-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            state.settingsTool = tab.dataset.settingsTool;
            loadSettings(state.settingsTool);
        });
    });

    // Save
    document.getElementById('btn-save-settings').addEventListener('click', saveSettings);
}

function loadSettings(tool) {
    api(`/api/settings/${tool}`)
        .then(r => r.json())
        .then(data => {
            if (data.error) return;
            document.getElementById('set-draw-speed').value = data.movement.draw_speed;
            document.getElementById('set-travel-speed').value = data.movement.travel_speed;
            document.getElementById('set-lift-height').value = data.movement.lift_height;
            document.getElementById('set-water-enabled').checked = data.water.enabled;
            document.getElementById('set-cup-x').value = data.water.cup_x;
            document.getElementById('set-cup-y').value = data.water.cup_y;
            document.getElementById('set-cup-height').value = data.water.cup_height;
            document.getElementById('set-cup-diameter').value = data.water.cup_diameter;
            document.getElementById('set-dip-depth').value = data.water.dip_depth;
            document.getElementById('set-dip-time').value = data.water.dip_time;
            document.getElementById('set-dip-interval').value = data.water.dip_interval;
            document.getElementById('set-blot-x').value = data.water.blot_x;
            document.getElementById('set-blot-y').value = data.water.blot_y;

            // Show/hide water settings based on tool
            const waterPanel = document.getElementById('water-settings');
            waterPanel.style.display = tool === 'watercolor' ? 'block' : 'none';
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
            },
            water: {
                enabled: document.getElementById('set-water-enabled').checked,
                cup_x: parseFloat(document.getElementById('set-cup-x').value),
                cup_y: parseFloat(document.getElementById('set-cup-y').value),
                cup_height: parseFloat(document.getElementById('set-cup-height').value),
                cup_diameter: parseFloat(document.getElementById('set-cup-diameter').value),
                dip_depth: parseFloat(document.getElementById('set-dip-depth').value),
                dip_time: parseInt(document.getElementById('set-dip-time').value),
                dip_interval: parseInt(document.getElementById('set-dip-interval').value),
                blot_x: parseFloat(document.getElementById('set-blot-x').value),
                blot_y: parseFloat(document.getElementById('set-blot-y').value),
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
                flash('Settings saved', 'accent');
            }
        });
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
                document.getElementById('progress-fill').style.background = 'var(--danger)';
            }
            if (data.completed >= data.total && data.total > 0) {
                document.getElementById('progress-text').textContent = 'Complete';
                flash('Plotting complete', 'accent');
            }
        }
    };

    state.ws.onclose = () => {
        // Reconnect after 3s
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

function flash(message, type = 'accent') {
    // Simple flash using the conn-status element temporarily
    // In production you'd want a toast system
    const colors = {
        accent: 'var(--accent)',
        warn: 'var(--warn)',
        danger: 'var(--danger)',
    };
    const bar = document.querySelector('.tab-bar');
    const existing = document.querySelector('.flash-msg');
    if (existing) existing.remove();

    const div = document.createElement('div');
    div.className = 'flash-msg';
    div.style.cssText = `
        position: fixed; top: 8px; right: 16px; z-index: 1000;
        padding: 8px 16px; background: var(--bg-2); border: 1px solid ${colors[type] || colors.accent};
        color: ${colors[type] || colors.accent}; font-family: var(--mono); font-size: 11px;
        border-radius: 3px; animation: flashIn 0.2s ease-out;
    `;
    div.textContent = message;
    document.body.appendChild(div);
    setTimeout(() => div.remove(), 3000);
}

// Add flash animation
const style = document.createElement('style');
style.textContent = `
    @keyframes flashIn {
        from { opacity: 0; transform: translateY(-10px); }
        to { opacity: 1; transform: translateY(0); }
    }
`;
document.head.appendChild(style);
