/* state.js — Pub/sub reactive store */

const state = {
    // Connection
    connected: false,
    busy: false,
    port: '',

    // Position
    position: { X: 0, Y: 0, Z: 0 },

    // Current tool (single source of truth)
    tool: 'pencil',

    // Page
    pageWidth: 220,
    pageHeight: 220,
    pagePreset: '220mm',
    pageOffsetX: 0,
    pageOffsetY: 0,

    // Current SVG / G-code
    currentSvgId: null,
    gcodeGenerated: false,
    gcodePreview: '',
    gcodeLineCount: 0,

    // Transforms
    transform: {
        scale: 1.0,
        rotate: 0,
        translate_x: 0,
        translate_y: 0,
        mirror_x: false,
        mirror_y: false,
        optimize: true,
        simplify: false,
    },

    // Preview data
    polylines: null,
    toolpath: [],
    strokeCount: 0,
    stats: null,

    // Canvas overlays
    showDraw: true,
    showTravel: false,
    showGrid: true,

    // Workflow step (1-5)
    currentStep: 1,
    stepComplete: { 1: false, 2: false, 3: false, 4: false },

    // Ink / Slate
    capturing: false,
    livePlotActive: false,
    inkStrokes: [],

    // Jog
    jogStep: 10,
};

const listeners = new Map();

export function getState() {
    return state;
}

export function setState(updates) {
    const changed = {};
    for (const [key, val] of Object.entries(updates)) {
        if (state[key] !== val) {
            state[key] = val;
            changed[key] = val;
        }
    }
    if (Object.keys(changed).length) {
        // Notify global listeners
        for (const [id, fn] of listeners) {
            try { fn(changed, state); } catch (e) { console.error('State listener error:', e); }
        }
    }
}

export function subscribe(id, fn) {
    listeners.set(id, fn);
}

export function unsubscribe(id) {
    listeners.delete(id);
}
