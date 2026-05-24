/* toon-tracer.js — Image tracing */

import { getState, setState } from '../state.js';
import { api, apiJSON } from '../api.js';
import { toast } from '../lib/toast.js';
import { redrawCanvas } from '../components/canvas-preview.js';

export function initToonTracer() {
    // Drop area
    const dropArea = document.getElementById('toon-drop-area');
    const input = document.getElementById('toon-input');
    let currentFile = null;

    dropArea?.addEventListener('click', () => input?.click());
    dropArea?.addEventListener('dragover', e => { e.preventDefault(); dropArea.classList.add('dragover'); });
    dropArea?.addEventListener('dragleave', () => dropArea.classList.remove('dragover'));
    dropArea?.addEventListener('drop', e => {
        e.preventDefault();
        dropArea.classList.remove('dragover');
        const file = e.dataTransfer.files[0];
        if (file && file.type.startsWith('image/')) {
            currentFile = file;
            dropArea.innerHTML = `<span style="color:var(--amber)">${file.name}</span>`;
        }
    });
    input?.addEventListener('change', e => {
        const file = e.target.files[0];
        if (file) {
            currentFile = file;
            if (dropArea) dropArea.innerHTML = `<span style="color:var(--amber)">${file.name}</span>`;
        }
    });

    // Trace sliders
    ['trace-threshold', 'trace-blur'].forEach(id => {
        const slider = document.getElementById(id);
        const display = document.getElementById(id + '-val');
        if (slider && display) {
            slider.addEventListener('input', () => { display.textContent = slider.value; });
        }
    });

    // Trace button
    document.getElementById('btn-trace')?.addEventListener('click', () => {
        if (!currentFile) return toast('Select an image first', 'warn');

        const fd = new FormData();
        fd.append('file', currentFile);
        fd.append('threshold', document.getElementById('trace-threshold')?.value || 128);
        fd.append('blur', document.getElementById('trace-blur')?.value || 2);
        fd.append('tool', getState().tool);
        fd.append('page_width', getState().pageWidth);
        fd.append('page_height', getState().pageHeight);

        api('/api/trace', { method: 'POST', body: fd })
            .then(r => r.json())
            .then(data => {
                if (data.error) return toast(data.error, 'error');

                setState({
                    currentSvgId: data.id,
                    polylines: data.polylines || null,
                    toolpath: [],
                    strokeCount: data.stroke_count || 0,
                    gcodeGenerated: false,
                });

                const info = document.getElementById('trace-info');
                if (info) info.textContent = `Traced: ${data.stroke_count} strokes`;

                redrawCanvas('create-canvas');
                toast(`Traced: ${data.stroke_count} strokes`, 'success');
            })
            .catch(err => toast('Trace failed: ' + err.message, 'error'));
    });
}
