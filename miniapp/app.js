/**
 * Главная логика Mini App — PredictBet
 */

window.state = {
    me: null,
    categories: [],
    events: [],
    activeCategory: '',
    selectedEvent: null,
    selectedOutcome: null,
    quoteTimer: null,
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
            api.events(),
        ]);
        state.me = me;
        state.categories = categories;
        state.events = events;
        renderBalance();
        renderCategories();
        renderEvents();
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
        state.events = await api.events(slug);
        renderEvents();
    } catch (e) {
        renderError(e.message);
    }
}

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

    // Изображение / эмодзи
    const img = card.querySelector('.event-image');
    if (event.image_url) {
        img.style.backgroundImage = `url(${event.image_url})`;
        img.style.backgroundSize = 'cover';
    } else {
        img.textContent = getCategoryEmoji(event);
    }

    // Заголовок (кликабелен)
    const titleEl = card.querySelector('.event-title');
    titleEl.textContent = event.title;
    titleEl.style.cursor = 'pointer';
    titleEl.onclick = () => openEventDetail(event.id);

    // Мета
    const deadline = fmtDeadline(event.closes_at);
    const isClosingSoon = (new Date(event.closes_at) - Date.now()) < 86400000 * 2;

    card.querySelector('.event-volume').innerHTML = `📊 ${event.outcomes.length} исхода`;
    card.querySelector('.event-deadline').innerHTML =
        isClosingSoon
            ? `<span style="color:var(--no)">⏰ ${deadline}</span>`
            : `⏱ ${deadline}`;

    // Исходы
    const outcomesDiv = card.querySelector('.event-outcomes');
    if (event.outcomes.length === 3) outcomesDiv.classList.add('multi-3');
    else if (event.outcomes.length === 1) outcomesDiv.classList.add('single');

    const totalPrice = event.outcomes.reduce((s, o) => s + o.price, 0) || 1;

    event.outcomes.forEach((outcome, i) => {
        const pct = outcome.price / totalPrice;
        const btn = document.createElement('button');
        btn.className = 'outcome-btn';

        if (event.outcomes.length === 2) {
            const lower = outcome.title.toLowerCase();
            if (lower === 'да' || lower === 'yes' || i === 0) btn.classList.add('yes');
            else btn.classList.add('no');
        }

        // CSS переменная для нижней полоски
        btn.style.setProperty('--fill', `${Math.round(pct * 100)}%`);

        btn.innerHTML = `
            <div class="outcome-title">${outcome.title}</div>
            <div class="outcome-stats">
                <span class="outcome-percent">${Math.round(pct * 100)}%</span>
                <span class="outcome-odds">×${outcome.odds.toFixed(2)}</span>
            </div>
        `;
        btn.onclick = () => openBetModal(event, outcome);
        outcomesDiv.appendChild(btn);
    });

    return card;
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
// Bottom Nav
// ═══════════════════════════════════════
function setupBottomNav() {
    document.querySelectorAll('.nav-btn').forEach((btn) => {
        btn.onclick = () => {
            document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            // Скрываем категории для не-маркетов
            const catEl = document.getElementById('categories');
            const tab = btn.dataset.tab;
            catEl.style.display = tab === 'markets' ? '' : 'none';
            if (tab === 'portfolio') loadPortfolio();
            else if (tab === 'profile') loadProfile();
            else {
                catEl.style.display = '';
                loadInitial();
            }
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
                statusHtml = `<span class="bet-status pending">В игре</span>`;
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
        const me = await api.me();
        state.me = me;
        renderBalance();

        const profitColor = me.stats.profit >= 0 ? 'var(--yes)' : 'var(--no)';
        const profitSign  = me.stats.profit >= 0 ? '+' : '';
        const avatar = (me.first_name || 'P')[0].toUpperCase();

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

            <button class="deposit-btn-large" onclick="openDepositModal()">
                💎 Пополнить через USDT (TON)
            </button>

            <div style="background:var(--bg-card);border:1px solid var(--border);border-radius:14px;padding:14px 16px">
                <div class="section-title" style="margin-bottom:10px">О платформе</div>
                <div style="font-size:13px;color:var(--text-muted);line-height:1.7">
                    PredictBet — рынок прогнозов на базе LMSR.<br>
                    Ставь на реальные события, зарабатывай если прав.<br>
                    <span style="color:var(--text-faint);font-size:12px">Минимальная ставка: 10 ₽</span>
                </div>
            </div>
        `;
    } catch (e) {
        renderError(e.message);
    }
}

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

        let imageHtml = '';
        if (event.image_url) {
            imageHtml = `<div class="event-detail-image" style="background-image:url(${event.image_url})"></div>`;
        } else {
            const emoji = cat ? cat.emoji : '🎯';
            imageHtml = `<div class="event-detail-image">${emoji}</div>`;
        }

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
                        <div class="stat-label">Закрытие</div>
                        <div class="stat-value" style="${isHot ? 'color:var(--no)' : ''}">${fmtDeadline(event.closes_at)}</div>
                    </div>
                </div>

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
            </div>
        `;

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

    } catch (e) {
        renderError(e.message);
    }
}

function goBack() {
    // Возвращаемся к предыдущему табу
    const activeTab = document.querySelector('.nav-btn.active')?.dataset?.tab;
    document.getElementById('categories').style.display = '';
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

    setupBottomNav();

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
            amountInput.value = btn.dataset.amount;
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

    loadInitial();
});
