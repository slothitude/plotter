/* test-patterns.js — Test pattern generation */

import { getState, setState } from '../state.js';
import { apiJSON, apiPost } from '../api.js';
import { toast } from '../lib/toast.js';
import { redrawCanvas } from '../components/canvas-preview.js';

export function initTestPatterns() {
    // Pattern buttons
    document.querySelectorAll('#test-patterns-grid .btn').forEach(btn => {
        btn.addEventListener('click', () => loadTestPattern(btn.dataset.pattern));
    });

    // Text pattern
    document.getElementById('btn-test-text')?.addEventListener('click', () => {
        const text = document.getElementById('test-text-value')?.value || 'Hello World';
        loadTestPattern('text', text);
    });
}

function loadTestPattern(pattern, text) {
    const s = getState();
    const size = parseFloat(document.getElementById('test-size')?.value) || 80;

    const body = {
        pattern,
        size,
        tool: s.tool,
        page_width: s.pageWidth,
        page_height: s.pageHeight,
        page_offset_x: s.pageOffsetX,
        page_offset_y: s.pageOffsetY,
        ...getTransformParams(s),
    };
    if (pattern === 'text') body.text = text || 'Hello World';

    apiPost('/api/test-pattern', body).then(data => {
        if (data.error) return toast(data.error, 'error');

        setState({
            currentSvgId: data.id,
            polylines: data.polylines || null,
            toolpath: data.toolpath || [],
            strokeCount: data.stroke_count || 0,
            stats: data.stats || null,
            gcodeGenerated: !!data.has_gcode,
            gcodePreview: data.gcode_preview || '',
            gcodeLineCount: data.line_count || 0,
        });

        if (data.has_gcode) {
            updateConvertUI(true, data);
        }

        redrawCanvas('create-canvas');
        toast(`Test pattern "${pattern}" loaded`, 'success');
    });
}

function getTransformParams(s) {
    const t = s.transform || {};
    return {
        scale: t.scale || 1,
        rotate: t.rotate || 0,
        translate_x: t.translate_x || 0,
        translate_y: t.translate_y || 0,
        mirror_x: t.mirror_x || false,
        mirror_y: t.mirror_y || false,
        optimize: t.optimize !== false,
        simplify: t.simplify || false,
    };
}

function updateConvertUI(hasGcode, data) {
    // Update prepare step buttons
    document.getElementById('btn-convert').disabled = hasGcode;
    document.getElementById('btn-download-gcode').disabled = !hasGcode;
    if (hasGcode) {
        document.getElementById('gcode-preview').textContent = data.gcode_preview || '';
        document.getElementById('gcode-lines').textContent = `${data.line_count} lines`;
    }
}
