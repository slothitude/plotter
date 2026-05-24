/* create.js — Step 2: Create (tab container with canvas) */

import { getState, setState, subscribe } from '../state.js';
import { initSvgUpload } from '../creators/svg-upload.js';
import { initTestPatterns } from '../creators/test-patterns.js';
import { initToonTracer } from '../creators/toon-tracer.js';
import { initInkDrawing } from '../creators/ink-drawing.js';
import { initScriptorium } from '../creators/scriptorium.js';
import { redrawCanvas } from '../components/canvas-preview.js';
import { nextStep } from '../router.js';

export function initCreate() {
    initTabs();
    initSvgUpload();
    initTestPatterns();
    initToonTracer();
    initInkDrawing();
    initScriptorium();
    initCanvasOverlays();
    initContinueButton();

    subscribe('create', (changed) => {
        if (changed.polylines !== undefined || changed.toolpath !== undefined ||
            changed.strokeCount !== undefined) {
            redrawCanvas('create-canvas');
            updateStrokeCount();
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

function initContinueButton() {
    const btn = document.getElementById('btn-create-continue');
    btn?.addEventListener('click', () => {
        if (!getState().currentSvgId) {
            // Will be handled by creators setting state
            return;
        }
        nextStep();
    });

    subscribe('create-continue', (changed) => {
        if (changed.currentSvgId !== undefined) {
            if (btn) btn.disabled = !changed.currentSvgId;
        }
    });
}
