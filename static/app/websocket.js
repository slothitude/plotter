/* websocket.js — WebSocket connection with auto-reconnect */

import { getState, setState } from './state.js';
import { toast } from './lib/toast.js';

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
        case 'ink':
            // Live stroke from Slate capture — accumulate into current stroke
            setState({ liveCurrentStroke: data.points || [] });
            break;
        case 'ink_stroke_complete':
            // Stroke finished — move to permanent layer
            {
                const s = getState();
                const finished = s.liveCurrentStroke || [];
                if (finished.length > 0) {
                    setState({
                        liveStrokes: [...s.liveStrokes, finished],
                        liveCurrentStroke: null,
                    });
                }
            }
            break;
        case 'ink_event':
            // Button/pause/hover/calibration events from Slate
            if (data.event === 'button') {
                if (data.action === 'pause') {
                    toast('Plotter paused (Slate button)', 'info');
                } else if (data.action === 'resume') {
                    toast('Plotter resumed (Slate button)', 'info');
                }
            } else if (data.event === 'hover') {
                // Hover position from Slate (pen detected, not touching)
                if (data.points) {
                    setState({ hoverPosition: data.points });
                }
            } else if (data.event === 'calibration') {
                // Proximity calibration events
                if (data.action === 'started') {
                    setState({ proxCalActive: true, proxCalStep: 0, proxCalTarget: null });
                } else if (data.action === 'move') {
                    setState({
                        proxCalStep: data.step,
                        proxCalTarget: { hotend_x: data.hotend_x, hotend_y: data.hotend_y },
                    });
                } else if (data.action === 'captured') {
                    toast(`Point ${data.step}/3 captured`, 'success');
                } else if (data.action === 'finished') {
                    setState({ proxCalActive: false, proxCalStep: 0, proxCalTarget: null, hoverPosition: null });
                    toast(`Page offset: (${data.offset_x}, ${data.offset_y})mm — max error ${data.max_residual}mm`, 'success');
                } else if (data.action === 'cancelled') {
                    setState({ proxCalActive: false, proxCalStep: 0, proxCalTarget: null, hoverPosition: null });
                    toast('Calibration cancelled', 'info');
                }
            } else if (data.event === 'jog') {
                // Jog mode pen state update
                const penEl = document.getElementById('jog-pen-state');
                if (data.pen_toggle && penEl) {
                    penEl.textContent = `Pen: ${data.pen_down ? 'DOWN' : 'UP'}`;
                    penEl.style.color = data.pen_down ? '#ff6b6b' : '#00dcdc';
                }
            }
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
