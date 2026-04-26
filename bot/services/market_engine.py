"""
Market Engine — реализация LMSR (Logarithmic Market Scoring Rule).

Используется на Polymarket / Augur / PredictIt.
Подробное описание формулы — см. docs/COEFFICIENTS.md

Ключевые функции:
  - cost_function(q, b)  → текущая суммарная стоимость рынка
  - get_prices(q, b)     → вероятности (= цены акций) каждого исхода
  - get_odds(q, b)       → коэффициенты (1/price)
  - calculate_bet_cost() → сколько стоит купить N акций конкретного исхода
"""
from decimal import Decimal, getcontext
from math import exp, log
from typing import Sequence

# Decimal precision для финансовых расчётов
getcontext().prec = 28


def _log_sum_exp(values: Sequence[float]) -> float:
    """
    Численно стабильное вычисление log(sum(exp(x_i))).
    Без этого трюка большие q вызывают переполнение float.
    """
    max_v = max(values)
    return max_v + log(sum(exp(v - max_v) for v in values))


def cost_function(q: Sequence[Decimal], b: Decimal) -> Decimal:
    """
    LMSR cost function:
        C(q) = b · ln(Σ exp(qᵢ / b))

    Args:
        q: список количества акций каждого исхода
        b: параметр ликвидности рынка

    Returns:
        Текущая стоимость рынка (сколько денег "лежит" в нём).
    """
    b_f = float(b)
    q_over_b = [float(qi) / b_f for qi in q]
    return Decimal(str(b_f * _log_sum_exp(q_over_b)))


def get_prices(q: Sequence[Decimal], b: Decimal) -> list[Decimal]:
    """
    Вероятности (цены акций) каждого исхода.
        pᵢ = exp(qᵢ/b) / Σⱼ exp(qⱼ/b)

    Сумма всегда = 1.

    Returns:
        Список цен. Например, [Decimal("0.45"), Decimal("0.55")]
    """
    b_f = float(b)
    q_over_b = [float(qi) / b_f for qi in q]
    max_v = max(q_over_b)
    exps = [exp(v - max_v) for v in q_over_b]
    total = sum(exps)
    return [Decimal(str(e / total)) for e in exps]


def get_odds(q: Sequence[Decimal], b: Decimal) -> list[Decimal]:
    """
    Коэффициенты для каждого исхода (1 / price).

    Например, при цене 0.4 коэф = 2.5: поставил 100, выиграл 250.

    Returns:
        Список коэффициентов в виде Decimal с 4 знаками.
    """
    prices = get_prices(q, b)
    return [
        (Decimal("1") / p).quantize(Decimal("0.0001"))
        if p > 0 else Decimal("999.9999")
        for p in prices
    ]


def calculate_bet_cost(
    q: Sequence[Decimal],
    b: Decimal,
    outcome_index: int,
    shares_to_buy: Decimal,
) -> Decimal:
    """
    Сколько денег нужно заплатить, чтобы купить shares_to_buy акций
    исхода с индексом outcome_index.

    cost = C(q + Δq) − C(q)

    Returns:
        Стоимость покупки в рублях (Decimal с 2 знаками).
    """
    if shares_to_buy <= 0:
        return Decimal("0.00")

    cost_before = cost_function(q, b)
    new_q = list(q)
    new_q[outcome_index] += shares_to_buy
    cost_after = cost_function(new_q, b)

    return (cost_after - cost_before).quantize(Decimal("0.01"))


def calculate_shares_for_amount(
    q: Sequence[Decimal],
    b: Decimal,
    outcome_index: int,
    amount_rub: Decimal,
    max_iterations: int = 50,
) -> Decimal:
    """
    Обратная задача: пользователь хочет потратить amount_rub —
    сколько акций он получит?

    Решаем бинарным поиском, потому что аналитического обратного
    решения для LMSR нет.

    Returns:
        Количество акций, которое получит пользователь (Decimal с 4 знаками).
    """
    if amount_rub <= 0:
        return Decimal("0.0000")

    # Грубая верхняя граница: при цене 0 максимум можно купить за amount.
    # На практике хватит amount * 5.
    lo, hi = Decimal("0"), amount_rub * Decimal("10")
    target = amount_rub

    for _ in range(max_iterations):
        mid = (lo + hi) / Decimal("2")
        cost = calculate_bet_cost(q, b, outcome_index, mid)

        if abs(cost - target) < Decimal("0.01"):
            return mid.quantize(Decimal("0.0001"))

        if cost < target:
            lo = mid
        else:
            hi = mid

    return ((lo + hi) / Decimal("2")).quantize(Decimal("0.0001"))


def calculate_payout(
    user_shares: Decimal,
    is_winning_outcome: bool,
    fee_percent: Decimal = Decimal("2.0"),
) -> tuple[Decimal, Decimal]:
    """
    Считаем выплату пользователю при разрешении события.

    На LMSR каждая выигрышная акция = 1 рубль.
    Платформа берёт fee% с прибыли (не со ставки).

    Args:
        user_shares: акции пользователя на этот исход
        is_winning_outcome: победил ли этот исход
        fee_percent: комиссия платформы

    Returns:
        (payout, fee) — сколько получит пользователь и сколько платформа.
    """
    if not is_winning_outcome:
        return Decimal("0.00"), Decimal("0.00")

    gross = user_shares  # 1 рубль за акцию
    fee = (gross * fee_percent / Decimal("100")).quantize(Decimal("0.01"))
    net = (gross - fee).quantize(Decimal("0.01"))
    return net, fee


def max_platform_loss(b: Decimal, n_outcomes: int) -> Decimal:
    """
    Максимальный убыток платформы по LMSR ограничен:
        max_loss = b · ln(N)

    Это нужно знать, чтобы не дотировать рынок больше необходимого.
    """
    return (b * Decimal(str(log(n_outcomes)))).quantize(Decimal("0.01"))


# ========== Sanity-check ==========
if __name__ == "__main__":
    # Бинарный рынок, b=1000, обе акции в нуле
    q = [Decimal("0"), Decimal("0")]
    b = Decimal("1000")

    print(f"Стартовые цены: {get_prices(q, b)}")
    print(f"Стартовые коэффициенты: {get_odds(q, b)}")
    print(f"Макс. убыток платформы: {max_platform_loss(b, 2)} ₽")

    # Алиса покупает 100 YES за ~52₽
    cost = calculate_bet_cost(q, b, 0, Decimal("100"))
    print(f"\n100 YES стоят: {cost} ₽")

    # Сколько акций можно купить за 1000₽?
    shares = calculate_shares_for_amount(q, b, 0, Decimal("1000"))
    print(f"За 1000₽ можно купить: {shares} YES акций")

    # После покупки 600 YES — посмотрим коэффициенты
    q_new = [Decimal("600"), Decimal("0")]
    print(f"\nПосле 600 YES: цены {get_prices(q_new, b)}")
    print(f"Коэффициенты: {get_odds(q_new, b)}")
