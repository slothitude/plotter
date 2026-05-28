/* plot.js — Step 4: Print + Progress + Live Plot + Watercolor Two-Pass */

import { getState, setState, subscribe } from '../state.js';
import { api, apiJSON, apiPost } from '../api.js';
import { toast } from '../lib/toast.js';
import { redrawCanvas } from '../components/canvas-preview.js';

export function initPlot() {
    initPrint();
    initLivePlot();
    initJogMode();
    initCanvasOverlays();
    initWcWorkflow();

    subscribe('plot', (changed, state) => {
        if (changed.progress !== undefined) {
            updateProgress(state.progress);
        }
        if (changed.busy !== undefined) {
            updateBusyState(state.busy);
        }
        if (changed.connected !== undefined || changed.capturing !== undefined) {
            updateLivePlotButton(state);
            updateJogButton(state);
        }
        if (changed.gcodeGenerated !== undefined || changed.currentSvgId !== undefined) {
            updatePrintButton(state);
        }
        if (changed.toolpath !== undefined || changed.polylines !== undefined ||
            changed.pageWidth !== undefined || changed.pageHeight !== undefined ||
            changed.liveStrokes !== undefined || changed.liveCurrentStroke !== undefined) {
            redrawCanvas('plot-canvas');
        }
        if (changed.stats !== undefined && changed.stats) {
            updateStatsDisplay(changed.stats);
        }
        if (changed.twoPass !== undefined || changed.wcStep !== undefined) {
            updateWcWorkflow(state);
        }
        if (changed.jogMode !== undefined) {
            updateJogButton(state);
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
        // Reset watercolor workflow to step 1 if active
        const now = getState();
        if (now.twoPass && now.wcStep > 0 && now.wcStep < 4) {
            setState({ wcStep: 1 });
        }
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
        const s = getState();
        if (s.twoPass && s.wcStep === 1) {
            toast('Pass 1 done \u2014 swap tools and recalibrate', 'success');
            setState({ busy: false, wcStep: 2 });
        } else if (s.twoPass && s.wcStep === 3) {
            toast('Painting complete!', 'success');
            setState({ busy: false, wcStep: 4 });
        } else {
            toast('Plotting complete', 'success');
            setState({ busy: false });
        }
        updateBusyState(false);
    }
}

function updateBusyState(busy) {
    const s = getState();
    const btnPrint = document.getElementById('btn-print');
    const btnStop = document.getElementById('btn-print-stop');
    const btnPass1 = document.getElementById('btn-wc-pass1');
    const btnPass2 = document.getElementById('btn-wc-pass2');

    if (btnPrint) btnPrint.disabled = busy || !s.gcodeGenerated;
    if (btnStop) btnStop.disabled = !busy;
    if (btnPass1) btnPass1.disabled = busy;
    if (btnPass2) btnPass2.disabled = busy;
}

function updatePrintButton(state) {
    const btn = document.getElementById('btn-print');
    if (!btn) return;
    btn.disabled = !state.gcodeGenerated || !state.currentSvgId || state.busy;
    if (state.stats) updateStatsDisplay(state.stats);
}

// ── Watercolor Two-Pass Workflow ──
function initWcWorkflow() {
    document.getElementById('btn-wc-pass1')?.addEventListener('click', wcPass1);
    document.getElementById('btn-wc-swap-done')?.addEventListener('click', () => {
        setState({ wcStep: 3 });
        toast('Ready for pass 2', 'info');
    });
    document.getElementById('btn-wc-pass2')?.addEventListener('click', wcPass2);

    const s = getState();
    // Auto-start workflow if entering Plot step with two-pass active
    if (s.twoPass && s.wcStep === 0) {
        setState({ wcStep: 1 });
    }
    updateWcWorkflow(s);
}

function updateWcWorkflow(state) {
    const btnPrint = document.getElementById('btn-print');
    const workflow = document.getElementById('wc-workflow');
    const step1 = document.getElementById('wc-step-1');
    const step2 = document.getElementById('wc-step-2');
    const step3 = document.getElementById('wc-step-3');

    if (!workflow) return;

    if (!state.twoPass) {
        // Normal mode — hide workflow, show normal print button
        workflow.classList.add('hidden');
        if (btnPrint) btnPrint.classList.remove('hidden');
        return;
    }

    // Two-pass mode — hide normal print button, show workflow
    workflow.classList.remove('hidden');
    if (btnPrint) btnPrint.classList.add('hidden');

    const step = state.wcStep;

    // Step 1
    step1.classList.toggle('hidden', step < 1 || step > 4);
    step1.classList.toggle('wc-active', step === 1);
    step1.classList.toggle('wc-done', step > 1);
    step1.classList.toggle('wc-pending', step < 1);

    // Step 2
    step2.classList.toggle('hidden', step < 2 || step > 4);
    step2.classList.toggle('wc-active', step === 2);
    step2.classList.toggle('wc-done', step > 2);
    step2.classList.toggle('wc-pending', step < 2);

    // Step 3
    step3.classList.toggle('hidden', step < 3 || step > 4);
    step3.classList.toggle('wc-active', step === 3);
    step3.classList.toggle('wc-done', step > 3);
    step3.classList.toggle('wc-pending', step < 3);
}

function wcPass1() {
    const s = getState();
    if (!s.connected) return toast('Connect printer first', 'warn');

    apiPost('/api/print', { id: s.currentSvgId })
        .then(data => {
            if (data.error) return toast(data.error, 'error');
            toast('Pass 1 plotting started (pencil guide)', 'success');
            setState({ busy: true });
            updateBusyState(true);
        })
        .catch(() => toast('Print failed', 'error'));
}

function wcPass2() {
    const s = getState();
    if (!s.connected) return toast('Connect printer first', 'warn');

    // Regenerate pass 2 with current calibration, then print
    toast('Regenerating pass 2 with current calibration...', 'info');
    apiPost('/api/convert-pass2', { id: s.currentSvgId, tool: s.tool })
        .then(data => {
            if (data.error) return toast(data.error, 'error');

            // Print the regenerated pass 2
            return apiPost('/api/print', { id: data.id })
                .then(printData => {
                    if (printData.error) return toast(printData.error, 'error');
                    toast('Pass 2 plotting started (wet brush)', 'success');
                    setState({ busy: true });
                    updateBusyState(true);
                });
        })
        .catch(() => toast('Pass 2 failed', 'error'));
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
    const sc = Number(stats.stroke_count) || 0;
    const dist = Number(stats.draw_distance_mm || stats.total_distance) || 0;
    const time = Number(stats.estimated_time_s || stats.estimated_time) || 0;
    const lines = Number(getState().gcodeLineCount) || 0;
    el.innerHTML = `
        <div class="stat-item"><span class="stat-label">Strokes</span><span class="stat-value">${sc}</span></div>
        <div class="stat-item"><span class="stat-label">Distance</span><span class="stat-value">${dist.toFixed(1)} mm</span></div>
        <div class="stat-item"><span class="stat-label">Time</span><span class="stat-value">${formatTime(time)}</span></div>
        <div class="stat-item"><span class="stat-label">Lines</span><span class="stat-value">${lines}</span></div>
    `;
}

function formatTime(seconds) {
    if (!seconds || seconds <= 0) return '--';
    if (seconds < 60) return `${Math.round(seconds)}s`;
    const m = Math.floor(seconds / 60);
    const s = Math.round(seconds % 60);
    return `${m}m ${s}s`;
}

// ── Jog Mode ──
function initJogMode() {
    document.getElementById('btn-jog-mode')?.addEventListener('click', toggleJogMode);
    updateJogButton(getState());
}

function toggleJogMode() {
    const s = getState();
    const btn = document.getElementById('btn-jog-mode');
    if (!btn) return;

    if (s.jogMode) {
        api('/api/ink/jog-stop', { method: 'POST' })
            .then(r => r.json())
            .then(() => {
                setState({ jogMode: false });
                btn.classList.remove('active');
                btn.textContent = 'Start Jog Mode';
                toast('Jog mode stopped', 'info');
            })
            .catch(err => toast('Jog stop failed: ' + err.message, 'error'));
    } else {
        apiPost('/api/ink/jog-start', { tool: s.tool })
            .then(data => {
                if (data.error) return toast(data.error, 'error');
                setState({ jogMode: true });
                btn.classList.add('active');
                btn.textContent = 'Stop Jog Mode';
                toast('Jog mode active \u2014 tap Slate to move plotter', 'success');
            })
            .catch(err => toast('Jog start failed: ' + err.message, 'error'));
    }
}

function updateJogButton(state) {
    const btn = document.getElementById('btn-jog-mode');
    if (!btn) return;
    const canStart = state.connected && (state.capturing || state.jogMode);
    btn.disabled = !canStart;
    if (state.jogMode) {
        btn.classList.add('active');
        btn.textContent = 'Stop Jog Mode';
    } else {
        btn.classList.remove('active');
        btn.textContent = 'Start Jog Mode';
    }
}
