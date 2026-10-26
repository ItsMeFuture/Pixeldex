from typing import TYPE_CHECKING

import discord
from discord import app_commands

from ballsdex.core.models import SpawnTip
from ballsdex.core.utils.paginator import Pages, TextPageSource
from ballsdex.settings import settings

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot


class Tips(app_commands.Group):
    """
    Manage rotating spawn tips.
    """

    def __init__(self):
        super().__init__(name="tips", description="Manage rotating spawn tips")

    @app_commands.command(name="list")
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    async def tips_list(self, interaction: discord.Interaction["BallsDexBot"]):
        tips = await SpawnTip.all().order_by("id")
        if not tips:
            await interaction.response.send_message("No tips found.", ephemeral=True)
            return

        text = ""
        for tip in tips:
            status = "🟢" if tip.enabled else "🔴"
            text += f"{status} `{tip.id}` — {tip.text}\n"

        source = TextPageSource(text)
        pages = Pages(source=source, interaction=interaction, compact=True)
        pages.remove_item(pages.stop_pages)
        await pages.start(ephemeral=True)

    @app_commands.command(name="add")
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    async def tips_add(self, interaction: discord.Interaction["BallsDexBot"], text: str):
        tip = await SpawnTip.create(text=text)
        await interaction.response.send_message(
            f"Tip added with ID `{tip.id}`.", ephemeral=True
        )

    @app_commands.command(name="remove")
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    async def tips_remove(self, interaction: discord.Interaction["BallsDexBot"], tip_id: int):
        tip = await SpawnTip.get_or_none(id=tip_id)
        if not tip:
            await interaction.response.send_message(
                f"Tip with ID `{tip_id}` not found.", ephemeral=True
            )
            return
        await tip.delete()
        await interaction.response.send_message(
            f"Tip `{tip_id}` removed.", ephemeral=True
        )

    @app_commands.command(name="toggle")
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    async def tips_toggle(self, interaction: discord.Interaction["BallsDexBot"], tip_id: int):
        tip = await SpawnTip.get_or_none(id=tip_id)
        if not tip:
            await interaction.response.send_message(
                f"Tip with ID `{tip_id}` not found.", ephemeral=True
            )
            return
        tip.enabled = not tip.enabled
        await tip.save(update_fields=("enabled",))
        status = "enabled" if tip.enabled else "disabled"
        await interaction.response.send_message(
            f"Tip `{tip_id}` is now **{status}**.", ephemeral=True
        )