import asyncio
import datetime

import aiohttp
import aiohttp.client_exceptions
import discord
import re
import time
import concurrent.futures
from io import BytesIO
from discord.ext import commands, tasks
from typing import Optional
from functools import partial

from main import UtilsBot
from src.storage.token import api_token
from src.storage import config
from src.checks.user_check import is_owner
from src.helpers.graph_helper import pie_chart_from_amount_and_labels
from src.helpers.storage_helper import DataHelper


exceptions = (asyncio.exceptions.TimeoutError, aiohttp.client_exceptions.ServerDisconnectedError,
              aiohttp.client_exceptions.ClientConnectorError, aiohttp.client_exceptions.ClientOSError)


class DBApiClient(commands.Cog):
    def __init__(self, bot: UtilsBot):
        self.bot = bot
        self.bot.database_handler = self
        self.session = aiohttp.ClientSession()
        self.restarting = False
        self.data = DataHelper()
        self.db_url = "tgwaffles.me"
        self.bot.loop.create_task(self.ping_db_server())
        self.last_update = self.bot.create_processing_embed("Working...", "Starting processing!")
        self.last_ping = datetime.datetime.now()
        self.update_motw.start()

    @tasks.loop(seconds=1800, count=None)
    async def update_motw(self):
        monkey_guild: discord.Guild = self.bot.get_guild(config.monkey_guild_id)
        motw_role = monkey_guild.get_role(config.motw_role_id)
        motw_channel: discord.TextChannel = self.bot.get_channel(config.motw_channel_id)
        params = {'token': api_token, 'guild_id': config.monkey_guild_id}
        try:
            async with self.session.get(url=f"http://{self.db_url}:6970/leaderboard", timeout=10,
                                        json=params) as request:
                response_json = await request.json()
                results = response_json.get("results")
        except exceptions:
            await self.restart_db_server()
            return
        members = [monkey_guild.get_member(user[0]) for user in results]
        for member in monkey_guild.members:
            if motw_role in member.roles and member not in members:
                await member.remove_roles(motw_role)
                await motw_channel.send(f"Goodbye {member.mention}! You will be missed!")
        for member in members:
            if motw_role not in member.roles:
                await member.add_roles(motw_role)
                await motw_channel.send(f"Welcome {member.mention}! I hope you enjoy your stay!")

    async def ping_db_server(self):
        while True:
            try:
                params = {'timestamp': datetime.datetime.utcnow().timestamp()}
                async with self.session.get(url=f"http://{self.db_url}:6970/ping", timeout=5, json=params) as request:
                    json_info = await request.json()
                    self.last_ping = datetime.datetime.now()
                    if json_info.get("time_delay", 100) > 3:
                        self.bot.loop.create_task(self.restart_db_server())
                        await asyncio.sleep(3)
            except aiohttp.client_exceptions.ClientConnectorError:
                self.bot.loop.create_task(self.restart_db_server())
                await asyncio.sleep(5)
            except asyncio.exceptions.TimeoutError:
                self.bot.loop.create_task(await self.restart_db_server())
                await asyncio.sleep(3)
            except aiohttp.client_exceptions.ClientOSError:
                await asyncio.sleep(2)
            await asyncio.sleep(1)

    async def restart_db_server(self):
        if not self.restarting:
            params = {'token': api_token}
            self.restarting = True
            try:
                async with self.session.post(url=f"http://{self.db_url}:6970/restart", timeout=10, json=params) as request:
                    if request.status == 202:
                        print("Restarted DB server")
                    else:
                        raise aiohttp.client_exceptions.ClientConnectorError
            except aiohttp.client_exceptions.ClientConnectorError:
                await self.session.post(url=f"http://{self.db_url}:6969/restart", json=params)
                print("Force restarted DB server.")
            last_ping = self.last_ping
            while self.last_ping == last_ping:
                await asyncio.sleep(0.1)
            self.restarting = False

    async def get_someone_id(self, guild_id):
        params = {'token': api_token, "guild_id": guild_id}
        while True:
            try:
                async with self.session.get(url=f"http://{self.db_url}:6970/someone", timeout=10, json=params) as request:
                    response_json = await request.json()
                    return response_json.get("member_id")
            except exceptions:
                await self.restart_db_server()

    @commands.command()
    async def snipe(self, ctx, amount=1):
        sent = await ctx.reply(embed=self.bot.create_processing_embed("Processing...", "Getting sniped message..."))
        params = {'token': api_token, 'channel_id': ctx.channel.id, "amount": amount}
        while True:
            try:
                async with self.session.get(url=f"http://{self.db_url}:6970/snipe", timeout=10, json=params) as request:
                    if request.status != 200:
                        await sent.edit(embed=self.bot.create_error_embed(f"Couldn't snipe! "
                                                                          f"(status: {request.status})"))
                        return
                    response_json = await request.json()
                    user_id = response_json.get("user_id")
                    content = response_json.get("content")
                    timestamp = datetime.datetime.fromisoformat(response_json.get("timestamp"))
                    user = self.bot.get_user(user_id)
                    embed = discord.Embed(title="Sniped Message", colour=discord.Colour.red())
                    embed.set_author(name=user.name, icon_url=user.avatar_url)
                    preceding_message = (await ctx.channel.history(before=timestamp, limit=1).flatten())[0] or None
                    if preceding_message is not None:
                        embed.add_field(name="\u200b", value=f"[Previous Message]({preceding_message.jump_url})")
                    embed.description = content
                    embed.timestamp = timestamp
                    await sent.edit(embed=embed)
                    return True
            except exceptions:
                await self.restart_db_server()

    @commands.command(description="Get leaderboard pie!")
    async def leaderpie(self, ctx):
        sent = await ctx.reply(embed=self.bot.create_processing_embed("Generating leaderboard",
                                                                      "Processing messages for leaderboard..."))
        params = {'token': api_token, 'guild_id': ctx.guild.id}
        while True:
            try:
                async with self.session.get(url=f"http://{self.db_url}:6970/leaderboard_pie", timeout=10,
                                            json=params) as request:
                    if request.status != 200:
                        await sent.edit(embed=self.bot.create_error_embed(f"Couldn't generate leaderboard! "
                                                                          f"(status: {request.status})"))
                        return
                    request_json = await request.json()
                    labels = request_json.get("labels")
                    amounts = request_json.get("amounts")
                    await sent.edit(embed=self.bot.create_processing_embed("Got leaderboard!", "Generating pie chart."))
                    with concurrent.futures.ProcessPoolExecutor() as pool:
                        data = await self.bot.loop.run_in_executor(pool, partial(pie_chart_from_amount_and_labels,
                                                                                 labels, amounts))
                    file = BytesIO(data)
                    file.seek(0)
                    discord_file = discord.File(fp=file, filename="image.png")
                    await ctx.reply(file=discord_file)
                    await sent.delete()
                    return
            except exceptions:
                await self.restart_db_server()

    @commands.command(description="Count how many times a phrase has been said!")
    async def count(self, ctx, *, phrase):
        if len(phrase) > 223:
            await ctx.reply(embed=self.bot.create_error_embed("That phrase was too long!"))
            return
        sent = await ctx.reply(embed=self.bot.create_processing_embed("Counting...",
                                                                      f"Counting how many times \"{phrase}\" "
                                                                      f"has been said..."))
        params = {"phrase": phrase, "guild_id": ctx.guild.id, "token": api_token}
        while True:
            try:
                async with self.session.get(url=f"http://{self.db_url}:6970/global_phrase_count", timeout=10,
                                            json=params) as request:
                    if request.status != 200:
                        await sent.edit(embed=self.bot.create_error_embed(f"Couldn't count! "
                                                                          f"(status: {request.status})"))
                        return
                    response_json = await request.json()
                    amount = response_json.get("amount")
                    embed = self.bot.create_completed_embed(
                        f"Number of times \"{phrase}\" has been said:", f"**{amount}** times!")
                    embed.set_footer(text="If you entered a phrase, remember to surround it in **straight** quotes ("
                                          "\"\")!")
                    await sent.edit(embed=embed)
                    return True
            except exceptions:
                await self.restart_db_server()

    @commands.command(aliases=["ratio", "percentage"])
    async def percent(self, ctx, member: Optional[discord.User]):
        if member is None:
            member = ctx.author
        sent = await ctx.reply(embed=self.bot.create_processing_embed("Counting...",
                                                                      f"Counting {member.name}'s amount of "
                                                                      f"messages!"))
        params = {"guild_id": ctx.guild.id, "member_id": member.id, "token": api_token}
        while True:
            try:
                async with self.session.get(url=f"http://{self.db_url}:6970/percentage", timeout=10,
                                            json=params) as request:
                    if request.status != 200:
                        await sent.edit(embed=self.bot.create_error_embed(f"Couldn't count! "
                                                                          f"(status: {request.status})"))
                        return
                    response_json = await request.json()
                    amount = response_json.get("amount")
                    percentage = response_json.get("percentage")
                    embed = self.bot.create_completed_embed(f"Amount of messages {member.name} has sent!",
                                                            f"{member.name} has sent {amount:,} messages. "
                                                            f"That's {percentage}% "
                                                            f"of the server's total!")
                    await sent.edit(embed=embed)
                    return True
            except exceptions:
                await self.restart_db_server()

    async def send_update(self, sent_message):
        if len(self.last_update.description) < 2000:
            await sent_message.edit(embed=self.last_update)

    @commands.command()
    @is_owner()
    async def full_guild(self, ctx):
        sent_message = await ctx.reply(embed=self.bot.create_processing_embed("Working...", "Starting processing!"))
        tasks = []
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=20000)
        for channel in ctx.guild.text_channels:
            tasks.append(self.bot.loop.create_task(self.load_channel(channel, pool)))
        while any([not task.done() for task in tasks]):
            await self.send_update(sent_message)
            await asyncio.sleep(1)
        await asyncio.gather(*tasks)
        await sent_message.edit(embed=self.bot.create_completed_embed("Finished", "done ALL messages. wow."))

    async def load_channel(self, channel: discord.TextChannel, executor):
        last_edit = time.time()
        resume_from = self.data.get("resume_from_{}".format(channel.id), None)
        if resume_from is not None:
            resume_from = await channel.fetch_message(resume_from)
        print(resume_from)
        messages_to_send = []
        # noinspection DuplicatedCode
        async for message in channel.history(limit=None, oldest_first=True, after=resume_from):
            now = time.time()
            if now - last_edit > 3:
                embed = discord.Embed(title="Processing messages",
                                      description="Last Message text: {}, from {}, in {}".format(
                                          message.clean_content, message.created_at.strftime("%Y-%m-%d %H:%M"),
                                          channel.mention), colour=discord.Colour.orange())
                embed.set_author(name=message.author.name, icon_url=message.author.avatar_url)
                embed.timestamp = message.created_at
                self.last_update = embed
                last_edit = now
                self.data[f"resume_from_{channel.id}"] = message.id
            if len(message.embeds) > 0:
                embed_json = message.embeds[0].to_dict()
            else:
                embed_json = None
            messages_to_send.append({"id": message.id, "channel_id": message.channel.id,
                                     "guild_id": message.guild.id, "user_id": message.author.id,
                                     "content": message.content, "embed_json": embed_json,
                                     "timestamp": message.created_at.isoformat(), "name": message.author.name,
                                     "bot": message.author.bot})
            if len(messages_to_send) >= 100:
                while True:
                    try:
                        req = await self.session.post(url=f"http://elastic.thom.club:6970/many_messages", timeout=10,
                                                      json={"token": api_token, "messages": messages_to_send})
                        messages_to_send = []
                        response = await req.json()
                        if not response.get("success"):
                            print("it managed to fail?")
                        break
                    except exceptions:
                        await self.restart_db_server()

    @commands.command()
    async def leaderboard(self, ctx):
        sent = await ctx.reply(embed=self.bot.create_processing_embed("Generating leaderboard",
                                                                      "Processing messages for leaderboard..."))
        params = {'token': api_token, 'guild_id': ctx.guild.id}
        while True:
            try:
                async with self.session.get(url=f"http://{self.db_url}:6970/leaderboard", timeout=10,
                                            json=params) as request:
                    if request.status != 200:
                        await sent.edit(embed=self.bot.create_error_embed(f"Couldn't generate leaderboard! "
                                                                          f"(status: {request.status})"))
                        return
                    request_json = await request.json()
                    results = request_json.get("results")
                    embed = discord.Embed(title="Activity Leaderboard - Past 7 Days", colour=discord.Colour.green())
                    embed.description = "```"
                    embed.set_footer(text="More information about this in #role-assign (monkeys of the week!)")
                    regex_pattern = re.compile(pattern="["
                                                       u"\U0001F600-\U0001F64F"
                                                       u"\U0001F300-\U0001F5FF"
                                                       u"\U0001F680-\U0001F6FF"
                                                       u"\U0001F1E0-\U0001F1FF"
                                                       "]+", flags=re.UNICODE)
                    lengthening = []
                    for index, user in enumerate(results):
                        member = ctx.guild.get_member(user[0])
                        name = (member.nick or member.name).replace("✨", "aa")
                        name = regex_pattern.sub('a', name)
                        name_length = len(name)
                        lengthening.append(name_length + len(str(index + 1)))
                    max_length = max(lengthening)
                    for i in range(len(results)):
                        member = ctx.guild.get_member(results[i][0])
                        name = member.nick or member.name
                        text = f"{i + 1}. {name}: " + " " * (max_length - lengthening[i]) + f"Score: {results[i][1]}\n"
                        embed.description += text
                    embed.description += "```"
                    await sent.edit(embed=embed)
                    return True
            except exceptions:
                await self.restart_db_server()

    async def update(self):
        params = {'token': api_token}
        await self.session.post(url=f"http://{self.db_url}:6969/update", json=params)


def setup(bot: UtilsBot):
    cog = DBApiClient(bot)
    bot.add_cog(cog)
