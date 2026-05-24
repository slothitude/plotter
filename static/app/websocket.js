/* websocket.js — WebSocket connection with auto-reconnect */

import { getState, setState } from './state.js';

let ws = null;
let reconnectTimer = null;
const RECONNECT_DELAY = 3000;

export function connectWebSocket() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${protocol}//${location.host}/ws`;

    ws = new WebSocket(url);

    ws.onopen = () => {
        console.log('WebSocket connected');
        if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    };

    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            handleWSMessage(data);
        } catch (e) {
            console.error('WS parse error:', e);
        }
    };

    ws.onclose = () => {
        console.log('WebSocket closed, reconnecting...');
        ws = null;
        reconnectTimer = setTimeout(connectWebSocket, RECONNECT_DELAY);
    };

    ws.onerror = (err) => {
        console.error('WebSocket error:', err);
        if (ws) ws.close();
    };
}

function handleWSMessage(data) {
    switch (data.type) {
        case 'progress':
            setState({
                progress: { completed: data.completed, total: data.total, info: data.info },
                busy: data.completed < data.total,
            });
            break;
        case 'position':
            setState({ position: data.position });
            break;
        case 'status':
            if (data.connected !== undefined) setState({ connected: data.connected });
            if (data.busy !== undefined) setState({ busy: data.busy });
            if (data.position) setState({ position: data.position });
            break;
        case 'ink_stroke':
            // Live stroke from Slate capture
            setState({ inkLiveStroke: data });
            break;
        case 'log':
            // Append to log
            if (data.message) appendLog(data.direction || 'info', data.message);
            break;
    }
}

const logListeners = [];
let logEntries = [];

export function onLog(fn) {
    logListeners.push(fn);
    logEntries.forEach(e => fn(e.dir, e.msg));
}

function appendLog(dir, msg) {
    const entry = { dir, msg, ts: Date.now() };
    logEntries.push(entry);
    if (logEntries.length > 500) logEntries = logEntries.slice(-300);
    logListeners.forEach(fn => fn(dir, msg));
}

export { appendLog };
