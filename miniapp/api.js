/**
 * API-клиент для Mini App.
 * Все запросы автоматически проходят с initData для авторизации.
 */
const API_BASE = ''; // Same origin

const tg = window.Telegram?.WebApp;

if (tg) {
    tg.ready();
    tg.expand();
}

async function apiRequest(path, options = {}) {
    const initData = tg?.initData || '';

    const headers = {
        'Content-Type': 'application/json',
        'X-Telegram-Init-Data': initData,
        ...(options.headers || {}),
    };

    const res = await fetch(`${API_BASE}${path}`, { ...options, headers });

    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
}

const api = {
    me: () => apiRequest('/api/me'),
    categories: () => apiRequest('/api/categories'),
    events: (category = '') => {
        const q = category ? `?category=${encodeURIComponent(category)}` : '';
        return apiRequest(`/api/events${q}`);
    },
    event: (id) => apiRequest(`/api/events/${id}`),
    quote: (event_id, outcome_id, amount_rub) =>
        apiRequest('/api/bet/quote', {
            method: 'POST',
            body: JSON.stringify({ event_id, outcome_id, amount_rub }),
        }),
    placeBet: (event_id, outcome_id, amount_rub) =>
        apiRequest('/api/bet/place', {
            method: 'POST',
            body: JSON.stringify({ event_id, outcome_id, amount_rub }),
        }),
    myBets: () => apiRequest('/api/my/bets'),
};
