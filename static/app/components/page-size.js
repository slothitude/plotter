/* page-size.js — Unified page size control */

import { getState, setState, subscribe } from '../state.js';
import { apiJSON, apiPost } from '../api.js';
import { toast } from '../lib/toast.js';
import { redrawCanvas } from './canvas-preview.js';

const PRESETS = [
    { label: 'A5', w: 148, h: 210 },
    { label: 'A4', w: 210, h: 297 },
    { label: 'A3', w: 297, h: 420 },
    { label: '220mm', w: 220, h: 220 },
    { label: 'Full', w: 220, h: 220 },
];

export function initPageSize() {
    const container = document.getElementById('page-size-container');
    if (!container) return;

    container.innerHTML = `
        <div class="page-presets">
            ${PRESETS.map(p => `<button class="preset-btn" data-w="${p.w}" data-h="${p.h}">${p.label}</button>`).join('')}
        </div>
        <div class="form-row mt-md">
            <label class="form-label">Width</label>
            <input type="number" id="page-w" class="input-field" value="${getState().pageWidth}">
            <span class="unit">mm</span>
            <label class="form-label" style="margin-left:8px">Height</label>
            <input type="number" id="page-h" class="input-field" value="${getState().pageHeight}">
            <span class="unit">mm</span>
        </div>
        <div class="form-row mt-sm">
            <label class="form-label">Offset X</label>
            <input type="number" id="page-ox" class="input-field" value="${getState().pageOffsetX}" step="1">
            <span class="unit">mm</span>
            <label class="form-label" style="margin-left:8px">Offset Y</label>
            <input type="number" id="page-oy" class="input-field" value="${getState().pageOffsetY}" step="1">
            <span class="unit">mm</span>
        </div>
    `;

    // Preset buttons
    container.querySelectorAll('.preset-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const w = parseInt(btn.dataset.w);
            const h = parseInt(btn.dataset.h);
            setPage(w, h);
            document.getElementById('page-w').value = w;
            document.getElementById('page-h').value = h;
            container.querySelectorAll('.preset-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
        });
    });

    // Manual inputs
    ['page-w', 'page-h', 'page-ox', 'page-oy'].forEach(id => {
        document.getElementById(id)?.addEventListener('change', () => {
            const w = parseFloat(document.getElementById('page-w').value) || 220;
            const h = parseFloat(document.getElementById('page-h').value) || 220;
            const ox = parseFloat(document.getElementById('page-ox').value) || 0;
            const oy = parseFloat(document.getElementById('page-oy').value) || 0;
            setPage(w, h, ox, oy);
        });
    });

    // Highlight active preset
    updatePresetHighlight();
}

function setPage(w, h, ox, oy) {
    const s = getState();
    if (ox === undefined) ox = s.pageOffsetX;
    if (oy === undefined) oy = s.pageOffsetY;

    setState({ pageWidth: w, pageHeight: h, pageOffsetX: ox, pageOffsetY: oy });

    apiPost('/api/page-size', {
        width: w, height: h,
        preset: `${w}mm`,
        offset_x: ox, offset_y: oy,
    }).catch(() => {});

    updatePresetHighlight();
    redrawCanvas('setup-canvas');
}

function updatePresetHighlight() {
    const s = getState();
    document.querySelectorAll('.preset-btn').forEach(btn => {
        const match = parseInt(btn.dataset.w) === s.pageWidth && parseInt(btn.dataset.h) === s.pageHeight;
        btn.classList.toggle('active', match);
    });
}
