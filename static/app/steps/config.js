/* config.js — Step 5: Tool profiles, movement speeds, water, fill/hatch */

import { getState, setState, subscribe } from '../state.js';
import { apiJSON, apiPost } from '../api.js';
import { toast } from '../lib/toast.js';

export function initConfig() {
    loadSettings();
    bindSettings();
    initParkPosition();

    // Reload when tool changes
    subscribe('config', (changed) => {
        if (changed.tool !== undefined) loadSettings();
    });
}

function loadSettings() {
    const tool = getState().tool;
    apiJSON(`/api/settings/${tool}`).then(settings => {
        setState({ settings });
        populateFields(settings);
    }).catch(() => {});
}

function populateFields(settings) {
    if (!settings) return;

    const mv = settings.movement || {};
    setVal('set-travel-speed', mv.travel_speed);
    setVal('set-draw-speed', mv.draw_speed);
    setVal('set-z-travel-speed', mv.z_travel_speed);
    setVal('set-z-draw-speed', mv.z_draw_speed);

    const water = settings.water || {};
    setVal('set-water-interval', water.dip_interval || 0);
    setVal('set-water-duration', water.dip_duration || 500);

    const fill = settings.fill || {};
    setVal('set-fill-mode', fill.mode || 'none');
    setVal('set-fill-spacing', fill.spacing || 2);
    setVal('set-fill-angle', fill.angle || 45);
    setVal('set-fill-inset', fill.inset || 0);
}

function setVal(id, val) {
    const el = document.getElementById(id);
    if (el) el.value = val;
}

function bindSettings() {
    const fields = [
        'set-travel-speed', 'set-draw-speed', 'set-z-travel-speed', 'set-z-draw-speed',
        'set-water-interval', 'set-water-duration',
        'set-fill-mode', 'set-fill-spacing', 'set-fill-angle', 'set-fill-inset',
    ];

    fields.forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;

        const evt = el.tagName === 'SELECT' ? 'change' : 'change';
        el.addEventListener(evt, () => {
            autoSave();
        });
    });
}

function autoSave() {
    const tool = getState().tool;

    const body = {
        movement: {
            travel_speed: getNum('set-travel-speed', 3000),
            draw_speed: getNum('set-draw-speed', 1500),
            z_travel_speed: getNum('set-z-travel-speed', 3000),
            z_draw_speed: getNum('set-z-draw-speed', 1000),
        },
        water: {
            dip_interval: getNum('set-water-interval', 0),
            dip_duration: getNum('set-water-duration', 500),
        },
        fill: {
            mode: getStr('set-fill-mode', 'none'),
            spacing: getNum('set-fill-spacing', 2),
            angle: getNum('set-fill-angle', 45),
            inset: getNum('set-fill-inset', 0),
        },
    };

    apiPost(`/api/settings/${tool}`, body).then(data => {
        if (data.ok) toast('Settings saved', 'success');
    }).catch(() => {});
}

function initParkPosition() {
    document.getElementById('btn-park-goto')?.addEventListener('click', () => {
        if (!getState().connected) return toast('Connect printer first', 'warn');
        apiPost('/api/tool-change-park', { action: 'goto', tool: getState().tool })
            .then(data => {
                if (data.error) return toast(data.error, 'error');
                toast('Moved to park position', 'success');
            });
    });

    document.getElementById('btn-park-save')?.addEventListener('click', () => {
        apiPost('/api/tool-change-park', { action: 'save', tool: getState().tool })
            .then(data => {
                if (data.error) return toast(data.error, 'error');
                toast('Park position saved', 'success');
            });
    });
}

function getNum(id, def) {
    return parseFloat(document.getElementById(id)?.value) || def;
}

function getStr(id, def) {
    return document.getElementById(id)?.value || def;
}
