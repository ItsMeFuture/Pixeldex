from __future__ import annotations

import logging
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Button, Select, View
from PIL import Image

from ballsdex.core.models import Ball, BallInstance, Player
from ballsdex.packages.shop import image_gen
from ballsdex.packages.shop.currency import add_balance, get_balance
from ballsdex.packages.shop.tortoise_models import (
    Currency,
    PlayerDaily,
    ShopItem,
    ShopPurchase,
    ShopRotation,
    ShopSection,
    ShopBundleItem,
)
from ballsdex.settings import settings

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger("ballsdex.packages.shop")

GRID_SLOTS = image_gen.GRID_SLOTS

# ── Daily reward config ──────────────────────────────────────────────────────
DAILY_BASE_AMOUNT = 10
DAILY_STREAK_MULTIPLIER = 0.1
DAILY_MAX_STREAK_MULTIPLIER = 5.0
DAILY_BALL_DROP_INTERVAL = 5
DAILY_BALL_DROP_STREAK_MIN = 3
DAILY_RESET_HOURS = 48


def _now():
    return datetime.now(timezone.utc)


def _is_new_day(last_claimed: datetime | None) -> bool:
    if not last_claimed:
        return True
    return last_claimed.date() < _now().date()


def _is_streak_active(last_claimed: datetime | None) -> bool:
    if not last_claimed:
        return False
    return (_now() - last_claimed) < timedelta(hours=DAILY_RESET_HOURS)


async def _award_daily_currencies(player: Player, streak: int):
    rewards = []
    multiplier = 1.0 + (min(streak, 50) * DAILY_STREAK_MULTIPLIER)
    currencies = await Currency.filter(enabled=True)
    for currency in currencies:
        rarity_bonus = max(1.0, currency.rarity / 100.0)
        amount = int(DAILY_BASE_AMOUNT * multiplier * rarity_bonus)
        new_balance = await add_balance(player, currency, amount)
        rewards.append((currency, amount, new_balance))
    return rewards


async def _try_ball_drop(player: Player, streak: int):
    if streak < DAILY_BALL_DROP_STREAK_MIN:
        return None
    days_since = streak - DAILY_BALL_DROP_STREAK_MIN
    if days_since % DAILY_BALL_DROP_INTERVAL != 0:
        return None

    balls = await Ball.filter(enabled=True)
    if not balls:
        return None

    weights = [b.rarity for b in balls]
    total = sum(weights)
    if total <= 0:
        return None

    pick = random.uniform(0, total)
    current = 0
    chosen = balls[0]
    for ball in balls:
        current += ball.rarity
        if current >= pick:
            chosen = ball
            break

    atk = random.randint(-settings.max_attack_bonus, settings.max_attack_bonus)
    hp = random.randint(-settings.max_health_bonus, settings.max_health_bonus)

    return await BallInstance.create(
        ball=chosen, player=player, attack_bonus=atk, health_bonus=hp,
    )


async def _is_section_active(section: ShopSection) -> bool:
    if not section.enabled:
        return False
    now = _now()
    if section.start_date and section.start_date > now:
        return False
    if section.end_date and section.end_date < now:
        return False
    return True


async def _get_pool(section: ShopSection) -> list[ShopItem]:
    return list(
        await ShopItem.filter(section=section, enabled=True)
        .order_by("id")
        .prefetch_related("output_ball", "output_special", "currency", "bundle_items", "bundle_items__ball", "bundle_items__special")
    )


async def _rotation_for(section: ShopSection) -> ShopRotation | None:
    return await ShopRotation.get_or_none(section=section)


async def _ensure_rotation(section: ShopSection) -> ShopRotation:
    pool = await _get_pool(section)
    rotation = await _rotation_for(section)
    needs_reroll = (
        rotation is None
        or _now() - rotation.started_at >= timedelta(hours=section.rotation_hours)
    )
    if needs_reroll:
        size = min(section.rotation_size, GRID_SLOTS, len(pool))
        chosen_ids = [i.pk for i in random.sample(pool, size)] if pool else []
        if rotation:
            rotation.item_ids = chosen_ids
            await rotation.save(update_fields=("item_ids", "started_at"))
        else:
            rotation = await ShopRotation.create(section=section, item_ids=chosen_ids)
        image_gen.invalidate_cache(section.pk)
    return rotation


async def num_pages(section: ShopSection) -> int:
    if section.is_rotating:
        return 1
    pool_count = await ShopItem.filter(section=section, enabled=True).count()
    return max(1, math.ceil(pool_count / GRID_SLOTS))


async def get_page_items(section: ShopSection, page: int) -> list[ShopItem]:
    if section.is_rotating:
        rotation = await _ensure_rotation(section)
        pool = await _get_pool(section)
        by_id = {i.pk: i for i in pool}
        return [by_id[i] for i in rotation.item_ids if i in by_id]

    pool = await _get_pool(section)
    start = page * GRID_SLOTS
    return pool[start:start + GRID_SLOTS]


async def _purchase_scope_start(item: ShopItem) -> datetime | None:
    section = item.section if isinstance(item.section, ShopSection) else await item.section
    if not section.is_rotating:
        return None
    rotation = await _rotation_for(section)
    return rotation.started_at if rotation else None


async def _player_purchase_count(player: Player, item: ShopItem) -> int:
    scope_start = await _purchase_scope_start(item)
    qs = ShopPurchase.filter(player=player, item=item)
    if scope_start:
        qs = qs.filter(purchased_at__gte=scope_start)
    rows = await qs.values_list("quantity", flat=True)
    return sum(rows)


async def _global_purchase_count(item: ShopItem) -> int:
    scope_start = await _purchase_scope_start(item)
    qs = ShopPurchase.filter(item=item)
    if scope_start:
        qs = qs.filter(purchased_at__gte=scope_start)
    rows = await qs.values_list("quantity", flat=True)
    return sum(rows)


async def _item_status_text(item: ShopItem, section: ShopSection | None = None) -> str:
    parts = []
    discount = _effective_discount(item, section)
    if discount > 0:
        parts.append(f"🔥 -{discount}%")
    if item.stock_limit is not None:
        sold = await _global_purchase_count(item)
        remaining = item.stock_limit - sold
        if remaining <= 0:
            return "Sold out"
        parts.append(f"{remaining} left")
    if item.max_per_player is not None:
        parts.append(f"Limit {item.max_per_player}/player")
    return " • ".join(parts)


def _currency_display(bot: "BallsDexBot", currency: Currency) -> str:
    if currency.emoji and currency.emoji.isdigit():
        emoji = bot.get_emoji(int(currency.emoji))
        if emoji:
            return f"{emoji} {currency.name}"
    if currency.emoji:
        return f"{currency.emoji} {currency.name}"
    return currency.name


def _effective_price(item: ShopItem, section: ShopSection | None = None) -> int:
    base = item.current_price
    if section and section.is_on_sale:
        return int(base * (100 - section.discount_percent) / 100)
    return base


def _effective_discount(item: ShopItem, section: ShopSection | None = None) -> int:
    if not section or not section.is_on_sale:
        return item.discount_percent if item.is_on_sale else 0
    item_mult = (100 - item.discount_percent) / 100.0 if item.is_on_sale else 1.0
    section_mult = (100 - section.discount_percent) / 100.0
    return 100 - int(100 * item_mult * section_mult)


def _is_effectively_on_sale(item: ShopItem, section: ShopSection | None = None) -> bool:
    return item.is_on_sale or (section is not None and section.is_on_sale)


async def _max_affordable_quantity(player: Player, item: ShopItem, section: ShopSection | None = None) -> int:
    limits = [99]
    if item.stock_limit is not None:
        sold = await _global_purchase_count(item)
        limits.append(max(0, item.stock_limit - sold))
    if item.max_per_player is not None:
        owned = await _player_purchase_count(player, item)
        limits.append(max(0, item.max_per_player - owned))
    balance = await get_balance(player, item.currency)
    effective = _effective_price(item, section)
    if effective > 0:
        limits.append(balance // effective)
    return max(0, min(limits))


def _item_display_name(item: ShopItem) -> str:
    if item.name:
        return item.name
    if item.output_ball:
        return item.output_ball.country
    return "Unknown Item"


# ── Preview Shop Items ──────────────────────────────────────────────────

class BundleItemSelect(Select):
    def __init__(self, bundle_items: list[ShopBundleItem], output_ball: Ball | None):
        options = []
        if output_ball:
            options.append(discord.SelectOption(
                label=f"Cover: {output_ball.country}",
                value="cover",
                default=True,
            ))
        for idx, bi in enumerate(bundle_items):
            name = f"{bi.quantity}× {bi.ball.country}"
            if bi.special:
                name += f" ({bi.special.name})"
            options.append(discord.SelectOption(
                label=name[:100],
                value=str(idx),
            ))
        super().__init__(placeholder="Preview bundle contents…", options=options, row=2)

    async def callback(self, interaction: discord.Interaction):
        view: PreviewView = self.view  # type: ignore
        if self.values[0] == "cover":
            view.selected_bundle_index = None
        else:
            view.selected_bundle_index = int(self.values[0])
        await view._update_view(interaction)


class PreviewView(View):
    def __init__(self, bot: "BallsDexBot", shop_item: ShopItem, player: Player, section: ShopSection | None = None):
        super().__init__(timeout=120)
        self.bot = bot
        self.shop_item = shop_item
        self.player = player
        self.section = section
        self.current_view = "spawn"
        self.bundle_items = list(shop_item.bundle_items) if shop_item.is_bundle else []
        self.selected_bundle_index: int | None = None
        
        if self.bundle_items:
            self.add_item(BundleItemSelect(self.bundle_items, shop_item.output_ball))

    def _get_current_ball(self) -> Ball | None:
        if self.selected_bundle_index is not None and self.bundle_items:
            return self.bundle_items[self.selected_bundle_index].ball
        return self.shop_item.output_ball

    def _get_current_special(self) -> Special | None:
        if self.selected_bundle_index is not None and self.bundle_items:
            bi = self.bundle_items[self.selected_bundle_index]
            return bi.special if getattr(bi, 'special_id', None) else None
        return self.shop_item.output_special if self.shop_item.output_special_id else None

    def _get_current_attack_bonus(self) -> int | None:
        if self.selected_bundle_index is not None and self.bundle_items:
            return self.bundle_items[self.selected_bundle_index].attack_bonus
        return self.shop_item.output_attack_bonus

    def _get_current_health_bonus(self) -> int | None:
        if self.selected_bundle_index is not None and self.bundle_items:
            return self.bundle_items[self.selected_bundle_index].health_bonus
        return self.shop_item.output_health_bonus

    def _get_display_name(self) -> str:
        if self.shop_item.name:
            return self.shop_item.name
        ball = self._get_current_ball()
        if ball:
            return ball.country
        return "Unknown Item"

    async def _get_spawn_embed(self) -> discord.Embed:
        ball = self._get_current_ball()
        price = _effective_price(self.shop_item, self.section)
        price_str = f"{price:,} {_currency_display(self.bot, self.shop_item.currency)}"
        desc = f"**Price:** {price_str}"
        if _is_effectively_on_sale(self.shop_item, self.section):
            discount = _effective_discount(self.shop_item, self.section)
            desc += f"\n~~Original: {self.shop_item.price:,}~~ (-{discount}%)"
        
        title_name = self._get_display_name()
        if self.selected_bundle_index is not None:
            bi = self.bundle_items[self.selected_bundle_index]
            title_name = f"{bi.quantity}× {ball.country if ball else 'Unknown'}"
            
        embed = discord.Embed(
            title=f"🔍 Spawn Art — {title_name}",
            description=desc,
            colour=discord.Colour.gold(),
        )
        embed.set_image(url="attachment://preview.png")
        return embed

    async def _get_card_embed(self) -> discord.Embed:
        ball = self._get_current_ball()
        price = _effective_price(self.shop_item, self.section)
        price_str = f"{price:,} {_currency_display(self.bot, self.shop_item.currency)}"
        desc = f"**Price:** {price_str}"
        if _is_effectively_on_sale(self.shop_item, self.section):
            discount = _effective_discount(self.shop_item, self.section)
            desc += f"\n~~Original: {self.shop_item.price:,}~~ (-{discount}%)"
        
        title_name = self._get_display_name()
        if self.selected_bundle_index is not None:
            bi = self.bundle_items[self.selected_bundle_index]
            title_name = f"🃏 Card Preview — {bi.quantity}× {ball.country if ball else 'Unknown'}"
        else:
            title_name = f"🃏 Card Preview — {title_name}"
            
        embed = discord.Embed(
            title=title_name,
            description=desc,
            colour=discord.Colour.blue(),
        )
        embed.set_image(url="attachment://card.png")
        return embed

    async def _get_spawn_file(self) -> discord.File | None:
        # If viewing cover and shop_item has custom_image, show that instead
        if self.selected_bundle_index is None and self.shop_item.custom_image:
            try:
                art_path = Path("./admin_panel/media/") / self.shop_item.custom_image.lstrip("/")
                return discord.File(art_path, filename="preview.png")
            except Exception:
                pass
        
        ball = self._get_current_ball()
        if not ball:
            return None
        media_path = "./admin_panel/media/"
        try:
            art_path = Path(media_path) / ball.wild_card.lstrip("/")
            return discord.File(art_path, filename="preview.png")
        except Exception:
            return None

    async def _get_card_file(self) -> discord.File | None:
        ball = self._get_current_ball()
        if not ball:
            return None
        try:
            from ballsdex.core.image_generator.image_gen import draw_card
            from io import BytesIO

            attack = self._get_current_attack_bonus()
            health = self._get_current_health_bonus()
            
            temp_instance = BallInstance(
                ball=ball,
                player=self.player,
                special=self._get_current_special(),
                attack_bonus=attack if attack is not None else 0,
                health_bonus=health if health is not None else 0,
            )

            card_image, meta = draw_card(temp_instance)
            
            buffer = BytesIO()
            if meta.get("format") == "WEBP" and meta.get("frames"):
                card_image.save(buffer, format="WEBP")
            else:
                card_image.save(buffer, format="PNG")
            buffer.seek(0)
            
            filename = "card.webp" if meta.get("format") == "WEBP" else "card.png"
            return discord.File(buffer, filename=filename)
        except Exception as e:
            log.warning("Failed to generate card preview: %s", e)
            return None

    @discord.ui.button(label="🖼️ Spawn Art", style=discord.ButtonStyle.primary, disabled=True)
    async def spawn_button(self, interaction: discord.Interaction, button: Button):
        self.current_view = "spawn"
        await self._update_view(interaction)

    @discord.ui.button(label="🃏 Card", style=discord.ButtonStyle.secondary)
    async def card_button(self, interaction: discord.Interaction, button: Button):
        self.current_view = "card"
        await self._update_view(interaction)

    async def _update_view(self, interaction: discord.Interaction):
        for child in self.children:
            if isinstance(child, Button):
                child.disabled = (child.label.startswith("🖼️") and self.current_view == "spawn") or \
                                (child.label.startswith("🃏") and self.current_view == "card")

        if self.current_view == "spawn":
            embed = await self._get_spawn_embed()
            file = await self._get_spawn_file()
        else:
            embed = await self._get_card_embed()
            file = await self._get_card_file()

        if file:
            await interaction.response.edit_message(embed=embed, attachments=[file], view=self)
        else:
            embed.set_footer(text="Image unavailable")
            await interaction.response.edit_message(embed=embed, view=self)

    async def send_initial(self, interaction: discord.Interaction, ephemeral: bool = True):
        embed = await self._get_spawn_embed()
        file = await self._get_spawn_file()
        
        if file:
            await interaction.followup.send(embed=embed, file=file, view=self, ephemeral=ephemeral)
        else:
            embed.set_footer(text="Image unavailable")
            await interaction.followup.send(embed=embed, view=self, ephemeral=ephemeral)


# ── Purchase confirmation view ──────────────────────────────────────────────────

class QuantityConfirmView(View):
    def __init__(
        self,
        bot: "BallsDexBot",
        interaction: discord.Interaction,
        player: Player,
        item: ShopItem,
        max_quantity: int,
        section: ShopSection | None = None,
    ):
        super().__init__(timeout=60)
        self.bot = bot
        self.interaction = interaction
        self.player = player
        self.item = item
        self.section = section
        self.quantity = 1 if max_quantity > 0 else 0
        self.max_quantity = max_quantity
        self.confirmed = False
        self._refresh_disabled()

    def _refresh_disabled(self):
        self.decrease.disabled = self.quantity <= 1
        self.increase.disabled = self.quantity >= self.max_quantity
        self.confirm.disabled = self.quantity < 1

    def _embed(self) -> discord.Embed:
        ball_name = _item_display_name(self.item)
        currency_str = _currency_display(self.bot, self.item.currency)
        total = _effective_price(self.item, self.section) * self.quantity

        e = discord.Embed(
            title=f"🛍️ Buy {ball_name}",
            colour=discord.Colour.gold() if self.max_quantity > 0 else discord.Colour.red(),
        )
        if self.max_quantity <= 0:
            e.description = "You can\'t afford this or you\'ve hit a purchase limit."
        else:
            e.add_field(name="Quantity", value=str(self.quantity), inline=True)
            e.add_field(name="Total cost", value=f"{total:,} {currency_str}", inline=True)
            discount = _effective_discount(self.item, self.section)
            if discount > 0:
                e.add_field(name="Discount", value=f"-{discount}%", inline=True)
                e.add_field(name="Original", value=f"~~{self.item.price:,}~~", inline=True)
        return e

    @discord.ui.button(label="➖", style=discord.ButtonStyle.secondary)
    async def decrease(self, interaction: discord.Interaction, button: Button):
        self.quantity = max(1, self.quantity - 1)
        self._refresh_disabled()
        await interaction.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.button(label="➕", style=discord.ButtonStyle.secondary)
    async def increase(self, interaction: discord.Interaction, button: Button):
        self.quantity = min(self.max_quantity, self.quantity + 1)
        self._refresh_disabled()
        await interaction.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: Button):
        self.confirmed = True
        self.stop()
        for c in self.children:
            c.disabled = True  # type: ignore
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="✖️ Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: Button):
        self.stop()
        for c in self.children:
            c.disabled = True  # type: ignore
        cancel_embed = discord.Embed(title="✖️ Purchase cancelled", colour=discord.Colour.red())
        await interaction.response.edit_message(embed=cancel_embed, view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.interaction.user.id:
            await interaction.response.send_message(
                "This is not your purchase menu.", ephemeral=True
            )
            return False
        return True


async def _execute_purchase(
    interaction: discord.Interaction,
    player: Player,
    item: ShopItem,
    quantity: int,
    section: ShopSection | None = None,
) -> None:
    if not item.is_bundle and not item.output_ball:
        await interaction.followup.send(
            "This item is misconfigured (missing output ball).", ephemeral=True
        )
        return

    currency = item.currency
    total_cost = _effective_price(item, section) * quantity
    await add_balance(player, currency, -total_cost)

    ball_name = _item_display_name(item)
    created = []
    
    if item.is_bundle:
        bundle_items = list(item.bundle_items)
        for _ in range(quantity):
            for bi in bundle_items:
                for _ in range(bi.quantity):
                    atk = (
                        bi.attack_bonus
                        if bi.attack_bonus is not None
                        else random.randint(-settings.max_attack_bonus, settings.max_attack_bonus)
                    )
                    hp = (
                        bi.health_bonus
                        if bi.health_bonus is not None
                        else random.randint(-settings.max_health_bonus, settings.max_health_bonus)
                    )
                    instance = await BallInstance.create(
                        ball=bi.ball,
                        player=player,
                        special=bi.special if getattr(bi, 'special_id', None) else None,
                        attack_bonus=atk,
                        health_bonus=hp,
                    )
                    created.append(instance)
    else:
        for _ in range(quantity):
            for _ in range(item.output_quantity):
                atk = (
                    item.output_attack_bonus
                    if item.output_attack_bonus is not None
                    else random.randint(-settings.max_attack_bonus, settings.max_attack_bonus)
                )
                hp = (
                    item.output_health_bonus
                    if item.output_health_bonus is not None
                    else random.randint(-settings.max_health_bonus, settings.max_health_bonus)
                )
                instance = await BallInstance.create(
                    ball=item.output_ball,
                    player=player,
                    special=item.output_special if item.output_special_id else None,
                    attack_bonus=atk,
                    health_bonus=hp,
                )
                created.append(instance)

    await ShopPurchase.create(player=player, item=item, quantity=quantity)
    image_gen.invalidate_cache(item.section_id)

    ids = ", ".join(f"`#{c.pk:0X}`" for c in created)
    currency_str = currency.name
    success_embed = discord.Embed(
        title="✅ Purchase complete",
        description=f"Bought **{quantity}× {ball_name}** for **{total_cost:,}** {currency_str}!",
        colour=discord.Colour.green(),
    )
    if _is_effectively_on_sale(item, section):
        saved = (item.price - _effective_price(item, section)) * quantity
        success_embed.add_field(
            name="You saved",
            value=f"{saved:,} {currency_str}",
            inline=False,
        )
    success_embed.add_field(name="New balls", value=ids or "—", inline=False)
    await interaction.followup.send(embed=success_embed, ephemeral=True)

    log.info(
        "%s bought %dx shop item %s for %d %s",
        interaction.user, quantity, item.pk, total_cost, currency.name,
    )


# ── Shop view ────────────────────────────────────────────────────────────────────

class ItemBuySelect(Select):
    def __init__(self, items: list[ShopItem], statuses: dict[int, str], section: ShopSection | None = None):
        options = []
        for it in items:
            name = _item_display_name(it)
            status = statuses.get(it.pk, "")
            sold_out = status == "Sold out"
            
            if it.is_bundle:
                name = f"📦 {name}"
            
            price = _effective_price(it, section)
            desc = f"{price:,} {it.currency.name}"
            if _is_effectively_on_sale(it, section):
                discount = _effective_discount(it, section)
                desc = f"~~{it.price:,}~~ → {price:,} {it.currency.name} (-{discount}%)"
            if status and not sold_out:
                desc += f" • {status}"
            if it.is_bundle:
                bundle_count = len(list(it.bundle_items)) if hasattr(it, 'bundle_items') else 0
                desc += f" • Bundle ({bundle_count} types)"
            elif it.output_quantity > 1:
                desc += f" • x{it.output_quantity}"
            if it.output_special:
                desc += f" • {it.output_special.name}"
            
            options.append(
                discord.SelectOption(
                    label=name[:100],
                    description=desc[:100],
                    value=str(it.pk),
                )
            )
        if not options:
            options = [discord.SelectOption(label="No items available", value="none")]
        super().__init__(placeholder="Select an item to buy…", options=options, row=1, disabled=not items)

    async def callback(self, interaction: discord.Interaction):
        view: ShopView = self.view  # type: ignore
        if not self.values or self.values[0] == "none":
            return
        await view.open_purchase(interaction, int(self.values[0]))

class SectionSelect(Select):
    def __init__(self, sections: list[ShopSection], current_index: int):
        options = [
            discord.SelectOption(label=s.name, value=str(i), default=(i == current_index))
            for i, s in enumerate(sections)
        ]
        super().__init__(placeholder="Choose a section…", options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        view: ShopView = self.view  # type: ignore
        index = int(self.values[0])
        view.current_index = index
        view.current_page = 0
        await view.refresh(interaction)


class PrevPageButton(Button):
    def __init__(self):
        super().__init__(label="◀ Prev", style=discord.ButtonStyle.secondary, row=2)

    async def callback(self, interaction: discord.Interaction):
        view: ShopView = self.view  # type: ignore
        view.current_page = max(0, view.current_page - 1)
        await view.refresh(interaction)


class NextPageButton(Button):
    def __init__(self):
        super().__init__(label="Next ▶", style=discord.ButtonStyle.secondary, row=2)

    async def callback(self, interaction: discord.Interaction):
        view: ShopView = self.view  # type: ignore
        view.current_page = min(view.total_pages - 1, view.current_page + 1)
        await view.refresh(interaction)


class ShopView(View):
    def __init__(self, bot: "BallsDexBot", sections: list[ShopSection], user_id: int):
        super().__init__(timeout=180)
        self.bot = bot
        self.sections = sections
        self.user_id = user_id
        self.current_index = 0
        self.current_page = 0
        self.total_pages = 1
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This is not your shop menu.", ephemeral=True
            )
            return False
        return True

    @property
    def section(self) -> ShopSection:
        return self.sections[self.current_index]

    async def build(self) -> tuple[discord.Embed, discord.File]:
        section = self.section
        self.total_pages = await num_pages(section)
        self.current_page = min(self.current_page, self.total_pages - 1)

        items = await get_page_items(section, self.current_page)
        status_map = {item.pk: await _item_status_text(item, self.section) for item in items}

        is_animated = False
        if section.background:
            try:
                bg_path = Path("./admin_panel/media/") / section.background.lstrip("/")
                with Image.open(bg_path) as img:
                    is_animated = getattr(img, "is_animated", False)
            except Exception:
                pass

        if is_animated:
            buffer = await image_gen.render_animated_grid(
                section, items, status_map, page=self.current_page, total_pages=self.total_pages,
            )
            filename = "shop.gif"
        else:
            buffer = await image_gen.render_section_grid(
                section, items, status_map, page=self.current_page, total_pages=self.total_pages,
            )
            filename = "shop.png"

        embed = discord.Embed(title=f"🛒 {section.name}", colour=discord.Colour.gold())
        if section.is_rotating:
            rotation = await _rotation_for(section)
            if rotation:
                next_refresh = rotation.started_at + timedelta(hours=section.rotation_hours)
                embed.description = f"Refreshes {discord.utils.format_dt(next_refresh, style='R')}"
        embed.set_image(url=f"attachment://{filename}")
        embed.set_footer(text="Pick an item from the dropdown below to buy it.")

        self._rebuild_items(items, status_map)
        file = discord.File(buffer, filename=filename)
        return embed, file

    def _rebuild_items(self, items: list[ShopItem], statuses: dict[int, str]):
        self.clear_items()
        if len(self.sections) > 1:
            self.add_item(SectionSelect(self.sections, self.current_index))
        self.add_item(ItemBuySelect(items, statuses, self.section))
        if self.total_pages > 1:
            prev = PrevPageButton()
            nxt = NextPageButton()
            prev.disabled = self.current_page == 0
            nxt.disabled = self.current_page >= self.total_pages - 1
            self.add_item(prev)
            self.add_item(nxt)

    async def refresh(self, interaction: discord.Interaction):
        embed, file = await self.build()
        await interaction.response.edit_message(embed=embed, attachments=[file], view=self)

    async def open_purchase(self, interaction: discord.Interaction, item_id: int):
        item = await ShopItem.get_or_none(pk=item_id).prefetch_related(
            "output_ball", "output_special", "currency", "section", "bundle_items", "bundle_items__ball", "bundle_items__special"
        )
        if not item or not item.enabled:
            await interaction.response.send_message("That item is no longer available.", ephemeral=True)
            return

        player, _ = await Player.get_or_create(discord_id=interaction.user.id)
        max_qty = await _max_affordable_quantity(player, item, self.section)

        confirm_view = QuantityConfirmView(self.bot, interaction, player, item, max_qty, self.section)
        await interaction.response.send_message(
            embed=confirm_view._embed(), view=confirm_view, ephemeral=True
        )
        await confirm_view.wait()

        if not confirm_view.confirmed or confirm_view.quantity < 1:
            return

        max_qty_recheck = await _max_affordable_quantity(player, item, self.section)
        quantity = min(confirm_view.quantity, max_qty_recheck)
        if quantity < 1:
            await interaction.followup.send(
                "Sorry, this item is no longer available in that quantity.", ephemeral=True
            )
            return

        await _execute_purchase(interaction, player, item, quantity, self.section)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(view=self)
        except (discord.HTTPException, AttributeError):
            pass


# ── Cog ────────────────────────────────────────────────────────────────────────

class Shop(commands.Cog):
    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot

    async def item_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        items = await ShopItem.filter(enabled=True).limit(25).prefetch_related(
            "output_ball", "currency", "section", "bundle_items"
        )
        return [
            app_commands.Choice(
                name=f"{'[Bundle] ' if i.is_bundle else ''}{_item_display_name(i)} ({_effective_price(i, i.section):,} {i.currency.name})",
                value=str(i.pk),
            )
            for i in items
            if not current or current.lower() in _item_display_name(i).lower()
        ]

    async def cog_load(self):
        # Hook into the core reloadcache command by wrapping bot.load_cache
        original_load_cache = self.bot.load_cache

        async def _patched_load_cache():
            from ballsdex.packages.shop import image_gen
            image_gen.invalidate_cache()
            return await original_load_cache()

        self.bot.load_cache = _patched_load_cache

    @app_commands.command(name="shop", description="Browse and buy items from the shop.")
    @app_commands.describe(ephemeral="Whether to hide the shop from others")
    async def shop(self, interaction: discord.Interaction["BallsDexBot"], ephemeral: bool = False):
        await interaction.response.defer(thinking=True)

        sections = await ShopSection.all().order_by("order")
        active_sections = [s for s in sections if await _is_section_active(s)]

        if not active_sections:
            await interaction.followup.send("The shop is currently empty.", ephemeral=ephemeral)
            return

        view = ShopView(self.bot, active_sections, interaction.user.id)
        embed, file = await view.build()
        message = await interaction.followup.send(embed=embed, file=file, view=view, ephemeral=ephemeral)
        view.message = message

    @app_commands.command(name="daily", description="Claim your daily reward and build your streak.")
    @app_commands.describe(ephemeral="Whether to hide the result from others")
    async def daily(self, interaction: discord.Interaction["BallsDexBot"], ephemeral: bool = False):
        await interaction.response.defer(thinking=True)

        player, _ = await Player.get_or_create(discord_id=interaction.user.id)
        daily, _ = await PlayerDaily.get_or_create(
            player=player,
            defaults={"streak": 0, "total_claimed": 0, "last_claimed": None}
        )

        if not _is_new_day(daily.last_claimed):
            now = _now()
            tomorrow = datetime(now.year, now.month, now.day, tzinfo=timezone.utc) + timedelta(days=1)
            time_until = discord.utils.format_dt(tomorrow, style="R")
            
            embed = discord.Embed(
                title="⏰ Already Claimed!",
                description=f"You've already claimed your daily reward today.\nCome back {time_until}!",
                colour=discord.Colour.orange(),
            )
            embed.add_field(name="Current Streak", value=f"🔥 {daily.streak} days", inline=True)
            embed.add_field(name="Total Claimed", value=f"📅 {daily.total_claimed}", inline=True)
            embed.set_thumbnail(url=interaction.user.display_avatar.url)
            await interaction.followup.send(embed=embed, ephemeral=ephemeral)
            return

        streak_broken = daily.last_claimed and not _is_streak_active(daily.last_claimed)
        if streak_broken:
            old_streak = daily.streak
            daily.streak = 1
            streak_message = f"🔥 Streak reset! Your {old_streak}-day streak has ended."
        else:
            daily.streak += 1
            if daily.streak == 1:
                streak_message = "🌟 Starting a new streak!"
            else:
                streak_message = f"🔥 Streak continued! Day {daily.streak}!"

        daily.last_claimed = _now()
        daily.total_claimed += 1
        await daily.save()

        currency_rewards = await _award_daily_currencies(player, daily.streak)
        
        reward_lines = []
        for currency, amount, new_balance in currency_rewards:
            currency_str = _currency_display(self.bot, currency)
            reward_lines.append(f"{currency_str}: **+{amount:,}** (Total: {new_balance:,})")

        ball_drop = await _try_ball_drop(player, daily.streak)
        
        embed = discord.Embed(
            title="🎁 Daily Reward Claimed!",
            description=f"{streak_message}\n\n**Rewards:**\n" + "\n".join(reward_lines),
            colour=discord.Colour.green(),
        )
        
        multiplier = 1.0 + (min(daily.streak, 50) * DAILY_STREAK_MULTIPLIER)
        embed.add_field(
            name="Streak Multiplier",
            value=f"**{multiplier:.1f}x** (+{int((multiplier - 1.0) * 100)}%)",
            inline=True,
        )
        
        if daily.streak >= DAILY_BALL_DROP_STREAK_MIN:
            days_until_next = DAILY_BALL_DROP_INTERVAL - ((daily.streak - DAILY_BALL_DROP_STREAK_MIN) % DAILY_BALL_DROP_INTERVAL)
            if days_until_next == 0:
                days_until_next = DAILY_BALL_DROP_INTERVAL
            embed.add_field(
                name="Next Ball Drop",
                value=f"🎱 In **{days_until_next}** day(s)",
                inline=True,
            )
        else:
            days_needed = DAILY_BALL_DROP_STREAK_MIN - daily.streak
            embed.add_field(
                name="Ball Drops Unlock",
                value=f"🔒 In **{days_needed}** more day(s)",
                inline=True,
            )

        embed.add_field(name="Total Dailies", value=f"📅 {daily.total_claimed}", inline=True)

        if ball_drop:
            ball = ball_drop.ball
            embed.add_field(
                name="🎱 BONUS BALL DROP!",
                value=f"You caught **{ball.country}**! `#{ball_drop.pk:0X}`",
                inline=False,
            )
            try:
                media_path = "./admin_panel/media/"
                art_path = Path(media_path) / ball.wild_card.lstrip("/")
                file = discord.File(art_path, filename="daily_ball.png")
                embed.set_image(url="attachment://daily_ball.png")
                await interaction.followup.send(embed=embed, file=file, ephemeral=ephemeral)
                return
            except Exception:
                pass

        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.set_footer(text=f"Streak breaks after {DAILY_RESET_HOURS} hours of inactivity.")
        await interaction.followup.send(embed=embed, ephemeral=ephemeral)

        log.info(
            "%s claimed daily reward (streak: %d, total: %d)",
            interaction.user, daily.streak, daily.total_claimed,
        )

    @app_commands.command(name="balance", description="Check how much currency you have.")
    @app_commands.describe(ephemeral="Whether to hide your balance from others")
    async def balance(self, interaction: discord.Interaction["BallsDexBot"], ephemeral: bool = True):
        target = interaction.user
        player = await Player.get_or_none(discord_id=target.id)

        currencies = await Currency.filter(enabled=True).order_by("name")
        if not currencies:
            await interaction.response.send_message(
                "No currencies have been configured yet.", ephemeral=ephemeral
            )
            return

        embed = discord.Embed(
            title=f"💰 Balance — {target.display_name}",
            colour=discord.Colour.gold(),
        )
        embed.set_thumbnail(url=target.display_avatar.url)

        max_rarity = settings.catch_reward_max_rarity
        for currency in currencies:
            amount = await get_balance(player, currency) if player else 0
            chance = (currency.rarity / max_rarity) * 100 if max_rarity > 0 else 0
            value = f"{amount:,}\n`Drop: {chance:.2f}%`"
            embed.add_field(
                name=_currency_display(self.bot, currency),
                value=value,
                inline=True,
            )

        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    @app_commands.command(name="preview", description="Preview a shop item's spawn art and card.")
    @app_commands.autocomplete(item=item_autocomplete)
    @app_commands.describe(item="The shop item to preview", ephemeral="Whether to hide the preview from others")
    async def preview(self, interaction: discord.Interaction["BallsDexBot"], item: str, ephemeral: bool = True):
        await interaction.response.defer(thinking=True, ephemeral=ephemeral)

        shop_item = await ShopItem.get_or_none(pk=int(item)).prefetch_related(
            "output_ball", "output_special", "currency", "section", "bundle_items", "bundle_items__ball", "bundle_items__special"
        )
        if not shop_item or not shop_item.enabled:
            await interaction.followup.send("That item is not available.", ephemeral=ephemeral)
            return

        player, _ = await Player.get_or_create(discord_id=interaction.user.id)

        view = PreviewView(self.bot, shop_item, player, shop_item.section)
        await view.send_initial(interaction, ephemeral=ephemeral)