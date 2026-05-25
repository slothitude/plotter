/* scriptorium.js — Manuscript generator */

import { getState, setState } from '../state.js';
import { apiJSON, apiPost } from '../api.js';
import { toast } from '../lib/toast.js';
import { redrawCanvas } from '../components/canvas-preview.js';

export function initScriptorium() {
    document.getElementById('btn-script-generate')?.addEventListener('click', generate);
}

function generate() {
    const text = document.getElementById('script-text')?.value;
    if (!text) return toast('Enter some text first', 'warn');
    if (text.length > 500) return toast('Text too long (max 500 characters)', 'warn');

    const s = getState();
    const fontSize = parseFloat(document.getElementById('script-size')?.value) || 25;
    const spacing = parseFloat(document.getElementById('script-spacing')?.value) || 2;
    const font = document.getElementById('script-font')?.value || 'hershey';

    apiPost('/api/test-pattern', {
        pattern: 'text',
        text,
        font,
        font_size: fontSize,
        tool: s.tool,
        page_width: s.pageWidth,
        page_height: s.pageHeight,
        page_offset_x: s.pageOffsetX,
        page_offset_y: s.pageOffsetY,
        scale: 1, rotate: 0, translate_x: 0, translate_y: 0,
        mirror_x: false, mirror_y: false, optimize: true, simplify: false,
        spacing,
    }).then(data => {
        if (data.error) return toast(data.error, 'error');

        setState({
            currentSvgId: data.id,
            polylines: data.polylines || null,
            toolpath: [],
            strokeCount: data.stroke_count || 0,
            stats: null,
            gcodeGenerated: false,
        });

        redrawCanvas('create-canvas');
        toast('Scriptorium SVG loaded', 'success');
    }).catch(err => toast('Generate failed: ' + err.message, 'error'));
}
