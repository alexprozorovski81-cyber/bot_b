"""
Сервис автоматического вывода USDT через TON.

Использует tonsdk для подписи транзакций с горячего кошелька платформы.
Требует TON_HOT_WALLET_MNEMONIC в .env.

Архитектура горячего кошелька:
- Горячий кошелёк хранит небольшой запас USDT для автовыводов
- Остальные средства — на холодном кошельке (без доступа отсюда)
- При нехватке баланса — вывод ставится в очередь / переходит на ручной
"""
import logging
from decimal import Decimal

import httpx

from bot.config import settings

logger = logging.getLogger(__name__)

_TONCENTER_BASE = "https://toncenter.com/api/v2"
# USDT Jetton master в TON mainnet
_USDT_MASTER = "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs"


async def _toncenter_headers() -> dict:
    h = {"Content-Type": "application/json"}
    if settings.ton_api_key:
        h["X-API-Key"] = settings.ton_api_key
    return h


async def get_hot_wallet_usdt_balance() -> Decimal:
    """Возвращает баланс USDT на горячем кошельке платформы."""
    if not settings.usdt_wallet_address:
        return Decimal("0")
    try:
        url = "https://toncenter.com/api/v3/jetton/wallets"
        params = {
            "owner_address": settings.usdt_wallet_address,
            "jetton_address": settings.usdt_jetton_master or _USDT_MASTER,
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params, headers=await _toncenter_headers())
            resp.raise_for_status()
            data = resp.json()
            wallets = data.get("jetton_wallets", [])
            if wallets:
                raw = int(wallets[0].get("balance", 0))
                return Decimal(raw) / Decimal("1000000")  # USDT 6 знаков
    except Exception as e:
        logger.warning("get_hot_wallet_usdt_balance error: %s", e)
    return Decimal("0")


async def send_usdt(
    to_address: str,
    amount_usdt: Decimal,
    comment: str = "",
) -> str | None:
    """
    Отправляет USDT с горячего кошелька платформы на указанный адрес.

    Returns:
        TX hash (строка) при успехе, None при ошибке.

    Требует: TON_HOT_WALLET_MNEMONIC, TON_API_KEY (желательно).

    Зависимость: tonsdk (pip install tonsdk).
    При отсутствии tonsdk логирует ошибку и возвращает None.
    """
    if not settings.ton_hot_wallet_mnemonic:
        logger.error("TON_HOT_WALLET_MNEMONIC not set — auto withdrawal disabled")
        return None

    try:
        from tonsdk.contract.wallet import Wallets, WalletVersionEnum
        from tonsdk.utils import to_nano, Address
        from tonsdk.boc import Cell
        import base64
    except ImportError:
        logger.error("tonsdk not installed — run: pip install tonsdk")
        return None

    try:
        mnemonics = settings.ton_hot_wallet_mnemonic.split()
        _, _, _, wallet = Wallets.from_mnemonics(
            mnemonics, WalletVersionEnum.v4r2, workchain=0
        )

        # Получаем seqno горячего кошелька
        seqno = await _get_seqno(wallet.address.to_string(True, True, True))
        if seqno is None:
            return None

        # Адрес Jetton-кошелька горячего кошелька
        jetton_wallet_addr = await _get_jetton_wallet_address(
            wallet.address.to_string(True, True, True),
            settings.usdt_jetton_master or _USDT_MASTER,
        )
        if not jetton_wallet_addr:
            logger.error("Cannot find jetton wallet for hot wallet")
            return None

        # Строим payload Jetton transfer
        # op=0xf8a7ea5 (transfer), query_id=0, amount, destination, response_dest, forward_amount
        amount_nano = int(amount_usdt * Decimal("1000000"))

        forward_payload = Cell()
        if comment:
            forward_payload.bits.write_uint(0, 32)  # op=0 text comment
            forward_payload.bits.write_bytes(comment.encode())

        jetton_transfer = Cell()
        jetton_transfer.bits.write_uint(0xf8a7ea5, 32)  # op
        jetton_transfer.bits.write_uint(0, 64)           # query_id
        jetton_transfer.bits.write_coins(amount_nano)    # amount
        jetton_transfer.bits.write_address(Address(to_address))    # destination
        jetton_transfer.bits.write_address(Address(
            wallet.address.to_string(True, True, True)
        ))  # response_destination
        jetton_transfer.bits.write_bit(0)               # custom_payload = null
        jetton_transfer.bits.write_coins(1)             # forward_ton_amount = 1 nanoTON
        jetton_transfer.bits.write_bit(0)               # forward_payload inline
        jetton_transfer.refs.append(forward_payload)

        # Подписываем и отправляем
        transfer = wallet.create_transfer_message(
            to_addr=jetton_wallet_addr,
            amount=to_nano("0.05", "ton"),  # gas для jetton transfer
            seqno=seqno,
            payload=jetton_transfer,
        )

        boc_b64 = base64.b64encode(transfer["message"].to_boc(False)).decode()
        tx_hash = await _send_boc(boc_b64)
        return tx_hash

    except Exception as e:
        logger.exception("send_usdt error: %s", e)
        return None


async def _get_seqno(address: str) -> int | None:
    """Получает seqno кошелька через Toncenter v2."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{_TONCENTER_BASE}/runGetMethod",
                params={"address": address, "method": "seqno", "stack": "[]"},
                headers=await _toncenter_headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            stack = data.get("stack", [])
            if stack:
                return int(stack[0][1], 16)
    except Exception as e:
        logger.warning("_get_seqno error: %s", e)
    return None


async def _get_jetton_wallet_address(owner: str, jetton_master: str) -> str | None:
    """Возвращает адрес Jetton-кошелька для данного owner."""
    try:
        url = "https://toncenter.com/api/v3/jetton/wallets"
        params = {"owner_address": owner, "jetton_address": jetton_master}
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params, headers=await _toncenter_headers())
            resp.raise_for_status()
            data = resp.json()
            wallets = data.get("jetton_wallets", [])
            if wallets:
                return wallets[0].get("address")
    except Exception as e:
        logger.warning("_get_jetton_wallet_address error: %s", e)
    return None


async def _send_boc(boc_b64: str) -> str | None:
    """Отправляет BOC через Toncenter и возвращает hash транзакции."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{_TONCENTER_BASE}/sendBoc",
                json={"boc": boc_b64},
                headers=await _toncenter_headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("result", {}).get("hash")
    except Exception as e:
        logger.warning("_send_boc error: %s", e)
    return None
