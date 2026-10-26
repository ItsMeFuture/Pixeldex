import enum
import logging
from typing import TYPE_CHECKING, cast

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Button, Modal, TextInput, View, button
from tortoise.exceptions import DoesNotExist
from tortoise.expressions import Q, RawSQL
from tortoise.functions import Count

from collections import defaultdict

from ballsdex.core.models import (
    Ball,
    BallInstance,
    DonationPolicy,
    Player,
    Special,
    Trade,
    TradeObject,
    balls,
)
from ballsdex.core.utils.buttons import ConfirmChoiceView
from ballsdex.core.utils.paginator import FieldPageSource, Pages
from ballsdex.core.utils.sorting import FilteringChoices, SortingChoices, filter_balls, sort_balls
from ballsdex.core.utils.transformers import (
    BallEnabledTransform,
    BallInstanceTransform,
    SpecialEnabledTransform,
    TradeCommandType,
)
from ballsdex.core.utils.utils import can_mention, inventory_privacy, is_staff
from ballsdex.packages.balls.countryballs_paginator import CountryballsViewer, DuplicateViewMenu
from ballsdex.settings import settings

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger("ballsdex.packages.countryballs")

# Rarity & Details update
GROUPS_PER_PAGE = 7

CONFIG = {
    "Embed Color": discord.Color(0xFFFFFF),
    "Line Symbol": "⋄"
}


def _ball_emoji(bot: "BallsDexBot", ball: Ball) -> str:
    if ball.emoji_id:
        emoji = bot.get_emoji(ball.emoji_id)
        if emoji:
            return str(emoji)
    return "⋄"

class RaritySkipModal(Modal, title="Go to page"):
    query = TextInput(
        label="Page or Ball Name",
        placeholder="Enter a page number or ball name",
        min_length=1,
        max_length=100,
    )

    async def on_submit(self, interaction: discord.Interaction["BallsDexBot"]) -> None:
        self.interaction = interaction
        self.stop()


class RarityPages(Pages):
    def __init__(
        self,
        source,
        *,
        interaction: discord.Interaction["BallsDexBot"],
        balls_lookup: dict[str, int],
        check_embeds: bool = False,
        compact: bool = False,
    ):
        self.balls_lookup = balls_lookup
        super().__init__(
            source,
            interaction=interaction,
            check_embeds=check_embeds,
            compact=compact,
        )

    @discord.ui.button(label="Skip to page...", style=discord.ButtonStyle.grey)
    async def numbered_page(
        self, interaction: discord.Interaction["BallsDexBot"], button: discord.ui.Button
    ):
        modal = RaritySkipModal()
        await interaction.response.send_modal(modal)
        timed_out = await modal.wait()

        if timed_out:
            await interaction.followup.send("Took too long", ephemeral=True)
            return
        elif self.is_finished():
            await modal.interaction.response.send_message("Took too long", ephemeral=True)
            return

        value = str(modal.query.value).strip()

        # Try page number first
        if value.isdigit():
            await self.show_checked_page(modal.interaction, int(value) - 1)
            if not modal.interaction.response.is_done():
                await modal.interaction.response.send_message(
                    "Invalid page number.", ephemeral=True
                )
            return

        # Try exact ball name, then partial
        page_num = self.balls_lookup.get(value.lower())
        if page_num is None:
            for name, pg in self.balls_lookup.items():
                if value.lower() in name:
                    page_num = pg
                    break

        if page_num is not None:
            await self.show_page(modal.interaction, page_num)
        else:
            await modal.interaction.response.send_message(
                f"Could not find a page or ball matching `{value}`.", ephemeral=True
            )


class DonationRequest(View):
    def __init__(
        self,
        bot: "BallsDexBot",
        interaction: discord.Interaction["BallsDexBot"],
        countryball: BallInstance,
        new_player: Player,
    ):
        super().__init__(timeout=120)
        self.bot = bot
        self.original_interaction = interaction
        self.countryball = countryball
        self.new_player = new_player

    async def interaction_check(self, interaction: discord.Interaction["BallsDexBot"], /) -> bool:
        if interaction.user.id != self.new_player.discord_id:
            await interaction.response.send_message(
                "You are not allowed to interact with this menu.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True  # type: ignore
        try:
            await self.original_interaction.followup.edit_message(
                "@original", view=self  # type: ignore
            )
        except discord.NotFound:
            pass
        await self.countryball.unlock()

    @button(
        style=discord.ButtonStyle.success, emoji="\N{HEAVY CHECK MARK}\N{VARIATION SELECTOR-16}"
    )
    async def accept(self, interaction: discord.Interaction["BallsDexBot"], button: Button):
        self.stop()
        for item in self.children:
            item.disabled = True  # type: ignore
        self.countryball.favorite = False
        self.countryball.trade_player = self.countryball.player
        self.countryball.player = self.new_player
        await self.countryball.save()
        trade = await Trade.create(player1=self.countryball.trade_player, player2=self.new_player)
        await TradeObject.create(
            trade=trade, ballinstance=self.countryball, player=self.countryball.trade_player
        )
        await interaction.response.edit_message(
            content=interaction.message.content  # type: ignore
            + "\n\N{WHITE HEAVY CHECK MARK} The donation was accepted!",
            view=self,
        )
        await self.countryball.unlock()

    @button(
        style=discord.ButtonStyle.danger,
        emoji="\N{HEAVY MULTIPLICATION X}\N{VARIATION SELECTOR-16}",
    )
    async def deny(self, interaction: discord.Interaction["BallsDexBot"], button: Button):
        self.stop()
        for item in self.children:
            item.disabled = True  # type: ignore
        await interaction.response.edit_message(
            content=interaction.message.content  # type: ignore
            + "\n\N{CROSS MARK} The donation was denied.",
            view=self,
        )
        await self.countryball.unlock()


class DuplicateType(enum.StrEnum):
    countryballs = settings.plural_collectible_name
    specials = "specials"


class Balls(commands.GroupCog, group_name=settings.players_group_cog_name):
    """
    View and manage your countryballs collection.
    """

    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot

    @app_commands.command()
    @app_commands.checks.cooldown(1, 10, key=lambda i: i.user.id)
    async def vault(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        user: discord.User | None = None,
        sort: SortingChoices | None = None,
        reverse: bool = False,
        countryball: BallEnabledTransform | None = None,
        special: SpecialEnabledTransform | None = None,
        filter: FilteringChoices | None = None,
    ):
        """
        Show your vault of pixelballs.

        Parameters
        ----------
        user: discord.User
            The user whose vault you want to view, if not yours.
        sort: SortingChoices
            Choose how countryballs are sorted. Can be used to show duplicates.
        reverse: bool
            Reverse the output of the list.
        countryball: Ball
            Filter the list by a specific countryball.
        special: Special
            Filter the list by a specific special event.
        filter: FilteringChoices
            Filter the list by a specific filter.
        """
        user_obj = user or interaction.user
        await interaction.response.defer(thinking=True)

        try:
            player = await Player.get(discord_id=user_obj.id)
        except DoesNotExist:
            if user_obj == interaction.user:
                await interaction.followup.send(
                    f"You don't have any {settings.plural_collectible_name} yet."
                )
            else:
                await interaction.followup.send(
                    f"{user_obj.name} doesn't have any {settings.plural_collectible_name} yet."
                )
            return
        if user is not None:
            if user.id in self.bot.blacklist:
                await interaction.followup.send(
                    "You cannot view the vault of a blacklisted user.", ephemeral=True
                )
                return
            if await inventory_privacy(self.bot, interaction, player, user_obj) is False:
                return

        interaction_player, _ = await Player.get_or_create(discord_id=interaction.user.id)

        blocked = await player.is_blocked(interaction_player)
        if blocked and not is_staff(interaction):
            await interaction.followup.send(
                "You cannot view the vault of a user that has you blocked.", ephemeral=True
            )
            return

        query = BallInstance.filter(player=player).prefetch_related("ball", "special")
        if filter:
            query = filter_balls(filter, query, interaction.guild_id)
        if countryball:
            query = query.filter(ball=countryball)
        if special:
            query = query.filter(special=special)
        if sort:
            query = sort_balls(sort, query)
        else:
            query = query.order_by("-favorite")
        countryballs = cast(list[int], await query.values_list("id", flat=True))

        if len(countryballs) < 1:
            ball_txt = countryball.country if countryball else ""
            special_txt = special if special else ""

            if special_txt and ball_txt:
                combined = f"{special_txt} {ball_txt}"
            elif special_txt:
                combined = special_txt
            elif ball_txt:
                combined = ball_txt
            else:
                combined = ""

            if user_obj == interaction.user:
                await interaction.followup.send(
                    f"You don't have any {combined} {settings.plural_collectible_name} yet."
                )
            else:
                await interaction.followup.send(
                    f"{user_obj.name} doesn't have any {combined} "
                    f"{settings.plural_collectible_name} yet."
                )
            return
        if reverse:
            countryballs.reverse()

        paginator = CountryballsViewer(interaction, countryballs)
        if user_obj == interaction.user:
            await paginator.start()
        else:
            await paginator.start(
                content=f"Viewing {user_obj.name}'s {settings.plural_collectible_name}"
            )

    @app_commands.command()
    @app_commands.checks.cooldown(1, 20, key=lambda i: i.user.id)
    async def collection(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        user: discord.User | None = None,
        special: SpecialEnabledTransform | None = None,
        filter: FilteringChoices | None = None,
    ):
        """
        Show your current collection of pixelballs in the bot.

        Parameters
        ----------
        user: discord.User
            The user whose collection you want to view, if not yours.
        special: Special
            The special you want to see the collection of
        filter: FilteringChoices
            Filter the list by a specific filter.
        """
        user_obj = user or interaction.user
        await interaction.response.defer(thinking=True)
        extra_text = f"{special.name} " if special else ""
        if user is not None:
            try:
                player = await Player.get(discord_id=user_obj.id)
            except DoesNotExist:
                await interaction.followup.send(
                    f"{user_obj.name} doesn't have any "
                    f"{extra_text}{settings.plural_collectible_name} yet."
                )
                return
            if user.id in self.bot.blacklist:
                await interaction.followup.send(
                    "You cannot view the collection of a blacklisted user.", ephemeral=True
                )
                return

            interaction_player, _ = await Player.get_or_create(discord_id=interaction.user.id)

            blocked = await player.is_blocked(interaction_player)
            if blocked and not is_staff(interaction):
                await interaction.followup.send(
                    "You cannot view the collection of a user that has blocked you.",
                    ephemeral=True,
                )
                return

            if await inventory_privacy(self.bot, interaction, player, user_obj) is False:
                return
        # Filter disabled balls, they do not count towards progression
        # Only ID and emoji is interesting for us
        bot_countryballs = {x: y.emoji_id for x, y in balls.items() if y.enabled and x not in settings.hidden_collectible_ids}

        # Set of ball IDs owned by the player
        filters = {"player__discord_id": user_obj.id, "ball__enabled": True}
        if special:
            filters["special"] = special
            bot_countryballs = {
                x: y.emoji_id
                for x, y in balls.items()
                if y.enabled
                and x not in settings.hidden_collectible_ids
                and (special.end_date is None or y.created_at < special.end_date)
            }
        if filter:
            query = filter_balls(filter, BallInstance.filter(**filters))
        else:
            query = BallInstance.filter(**filters)

        if not bot_countryballs:
            await interaction.followup.send(
                f"There are no {extra_text}{settings.plural_collectible_name}"
                " registered on this bot yet.",
                ephemeral=True,
            )
            return

        owned_countryballs = set(
            x[0] for x in await query.distinct().values_list("ball_id")
        )

        entries: list[tuple[str, str]] = []

        def fill_fields(title: str, emoji_ids: set[int]):
            first_field_added = False
            buffer = ""

            for emoji_id in emoji_ids:
                emoji = self.bot.get_emoji(emoji_id)
                if not emoji:
                    continue

                text = f"{emoji} "
                if len(buffer) + len(text) > 1024:
                    if first_field_added:
                        entries.append(("\u200b", buffer))
                    else:
                        entries.append((f"__**{title}**__", buffer))
                        first_field_added = True
                    buffer = ""
                buffer += text

            if buffer:
                if first_field_added:
                    entries.append(("\u200b", buffer))
                else:
                    entries.append((f"__**{title}**__", buffer))

        if owned_countryballs:
            fill_fields(
                f"Owned {settings.plural_collectible_name}",
                set(bot_countryballs[x] for x in owned_countryballs if x not in settings.hidden_collectible_ids),
            )
        else:
            entries.append((f"__**Owned {settings.plural_collectible_name}**__", "Nothing yet."))

        if missing := set(y for x, y in bot_countryballs.items() if x not in owned_countryballs):
            fill_fields(f"Missing {settings.plural_collectible_name}", missing)
        else:
            entries.append(
                (
                    f"__**:tada: No missing {settings.plural_collectible_name}, "
                    "congratulations! :tada:**__",
                    "\u200b",
                )
            )

        source = FieldPageSource(entries, per_page=5, inline=False, clear_description=False)
        special_str = f" ({special.name})" if special else ""
        original_catcher_string = " " + filter.value.replace("_", " ") + " " if filter else ""
        source.embed.description = (
            f"{settings.bot_name}{original_catcher_string}{special_str} progression: "
            f"**{round(len(owned_countryballs) / len(bot_countryballs) * 100, 1)}%**"
        )
        source.embed.colour = discord.Color(0xFFFFFF)
        source.embed.set_author(name=user_obj.display_name, icon_url=user_obj.display_avatar.url)

        pages = Pages(source=source, interaction=interaction, compact=True)
        await pages.start()

    @app_commands.command()
    @app_commands.checks.cooldown(1, 5, key=lambda i: i.user.id)
    async def card(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        countryball: BallInstanceTransform,
        special: SpecialEnabledTransform | None = None,
    ):
        """
        Display the card of a pixelball from your vault.

        Parameters
        ----------
        countryball: BallInstance
            The countryball you want to inspect
        special: Special
            Filter the results of autocompletion to a special event. Ignored afterwards.
        """
        if not countryball:
            return
        await interaction.response.defer(thinking=True)
        content, file, view = await countryball.prepare_for_message(interaction)
        await interaction.followup.send(content=content, file=file, view=view)
        file.close()

    @app_commands.command()
    @app_commands.checks.cooldown(1, 5, key=lambda i: i.user.id)
    async def last(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        user: discord.User | None = None,
        filter: FilteringChoices | None = None,
    ):
        """
        Display info of your or another users last caught countryball.

        Parameters
        ----------
        user: discord.Member
            The user you would like to see
        filter: FilteringChoices
            Filter the last caught countryball by a specific filter.
            Only works if the user has caught at least one countryball.
        """
        user_obj = user if user else interaction.user
        await interaction.response.defer(thinking=True)
        try:
            player = await Player.get(discord_id=user_obj.id)
        except DoesNotExist:
            msg = f"{'You do' if user is None else f'{user_obj.display_name} does'}"
            await interaction.followup.send(
                f"{msg} not have any {settings.plural_collectible_name} yet.",
                ephemeral=True,
            )
            return

        if user is not None:
            if user.id in self.bot.blacklist:
                await interaction.followup.send(
                    (
                        "You cannot view the last caught "
                        f"{settings.collectible_name} of a blacklisted user."
                    ),
                    ephemeral=True,
                )
                return
            if await inventory_privacy(self.bot, interaction, player, user_obj) is False:
                return

        interaction_player, _ = await Player.get_or_create(discord_id=interaction.user.id)

        blocked = await player.is_blocked(interaction_player)
        if blocked and not is_staff(interaction):
            await interaction.followup.send(
                f"You cannot view the last caught {settings.collectible_name} "
                "of a user that has blocked you.",
                ephemeral=True,
            )
            return
        query = player.balls.all()
        filter_msg = ""
        if filter:
            filter_msg = f" with the `{filter.value.replace('_', ' ')}` filter"
            query = filter_balls(filter, query, interaction.guild_id)
        countryball = await query.order_by("-id").first()
        if not countryball:
            msg = f"{'You do' if user is None else f'{user_obj.display_name} does'}"
            await interaction.followup.send(
                f"{msg} not have any {settings.plural_collectible_name} yet.",
                ephemeral=True,
            )
            return

        content, file, view = await countryball.prepare_for_message(interaction)
        if user is not None and user.id != interaction.user.id:
            content = (
                f"You are viewing {user.display_name}'s last caught {settings.collectible_name}"
                f"{filter_msg}.\n" + content
            )
        else:
            content = (
                f"You are viewing your last caught {settings.collectible_name}{filter_msg}.\n"
                + content
            )
        await interaction.followup.send(content=content, file=file, view=view)
        file.close()

    @app_commands.command()
    async def favorite(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        countryball: BallInstanceTransform,
        special: SpecialEnabledTransform | None = None,
    ):
        """
        Set favorite countryballs.

        Parameters
        ----------
        countryball: BallInstance
            The countryball you want to set/unset as favorite
        special: Special
            Filter the results of autocompletion to a special event. Ignored afterwards.
        """
        if not countryball:
            return

        if settings.max_favorites == 0:
            await interaction.response.send_message(
                f"You cannot set favorite {settings.plural_collectible_name} in this bot."
            )
            return

        if not countryball.favorite:
            try:
                player = await Player.get(discord_id=interaction.user.id).prefetch_related("balls")
            except DoesNotExist:
                await interaction.response.send_message(
                    f"You don't have any {settings.plural_collectible_name} yet.", ephemeral=True
                )
                return

            grammar = (
                f"{settings.collectible_name}"
                if settings.max_favorites == 1
                else f"{settings.plural_collectible_name}"
            )
            if await player.balls.filter(favorite=True).count() >= settings.max_favorites:
                await interaction.response.send_message(
                    f"You cannot set more than {settings.max_favorites} favorite {grammar}.",
                    ephemeral=True,
                )
                return

            countryball.favorite = True  # type: ignore
            await countryball.save()
            emoji = self.bot.get_emoji(countryball.countryball.emoji_id) or ""
            await interaction.response.send_message(
                f"{emoji} `#{countryball.pk:0X}` {countryball.countryball.country} "
                f"is now a favorite {settings.collectible_name}!",
                ephemeral=True,
            )

        else:
            countryball.favorite = False  # type: ignore
            await countryball.save()
            emoji = self.bot.get_emoji(countryball.countryball.emoji_id) or ""
            await interaction.response.send_message(
                f"{emoji} `#{countryball.pk:0X}` {countryball.countryball.country} "
                f"isn't a favorite {settings.collectible_name} anymore.",
                ephemeral=True,
            )

    @app_commands.command(extras={"trade": TradeCommandType.PICK})
    async def gift(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        user: discord.User,
        countryball: BallInstanceTransform,
        special: SpecialEnabledTransform | None = None,
    ):
        """
        Gift a countryball to a user.

        Parameters
        ----------
        user: discord.User
            The user you want to give a countryball to
        countryball: BallInstance
            The countryball you're giving away
        special: Special
            Filter the results of autocompletion to a special event. Ignored afterwards.
        """
        if not countryball:
            return
        if not countryball.is_tradeable:
            await interaction.response.send_message(
                f"You cannot donate this {settings.collectible_name}.", ephemeral=True
            )
            return
        if user.bot:
            await interaction.response.send_message("You cannot donate to bots.", ephemeral=True)
            return
        if await countryball.is_locked():
            await interaction.response.send_message(
                f"This {settings.collectible_name} is currently locked for a trade. "
                "Please try again later.",
                ephemeral=True,
            )
            return
        favorite = countryball.favorite
        if favorite:
            view = ConfirmChoiceView(
                interaction,
                accept_message=f"{settings.collectible_name.title()} donated.",
                cancel_message="This request has been cancelled.",
            )
            await interaction.response.send_message(
                f"This {settings.collectible_name} is a favorite, "
                "are you sure you want to donate it?",
                view=view,
                ephemeral=True,
            )
            await view.wait()
            if not view.value:
                return
            interaction = view.interaction_response
        else:
            await interaction.response.defer()
        await countryball.lock_for_trade()
        new_player, _ = await Player.get_or_create(discord_id=user.id)
        old_player = countryball.player

        if new_player == old_player:
            await interaction.followup.send(
                f"You cannot gift a {settings.collectible_name} to yourself.", ephemeral=True
            )
            await countryball.unlock()
            return
        if new_player.donation_policy == DonationPolicy.ALWAYS_DENY:
            await interaction.followup.send(
                "This player does not accept donations. You can use trades instead.",
                ephemeral=True,
            )
            await countryball.unlock()
            return

        friendship = await new_player.is_friend(old_player)
        if new_player.donation_policy == DonationPolicy.FRIENDS_ONLY:
            if not friendship:
                await interaction.followup.send(
                    "This player only accepts donations from friends, use trades instead.",
                    ephemeral=True,
                )
                await countryball.unlock()
                return
        blocked = await new_player.is_blocked(old_player)
        if blocked:
            await interaction.followup.send(
                "You cannot interact with a user that has blocked you.", ephemeral=True
            )
            await countryball.unlock()
            return
        if new_player.discord_id in self.bot.blacklist:
            await interaction.followup.send(
                "You cannot donate to a blacklisted user.", ephemeral=True
            )
            await countryball.unlock()
            return
        elif new_player.donation_policy == DonationPolicy.REQUEST_APPROVAL:
            await interaction.followup.send(
                f"Hey {user.mention}, {interaction.user.name} wants to give you "
                f"{countryball.description(include_emoji=True, bot=self.bot, is_trade=True)}!\n"
                "Do you accept this donation?",
                view=DonationRequest(self.bot, interaction, countryball, new_player),
                allowed_mentions=await can_mention([new_player, old_player]),
            )
            return

        countryball.player = new_player
        countryball.trade_player = old_player
        countryball.favorite = False
        await countryball.save()

        trade = await Trade.create(player1=old_player, player2=new_player)
        await TradeObject.create(trade=trade, ballinstance=countryball, player=old_player)

        cb_txt = (
            countryball.description(short=True, include_emoji=True, bot=self.bot, is_trade=True)
            + f" (`{countryball.attack_bonus:+}%/{countryball.health_bonus:+}%`)"
        )
        if favorite:
            await interaction.followup.send(
                f"{interaction.user.mention}, you just gave the "
                f"{settings.collectible_name} {cb_txt} to {user.mention}!",
                allowed_mentions=await can_mention([new_player, old_player]),
            )
        else:
            await interaction.followup.send(
                f"You just gave the {settings.collectible_name} {cb_txt} to {user.mention}!",
                allowed_mentions=await can_mention([new_player]),
            )
        await countryball.unlock()

    @app_commands.command()
    async def count(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        countryball: BallEnabledTransform | None = None,
        special: SpecialEnabledTransform | None = None,
        current_server: bool = False,
    ):
        """
        Count how many countryballs you have.

        Parameters
        ----------
        countryball: Ball
            The countryball you want to count
        special: Special
            The special you want to count
        current_server: bool
            Only count countryballs caught in the current server
        """
        if interaction.response.is_done():
            return

        assert interaction.guild
        filters = {}
        if countryball:
            filters["ball"] = countryball
        if special:
            filters["special"] = special
        if current_server:
            filters["server_id"] = interaction.guild.id
        filters["player__discord_id"] = interaction.user.id

        await interaction.response.defer(ephemeral=True, thinking=True)

        balls = await BallInstance.filter(**filters).count()
        country = f"{countryball.country} " if countryball else ""
        plural = "s" if balls > 1 or balls == 0 else ""
        special_str = f"{special.name} " if special else ""
        guild = f" caught in {interaction.guild.name}" if current_server else ""

        await interaction.followup.send(
            f"You have {balls:,} {special_str}"
            f"{country}{settings.collectible_name}{plural}{guild}."
        )

    @app_commands.command()
    @app_commands.checks.cooldown(1, 20, key=lambda i: i.user.id)
    async def duplicate(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        type: DuplicateType,
        limit: int | None = None,
    ):
        """
        Shows your most duplicated countryballs or specials.

        Parameters
        ----------
        type: DuplicateType
            Type of duplicate to check (countryballs or specials).
        limit: int | None
            The amount of countryballs to show, can only be used with `countryballs`.
        """
        await interaction.response.defer(thinking=True, ephemeral=True)

        player, _ = await Player.get_or_create(discord_id=interaction.user.id)
        is_special = type == DuplicateType.specials
        queryset = BallInstance.filter(player=player)

        if is_special:
            queryset = queryset.filter(special_id__isnull=False).prefetch_related("special")
            annotations = {"name": "special__name", "emoji": "special__emoji"}
            apply_limit = False
        else:
            queryset = queryset.filter(ball__tradeable=True)
            annotations = {"name": "ball__country", "emoji": "ball__emoji_id"}
            apply_limit = True

        query = (
            queryset.annotate(count=Count("id")).group_by(*annotations.values()).order_by("-count")
        )

        if apply_limit and limit is not None:
            query = query.limit(limit)

        query = query.values(*annotations.values(), "count")
        results = await query

        if not results:
            await interaction.followup.send(
                f"You don't have any {type.value} duplicates in your inventory.", ephemeral=True
            )
            return

        entries = [
            {
                "name": item[annotations["name"]],
                "emoji": (
                    self.bot.get_emoji(item[annotations["emoji"]]) or item[annotations["emoji"]]
                ),
                "count": item["count"],
            }
            for item in results
        ]

        source = DuplicateViewMenu(interaction, entries, is_special)
        await source.start(content=f"View your duplicate {type.value}.")

    @app_commands.command()
    @app_commands.checks.cooldown(1, 20, key=lambda i: i.user.id)
    async def compare(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        user: discord.User,
        special: SpecialEnabledTransform | None = None,
    ):
        """
        Compare your countryballs with another user.

        Parameters
        ----------
        user: discord.User
            The user you want to compare with
        special: Special
            Filter the results of the comparison to a special event.
        """
        await interaction.response.defer(thinking=True)
        if interaction.user == user:
            await interaction.followup.send("You cannot compare with yourself.", ephemeral=True)
            return

        try:
            player = await Player.get(discord_id=user.id)
        except DoesNotExist:
            await interaction.followup.send(
                f"{user.display_name} doesn't have any {settings.plural_collectible_name} yet."
            )
            return

        if user.id in self.bot.blacklist:
            await interaction.followup.send(
                "You cannot compare the inventory of a blacklisted user.", ephemeral=True
            )
            return

        if await inventory_privacy(self.bot, interaction, player, user) is False:
            return

        bot_countryballs = {x: y.emoji_id for x, y in balls.items() if y.enabled and x not in settings.hidden_collectible_ids}
        if special:
            bot_countryballs = {
                x: y.emoji_id
                for x, y in balls.items()
                if y.enabled
                and x not in settings.hidden_collectible_ids
                and (special.end_date is None or y.created_at < special.end_date)
            }

        player1, _ = await Player.get_or_create(discord_id=interaction.user.id)
        player2, _ = await Player.get_or_create(discord_id=user.id)

        blocked = await player.is_blocked(player1)
        if blocked and not is_staff(interaction):
            await interaction.followup.send(
                "You cannot compare with a user that has you blocked.", ephemeral=True
            )
            return

        blocked = await player.is_blocked(player2)
        if blocked and not is_staff(interaction):
            await interaction.followup.send(
                "You cannot compare with a user that has you blocked.", ephemeral=True
            )
            return
        queryset = BallInstance.filter(ball__enabled=True).distinct()
        if special:
            queryset = queryset.filter(special=special)
        user1_balls = cast(
            list[int],
            await queryset.filter(player=player1).values_list("ball_id", flat=True),
        )
        user2_balls = cast(
            list[int],
            await queryset.filter(player=player2).values_list("ball_id", flat=True),
        )
        both = set(user1_balls) & set(user2_balls)
        user1_only = set(user1_balls) - set(user2_balls)
        user2_only = set(user2_balls) - set(user1_balls)
        neither = set(bot_countryballs.keys()) - both - user1_only - user2_only

        entries = []

        def fill_fields(title: str, ids: set[int]):
            first_field_added = False
            buffer = ""

            for ball_id in ids:
                emoji = self.bot.get_emoji(bot_countryballs[ball_id])
                if not emoji:
                    continue

                text = f"{emoji} "
                if len(buffer) + len(text) > 1024:
                    if first_field_added:
                        entries.append(("\u200b", buffer))
                    else:
                        entries.append((f"__**{title}**__", buffer))
                        first_field_added = True
                    buffer = ""
                buffer += text

            if buffer:
                if first_field_added:
                    entries.append(("\u200b", buffer))
                else:
                    entries.append((f"__**{title}**__", buffer))

        if both:
            fill_fields("Both have", both - set(settings.hidden_collectible_ids))
        else:
            entries.append(("__**Both have**__", "None"))
        fill_fields(f"{interaction.user.display_name} has", user1_only - set(settings.hidden_collectible_ids))
        fill_fields(f"{user.display_name} has", user2_only - set(settings.hidden_collectible_ids))
        fill_fields("Neither have", neither - set(settings.hidden_collectible_ids))

        source = FieldPageSource(entries, per_page=5, inline=False, clear_description=False)
        special_str = f" ({special.name})" if special else ""
        source.embed.title = (
            f"Comparison of {interaction.user.display_name} and {user.display_name}'s "
            f"{settings.plural_collectible_name}{special_str}"
        )
        source.embed.colour = discord.Color(0xFFFFFF)

        pages = Pages(source=source, interaction=interaction, compact=True)
        await pages.start()

    @app_commands.command()
    async def supply(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        countryball: BallEnabledTransform | None = None,
        ephemeral: bool = False,
    ):
        """
        Show your supply of a specific countryball.

        Parameters
        ----------
        countryball: Ball
            The countryball you want to see the supply of
        ephemeral: bool
            Whether or not to send the command ephemerally.
        """
        await interaction.response.defer(thinking=True, ephemeral=ephemeral)
        player, _ = await Player.get_or_create(discord_id=interaction.user.id)

        # Fix: use Q filter to include balls with no special (special_id IS NULL)
        # .exclude(special__hidden=True) causes an INNER JOIN that drops NULL special_id rows
        query = (
            BallInstance.filter(player=player)
            .filter(Q(special_id__isnull=True) | Q(special__hidden=False))
            .annotate(
                total=RawSQL("COUNT(*)"),
                traded=RawSQL("SUM(CASE WHEN trade_player_id IS NULL THEN 0 ELSE 1 END)"),
                specials=RawSQL("SUM(CASE WHEN special_id IS NULL THEN 0 ELSE 1 END)"),
            )
            .group_by("player_id")
        )
        specials = (
            BallInstance.filter(player=player)
            .filter(Q(special_id__isnull=False) & Q(special__hidden=False))
            .annotate(count=Count("id"))
            .group_by("special__name")
        )
        if countryball:
            query = query.filter(ball=countryball)
            specials = specials.filter(ball=countryball)
        counts_list = await query.values("player_id", "total", "traded", "specials")
        specials = await specials.values("special__name", "count")

        if not counts_list:
            if countryball:
                await interaction.followup.send(
                    f"You don't have any {countryball.country} "
                    f"{settings.plural_collectible_name} yet."
                )
            else:
                await interaction.followup.send(
                    f"You don't have any {settings.plural_collectible_name} yet."
                )
            return
        counts = counts_list[0]
        all_specials = await Special.filter(hidden=False)
        special_emojis = {x.name: x.emoji for x in all_specials}

        desc = (
            f"**Total**: {counts['total']:,} ({counts['total'] - counts['traded']:,} caught, "
            f"{counts['traded']:,} received from trade)\n"
            f"**Total Specials**: {counts['specials']:,}\n\n"
        )
        if counts["specials"]:
            desc += "**Specials**:\n"
        for special in sorted(specials, key=lambda x: x["count"], reverse=True):
            emoji = special_emojis.get(special["special__name"], "")
            desc += f"{emoji} {special['special__name']}: {special['count']:,}\n"

        embed = discord.Embed(
            title=f"Supply of {countryball.country}" if countryball else "Total Supply",
            description=desc,
            color=discord.Color(0xFFFFFF),
        )
        embed.set_author(
            name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url
        )
        if countryball:
            file_location = "./admin_panel/media/" + countryball.wild_card
            file = discord.File(file_location, filename="countryball.png")
            embed.set_thumbnail(url="attachment://countryball.png")
            await interaction.followup.send(embed=embed, file=file)
        else:
            await interaction.followup.send(embed=embed)

    @app_commands.command(description="Check the rarity list of the bot")
    @app_commands.describe(
        search=f"Search a specific {settings.collectible_name}'s rarity",
        reverse="Reverse the output of the rarity list",
        ephemeral="Whether or not to send the command ephemerally.",
    )
    async def rarity(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        search: str | None = None,
        reverse: bool = False,
        ephemeral: bool = False,
    ):
        name = settings.collectible_name.capitalize()
        plural = settings.plural_collectible_name.capitalize()
        bot_name = settings.bot_name

        balls_list: list[Ball] = [
            b for b in await Ball.all()
            if b.enabled and b.pk not in settings.hidden_collectible_ids
        ]

        if not balls_list:
            await interaction.response.send_message(
                f"No {settings.plural_collectible_name} are currently enabled.",
                ephemeral=True,
            )
            return

        # ── Search mode ───────────────────────────────────────────────────────
        if search:
            try:
                rarity_value = float(search.replace(",", "."))
                matches = [b for b in balls_list if float(b.rarity) == rarity_value]

                if not matches:
                    await interaction.response.send_message(
                        f"There are no {settings.collectible_name} "
                        f"with rarity `{search}`.",
                        ephemeral=True,
                    )
                    return

                lines = [f"{_ball_emoji(self.bot, b)} {b.country}" for b in matches]
                await interaction.response.send_message(
                    f"{plural} with rarity `{search}`:\n" + "\n".join(lines),
                    ephemeral=True,
                )
                return
            except ValueError:
                pass

            match = next(
                (b for b in balls_list if b.country.lower() == search.lower()), None
            )
            if not match:
                match = next(
                    (b for b in balls_list if search.lower() in b.country.lower()), None
                )
            if not match:
                await interaction.response.send_message(
                    f"No {settings.collectible_name} found matching `{search}`.",
                    ephemeral=True,
                )
                return

            emoji = _ball_emoji(self.bot, match)
            await interaction.response.send_message(
                f"{emoji} **{match.country}**\nRarity: `{match.rarity}`",
                ephemeral=True,
            )
            return

        # ── Full paginated list ───────────────────────────────────────────────
        await interaction.response.defer(ephemeral=ephemeral)

        rarity_map: dict[float, list[Ball]] = defaultdict(list)
        for b in balls_list:
            rarity_map[float(b.rarity)].append(b)

        sorted_rarities = sorted(rarity_map.keys(), reverse=reverse)

        entries: list[tuple[str, str]] = []
        balls_lookup: dict[str, int] = {}
        for idx, r in enumerate(sorted_rarities):
            group_balls = rarity_map[r]
            lines = [f"⋄ {_ball_emoji(self.bot, b)} {b.country}" for b in group_balls]
            entries.append((f"∥ Rarity: {r}", "\n".join(lines)))
            page_num = idx // GROUPS_PER_PAGE
            for b in group_balls:
                balls_lookup[b.country.lower()] = page_num

        source = FieldPageSource(entries, per_page=GROUPS_PER_PAGE, inline=False)
        source.embed.title = f"{bot_name} Rarity List"
        source.embed.colour = discord.Color(0xFFFFFF)

        pages = RarityPages(
            source, interaction=interaction, balls_lookup=balls_lookup
        )
        await pages.start(ephemeral=ephemeral)

    @rarity.autocomplete("search")
    async def rarity_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        balls_list: list[Ball] = await Ball.all()
        results: list[app_commands.Choice[str]] = []
        for b in balls_list:
            if not b.enabled or b.pk in settings.hidden_collectible_ids:
                continue
            if current.lower() in b.country.lower():
                results.append(app_commands.Choice(name=b.country, value=b.country))
            if len(results) >= 25:
                break
        return results

    @app_commands.command()
    async def details(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        countryball: BallEnabledTransform,
    ):
        """
        Display details of a specific countryball.

        Parameters
        ----------
        countryball: Ball
            The countryball you want to inspect.
        """
        await countryball.fetch_related("regime", "economy")

        emoji = interaction.client.get_emoji(countryball.emoji_id) or ""

        embed = discord.Embed(
            title=f"{emoji} {countryball.country} Information:",
            description=(
                f"⋄ **Short Name:** {countryball.short_name}\n"
                f"⋄ **Catch Names:** {countryball.catch_names}\n"
                f"⋄ **Regime:** {countryball.regime}\n"
                f"⋄ **Economy:** {countryball.economy}\n"
                f"⋄ **Rarity:** {countryball.rarity}\n"
                f"⋄ **Attack:** {countryball.attack}\n"
                f"⋄ **Health:** {countryball.health}\n"
                f"⋄ **Capacity Name:** {countryball.capacity_name}\n"
                f"⋄ **Capacity Description:** {countryball.capacity_description}\n"
                f"⋄ **Image Credits:** {countryball.credits}\n"
            ).replace("⋄", CONFIG["Line Symbol"]),
            color=CONFIG["Embed Color"]
        )

        await interaction.response.send_message(embed=embed)