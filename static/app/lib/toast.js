/* toast.js — Toast notification system */

const TOAST_ICONS = {
    success: '\u2713',
    error: '\u2717',
    warn: '\u26A0',
    info: '\u2139',
};

export function toast(message, type = 'success') {
    const container = document.getElementById('toast-container');
    if (!container) return;
    const el = document.createElement('div');
    el.className = `toast toast-${type}`;
    el.innerHTML = `<span class="toast-icon">${TOAST_ICONS[type] || ''}</span><span class="toast-msg">${message}</span>`;
    container.appendChild(el);
    setTimeout(() => {
        el.classList.add('fadeout');
        setTimeout(() => el.remove(), 400);
    }, 3000);
}
