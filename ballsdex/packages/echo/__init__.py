from typing import TYPE_CHECKING
from .cog import EchoAdminCommand, EchoCog

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot


async def setup(bot: "BallsDexBot"):
    import discord
    import logging
    from ballsdex.settings import settings

    log = logging.getLogger("ballsdex.packages.echo")

    admin_cog = bot.get_cog("Admin")
    if admin_cog and admin_cog.__cog_app_commands_group__:
        group = admin_cog.__cog_app_commands_group__

        # Remove any previously registered echo command
        for cmd in list(group.commands):
            if isinstance(cmd, discord.app_commands.Command) and hasattr(cmd, "_is_echo"):
                group.remove_command(cmd.name)
                log.info("Removed existing echo command '%s' before re-adding", cmd.name)

        cmd = EchoAdminCommand(bot)
        group.add_command(cmd)
        log.info("Attached /admin echo to Admin cog group")
    else:
        log.warning(
            "Could not find Admin cog or its command group. "
            "/admin echo will NOT be registered. "
            "Ensure ballsdex.packages.admin is loaded before "
            "ballsdex.packages.echo in config.yml.",
        )

    await bot.add_cog(EchoCog(bot))

    try:
        await bot.tree.sync()
        for guild_id in settings.admin_guild_ids:
            await bot.tree.sync(guild=discord.Object(id=guild_id))
        log.info("Command tree synced after echo setup")
    except Exception:
        log.warning("Failed to sync command tree after echo setup", exc_info=True)
