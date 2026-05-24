/* drop-zone.js — Reusable drag-and-drop file component */

import { escapeHtml } from './escape.js';
import { toast } from './toast.js';

export function initDropZone(containerId, inputId, onFile, accept = '.svg') {
    const zone = document.getElementById(containerId);
    const input = document.getElementById(inputId);
    if (!zone || !input) return;

    zone.addEventListener('click', () => input.click());
    zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('dragover'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
    zone.addEventListener('drop', e => {
        e.preventDefault();
        zone.classList.remove('dragover');
        const file = e.dataTransfer.files[0];
        if (file) validateAndAccept(file);
    });
    input.addEventListener('change', e => {
        const file = e.target.files[0];
        if (file) validateAndAccept(file);
        input.value = ''; // allow re-upload of same file
    });

    function validateAndAccept(file) {
        if (!file.name.endsWith(accept)) {
            toast(`Only ${accept} files accepted`, 'error');
            return;
        }
        onFile(file);
    }
}
