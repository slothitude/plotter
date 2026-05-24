/* main.js — Init + wire all modules */

import { getState, setState, subscribe } from './state.js';
import { connectWebSocket } from './websocket.js';
import { initRouter } from './router.js';
import { initStatusBar } from './components/status-bar.js';
import { initSetup } from './steps/setup.js';
import { initCreate } from './steps/create.js';
import { initPrepare } from './steps/prepare.js';
import { initPlot } from './steps/plot.js';
import { initConfig } from './steps/config.js';
import { initLogDrawer } from './log-drawer.js';
import { initCanvasPreview } from './components/canvas-preview.js';

document.addEventListener('DOMContentLoaded', () => {
    // Core
    connectWebSocket();
    initRouter();
    initStatusBar();
    initCanvasPreview();

    // Steps
    initSetup();
    initCreate();
    initPrepare();
    initPlot();
    initConfig();

    // UI
    initLogDrawer();

    // Load initial data
    loadInitialData();
});

async function loadInitialData() {
    // Load page size
    try {
        const data = await (await fetch('/api/page-size')).json();
        if (data.width) {
            setState({
                pageWidth: data.width,
                pageHeight: data.height,
                pagePreset: data.preset || '220mm',
                pageOffsetX: data.offset_x || 0,
                pageOffsetY: data.offset_y || 0,
            });
        }
    } catch (e) {}

    // Load calibration
    try {
        const cal = await (await fetch('/api/calibration')).json();
        setState({ calibration: cal });
    } catch (e) {}

    // Load settings for current tool
    try {
        const tool = getState().tool;
        const settings = await (await fetch(`/api/settings/${tool}`)).json();
        setState({ settings });
    } catch (e) {}
}
