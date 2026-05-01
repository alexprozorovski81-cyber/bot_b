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

// Canvas + browser fingerprint — вычисляется один раз и кэшируется
let _cachedFingerprint = null;

async function getDeviceFingerprint() {
    if (_cachedFingerprint) return _cachedFingerprint;
    try {
        const canvas = document.createElement('canvas');
        const ctx = canvas.getContext('2d');
        ctx.fillText('PredictBet', 10, 10);
        const canvasData = canvas.toDataURL();

        const raw = [
            canvasData,
            screen.width,
            screen.height,
            screen.colorDepth,
            (navigator.languages || []).join(','),
            Intl.DateTimeFormat().resolvedOptions().timeZone,
            navigator.hardwareConcurrency || 0,
        ].join('|');

        const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(raw));
        _cachedFingerprint = Array.from(new Uint8Array(buf))
            .map(b => b.toString(16).padStart(2, '0'))
            .join('');
    } catch {
        _cachedFingerprint = 'unavailable';
    }
    return _cachedFingerprint;
}

async function apiRequest(path, options = {}) {
    const initData = tg?.initData || '';
    const fp = await getDeviceFingerprint();

    const headers = {
        'Content-Type': 'application/json',
        'X-Telegram-Init-Data': initData,
        'X-Device-Fingerprint': fp,
        ...(options.headers || {}),
    };

    const res = await fetch(`${API_BASE}${path}`, { ...options, headers });

    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        const error = new Error(err.detail || `HTTP ${res.status}`);
        error.status = res.status;
        throw error;
    }
    return res.json();
}

const api = {
    me: () => apiRequest('/api/me'),
    categories: () => apiRequest('/api/categories'),
    events: (category = '', timeframe = '') => {
        const params = new URLSearchParams();
        if (category)  params.set('category', category);
        if (timeframe) params.set('timeframe', timeframe);
        const q = params.toString() ? `?${params}` : '';
        return apiRequest(`/api/events${q}`);
    },
    event: (id) => apiRequest(`/api/events/${id}`),
    leaderboard: (period = 'week') =>
        apiRequest(`/api/leaderboard?period=${encodeURIComponent(period)}`),
    activity: (limit = 30) =>
        apiRequest(`/api/activity?limit=${limit}`),
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
    comments: (eventId) => apiRequest(`/api/events/${eventId}/comments`),
    postComment: (eventId, text) =>
        apiRequest(`/api/events/${eventId}/comments`, {
            method: 'POST',
            body: JSON.stringify({ text }),
        }),
    cryptoDepositInit: (currency, amount_usd) =>
        apiRequest('/api/deposit/crypto/init', {
            method: 'POST',
            body: JSON.stringify({ currency, amount_usd }),
        }),
    withdrawRequest: (amount_coins, network, wallet_address) =>
        apiRequest('/api/withdraw/request', {
            method: 'POST',
            body: JSON.stringify({ amount_coins, network, wallet_address }),
        }),
    withdrawStatus: () => apiRequest('/api/withdraw/status'),
    withdrawInfo: () => apiRequest('/api/withdraw/info'),
    achievements: () => apiRequest('/api/me/achievements'),
};
