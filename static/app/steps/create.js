/* create.js — Step 2: Create (tab container with canvas) */

import { getState, setState, subscribe } from '../state.js';
import { initSvgUpload } from '../creators/svg-upload.js';
import { initTestPatterns } from '../creators/test-patterns.js';
import { initToonTracer } from '../creators/toon-tracer.js';
import { initInkDrawing } from '../creators/ink-drawing.js';
import { initScriptorium } from '../creators/scriptorium.js';
import { initMangaTools } from '../creators/manga-tools.js';
import { redrawCanvas } from '../components/canvas-preview.js';
import { nextStep } from '../router.js';
import { toast } from '../lib/toast.js';

export function initCreate() {
    initTabs();
    initSvgUpload();
    initTestPatterns();
    initToonTracer();
    initInkDrawing();
    initScriptorium();
    initMangaTools();
    initCanvasOverlays();
    initContinueButton();

    subscribe('create', (changed) => {
        if (changed.polylines !== undefined || changed.toolpath !== undefined ||
            changed.strokeCount !== undefined) {
            redrawCanvas('create-canvas');
            updateStrokeCount();
        }
        // Enable/disable continue when SVG loads
        if (changed.currentSvgId !== undefined) {
            updateContinueButton();
        }
        // Reset gcode state when new SVG loaded (needs re-convert)
        if (changed.currentSvgId !== undefined && changed.currentSvgId !== null) {
            setState({ gcodeGenerated: false, twoPass: false, twoPassId2: null, wcStep: 0 });
        }
    });
}

function initTabs() {
    const tabs = document.querySelectorAll('#create-tabs .sub-tab');
    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            tabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            document.querySelectorAll('#step-2 .sub-panel').forEach(p => p.classList.remove('active'));
            const panel = document.getElementById(`tab-${tab.dataset.tab}`);
            if (panel) panel.classList.add('active');
        });
    });
}

function initCanvasOverlays() {
    document.getElementById('btn-show-draw')?.addEventListener('click', function() {
        this.classList.toggle('active');
        setState({ showDraw: this.classList.contains('active') });
        redrawCanvas('create-canvas');
    });
    document.getElementById('btn-show-travel')?.addEventListener('click', function() {
        this.classList.toggle('active');
        setState({ showTravel: this.classList.contains('active') });
        redrawCanvas('create-canvas');
    });
    document.getElementById('btn-show-grid')?.addEventListener('click', function() {
        this.classList.toggle('active');
        setState({ showGrid: this.classList.contains('active') });
        redrawCanvas('create-canvas');
    });
}

function updateStrokeCount() {
    const el = document.getElementById('create-stroke-count');
    if (el) el.textContent = `${getState().strokeCount || 0} strokes`;
}

function updateContinueButton() {
    const btn = document.getElementById('btn-create-continue');
    if (btn) btn.disabled = !getState().currentSvgId;
}

function initContinueButton() {
    const btn = document.getElementById('btn-create-continue');
    if (btn) btn.disabled = true;
    btn?.addEventListener('click', () => {
        if (!getState().currentSvgId) {
            toast('Load an SVG or create a pattern first', 'warn');
            return;
        }
        nextStep();
    });
}
