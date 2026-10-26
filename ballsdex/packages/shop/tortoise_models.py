"""
Tortoise ORM models for the shop package.
Add to config.yaml under extra-tortoise-models:
  - ballsdex.packages.shop.tortoise_models
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from tortoise import fields, models
from datetime import datetime, timezone

if TYPE_CHECKING:
    from ballsdex.core.models import Ball, Player, Special

def _now_for_compare(dt):
    """Return a datetime compatible for comparison with dt (handles naive/aware)."""
    now = datetime.now(timezone.utc)
    if dt and dt.tzinfo is None:
        return now.replace(tzinfo=None)
    return now

class Currency(models.Model):
    name = fields.CharField(max_length=64)
    icon = fields.CharField(
        max_length=200, description="Path to uploaded 512x512 PNG (Django admin)"
    )
    emoji = fields.CharField(
        max_length=20, null=True, default=None,
        description="Discord emoji ID or unicode character",
    )

    rarity = fields.FloatField(
        default=280.0,
        description="Drop weight. Higher = more common. Lower = rarer.",
    )
    catch_reward_base = fields.IntField(default=0)
    catch_reward_multiplier = fields.FloatField(default=0)

    start_date = fields.DatetimeField(null=True, default=None)
    end_date = fields.DatetimeField(null=True, default=None)
    enabled = fields.BooleanField(default=True)

    def __str__(self) -> str:
        return self.name

    class Meta:
        table = "shop_currency"


class PlayerBalance(models.Model):
    player: fields.ForeignKeyRelation["Player"] = fields.ForeignKeyField(
        "models.Player", related_name="balances", on_delete=fields.CASCADE
    )
    currency: fields.ForeignKeyRelation[Currency] = fields.ForeignKeyField(
        "models.Currency", on_delete=fields.CASCADE
    )
    amount = fields.BigIntField(default=0)

    class Meta:
        table = "shop_player_balance"
        unique_together = (("player", "currency"),)


class ShopSection(models.Model):
    name = fields.CharField(max_length=64)
    background = fields.CharField(
        max_length=200,
        description="Path to the uploaded background image (managed by Django admin)",
    )
    overlay = fields.CharField(
        max_length=200, null=True, default=None,
        description="Optional top layer image pasted over everything"
    )
    arrow_left = fields.CharField(
        max_length=200, null=True, default=None,
        description="Arrow image shown on the left when there is a previous page"
    )
    arrow_right = fields.CharField(
        max_length=200, null=True, default=None,
        description="Arrow image shown on the right when there is a next page"
    )
    order = fields.IntField(default=0, description="Display order in the section dropdown")
    enabled = fields.BooleanField(default=True)

    # Rotation
    is_rotating = fields.BooleanField(
        default=False, description="If true, items rotate randomly from the pool"
    )
    rotation_size = fields.IntField(
        default=3, description="How many items to feature per rotation"
    )
    rotation_hours = fields.IntField(
        default=24, description="How often the rotation refreshes, in hours"
    )

    discount_percent = fields.IntField(default=0)
    sale_start_date = fields.DatetimeField(null=True, default=None)
    sale_end_date = fields.DatetimeField(null=True, default=None)

    # Optional limited-time window (independent of rotation)
    start_date = fields.DatetimeField(null=True, default=None)
    end_date = fields.DatetimeField(null=True, default=None)

    # ── Custom Fonts ──
    font_name = fields.CharField(max_length=200, null=True, default=None)
    font_name_size = fields.IntField(default=56)
    font_price = fields.CharField(max_length=200, null=True, default=None)
    font_price_size = fields.IntField(default=48)
    font_desc = fields.CharField(max_length=200, null=True, default=None)
    font_desc_size = fields.IntField(default=38)
    font_stats = fields.CharField(max_length=200, null=True, default=None)
    font_stats_size = fields.IntField(default=34)
    font_empty = fields.CharField(max_length=200, null=True, default=None)
    font_empty_size = fields.IntField(default=80)
    font_qty = fields.CharField(max_length=200, null=True, default=None)
    font_qty_size = fields.IntField(default=44)

    items: fields.ReverseRelation["ShopItem"]

    @property
    def is_on_sale(self) -> bool:
        if not self.discount_percent:
            return False
        now = _now_for_compare(self.sale_end_date or self.sale_start_date)
        if self.sale_start_date and self.sale_start_date > now:
            return False
        if self.sale_end_date and self.sale_end_date < now:
            return False
        return True

    def __str__(self) -> str:
        return self.name

    class Meta:
        table = "shop_section"
        ordering = ["order"]


class ShopItem(models.Model):
    section: fields.ForeignKeyRelation[ShopSection] = fields.ForeignKeyField(
        "models.ShopSection", related_name="items", on_delete=fields.CASCADE
    )
    name = fields.CharField(
        max_length=64,
        null=True,
        default=None,
        description="Optional display name override. Defaults to the output ball's name.",
    )
    description = fields.CharField(max_length=200, null=True, default=None)

    currency: fields.ForeignKeyRelation[Currency] = fields.ForeignKeyField(
        "models.Currency", on_delete=fields.CASCADE
    )
    price = fields.BigIntField()

    discount_percent = fields.IntField(default=0)
    sale_start_date = fields.DatetimeField(null=True, default=None)
    sale_end_date = fields.DatetimeField(null=True, default=None)    

    output_ball: fields.ForeignKeyRelation["Ball"] | None = fields.ForeignKeyField(
        "models.Ball", related_name=False, null=True, default=None
    )
    output_special: fields.ForeignKeyRelation["Special"] | None = fields.ForeignKeyField(
        "models.Special", null=True, default=None, on_delete=fields.SET_NULL, related_name=False
    )
    output_attack_bonus = fields.IntField(null=True, default=None)
    output_health_bonus = fields.IntField(null=True, default=None)
    output_quantity = fields.IntField(default=1)

    stock_limit = fields.IntField(
        null=True, default=None, description="Global cap on purchases. Null = unlimited."
    )
    max_per_player = fields.IntField(
        null=True, default=None, description="Per-player cap on purchases. Null = unlimited."
    )

    enabled = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    # ── Bundle ──
    is_bundle = fields.BooleanField(
        default=False, description="If true, this item is a bundle containing multiple balls."
    )

    # ── Custom Grid Image ──
    custom_image = fields.CharField(
        max_length=200, null=True, default=None,
        description="Custom image shown in the shop grid. Overrides the ball's wild card."
    )

    bundle_items: fields.ReverseRelation["ShopBundleItem"]

    @property
    def current_price(self) -> int:
        if not self.discount_percent:
            return self.price
        now = _now_for_compare(self.sale_end_date or self.sale_start_date)
        if self.sale_start_date and self.sale_start_date > now:
            return self.price
        if self.sale_end_date and self.sale_end_date < now:
            return self.price
        return int(self.price * (100 - self.discount_percent) / 100)

    @property
    def is_on_sale(self) -> bool:
        return self.current_price < self.price

    def __str__(self) -> str:
        return self.name or f"Item #{self.pk}"

    class Meta:
        table = "shop_item"


class ShopBundleItem(models.Model):
    """One row per ball inside a bundle."""
    shop_item: fields.ForeignKeyRelation["ShopItem"] = fields.ForeignKeyField(
        "models.ShopItem", related_name="bundle_items", on_delete=fields.CASCADE
    )
    ball: fields.ForeignKeyRelation["Ball"] = fields.ForeignKeyField(
        "models.Ball", related_name=False, on_delete=fields.CASCADE
    )
    special: fields.ForeignKeyRelation["Special"] | None = fields.ForeignKeyField(
        "models.Special", null=True, default=None, on_delete=fields.SET_NULL, related_name=False
    )
    attack_bonus = fields.IntField(null=True, default=None)
    health_bonus = fields.IntField(null=True, default=None)
    quantity = fields.IntField(default=1)

    class Meta:
        table = "shop_bundle_item"


class ShopRotation(models.Model):
    """Tracks the currently featured items for a rotating section."""
    section: fields.OneToOneRelation[ShopSection] = fields.OneToOneField(
        "models.ShopSection", related_name="rotation", on_delete=fields.CASCADE
    )
    started_at = fields.DatetimeField(auto_now=True)
    item_ids = fields.JSONField(default=list)  # list[int] of ShopItem pks, in display order

    class Meta:
        table = "shop_rotation"


class ShopPurchase(models.Model):
    """One row per purchase transaction, used to enforce stock/per-player limits."""
    player: fields.ForeignKeyRelation["Player"] = fields.ForeignKeyField(
        "models.Player", on_delete=fields.CASCADE
    )
    item: fields.ForeignKeyRelation[ShopItem] = fields.ForeignKeyField(
        "models.ShopItem", related_name="purchases", on_delete=fields.CASCADE
    )
    quantity = fields.IntField(default=1)
    purchased_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "shop_purchase"


class PlayerDaily(models.Model):
    """Tracks daily reward streaks and last claim time."""
    player: fields.OneToOneRelation["Player"] = fields.OneToOneField(
        "models.Player", related_name="daily", on_delete=fields.CASCADE
    )
    streak = fields.IntField(default=0)
    last_claimed = fields.DatetimeField(null=True, default=None)
    total_claimed = fields.IntField(default=0)

    def __str__(self) -> str:
        return f"{self.player} — Streak: {self.streak}, Total: {self.total_claimed}"

    class Meta:
        table = "shop_player_daily"