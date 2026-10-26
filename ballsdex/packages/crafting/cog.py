from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING, AsyncIterator, List, Set

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Button, View

from ballsdex.core.models import BallInstance, Player
from ballsdex.core.utils.paginator import FieldPageSource, Pages
from ballsdex.packages.balls.countryballs_paginator import CountryballsSource
from ballsdex.packages.crafting.tortoise_models import (
    CraftingIngredient,
    CraftingLog,
    CraftingRecipe,
)
from ballsdex.settings import settings

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger("ballsdex.packages.crafting")


async def _count_ingredients(
    player: Player,
    recipe: CraftingRecipe,
) -> dict[int, int]:
    """
    Returns {ingredient.pk: count_available} for every ingredient.
    Always returns per-ingredient counts regardless of whether all
    requirements are met — used for display in craft_view.
    """
    counts: dict[int, int] = {}
    for ing in recipe.ingredients:
        filters: dict = {"player": player, "ball_id": ing.ball_id}
        if ing.special_id:
            filters["special_id"] = ing.special_id
        if ing.require_star_level is not None:
            filters["star_level"] = ing.require_star_level
        counts[ing.pk] = await BallInstance.filter(**filters).count()
    return counts


async def _get_matching_ball_ids(
    player: Player,
    ing: CraftingIngredient,
    exclude_ids: set[int] | None = None,
) -> list[int]:
    """
    Returns IDs of all BallInstances the player has that match an ingredient.
    No tradeable filter — any owned ball is valid as a crafting ingredient.
    Optionally excludes IDs already chosen for other ingredients.
    """
    filters: dict = {"player": player, "ball_id": ing.ball_id}
    if ing.special_id:
        filters["special_id"] = ing.special_id
    if ing.require_star_level is not None:
        filters["star_level"] = ing.require_star_level
    qs = BallInstance.filter(**filters)
    ids: list[int] = await qs.values_list("id", flat=True)
    if exclude_ids:
        ids = [i for i in ids if i not in exclude_ids]
    return ids


# ── Crafting ball selector ─────────────────────────────────────────────────────

class CraftingIngredientSource(CountryballsSource):
    """
    Same paging behaviour as CountryballsSource, but returns the selector's
    status embed from format_page so Pages renders it automatically on the
    initial message and on every page change.
    """

    async def format_page(self, menu: "CraftingBallSelector", balls):
        # Let the parent handle converting the raw page (list of IDs) into
        # the async iterator and calling menu.set_options(...) correctly.
        await super().format_page(menu, balls)
        return menu._status_embed()


class CraftingBallSelector(Pages):
    """
    Paginated dropdown selector for picking ingredient balls.
    Mirrors the trade bulk-add CountryballsSelector pattern:
    selections accumulate across pages until the required count is reached.
    """

    def __init__(
        self,
        interaction: discord.Interaction,
        ball_ids: List[int],
        required: int,
        ing_label: str,
    ):
        self.bot = interaction.client
        source = CraftingIngredientSource(ball_ids)
        super().__init__(source, interaction=interaction)
        self.required = required
        self.ing_label = ing_label
        self.balls_selected: Set[BallInstance] = set()
        self.confirmed = False
        self.cancelled = False

        self.add_item(self.select_menu)
        self.add_item(self.random_button)
        self.add_item(self.confirm_button)
        self.add_item(self.cancel_button)

    # Called by CraftingIngredientSource.format_page each time the page changes
    async def set_options(self, balls: AsyncIterator[BallInstance]):
        options: List[discord.SelectOption] = []
        async for ball in balls:
            emoji = self.bot.get_emoji(int(ball.countryball.emoji_id))
            special_str = f"✨ " if ball.special_id else ""
            options.append(
                discord.SelectOption(
                    label=f"#{ball.pk:0X} {ball.countryball.country}",
                    description=(
                        f"{special_str}"
                        f"ATK: {ball.attack_bonus:+d}% • "
                        f"HP: {ball.health_bonus:+d}%"
                    ),
                    emoji=emoji,
                    value=str(ball.pk),
                    default=any(b.pk == ball.pk for b in self.balls_selected),
                )
            )
        self.select_menu.options = options
        # Allow selecting up to however many are still needed
        remaining = max(1, self.required - len(self.balls_selected))
        self.select_menu.max_values = min(remaining, len(options))
        self.select_menu.min_values = 0
        self._refresh_button_states()

    def _refresh_button_states(self):
        self.confirm_button.disabled = len(self.balls_selected) < self.required
        self.random_button.disabled = len(self.balls_selected) >= self.required

    def _status_embed(self) -> discord.Embed:
        e = discord.Embed(
            title=f"⚒️ Select ingredient: {self.ing_label}",
            description=(
                "Use the dropdown below to pick balls, or click **Random** "
                "to auto-fill the rest.\nSelections carry over when you change page."
            ),
            colour=discord.Colour.orange(),
        )
        e.add_field(
            name="Progress",
            value=f"**{len(self.balls_selected)}/{self.required}** selected",
            inline=False,
        )
        if self.balls_selected:
            lines = [
                f"• `#{b.pk:0X}` **{b.countryball.country}** "
                f"ATK {b.attack_bonus:+d}% HP {b.health_bonus:+d}%"
                for b in self.balls_selected
            ]
            e.add_field(name="Currently selected", value="\n".join(lines), inline=False)
        return e

    @discord.ui.select(min_values=0, max_values=25, placeholder="Select balls to use…")
    async def select_menu(
        self,
        interaction: discord.Interaction,
        item: discord.ui.Select,
    ):
        for value in item.values:
            if len(self.balls_selected) >= self.required:
                break
            ball = await BallInstance.get(id=int(value)).prefetch_related("ball", "special")
            self.balls_selected.add(ball)
        self._refresh_button_states()
        await interaction.response.edit_message(embed=self._status_embed(), view=self)

    @discord.ui.button(label="🎲 Random", style=discord.ButtonStyle.primary)
    async def random_button(
        self,
        interaction: discord.Interaction,
        button: Button,
    ):
        needed = self.required - len(self.balls_selected)
        selected_ids = {b.pk for b in self.balls_selected}
        # Pull all IDs from the paginator source and pick randomly from unselected
        all_ids: list[int] = self.source.entries  # type: ignore
        available = [i for i in all_ids if i not in selected_ids]
        picks = random.sample(available, min(needed, len(available)))
        for ball_id in picks:
            ball = await BallInstance.get(id=ball_id).prefetch_related("ball", "special")
            self.balls_selected.add(ball)
        self._refresh_button_states()
        await interaction.response.edit_message(embed=self._status_embed(), view=self)

    @discord.ui.button(
        label="✅ Confirm", style=discord.ButtonStyle.success, disabled=True
    )
    async def confirm_button(
        self,
        interaction: discord.Interaction,
        button: Button,
    ):
        self.confirmed = True
        self.stop()
        for item in self.children:
            item.disabled = True  # type: ignore
        done_embed = discord.Embed(
            title="✅ Ingredient confirmed",
            description=(
                f"Confirmed **{len(self.balls_selected)}** ball(s) "
                f"for **{self.ing_label}**."
            ),
            colour=discord.Colour.green(),
        )
        await interaction.response.edit_message(embed=done_embed, view=self)

    @discord.ui.button(label="✖️ Cancel", style=discord.ButtonStyle.danger)
    async def cancel_button(
        self,
        interaction: discord.Interaction,
        button: Button,
    ):
        self.cancelled = True
        self.stop()
        for item in self.children:
            item.disabled = True  # type: ignore
        cancel_embed = discord.Embed(
            title="✖️ Crafting cancelled",
            colour=discord.Colour.red(),
        )
        await interaction.response.edit_message(embed=cancel_embed, view=self)


# ── Confirm view ───────────────────────────────────────────────────────────────

class ConfirmCraftView(View):
    def __init__(self, interaction: discord.Interaction):
        super().__init__(timeout=60)
        self.interaction = interaction
        self.confirmed = False

    @discord.ui.button(label="⚒️ Craft!", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: Button):
        self.confirmed = True
        self.stop()
        for item in self.children:
            item.disabled = True  # type: ignore
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="✖️ Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: Button):
        self.stop()
        for item in self.children:
            item.disabled = True  # type: ignore
        await interaction.response.edit_message(view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.interaction.user.id:
            await interaction.response.send_message(
                "This is not your crafting menu.", ephemeral=True
            )
            return False
        return True


# ── Cog ────────────────────────────────────────────────────────────────────────

class Crafting(commands.GroupCog, group_name="craft"):
    """
    Craft new pixelballs from recipes.
    """

    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot

    @app_commands.command(name="list")
    async def craft_list(self, interaction: discord.Interaction["BallsDexBot"]):
        """
        Show all available crafting recipes.
        """
        recipes = await CraftingRecipe.filter(enabled=True).prefetch_related(
            "output_ball", "output_special"
        )

        if not recipes:
            await interaction.response.send_message(
                "There are no crafting recipes available right now.", ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True)

        entries: list[tuple[str, str]] = []
        for recipe in recipes:
            ball = recipe.output_ball
            emoji = self.bot.get_emoji(ball.emoji_id) or "⋄"
            special_str = f" ✨ {recipe.output_special.name}" if recipe.output_special_id else ""
            qty_str = f" ×{recipe.output_quantity}" if recipe.output_quantity > 1 else ""
            ing_count = await CraftingIngredient.filter(recipe=recipe).count()
            limit_str = (
                f" • Limit: {recipe.max_crafts_per_player}×"
                if recipe.max_crafts_per_player
                else ""
            )
            value = (
                (f"{recipe.description}\n" if recipe.description else "")
                + f"{ing_count} ingredient(s){limit_str}"
            )
            entries.append((f"{emoji} {ball.country}{special_str}{qty_str}", value))

        source = FieldPageSource(entries, per_page=5, inline=False)
        source.embed.title = "⚒️ Crafting Recipes"
        source.embed.description = (
            "Use `/craft view <name>` to see full details and ingredients."
        )
        source.embed.colour = discord.Colour.orange()

        pages = Pages(source=source, interaction=interaction)
        await pages.start()

    @app_commands.command(name="view")
    @app_commands.describe(name="Name of the recipe to view")
    async def craft_view(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        name: str,
    ):
        """
        View the details and ingredients of a specific recipe.

        Parameters
        ----------
        name: str
            Name of the recipe.
        """
        recipe = await CraftingRecipe.filter(
            name__icontains=name, enabled=True
        ).prefetch_related("output_ball", "output_special", "ingredients").first()

        if not recipe:
            await interaction.response.send_message(
                f"No recipe found matching `{name}`.", ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True, ephemeral=True)

        player, _ = await Player.get_or_create(discord_id=interaction.user.id)
        counts = await _count_ingredients(player, recipe)
        can_craft = all(
            counts.get(ing.pk, 0) >= ing.quantity for ing in recipe.ingredients
        )

        ball = recipe.output_ball
        emoji = self.bot.get_emoji(ball.emoji_id) or ""
        special_str = f" ✨ {recipe.output_special.name}" if recipe.output_special_id else ""
        atk_str = (
            f"{recipe.output_attack_bonus:+d}%"
            if recipe.output_attack_bonus is not None
            else "random"
        )
        hp_str = (
            f"{recipe.output_health_bonus:+d}%"
            if recipe.output_health_bonus is not None
            else "random"
        )
        qty_str = f"×{recipe.output_quantity} " if recipe.output_quantity > 1 else ""

        embed = discord.Embed(
            title=f"⚒️ {recipe.name}",
            description=recipe.description or "",
            colour=discord.Colour.green() if can_craft else discord.Colour.red(),
        )
        embed.add_field(
            name="Output",
            value=(
                f"{emoji} {qty_str}**{ball.country}**{special_str}\n"
                f"ATK: {atk_str} • HP: {hp_str}"
            ),
            inline=False,
        )

        ing_lines = []
        for ing in recipe.ingredients:
            ing_ball = await ing.ball
            ing_emoji = self.bot.get_emoji(ing_ball.emoji_id) or "⋄"
            special_ing = f" ✨ {(await ing.special).name}" if ing.special_id else ""
            star_str = f" ({'✦' * ing.require_star_level})" if ing.require_star_level else ""
            have = counts.get(ing.pk, 0)
            status = "✅" if have >= ing.quantity else f"❌ {have}/{ing.quantity}"
            ing_lines.append(
                f"{status} {ing_emoji} {ing.quantity}× "
                f"**{ing_ball.country}**{special_ing}{star_str}"
            )

        embed.add_field(
            name="Ingredients",
            value="\n".join(ing_lines) or "None",
            inline=False,
        )

        if recipe.max_crafts_per_player:
            log_entry = await CraftingLog.get_or_none(player=player, recipe=recipe)
            crafted = log_entry.count if log_entry else 0
            embed.add_field(
                name="Craft Limit",
                value=f"{crafted}/{recipe.max_crafts_per_player} used",
                inline=True,
            )

        embed.set_footer(
            text="✅ You can craft this!" if can_craft else "❌ Missing ingredients"
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="make")
    @app_commands.describe(
        name="Name of the recipe to craft",
        random_selection="Skip selection and automatically pick random balls for all ingredients",
    )
    async def craft_make(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        name: str,
        random_selection: bool = False,
    ):
        """
        Craft a recipe. Pick which balls to use, or set random to True to skip selection.

        Parameters
        ----------
        name: str
            Name of the recipe to craft.
        random_selection: bool
            Automatically pick random matching balls for all ingredients.
        """
        recipe = await CraftingRecipe.filter(
            name__icontains=name, enabled=True
        ).prefetch_related("output_ball", "output_special", "ingredients").first()

        if not recipe:
            await interaction.response.send_message(
                f"No recipe found matching `{name}`.", ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True, ephemeral=True)

        player, _ = await Player.get_or_create(discord_id=interaction.user.id)

        # Check craft limit
        if recipe.max_crafts_per_player:
            log_entry = await CraftingLog.get_or_none(player=player, recipe=recipe)
            if log_entry and log_entry.count >= recipe.max_crafts_per_player:
                await interaction.followup.send(
                    f"You have reached the craft limit for **{recipe.name}** "
                    f"({recipe.max_crafts_per_player}×).",
                    ephemeral=True,
                )
                return

        # Quick count check before opening selection
        counts = await _count_ingredients(player, recipe)
        missing = []
        for ing in recipe.ingredients:
            have = counts.get(ing.pk, 0)
            if have < ing.quantity:
                ing_ball = await ing.ball
                special_str = f" ✨ {(await ing.special).name}" if ing.special_id else ""
                ing_emoji = self.bot.get_emoji(ing_ball.emoji_id) or "⋄"
                missing.append(
                    f"{ing_emoji} **{ing_ball.country}{special_str}** "
                    f"• Need: `{ing.quantity}` • Have: `{have}`"
                )
        if missing:
            ball = recipe.output_ball
            emoji = self.bot.get_emoji(ball.emoji_id) or "⋄"
            
            missing_embed = discord.Embed(
                title="❌ Missing Ingredients",
                description=f"You are missing ingredients for {emoji} **{recipe.name}**:",
                colour=discord.Colour.red(),
            )
            missing_embed.add_field(name="Needed:", value="\n".join(missing), inline=False)
            await interaction.followup.send(embed=missing_embed, ephemeral=True)
            return

        all_selected: dict[int, list[BallInstance]] = {}
        used_ids: set[int] = set()

        for ing in recipe.ingredients:
            ing_ball = await ing.ball
            special_str = f" ✨ {(await ing.special).name}" if ing.special_id else ""
            ing_label = f"{ing.quantity}× {ing_ball.country}{special_str}"

            ball_ids = await _get_matching_ball_ids(player, ing, exclude_ids=used_ids)

            if len(ball_ids) < ing.quantity:
                await interaction.followup.send(
                    f"Not enough unique **{ing_ball.country}{special_str}** balls available "
                    f"(some may already be reserved for another ingredient in this recipe).",
                    ephemeral=True,
                )
                return

            if random_selection:
                # Auto-pick random matching balls, skip the selector entirely
                picks = random.sample(ball_ids, ing.quantity)
                chosen = await BallInstance.filter(id__in=picks).prefetch_related("ball", "special")
                chosen = list(chosen)
            else:
                selector = CraftingBallSelector(
                    interaction, ball_ids, ing.quantity, ing_label
                )
                await selector.start(
                    ephemeral=True,
                )
                await selector.wait()

                if selector.cancelled or not selector.confirmed:
                    await interaction.followup.send("Crafting cancelled.", ephemeral=True)
                    return

                chosen = list(selector.balls_selected)

            all_selected[ing.pk] = chosen
            used_ids.update(b.pk for b in chosen)

        # Final confirmation showing exactly which balls will be consumed
        ball = recipe.output_ball
        emoji = self.bot.get_emoji(ball.emoji_id) or ""
        special_str = f" ✨ {recipe.output_special.name}" if recipe.output_special_id else ""
        qty_str = f"×{recipe.output_quantity} " if recipe.output_quantity > 1 else ""

        consumed_lines = []
        for ing in recipe.ingredients:
            ing_ball = await ing.ball
            ing_emoji = self.bot.get_emoji(ing_ball.emoji_id) or "⋄"
            for b in all_selected[ing.pk]:
                consumed_lines.append(
                    f"{ing_emoji} **{ing_ball.country}** "
                    f"`#{b.pk:0X}` ATK {b.attack_bonus:+d}% HP {b.health_bonus:+d}%"
                )

        confirm_embed = discord.Embed(
            title="⚒️ Confirm Craft",
            description=(
                f"**You will receive:** {emoji} {qty_str}{ball.country}{special_str}\n\n"
                f"**Pixelballs consumed:**\n"
                + "\n".join(consumed_lines)
                + "\n\n⚠️ *This cannot be undone.*"
            ),
            colour=discord.Colour.orange(),
        )

        confirm_view = ConfirmCraftView(interaction)
        await interaction.followup.send(
            embed=confirm_embed, view=confirm_view, ephemeral=True
        )
        await confirm_view.wait()

        if not confirm_view.confirmed:
            await interaction.followup.send("Crafting cancelled.", ephemeral=True)
            return

        # Consume selected balls
        for instances in all_selected.values():
            for instance in instances:
                instance.deleted = True
                await instance.save(update_fields=("deleted",))

        # Create output ball(s)
        created = []
        for _ in range(recipe.output_quantity):
            atk = (
                recipe.output_attack_bonus
                if recipe.output_attack_bonus is not None
                else random.randint(-settings.max_attack_bonus, settings.max_attack_bonus)
            )
            hp = (
                recipe.output_health_bonus
                if recipe.output_health_bonus is not None
                else random.randint(-settings.max_health_bonus, settings.max_health_bonus)
            )
            instance = await BallInstance.create(
                ball=recipe.output_ball,
                player=player,
                special=recipe.output_special if recipe.output_special_id else None,
                attack_bonus=atk,
                health_bonus=hp,
            )
            created.append(instance)

        # Update craft log
        log_entry, _ = await CraftingLog.get_or_create(player=player, recipe=recipe)
        log_entry.count += 1
        await log_entry.save(update_fields=("count", "last_crafted"))

        ids = ", ".join(f"`#{c.pk:0X}`" for c in created)
        await interaction.followup.send(
            f"✅ Crafted! You received {emoji} {qty_str}**{ball.country}**{special_str} {ids}",
            ephemeral=True,
        )
        log.info(
            "%s crafted recipe %s, produced %d ball(s)",
            interaction.user,
            recipe.name,
            recipe.output_quantity,
        )

    @craft_view.autocomplete("name")
    @craft_make.autocomplete("name")
    async def recipe_autocomplete(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        current: str,
    ) -> list[app_commands.Choice[str]]:
        recipes = await CraftingRecipe.filter(enabled=True).only("name")
        return [
            app_commands.Choice(name=r.name, value=r.name)
            for r in recipes
            if current.lower() in r.name.lower()
        ][:25]