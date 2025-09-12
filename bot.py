import discord
from discord.ext import commands
import yt_dlp
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import asyncio
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Spotify setup
sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET
))

# Music queue dict: guild_id -> queue
music_queues = {}

# YTDL options
ytdl_opts = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'default_search': 'ytsearch'
}

ffmpeg_opts = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

ytdl = yt_dlp.YoutubeDL(ytdl_opts)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')

    @classmethod
    async def from_url(cls, url, *, loop=None):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=False))
        if 'entries' in data:
            data = data['entries'][0]
        return cls(discord.FFmpegPCMAudio(data['url'], **ffmpeg_opts), data=data)

# ----- BOT EVENTS -----
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')

# ----- BOT COMMANDS -----
async def ensure_queue(ctx):
    if ctx.guild.id not in music_queues:
        music_queues[ctx.guild.id] = {'queue': [], 'playing': False}

@bot.command()
async def join(ctx):
    if ctx.author.voice:
        channel = ctx.author.voice.channel
        await channel.connect()
    else:
        await ctx.send("You are not in a voice channel!")

@bot.command()
async def leave(ctx):
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        music_queues.pop(ctx.guild.id, None)
    else:
        await ctx.send("I'm not in a voice channel!")

@bot.command()
async def play(ctx, *, query):
    await ensure_queue(ctx)

    # If Spotify link, convert to YouTube search
    if "spotify.com/track" in query:
        track = sp.track(query)
        query = f"{track['name']} {track['artists'][0]['name']}"

    # Get audio source
    source = await YTDLSource.from_url(query)
    music_queues[ctx.guild.id]['queue'].append(source)

    await ctx.send(f"Added **{source.title}** to the queue!")

    if not music_queues[ctx.guild.id]['playing']:
        await play_next(ctx)

async def play_next(ctx):
    queue = music_queues[ctx.guild.id]['queue']
    if not queue:
        music_queues[ctx.guild.id]['playing'] = False
        return

    music_queues[ctx.guild.id]['playing'] = True
    source = queue.pop(0)
    ctx.voice_client.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop))
    await ctx.send(f"Now playing: **{source.title}**")

@bot.command()
async def skip(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("Skipped the current song!")
    else:
        await ctx.send("Nothing is playing!")

@bot.command()
async def pause(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("Paused!")
    else:
        await ctx.send("Nothing is playing!")

@bot.command()
async def resume(ctx):
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("Resumed!")
    else:
        await ctx.send("Nothing is paused!")

@bot.command()
async def queue(ctx):
    await ensure_queue(ctx)
    queue_list = music_queues[ctx.guild.id]['queue']
    if queue_list:
        msg = "\n".join([f"{i+1}. {song.title}" for i, song in enumerate(queue_list)])
        await ctx.send(f"**Queue:**\n{msg}")
    else:
        await ctx.send("Queue is empty.")

@bot.command()
async def stop(ctx):
    if ctx.voice_client:
        ctx.voice_client.stop()
        music_queues[ctx.guild.id]['queue'] = []
        music_queues[ctx.guild.id]['playing'] = False
        await ctx.send("Stopped playback and cleared the queue!")
    else:
        await ctx.send("Nothing is playing!")

bot.run(DISCORD_TOKEN)
