"""
Django ORM models for the crafting package.
Used by the admin panel only.
"""
from django.db import models


class CraftingRecipe(models.Model):
    name = models.CharField(max_length=64)
    description = models.TextField(blank=True, null=True)

    # Output
    output_ball = models.ForeignKey(
        "bd_models.Ball",
        on_delete=models.CASCADE,
        related_name="crafting_recipes",
        help_text="The ball this recipe produces",
    )
    output_special = models.ForeignKey(
        "bd_models.Special",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Force a specific special on the output (optional)",
    )
    output_attack_bonus = models.IntegerField(
        null=True,
        blank=True,
        help_text="Fixed ATK bonus on output. Leave blank for random.",
    )
    output_health_bonus = models.IntegerField(
        null=True,
        blank=True,
        help_text="Fixed HP bonus on output. Leave blank for random.",
    )
    output_quantity = models.IntegerField(
        default=1,
        help_text="How many copies of the ball this recipe produces",
    )
    output_star_level = models.IntegerField(
        default=0,
        help_text="Star level of the output ball (0 = base)",
    )

    # Limits
    max_crafts_per_player = models.IntegerField(
        null=True,
        blank=True,
        help_text="Max times a single player can craft this. Leave blank for unlimited.",
    )
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name

    class Meta:
        db_table = "crafting_recipe"
        verbose_name = "Crafting Recipe"
        verbose_name_plural = "Crafting Recipes"


class CraftingIngredient(models.Model):
    recipe = models.ForeignKey(
        CraftingRecipe,
        on_delete=models.CASCADE,
        related_name="ingredients",
    )
    ball = models.ForeignKey(
        "bd_models.Ball",
        on_delete=models.CASCADE,
        help_text="Required ball type",
    )
    special = models.ForeignKey(
        "bd_models.Special",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Require a specific special on this ingredient (optional)",
    )
    quantity = models.IntegerField(default=1)
    require_star_level = models.IntegerField(
        null=True,
        blank=True,
        help_text="Require a specific star level. Leave blank for any.",
    )

    def __str__(self) -> str:
        special_str = f" ({self.special})" if self.special_id else ""
        return f"{self.quantity}× {self.ball}{special_str}"

    class Meta:
        db_table = "crafting_ingredient"
        verbose_name = "Ingredient"
        verbose_name_plural = "Ingredients"


class CraftingLog(models.Model):
    player = models.ForeignKey(
        "bd_models.Player",
        on_delete=models.CASCADE,
        related_name="crafting_logs",
    )
    recipe = models.ForeignKey(
        CraftingRecipe,
        on_delete=models.CASCADE,
        related_name="logs",
    )
    count = models.IntegerField(default=0)
    last_crafted = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.player} × {self.recipe} ({self.count})"

    class Meta:
        db_table = "crafting_log"
        unique_together = (("player", "recipe"),)
        verbose_name = "Crafting Log"
        verbose_name_plural = "Crafting Logs"