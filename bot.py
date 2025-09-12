import discord
from discord.ext import commands, tasks
import yt_dlp
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import asyncio
import os
from dotenv import load_dotenv
# from flask import Flask
# from threading import Thread

# Load environment variables
load_dotenv()
PORT = int(os.getenv("PORT")) or 3000
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

# # ----- SERVER SETUP USING FLASK -----
# app = Flask('')

# @app.route('/')
# def home():
#     return "Discord bot is running!"

# def run_server():
#     app.run(host='0.0.0.0', port=PORT)

# Thread(target=run_server, daemon=True).start()

# ----- DISCORD BOT SETUP -----
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Spotify setup
sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET
))

# Music queue dict: guild_id -> queue, playing status, and auto-leave task
music_queues = {}

# YTDL options
ytdl_opts = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': False,
    'default_search': 'ytsearch',
    # 'cookiefile': 'cookies.txt'
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
        try:
            data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=False))
        except Exception:
            # fallback: search query instead of using the link
            data = await loop.run_in_executor(None, lambda: ytdl.extract_info(f"ytsearch:{url}", download=False))

        if 'entries' in data:
            if not data['entries']:
                raise ValueError(f"No results found for query: {url}")
            data = data['entries'][0]

        return cls(discord.FFmpegPCMAudio(data['url'], **ffmpeg_opts), data=data)


# ----- BOT EVENTS -----
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')

# ----- HELPER FUNCTIONS -----
async def ensure_queue(ctx):
    if ctx.guild.id not in music_queues:
        music_queues[ctx.guild.id] = {
            'queue': [],
            'playing': False,
            'auto_leave_task': None
        }

async def auto_leave_check(ctx):
    await asyncio.sleep(300) # 5 minutes
    guild_data = music_queues.get(ctx.guild.id)
    if guild_data and not guild_data['queue'] and ctx.voice_client and not ctx.voice_client.is_playing():
        await ctx.voice_client.disconnect()
        music_queues.pop(ctx.guild.id, None)
        await ctx.send("👋 Left the voice channel due to inactivity.")

async def play_next(ctx):
    queue = music_queues[ctx.guild.id]['queue']
    if not queue:
        music_queues[ctx.guild.id]['playing'] = False
        # start auto-leave task
        if music_queues[ctx.guild.id]['auto_leave_task']:
            music_queues[ctx.guild.id]['auto_leave_task'].cancel()
        music_queues[ctx.guild.id]['auto_leave_task'] = bot.loop.create_task(auto_leave_check(ctx))
        return

    music_queues[ctx.guild.id]['playing'] = True
    source = queue.pop(0)
    ctx.voice_client.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop))
    await ctx.send(f"▶️ Now playing -> **{source.title}** ❤️")

# ----- BOT COMMANDS -----
@bot.command(aliases=['p'])
async def play(ctx, *, query=None):
    await ensure_queue(ctx)

    # Check if user is in a voice channel
    if not ctx.author.voice:
        await ctx.send("📣 You need to join a voice channel first!")
        return

    # Check if query is empty
    if not query:
        await ctx.send("📣 You need to provide a song name or link!")
        return

    # Auto join if not connected
    if not ctx.voice_client:
        channel = ctx.author.voice.channel
        await channel.connect()
    else:
        # cancel auto-leave if playing
        guild_data = music_queues[ctx.guild.id]
        if guild_data['auto_leave_task']:
            guild_data['auto_leave_task'].cancel()
            guild_data['auto_leave_task'] = None

    # Handle Spotify link
    if "spotify.com/track" in query:
        track = sp.track(query)
        query = f"{track['name']} {track['artists'][0]['name']}"

    # Get source and add to queue
    source = await YTDLSource.from_url(query)
    music_queues[ctx.guild.id]['queue'].append(source)

    # Play if nothing is playing
    if not music_queues[ctx.guild.id]['playing']:
        await play_next(ctx)
    else:
        await ctx.send(f"✅ Added to queue -> **{source.title}** ❤️")

@bot.command()
async def skip(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("📣 Skipped the current song! ⏩")
    else:
        await ctx.send("❌ Nothing is playing!")

@bot.command()
async def pause(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("⏸️ Paused!")
    else:
        await ctx.send("❌ Nothing is playing!")

@bot.command()
async def resume(ctx):
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("▶️ Resumed!")
    else:
        await ctx.send("❌ Nothing is paused!")

@bot.command(aliases=['q'])
async def queue(ctx):
    await ensure_queue(ctx)
    guild_data = music_queues[ctx.guild.id]
    queue_list = guild_data['queue']

    # Create embed
    embed = discord.Embed(
        title="🎶 Music Queue",
        color=discord.Color.blue()
    )

    # Show current song if playing
    if guild_data['playing'] and ctx.voice_client and ctx.voice_client.is_playing():
        current = ctx.voice_client.source
        embed.add_field(
            name="\u200b",  # empty field name
            value=f"▶️ Now playing: **{current.title}**",
            inline=False
        )

    # Show upcoming queue
    if queue_list:
        desc = "\n".join([f"{i+1}. {song.title}" for i, song in enumerate(queue_list[:10])])
        if len(queue_list) > 10:
            desc += f"\n... and {len(queue_list)-10} more"
        embed.add_field(
            name="\u200b",
            value=f"📋 Up Next:\n{desc}",
            inline=False
        )
    else:
        embed.add_field(
            name="\u200b",
            value="Queue is empty.",
            inline=False
        )

    await ctx.send(embed=embed)

@bot.command()
async def stop(ctx):
    if ctx.voice_client:
        ctx.voice_client.stop()
        guild_data = music_queues.get(ctx.guild.id)
        if guild_data:
            guild_data['queue'] = []
            guild_data['playing'] = False
        await ctx.send("🛑 Stopped playback and cleared the queue!")
    else:
        await ctx.send("Nothing is playing!")


# ----- RUN BOT -----
bot.run(DISCORD_TOKEN)
