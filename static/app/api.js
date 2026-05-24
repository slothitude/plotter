/* api.js — Fetch wrapper with error handling */

import { toast } from './lib/toast.js';

export async function api(url, opts = {}) {
    try {
        const res = await fetch(url, opts);
        if (!res.ok) {
            const text = await res.text().catch(() => '');
            throw new Error(text || `HTTP ${res.status}`);
        }
        return res;
    } catch (err) {
        if (opts.silent !== true) {
            console.error(`API ${url}:`, err);
        }
        throw err;
    }
}

export async function apiJSON(url, opts = {}) {
    const res = await api(url, opts);
    return res.json();
}

export async function apiPost(url, body) {
    return apiJSON(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
}
