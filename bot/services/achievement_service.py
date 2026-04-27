"""Сервис ачивок — проверяет условия и выдаёт значки пользователям."""
import logging
from datetime import datetime, timezone

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    Achievement, AchievementCondition, Bet, Comment,
    Transaction, TransactionType, UserAchievement,
)

logger = logging.getLogger(__name__)


async def check_and_award(user_id: int, session: AsyncSession) -> list[Achievement]:
    """Проверяет все условия ачивок и выдаёт новые. Возвращает список новых ачивок."""
    # Уже полученные
    ua_result = await session.execute(
        select(UserAchievement.achievement_id).where(UserAchievement.user_id == user_id)
    )
    already_earned = set(ua_result.scalars().all())

    all_result = await session.execute(select(Achievement))
    all_achievements = all_result.scalars().all()

    newly_earned: list[Achievement] = []

    for ach in all_achievements:
        if ach.id in already_earned:
            continue

        earned = await _check_condition(user_id, ach, session)
        if not earned:
            continue

        ua = UserAchievement(
            user_id=user_id,
            achievement_id=ach.id,
            unlocked_at=datetime.now(timezone.utc),
        )
        session.add(ua)
        newly_earned.append(ach)
        logger.info("Achievement unlocked: user=%s slug=%s", user_id, ach.slug)

    if newly_earned:
        await session.commit()
        await _notify_user(user_id, newly_earned)

    return newly_earned


async def _check_condition(
    user_id: int,
    ach: Achievement,
    session: AsyncSession,
) -> bool:
    ctype = ach.condition_type
    threshold = ach.condition_value

    if ctype == AchievementCondition.FIRST_BET or ctype == AchievementCondition.BETS_COUNT:
        result = await session.execute(
            select(func.count()).select_from(Bet).where(Bet.user_id == user_id)
        )
        return (result.scalar() or 0) >= threshold

    if ctype == AchievementCondition.WIN_COUNT:
        result = await session.execute(
            select(func.count()).select_from(Bet).where(
                Bet.user_id == user_id,
                Bet.is_settled == True,
                Bet.payout_rub > 0,
            )
        )
        return (result.scalar() or 0) >= threshold

    if ctype == AchievementCondition.VOLUME_TOTAL:
        result = await session.execute(
            select(func.coalesce(func.sum(Bet.amount_rub), 0)).where(Bet.user_id == user_id)
        )
        return float(result.scalar() or 0) >= threshold

    if ctype == AchievementCondition.DEPOSIT_FIRST:
        result = await session.execute(
            select(func.count()).select_from(Transaction).where(
                Transaction.user_id == user_id,
                Transaction.type == TransactionType.DEPOSIT,
            )
        )
        return (result.scalar() or 0) >= threshold

    if ctype == AchievementCondition.COMMENT_FIRST:
        result = await session.execute(
            select(func.count()).select_from(Comment).where(Comment.user_id == user_id)
        )
        return (result.scalar() or 0) >= threshold

    if ctype == AchievementCondition.PERFECT_STREAK:
        # Последовательные выигрыши подряд
        bets_result = await session.execute(
            select(Bet.is_settled, Bet.payout_rub)
            .where(Bet.user_id == user_id, Bet.is_settled == True)
            .order_by(Bet.created_at.desc())
        )
        bets = bets_result.all()
        streak = 0
        for _, payout in bets:
            if payout and payout > 0:
                streak += 1
            else:
                break
        return streak >= threshold

    return False


async def _notify_user(user_id: int, achievements: list[Achievement]) -> None:
    try:
        from db.database import AsyncSessionLocal
        from db.models import User
        from bot.notifier import notify_user

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(User.id == user_id)
            )
            user = result.scalar_one_or_none()
            if not user:
                return

        for ach in achievements:
            rarity_label = {
                "common": "Обычная",
                "rare": "Редкая",
                "epic": "Эпическая",
                "legendary": "Легендарная",
            }.get(ach.rarity, ach.rarity)

            await notify_user(
                user.telegram_id,
                f"🏅 <b>Новая ачивка!</b>\n\n"
                f"{ach.emoji} <b>{ach.name}</b>\n"
                f"<i>{ach.description}</i>\n\n"
                f"Редкость: {rarity_label}",
            )
    except Exception as e:
        logger.warning("Failed to notify user about achievement: %s", e)
