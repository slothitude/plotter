/* setup.js — Step 1: Connect, calibrate, page setup, bed level, mark page */

import { getState, setState, subscribe } from '../state.js';
import { api, apiJSON, apiPost } from '../api.js';
import { toast } from '../lib/toast.js';
import { initPageSize } from '../components/page-size.js';
import { redrawCanvas } from '../components/canvas-preview.js';
import { appendLog } from '../websocket.js';
import { nextStep } from '../router.js';

const BED_CENTER = 110;

export function initSetup() {
    initPageSize();
    initCalibration();
    initBedLevel();
    initMarkPage();
    initContinueButton();

    subscribe('setup', (changed) => {
        if (changed.position) updateZDisplay(changed.position);
    });
}

function initContinueButton() {
    document.getElementById('btn-setup-continue')?.addEventListener('click', () => {
        nextStep();
    });
}

// ── Calibration ──
function initCalibration() {
    const calTool = document.getElementById('cal-tool');
    calTool?.addEventListener('change', () => {
        loadPenOffsetInputs();
    });

    document.getElementById('btn-cal-start')?.addEventListener('click', () => {
        if (!getState().connected) return toast('Connect printer first', 'warn');
        apiJSON('/api/calibration/start', { method: 'POST' }).then(data => {
            if (data.ok) {
                if (data.position) setState({ position: data.position });
                toast('At load position \u2014 insert pen, then tap Pen Loaded', 'success');
            }
        });
    });

    document.getElementById('btn-cal-pen-loaded')?.addEventListener('click', () => {
        if (!getState().connected) return toast('Connect printer first', 'warn');
        apiJSON('/api/calibration/pen-loaded', { method: 'POST' }).then(data => {
            if (data.ok) {
                if (data.position) setState({ position: data.position });
                toast('Pen raised \u2014 adjust Z until pen touches paper', 'success');
            }
        });
    });

    document.getElementById('btn-cal-test-dot')?.addEventListener('click', () => {
        if (!getState().connected) return toast('Connect printer first', 'warn');
        apiPost('/api/calibration/test-dot', { tool: getCalTool() }).then(data => {
            if (data.ok) toast('Test dot executed', 'success');
        });
    });

    // Z step buttons
    document.querySelectorAll('.btn-z-step').forEach(btn => {
        btn.addEventListener('click', () => {
            if (!getState().connected) return toast('Connect printer first', 'warn');
            const delta = parseFloat(btn.dataset.delta);
            apiPost('/api/calibration/step', { distance: delta }).then(data => {
                if (data.position) setState({ position: data.position });
            });
        });
    });

    // Save Z
    document.getElementById('btn-cal-save')?.addEventListener('click', () => {
        const z = parseFloat(document.getElementById('cal-z-display')?.textContent);
        if (isNaN(z)) return toast('No Z value to save', 'warn');
        const liftHeight = 5.0;
        apiPost('/api/calibration/save', {
            tool: getCalTool(),
            pen_down_z: z,
            pen_up_z: z + liftHeight,
        }).then(data => {
            if (data.ok) {
                toast(`Saved ${getCalTool()}: pen_down_z=${z.toFixed(3)}`, 'success');
                loadCalibration();
            }
        });
    });

    // Read offset
    document.getElementById('btn-cal-read-offset')?.addEventListener('click', () => {
        apiJSON('/api/status').then(data => {
            if (!data.position?.X) return toast('No position \u2014 connect and home first', 'warn');
            const hx = parseFloat(data.position.X);
            const hy = parseFloat(data.position.Y);
            document.getElementById('cal-offset-x').value = (BED_CENTER - hx).toFixed(1);
            document.getElementById('cal-offset-y').value = (BED_CENTER - hy).toFixed(1);
            toast(`Pen offset read: X=${(BED_CENTER - hx).toFixed(1)} Y=${(BED_CENTER - hy).toFixed(1)}`, 'info');
        });
    });

    // Save offset
    document.getElementById('btn-cal-save-offset')?.addEventListener('click', () => {
        const penOx = parseFloat(document.getElementById('cal-offset-x').value) || 0;
        const penOy = parseFloat(document.getElementById('cal-offset-y').value) || 0;
        apiPost('/api/calibration/offset', {
            tool: getCalTool(),
            offset_x: BED_CENTER - penOx,
            offset_y: BED_CENTER - penOy,
        }).then(data => {
            if (data.ok) {
                toast(`Saved ${getCalTool()} pen offset: X=${penOx} Y=${penOy}`, 'success');
                loadCalibration();
                showEffectiveArea(BED_CENTER - penOx, BED_CENTER - penOy);
            }
        });
    });

    // Clear offset
    document.getElementById('btn-cal-clear-offset')?.addEventListener('click', () => {
        document.getElementById('cal-offset-x').value = 0;
        document.getElementById('cal-offset-y').value = 0;
        apiPost('/api/calibration/offset', {
            tool: getCalTool(),
            offset_x: 0, offset_y: 0,
        }).then(data => {
            if (data.ok) {
                toast('Pen offset cleared', 'info');
                loadCalibration();
                showEffectiveArea(0, 0);
            }
        });
    });

    loadCalibration();
    loadPenOffsetInputs();
}

function getCalTool() {
    return document.getElementById('cal-tool')?.value || 'pencil';
}

function updateZDisplay(pos) {
    if (pos.Z !== undefined) {
        const el = document.getElementById('cal-z-display');
        if (el) el.textContent = pos.Z.toFixed(3);
    }
}

function loadCalibration() {
    apiJSON('/api/calibration').then(cal => {
        setState({ calibration: cal });
        const list = document.getElementById('cal-saved-list');
        if (!list) return;

        if (!Object.keys(cal).length) {
            list.innerHTML = '<div style="font-size:10px;color:var(--text-3)">No calibrations saved yet.</div>';
            return;
        }

        list.innerHTML = '';
        for (const [tool, heights] of Object.entries(cal)) {
            const div = document.createElement('div');
            div.className = 'cal-saved-item';
            const ox = heights.offset_x || 0;
            const oy = heights.offset_y || 0;
            const nameSpan = document.createElement('span');
            nameSpan.className = 'tool-name';
            nameSpan.textContent = tool;
            const valSpan = document.createElement('span');
            valSpan.className = 'height-values';
            valSpan.textContent = `Down: ${heights.pen_down_z.toFixed(3)} Up: ${heights.pen_up_z.toFixed(3)}\nPen offset: X=${(BED_CENTER - ox).toFixed(1)} Y=${(BED_CENTER - oy).toFixed(1)}`;
            div.appendChild(nameSpan);
            div.appendChild(valSpan);
            list.appendChild(div);
        }
    });
}

function loadPenOffsetInputs() {
    apiJSON('/api/calibration').then(cal => {
        const data = cal[getCalTool()];
        if (data) {
            const storedOx = data.offset_x || 0;
            const storedOy = data.offset_y || 0;
            document.getElementById('cal-offset-x').value = storedOx ? (BED_CENTER - storedOx).toFixed(1) : 0;
            document.getElementById('cal-offset-y').value = storedOy ? (BED_CENTER - storedOy).toFixed(1) : 0;
            showEffectiveArea(storedOx, storedOy);
        }
    });
}

function showEffectiveArea(ox, oy) {
    const el = document.getElementById('effective-area-info');
    if (!el) return;
    if (ox === 0 && oy === 0) {
        el.textContent = `Effective area: 220 x 220 mm (full bed)`;
        return;
    }
    const cx = ox - 110;
    const cy = oy - 110;
    const physX = -cx;
    const physY = -cy;
    const effOx = Math.max(0, physX);
    const effOy = Math.max(0, physY);
    const effW = Math.min(220, 220 + physX) - effOx;
    const effH = Math.min(220, 220 + physY) - effOy;
    el.textContent = `Effective area: ${effW.toFixed(0)} x ${effH.toFixed(0)} mm at (${effOx.toFixed(0)}, ${effOy.toFixed(0)})`;
}

// ── Bed Leveling ──
function initBedLevel() {
    const btnStart = document.getElementById('btn-bed-level');
    const btnRepeat = document.getElementById('btn-bed-repeat');
    const btnNext = document.getElementById('btn-bed-next');
    const btnStop = document.getElementById('btn-bed-stop');
    const statusEl = document.getElementById('bed-level-status');

    function showControls(corner, label) {
        [btnRepeat, btnNext, btnStop].forEach(b => b.classList.remove('hidden'));
        btnStart?.classList.add('hidden');
        if (statusEl) statusEl.textContent = `Corner ${corner + 1}/4: ${label}`;
    }

    function hideControls() {
        [btnRepeat, btnNext, btnStop].forEach(b => b.classList.add('hidden'));
        btnStart?.classList.remove('hidden');
        if (statusEl) statusEl.textContent = '';
    }

    btnStart?.addEventListener('click', () => {
        if (!getState().connected) return toast('Connect printer first', 'warn');
        apiPost('/api/bed-level', { action: 'start', tool: getCalTool() }).then(data => {
            if (data.error) return toast(data.error, 'error');
            showControls(data.corner, data.label);
            toast(`Drawing at ${data.label}`, 'success');
        });
    });

    btnRepeat?.addEventListener('click', () => {
        apiPost('/api/bed-level', { action: 'repeat', tool: getCalTool() }).then(data => {
            if (data.error) return toast(data.error, 'error');
            showControls(data.corner, data.label);
            toast(`Redrawing ${data.label}`, 'success');
        });
    });

    btnNext?.addEventListener('click', () => {
        apiPost('/api/bed-level', { action: 'next', tool: getCalTool() }).then(data => {
            if (data.error) return toast(data.error, 'error');
            if (data.done) {
                hideControls();
                toast('Bed leveling complete \u2014 all 4 corners checked', 'success');
            } else {
                showControls(data.corner, data.label);
                toast(`Drawing at ${data.label}`, 'success');
            }
        });
    });

    btnStop?.addEventListener('click', () => {
        apiPost('/api/bed-level', { action: 'stop', tool: getCalTool() }).then(() => {
            hideControls();
            toast('Bed leveling stopped', 'info');
        });
    });
}

// ── Mark Page ──
function initMarkPage() {
    document.getElementById('btn-mark-page')?.addEventListener('click', () => {
        if (!getState().connected) return toast('Connect printer first', 'warn');
        apiPost('/api/mark-page', { tool: getState().tool }).then(data => {
            if (data.error) return toast(data.error, 'error');
            toast('Page outline marked \u2014 check corners', 'success');
        });
    });
}
