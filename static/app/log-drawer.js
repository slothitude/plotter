/* log-drawer.js — Bottom log drawer */

import { onLog } from './websocket.js';

export function initLogDrawer() {
    const drawer = document.getElementById('log-drawer');
    const header = document.getElementById('log-header');
    const toggle = document.getElementById('log-toggle');
    const output = document.getElementById('log-output');
    const input = document.getElementById('log-command-input');
    const sendBtn = document.getElementById('log-send-btn');

    if (!drawer) return;

    const toggleDrawer = () => drawer.classList.toggle('open');

    header?.addEventListener('click', toggleDrawer);
    toggle?.addEventListener('click', toggleDrawer);

    // Wire log output
    if (output) {
        onLog((dir, msg) => {
            const line = document.createElement('div');
            line.className = `log-line ${dir}`;
            const ts = new Date().toLocaleTimeString('en-US', { hour12: false });
            line.innerHTML = `<span class="log-ts">${ts}</span><span class="log-dir">${dir.toUpperCase()}</span><span class="log-msg">${msg}</span>`;
            output.appendChild(line);
            output.scrollTop = output.scrollHeight;
            // Limit entries
            while (output.children.length > 300) output.removeChild(output.firstChild);
        });
    }

    // Manual command input
    if (input && sendBtn) {
        const sendCommand = () => {
            const cmd = input.value.trim();
            if (!cmd) return;
            fetch('/api/send-command', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ command: cmd }),
            });
            input.value = '';
        };
        sendBtn.addEventListener('click', sendCommand);
        input.addEventListener('keydown', e => { if (e.key === 'Enter') sendCommand(); });
    }
}
