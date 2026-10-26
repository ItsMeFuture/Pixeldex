"""
Helper functions for awarding and managing currency balances.
Used by the countryballs catch hook and the shop cog.
"""
from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING
from datetime import datetime, timezone

from ballsdex.packages.shop.tortoise_models import Currency, PlayerBalance
from ballsdex.settings import settings

if TYPE_CHECKING:
    from ballsdex.core.models import Ball, Player

log = logging.getLogger("ballsdex.packages.shop.currency")


async def get_balance(player: "Player", currency: Currency) -> int:
    balance = await PlayerBalance.get_or_none(player=player, currency=currency)
    return balance.amount if balance else 0


async def add_balance(player: "Player", currency: Currency, amount: int) -> int:
    """Add (or subtract, if negative) amount to a player's balance. Returns new total."""
    balance, _ = await PlayerBalance.get_or_create(
        player=player, currency=currency, defaults={"amount": 0}
    )
    balance.amount += amount
    if balance.amount < 0:
        balance.amount = 0
    await balance.save(update_fields=("amount",))
    return balance.amount


async def award_catch_currency(player: "Player", ball: "Ball") -> list[tuple[Currency, int]]:
    """
    Award multiple currencies per catch. Each enabled currency rolls independently
    based on its rarity vs. the global max rarity.
    """
    now = datetime.now(timezone.utc)

    currencies = await Currency.filter(enabled=True)
    if not currencies:
        return []

    active = []
    for c in currencies:
        if c.start_date and c.start_date > now:
            continue
        if c.end_date and c.end_date < now:
            continue
        active.append(c)

    if not active:
        return []

    max_rarity = settings.catch_reward_max_rarity
    rewards = []

    for currency in active:
        # Drop chance = rarity / max_rarity
        # e.g. rarity 280 -> 100% chance, rarity 0.2 -> ~0.07% chance
        chance = min(1.0, currency.rarity / max_rarity) if max_rarity > 0 else 0.0
        if random.random() < chance:
            amount = int(
                currency.catch_reward_base
                + max(0.0, max_rarity - ball.rarity) * currency.catch_reward_multiplier
            )
            if amount > 0:
                await add_balance(player, currency, amount)
                rewards.append((currency, amount))

    return rewards