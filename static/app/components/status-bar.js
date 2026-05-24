/* status-bar.js — Top bar: connection, position, jog, tool selector */

import { getState, setState, subscribe } from '../state.js';
import { api, apiJSON, apiPost } from '../api.js';
import { toast } from '../lib/toast.js';
import { appendLog } from '../websocket.js';

export function initStatusBar() {
    loadPorts();
    bindConnect();
    bindHome();
    bindEstop();
    bindJog();
    bindToolSelector();
    bindPosition();

    subscribe('status-bar', (changed) => {
        if (changed.connected !== undefined) updateConnectionUI(changed.connected);
        if (changed.position !== undefined) updatePositionUI(changed.position);
        if (changed.tool !== undefined) updateToolUI(changed.tool);
    });
}

function loadPorts() {
    apiJSON('/api/ports').then(ports => {
        const sel = document.getElementById('port-select');
        if (!sel) return;
        sel.innerHTML = '<option value="">-- Port --</option>';
        ports.forEach(p => {
            const opt = document.createElement('option');
            opt.value = p.port;
            opt.textContent = `${p.port} \u2014 ${p.description}`;
            sel.appendChild(opt);
        });
    }).catch(() => {});
}

function bindConnect() {
    const btn = document.getElementById('btn-connect');
    if (!btn) return;
    btn.addEventListener('click', () => {
        const s = getState();
        if (s.connected) {
            api('/api/serial/disconnect', { method: 'POST' })
                .then(() => { setState({ connected: false }); toast('Disconnected', 'warn'); });
        } else {
            const port = document.getElementById('port-select')?.value;
            if (!port) return toast('Select a port first', 'warn');
            toast('Connecting...', 'info');
            apiPost('/api/serial/connect', { port })
                .then(data => {
                    if (data.ok) { setState({ connected: true }); toast('Connected', 'success'); }
                    else toast(data.error || 'Connection failed', 'error');
                })
                .catch(() => toast('Connection failed', 'error'));
        }
    });
}

function bindHome() {
    document.getElementById('btn-home')?.addEventListener('click', () => {
        apiJSON('/api/home', { method: 'POST' }).then(data => {
            if (data.position) setState({ position: data.position });
            if (data.ok) toast('Homed all axes', 'success');
        });
    });
}

function bindEstop() {
    document.getElementById('btn-estop')?.addEventListener('click', () => {
        api('/api/stop', { method: 'POST' }).then(() => toast('Emergency stop sent', 'error'));
    });
}

function bindJog() {
    document.querySelectorAll('.btn-jog').forEach(btn => {
        btn.addEventListener('click', () => {
            const axis = btn.dataset.axis;
            const dir = parseInt(btn.dataset.dir);
            const step = getState().jogStep;
            jog(axis, dir * step);
        });
    });

    document.getElementById('jog-step')?.addEventListener('change', e => {
        setState({ jogStep: parseFloat(e.target.value) });
    });
}

function jog(axis, distance) {
    appendLog('tx', `${axis}${distance > 0 ? '+' : ''}${distance.toFixed(3)}`);
    apiPost('/api/jog', { axis, distance, speed: axis === 'Z' ? 300 : 1500 })
        .then(data => {
            if (data.position) setState({ position: data.position });
            if (data.ok) appendLog('rx', 'OK');
        });
}

function bindToolSelector() {
    document.querySelectorAll('.tool-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            setState({ tool: btn.dataset.tool });
        });
    });
}

function bindPosition() {
    // Initial position from status
    apiJSON('/api/status').then(data => {
        if (data.connected) setState({ connected: true });
        if (data.position) setState({ position: data.position });
    }).catch(() => {});
}

function updateConnectionUI(connected) {
    const btn = document.getElementById('btn-connect');
    const badge = document.getElementById('conn-status');
    if (btn) {
        btn.textContent = connected ? 'DISCONNECT' : 'CONNECT';
        btn.className = connected ? 'btn btn-disconnect' : 'btn btn-connect';
    }
    if (badge) {
        badge.textContent = connected ? 'ONLINE' : 'OFFLINE';
        badge.className = 'conn-badge ' + (connected ? 'connected' : 'disconnected');
    }
    appendLog('info', connected ? 'Printer connected' : 'Printer disconnected');
}

function updatePositionUI(pos) {
    if (pos.X !== undefined) document.getElementById('pos-x') && (document.getElementById('pos-x').textContent = pos.X.toFixed(3));
    if (pos.Y !== undefined) document.getElementById('pos-y') && (document.getElementById('pos-y').textContent = pos.Y.toFixed(3));
    if (pos.Z !== undefined) document.getElementById('pos-z') && (document.getElementById('pos-z').textContent = pos.Z.toFixed(3));
}

function updateToolUI(tool) {
    document.querySelectorAll('.tool-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.tool === tool);
    });
}
