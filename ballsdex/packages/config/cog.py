from typing import TYPE_CHECKING, Optional, cast

import discord
from discord import app_commands
from discord.ext import commands

from ballsdex.core.models import GuildConfig
from ballsdex.packages.config.components import AcceptTOSView
from ballsdex.settings import settings

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot


MIN_MEMBERS_FOR_SPAWN = 15


async def count_humans(guild: discord.Guild) -> int:
    """
    Count human members. Chunks the guild if the member cache isn't complete.
    """
    if not guild.chunked:
        try:
            await guild.chunk()
        except (discord.HTTPException, discord.ClientException):
            pass

    if guild.members:
        return sum(1 for m in guild.members if not m.bot)

    return max(0, (guild.member_count or 0) - 3)

activation_embed = discord.Embed(
    colour=0x00D936,
    title=f"{settings.bot_name} activation",
    description=f"To enable {settings.bot_name} in your server, you must "
    f"read and accept the [Terms of Service]({settings.terms_of_service}).\n\n"
    "As a summary, these are the rules of the bot:\n"
    f"- No farming (spamming or creating servers for {settings.plural_collectible_name})\n"
    f"- Selling or exchanging {settings.plural_collectible_name} "
    "against money or other goods is forbidden\n"
    "- Do not attempt to abuse the bot's internals\n"
    "**Not respecting these rules will lead to a blacklist**",
)


@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
class Config(commands.GroupCog):
    """
    View and manage your countryballs collection.
    """

    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot

    @app_commands.command()
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.checks.bot_has_permissions(
        read_messages=True,
        send_messages=True,
        embed_links=True,
    )
    async def channel(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        channel: Optional[discord.TextChannel] = None,
    ):
        """
        Set or change the channel where countryballs will spawn.

        Parameters
        ----------
        channel: discord.TextChannel
            The channel you want to set, current one if not specified.
        """
        user = cast(discord.Member, interaction.user)

        if channel is None:
            if isinstance(interaction.channel, discord.TextChannel):
                channel = interaction.channel
            else:
                await interaction.response.send_message(
                    "The current channel is not a valid text channel.", ephemeral=True
                )
                return

        guild = interaction.guild
        assert guild
        if guild.unavailable:
            await interaction.response.send_message(
                "The server is unavailable to the bot and will not work properly. "
                "Kicking and readding the bot may fix this.",
                ephemeral=True,
            )
            return

        # --- Minimum human member check ---
        human_count = await count_humans(guild)
        if human_count < MIN_MEMBERS_FOR_SPAWN:
            await interaction.response.send_message(
                f"This server has **{human_count}** human members, but spawning requires at least "
                f"**{MIN_MEMBERS_FOR_SPAWN}** human members to prevent farming.\n"
                f"Once your server reaches {MIN_MEMBERS_FOR_SPAWN} human members, run this command again.",
                ephemeral=True,
            )
            return
        # --- End check ---

        view = AcceptTOSView(interaction, channel, user)
        embed = activation_embed.copy()

        readable_channels = len(
            [x for x in guild.text_channels if x.permissions_for(guild.me).read_messages]
        )
        if readable_channels / len(guild.text_channels) < 0.75:
            embed.add_field(
                name="\N{WARNING SIGN}\N{VARIATION SELECTOR-16} Warning",
                value=f"This server has {len(guild.text_channels)} channels, but "
                f"{settings.bot_name} can only read {readable_channels} channels.\n"
                "Spawn is based on message activity, too few readable channels will result in "
                "fewer spawns. It is recommended that you inspect your permissions.",
            )
        message = await channel.send(embed=embed, view=view)
        view.message = message

        await interaction.response.send_message(
            f"The activation embed has been sent in {channel.mention}.\n\n"
            "-# PixelDex will scan this server to verify it meets the human member criteria before enabling spawns.",
            ephemeral=True,
        )

    @app_commands.command()
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.checks.bot_has_permissions(send_messages=True)
    async def disable(self, interaction: discord.Interaction["BallsDexBot"]):
        """
        Disable or enable countryballs spawning.
        """
        guild = cast(discord.Guild, interaction.guild)  # guild-only command
        config, created = await GuildConfig.get_or_create(guild_id=interaction.guild_id)
        if config.enabled:
            config.enabled = False  # type: ignore
            await config.save()
            self.bot.dispatch("ballsdex_settings_change", guild, enabled=False)
            await interaction.response.send_message(
                f"{settings.bot_name} is now disabled in this server. Commands will still be "
                f"available, but the spawn of new {settings.plural_collectible_name} "
                "is suspended.\nTo re-enable the spawn, use the same command."
            )
        else:
            config.enabled = True  # type: ignore
            await config.save()
            self.bot.dispatch("ballsdex_settings_change", guild, enabled=True)
            if config.spawn_channel and (channel := guild.get_channel(config.spawn_channel)):
                if channel:
                    await interaction.response.send_message(
                        f"{settings.bot_name} is now enabled in this server, "
                        f"{settings.plural_collectible_name} will start spawning "
                        f"soon in {channel.mention}."
                    )
                else:
                    await interaction.response.send_message(
                        "The spawning channel specified in the configuration is not available.",
                        ephemeral=True,
                    )
            else:
                config_cmd = self.channel.extras.get("mention", "`/config channel`")
                await interaction.response.send_message(
                    f"{settings.bot_name} is now enabled in this server, however there is no "
                    f"spawning channel set. Please configure one with {config_cmd}."
                )

    @app_commands.command()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def status(self, interaction: discord.Interaction["BallsDexBot"]):
        """
        Check the server configuration status.
        """
        config = await GuildConfig.get_or_none(guild_id=interaction.guild_id)
        config_cmd = self.channel.extras.get("mention", "`/config channel`")
        if not config or not config.spawn_channel:
            await interaction.response.send_message(
                f"{settings.bot_name} is not configured in this server yet.\n"
                f"Please use {config_cmd} to set a channel.",
                ephemeral=False,
            )
        else:
            assert interaction.guild
            if interaction.guild.unavailable:
                await interaction.response.send_message(
                    "Your server is unavailable to the bot. Readding it may fix this."
                )
                return
            channel = interaction.guild.get_channel(config.spawn_channel)
            if channel:
                await interaction.response.send_message(
                    f"{settings.bot_name} is configured in this server.\n"
                    f"Spawn channel: {channel.mention}\n"
                    f"Status: {'Enabled' if config.enabled else 'Disabled'}",
                    ephemeral=False,
                )
            else:
                await interaction.response.send_message(
                    f"{settings.bot_name} is configured, but the specified channel "
                    "could not be found.\n"
                    f"Please use {config_cmd} to set it again.",
                    ephemeral=False,
                )

    @app_commands.command()
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(
        role="The role to ping on each spawn message.",
        remove="Set to True to remove the current spawn role.",
    )
    async def spawnrole(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        role: Optional[str] = None,
        remove: bool = False,
    ):
        guild_id = interaction.guild_id
        assert guild_id

        config, _ = await GuildConfig.get_or_create(guild_id=guild_id)

        resolved_role: discord.Role | None = None
        if role is not None:
            guild = interaction.guild
            assert guild
            try:
                resolved_role = guild.get_role(int(role))
            except (ValueError, TypeError):
                await interaction.response.send_message("Invalid role.", ephemeral=True)
                return
            if resolved_role is None:
                await interaction.response.send_message("Role not found.", ephemeral=True)
                return

        if remove:
            if config.spawn_role:
                old = config.spawn_role
                config.spawn_role = None
                await config.save(update_fields=("spawn_role",))
                await interaction.response.send_message(
                    f"{settings.bot_name} will no longer alert <@&{old}> when "
                    f"{settings.plural_collectible_name} spawn.",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            else:
                await interaction.response.send_message(
                    "There is no spawn role configured for this server.", ephemeral=True
                )
            return

        if role is None:
            await interaction.response.send_message(
                "Please provide a `role` to set, or use `remove: True` to clear the current one.",
                ephemeral=True,
            )
            return

        assert resolved_role is not None
        config.spawn_role = resolved_role.id
        await config.save(update_fields=("spawn_role",))
        await interaction.response.send_message(
            f"{settings.bot_name} will now alert <@&{resolved_role.id}> when "
            f"{settings.plural_collectible_name} spawn.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @spawnrole.autocomplete("role")
    async def spawnrole_autocomplete(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        current: str,
    ) -> list[app_commands.Choice[str]]:
        guild = interaction.guild
        if not guild:
            return []
        results: list[app_commands.Choice[str]] = []
        for r in guild.roles:
            if r.is_default():
                continue
            if current.lower() in r.name.lower():
                results.append(app_commands.Choice(name=r.name, value=str(r.id)))
            if len(results) >= 25:
                break
        return results

    @app_commands.command()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def toggletips(self, interaction: discord.Interaction["BallsDexBot"]):
        """
        Enable or disable rotating tips under spawns for this server.
        """
        config, _ = await GuildConfig.get_or_create(guild_id=interaction.guild_id)
        config.show_tips = not config.show_tips
        await config.save(update_fields=("show_tips",))
        status = "enabled" if config.show_tips else "disabled"
        await interaction.response.send_message(
            f"Spawn tips are now **{status}** for this server.",
            ephemeral=True,
        )