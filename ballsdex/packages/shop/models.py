"""Django ORM models for the shop package admin panel."""
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone

def _now_for_compare(dt):
    from django.utils import timezone
    now = timezone.now()
    if dt and dt.tzinfo is None:
        return now.replace(tzinfo=None)
    return now

class Currency(models.Model):
    name = models.CharField(max_length=64)
    icon = models.ImageField(
        upload_to="currency/icons/",
        help_text="512x512 PNG, used in shop grid images",
    )
    emoji = models.CharField(
        max_length=20, blank=True, null=True,
        help_text="Discord emoji ID or unicode character, used in text/embeds",
    )

    rarity = models.FloatField(
        default=280.0,
        help_text="Drop weight. Higher = more common. Lower = rarer. "
                  "Use the same scale as ball rarity (e.g. 280 = very common, 0.2 = very rare).",
    )
    catch_reward_base = models.IntegerField(
        default=0, help_text="Flat amount awarded per eligible catch."
    )
    catch_reward_multiplier = models.FloatField(
        default=0, help_text="Bonus per point of rarity below the global max."
    )

    start_date = models.DateTimeField(
        blank=True, null=True, help_text="Leave blank for a permanent currency."
    )
    end_date = models.DateTimeField(
        blank=True, null=True, help_text="Leave blank for a permanent currency."
    )
    enabled = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    class Meta:
        db_table = "shop_currency"
        verbose_name = "Currency"
        verbose_name_plural = "Currencies"


class PlayerBalance(models.Model):
    player = models.ForeignKey(
        "bd_models.Player", on_delete=models.CASCADE, related_name="balances"
    )
    currency = models.ForeignKey(Currency, on_delete=models.CASCADE)
    amount = models.BigIntegerField(default=0)

    def __str__(self):
        return f"{self.player} — {self.amount} {self.currency}"

    class Meta:
        db_table = "shop_player_balance"
        unique_together = (("player", "currency"),)
        verbose_name = "Player Balance"
        verbose_name_plural = "Player Balances"


class ShopSection(models.Model):
    name = models.CharField(max_length=64)
    background = models.ImageField(
        upload_to="shop/backgrounds/",
        help_text="Background image for the shop grid",
    )
    overlay = models.ImageField(
        upload_to="shop/overlays/",
        blank=True, null=True,
        help_text="Optional top layer image pasted over everything (for frames, decorations)",
    )
    arrow_left = models.ImageField(
        upload_to="shop/arrows/",
        blank=True, null=True,
        help_text="Arrow image shown on the left when there is a previous page",
    )
    arrow_right = models.ImageField(
        upload_to="shop/arrows/",
        blank=True, null=True,
        help_text="Arrow image shown on the right when there is a next page",
    )
    discount_percent = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Percentage discount applied to ALL items in this section. Stacks with item sales.",
    )
    sale_start_date = models.DateTimeField(
        blank=True, null=True,
        help_text="When the section sale starts. Leave blank to start immediately.",
    )
    sale_end_date = models.DateTimeField(
        blank=True, null=True,
        help_text="When the section sale ends. Leave blank for no end date.",
    )
    order = models.IntegerField(default=0)
    enabled = models.BooleanField(default=True)

    is_rotating = models.BooleanField(default=False)
    rotation_size = models.IntegerField(default=3)
    rotation_hours = models.IntegerField(default=24)

    start_date = models.DateTimeField(blank=True, null=True)
    end_date = models.DateTimeField(blank=True, null=True)

    # ── Custom Fonts ──
    font_name = models.FileField(
        upload_to="shop/fonts/", blank=True, null=True,
        help_text="Font for item names (e.g. country names). TTF/OTF.",
    )
    font_name_size = models.IntegerField(default=56)

    font_price = models.FileField(
        upload_to="shop/fonts/", blank=True, null=True,
        help_text="Font for prices. TTF/OTF.",
    )
    font_price_size = models.IntegerField(default=48)

    font_desc = models.FileField(
        upload_to="shop/fonts/", blank=True, null=True,
        help_text="Font for descriptions and status text. TTF/OTF.",
    )
    font_desc_size = models.IntegerField(default=38)

    font_stats = models.FileField(
        upload_to="shop/fonts/", blank=True, null=True,
        help_text="Font for HP/ATK/Special bars. TTF/OTF.",
    )
    font_stats_size = models.IntegerField(default=34)

    font_empty = models.FileField(
        upload_to="shop/fonts/", blank=True, null=True,
        help_text="Font for 'No items available' and missing artwork placeholders. TTF/OTF.",
    )
    font_empty_size = models.IntegerField(default=80)

    font_qty = models.FileField(
        upload_to="shop/fonts/", blank=True, null=True,
        help_text="Font for quantity badge (x2, x3, etc.). TTF/OTF.",
    )
    font_qty_size = models.IntegerField(default=44)

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

    def __str__(self):
        return self.name

    class Meta:
        db_table = "shop_section"
        ordering = ["order"]
        verbose_name = "Shop Section"
        verbose_name_plural = "Shop Sections"


class ShopItem(models.Model):
    section = models.ForeignKey(ShopSection, on_delete=models.CASCADE, related_name="items")
    name = models.CharField(
        max_length=64, blank=True, null=True,
        help_text="Optional display name override. Defaults to the output ball's name.",
    )
    description = models.CharField(max_length=200, blank=True, null=True)

    currency = models.ForeignKey(Currency, on_delete=models.CASCADE)
    price = models.BigIntegerField()

    discount_percent = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Percentage discount (0-100). Set to 0 for no discount.",
    )
    sale_start_date = models.DateTimeField(
        blank=True, null=True,
        help_text="When the sale starts. Leave blank to start immediately.",
    )
    sale_end_date = models.DateTimeField(
        blank=True, null=True,
        help_text="When the sale ends. Leave blank for no end date.",
    )

    output_ball = models.ForeignKey(
        "bd_models.Ball", on_delete=models.CASCADE, related_name="+",
        null=True, blank=True,
        help_text="Cover ball shown in the grid. Optional if you use a custom image or if this is a bundle.",
    )
    output_special = models.ForeignKey(
        "bd_models.Special", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    output_attack_bonus = models.IntegerField(
        null=True, blank=True, help_text="Leave blank for random."
    )
    output_health_bonus = models.IntegerField(
        null=True, blank=True, help_text="Leave blank for random."
    )
    output_quantity = models.IntegerField(default=1)

    stock_limit = models.IntegerField(
        null=True, blank=True, help_text="Global cap on purchases. Leave blank for unlimited."
    )
    max_per_player = models.IntegerField(
        null=True, blank=True, help_text="Per-player cap. Leave blank for unlimited."
    )

    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # ── Bundle ──
    is_bundle = models.BooleanField(
        default=False,
        help_text="If true, this item is a bundle containing multiple balls.",
    )

    # ── Custom Grid Image ──
    custom_image = models.ImageField(
        upload_to="shop/items/",
        blank=True, null=True,
        help_text="Custom image shown in the shop grid. Overrides the ball's wild card. "
                  "Recommended: 380x380 or larger pixel art, PNG/GIF.",
    )

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

    def __str__(self):
        return self.name or f"Item #{self.pk}"

    class Meta:
        db_table = "shop_item"
        verbose_name = "Shop Item"
        verbose_name_plural = "Shop Items"


class ShopBundleItem(models.Model):
    """One row per ball inside a bundle."""
    shop_item = models.ForeignKey(
        ShopItem, on_delete=models.CASCADE, related_name="bundle_items"
    )
    ball = models.ForeignKey(
        "bd_models.Ball", on_delete=models.CASCADE, related_name="+"
    )
    special = models.ForeignKey(
        "bd_models.Special", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    attack_bonus = models.IntegerField(
        null=True, blank=True, help_text="Leave blank for random."
    )
    health_bonus = models.IntegerField(
        null=True, blank=True, help_text="Leave blank for random."
    )
    quantity = models.IntegerField(default=1)

    def __str__(self):
        return f"{self.quantity}× {self.ball}"

    class Meta:
        db_table = "shop_bundle_item"
        verbose_name = "Bundle Item"
        verbose_name_plural = "Bundle Items"


class ShopRotation(models.Model):
    section = models.OneToOneField(ShopSection, on_delete=models.CASCADE, related_name="rotation")
    started_at = models.DateTimeField(auto_now=True)
    item_ids = models.JSONField(default=list)

    class Meta:
        db_table = "shop_rotation"
        verbose_name = "Shop Rotation"
        verbose_name_plural = "Shop Rotations"


class ShopPurchase(models.Model):
    player = models.ForeignKey("bd_models.Player", on_delete=models.CASCADE)
    item = models.ForeignKey(ShopItem, on_delete=models.CASCADE, related_name="purchases")
    quantity = models.IntegerField(default=1)
    purchased_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.player} × {self.item} ({self.quantity})"

    class Meta:
        db_table = "shop_purchase"
        verbose_name = "Shop Purchase"
        verbose_name_plural = "Shop Purchases"


class PlayerDaily(models.Model):
    """Tracks daily reward streaks and last claim time."""
    player = models.OneToOneField(
        "bd_models.Player", on_delete=models.CASCADE, related_name="daily"
    )
    streak = models.IntegerField(default=0)
    last_claimed = models.DateTimeField(blank=True, null=True)
    total_claimed = models.IntegerField(default=0)

    def __str__(self):
        return f"{self.player} — Streak: {self.streak}, Total: {self.total_claimed}"

    class Meta:
        db_table = "shop_player_daily"
        verbose_name = "Player Daily"
        verbose_name_plural = "Player Dailies"