/* plot.js — Step 4: Print + Progress + Live Plot */

import { getState, setState, subscribe } from '../state.js';
import { api, apiJSON, apiPost } from '../api.js';
import { toast } from '../lib/toast.js';
import { redrawCanvas } from '../components/canvas-preview.js';

export function initPlot() {
    initPrint();
    initLivePlot();
    initCanvasOverlays();

    subscribe('plot', (changed, state) => {
        if (changed.progress !== undefined) {
            updateProgress(state.progress);
        }
        if (changed.busy !== undefined) {
            updateBusyState(state.busy);
        }
        if (changed.connected !== undefined || changed.capturing !== undefined) {
            updateLivePlotButton(state);
        }
        if (changed.gcodeGenerated !== undefined || changed.currentSvgId !== undefined) {
            updatePrintButton(state);
        }
        if (changed.toolpath !== undefined || changed.polylines !== undefined ||
            changed.pageWidth !== undefined || changed.pageHeight !== undefined) {
            redrawCanvas('plot-canvas');
        }
        if (changed.stats !== undefined && changed.stats) {
            updateStatsDisplay(changed.stats);
        }
    });
}

// ── Print ──
function initPrint() {
    document.getElementById('btn-print')?.addEventListener('click', startPrint);
    document.getElementById('btn-print-stop')?.addEventListener('click', stopPrint);
    updatePrintButton(getState());
}

function startPrint() {
    const s = getState();
    if (!s.connected) return toast('Connect printer first', 'warn');
    if (!s.currentSvgId || !s.gcodeGenerated) return toast('Generate G-code first', 'warn');

    apiPost('/api/print', { id: s.currentSvgId })
        .then(data => {
            if (data.error) return toast(data.error, 'error');
            toast('Plotting started', 'success');
            setState({ busy: true });
            updateBusyState(true);
        })
        .catch(() => toast('Print failed', 'error'));
}

function stopPrint() {
    api('/api/stop', { method: 'POST' }).then(() => {
        toast('Plotting stopped', 'warn');
        setState({ busy: false });
        updateBusyState(false);
    });
}

function updateProgress(progress) {
    if (!progress) return;
    const pct = progress.total > 0 ? (progress.completed / progress.total * 100) : 0;
    const fill = document.getElementById('plot-progress-fill');
    const text = document.getElementById('plot-progress-text');
    if (fill) fill.style.width = `${pct}%`;
    if (text) text.textContent = `${progress.completed}/${progress.total} lines${progress.info ? ' \u2014 ' + progress.info : ''}`;

    if (progress.completed >= progress.total && progress.total > 0) {
        toast('Plotting complete', 'success');
        setState({ busy: false });
        updateBusyState(false);
    }
}

function updateBusyState(busy) {
    const btnPrint = document.getElementById('btn-print');
    const btnStop = document.getElementById('btn-print-stop');
    if (btnPrint) btnPrint.disabled = busy || !getState().gcodeGenerated;
    if (btnStop) btnStop.disabled = !busy;
}

function updatePrintButton(state) {
    const btn = document.getElementById('btn-print');
    if (!btn) return;
    btn.disabled = !state.gcodeGenerated || !state.currentSvgId || state.busy;
    if (state.stats) updateStatsDisplay(state.stats);
}

// ── Live Plot ──
function initLivePlot() {
    document.getElementById('btn-live-plot')?.addEventListener('click', toggleLivePlot);
    updateLivePlotButton(getState());
}

function toggleLivePlot() {
    const s = getState();
    const btn = document.getElementById('btn-live-plot');
    if (!btn) return;

    if (s.livePlotActive) {
        // Stop
        api('/api/ink/live-stop', { method: 'POST' })
            .then(r => r.json())
            .then(data => {
                setState({ livePlotActive: false });
                btn.classList.remove('active');
                btn.textContent = 'Start Live Plot';
                toast('Live plot stopped \u2014 plotter parked', 'success');
                updateLivePlotStatus('Stopped');
            })
            .catch(err => toast('Stop failed: ' + err.message, 'error'));
    } else {
        // Start
        apiPost('/api/ink/live-start', { tool: s.tool })
            .then(data => {
                if (data.error) return toast(data.error, 'error');
                setState({ livePlotActive: true });
                btn.classList.add('active');
                btn.textContent = 'Stop Live Plot';
                toast('Live plot active \u2014 draw on Slate to plot', 'success');
                updateLivePlotStatus('Active \u2014 draw on Slate');
            })
            .catch(err => toast('Live plot failed: ' + err.message, 'error'));
    }
}

function updateLivePlotButton(state) {
    const btn = document.getElementById('btn-live-plot');
    if (!btn) return;

    // Enable if connected + capturing (or already active)
    const canStart = state.connected && (state.capturing || state.livePlotActive);
    btn.disabled = !canStart;

    if (state.livePlotActive) {
        btn.classList.add('active');
        btn.textContent = 'Stop Live Plot';
    } else {
        btn.classList.remove('active');
        btn.textContent = 'Start Live Plot';
    }
}

function updateLivePlotStatus(msg) {
    const el = document.getElementById('live-plot-status');
    if (el) el.textContent = msg;
}

function initCanvasOverlays() {
    document.getElementById('btn-plot-show-draw')?.addEventListener('click', function() {
        this.classList.toggle('active');
        setState({ showDraw: this.classList.contains('active') });
        redrawCanvas('plot-canvas');
    });
    document.getElementById('btn-plot-show-travel')?.addEventListener('click', function() {
        this.classList.toggle('active');
        setState({ showTravel: this.classList.contains('active') });
        redrawCanvas('plot-canvas');
    });
    document.getElementById('btn-plot-show-grid')?.addEventListener('click', function() {
        this.classList.toggle('active');
        setState({ showGrid: this.classList.contains('active') });
        redrawCanvas('plot-canvas');
    });
}

function updateStatsDisplay(stats) {
    const el = document.getElementById('plot-stats');
    if (!el || !stats) return;
    el.innerHTML = `
        <div class="stat-item"><span class="stat-label">Strokes</span><span class="stat-value">${stats.stroke_count || 0}</span></div>
        <div class="stat-item"><span class="stat-label">Distance</span><span class="stat-value">${(stats.draw_distance_mm || stats.total_distance || 0).toFixed(1)} mm</span></div>
        <div class="stat-item"><span class="stat-label">Time</span><span class="stat-value">${formatTime(stats.estimated_time_s || stats.estimated_time || 0)}</span></div>
        <div class="stat-item"><span class="stat-label">Lines</span><span class="stat-value">${getState().gcodeLineCount || 0}</span></div>
    `;
}

function formatTime(seconds) {
    if (!seconds || seconds <= 0) return '--';
    if (seconds < 60) return `${Math.round(seconds)}s`;
    const m = Math.floor(seconds / 60);
    const s = Math.round(seconds % 60);
    return `${m}m ${s}s`;
}
