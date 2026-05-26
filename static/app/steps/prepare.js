/* prepare.js — Step 3: Transforms + Convert */

import { getState, setState, subscribe } from '../state.js';
import { apiJSON, apiPost } from '../api.js';
import { toast } from '../lib/toast.js';
import { redrawCanvas } from '../components/canvas-preview.js';
import { nextStep } from '../router.js';

export function initPrepare() {
    initTransforms();
    initConvert();
    initCanvasOverlays();
    initContinueButton();

    subscribe('prepare', (changed) => {
        if (changed.polylines !== undefined || changed.toolpath !== undefined ||
            changed.pageWidth !== undefined || changed.pageHeight !== undefined ||
            changed.currentSvgId !== undefined) {
            redrawCanvas('prepare-canvas');
            updateInfo();
            updateConvertButton();
        }
        if (changed.gcodeGenerated !== undefined) {
            const btn = document.getElementById('btn-prepare-continue');
            if (btn) btn.disabled = !changed.gcodeGenerated;
            updateInfo();
        }
    });
}

function initTransforms() {
    const sliders = [
        { id: 'tf-scale', key: 'scale', valId: 'tf-scale-val', fmt: v => parseFloat(v).toFixed(1) },
        { id: 'tf-rotate', key: 'rotate', valId: 'tf-rotate-val', fmt: v => parseInt(v) },
        { id: 'tf-tx', key: 'translate_x', valId: 'tf-tx-val', fmt: v => parseInt(v) },
        { id: 'tf-ty', key: 'translate_y', valId: 'tf-ty-val', fmt: v => parseInt(v) },
    ];

    sliders.forEach(({ id, key, valId, fmt }) => {
        const slider = document.getElementById(id);
        const display = document.getElementById(valId);
        if (!slider) return;

        slider.addEventListener('input', () => {
            const val = parseFloat(slider.value);
            if (display) display.textContent = fmt(slider.value);
            updateTransform(key, val);
        });
    });

    // Checkboxes
    ['tf-mirror-x', 'tf-mirror-y', 'tf-optimize', 'tf-simplify'].forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;
        const key = id.replace('tf-', '').replace(/-/g, '_');
        el.addEventListener('change', () => {
            updateTransform(key, el.checked);
        });
    });

    // Reset
    document.getElementById('btn-tf-reset')?.addEventListener('click', () => {
        const defaults = { scale: 1, rotate: 0, translate_x: 0, translate_y: 0, mirror_x: false, mirror_y: false, optimize: true, simplify: false };
        setState({ transform: defaults });
        document.getElementById('tf-scale').value = 1;
        document.getElementById('tf-scale-val').textContent = '1.0';
        document.getElementById('tf-rotate').value = 0;
        document.getElementById('tf-rotate-val').textContent = '0';
        document.getElementById('tf-tx').value = 0;
        document.getElementById('tf-tx-val').textContent = '0';
        document.getElementById('tf-ty').value = 0;
        document.getElementById('tf-ty-val').textContent = '0';
        document.getElementById('tf-mirror-x').checked = false;
        document.getElementById('tf-mirror-y').checked = false;
        document.getElementById('tf-optimize').checked = true;
        document.getElementById('tf-simplify').checked = false;
        redrawCanvas('prepare-canvas');
        toast('Transforms reset', 'info');
    });
}

function updateTransform(key, value) {
    const t = { ...getState().transform, [key]: value };
    setState({ transform: t, gcodeGenerated: false, toolpath: [], twoPass: false, twoPassId2: null, wcStep: 0 });
    document.getElementById('wc-twopass-badge')?.classList.add('hidden');
    // Re-enable convert button
    document.getElementById('btn-convert').disabled = false;
    document.getElementById('btn-download-gcode').disabled = true;
    document.getElementById('btn-prepare-continue').disabled = true;
    document.getElementById('gcode-preview').textContent = '';
    document.getElementById('gcode-lines').textContent = '0 lines';
    redrawCanvas('prepare-canvas');
}

function initConvert() {
    document.getElementById('btn-convert')?.addEventListener('click', convertSvg);
    document.getElementById('btn-download-gcode')?.addEventListener('click', downloadGcode);
}

function updateConvertButton() {
    const btn = document.getElementById('btn-convert');
    if (!btn) return;
    const s = getState();
    btn.disabled = !s.currentSvgId || s.gcodeGenerated;
}

function convertSvg() {
    const s = getState();
    if (!s.currentSvgId) return toast('Load an SVG first', 'warn');

    const t = s.transform;
    apiPost('/api/convert', {
        id: s.currentSvgId,
        tool: s.tool,
        page_width: s.pageWidth,
        page_height: s.pageHeight,
        page_offset_x: s.pageOffsetX,
        page_offset_y: s.pageOffsetY,
        scale: t.scale,
        rotate: t.rotate,
        translate_x: t.translate_x,
        translate_y: t.translate_y,
        mirror_x: t.mirror_x,
        mirror_y: t.mirror_y,
        optimize: t.optimize !== false,
        simplify: t.simplify || false,
    }).then(data => {
        if (data.error) return toast(data.error, 'error');

        setState({
            gcodeGenerated: true,
            gcodePreview: data.gcode_preview || '',
            gcodeLineCount: data.line_count || 0,
            toolpath: data.toolpath || s.toolpath,
            stats: data.stats || null,
            twoPass: !!data.two_pass,
            twoPassId2: data.id_pass2 || null,
        });

        // Toggle two-pass badge
        document.getElementById('wc-twopass-badge')?.classList.toggle('hidden', !data.two_pass);

        // Update UI elements
        document.getElementById('btn-convert').disabled = true;
        document.getElementById('btn-download-gcode').disabled = false;
        document.getElementById('gcode-preview').textContent = data.gcode_preview || '';
        document.getElementById('gcode-lines').textContent = `${data.line_count} lines`;
        document.getElementById('btn-prepare-continue').disabled = false;

        if (data.stats) updateStats(data.stats);

        redrawCanvas('prepare-canvas');
        toast('G-code generated', 'success');
    }).catch(() => toast('Conversion failed', 'error'));
}

function downloadGcode() {
    const s = getState();
    if (!s.currentSvgId) return;
    window.location.href = `/api/download/${s.currentSvgId}`;
}

function updateStats(stats) {
    const el = document.getElementById('convert-stats');
    if (!el || !stats) return;
    el.innerHTML = `
        <div class="stat-item"><span class="stat-label">Strokes</span><span class="stat-value">${stats.stroke_count || 0}</span></div>
        <div class="stat-item"><span class="stat-label">Distance</span><span class="stat-value">${(stats.draw_distance_mm || stats.total_distance || 0).toFixed(1)} mm</span></div>
        <div class="stat-item"><span class="stat-label">Time</span><span class="stat-value">${formatTime(stats.estimated_time_s || stats.estimated_time || 0)}</span></div>
        <div class="stat-item"><span class="stat-label">Pen Ups</span><span class="stat-value">${stats.pen_ups || 0}</span></div>
    `;
}

function formatTime(seconds) {
    if (!seconds || seconds <= 0) return '--';
    if (seconds < 60) return `${Math.round(seconds)}s`;
    const m = Math.floor(seconds / 60);
    const s = Math.round(seconds % 60);
    return `${m}m ${s}s`;
}

function updateInfo() {
    const el = document.getElementById('prep-info');
    if (!el) return;
    const s = getState();
    if (s.currentSvgId) {
        el.textContent = `${s.strokeCount || 0} strokes` + (s.gcodeGenerated ? ' \u00b7 G-code ready' : '');
    } else {
        el.textContent = 'No SVG loaded — go to Create step first';
    }
}

function initCanvasOverlays() {
    document.getElementById('btn-prep-show-draw')?.addEventListener('click', function() {
        this.classList.toggle('active');
        setState({ showDraw: this.classList.contains('active') });
        redrawCanvas('prepare-canvas');
    });
    document.getElementById('btn-prep-show-travel')?.addEventListener('click', function() {
        this.classList.toggle('active');
        setState({ showTravel: this.classList.contains('active') });
        redrawCanvas('prepare-canvas');
    });
    document.getElementById('btn-prep-show-grid')?.addEventListener('click', function() {
        this.classList.toggle('active');
        setState({ showGrid: this.classList.contains('active') });
        redrawCanvas('prepare-canvas');
    });
}

function initContinueButton() {
    const btn = document.getElementById('btn-prepare-continue');
    if (btn) btn.disabled = !getState().gcodeGenerated;
    btn?.addEventListener('click', () => {
        if (!getState().gcodeGenerated) {
            toast('Generate G-code first', 'warn');
            return;
        }
        nextStep();
    });
}
