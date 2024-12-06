import asyncio
import logging
from pathlib import Path
import uuid
import validators
import discord
from discord.interactions import Interaction
from discord.ext import commands, tasks
from discord.ui import Button, View
from dotenv import load_dotenv
import os
import yt_dlp as youtube_dl
import datetime
from subclasses import filebin, glyph_tools
import spotipy

make_ephemeral = False
COOKIES_FILE = 'cookies.txt'

# Load the environment variables from .env file
load_dotenv()

# Create a new bot instance
intents = discord.Intents()
intents.members = True
intents.message_content = True

bot = commands.AutoShardedBot(intents=intents, sync_commands=False, help_command=None)

timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

# Create a logger with timestamp in the file name
def setup_logger():
    """
    Setup the logger.
    """
    log_file = f"bot_{timestamp}.log"
    logger = logging.getLogger('bot.py')
    logger.setLevel(logging.INFO)

    # Create a file handler and set the log level
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.INFO)

    # Create a formatter and add it to the file handler
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)

    # Add the file handler to the logger
    logger.addHandler(file_handler)

    return logger

logger = setup_logger()

# load all cogs within the cogs directory
for filename in os.listdir('./cogs'):
    if filename.endswith('.py'):
        try:
            bot.load_extension(f'cogs.{filename[:-3]}')
            logger.info(f"Loaded extension: {filename}")
        except Exception as e:
            logger.error(f"Failed to load extension: {filename}")
            logger.error(f"Error: {str(e)}")
            print(f"Error loading extension: {filename}")
            print(f"Error: {str(e)}")

activity: str = "Meaw~"

@bot.event
async def on_command_error(ctx, error):
    """
    Event triggered when a command fails.
    """
    logger.error(f"Command {ctx.command} failed with error: {str(error)}")

@bot.event
async def on_ready():
    """
    Event triggered when the bot is ready.
    """
    logger.info(f'Logged in as {bot.user.name}')
    logger.info(f'ID: {bot.user.id}')

    activity = "Meaw~"

    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.playing, name=activity))

    # delete all commands and recreate them
    await bot.sync_commands()

@tasks.loop(minutes=1)
async def change_activity():
    """
    Change the bot's activity every 60 seconds.
    """
    # Set the activity
    global activity # make 'activity' a global variable so it can be accessed by the function

    
    # Cycle through a list of activities
    activities = ["Having fun", "Nya", "Meaw~"]
    activity = activities[(activities.index(activity) + 1) % len(activities)]

    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.playing, name=activity))

change_activity.start()

@bot.slash_command(integration_types={discord.IntegrationType.guild_install, discord.IntegrationType.user_install}, name="dl_trim", description="downloads audio from a URL with a specific time range")
async def dl_trim(ctx: discord.ApplicationContext,
                   url: str = discord.Option(name="audio_url", description="The audio file URL", required=True),
                   begin: float = discord.Option(name="start_time", description="The time to start playing the audio in seconds", default=0.0),
                   end: float = discord.Option(name="end_time", description="The time to stop playing the audio in seconds", default=None)):
    """
    Command to play audio from a URL at a specific time.
    """
    logger.info(f"{ctx.author} used /dl_trim command in {ctx.channel} on {ctx.guild}.")

    await ctx.defer()

    try:
        if not validators.url(url):
            await ctx.respond(content="Invalid URL provided.")
            return
    except Exception as e:
        await ctx.respond(content=f"Error validating URL: {str(e)}", ephemeral=True)
        return

    if "spotify.com" in url:
        sp = spotipy.Spotify(auth_manager=spotipy.oauth2.SpotifyClientCredentials(
            client_id=os.getenv('SPOTIFY_CLIENT_ID'),
            client_secret=os.getenv('SPOTIFY_CLIENT_SECRET')
        ))
        track_id = url.split('/')[-1].split('?')[0]
        track_info = sp.track(track_id)
        artist = track_info['artists'][0]['name']
        title = track_info['name']
        search_query = f"{artist} - {title}"
        ydl_opts = {
            'default_search': 'ytsearch',
            'quiet': True,
            'cookiefile': COOKIES_FILE,
        }
        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(search_query, download=False)
            url = info['entries'][0]['webpage_url']
    
    ydl = youtube_dl.YoutubeDL({
        'cookiefile': COOKIES_FILE,
        'quiet': True,
        'no_warnings': True,
        'extract_flat': True,
        'nocheckcertificate': True,
        'ignoreerrors': False,
        'logtostderr': False,
        'no_color': True,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    })
    try:
        info = ydl.extract_info(url, download=False)
    except youtube_dl.DownloadError:
        await ctx.respond(content="Error extracting info from the URL. Please check if the video is available and try again.", ephemeral=True)
        return

    if end is None:
        end = info['duration']
    title = info['title']

    title += f"_{uuid.uuid4()}"

    try:
        begin = float(begin)
        end = float(end)
    except ValueError:
        await ctx.respond(content="Invalid begin or end time.", ephemeral=True)
        return

    if begin < 0.0 or end < 0.0 or begin > end or end > info['duration']:
        await ctx.respond(content="Invalid begin or end time.", ephemeral=True)
        return

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': f'{title}.%(ext)s',
        'restrictfilenames': True,
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'nooverwrites': True,
        'cookiefile': COOKIES_FILE,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'opus',
            'preferredquality': '192',
        }],
    }
    loop = asyncio.get_event_loop()
    with youtube_dl.YoutubeDL(ydl_opts) as ydl:
        try:
            await loop.run_in_executor(None, lambda: ydl.download([url]))
        except youtube_dl.DownloadError as e:
            await ctx.respond(content=f"Error downloading the audio file: {e}", ephemeral=True)
            return

    ffmpeg_cmd = ['ffmpeg', '-i', f'{title}.opus', '-ab', '189k', '-ss', str(begin), '-t', str(end - begin), '-acodec', 'libopus', f'{title}.ogg']
    process = await asyncio.create_subprocess_exec(*ffmpeg_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        await ctx.respond(content=f"Error trimming the audio file: {stderr.decode()}", ephemeral=True)
        return

    try:
        await ctx.respond(content="Here's your audio! Enjoy! 🎵", file=discord.File(f'{title}.ogg'))
    finally:
        for extension in ['.opus', '.ogg']:
            audio_file = Path(f'{title}{extension}')
            if audio_file.is_file():
                audio_file.unlink()


@bot.slash_command(integration_types={discord.IntegrationType.guild_install, discord.IntegrationType.user_install}, name="get-yt-link", description="Get the Youtube link based of a link to some music e.g. Spotify link")
async def get_yt_link(ctx: discord.ApplicationContext, 
                 url: str = discord.Option(name="url", description="The link to the music", required=True),
                 ephemeral: str = discord.Option(name="ephemeral", description="Whether to send the response as an ephemeral message", required=False, default="True", choices=["True", "False"])):
    """
    Command to get the Youtube link based of a link to some music e.g. Spotify link
    """
    ephemeral = True if ephemeral == "True" else False

    logger.info(f"{ctx.author} used /get-yt-link command in {ctx.channel} on {ctx.guild} with URL: {url} and ephemeral: {ephemeral}.")
    
    await ctx.defer(ephemeral=ephemeral)

    try:
        if not validators.url(url):
            await ctx.respond(content="Invalid URL provided.", ephemeral=True)
            return

        if "spotify.com" in url:
            # Initialize Spotify client
            sp = spotipy.Spotify(auth_manager=spotipy.oauth2.SpotifyClientCredentials(
                client_id=os.getenv('SPOTIFY_CLIENT_ID'),
                client_secret=os.getenv('SPOTIFY_CLIENT_SECRET')
            ))
            
            # Extract track ID from URL
            track_id = url.split('/')[-1].split('?')[0]
            
            # Get track info
            track_info = sp.track(track_id)
            artist = track_info['artists'][0]['name']
            title = track_info['name']
            
            # Search on YouTube
            search_query = f"{artist} - {title}"
            ydl_opts = {
                'default_search': 'ytsearch',
                'quiet': True,
                'cookiefile': COOKIES_FILE, 
            }
            with youtube_dl.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(search_query, download=False)
                video_url = info['entries'][0]['webpage_url']
            
            await ctx.respond(content=f"Found YouTube link for '{artist} - {title}': {video_url}", ephemeral=ephemeral)
            return

        # Handle non-Spotify URLs
        ydl = youtube_dl.YoutubeDL({'cookiefile': COOKIES_FILE})
        info = ydl.extract_info(url, download=False)
        if 'entries' in info:
            video_url = info['entries'][0]['webpage_url']
        else:
            video_url = info['webpage_url']
        await ctx.respond(content=f"Here's the YouTube link: {video_url}", ephemeral=ephemeral)
    except spotipy.SpotifyException as e:
        await ctx.respond(content="Error accessing Spotify. Please check the URL.", ephemeral=True)
    except youtube_dl.DownloadError as e:
        await ctx.respond(content="Error finding the video.", ephemeral=True)
    except Exception as e:
        await ctx.respond(content=f"An unexpected error occurred: {str(e)}", ephemeral=True)



# Run the bot
bot.run(os.getenv('BOT_TOKEN'))