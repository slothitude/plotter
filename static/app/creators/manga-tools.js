/* manga-tools.js — Manga Plotter Toolkit UI */

import { getState, setState, subscribe } from '../state.js';
import { api } from '../api.js';
import { toast } from '../lib/toast.js';
import { redrawCanvas } from '../components/canvas-preview.js';

let mangaState = {
    pageDesc: {
        page_width: 148,
        page_height: 210,
        bleed: 3,
        panels: [],
    },
    selectedPanel: -1,
    layerVisibility: {
        border: true, outline: true, detail: true,
        tone: true, effect: true, text: true,
    },
    polylines: [],
    svgId: null,
};

export function initMangaTools() {
    // Sync page dimensions from global state
    const s = getState();
    mangaState.pageDesc.page_width = s.pageWidth || 148;
    mangaState.pageDesc.page_height = s.pageHeight || 210;

    subscribe('manga', (changed) => {
        if (changed.pageWidth !== undefined) mangaState.pageDesc.page_width = changed.pageWidth;
        if (changed.pageHeight !== undefined) mangaState.pageDesc.page_height = changed.pageHeight;
    });

    // Preset buttons
    document.querySelectorAll('.manga-preset-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const preset = btn.dataset.preset;
            loadPreset(preset);
        });
    });

    // Generate full page
    document.getElementById('btn-manga-generate')?.addEventListener('click', generatePage);

    // Per-panel tools
    document.getElementById('btn-manga-add-speed')?.addEventListener('click', addSpeedLines);
    document.getElementById('btn-manga-add-tone')?.addEventListener('click', addTone);
    document.getElementById('btn-manga-add-bubble')?.addEventListener('click', addBubble);
    document.getElementById('btn-manga-add-burst')?.addEventListener('click', addBurst);
    document.getElementById('btn-manga-add-rain')?.addEventListener('click', addRain);
    document.getElementById('btn-manga-add-sfx')?.addEventListener('click', addSFX);
    document.getElementById('btn-manga-clear-panel')?.addEventListener('click', clearPanel);

    // Layer toggles
    document.querySelectorAll('.manga-layer-toggle').forEach(cb => {
        cb.addEventListener('change', () => {
            mangaState.layerVisibility[cb.dataset.layer] = cb.checked;
        });
    });

    // Panel selector
    document.getElementById('manga-panel-select')?.addEventListener('change', (e) => {
        mangaState.selectedPanel = parseInt(e.target.value) || -1;
        updatePanelEditor();
    });

    // Slate import
    document.getElementById('btn-manga-slate-import')?.addEventListener('click', slateImport);

    // Initialize with default preset
    loadPreset('4-grid');
}

async function loadPreset(preset) {
    const pw = mangaState.pageDesc.page_width;
    const ph = mangaState.pageDesc.page_height;
    const bleed = mangaState.pageDesc.bleed;

    try {
        const res = await api('/api/manga/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                action: 'panels-preset',
                preset, page_width: pw, page_height: ph, bleed,
            }),
        });
        const data = await res.json();
        if (data.ok) {
            mangaState.pageDesc.panels = data.panels;
            updatePanelSelector();
            toast(`Layout: ${preset}`, 'success');
        }
    } catch (e) {
        toast('Failed to load preset', 'error');
    }
}

async function generatePage() {
    try {
        const res = await api('/api/manga/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                action: 'compile-page',
                page: mangaState.pageDesc,
            }),
        });
        const data = await res.json();
        if (data.ok) {
            mangaState.polylines = data.polylines;
            mangaState.svgId = data.svg_id;
            setState({
                currentSvgId: data.svg_id,
                polylines: data.polylines,
                strokeCount: data.stroke_count,
            });
            toast(`Generated ${data.stroke_count} strokes`, 'success');
        } else {
            toast(data.error || 'Generation failed', 'error');
        }
    } catch (e) {
        toast('Generation failed: ' + e.message, 'error');
    }
}

function getSelectedPanel() {
    if (mangaState.selectedPanel < 0) return null;
    return mangaState.pageDesc.panels[mangaState.selectedPanel] || null;
}

function addChildToPanel(child) {
    const panel = getSelectedPanel();
    if (!panel) {
        toast('Select a panel first', 'warn');
        return false;
    }
    if (!panel.children) panel.children = [];
    panel.children.push(child);
    return true;
}

async function addSpeedLines() {
    const panel = getSelectedPanel();
    if (!panel) { toast('Select a panel first', 'warn'); return; }
    const origin_x = parseFloat(document.getElementById('manga-speed-ox')?.value) || 50;
    const origin_y = parseFloat(document.getElementById('manga-speed-oy')?.value) || 50;
    const count = parseInt(document.getElementById('manga-speed-count')?.value) || 20;
    const style = document.getElementById('manga-speed-style')?.value || 'radial';

    addChildToPanel({
        type: 'speed_lines',
        origin: [origin_x, origin_y],
        count,
        length: [15, 50],
        style,
        jitter: 0.5,
    });
    toast('Speed lines added', 'success');
}

async function addTone() {
    const panel = getSelectedPanel();
    if (!panel) { toast('Select a panel first', 'warn'); return; }
    const style = document.getElementById('manga-tone-style')?.value || 'dot';
    const density = parseInt(document.getElementById('manga-tone-density')?.value) || 40;
    const bounds = panel.bounds;

    // Use panel bounds as the tone polygon
    const polygon = [
        [bounds[0] + 3, bounds[1] + 3],
        [bounds[2] - 3, bounds[1] + 3],
        [bounds[2] - 3, bounds[3] - 3],
        [bounds[0] + 3, bounds[3] - 3],
    ];

    addChildToPanel({
        type: 'tone',
        polygon,
        style,
        lpi: density,
        dot_size: 0.4,
        spacing: 2.0,
        angle: 45,
    });
    toast('Tone added', 'success');
}

async function addBubble() {
    const panel = getSelectedPanel();
    if (!panel) { toast('Select a panel first', 'warn'); return; }
    const text = document.getElementById('manga-bubble-text')?.value || 'Hello!';
    const shape = document.getElementById('manga-bubble-shape')?.value || 'ellipse';
    const tail = document.getElementById('manga-bubble-tail')?.value || 'bottom-left';
    const bounds = panel.bounds;
    const cx = (bounds[0] + bounds[2]) / 2;
    const cy = (bounds[1] + bounds[3]) / 2;

    addChildToPanel({
        type: 'bubble',
        cx, cy,
        text,
        shape,
        tail,
        font: 'hershey',
        font_size: 4,
        padding: 3,
    });
    toast('Bubble added', 'success');
}

async function addBurst() {
    const panel = getSelectedPanel();
    if (!panel) { toast('Select a panel first', 'warn'); return; }
    const bounds = panel.bounds;
    const cx = (bounds[0] + bounds[2]) / 2;
    const cy = (bounds[1] + bounds[3]) / 2;

    addChildToPanel({
        type: 'effect',
        name: 'impact_burst',
        cx, cy,
        radius: 15,
        points: 8,
        irregularity: 0.3,
    });
    toast('Burst added', 'success');
}

async function addRain() {
    const panel = getSelectedPanel();
    if (!panel) { toast('Select a panel first', 'warn'); return; }

    addChildToPanel({
        type: 'effect',
        name: 'rain',
        bounds: panel.bounds,
        angle: 75,
        density: 25,
    });
    toast('Rain added', 'success');
}

async function addSFX() {
    const panel = getSelectedPanel();
    if (!panel) { toast('Select a panel first', 'warn'); return; }
    const text = document.getElementById('manga-sfx-text')?.value || 'BOOM';
    const size = parseFloat(document.getElementById('manga-sfx-size')?.value) || 15;
    const angle = parseFloat(document.getElementById('manga-sfx-angle')?.value) || 0;
    const bounds = panel.bounds;

    // Place SFX at center of panel
    addChildToPanel({
        type: 'sfx_placeholder', // handled by compile as raw text polylines
    });

    toast('SFX added (compile page to see)', 'success');
}

function clearPanel() {
    const panel = getSelectedPanel();
    if (!panel) { toast('Select a panel first', 'warn'); return; }
    panel.children = [];
    toast('Panel cleared', 'info');
}

async function slateImport() {
    toast('Slate import: draw panel rectangles on Slate, then press capture', 'info');
    // This would require capture.py integration — placeholder for now
}

function updatePanelSelector() {
    const select = document.getElementById('manga-panel-select');
    if (!select) return;
    select.innerHTML = '<option value="-1">-- Select Panel --</option>';
    mangaState.pageDesc.panels.forEach((panel, i) => {
        const opt = document.createElement('option');
        opt.value = i;
        opt.textContent = `Panel ${i + 1} (${panel.bounds.map(v => v.toFixed(0)).join(', ')})`;
        select.appendChild(opt);
    });
}

function updatePanelEditor() {
    const panel = getSelectedPanel();
    const info = document.getElementById('manga-panel-info');
    if (!info) return;
    if (!panel) {
        info.textContent = 'No panel selected';
        return;
    }
    const children = panel.children || [];
    const counts = {};
    children.forEach(c => {
        const type = c.type === 'effect' ? c.name : c.type;
        counts[type] = (counts[type] || 0) + 1;
    });
    const desc = Object.entries(counts).map(([k, v]) => `${v}x ${k}`).join(', ') || 'empty';
    info.textContent = `Panel ${mangaState.selectedPanel + 1}: ${desc}`;
}
