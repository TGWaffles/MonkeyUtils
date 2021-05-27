import audioop
import json.decoder
import random
import re
import time
from concurrent.futures import ProcessPoolExecutor

import aiohttp
import discord
import youtube_dl
import youtubesearchpython.__future__ as youtube_search
from discord.ext import commands
from pytube import Playlist

from src.checks.role_check import is_staff
from src.helpers.paginator import Paginator
from src.helpers.spotify_helper import *

# TODO:
"""1. Add a true pagination system to the bot as a whole to allow !queue DONE
2. Add !queue DONE, !clearqueue, !dequeue <index>
3. Add !volume DONE
4. Add thumbnails (maybe) for "now playing" embeds. DONE
5. Clean up help command for music related bot commands.
6. Add variable prefix (set to something obscure at first) DONE
7. Add !pause DONE
8. <FUTURE> Start on web help page and change help handler to fully custom help handler."""

youtube_dl.utils.bug_reports_message = lambda: ''

ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0'  # bind to ipv4 since ipv6 addresses cause issues sometimes
}

ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)


class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5, resume_from=0):
        super().__init__(source, volume)
        self.data = data
        self.time = 0.0
        self.title = data.get('title')
        self.url = data.get('url')
        self.webpage_url = data.get("webpage_url")
        self.start_time = None
        self.resume_from = resume_from

    def read(self):
        if not self.start_time:
            self.start_time = time.time() - self.resume_from
        ret = self.original.read()
        return audioop.mul(ret, 2, self._volume)

    @classmethod
    async def from_url(cls, url, *, loop=None):
        data = await cls.get_video_data(url, loop)
        if data is None:
            return None
        return cls(discord.FFmpegPCMAudio(data["url"], **ffmpeg_options), data=data)

    @staticmethod
    async def get_video_data(url, loop=None, search=False, target_duration=None):
        loop = loop or asyncio.get_event_loop()
        if search:
            if target_duration is not None:
                title = url.split("\uFEFF")[1]
                url = url.replace("\uFEFF", "")
                plain_query = youtube_search.CustomSearch(url, youtube_search.VideoSortOrder.relevance, limit=10)
                url += " description:(\"Auto-generated by YouTube.\")"
                query = youtube_search.CustomSearch(url, youtube_search.VideoSortOrder.viewCount, limit=10)
                original_results = await query.next()
                plain_results = await plain_query.next()
                original_results = original_results.get("result") + plain_results.get("result")
                # Check within 10s, 20s, 60s, then any result.
                for max_difference in [10000, 20000, 60000]:
                    results = [x for x in original_results if target_duration - max_difference <
                               transform_duration_to_ms(x.get(
                                   "duration")) < target_duration + max_difference]
                    if len(results) > 0:
                        with ProcessPoolExecutor() as pool:
                            results = await loop.run_in_executor(pool, partial(find_closest, title, url, results))
                        return results[0].get("link")
                    print(f"no results within {max_difference // 1000}s of target duration {target_duration // 1000}s")
            query = youtube_search.CustomSearch(url, youtube_search.VideoSortOrder.relevance, limit=1)
            data = await query.next()
            return data.get("result")[0].get("link")
        else:
            try:
                attempts = 0
                while True:
                    if attempts > 10:
                        return None
                    attempts += 1
                    ydl = youtube_dl.YoutubeDL(ytdl_format_options)
                    future = loop.run_in_executor(None, lambda: ydl.extract_info(url, download=False))
                    try:
                        data = await asyncio.wait_for(future, 10)
                        if data is not None:
                            break
                    except asyncio.TimeoutError:
                        pass
            except youtube_dl.utils.DownloadError:
                return None
            if 'entries' in data and len(data['entries']) > 0:
                data = sorted(data['entries'], key=lambda x: x.get("view_count", 0), reverse=True)[0]
                # take first item from a playlist
                # data = data['entries'][0]
        if data.get('url', None) is None:
            return None
        return data


class Music(commands.Cog):
    def __init__(self, bot: UtilsBot):
        self.bot = bot
        # self.data = DataHelper()
        self.tts_cog = bot.get_cog("TTS")
        self.music_db = self.bot.mongo.client.music
        # self.data["song_queues"] = {}
        self.spotify = SpotifySearcher(self.bot)
        self.url_to_title_cache = {}
        self.bot.loop.create_task(self.restart_watcher())

    async def save_all_tracks(self):
        for voice_client in self.bot.voice_clients:
            if not isinstance(voice_client, discord.VoiceClient):
                continue
            if not isinstance(voice_client.source, YTDLSource):
                continue
            await self.pause_voice_client(voice_client)
            await self.bot.mongo.force_insert(self.music_db.restart_resume, {"_id": voice_client.channel.id})

    async def restart_watcher(self):
        if self.bot.restart_event is None:
            self.bot.restart_event = asyncio.Event()
        while True:
            try:
                await self.bot.wait_until_ready()
                await self.post_restart_resume()
                await self.bot.restart_event.wait()
                async with self.bot.restart_waiter_lock:
                    self.bot.restart_waiters += 1
                await self.save_all_tracks()
                async with self.bot.restart_waiter_lock:
                    self.bot.restart_waiters -= 1
                return
            except RuntimeError:
                pass

    async def enqueue(self, guild, song_url, resume_time=None, start=False):
        guild_document = await self.guild_document_from_guild(guild)
        guild_queue = guild_document.get("queue", [])
        if resume_time is None:
            to_queue = song_url
        else:
            to_queue = [song_url, resume_time]
        if start:
            guild_queue.insert(0, to_queue)
        else:
            guild_queue.append(to_queue)
        await self.music_db.songs.update_one({"_id": guild.id}, {'$set': {"queue": guild_queue}}, upsert=True)
        return True

    async def bulk_enqueue(self, guild, song_urls, start=False):
        guild_document = await self.guild_document_from_guild(guild)
        guild_queue = guild_document.get("queue", [])
        if start:
            guild_queue = song_urls + guild_queue
        else:
            guild_queue += song_urls
        await self.music_db.songs.update_one({"_id": guild.id}, {'$set': {"queue": guild_queue}}, upsert=True)
        return True

    @staticmethod
    def get_playlist(provided_info):
        try:
            playlist_info = Playlist(provided_info).video_urls
        except KeyError:
            playlist_info = None
        return playlist_info

    async def title_from_url(self, video_url):
        if video_url in self.url_to_title_cache:
            return self.url_to_title_cache[video_url]
        if "open.spotify.com" in video_url:
            _, title, _ = await self.bot.loop.run_in_executor(None, partial(self.spotify.get_track, video_url))
            self.url_to_title_cache[video_url] = title
            return title
        params = {"format": "json", "url": video_url}
        url = "https://www.youtube.com/oembed"
        async with aiohttp.ClientSession() as session:
            request = await session.get(url=url, params=params)
            try:
                json_response = await request.json()
            except json.decoder.JSONDecodeError:
                json_response = await YTDLSource.get_video_data(video_url, self.bot.loop)
        title = json_response["title"]
        self.url_to_title_cache[video_url] = title
        return title

    async def thumbnail_from_url(self, video_url):
        exp = r"^.*((youtu.be\/)|(v\/)|(\/u\/\w\/)|(embed\/)|(watch\?))\??v?=?([^#&?]*).*"
        try:
            s = re.findall(exp, video_url)[0][-1]
        except IndexError:
            json_response = await YTDLSource.get_video_data(video_url, self.bot.loop)
            try:
                return json_response["thumbnails"][-1]["url"]
            except (KeyError, IndexError):
                return None
        thumbnail = f"https://i.ytimg.com/vi/{s}/hqdefault.jpg"
        return thumbnail

    async def song_from_yt(self, song, duration=None):
        attempts = 0
        while True:
            if attempts > 3:
                print(f"{song} failed after 3 attempts")
                return None
            attempts += 1
            youtube_song = await YTDLSource.get_video_data(song, self.bot.loop, search=True, target_duration=duration)
            if isinstance(youtube_song, str):
                return youtube_song
            if youtube_song is not None and youtube_song.get("webpage_url") is not None:
                return youtube_song.get("webpage_url")
            await asyncio.sleep(2)

    async def transform_spotify(self, to_play):
        spotify_playlist = await self.spotify.handle_spotify(to_play)
        if spotify_playlist is None:
            return None
        for song in spotify_playlist:
            self.url_to_title_cache[song[0]] = song[1]
        return [song[0] for song in spotify_playlist]

    async def transform_single_song(self, song):
        if "open.spotify.com" not in song:
            return song
        _, string_song, duration = await self.bot.loop.run_in_executor(None, partial(self.spotify.get_track, song))
        if string_song is None:
            return None
        youtube_link = await self.song_from_yt(string_song, duration=duration)
        return youtube_link

    async def send_queue(self, channel, reply_message=None):
        guild_document = await self.guild_document_from_guild(channel.guild)
        guild_queued = guild_document.get("queue", [])
        if len(guild_queued) == 0:
            return False
        futures = []
        titles = []
        for url in guild_queued:
            if type(url) == tuple or type(url) == list:
                url, _ = url
            if url in self.url_to_title_cache:
                titles.append(self.url_to_title_cache[url])
                continue
            titles.append(None)
            futures.append(self.bot.loop.create_task(self.title_from_url(url), name=url))
        waited_titles = await asyncio.gather(*futures)
        for index, title in enumerate(titles.copy()):
            if title is None:
                # noinspection PyUnresolvedReferences
                titles[index] = waited_titles.pop(0)
        successfully_added = ""
        for index, title in enumerate(titles):
            successfully_added += f"{index + 1}. **{title}**\n"
        paginator = Paginator(self.bot, channel, "Queued Songs", successfully_added, 500, reply_message=reply_message)
        await paginator.start()
        return True

    @commands.command(aliases=["que", "cue", "q"])
    async def queue(self, ctx):
        if not await self.send_queue(ctx.channel, ctx):
            await ctx.reply(embed=self.bot.create_error_embed("No songs queued!"))
            return

    @commands.command(aliases=["clearqueue"])
    @is_staff()
    async def clear_queue(self, ctx):
        guild_document = await self.guild_document_from_guild(ctx.guild)
        guild_queued = guild_document.get("queue", [])
        if len(guild_queued) == 0:
            await ctx.reply(embed=self.bot.create_error_embed("There are no songs queued."))
            return
        await self.music_db.songs.update_one({"_id": ctx.guild.id}, {'$set': {"queue": []}}, upsert=True)
        await ctx.reply(embed=self.bot.create_completed_embed("Cleared Queue!", "Queue cleared!"))

    @commands.command(aliases=["unqueue"])
    async def dequeue(self, ctx, index: int):
        guild_document = await self.guild_document_from_guild(ctx.guild)
        guild_queued = guild_document.get("queue", [])
        if not 0 < index < len(guild_queued) + 1:
            await ctx.reply(embed=self.bot.create_error_embed("That is not a valid queue position!"))
            return
        index -= 1
        song = guild_queued.pop(index)
        await self.music_db.songs.update_one({"_id": ctx.guild.id}, {'$set': {"queue": guild_queued}}, upsert=True)
        title = await self.title_from_url(song)
        await ctx.reply(embed=self.bot.create_completed_embed("Successfully removed song from queue!",
                                                              f"Successfully removed [{title}]({song})"
                                                              f" from the queue!"))

    @dequeue.error
    async def dequeue_error(self, ctx, error):
        if isinstance(error, commands.ConversionError):
            await ctx.reply(embed=self.bot.create_error_embed("Please refer to the song by index, not name, "
                                                              "so I don't guess wrong! \n"
                                                              "(do !queue to see the queue with indexes)"))

    @commands.command(aliases=["p"])
    async def play(self, ctx, *, to_play):
        async with ctx.typing():
            if "spotify" in to_play:
                playlist_info = await self.transform_spotify(to_play)
                if playlist_info is None:
                    await ctx.reply(embed=self.bot.create_error_embed("I couldn't recognise that song, sorry!"))
            else:
                playlist_info = await self.bot.loop.run_in_executor(None, partial(self.get_playlist, to_play))
                if playlist_info is None:
                    video_info = await YTDLSource.get_video_data(to_play, self.bot.loop)
                    playlist_info = [video_info["webpage_url"]]
            first_song = playlist_info.pop(0)
            first_song = await self.transform_single_song(first_song)
            await self.music_db.songs.update_one({"_id": ctx.guild.id}, {'$set': {"text_channel_id": ctx.channel.id}},
                                                 upsert=True)
            start = False
            if not ctx.voice_client.is_playing() or not isinstance(ctx.voice_client.source, YTDLSource):
                start = True
            if start:
                await self.enqueue(ctx.guild, first_song, None, True)
                self.bot.loop.create_task(self.play_next_queued(ctx.voice_client))
                await asyncio.sleep(1)
            else:
                await self.enqueue(ctx.guild, first_song)
            first_song_name = await self.title_from_url(first_song)
            embed = self.bot.create_completed_embed("Added song to queue!", f"Added [{first_song_name}]"
                                                                            f"({first_song}) "
                                                                            f"to queue!\n"
                                                                            f"Please note other songs in "
                                                                            f"a playlist may still be "
                                                                            f"processing.")
            thumbnail_url = await self.thumbnail_from_url(first_song)
            embed.set_thumbnail(url=thumbnail_url)
            await ctx.reply(embed=embed)
            futures = []
            for url in playlist_info:
                futures.append(self.bot.loop.create_task(self.title_from_url(url), name=url))
            await asyncio.sleep(2)
            titles = await asyncio.gather(*futures)
            await self.bulk_enqueue(ctx.guild, titles, start)
            await self.send_queue(ctx.channel, ctx)

    @commands.command(aliases=["repeat"])
    async def loop(self, ctx):
        guild_document = await self.guild_document_from_guild(ctx.guild)
        if guild_document.get("loop", False):
            await self.music_db.songs.update_one({"_id": guild_document.get("_id")}, {"$set": {"loop": False}})
            await ctx.reply(embed=self.bot.create_completed_embed("Disabled Looping!", "The song will no longer loop!"))
        else:
            if ctx.voice_client.source is not None:
                currently_playing_url = ctx.voice_client.source.webpage_url
                await self.enqueue(ctx.voice_client.guild, currently_playing_url)
            await self.music_db.songs.update_one({"_id": guild_document.get("_id")}, {"$set": {"loop": True}})
            await ctx.reply(embed=self.bot.create_completed_embed("Enabled Looping!", "The song will now loop!"))

    @commands.command(aliases=["shuff", "mix"])
    async def shuffle(self, ctx):
        guild_document = await self.guild_document_from_guild(ctx.guild)
        guild_queued = guild_document.get("queue", [])
        if len(guild_queued) == 0:
            await ctx.reply(embed=self.bot.create_error_embed("There is no queue in your guild!"))
            return
        random.shuffle(guild_queued)
        await self.music_db.songs.update_one({"_id": ctx.guild.id}, {'$set': {"queue": guild_queued}}, upsert=True)
        await ctx.reply(embed=self.bot.create_completed_embed("Shuffled!", "Shuffled song queue! "
                                                                           "(skip to go to next shuffled song)"))

    async def guild_document_from_guild(self, guild):
        song_collection = self.music_db.songs
        guild_document = await self.bot.mongo.find_by_id(song_collection, guild.id)
        if guild_document is None:
            guild_document = {"_id": guild.id, "queue": [], "text_channel_id": None}
            await self.bot.mongo.force_insert(song_collection, guild_document)
        return guild_document

    async def play_next_queued(self, voice_client: discord.VoiceClient):
        if voice_client is None or not voice_client.is_connected():
            return
        await asyncio.sleep(0.5)
        guild_document = await self.guild_document_from_guild(voice_client.guild)
        guild_queued = guild_document.get("queue", [])
        if len(guild_queued) == 0:
            # await voice_client.disconnect()
            return
        if guild_document.get("loop", False):
            next_song_url = guild_queued[0]
        else:
            next_song_url = guild_queued.pop(0)
        await self.music_db.songs.update_one({"_id": voice_client.guild.id}, {'$set': {"queue": guild_queued}},
                                             upsert=True)
        local_ffmpeg_options = ffmpeg_options.copy()
        resume_from = 0
        if type(next_song_url) == tuple or type(next_song_url) == list:
            next_song_url, resume_from = next_song_url
            local_ffmpeg_options['options'] = "-vn -ss {}".format(resume_from)
        volume_document = await self.bot.mongo.find_by_id(self.music_db.volumes, voice_client.guild.id)
        volume = volume_document.get("volume", 0.5)
        if next_song_url is None:
            self.bot.loop.create_task(self.play_next_queued(voice_client))
            return
        next_song_url = await self.transform_single_song(next_song_url)
        if next_song_url is None:
            self.bot.loop.create_task(self.play_next_queued(voice_client))
            return
        data = await YTDLSource.get_video_data(next_song_url, self.bot.loop)
        source = YTDLSource(discord.FFmpegPCMAudio(data["url"], **local_ffmpeg_options),
                            data=data, volume=volume, resume_from=resume_from)
        if voice_client.guild.id in self.tts_cog.guild_queues:
            while len(self.tts_cog.guild_queues[voice_client.guild.id]) > 0:
                await asyncio.sleep(0.1)
        while voice_client.is_playing():
            await asyncio.sleep(0.1)
        voice_client.play(source, after=lambda e: self.bot.loop.create_task(self.play_next_queued(voice_client)))
        title = await self.title_from_url(next_song_url)
        embed = self.bot.create_completed_embed("Playing next song!", "Playing **[{}]({})**".format(title,
                                                                                                    next_song_url))
        thumbnail_url = await self.thumbnail_from_url(next_song_url)
        if thumbnail_url is not None:
            embed.set_thumbnail(url=thumbnail_url)
        text_channel_id = guild_document.get("text_channel_id", None)
        if text_channel_id is None:
            print("text channel id is none")
            return
        # noinspection PyTypeChecker
        called_channel = self.bot.get_channel(text_channel_id)
        history = await called_channel.history(limit=1).flatten()
        print("sending message")
        if len(history) > 0 and history[0].author.id == self.bot.user.id:
            old_message = history[0]
            if len(old_message.embeds) > 0:
                if old_message.embeds[0].title == "Playing next song!":
                    await old_message.edit(embed=embed)
                    return
        await called_channel.send(embed=embed)

    @commands.command(aliases=["res", "continue"])
    async def resume(self, ctx):
        self.bot.loop.create_task(self.play_next_queued(ctx.voice_client))
        await ctx.reply(embed=self.bot.create_completed_embed("Resumed!", "Resumed playing."))

    async def post_restart_resume(self):
        resume_collection = self.music_db.restart_resume
        resume_channels = await resume_collection.distinct("_id")
        for voice_channel_id in resume_channels:
            voice_channel = self.bot.get_channel(voice_channel_id)
            try:
                voice_client = await voice_channel.connect()
            except AttributeError:
                continue
            self.bot.loop.create_task(self.play_next_queued(voice_client))
        await resume_collection.delete_many({})

    async def pause_voice_client(self, voice_client):
        if voice_client.source is not None:
            currently_playing_url = voice_client.source.webpage_url
            current_time = int(time.time() - voice_client.source.start_time)
            await self.enqueue(voice_client.guild, currently_playing_url, int(current_time), start=True)
        voice_client.stop()
        await voice_client.disconnect()

    @commands.command(aliases=["stop", "leave", "quit"])
    async def pause(self, ctx):
        await self.pause_voice_client(ctx.voice_client)
        await ctx.reply(embed=self.bot.create_completed_embed("Successfully paused.", "Song paused successfully."))

    async def skip_guild(self, guild):
        if guild.voice_client.is_playing():
            try:
                song = f" \"{guild.voice_client.source.title}\""
            except AttributeError:
                song = ""
            guild.voice_client.stop()
        else:
            guild_document = await self.guild_document_from_guild(guild)
            guild_queued = guild_document.get("queue", [])
            if len(guild_queued) == 0:
                return None
            song_url = guild_queued.pop(0)
            await self.music_db.songs.update_one({"_id": guild.id}, {'$set': {"queue": guild_queued}}, upsert=True)
            song = f" \"{await self.title_from_url(song_url)}\""
        return song

    @commands.command(aliases=["next"])
    async def skip(self, ctx):
        song = await self.skip_guild(ctx.guild)
        if song is None:
            await ctx.reply(embed=self.bot.create_error_embed("There is no song playing or queued!"))
            return
        await ctx.reply(embed=self.bot.create_completed_embed("Song skipped.", f"Song{song} skipped successfully."))

    @commands.command(aliases=["vol"])
    async def volume(self, ctx, volume: float):
        volume = volume / 100
        if volume < 0:
            volume = 0
        document = {"_id": ctx.guild.id, "volume": volume}
        await self.bot.mongo.force_insert(self.music_db.volumes, document)
        try:
            ctx.voice_client.source.volume = volume
        except AttributeError:
            pass
        await ctx.reply(embed=self.bot.create_completed_embed("Changed volume!", f"Set volume to "
                                                                                 f"{volume * 100}% for this guild!"))

    # async def queue(self, ctx):
    #     self.bot.add_listener()
    #     guild_queue = self.data.get("song_queues", {}).get(str(ctx.guild.id), [])
    #     queue_message = ""
    #     for index in range(len(guild_queue)):
    #         link = guild_queue[index]
    #         if index % 5 == 0:

    @pause.before_invoke
    @play.before_invoke
    @resume.before_invoke
    @skip.before_invoke
    async def ensure_voice(self, ctx):
        if ctx.voice_client is None:
            if ctx.author.voice:
                await ctx.author.voice.channel.connect()
            else:
                await ctx.reply(embed=self.bot.create_error_embed("You are not connected to a voice channel."))
                raise commands.CommandError("Author not connected to a voice channel.")
        elif not ctx.author.voice or ctx.voice_client.channel != ctx.author.voice.channel:
            await ctx.reply(embed=self.bot.create_error_embed("You have to be connected to the voice channel to "
                                                              "execute these commands!"))
            raise commands.CommandError("Author not connected to the correct voice channel.")


def setup(bot: UtilsBot):
    cog = Music(bot)
    bot.add_cog(cog)


def teardown(bot: UtilsBot):
    cog = bot.get_cog("Music")
    bot.loop.create_task(cog.save_all_tracks())
