"""
Tortoise ORM models for the crafting package.
Referenced in config.yaml under extra-tortoise-models.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from tortoise import fields, models

if TYPE_CHECKING:
    from ballsdex.core.models import Ball, BallInstance, Player, Special


class CraftingRecipe(models.Model):
    name = fields.CharField(max_length=64, description="Name of this recipe")
    description = fields.TextField(
        null=True, default=None, description="Optional description shown to players"
    )

    # Output
    output_ball: fields.ForeignKeyRelation["Ball"] = fields.ForeignKeyField(
        "models.Ball", description="The ball this recipe produces"
    )
    output_special: fields.ForeignKeyRelation["Special"] | None = fields.ForeignKeyField(
        "models.Special",
        null=True,
        default=None,
        on_delete=fields.SET_NULL,
        description="Force a specific special on the output (optional)",
    )
    output_attack_bonus = fields.IntField(
        null=True,
        default=None,
        description="Fixed ATK bonus on output. Leave null for random.",
    )
    output_health_bonus = fields.IntField(
        null=True,
        default=None,
        description="Fixed HP bonus on output. Leave null for random.",
    )
    output_quantity = fields.IntField(
        default=1, description="How many copies of the ball this recipe produces"
    )
    output_star_level = fields.IntField(
        default=0, description="Star level of the output ball (0 = base)"
    )

    # Limits
    max_crafts_per_player = fields.IntField(
        null=True,
        default=None,
        description="Max times a player can craft this. Null = unlimited.",
    )
    enabled = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    ingredients: fields.ReverseRelation["CraftingIngredient"]

    def __str__(self) -> str:
        return self.name

    class Meta:
        table = "crafting_recipe"


class CraftingIngredient(models.Model):
    recipe: fields.ForeignKeyRelation[CraftingRecipe] = fields.ForeignKeyField(
        "models.CraftingRecipe",
        related_name="ingredients",
        on_delete=fields.CASCADE,
    )
    ball: fields.ForeignKeyRelation["Ball"] = fields.ForeignKeyField(
        "models.Ball", description="Required ball type"
    )
    special: fields.ForeignKeyRelation["Special"] | None = fields.ForeignKeyField(
        "models.Special",
        null=True,
        default=None,
        on_delete=fields.SET_NULL,
        description="Require a specific special on this ingredient (optional)",
    )
    quantity = fields.IntField(default=1, description="How many of this ball are required")
    require_star_level = fields.IntField(
        null=True,
        default=None,
        description="Require a specific star level. Null = any.",
    )

    def __str__(self) -> str:
        return f"{self.quantity}x {self.ball_id}"

    class Meta:
        table = "crafting_ingredient"


class CraftingLog(models.Model):
    """Tracks how many times each player has crafted each recipe."""
    player: fields.ForeignKeyRelation["Player"] = fields.ForeignKeyField(
        "models.Player", on_delete=fields.CASCADE
    )
    recipe: fields.ForeignKeyRelation[CraftingRecipe] = fields.ForeignKeyField(
        "models.CraftingRecipe", on_delete=fields.CASCADE
    )
    count = fields.IntField(default=0)
    last_crafted = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "crafting_log"
        unique_together = (("player", "recipe"),)