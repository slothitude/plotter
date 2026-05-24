/* router.js — Step navigation + sidebar highlighting */

import { getState, setState, subscribe } from './state.js';

const STEPS = [
    { id: 1, key: 'setup', label: 'SETUP' },
    { id: 2, key: 'create', label: 'CREATE' },
    { id: 3, key: 'prepare', label: 'PREPARE' },
    { id: 4, key: 'plot', label: 'PLOT' },
    { id: 5, key: 'config', label: 'CONFIG' },
];

export function initRouter() {
    renderSidebar();

    subscribe('router', (changed) => {
        if (changed.currentStep !== undefined) {
            updateSidebar(changed.currentStep);
            showPanel(changed.currentStep);
        }
        if (changed.stepComplete !== undefined) {
            renderSidebar();
        }
    });

    // Navigate after subscribe is registered
    showPanel(getState().currentStep);
}

function renderSidebar() {
    const list = document.getElementById('step-list');
    if (!list) return;

    const s = getState();
    list.innerHTML = '';

    STEPS.forEach((step, i) => {
        const el = document.createElement('div');
        el.className = 'step-item' + (s.currentStep === step.id ? ' active' : '') + (s.stepComplete[step.id] ? ' completed' : '');
        el.dataset.step = step.id;
        el.innerHTML = `<span class="step-num">${s.stepComplete[step.id] ? '\u2713' : step.id}</span><span class="step-label">${step.label}</span>`;
        el.addEventListener('click', () => navigateTo(step.id));
        list.appendChild(el);

        if (i < STEPS.length - 1) {
            const conn = document.createElement('div');
            conn.className = 'step-connector';
            list.appendChild(conn);
        }
    });
}

function updateSidebar(step) {
    const s = getState();
    document.querySelectorAll('.step-item').forEach(el => {
        const id = parseInt(el.dataset.step);
        el.classList.toggle('active', id === step);
        el.classList.toggle('completed', !!s.stepComplete[id]);
        el.querySelector('.step-num').textContent = s.stepComplete[id] ? '\u2713' : id;
    });
}

function showPanel(step) {
    document.querySelectorAll('.step-panel').forEach(p => p.classList.remove('active'));
    const panel = document.getElementById(`step-${step}`);
    if (panel) panel.classList.add('active');
}

export function navigateTo(step) {
    setState({ currentStep: step });
}

export function nextStep() {
    const s = getState();
    if (s.currentStep < 5) navigateTo(s.currentStep + 1);
}
