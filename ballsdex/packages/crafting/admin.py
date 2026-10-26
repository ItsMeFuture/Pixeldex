from django.contrib import admin
from django.utils.html import format_html

from .models import CraftingIngredient, CraftingLog, CraftingRecipe


class CraftingIngredientInline(admin.TabularInline):
    model = CraftingIngredient
    extra = 1
    fields = ("ball", "quantity", "special", "require_star_level")
    autocomplete_fields = ("ball", "special")


class CraftingLogInline(admin.TabularInline):
    model = CraftingLog
    extra = 0
    readonly_fields = ("player", "count", "last_crafted")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(CraftingRecipe)
class CraftingRecipeAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "output_ball",
        "output_special",
        "output_quantity",
        "output_star_level",
        "ingredient_count",
        "max_crafts_per_player",
        "enabled",
        "created_at",
    )
    list_filter = ("enabled", "output_special", "output_star_level")
    search_fields = ("name", "output_ball__country")
    autocomplete_fields = ("output_ball", "output_special")
    readonly_fields = ("created_at",)
    inlines = (CraftingIngredientInline, CraftingLogInline)

    fieldsets = (
        (None, {
            "fields": ("name", "description", "enabled"),
        }),
        ("Output", {
            "fields": (
                "output_ball",
                "output_special",
                "output_quantity",
                "output_star_level",
                "output_attack_bonus",
                "output_health_bonus",
            ),
            "description": (
                "Leave ATK/HP bonus blank for a random value. "
                "Star level requires the fusion package to be active."
            ),
        }),
        ("Limits", {
            "fields": ("max_crafts_per_player",),
        }),
        ("Metadata", {
            "fields": ("created_at",),
            "classes": ("collapse",),
        }),
    )

    @admin.display(description="Ingredients")
    def ingredient_count(self, obj):
        count = obj.ingredients.count()
        return format_html(
            '<span style="font-weight:bold">{}</span>', count
        )


@admin.register(CraftingLog)
class CraftingLogAdmin(admin.ModelAdmin):
    list_display = ("player", "recipe", "count", "last_crafted")
    list_filter = ("recipe",)
    search_fields = ("player__discord_id", "recipe__name")
    readonly_fields = ("player", "recipe", "count", "last_crafted")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False