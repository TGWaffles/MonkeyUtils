import asyncio

from discord.ext import commands

from main import UtilsBot
from src.checks.user_check import is_owner
from src.checks.role_check import is_high_staff


class CommandManager(commands.Cog):
    def __init__(self, bot: UtilsBot):
        self.bot = bot

    @commands.command()
    @is_owner()
    async def disable(self, ctx, command_name):
        command: commands.Command = self.bot.get_command(command_name)
        if command is None:
            await ctx.reply(embed=self.bot.create_error_embed("Couldn't find a command with that name."))
            return
        if not command.enabled:
            await ctx.reply(embed=self.bot.create_error_embed("Command already disabled."))
            return
        command.update(enabled=False)
        await ctx.reply(embed=self.bot.create_completed_embed("Disabled.", "Command {} disabled!".format(command_name)))

    @commands.command()
    @is_owner()
    async def enable(self, ctx, command_name):
        command: commands.Command = self.bot.get_command(command_name)
        if command is None:
            await ctx.reply(embed=self.bot.create_error_embed("Couldn't find a command with that name."))
        if command.enabled:
            await ctx.reply(embed=self.bot.create_error_embed("Command already enabled."))
            return
        cog = command.cog
        command.update(enabled=True)
        try:
            await command.callback(cog, ctx)
        except TypeError:
            pass
        enabling_msg = await ctx.reply(embed=self.bot.create_processing_embed("Enabling...",
                                                                              text="Enabling {}".format(command_name)))
        await asyncio.sleep(3)
        await enabling_msg.edit(embed=self.bot.create_completed_embed("Enabled.",
                                                                      "Command {} enabled!".format(command_name)))

    @commands.command()
    @is_high_staff()
    async def prefix(self, ctx, new_prefix):
        guild_document = await self.bot.mongo.find_by_id(self.bot.mongo.discord_db.guilds, ctx.guild.id)
        if guild_document is None:
            await self.bot.mongo.insert_guild(ctx.guild)
        await self.bot.mongo.discord_db.guilds.update_one({"_id": ctx.guild.id}, {"$set": {"prefix": new_prefix}})
        await ctx.reply(embed=self.bot.create_completed_embed("Prefix Updated!", f"Set prefix in this guild to: "
                                                                                 f"{new_prefix} (note that "
                                                                                 f"\"u!command\" "
                                                                                 f"always works)"))

    @prefix.error
    async def on_prefix_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(embed=self.bot.create_error_embed("The command is formatted like `u!prefix new_prefix` "
                                                              "where new_prefix is the new prefix you'd like, "
                                                              "for example, `u!prefix $` would set the prefix to "
                                                              "\"$\"."))
            ctx.kwargs["resolved"] = True


def setup(bot):
    cog = CommandManager(bot)
    bot.add_cog(cog)
