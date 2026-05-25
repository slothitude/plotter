/* canvas-preview.js — Blueprint canvas: grid, page, toolpath */

import { getState, subscribe } from '../state.js';

const BED_SIZE = 220;

export function initCanvasPreview() {
    subscribe('canvas', (changed, state) => {
        if (changed.polylines !== undefined || changed.toolpath !== undefined ||
            changed.showDraw !== undefined || changed.showTravel !== undefined ||
            changed.showGrid !== undefined || changed.pageWidth !== undefined ||
            changed.pageHeight !== undefined) {
            redrawAll(state);
        }
    });
}

function getCanvas(canvasId) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return null;
    const container = canvas.parentElement;
    // Use CSS pixels (layout size), not canvas attribute pixels
    const w = container.clientWidth;
    const h = container.clientHeight;
    if (w === 0 || h === 0) return null;
    const dpr = window.devicePixelRatio || 1;
    const pw = Math.round(w * dpr);
    const ph = Math.round(h * dpr);
    // Only resize backing store when dimensions actually change
    if (canvas.width !== pw || canvas.height !== ph) {
        canvas.width = pw;
        canvas.height = ph;
    }
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { canvas, ctx, w, h };
}

export function drawCanvas(canvasId, state) {
    const info = getCanvas(canvasId);
    if (!info) return;
    const { ctx, w, h } = info;
    const s = state || getState();

    ctx.clearRect(0, 0, w, h);

    // Background
    ctx.fillStyle = '#0a1628';
    ctx.fillRect(0, 0, w, h);

    // Blueprint grid
    if (s.showGrid !== false) {
        drawGrid(ctx, w, h);
    }

    // Page rectangle
    const { ox, oy, scale } = drawPage(ctx, w, h, s);

    // Polylines (SVG preview with transforms applied)
    if (s.polylines && s.showDraw !== false) {
        const t = s.transform || {};
        const transformed = applyTransforms(s.polylines, t, s.pageWidth || 220, s.pageHeight || 220);
        drawPolylines(ctx, ox, oy, scale, transformed, '#5b9bd5');
    }

    // Toolpath (G-code preview — already in bed coordinates, render directly)
    if (s.toolpath && s.toolpath.length > 0) {
        drawToolpath(ctx, ox, oy, scale, s.toolpath, s.showDraw, s.showTravel);
    }
}

function drawGrid(ctx, w, h) {
    const gridSize = 20;
    ctx.strokeStyle = '#1a2d50';
    ctx.lineWidth = 0.5;

    for (let x = 0; x < w; x += gridSize) {
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, h);
        ctx.stroke();
    }
    for (let y = 0; y < h; y += gridSize) {
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(w, y);
        ctx.stroke();
    }
}

function drawPage(ctx, w, h, s) {
    const padding = 20;
    const availW = w - padding * 2;
    const availH = h - padding * 2;

    const scaleX = availW / BED_SIZE;
    const scaleY = availH / BED_SIZE;
    const scale = Math.min(scaleX, scaleY);

    const bedW = BED_SIZE * scale;
    const bedH = BED_SIZE * scale;
    const ox = (w - bedW) / 2;
    const oy = (h - bedH) / 2;

    // Bed border
    ctx.strokeStyle = '#2a4a7f';
    ctx.lineWidth = 1;
    ctx.strokeRect(ox, oy, bedW, bedH);

    // Page rectangle
    const pageW = (s.pageWidth || 220) * scale;
    const pageH = (s.pageHeight || 220) * scale;
    const pageOx = (s.pageOffsetX || 0) * scale;
    const pageOy = (s.pageOffsetY || 0) * scale;

    ctx.strokeStyle = '#3d6ab5';
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    // Center page on bed
    const pageX = ox + (bedW - pageW) / 2 + pageOx;
    const pageY = oy + (bedH - pageH) / 2 + pageOy;
    ctx.strokeRect(pageX, pageY, pageW, pageH);
    ctx.setLineDash([]);

    // Center crosshair
    ctx.strokeStyle = '#2a4a7f';
    ctx.lineWidth = 0.5;
    ctx.beginPath();
    ctx.moveTo(ox + bedW / 2 - 5, oy + bedH / 2);
    ctx.lineTo(ox + bedW / 2 + 5, oy + bedH / 2);
    ctx.moveTo(ox + bedW / 2, oy + bedH / 2 - 5);
    ctx.lineTo(ox + bedW / 2, oy + bedH / 2 + 5);
    ctx.stroke();

    return { ox, oy, scale };
}

function applyTransforms(polylines, t, pageW, pageH) {
    const sc = t.scale || 1;
    const rot = (t.rotate || 0) * Math.PI / 180;
    const tx = t.translate_x || 0;
    const ty = t.translate_y || 0;
    const mx = t.mirror_x;
    const my = t.mirror_y;
    // Center of page for rotation/scaling
    const cx = pageW / 2;
    const cy = pageH / 2;

    return polylines.map(poly => {
        const pts = Array.isArray(poly) ? poly : poly.points;
        if (!pts || pts.length < 2) return poly;

        return pts.map(([x, y]) => {
            // Translate to center origin
            let px = x - cx;
            let py = y - cy;
            // Scale (matches backend: scale first)
            px *= sc;
            py *= sc;
            // Rotate (matches backend: rotate second)
            if (rot) {
                const cos = Math.cos(rot);
                const sin = Math.sin(rot);
                const rx = px * cos - py * sin;
                const ry = px * sin + py * cos;
                px = rx;
                py = ry;
            }
            // Mirror (matches backend: mirror third)
            if (mx) px = -px;
            if (my) py = -py;
            // Translate back + user offset (matches backend: translate last)
            px += cx + tx;
            py += cy + ty;
            return [px, py];
        });
    });
}

function drawPolylines(ctx, ox, oy, scale, polylines, color) {
    if (!polylines || !polylines.length) return;
    ctx.strokeStyle = color;
    ctx.lineWidth = 1;

    for (const poly of polylines) {
        // API returns polylines as arrays of [x,y] points (not objects)
        const pts = Array.isArray(poly) ? poly : poly.points;
        if (!pts || pts.length < 2) continue;
        ctx.beginPath();
        ctx.moveTo(ox + pts[0][0] * scale, oy + pts[0][1] * scale);
        for (let i = 1; i < pts.length; i++) {
            ctx.lineTo(ox + pts[i][0] * scale, oy + pts[i][1] * scale);
        }
        ctx.stroke();
    }
}

function drawToolpath(ctx, ox, oy, scale, toolpath, showDraw, showTravel) {
    for (const seg of toolpath) {
        // API returns {layer, points: [[x,y],[x,y]], type}
        const pts = seg.points;
        if (!pts || pts.length < 2) continue;

        if (seg.type === 'draw' && showDraw !== false) {
            ctx.strokeStyle = '#e8a838';
            ctx.lineWidth = 1;
        } else if (seg.type === 'travel' && showTravel) {
            ctx.strokeStyle = '#ff444466';
            ctx.lineWidth = 0.5;
            ctx.setLineDash([2, 2]);
        } else {
            continue;
        }

        ctx.beginPath();
        ctx.moveTo(ox + pts[0][0] * scale, oy + pts[0][1] * scale);
        for (let i = 1; i < pts.length; i++) {
            ctx.lineTo(ox + pts[i][0] * scale, oy + pts[i][1] * scale);
        }
        ctx.stroke();
        ctx.setLineDash([]);
    }
}

export function redrawAll(state) {
    const s = state || getState();
    // Redraw all visible canvases
    const activePanel = document.querySelector('.step-panel.active');
    if (!activePanel) return;

    const canvas = activePanel.querySelector('canvas');
    if (canvas) {
        drawCanvas(canvas.id, s);
    }
}

export function redrawCanvas(canvasId) {
    drawCanvas(canvasId, getState());
}

// Redraw on resize
let resizeTimer;
window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => redrawAll(), 100);
});
