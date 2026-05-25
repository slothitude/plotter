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
    setVal('set-water-cup-x', water.cup_x ?? 0);
    setVal('set-water-cup-y', water.cup_y ?? 0);
    setVal('set-water-cup-height', water.cup_height ?? 15);
    setVal('set-water-dip-depth', water.dip_depth ?? 15);
    setVal('set-water-interval', water.dip_interval ?? 50);
    setVal('set-water-duration', water.dip_time ?? 500);
    setVal('set-water-scrape-dist', water.scrape_distance ?? 15);
    setVal('set-water-scrape-speed', water.scrape_speed ?? 300);
    setChecked('set-water-two-pass', water.two_pass ?? true);

    const pass2 = water.pass2 || {};
    setVal('set-water-p2-draw', pass2.draw_speed ?? 800);
    setVal('set-water-p2-travel', pass2.travel_speed ?? 2500);
    setVal('set-water-p2-down', pass2.pen_down_z ?? 0);
    setVal('set-water-p2-lift', pass2.lift_height ?? 5);

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

function setChecked(id, val) {
    const el = document.getElementById(id);
    if (el) el.checked = val;
}

function getChecked(id) {
    const el = document.getElementById(id);
    return el ? el.checked : false;
}

function bindSettings() {
    const fields = [
        'set-travel-speed', 'set-draw-speed', 'set-z-travel-speed', 'set-z-draw-speed',
        'set-water-cup-x', 'set-water-cup-y', 'set-water-cup-height', 'set-water-dip-depth',
        'set-water-interval', 'set-water-duration', 'set-water-scrape-dist', 'set-water-scrape-speed',
        'set-water-two-pass',
        'set-water-p2-draw', 'set-water-p2-travel', 'set-water-p2-down', 'set-water-p2-lift',
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
            cup_x: getNum('set-water-cup-x', 0),
            cup_y: getNum('set-water-cup-y', 0),
            cup_height: getNum('set-water-cup-height', 15),
            dip_depth: getNum('set-water-dip-depth', 15),
            dip_interval: getNum('set-water-interval', 50),
            dip_time: getNum('set-water-duration', 500),
            scrape_distance: getNum('set-water-scrape-dist', 15),
            scrape_speed: getNum('set-water-scrape-speed', 300),
            two_pass: getChecked('set-water-two-pass'),
            pass2: {
                draw_speed: getNum('set-water-p2-draw', 800),
                travel_speed: getNum('set-water-p2-travel', 2500),
                pen_down_z: getNum('set-water-p2-down', 0),
                lift_height: getNum('set-water-p2-lift', 5),
            },
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
