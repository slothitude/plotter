/* ink-drawing.js — Hand drawing canvas */

import { getState, setState } from '../state.js';
import { api, apiJSON, apiPost } from '../api.js';
import { toast } from '../lib/toast.js';

let strokes = [];
let currentStroke = [];
let isDrawing = false;

export function initInkDrawing() {
    const canvas = document.getElementById('ink-canvas');
    if (!canvas) return;

    const ctx = canvas.getContext('2d');

    // Drawing
    canvas.addEventListener('mousedown', e => startDraw(e, canvas, ctx));
    canvas.addEventListener('mousemove', e => continueDraw(e, canvas, ctx));
    canvas.addEventListener('mouseup', () => endDraw(ctx));
    canvas.addEventListener('mouseleave', () => { if (isDrawing) endDraw(ctx); });

    // Touch support
    canvas.addEventListener('touchstart', e => { e.preventDefault(); startDraw(e.touches[0], canvas, ctx); });
    canvas.addEventListener('touchmove', e => { e.preventDefault(); continueDraw(e.touches[0], canvas, ctx); });
    canvas.addEventListener('touchend', e => { e.preventDefault(); endDraw(ctx); });

    // Controls
    document.getElementById('btn-ink-undo')?.addEventListener('click', () => {
        strokes.pop();
        redrawInk(ctx, canvas);
    });

    document.getElementById('btn-ink-clear')?.addEventListener('click', () => {
        strokes = [];
        currentStroke = [];
        ctx.clearRect(0, 0, canvas.width, canvas.height);
    });

    document.getElementById('btn-ink-to-plotter')?.addEventListener('click', () => {
        sendToPlotter();
    });

    // Brush size
    document.getElementById('ink-brush-size')?.addEventListener('input', () => {
        // Just visual, brush size is read at draw time
    });

    // Slate controls
    initSlateControls();
    initOCR();
}

function getPos(e, canvas) {
    const rect = canvas.getBoundingClientRect();
    return {
        x: (e.clientX - rect.left) * (canvas.width / rect.width),
        y: (e.clientY - rect.top) * (canvas.height / rect.height),
    };
}

function startDraw(e, canvas, ctx) {
    isDrawing = true;
    currentStroke = [getPos(e, canvas)];
}

function continueDraw(e, canvas, ctx) {
    if (!isDrawing) return;
    const pos = getPos(e, canvas);
    currentStroke.push(pos);

    const brushSize = parseInt(document.getElementById('ink-brush-size')?.value) || 3;
    ctx.strokeStyle = '#5b9bd5';
    ctx.lineWidth = brushSize;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';

    if (currentStroke.length >= 2) {
        const prev = currentStroke[currentStroke.length - 2];
        ctx.beginPath();
        ctx.moveTo(prev.x, prev.y);
        ctx.lineTo(pos.x, pos.y);
        ctx.stroke();
    }
}

function endDraw(ctx) {
    if (!isDrawing) return;
    isDrawing = false;
    if (currentStroke.length > 1) {
        strokes.push([...currentStroke]);
    }
    currentStroke = [];
}

function redrawInk(ctx, canvas) {
    const brushSize = parseInt(document.getElementById('ink-brush-size')?.value) || 3;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.strokeStyle = '#5b9bd5';
    ctx.lineWidth = brushSize;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';

    for (const stroke of strokes) {
        if (stroke.length < 2) continue;
        ctx.beginPath();
        ctx.moveTo(stroke[0].x, stroke[0].y);
        for (let i = 1; i < stroke.length; i++) {
            ctx.lineTo(stroke[i].x, stroke[i].y);
        }
        ctx.stroke();
    }
}

function sendToPlotter() {
    if (!strokes.length) return toast('Draw something first', 'warn');

    const canvas = document.getElementById('ink-canvas');
    canvas.toBlob(blob => {
        const fd = new FormData();
        fd.append('file', blob, 'ink-drawing.png');

        api('/api/upload', { method: 'POST', body: fd })
            .then(r => r.json())
            .then(data => {
                if (data.error) return toast(data.error, 'error');
                setState({
                    currentSvgId: data.id,
                    polylines: data.polylines || null,
                    strokeCount: data.stroke_count || 0,
                    gcodeGenerated: false,
                });
                toast('Sent to Plotter \u2014 ready to prepare', 'success');
            })
            .catch(err => toast('Failed: ' + err.message, 'error'));
    });
}

// ── Slate Controls ──
function initSlateControls() {
    document.getElementById('btn-slate-capture')?.addEventListener('click', () => {
        api('/api/ink/capture', { method: 'POST' })
            .then(r => r.json())
            .then(data => {
                if (data.error) return toast(data.error, 'error');
                setState({ capturing: true });
                updateSlateUI(true);
                toast('Slate connected \u2014 draw on the pad', 'success');
            })
            .catch(err => toast('Failed to start capture: ' + err.message, 'error'));
    });

    document.getElementById('btn-slate-stop')?.addEventListener('click', () => {
        api('/api/ink/stop', { method: 'POST' })
            .then(r => r.json())
            .then(data => {
                setState({ capturing: false });
                updateSlateUI(false);
                toast('Capture stopped', 'info');
            })
            .catch(err => toast('Stop failed: ' + err.message, 'error'));
    });

    document.getElementById('btn-slate-sync')?.addEventListener('click', () => {
        toast('Syncing pages from device...', 'info');
        api('/api/ink/sync', { method: 'POST' })
            .then(r => r.json())
            .then(data => {
                if (data.error) return toast(data.error, 'error');
                toast('Sync started \u2014 check device. Load pages when done.', 'success');
                loadSlatePages();
            })
            .catch(err => toast('Sync failed: ' + err.message, 'error'));
    });

    loadSlatePages();
}

function updateSlateUI(capturing) {
    const indicator = document.getElementById('slate-indicator');
    const text = document.getElementById('slate-status-text');
    const btnCapture = document.getElementById('btn-slate-capture');
    const btnStop = document.getElementById('btn-slate-stop');

    if (indicator) indicator.classList.toggle('active', capturing);
    if (text) text.textContent = capturing ? 'Capturing...' : 'Disconnected';
    if (btnCapture) btnCapture.classList.toggle('hidden', capturing);
    if (btnStop) btnStop.classList.toggle('hidden', !capturing);
}

function loadSlatePages() {
    apiJSON('/api/ink/pages').then(pages => {
        const container = document.getElementById('slate-pages');
        if (!container) return;
        if (!pages.length) {
            container.innerHTML = '<div style="font-size:10px;color:var(--text-3)">No synced pages</div>';
            return;
        }
        container.innerHTML = '';
        pages.forEach(page => {
            const btn = document.createElement('button');
            btn.className = 'btn btn-outline btn-sm';
            btn.style.marginRight = '4px';
            btn.style.marginBottom = '4px';
            btn.textContent = page.filename || page;
            btn.addEventListener('click', () => loadSlatePage(page.filename || page));
            container.appendChild(btn);
        });
    }).catch(() => {});
}

function loadSlatePage(filename) {
    apiPost('/api/ink/load-page', { filename })
        .then(data => {
            if (data.error) return toast(data.error, 'error');
            setState({
                currentSvgId: data.id,
                polylines: data.polylines || null,
                strokeCount: data.stroke_count || 0,
                gcodeGenerated: false,
            });
            toast(`Loaded page \u2014 ${data.stroke_count} strokes`, 'success');
        })
        .catch(err => toast('Load failed: ' + err.message, 'error'));
}

// ── OCR ──
function initOCR() {
    document.getElementById('btn-ink-ocr')?.addEventListener('click', () => {
        if (!strokes.length) return toast('Draw something first', 'warn');

        const svgId = getState().currentSvgId;
        if (!svgId) return toast('Capture or load an ink page first', 'warn');

        const resultEl = document.getElementById('ocr-result');
        if (resultEl) {
            resultEl.classList.remove('hidden');
            resultEl.textContent = 'Reading handwriting...';
        }

        apiPost('/api/ink/ocr', { id: svgId })
            .then(data => {
                if (data.error) return toast(data.error, 'error');
                if (resultEl) {
                    resultEl.textContent = data.text || data.result || 'No text detected';
                }
                toast('Handwriting read', 'success');
            })
            .catch(() => {
                toast('OCR failed', 'error');
                if (resultEl) resultEl.textContent = 'OCR failed';
            });
    });
}
