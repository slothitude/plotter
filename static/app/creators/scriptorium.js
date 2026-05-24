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

    const s = getState();
    const size = parseFloat(document.getElementById('script-size')?.value) || 10;
    const spacing = parseFloat(document.getElementById('script-spacing')?.value) || 2;
    const font = document.getElementById('script-font')?.value || 'hershey';
    const fontSize = parseFloat(document.getElementById('script-size')?.value) || 25;

    apiPost('/api/test-pattern', {
        pattern: 'text',
        text,
        font,
        font_size: fontSize,
        size,
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
            toolpath: data.toolpath || [],
            strokeCount: data.stroke_count || 0,
            stats: data.stats || null,
            gcodeGenerated: !!data.has_gcode,
        });

        redrawCanvas('create-canvas');
        toast('Scriptorium SVG loaded', 'success');
    }).catch(err => toast('Generate failed: ' + err.message, 'error'));
}
