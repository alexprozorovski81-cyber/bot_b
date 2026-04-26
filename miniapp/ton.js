/**
 * TON Connect + USDT deposit logic для PredictBet Mini App.
 *
 * Флоу:
 * 1. Пользователь нажимает "Пополнить USDT" → открывается депозит-модалка
 * 2. Нажимает "Подключить кошелёк" → TON Connect открывает Tonkeeper
 * 3. Вводит сумму и нажимает "Отправить USDT"
 * 4. TON Connect создаёт Jetton Transfer → Tonkeeper просит подтверждение
 * 5. Бэкенд находит транзакцию → зачисляет баланс → уведомление
 */

const USDT_MASTER  = 'EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs';
const PLATFORM_WALLET = 'UQCtLXIpcqxeUMBKvmet1mc1c2BIeWqEOzF6PTMPa_M2vFjS';
const MANIFEST_URL = window.location.origin + '/miniapp/tonconnect-manifest.json';

const USDT_DECIMALS = 6;          // USDT на TON — 6 знаков
const GAS_AMOUNT   = '100000000'; // 0.1 TON на газ для Jetton transfer

let tonConnectUI  = null;
let currentDepositId = null;
let depositPollTimer  = null;

// ── Инициализация ────────────────────────────────────────────────────────────

function initTonConnect() {
    if (tonConnectUI) return;
    try {
        tonConnectUI = new TON_CONNECT_UI.TonConnectUI({
            manifestUrl: MANIFEST_URL,
            buttonRootId: 'ton-connect-button',
        });
        tonConnectUI.onStatusChange(handleWalletStatusChange);
    } catch (e) {
        console.error('TON Connect init error:', e);
    }
}

function handleWalletStatusChange(wallet) {
    const connectedEl  = document.getElementById('deposit-connected');
    const disconnectedEl = document.getElementById('deposit-disconnected');
    const amountSection  = document.getElementById('deposit-amount-section');

    if (!connectedEl) return;

    if (wallet) {
        const addr = wallet.account.address;
        const short = addr.slice(0, 6) + '...' + addr.slice(-4);
        document.getElementById('deposit-wallet-addr').textContent = short;
        connectedEl.hidden  = false;
        disconnectedEl.hidden = true;
        amountSection.hidden  = false;
    } else {
        connectedEl.hidden  = true;
        disconnectedEl.hidden = false;
        amountSection.hidden  = true;
    }
}

// ── Открыть / закрыть депозит-модалку ────────────────────────────────────────

async function openDepositModal() {
    initTonConnect();
    document.getElementById('deposit-modal').hidden = false;

    // Узнаём курс USDT/RUB с бэкенда
    try {
        const info = await apiRequest('/api/deposit/ton/rate');
        document.getElementById('deposit-rate').textContent =
            `1 USDT ≈ ${info.rate_rub} ₽`;
    } catch (_) {}

    // Если кошелёк уже подключён — сразу показываем форму
    const wallet = tonConnectUI?.wallet;
    handleWalletStatusChange(wallet || null);
}

function closeDepositModal() {
    document.getElementById('deposit-modal').hidden = true;
    clearTimeout(depositPollTimer);
    currentDepositId = null;
}

// ── Отправка USDT ─────────────────────────────────────────────────────────────

async function sendUSDT() {
    const amountInput = document.getElementById('deposit-usdt-amount');
    const amount = parseFloat(amountInput.value);
    if (!amount || amount < 1) {
        toast('Минимум 1 USDT', 'error');
        return;
    }

    const btn = document.getElementById('deposit-send-btn');
    btn.disabled = true;
    btn.textContent = 'Подготовка...';

    try {
        // 1. Создаём запись о депозите на бэкенде
        const deposit = await apiRequest('/api/deposit/ton/init', {
            method: 'POST',
            body: JSON.stringify({ amount_usdt: amount }),
        });
        currentDepositId = deposit.deposit_id;

        // 2. Получаем адрес Jetton-кошелька пользователя
        const wallet = tonConnectUI.wallet;
        const userTonAddr = wallet.account.address;
        const jettonWalletAddr = await getUserJettonWallet(userTonAddr);

        if (!jettonWalletAddr) {
            throw new Error('Не удалось найти USDT-кошелёк. Убедись что у тебя есть USDT на TON.');
        }

        // 3. Строим payload для Jetton Transfer
        btn.textContent = 'Открываем Tonkeeper...';
        const payload = buildJettonTransferPayload(
            amount,
            PLATFORM_WALLET,
            userTonAddr,
            deposit.deposit_id.toString(),
        );

        // 4. Отправляем транзакцию через TON Connect
        await tonConnectUI.sendTransaction({
            validUntil: Math.floor(Date.now() / 1000) + 600,
            messages: [{
                address: jettonWalletAddr,
                amount: GAS_AMOUNT,
                payload: payload,
            }],
        });

        // 5. Показываем статус и начинаем поллинг
        btn.textContent = 'Ожидаем подтверждения...';
        document.getElementById('deposit-status').hidden = false;
        startDepositPolling(deposit.deposit_id);

    } catch (e) {
        if (e.message?.includes('User rejects')) {
            toast('Отменено', 'info');
        } else {
            toast('Ошибка: ' + (e.message || 'попробуй снова'), 'error');
        }
        btn.disabled = false;
        btn.textContent = 'Отправить USDT';
    }
}

// ── Получить адрес Jetton-кошелька пользователя ──────────────────────────────

async function getUserJettonWallet(userTonAddress) {
    try {
        const resp = await fetch(
            `https://toncenter.com/api/v3/jetton/wallets?owner_address=${encodeURIComponent(userTonAddress)}&jetton_address=${USDT_MASTER}&limit=1`,
            { headers: { 'X-API-Key': '' } }
        );
        const data = await resp.json();
        if (data.jetton_wallets && data.jetton_wallets.length > 0) {
            return data.jetton_wallets[0].address;
        }
    } catch (e) {
        console.error('Jetton wallet fetch error:', e);
    }
    return null;
}

// ── Построить payload Jetton Transfer (TL-B) ─────────────────────────────────

function buildJettonTransferPayload(amountUsdt, toAddress, responseAddress, memo) {
    // Используем TonWeb для сериализации
    const tw = window.TonWeb;
    if (!tw) {
        console.error('TonWeb not loaded');
        return '';
    }

    const amount = Math.floor(amountUsdt * Math.pow(10, USDT_DECIMALS));

    // Строим ячейку вручную через TonWeb.boc.Cell
    const cell = new tw.boc.Cell();
    cell.bits.writeUint(0xf8a7ea5, 32);           // op: transfer
    cell.bits.writeUint(Math.floor(Math.random() * 2**31), 64); // query_id
    cell.bits.writeCoins(amount);                  // jetton amount
    cell.bits.writeAddress(new tw.utils.Address(toAddress));        // destination
    cell.bits.writeAddress(new tw.utils.Address(responseAddress));  // response_destination
    cell.bits.writeBit(0);                         // no custom_payload
    cell.bits.writeCoins(1);                       // forward_ton_amount (1 nanoton)
    cell.bits.writeBit(0);                         // forward_payload inline

    // Memo как текст
    if (memo) {
        const textCell = new tw.boc.Cell();
        textCell.bits.writeUint(0, 32);            // text comment op
        for (const ch of memo) {
            textCell.bits.writeUint(ch.charCodeAt(0), 8);
        }
        cell.refs.push(textCell);
        // Перезаписываем last bit как ref
    }

    try {
        const bytes = cell.toBoc(false);
        return tw.utils.bytesToBase64(bytes);
    } catch (e) {
        console.error('Cell serialization error:', e);
        return '';
    }
}

// ── Поллинг статуса депозита ──────────────────────────────────────────────────

function startDepositPolling(depositId) {
    let attempts = 0;
    const MAX_ATTEMPTS = 20; // 10 минут (20 × 30 сек)

    depositPollTimer = setInterval(async () => {
        attempts++;
        if (attempts > MAX_ATTEMPTS) {
            clearInterval(depositPollTimer);
            toast('Транзакция не найдена за 10 мин. Проверь вручную.', 'error');
            return;
        }

        try {
            const status = await apiRequest(`/api/deposit/ton/status/${depositId}`);
            if (status.status === 'confirmed') {
                clearInterval(depositPollTimer);
                // Обновляем баланс в UI
                if (window.state && window.state.me) {
                    window.state.me.balance_rub = status.new_balance_rub;
                    if (typeof renderBalance === 'function') renderBalance();
                }
                document.getElementById('deposit-status').innerHTML =
                    `<div style="color:var(--yes);font-weight:600">✅ Зачислено ${status.credited_rub} ₽!</div>`;
                toast(`Зачислено ${status.credited_rub} ₽`, 'success');
                setTimeout(closeDepositModal, 3000);
            }
        } catch (_) {}
    }, 30000);
}

// ── Обновление суммы в ₽ при вводе ──────────────────────────────────────────

async function updateDepositRubPreview() {
    const amount = parseFloat(document.getElementById('deposit-usdt-amount').value) || 0;
    try {
        const info = await apiRequest('/api/deposit/ton/rate');
        const rub = Math.floor(amount * info.rate_rub);
        document.getElementById('deposit-rub-preview').textContent =
            rub > 0 ? `≈ ${rub.toLocaleString('ru-RU')} ₽` : '';
    } catch (_) {}
}
