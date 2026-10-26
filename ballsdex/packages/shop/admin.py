from django.contrib import admin
from django.utils import timezone
from ballsdex.settings import settings

from .models import (
    Currency,
    PlayerBalance,
    PlayerDaily,
    ShopItem,
    ShopBundleItem,
    ShopPurchase,
    ShopRotation,
    ShopSection,
)

@admin.register(Currency)
class CurrencyAdmin(admin.ModelAdmin):
    list_display = (
        "name", "rarity", "drop_chance", "catch_reward_base",
        "catch_reward_multiplier", "enabled",
    )
    list_filter = ("enabled",)
    search_fields = ("name",)
    fieldsets = (
        (None, {
            "fields": ("name", "icon", "emoji", "enabled"),
        }),
        ("Drop Settings", {
            "fields": ("rarity", "catch_reward_base", "catch_reward_multiplier"),
            "description": "Rarity = drop weight. Higher values drop more often. "
                           "Lower values are rarer but can drop from any ball. "
                           "Base/Multiplier control how much is given.",
        }),
        ("Limited Time (optional)", {
            "fields": ("start_date", "end_date"),
        }),
    )

    @admin.display(description="Drop %")
    def drop_chance(self, obj):
        max_rarity = settings.catch_reward_max_rarity
        if max_rarity <= 0:
            return "N/A"
        chance = (obj.rarity / max_rarity) * 100
        return f"{chance:.2f}%"


@admin.register(PlayerBalance)
class PlayerBalanceAdmin(admin.ModelAdmin):
    list_display = ("player_discord_id", "player_name", "currency", "formatted_amount")
    list_filter = ("currency",)
    search_fields = ("player__discord_id",)
    autocomplete_fields = ("player", "currency")
    list_per_page = 50
    ordering = ("-amount",)

    @admin.display(description="Discord ID")
    def player_discord_id(self, obj):
        return str(obj.player.discord_id)

    @admin.display(description="Player")
    def player_name(self, obj):
        return str(obj.player)

    @admin.display(description="Amount")
    def formatted_amount(self, obj):
        return f"{obj.amount:,}"


@admin.register(PlayerDaily)
class PlayerDailyAdmin(admin.ModelAdmin):
    list_display = ("player_discord_id", "player_name", "streak", "total_claimed", "last_claimed")
    search_fields = ("player__discord_id",)
    readonly_fields = ("player", "streak", "total_claimed", "last_claimed")

    @admin.display(description="Discord ID")
    def player_discord_id(self, obj):
        return str(obj.player.discord_id)

    @admin.display(description="Player")
    def player_name(self, obj):
        return str(obj.player)


class ShopItemInline(admin.TabularInline):
    model = ShopItem
    extra = 1
    fields = (
        "name", "custom_image", "output_ball", "output_special", "currency", "price",
        "discount_percent", "sale_start_date", "sale_end_date",
        "output_attack_bonus", "output_health_bonus", "output_quantity",
        "stock_limit", "max_per_player", "is_bundle", "enabled",
    )
    autocomplete_fields = ("output_ball", "output_special", "currency")


class ShopBundleItemInline(admin.TabularInline):
    model = ShopBundleItem
    extra = 1
    fields = ("ball", "special", "attack_bonus", "health_bonus", "quantity")
    autocomplete_fields = ("ball", "special")


@admin.register(ShopSection)
class ShopSectionAdmin(admin.ModelAdmin):
    list_display = (
        "name", "order", "is_rotating", "rotation_size", "rotation_hours",
        "sale_info", "start_date", "end_date", "enabled",
    )
    list_filter = ("enabled", "is_rotating")
    search_fields = ("name",)
    inlines = (ShopItemInline,)

    fieldsets = (
        (None, {"fields": ("name", "background", "overlay", "arrow_left", "arrow_right", "order", "enabled")}),
        ("Rotation", {
            "fields": ("is_rotating", "rotation_size", "rotation_hours"),
            "description": "If enabled, a random subset of items refreshes periodically.",
        }),
        ("Section Sale", {
            "fields": ("discount_percent", "sale_start_date", "sale_end_date"),
            "description": "Applies to ALL items in this section. Stacks multiplicatively with item-level sales.",
        }),
        ("Limited Time (optional)", {
            "fields": ("start_date", "end_date"),
            "description": "Leave blank for an always-available section.",
        }),
        ("Custom Fonts", {
            "fields": (
                "font_name", "font_name_size",
                "font_price", "font_price_size",
                "font_desc", "font_desc_size",
                "font_stats", "font_stats_size",
                "font_empty", "font_empty_size",
                "font_qty", "font_qty_size",
            ),
            "description": "Upload TTF/OTF files to override the default BallsDex fonts. Leave blank to use defaults.",
        }),
    )

    @admin.display(description="Sale")
    def sale_info(self, obj):
        if obj.is_on_sale:
            return f"-{obj.discount_percent}%"
        return "—"


@admin.register(ShopItem)
class ShopItemAdmin(admin.ModelAdmin):
    list_display = (
        "name", "section", "output_ball", "currency", "price", "sale_info",
        "stock_limit", "max_per_player", "is_bundle", "enabled",
    )
    list_filter = ("section", "currency", "is_bundle", "enabled")
    search_fields = ("name", "output_ball__country")
    autocomplete_fields = ("section", "output_ball", "output_special", "currency")
    inlines = (ShopBundleItemInline,)

    fieldsets = (
        (None, {"fields": ("section", "name", "description", "custom_image", "is_bundle", "enabled")}),
        ("Pricing", {
            "fields": ("currency", "price", "discount_percent", "sale_start_date", "sale_end_date"),
        }),
        ("Output", {
            "fields": ("output_ball", "output_special", "output_attack_bonus", "output_health_bonus", "output_quantity"),
            "description": "For bundles, output_ball is optional (used as cover if no custom image is set).",
        }),
        ("Limits", {
            "fields": ("stock_limit", "max_per_player"),
        }),
    )

    @admin.display(description="Sale")
    def sale_info(self, obj):
        if obj.is_on_sale:
            return f"-{obj.discount_percent}% → {obj.current_price:,}"
        return "—"


@admin.register(ShopRotation)
class ShopRotationAdmin(admin.ModelAdmin):
    list_display = ("section", "started_at", "item_ids")
    readonly_fields = ("section", "started_at", "item_ids")

    def has_add_permission(self, request):
        return False


@admin.register(ShopPurchase)
class ShopPurchaseAdmin(admin.ModelAdmin):
    list_display = ("player", "item", "quantity", "purchased_at")
    list_filter = ("item__section",)
    search_fields = ("player__discord_id", "item__name")
    readonly_fields = ("player", "item", "quantity", "purchased_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False