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
        // Auto-update step completion
        const newComplete = { ...state.stepComplete };
        let completionChanged = false;

        // Step 2 (Create) complete when SVG loaded
        if (changed.currentSvgId !== undefined) {
            const complete = !!changed.currentSvgId;
            if (newComplete[2] !== complete) { newComplete[2] = complete; completionChanged = true; }
        }
        // Step 3 (Prepare) complete when G-code generated
        if (changed.gcodeGenerated !== undefined) {
            if (newComplete[3] !== changed.gcodeGenerated) { newComplete[3] = changed.gcodeGenerated; completionChanged = true; }
        }
        // Step 4 (Plot) complete when plotting finishes
        if (changed.busy === false && state.stepComplete[3]) {
            if (!newComplete[4]) { newComplete[4] = true; completionChanged = true; }
        }

        if (completionChanged) {
            state.stepComplete = newComplete;
            changed.stepComplete = newComplete;
        }

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
