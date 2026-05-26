/* svg-upload.js — SVG upload + drag-drop */

import { getState, setState } from '../state.js';
import { api, apiJSON } from '../api.js';
import { toast } from '../lib/toast.js';
import { initDropZone } from '../lib/drop-zone.js';
import { redrawCanvas } from '../components/canvas-preview.js';

export function initSvgUpload() {
    initDropZone('drop-zone', 'svg-input', uploadSvg, '.svg');
}

function uploadSvg(file) {
    const fd = new FormData();
    fd.append('file', file);

    api('/api/upload', { method: 'POST', body: fd })
        .then(r => r.json())
        .then(data => {
            if (data.error) return toast(data.error, 'error');

            setState({
                currentSvgId: data.id,
                toolpath: [],
                gcodeGenerated: false,
                polylines: data.polylines || null,
                strokeCount: data.stroke_count || 0,
            });

            const info = document.getElementById('upload-info');
            if (info) {
                info.classList.remove('hidden');
                const fn = info.querySelector('.filename') || document.createElement('span');
                fn.className = 'filename';
                fn.textContent = file.name;
                info.innerHTML = 'Uploaded: ';
                info.appendChild(fn);
                info.append(` \u00b7 ${data.stroke_count} strokes`);
            }

            redrawCanvas('create-canvas');
            toast('SVG uploaded', 'success');
        })
        .catch(() => toast('Upload failed', 'error'));
}
