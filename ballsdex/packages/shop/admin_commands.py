from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands
from tortoise import Tortoise

from ballsdex.core.models import Player
from ballsdex.packages.shop.currency import add_balance, get_balance
from ballsdex.packages.shop.tortoise_models import Currency
from ballsdex.settings import settings

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger("ballsdex.packages.shop.admin_commands")


class ShopAdmin(commands.Cog):
    """
    Admin commands for managing shop currency balances.
    """

    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot
        self._app_commands: list[app_commands.Command] = []

    # ── DB helpers (raw SQL via Tortoise, no Django import) ─────────────────

    async def _get_synced_guild_ids(self) -> list[int]:
        """Fetch guild IDs where shopadmin commands have been synced."""
        try:
            conn = Tortoise.get_connection("default")
            result = await conn.execute_query(
                "SELECT guild_id FROM guildconfig WHERE shopadmin_command_synced = true"
            )
            # Defensive: result shape varies by backend
            if isinstance(result, tuple) and len(result) == 2:
                rows = result[1]
            else:
                rows = result

            guild_ids: list[int] = []
            for row in rows:
                if isinstance(row, dict):
                    guild_ids.append(row.get("guild_id"))
                else:
                    guild_ids.append(row[0])
            return guild_ids
        except Exception:
            log.warning(
                "Failed to read shopadmin synced guilds (did you run the migration yet?)",
                exc_info=True,
            )
            return []

    async def _set_synced(self, guild_id: int, synced: bool) -> None:
        """Upsert guildconfig row (PostgreSQL ON CONFLICT syntax)."""
        conn = Tortoise.get_connection("default")
        val = "true" if synced else "false"
        query = (
            f"INSERT INTO guildconfig (guild_id, shopadmin_command_synced, enabled, silent) "
            f"VALUES ({int(guild_id)}, {val}, false, false) "
            f"ON CONFLICT (guild_id) "
            f"DO UPDATE SET shopadmin_command_synced = {val}"
        )
        await conn.execute_query(query)

    # ── Lifecycle (guild-restricted registration) ───────────────────────────

    async def cog_load(self):
        guild_ids = await self._get_synced_guild_ids()
        guilds = [discord.Object(gid) for gid in guild_ids]

        self._app_commands = list(self.__cog_app_commands__)

        for cmd in self._app_commands:
            self.bot.tree.remove_command(cmd.name, guild=discord.Object(0))
            self.bot.tree.add_command(cmd, guilds=guilds, override=True)

        self.__cog_app_commands__.clear()

    async def cog_unload(self):
        guild_ids = await self._get_synced_guild_ids()
        for cmd in self._app_commands:
            for gid in guild_ids:
                self.bot.tree.remove_command(cmd.name, guild=discord.Object(gid))
            self.bot.tree.remove_command(cmd.name, guild=discord.Object(0))
    async def cog_app_command_error(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        error: app_commands.AppCommandError,
    ):
        if isinstance(error, app_commands.CommandSignatureMismatch):
            assert self.bot.user
            await interaction.response.send_message(
                "Shop admin commands are desynchronized and need to be re-synced. "
                f"Run `{self.bot.user.mention} shopadmin_sync` to fix this.",
                ephemeral=True,
            )
            interaction.extras["handled"] = True

    # ── Autocomplete ─────────────────────────────────────────────────────────

    async def currency_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        currencies = await Currency.all().limit(25)
        return [
            app_commands.Choice(
                name=f"{'[OFF] ' if not c.enabled else ''}{c.name}", value=str(c.pk)
            )
            for c in currencies
            if not current or current.lower() in c.name.lower()
        ]

    def _currency_display(self, currency: Currency) -> str:
        if currency.emoji and currency.emoji.isdigit():
            emoji = self.bot.get_emoji(int(currency.emoji))
            if emoji:
                return f"{emoji} {currency.name}"
        if currency.emoji:
            return f"{currency.emoji} {currency.name}"
        return currency.name

    # ── Prefix sync commands (hidden from help) ─────────────────────────────

    @commands.command(name="shopadmin_sync", hidden=True)
    @commands.is_owner()
    @commands.guild_only()
    async def sync_slash(self, ctx: commands.Context):
        """Sync shop admin slash commands to this guild."""
        guild = ctx.guild
        if not guild:
            await ctx.send("This command can only be used in a server.")
            return

        for cmd in self._app_commands:
            self.bot.tree.add_command(cmd, guild=guild, override=True)
        await self.bot.tree.sync(guild=guild)

        await self._set_synced(guild.id, True)

        await ctx.send(f"Shop admin slash commands synced to **{guild.name}**.")

    @commands.command(name="shopadmin_unsync", hidden=True)
    @commands.is_owner()
    @commands.guild_only()
    async def unsync_slash(self, ctx: commands.Context):
        """Remove shop admin slash commands from this guild."""
        guild = ctx.guild
        if not guild:
            await ctx.send("This command can only be used in a server.")
            return

        for cmd in self._app_commands:
            self.bot.tree.remove_command(cmd.name, type=cmd.type, guild=guild)
        await self.bot.tree.sync(guild=guild)

        await self._set_synced(guild.id, False)

        await ctx.send(f"Shop admin slash commands removed from **{guild.name}**.")

    # ── Toggle ─────────────────────────────────────────────────────────────

    @app_commands.guilds(0)
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.command(name="shopadmin_toggle", description="Enable or disable a currency.")
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    @app_commands.autocomplete(currency=currency_autocomplete)
    async def toggle(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        currency: str,
    ):
        """Enable or disable a currency."""
        await interaction.response.defer(thinking=True, ephemeral=True)

        currency_obj = await Currency.get_or_none(pk=int(currency))
        if not currency_obj:
            await interaction.followup.send("Invalid currency selected.", ephemeral=True)
            return

        currency_obj.enabled = not currency_obj.enabled
        await currency_obj.save(update_fields=("enabled",))

        status = "enabled ✅" if currency_obj.enabled else "disabled ❌"
        embed = discord.Embed(
            title=f"Currency {status}",
            description=f"{self._currency_display(currency_obj)} is now **{status}**.",
            colour=discord.Colour.green() if currency_obj.enabled else discord.Colour.red(),
        )

        await interaction.followup.send(embed=embed, ephemeral=True)
        log.info(
            "%s toggled currency %s (%s) to %s",
            interaction.user, currency_obj.name, currency_obj.pk, currency_obj.enabled,
        )

    # ── Add ────────────────────────────────────────────────────────────────

    @app_commands.guilds(0)
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.command(name="shopadmin_add", description="Add currency to a player's balance.")
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    @app_commands.autocomplete(currency=currency_autocomplete)
    async def add(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        user: discord.User,
        currency: str,
        amount: int,
    ):
        """Add currency to a player's balance."""
        await interaction.response.defer(thinking=True, ephemeral=True)

        if amount < 1:
            await interaction.followup.send("Amount must be at least 1.", ephemeral=True)
            return

        currency_obj = await Currency.get_or_none(pk=int(currency))
        if not currency_obj:
            await interaction.followup.send("Invalid currency selected.", ephemeral=True)
            return

        player, _ = await Player.get_or_create(discord_id=user.id)
        new_balance = await add_balance(player, currency_obj, amount)

        embed = discord.Embed(
            title="💰 Currency Added",
            colour=discord.Colour.green(),
        )
        embed.add_field(name="User", value=user.mention, inline=True)
        embed.add_field(
            name="Currency", value=self._currency_display(currency_obj), inline=True
        )
        embed.add_field(name="Amount Added", value=f"+{amount:,}", inline=True)
        embed.add_field(name="New Balance", value=f"{new_balance:,}", inline=True)

        await interaction.followup.send(embed=embed, ephemeral=True)
        log.info(
            "%s added %d %s to %s (new balance: %d)",
            interaction.user, amount, currency_obj.name, user, new_balance,
        )

    # ── Remove ─────────────────────────────────────────────────────────────

    @app_commands.guilds(0)
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.command(
        name="shopadmin_remove", description="Remove currency from a player's balance."
    )
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    @app_commands.autocomplete(currency=currency_autocomplete)
    async def remove(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        user: discord.User,
        currency: str,
        amount: int,
    ):
        """Remove currency from a player's balance."""
        await interaction.response.defer(thinking=True, ephemeral=True)

        if amount < 1:
            await interaction.followup.send("Amount must be at least 1.", ephemeral=True)
            return

        currency_obj = await Currency.get_or_none(pk=int(currency))
        if not currency_obj:
            await interaction.followup.send("Invalid currency selected.", ephemeral=True)
            return

        player, _ = await Player.get_or_create(discord_id=user.id)
        new_balance = await add_balance(player, currency_obj, -amount)

        embed = discord.Embed(
            title="💰 Currency Removed",
            colour=discord.Colour.red(),
        )
        embed.add_field(name="User", value=user.mention, inline=True)
        embed.add_field(
            name="Currency", value=self._currency_display(currency_obj), inline=True
        )
        embed.add_field(name="Amount Removed", value=f"-{amount:,}", inline=True)
        embed.add_field(name="New Balance", value=f"{new_balance:,}", inline=True)

        await interaction.followup.send(embed=embed, ephemeral=True)
        log.info(
            "%s removed %d %s from %s (new balance: %d)",
            interaction.user, amount, currency_obj.name, user, new_balance,
        )

    # ── Set ────────────────────────────────────────────────────────────────

    @app_commands.guilds(0)
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.command(
        name="shopadmin_set",
        description="Set a player's currency balance to an exact amount.",
    )
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    @app_commands.autocomplete(currency=currency_autocomplete)
    async def set(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        user: discord.User,
        currency: str,
        amount: int,
    ):
        """Set a player's currency balance to an exact amount."""
        await interaction.response.defer(thinking=True, ephemeral=True)

        if amount < 0:
            await interaction.followup.send("Amount cannot be negative.", ephemeral=True)
            return

        currency_obj = await Currency.get_or_none(pk=int(currency))
        if not currency_obj:
            await interaction.followup.send("Invalid currency selected.", ephemeral=True)
            return

        player, _ = await Player.get_or_create(discord_id=user.id)
        current = await get_balance(player, currency_obj)
        diff = amount - current
        new_balance = await add_balance(player, currency_obj, diff)

        embed = discord.Embed(
            title="💰 Balance Set",
            colour=discord.Colour.gold(),
        )
        embed.add_field(name="User", value=user.mention, inline=True)
        embed.add_field(
            name="Currency", value=self._currency_display(currency_obj), inline=True
        )
        embed.add_field(name="Previous", value=f"{current:,}", inline=True)
        embed.add_field(name="New Balance", value=f"{new_balance:,}", inline=True)

        await interaction.followup.send(embed=embed, ephemeral=True)
        log.info(
            "%s set %s's %s balance to %d (was %d)",
            interaction.user, user, currency_obj.name, new_balance, current,
        )

    # ── Balance ────────────────────────────────────────────────────────────

    @app_commands.guilds(0)
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.command(
        name="shopadmin_balance", description="Check a player's currency balance(s)."
    )
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    async def balance(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        user: discord.User,
        currency: str | None = None,
    ):
        """Check a player's currency balance(s)."""
        await interaction.response.defer(thinking=True, ephemeral=True)

        player = await Player.get_or_none(discord_id=user.id)
        if not player:
            await interaction.followup.send(
                "This user has no recorded balance.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"💰 Balance — {user.display_name}",
            colour=discord.Colour.gold(),
        )
        embed.set_thumbnail(url=user.display_avatar.url)

        if currency:
            currency_obj = await Currency.get_or_none(pk=int(currency))
            if not currency_obj:
                await interaction.followup.send("Invalid currency selected.", ephemeral=True)
                return
            amount = await get_balance(player, currency_obj)
            embed.add_field(
                name=self._currency_display(currency_obj),
                value=f"{amount:,}",
                inline=True,
            )
        else:
            currencies = await Currency.all().order_by("name")
            if not currencies:
                await interaction.followup.send("No currencies configured.", ephemeral=True)
                return
            for c in currencies:
                amount = await get_balance(player, c)
                embed.add_field(
                    name=self._currency_display(c),
                    value=f"{amount:,}",
                    inline=True,
                )

        await interaction.followup.send(embed=embed, ephemeral=True)