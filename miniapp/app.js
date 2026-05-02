/**
 * Главная логика Mini App — PredictBet
 */

window.state = {
    me: null,
    categories: [],
    events: [],
    activeCategory: '',
    activeTimeframe: '',
    selectedEvent: null,
    selectedOutcome: null,
    quoteTimer: null,
};

// ═══════════════════════════════════════
// Тема (light / dark)
// ═══════════════════════════════════════
const THEME_KEY = 'predictbet_theme';

function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    const btn = document.getElementById('theme-toggle');
    if (btn) btn.textContent = theme === 'dark' ? '☀️' : '🌙';
}

function loadTheme() {
    const tg = window.Telegram?.WebApp;
    // 1. CloudStorage
    if (tg?.CloudStorage) {
        tg.CloudStorage.getItem(THEME_KEY, (err, val) => {
            if (!err && val) { applyTheme(val); return; }
            // 2. localStorage fallback
            const ls = localStorage.getItem(THEME_KEY);
            if (ls) { applyTheme(ls); return; }
            // 3. Telegram colorScheme
            applyTheme(tg.colorScheme === 'dark' ? 'dark' : 'light');
        });
    } else {
        const ls = localStorage.getItem(THEME_KEY);
        if (ls) { applyTheme(ls); return; }
        const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
        applyTheme(prefersDark ? 'dark' : 'light');
    }
}

function saveTheme(theme) {
    localStorage.setItem(THEME_KEY, theme);
    const tg = window.Telegram?.WebApp;
    if (tg?.CloudStorage) tg.CloudStorage.setItem(THEME_KEY, theme, () => {});
}

window.toggleTheme = function() {
    const current = document.documentElement.getAttribute('data-theme') || 'light';
    const next = current === 'dark' ? 'light' : 'dark';
    applyTheme(next);
    saveTheme(next);
};

// ═══════════════════════════════════════
// Утилиты
// ═══════════════════════════════════════
const fmtMoney = (v) =>
    new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 }).format(v) + ' ₽';

const fmtPercent = (v) => `${Math.round(v * 100)}%`;

const fmtDeadline = (iso) => {
    const date = new Date(iso);
    const diff = date - Date.now();
    const days = Math.floor(diff / 86400000);
    if (days < 0)  return 'закрыто';
    if (days === 0) return 'сегодня';
    if (days === 1) return 'завтра';
    if (days < 7)  return `${days} дн`;
    if (days < 30) return `${Math.floor(days / 7)} нед`;
    return date.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
};

function fmtCountdown(iso) {
    const diff = new Date(iso) - new Date();
    if (diff <= 0) return 'Завершено';
    const d = Math.floor(diff / 86400000);
    const h = Math.floor((diff % 86400000) / 3600000);
    const m = Math.floor((diff % 3600000) / 60000);
    const s = Math.floor((diff % 60000) / 1000);
    return `${d}д ${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
}

const fmtVolume = (v) => {
    if (v >= 1_000_000) return (v / 1_000_000).toFixed(1) + 'M ₽';
    if (v >= 1_000)     return Math.round(v / 1_000) + 'K ₽';
    return Math.round(v) + ' ₽';
};

window.toast = (message, type = 'info') => {
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.textContent = message;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 3000);
};

// ═══════════════════════════════════════
// Загрузка данных
// ═══════════════════════════════════════
async function loadInitial() {
    renderSkeletons();
    try {
        const [me, categories, events] = await Promise.all([
            api.me(),
            api.categories(),
            api.events(state.activeCategory, state.activeTimeframe),
        ]);
        state.me = me;
        state.categories = categories;
        state.events = events;
        renderBalance();
        renderCategories();
        renderTimeframeTabs();
        renderEvents();
        loadActivityTicker();
    } catch (e) {
        console.error(e);
        renderError('Не удалось загрузить данные');
    }
}

// ═══════════════════════════════════════
// Баланс
// ═══════════════════════════════════════
window.renderBalance = function() {
    const el = document.getElementById('balance');
    if (state.me) el.textContent = fmtMoney(state.me.balance_rub);
};

// ═══════════════════════════════════════
// Категории
// ═══════════════════════════════════════
function renderCategories() {
    const nav = document.getElementById('categories');
    nav.innerHTML = '';

    const allBtn = document.createElement('button');
    allBtn.className = 'cat-pill' + (state.activeCategory === '' ? ' active' : '');
    allBtn.dataset.category = '';
    allBtn.innerHTML = '<span>🌍</span> Все';
    allBtn.onclick = () => switchCategory('');
    nav.appendChild(allBtn);

    state.categories.forEach((cat) => {
        const btn = document.createElement('button');
        btn.className = 'cat-pill' + (state.activeCategory === cat.slug ? ' active' : '');
        btn.innerHTML = `<span>${cat.emoji}</span> ${cat.name}`;
        btn.onclick = () => switchCategory(cat.slug);
        nav.appendChild(btn);
    });
}

async function switchCategory(slug) {
    state.activeCategory = slug;
    renderCategories();
    renderSkeletons();
    try {
        state.events = await api.events(slug, state.activeTimeframe);
        renderEvents();
    } catch (e) {
        renderError(e.message);
    }
}

// ═══════════════════════════════════════
// Timeframe tabs
// ═══════════════════════════════════════
function renderTimeframeTabs() {
    document.querySelectorAll('.timeframe-tab').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.timeframe === state.activeTimeframe);
    });
}

async function switchTimeframe(tf) {
    state.activeTimeframe = tf;
    renderTimeframeTabs();
    renderSkeletons();
    try {
        state.events = await api.events(state.activeCategory, tf);
        renderEvents();
    } catch (e) {
        renderError(e.message);
    }
}

// ═══════════════════════════════════════
// Activity ticker
// ═══════════════════════════════════════
async function loadActivityTicker() {
    const ticker = document.getElementById('activity-ticker');
    const scroll = document.getElementById('activity-ticker-scroll');
    if (!ticker || !scroll) return;
    try {
        const items = await api.activity(20);
        if (!items || !items.length) { ticker.style.display = 'none'; return; }
        scroll.innerHTML = items.map(it => `
            <div class="activity-ticker-item">
                <span class="activity-ticker-user">${it.username}</span>
                <span style="color:var(--text-faint)">→</span>
                <span class="activity-ticker-outcome">${it.outcome_title}</span>
                <span class="activity-ticker-event">«${it.event_title.slice(0, 35)}${it.event_title.length > 35 ? '…' : ''}»</span>
            </div>
        `).join('');
        ticker.style.display = '';
        // Auto-scroll every 4s — скроллим только сам контейнер, не страницу
        let idx = 0;
        setInterval(() => {
            idx = (idx + 1) % items.length;
            const item = scroll.children[idx];
            if (item) {
                scroll.scrollTo({ left: item.offsetLeft, behavior: 'smooth' });
            }
        }, 4000);
    } catch (_) {
        ticker.style.display = 'none';
    }}

// ═══════════════════════════════════════
// Список событий
// ═══════════════════════════════════════
function renderEvents() {
    const feed = document.getElementById('events-feed');
    if (!state.events.length) {
        feed.innerHTML = `
            <div class="empty-state">
                <div style="font-size:48px;margin-bottom:12px">🔮</div>
                <h3>Нет активных рынков</h3>
                <p>Скоро появятся новые события</p>
            </div>`;
        return;
    }
    feed.innerHTML = '';
    state.events.forEach((event) => feed.appendChild(buildEventCard(event)));
}

function renderSkeletons() {
    const feed = document.getElementById('events-feed');
    feed.innerHTML = '';
    for (let i = 0; i < 3; i++) {
        feed.innerHTML += `
            <div class="event-card" style="pointer-events:none">
                <div class="event-header">
                    <div class="event-image skeleton" style="min-height:54px"></div>
                    <div class="event-info">
                        <div class="skeleton" style="height:16px;width:80%;margin-bottom:8px;border-radius:6px"></div>
                        <div class="skeleton" style="height:12px;width:50%;border-radius:6px"></div>
                    </div>
                </div>
                <div class="event-outcomes">
                    <div class="skeleton" style="height:60px;border-radius:12px"></div>
                    <div class="skeleton" style="height:60px;border-radius:12px"></div>
                </div>
            </div>`;
    }
}

// ═══════════════════════════════════════
// Карточка события
// ═══════════════════════════════════════

const EMOJI_MAP = {
    политика: '🏛️', экономика: '📈', спорт: '⚽', крипто: '₿',
    технологии: '🚀', мир: '🌍', наука: '🔬', default: '🎯',
};

function getCategoryEmoji(event) {
    if (!state.categories.length) return EMOJI_MAP.default;
    const cat = state.categories.find(c => c.id === event.category_id);
    if (!cat) return EMOJI_MAP.default;
    return cat.emoji || EMOJI_MAP[cat.slug] || EMOJI_MAP.default;
}

function buildEventCard(event) {
    const tpl = document.getElementById('event-card-template');
    const card = tpl.content.cloneNode(true).querySelector('.event-card');

    // ── Photo banner (Polymarket style) ──────────────────────────────
    const photoWrap = card.querySelector('.event-card-photo-wrap');
    const emoji = getCategoryEmoji(event);

    if (event.image_url) {
        // Try loading image; fall back to emoji placeholder
        const img = document.createElement('img');
        img.className = 'event-card-photo';
        img.alt = '';
        img.src = event.image_url;
        img.onerror = () => {
            img.replaceWith(makePlaceholder(emoji));
        };
        photoWrap.appendChild(img);
    } else {
        photoWrap.appendChild(makePlaceholder(emoji));
    }

    // Timeframe badge for intraday events
    if (event.timeframe === 'intraday') {
        const badge = document.createElement('span');
        badge.className = 'timeframe-badge';
        badge.textContent = '⚡ СЕЙЧАС';
        photoWrap.appendChild(badge);
    }

    // Make whole photo clickable
    photoWrap.style.cursor = 'pointer';
    photoWrap.onclick = () => openEventDetail(event.id);

    // ── Card body ─────────────────────────────────────────────────────
    const titleEl = card.querySelector('.event-title');
    titleEl.textContent = event.title;
    titleEl.style.cursor = 'pointer';
    titleEl.onclick = () => openEventDetail(event.id);

    const isClosingSoon = (new Date(event.closes_at) - Date.now()) < 86400000 * 2;
    const players = event.players_count || 0;
    const volume = event.volume_rub || 0;

    card.querySelector('.event-volume').innerHTML =
        `👥 ${players} · 💰 ${fmtVolume(volume)}`;
    const deadlineEl = card.querySelector('.event-deadline');
    deadlineEl.innerHTML = isClosingSoon
        ? `<span style="color:var(--no)">⏰ <span data-closes-at="${event.closes_at}">${fmtCountdown(event.closes_at)}</span></span>`
        : `⏱ <span data-closes-at="${event.closes_at}">${fmtCountdown(event.closes_at)}</span>`;

    // ── Footer: YES / NO buttons ──────────────────────────────────────
    const footer = card.querySelector('.event-card-footer');
    const totalPrice = event.outcomes.reduce((s, o) => s + o.price, 0) || 1;

    // For binary events show YES/NO style; for multi show all as pills
    const isBinary = event.outcomes.length === 2;

    event.outcomes.forEach((outcome, i) => {
        const pct = Math.round((outcome.price / totalPrice) * 100);
        const btn = document.createElement('button');
        btn.className = 'outcome-btn-bottom';

        if (isBinary) {
            const lower = outcome.title.toLowerCase();
            if (lower === 'да' || lower.includes('выше') || lower === 'yes' || i === 0) btn.classList.add('yes');
            else btn.classList.add('no');
        } else {
            btn.style.cssText = 'flex:1;border:1px solid var(--border);border-radius:10px;padding:8px 4px;font-family:inherit;font-size:12px;font-weight:700;cursor:pointer;background:var(--bg-hover);color:var(--text)';
        }

        btn.innerHTML = `<span class="btn-label">${outcome.title.slice(0, 16)}</span><span class="btn-pct">${pct}%</span>`;
        btn.onclick = () => openBetModal(event, outcome);
        footer.appendChild(btn);
    });

    return card;
}

function makePlaceholder(emoji) {
    const div = document.createElement('div');
    div.className = 'event-card-photo-placeholder';
    div.textContent = emoji;
    return div;
}

function renderError(msg) {
    const feed = document.getElementById('events-feed');
    feed.innerHTML = `
        <div class="empty-state">
            <div style="font-size:40px;margin-bottom:12px">⚠️</div>
            <h3>Ошибка</h3>
            <p>${msg}</p>
        </div>`;
}

// ═══════════════════════════════════════
// Модалка ставки
// ═══════════════════════════════════════
window.openBetModal = function(event, outcome) {
    state.selectedEvent = event;
    state.selectedOutcome = outcome;

    document.getElementById('bet-event-title').textContent = event.title;
    document.getElementById('bet-outcome-title').textContent = outcome.title;

    const modal = document.getElementById('bet-modal');
    const outcomeBlock = modal.querySelector('.bet-modal-outcome');

    // Цвет исхода
    const lower = outcome.title.toLowerCase();
    outcomeBlock.style.background = '';
    outcomeBlock.style.borderColor = '';
    if (lower === 'да' || lower === 'yes') {
        outcomeBlock.style.background = 'var(--yes-bg)';
        outcomeBlock.style.borderColor = 'var(--yes-border)';
        modal.querySelector('.outcome-value').style.color = 'var(--yes)';
    } else if (lower === 'нет' || lower === 'no') {
        outcomeBlock.style.background = 'var(--no-bg)';
        outcomeBlock.style.borderColor = 'var(--no-border)';
        modal.querySelector('.outcome-value').style.color = 'var(--no)';
    } else {
        modal.querySelector('.outcome-value').style.color = 'var(--accent)';
    }

    const input = document.getElementById('bet-amount');
    input.value = '';
    document.getElementById('quote-shares').textContent = '—';
    document.getElementById('quote-odds').textContent = '—';
    document.getElementById('quote-payout').textContent = '— ₽';
    const btn = document.getElementById('bet-confirm-btn');
    btn.disabled = true;
    btn.textContent = 'Введи сумму';

    modal.hidden = false;
    setTimeout(() => input.focus(), 150);

    if (window.Telegram?.WebApp?.HapticFeedback)
        window.Telegram.WebApp.HapticFeedback.selectionChanged();
};

function closeBetModal() {
    document.getElementById('bet-modal').hidden = true;
    state.selectedEvent = null;
    state.selectedOutcome = null;
}

async function updateQuote() {
    const amount = parseFloat(document.getElementById('bet-amount').value);
    const btn = document.getElementById('bet-confirm-btn');

    if (!amount || amount < 10) {
        document.getElementById('quote-shares').textContent = '—';
        document.getElementById('quote-odds').textContent = '—';
        document.getElementById('quote-payout').textContent = '— ₽';
        btn.disabled = true;
        btn.textContent = 'Введи сумму (мин. 10 ₽)';
        return;
    }

    if (state.me && amount > state.me.balance_rub) {
        btn.disabled = true;
        btn.textContent = `Недостаточно средств`;
        return;
    }

    btn.textContent = 'Считаем...';

    try {
        const q = await api.quote(
            state.selectedEvent.id,
            state.selectedOutcome.id,
            amount,
        );
        document.getElementById('quote-shares').textContent = q.shares.toFixed(2);
        document.getElementById('quote-odds').textContent = `×${q.avg_odds.toFixed(3)}`;
        document.getElementById('quote-payout').textContent = fmtMoney(q.potential_payout);
        btn.disabled = false;
        btn.textContent = `Поставить ${fmtMoney(amount)}`;
    } catch (e) {
        btn.disabled = true;
        btn.textContent = 'Ошибка котировки';
    }
}

async function confirmBet() {
    const amount = parseFloat(document.getElementById('bet-amount').value);
    const btn = document.getElementById('bet-confirm-btn');
    btn.disabled = true;
    btn.textContent = 'Размещаем...';

    try {
        const result = await api.placeBet(
            state.selectedEvent.id,
            state.selectedOutcome.id,
            amount,
        );
        state.me.balance_rub = result.new_balance;
        renderBalance();
        toast(`✅ Ставка ${fmtMoney(amount)} принята!`, 'success');
        closeBetModal();
        if (window.Telegram?.WebApp?.HapticFeedback)
            window.Telegram.WebApp.HapticFeedback.notificationOccurred('success');
        await switchCategory(state.activeCategory);
    } catch (e) {
        toast(`Ошибка: ${e.message}`, 'error');
        btn.disabled = false;
        btn.textContent = `Поставить ${fmtMoney(amount)}`;
        if (window.Telegram?.WebApp?.HapticFeedback)
            window.Telegram.WebApp.HapticFeedback.notificationOccurred('error');
    }
}

// ═══════════════════════════════════════
// Leaderboard
// ═══════════════════════════════════════
let _activeLbPeriod = 'week';

async function loadLeaderboard(period = _activeLbPeriod) {
    _activeLbPeriod = period;

    // Update period tabs
    document.querySelectorAll('.leaderboard-period-tab').forEach(t => {
        t.classList.toggle('active', t.dataset.period === period);
    });

    const list = document.getElementById('leaderboard-list');
    if (!list) return;
    list.innerHTML = '<div class="loading"><div class="spinner"></div></div>';

    try {
        const rows = await api.leaderboard(period);
        if (!rows.length) {
            list.innerHTML = '<div class="empty-state"><h3>Нет данных</h3><p>Данных за этот период ещё нет</p></div>';
            return;
        }

        const rankLabels = { 1: '🥇', 2: '🥈', 3: '🥉' };
        const rankClass  = { 1: 'gold', 2: 'silver', 3: 'bronze' };

        list.innerHTML = rows.map(r => {
            const rankStr  = rankLabels[r.rank] || r.rank;
            const rankCls  = rankClass[r.rank] || '';
            const profit   = r.net_profit;
            const pSign    = profit >= 0 ? '+' : '';
            const pClass   = profit >= 0 ? 'positive' : 'negative';
            const winRate  = r.bets_count > 0 ? Math.round((r.win_count / r.bets_count) * 100) : 0;
            return `
                <div class="leaderboard-row">
                    <div class="leaderboard-rank ${rankCls}">${rankStr}</div>
                    <div class="leaderboard-info">
                        <div class="leaderboard-name">${r.display_name}</div>
                        <div class="leaderboard-bets">${r.bets_count} ставок · ${winRate}% побед</div>
                    </div>
                    <div class="leaderboard-profit ${pClass}">${pSign}${Math.round(profit).toLocaleString('ru-RU')} ₽</div>
                </div>`;
        }).join('');
    } catch (e) {
        list.innerHTML = `<div class="empty-state"><h3>Ошибка</h3><p>${e.message}</p></div>`;
    }
}

// ═══════════════════════════════════════
// Bottom Nav
// ═══════════════════════════════════════
function showScreen(tab) {
    const marketsScreen    = document.getElementById('markets-screen');
    const leaderboardScreen = document.getElementById('leaderboard-screen');
    const catEl            = document.getElementById('categories');
    const tfEl             = document.getElementById('timeframe-tabs');

    // Hide all screens
    if (marketsScreen)    marketsScreen.hidden = true;
    if (leaderboardScreen) leaderboardScreen.hidden = true;

    // Show events-feed placeholder for portfolio / profile
    const feedEl = document.getElementById('events-feed');

    if (tab === 'markets') {
        if (marketsScreen) marketsScreen.hidden = false;
        catEl.style.display = '';
        tfEl.style.display = '';
    } else if (tab === 'leaderboard') {
        if (leaderboardScreen) leaderboardScreen.hidden = false;
        catEl.style.display = 'none';
        tfEl.style.display = 'none';
    } else {
        // portfolio / profile — re-use events-feed inside markets-screen
        if (marketsScreen) marketsScreen.hidden = false;
        catEl.style.display = 'none';
        tfEl.style.display = 'none';
    }
}

function setupBottomNav() {
    document.querySelectorAll('.nav-btn').forEach((btn) => {
        btn.onclick = () => {
            document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            const tab = btn.dataset.tab;
            showScreen(tab);

            if (tab === 'portfolio')   loadPortfolio();
            else if (tab === 'profile') loadProfile();
            else if (tab === 'leaderboard') loadLeaderboard(_activeLbPeriod);
            else loadInitial();
        };
    });
}

// ═══════════════════════════════════════
// Портфель
// ═══════════════════════════════════════
async function loadPortfolio() {
    const feed = document.getElementById('events-feed');
    feed.innerHTML = '<div class="loading"><div class="spinner"></div><p>Загружаем ставки...</p></div>';
    try {
        const bets = await api.myBets();
        if (!bets.length) {
            feed.innerHTML = `
                <div class="empty-state">
                    <div style="font-size:48px;margin-bottom:12px">🎯</div>
                    <h3>Ставок пока нет</h3>
                    <p>Сделай первый прогноз на вкладке «Рынки»</p>
                </div>`;
            return;
        }

        // Считаем сводку
        const totalBet = bets.reduce((s, b) => s + b.amount_rub, 0);
        const totalPayout = bets.filter(b => b.payout_rub).reduce((s, b) => s + b.payout_rub, 0);
        const won = bets.filter(b => b.is_settled && b.payout_rub > 0).length;
        const settled = bets.filter(b => b.is_settled).length;

        feed.innerHTML = `
            <div class="profile-grid" style="margin-bottom:12px">
                <div class="profile-stat">
                    <div class="profile-stat-label">Поставлено</div>
                    <div class="profile-stat-value" style="font-size:16px">${fmtMoney(totalBet)}</div>
                </div>
                <div class="profile-stat">
                    <div class="profile-stat-label">Выиграно</div>
                    <div class="profile-stat-value green" style="font-size:16px">${won}/${settled}</div>
                </div>
            </div>
            <div class="section-title">История ставок</div>
        `;

        bets.forEach((bet) => {
            const card = document.createElement('div');
            card.className = 'bet-history-card';

            let statusHtml = '';
            if (!bet.is_settled) {
                const badge = bet.timeframe === 'intraday' ? '⚡ В игре' : '🕐 В игре';
                statusHtml = `<span class="bet-status pending">${badge}</span>`;
            } else if (bet.payout_rub > 0) {
                statusHtml = `
                    <div style="text-align:right">
                        <div class="bet-history-payout">+${fmtMoney(bet.payout_rub)}</div>
                        <span class="bet-status won">Выигрыш</span>
                    </div>`;
            } else {
                statusHtml = `<span class="bet-status lost">Проигрыш</span>`;
            }

            card.innerHTML = `
                <div class="bet-history-top">
                    <div class="bet-history-event">${bet.event_title}</div>
                </div>
                <div class="bet-history-outcome">🎯 ${bet.outcome_title} · ×${bet.avg_odds.toFixed(2)}</div>
                <div class="bet-history-bottom">
                    <div class="bet-history-amount">${fmtMoney(bet.amount_rub)} · ${bet.shares.toFixed(1)} акций</div>
                    ${statusHtml}
                </div>
            `;
            feed.appendChild(card);
        });
    } catch (e) {
        renderError(e.message);
    }
}

// ═══════════════════════════════════════
// Профиль
// ═══════════════════════════════════════
async function loadProfile() {
    const feed = document.getElementById('events-feed');
    feed.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
    try {
        const [me, achievements] = await Promise.all([api.me(), api.achievements()]);
        state.me = me;
        renderBalance();

        const profitColor = me.stats.profit >= 0 ? 'var(--yes)' : 'var(--no)';
        const profitSign  = me.stats.profit >= 0 ? '+' : '';
        const avatar = (me.first_name || 'P')[0].toUpperCase();

        const earnedCount = achievements.filter(a => a.earned).length;
        const achievHtml = achievements.map(a => {
            const rarityClass = `ach-${a.rarity}`;
            const title = a.earned
                ? `${a.name}\n${a.description}\n${new Date(a.unlocked_at).toLocaleDateString('ru-RU')}`
                : `${a.name}\n${a.description}\n(не получено)`;
            return `<div class="ach-badge ${rarityClass} ${a.earned ? 'earned' : 'locked'}" title="${title}">
                <span class="ach-emoji">${a.emoji}</span>
            </div>`;
        }).join('');

        feed.innerHTML = `
            <div class="event-card" style="margin-bottom:12px">
                <div class="event-header" style="margin-bottom:16px">
                    <div class="event-image"
                         style="background:linear-gradient(135deg,#6366f1,#a855f7);
                                display:flex;align-items:center;justify-content:center;
                                color:#fff;font-size:22px;font-weight:800">
                        ${avatar}
                    </div>
                    <div class="event-info">
                        <h3 class="event-title" style="margin-bottom:2px">${me.first_name || 'Игрок'}</h3>
                        <div class="event-meta">
                            ${me.username ? `<span>@${me.username}</span>` : ''}
                            <span style="color:var(--text-faint)">ID ${me.telegram_id}</span>
                        </div>
                    </div>
                </div>

                <div class="profile-grid">
                    <div class="profile-stat">
                        <div class="profile-stat-label">Баланс</div>
                        <div class="profile-stat-value green">${fmtMoney(me.balance_rub)}</div>
                    </div>
                    <div class="profile-stat">
                        <div class="profile-stat-label">Винрейт</div>
                        <div class="profile-stat-value purple">${me.stats.winrate}%</div>
                    </div>
                    <div class="profile-stat">
                        <div class="profile-stat-label">Ставок</div>
                        <div class="profile-stat-value">${me.stats.total_bets}</div>
                    </div>
                    <div class="profile-stat">
                        <div class="profile-stat-label">Прибыль</div>
                        <div class="profile-stat-value" style="color:${profitColor}">
                            ${profitSign}${fmtMoney(me.stats.profit)}
                        </div>
                    </div>
                </div>
            </div>

            <div class="profile-action-row">
                <button class="profile-action-btn deposit" onclick="openDepositModal()">
                    💎 Пополнить
                </button>
                <button class="profile-action-btn withdraw" onclick="openWithdrawModal()">
                    💸 Вывести
                </button>
                <button class="profile-action-btn howto" onclick="openHowItWorks()">
                    ❓ Как работает
                </button>
            </div>

            <div class="achievements-section">
                <div class="section-title">🏅 Ачивки <span class="ach-count">${earnedCount}/${achievements.length}</span></div>
                <div class="ach-grid">${achievHtml}</div>
            </div>
        `;
    } catch (e) {
        renderError(e.message);
    }
}

// ═══════════════════════════════════════
// Модалка вывода
// ═══════════════════════════════════════
let _withdrawRate = null;

window.openWithdrawModal = async function() {
    const modal = document.getElementById('withdraw-modal');
    if (!modal) return;
    document.getElementById('withdraw-amount').value = '';
    document.getElementById('withdraw-wallet').value = '';
    document.getElementById('withdraw-network').value = 'usdt_ton';
    const err = modal.querySelector('.withdraw-error');
    if (err) err.textContent = '';
    modal.hidden = false;

    // Загружаем актуальный курс
    try {
        const info = await api.withdrawInfo();
        _withdrawRate = info.rate_rub;
        const rateEl = document.getElementById('withdraw-rate-info');
        if (rateEl) rateEl.textContent = `Курс: 1 USDT ≈ ${_withdrawRate.toFixed(0)} монет`;
        _updateWithdrawConversion();
    } catch (_) {}
};

function _updateWithdrawConversion() {
    const amountEl = document.getElementById('withdraw-amount');
    const convEl = document.getElementById('withdraw-usdt-preview');
    if (!convEl || !amountEl) return;
    const coins = parseFloat(amountEl.value) || 0;
    if (_withdrawRate && coins > 0) {
        const usdt = (coins / _withdrawRate).toFixed(2);
        convEl.textContent = `≈ ${usdt} USDT`;
    } else {
        convEl.textContent = '';
    }
}

window.closeWithdrawModal = function() {
    const modal = document.getElementById('withdraw-modal');
    if (modal) modal.hidden = true;
};

window.submitWithdraw = async function() {
    const amount = parseFloat(document.getElementById('withdraw-amount').value);
    const network = document.getElementById('withdraw-network').value;
    const wallet = document.getElementById('withdraw-wallet').value.trim();
    const errEl = document.querySelector('#withdraw-modal .withdraw-error');
    const btn = document.getElementById('withdraw-submit-btn');

    if (!amount || amount < 100) { errEl.textContent = 'Минимум 100 монет'; return; }
    if (!wallet || wallet.length < 10) { errEl.textContent = 'Введи корректный адрес кошелька'; return; }
    if (state.me && amount > state.me.balance_rub) { errEl.textContent = 'Недостаточно монет'; return; }

    btn.disabled = true;
    btn.textContent = 'Отправляем...';
    errEl.textContent = '';

    try {
        const result = await api.withdrawRequest(amount, network, wallet);
        state.me.balance_rub = result.new_balance;
        renderBalance();
        closeWithdrawModal();
        const usdtInfo = result.amount_usdt > 0 ? ` (~${result.amount_usdt.toFixed(2)} USDT)` : '';
        toast(`✅ Заявка #${result.id} создана${usdtInfo}. Ожидай подтверждения.`, 'success');
    } catch (e) {
        errEl.textContent = e.message || 'Ошибка. Попробуй снова.';
    } finally {
        btn.disabled = false;
        btn.textContent = 'Отправить заявку';
    }
};

// ═══════════════════════════════════════
// Страница «Как работает»
// ═══════════════════════════════════════
window.openHowItWorks = function() {
    const feed = document.getElementById('events-feed');
    feed.innerHTML = `
        <button class="back-btn" onclick="loadProfile()">← Назад</button>
        <div class="howto-page">
            <h2 class="howto-title">Как работает PredictBet</h2>

            <div class="howto-block">
                <div class="howto-block-title">🪙 Что такое монеты</div>
                <p>Монеты — игровая валюта PredictBet. <b>1 ₽ = 1 монета.</b></p>
                <p>Пополняй баланс через USDT (TON), Telegram Stars или крипту (ETH/BTC/SOL). Выводи выигрыш в крипту на свой кошелёк.</p>
            </div>

            <div class="howto-block">
                <div class="howto-block-title">📐 Как считается выигрыш (LMSR)</div>
                <p>Система использует <b>LMSR</b> — математическую модель рынка предсказаний, как на Polymarket.</p>
                <ul class="howto-list">
                    <li>Каждая выигравшая акция = <b>1 монета</b></li>
                    <li>Коэффициент зависит от спроса: чем больше ставок «Да» — тем ниже коэф «Да» и выше «Нет»</li>
                    <li>Срок события <b>не влияет</b> на расчёт — ставишь по текущему рынку</li>
                </ul>

                <div class="howto-example">
                    <div class="howto-example-title">Пример</div>
                    <p>Событие: «Победит ли сборная на ЧМ?»</p>
                    <p>Рынок даёт <b>35% вероятности</b> → коэф <b>×2.86</b></p>
                    <p>Ставишь <b>500 монет</b> → при победе получаешь <b>~1 400 монет</b> (×2.86, минус 2% комиссии)</p>
                    <p style="color:var(--no)">При проигрыше теряешь 500 монет</p>
                </div>
            </div>

            <div class="howto-block">
                <div class="howto-block-title">💸 Пополнение и вывод</div>
                <ul class="howto-list">
                    <li><b>USDT (TON)</b> — отправь на наш кошелёк, укажи memo = ID платежа</li>
                    <li><b>Telegram Stars</b> — купи Stars в Telegram, обменяй на монеты</li>
                    <li><b>ETH / BTC / SOL</b> — через платёжный шлюз NOWPayments</li>
                    <li><b>Вывод</b> — создай заявку, укажи кошелёк. Выплата в течение 24 часов после ручного одобрения.</li>
                </ul>
            </div>

            <div class="howto-block">
                <div class="howto-block-title">🔒 Безопасность</div>
                <ul class="howto-list">
                    <li>Все балансы хранятся в БД с полным аудитом каждой транзакции</li>
                    <li>Вывод только на указанный тобой кошелёк после ручной проверки</li>
                    <li>Платформа берёт <b>2% комиссии</b> только с прибыли</li>
                </ul>
            </div>
        </div>
    `;
};

// ═══════════════════════════════════════
// Детальная страница события
// ═══════════════════════════════════════

// Генерируем псевдо-историю коэффициентов для графика
function generateSparklineData(currentPrice, points = 20) {
    const data = [];
    let val = currentPrice + (Math.random() - 0.5) * 0.3;
    for (let i = 0; i < points; i++) {
        val = Math.max(0.05, Math.min(0.95, val + (Math.random() - 0.5) * 0.04));
        data.push(parseFloat(val.toFixed(3)));
    }
    data[data.length - 1] = parseFloat(currentPrice.toFixed(3));
    return data;
}

function drawOddsChart(canvasId, outcomes) {
    const canvas = document.getElementById(canvasId);
    if (!canvas || !window.Chart) return;

    const labels = Array.from({ length: 20 }, (_, i) => '');
    labels[0] = '30 дн';
    labels[labels.length - 1] = 'Сейчас';

    const colors = ['#10d982', '#f43f5e', '#6366f1', '#f59e0b'];

    const datasets = outcomes.map((o, i) => ({
        label: o.title,
        data: generateSparklineData(o.price),
        borderColor: colors[i] || colors[0],
        backgroundColor: (colors[i] || colors[0]).replace(')', ', 0.08)').replace('rgb', 'rgba'),
        borderWidth: 2,
        fill: true,
        tension: 0.4,
        pointRadius: 0,
        pointHoverRadius: 4,
    }));

    new Chart(canvas, {
        type: 'line',
        data: { labels, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: {
                    display: outcomes.length > 2,
                    labels: {
                        color: '#8896b0',
                        font: { size: 11, family: 'Manrope' },
                        boxWidth: 10,
                        padding: 12,
                    },
                },
                tooltip: {
                    backgroundColor: '#111827',
                    borderColor: 'rgba(255,255,255,0.1)',
                    borderWidth: 1,
                    titleColor: '#8896b0',
                    bodyColor: '#f0f4ff',
                    callbacks: {
                        label: ctx => ` ${ctx.dataset.label}: ${Math.round(ctx.raw * 100)}%`,
                    },
                },
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: { color: '#4a5568', font: { size: 10 } },
                },
                y: {
                    min: 0, max: 1,
                    grid: { color: 'rgba(255,255,255,0.04)' },
                    ticks: {
                        color: '#4a5568',
                        font: { size: 10 },
                        callback: v => `${Math.round(v * 100)}%`,
                        stepSize: 0.25,
                    },
                },
            },
        },
    });
}

async function openEventDetail(eventId) {
    const feed = document.getElementById('events-feed');
    feed.innerHTML = '<div class="loading"><div class="spinner"></div><p>Загружаем событие...</p></div>';

    try {
        const event = await api.event(eventId);
        const cat = event.category;
        const isHot = (new Date(event.closes_at) - Date.now()) < 86400000 * 3;

        // Сначала рендерим эмодзи как плейсхолдер; ниже после insertion в DOM
        // пробуем подгрузить фото и заменяем background-image при успехе.
        const detailEmoji = cat ? cat.emoji : '🎯';
        const imageHtml = `<div class="event-detail-image" data-image-url="${event.image_url || ''}">${detailEmoji}</div>`;

        const outcomesHtml = event.outcomes.map((o, i) => {
            const pct = Math.round(o.price * 100);
            let cls = '';
            if (event.outcomes.length === 2) cls = i === 0 ? 'yes' : 'no';
            return `
                <button class="detail-outcome-btn ${cls}" data-outcome-id="${o.id}">
                    <div class="detail-outcome-top">
                        <span class="detail-outcome-name">${o.title}</span>
                        <span class="detail-outcome-percent">${pct}%</span>
                    </div>
                    <div class="detail-outcome-bar">
                        <div class="detail-outcome-fill" style="width:${pct}%"></div>
                    </div>
                    <div class="detail-outcome-bottom">
                        <span>Коэф: <b>×${o.odds.toFixed(2)}</b></span>
                        <span>${Math.round(o.shares_outstanding).toLocaleString('ru-RU')} акций</span>
                    </div>
                </button>
            `;
        }).join('');

        feed.innerHTML = `
            <button class="back-btn" onclick="goBack()">
                ← К событиям
            </button>

            <div class="event-detail">
                <div class="event-detail-header">
                    ${imageHtml}
                    <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;flex-wrap:wrap">
                        ${cat ? `<div class="event-detail-cat">${cat.emoji} ${cat.name}</div>` : ''}
                        ${isHot ? `<div class="badge-live">Скоро закрытие</div>` : ''}
                    </div>
                    <h1 class="event-detail-title">${event.title}</h1>
                    ${event.description ? `<p class="event-detail-desc">${event.description.replace(/\n/g,'<br>')}</p>` : ''}
                </div>

                <div class="event-detail-stats">
                    <div class="stat-pill">
                        <div class="stat-label">Объём</div>
                        <div class="stat-value">${fmtVolume(event.stats.volume_rub)}</div>
                    </div>
                    <div class="stat-pill">
                        <div class="stat-label">Игроков</div>
                        <div class="stat-value">${event.stats.players_count}</div>
                    </div>
                    <div class="stat-pill">
                        <div class="stat-label">До закрытия</div>
                        <div class="stat-value" style="${isHot ? 'color:var(--no)' : ''}">
                            <span data-closes-at="${event.closes_at}">${fmtCountdown(event.closes_at)}</span>
                        </div>
                    </div>
                </div>
                ${event.article_url ? `
                <div class="event-source-block">
                    <a href="${event.article_url}" target="_blank" rel="noopener noreferrer" class="event-source-btn">
                        📰 Читать источник новости
                    </a>
                </div>` : ''}

                <!-- График вероятностей -->
                <div class="chart-container">
                    <div class="chart-title">📈 Динамика вероятностей</div>
                    <div class="chart-canvas-wrap">
                        <canvas id="odds-chart"></canvas>
                    </div>
                </div>

                <!-- Исходы -->
                <div class="event-detail-section" style="margin-bottom:12px">
                    <h3 style="margin-bottom:10px;font-size:13px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.6px">Сделать ставку</h3>
                </div>
                <div class="event-detail-outcomes">${outcomesHtml}</div>

                ${event.similar_events && event.similar_events.length ? `
                <div class="similar-events-section">
                    <h3 class="section-title">🔥 Похожие события</h3>
                    <div class="similar-events-scroll">
                        ${event.similar_events.map(se => `
                            <div class="similar-card" onclick="openEventDetail(${se.id})">
                                <div class="similar-card-img" style="${se.image_url && se.image_url.startsWith('http') ? `background-image:url('${se.image_url}');background-size:cover;` : ''}">
                                    ${se.image_url && se.image_url.startsWith('http') ? '' : '🎯'}
                                </div>
                                <div class="similar-card-title">${se.title}</div>
                                <div class="similar-card-timer" data-closes-at="${se.closes_at}">${fmtCountdown(se.closes_at)}</div>
                            </div>
                        `).join('')}
                    </div>
                </div>` : ''}

                <div class="comments-section">
                    <h3 class="section-title">💬 Комментарии</h3>
                    <div class="comments-list" id="comments-list-${event.id}">
                        <div class="comments-loading">Загружаем...</div>
                    </div>
                    <div class="comment-form">
                        <textarea id="comment-input" class="comment-textarea" placeholder="Ваш комментарий (только для участников ставки)..." maxlength="500" rows="2"></textarea>
                        <button class="comment-submit-btn" onclick="submitComment(${event.id})">Отправить</button>
                    </div>
                </div>
            </div>
        `;

        // Подгрузка детальной картинки с фоллбеком на эмодзи
        const detailImg = feed.querySelector('.event-detail-image');
        if (detailImg && event.image_url) {
            const probe = new Image();
            probe.onload = () => {
                detailImg.style.backgroundImage = `url("${event.image_url}")`;
                detailImg.textContent = '';
            };
            probe.src = event.image_url;
            // на onerror — оставляем эмодзи, ничего не делаем
        }

        // Рисуем график
        setTimeout(() => drawOddsChart('odds-chart', event.outcomes), 50);

        // Клики на исходы
        feed.querySelectorAll('.detail-outcome-btn').forEach((btn) => {
            btn.onclick = () => {
                const outcomeId = parseInt(btn.dataset.outcomeId);
                const outcome = event.outcomes.find(o => o.id === outcomeId);
                openBetModal(event, outcome);
            };
        });

        state.selectedEvent = event;

        // Загружаем комментарии
        loadComments(event.id);

    } catch (e) {
        renderError(e.message);
    }
}

async function loadComments(eventId) {
    const list = document.getElementById(`comments-list-${eventId}`);
    if (!list) return;
    try {
        const comments = await api.comments(eventId);
        if (!comments.length) {
            list.innerHTML = '<div class="comments-empty">Пока нет комментариев. Сделайте ставку и оставьте первый!</div>';
            return;
        }
        list.innerHTML = comments.map(c => `
            <div class="comment-item">
                <div class="comment-avatar">${(c.username || '?')[0].toUpperCase()}</div>
                <div class="comment-body">
                    <div class="comment-header">
                        <span class="comment-username">@${c.username || 'Аноним'}</span>
                        <span class="comment-time">${new Date(c.created_at).toLocaleDateString('ru-RU', {day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'})}</span>
                    </div>
                    <div class="comment-text">${c.text.replace(/</g,'&lt;').replace(/>/g,'&gt;')}</div>
                </div>
            </div>
        `).join('');
    } catch (e) {
        list.innerHTML = '<div class="comments-empty">Не удалось загрузить комментарии</div>';
    }
}

window.submitComment = async function(eventId) {
    const input = document.getElementById('comment-input');
    const text = input?.value?.trim();
    if (!text) return;
    try {
        const comment = await api.postComment(eventId, text);
        input.value = '';
        toast('Комментарий добавлен', 'success');
        loadComments(eventId);
    } catch (e) {
        if (e.status === 403) {
            toast('Только участники могут комментировать', 'error');
        } else {
            toast('Ошибка: ' + (e.message || 'попробуйте снова'), 'error');
        }
    }
};

function goBack() {
    const activeTab = document.querySelector('.nav-btn.active')?.dataset?.tab;
    showScreen(activeTab || 'markets');
    if (activeTab === 'portfolio') loadPortfolio();
    else if (activeTab === 'profile') loadProfile();
    else loadInitial();
}

// ═══════════════════════════════════════
// Инициализация
// ═══════════════════════════════════════
document.addEventListener('DOMContentLoaded', () => {
    // Telegram WebApp
    const tg = window.Telegram?.WebApp;
    if (tg) {
        tg.ready();
        tg.expand();
    }

    // Блокируем открытие вне Telegram — без initData нельзя авторизоваться
    if (!tg?.initData) {
        // Получаем username бота для ссылки
        fetch('/api/config').then(r => r.json()).then(cfg => {
            const botLink = `https://t.me/${cfg.bot_username || 'predictbet_bot'}`;
            document.querySelector('.app').innerHTML = `
                <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;
                            height:100vh;padding:32px;text-align:center;font-family:Inter,sans-serif">
                    <div style="font-size:64px;margin-bottom:16px">🔒</div>
                    <h2 style="margin:0 0 8px;font-size:20px;font-weight:700">Открой через Telegram</h2>
                    <p style="color:#6b7280;font-size:14px;margin:0 0 24px;line-height:1.5">
                        PredictBet работает только как Telegram Mini App.<br>
                        Найди бота и нажми кнопку «Открыть приложение».
                    </p>
                    <a href="${botLink}"
                       style="background:#2563eb;color:#fff;padding:12px 28px;border-radius:12px;
                              text-decoration:none;font-weight:600;font-size:15px">
                        Открыть бота ↗
                    </a>
                </div>`;
        }).catch(() => {
            document.querySelector('.app').innerHTML = `
                <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;
                            height:100vh;padding:32px;text-align:center;font-family:Inter,sans-serif">
                    <div style="font-size:64px;margin-bottom:16px">🔒</div>
                    <h2 style="margin:0 0 8px;font-size:20px;font-weight:700">Открой через Telegram</h2>
                    <p style="color:#6b7280;font-size:14px;margin:0">
                        PredictBet работает только как Telegram Mini App.
                    </p>
                </div>`;
        });
        return;
    }

    // Theme
    loadTheme();
    const themeBtn = document.getElementById('theme-toggle');
    if (themeBtn) themeBtn.onclick = toggleTheme;

    setupBottomNav();

    // Timeframe tabs
    document.querySelectorAll('.timeframe-tab').forEach(btn => {
        btn.onclick = () => switchTimeframe(btn.dataset.timeframe);
    });

    // Leaderboard period tabs
    document.querySelectorAll('.leaderboard-period-tab').forEach(btn => {
        btn.onclick = () => loadLeaderboard(btn.dataset.period);
    });

    // Bet modal
    document.getElementById('bet-modal-close').onclick = closeBetModal;
    document.querySelector('#bet-modal .bet-modal-backdrop').onclick = closeBetModal;

    const amountInput = document.getElementById('bet-amount');
    amountInput.addEventListener('input', () => {
        clearTimeout(state.quoteTimer);
        state.quoteTimer = setTimeout(updateQuote, 350);
    });

    document.querySelectorAll('#bet-modal .amount-quick button').forEach((btn) => {
        btn.onclick = () => {
            if (btn.dataset.amount === 'max') {
                // MAX = текущий баланс
                amountInput.value = state.me ? Math.floor(state.me.balance_rub) : '';
            } else {
                amountInput.value = btn.dataset.amount;
            }
            updateQuote();
        };
    });

    document.getElementById('bet-confirm-btn').onclick = confirmBet;

    // Баланс → депозит
    document.getElementById('balance-pill').onclick = () => openDepositModal();

    // Поиск
    const searchInput = document.getElementById('search-input');
    searchInput.addEventListener('input', () => {
        const q = searchInput.value.toLowerCase().trim();
        if (!q) { renderEvents(); return; }
        const feed = document.getElementById('events-feed');
        feed.innerHTML = '';
        const filtered = state.events.filter(e => e.title.toLowerCase().includes(q));
        if (!filtered.length) {
            feed.innerHTML = `<div class="empty-state"><h3>Не найдено</h3><p>Попробуй другой запрос</p></div>`;
        } else {
            filtered.forEach(e => feed.appendChild(buildEventCard(e)));
        }
    });

    // Живые таймеры — один глобальный интервал на всё приложение
    setInterval(() => {
        document.querySelectorAll('[data-closes-at]').forEach(el => {
            el.textContent = fmtCountdown(el.dataset.closesAt);
        });
    }, 1000);

    loadInitial();
});
