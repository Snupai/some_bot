if __name__ == "__main__":
    print("This is a cog file and cannot be run directly.")
    exit()

import discord
from discord.ext import commands
import asyncio
import logging
from pathlib import Path
import uuid
import validators
import os
import yt_dlp as youtube_dl
import spotipy
from pydub import AudioSegment
import base64
import sqlite3

COOKIES_FILE = 'cookies.txt'

class YoutubeDLPCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger('bot.py')
        
        
    async def is_user_allowed(self, user):
        # check the allowed_users.sqlite file for the user
        conn = sqlite3.connect('allowed_users.sqlite')
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM allowed_users WHERE user_id = ?', (user.id,))
        result = cursor.fetchone()
        if result:
            return True
        return False
    
    ytdlp_cog = discord.SlashCommandGroup(integration_types={discord.IntegrationType.guild_install, discord.IntegrationType.user_install}, name="ytdlp", description="Youtube-dlp API")

    @ytdlp_cog.command(integration_types={discord.IntegrationType.guild_install, discord.IntegrationType.user_install}, name="dl_trim", description="downloads audio from a URL with a specific time range")
    async def dl_trim(self, ctx: discord.ApplicationContext,
                    url: str = discord.Option(name="audio_url", description="The audio file URL", required=True),
                    begin: float = discord.Option(name="start_time", description="The time to start playing the audio in seconds", default=0.0),
                    end: float = discord.Option(name="end_time", description="The time to stop playing the audio in seconds", default=None)):
        """
        Command to play audio from a URL at a specific time.
        """
        if not await self.is_user_allowed(ctx.author):
            await ctx.respond(content="You are not allowed to use this command.", ephemeral=True)
            return
        self.logger.info(f"{ctx.author} used /dl_trim command in {ctx.channel} on {ctx.guild}.")

        await ctx.defer()

        if not await self.validate_url(ctx, url):
            return

        url = await self.handle_spotify_url(url)

        info = await self.extract_info(ctx, url)
        if not info:
            return

        title, begin, end = await self.prepare_download(ctx, info, begin, end)
        if not title:
            return

        if not await self.download_audio(ctx, url, title):
            return

        if not await self.trim_audio(ctx, title, begin, end):
            return

        await self.send_audio(ctx, title)

    async def validate_url(self, ctx, url):
        try:
            if not validators.url(url):
                await ctx.respond(content="Invalid URL provided.")
                return False
        except Exception as e:
            await ctx.respond(content=f"Error validating URL: {str(e)}", ephemeral=True)
            return False
        return True

    async def handle_spotify_url(self, url):
        self.logger.info(f"Handling Spotify URL: {url}")
        try:
            if "spotify.com" in url:
                if not os.getenv('SPOTIFY_CLIENT_ID') or not os.getenv('SPOTIFY_CLIENT_SECRET'):
                    self.logger.error("Spotify credentials not found in environment variables")
                    raise Exception("Spotify credentials not configured")
                    
                sp = spotipy.Spotify(auth_manager=spotipy.oauth2.SpotifyClientCredentials(
                    client_id=os.getenv('SPOTIFY_CLIENT_ID'),
                    client_secret=os.getenv('SPOTIFY_CLIENT_SECRET')
                ))
                
                # Extract track ID from URL
                track_id = url.split('/')[-1].split('?')[0]
                self.logger.info(f"Extracted Spotify track ID: {track_id}")
                
                # Get track info
                track_info = sp.track(track_id)
                artist = track_info['artists'][0]['name']
                title = track_info['name']
                search_query = f"{artist} - {title}"
                self.logger.info(f"Generated search query: {search_query}")
                
                ydl_opts = {
                    'default_search': 'ytsearch',
                    'quiet': False,
                    'cookiefile': COOKIES_FILE,
                    'format': 'bestaudio/best',
                    'extract_audio': True,
                    'audio_format': 'opus',
                    'audio_quality': 192,
                }
                
                with youtube_dl.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(search_query, download=False)
                    if not info or 'entries' not in info or not info['entries']:
                        raise Exception("No YouTube results found for Spotify track")
                    url = info['entries'][0]['webpage_url']
                    self.logger.info(f"Found YouTube URL: {url}")
                    
            return url
        except Exception as e:
            self.logger.error(f"Error handling Spotify URL: {str(e)}")
            raise

    async def extract_info(self, ctx, url):
        self.logger.info(f"Attempting to extract info from URL: {url}")
        
        # Clean and validate URL
        url = url.strip()
        if not validators.url(url):
            await ctx.respond(content="Invalid URL provided. Please check the URL and try again.", ephemeral=True)
            return None

        ydl = youtube_dl.YoutubeDL({
            'cookiefile': COOKIES_FILE,
            'quiet': False,  # Enable logging
            'no_warnings': False,  # Show warnings
            'extract_flat': False,  # Get full info
            'nocheckcertificate': True,
            'ignoreerrors': False,
            'logtostderr': True,  # Enable stderr logging
            'no_color': True,
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'socket_timeout': 30,
            'retries': 3,
            'fragment_retries': 3,
            'file_access_retries': 3,
            'extractor_retries': 3,
            # Remove format restrictions for initial extraction
        })
        
        try:
            self.logger.info("Starting info extraction...")
            info = ydl.extract_info(url, download=False)
            
            if not info:
                self.logger.error("No info extracted from URL")
                await ctx.respond(content="Could not extract information from the URL. Please check if the video is available and try again.", ephemeral=True)
                return None
                
            self.logger.info(f"Successfully extracted info for: {info.get('title', 'Unknown Title')}")
            return info
            
        except youtube_dl.DownloadError as e:
            self.logger.error(f"DownloadError during extraction: {str(e)}")
            await ctx.respond(content=f"Error extracting info from the URL: {str(e)}. Please check if the video is available and try again.", ephemeral=True)
            return None
        except Exception as e:
            self.logger.error(f"Unexpected error during extraction: {str(e)}")
            await ctx.respond(content=f"Unexpected error while extracting info: {str(e)}", ephemeral=True)
            return None

    async def prepare_download(self, ctx, info, begin, end):
        if end is None:
            end = info['duration']
        title = str(info['title'])  # Fixed syntax for type conversion
        self.logger.debug(f"Type of title before modification: {type(title)}")

        title += f"_{uuid.uuid4()}"

        try:
            begin = float(begin)
            end = float(end)
        except ValueError:
            await ctx.respond(content="Invalid begin or end time.", ephemeral=True)
            return None, None, None

        if begin < 0.0:
            await ctx.respond(content="Begin time cannot be negative.", ephemeral=True)
            return None, None, None

        if end < 0.0:
            await ctx.respond(content="End time cannot be negative.", ephemeral=True)
            return None, None, None

        if begin > end:
            await ctx.respond(content="Begin time cannot be greater than end time.", ephemeral=True)
            return None, None, None

        if end > info['duration']:
            await ctx.respond(content="End time cannot be greater than the duration of the audio.", ephemeral=True)
            return None, None, None

        return title, begin, end

    async def download_audio(self, ctx, url, title):
        ydl_opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio/best',  # More flexible format selection
            'outtmpl': f'{title}.%(ext)s',
            'restrictfilenames': True,
            'noplaylist': True,
            'quiet': False,  # Enable logging for debugging
            'no_warnings': False,
            'nooverwrites': True,
            'cookiefile': COOKIES_FILE,
            'socket_timeout': 30,
            'retries': 3,
            'fragment_retries': 3,
            'file_access_retries': 3,
            'extractor_retries': 3,
            'ignoreerrors': True,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'opus',
                'preferredquality': '192',
            }],
            'verbose': True,  # Add verbose output for debugging
        }
        loop = asyncio.get_event_loop()
        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            try:
                self.logger.info(f"Starting download for URL: {url}")
                await loop.run_in_executor(None, lambda: ydl.download([url]))
                
                # Check if the file was actually created
                if not os.path.exists(f'{title}.opus'):
                    self.logger.error(f"Download completed but file {title}.opus not found")
                    await ctx.respond(content="Failed to download the audio file. Please try again.", ephemeral=True)
                    return False
                    
                self.logger.info(f"Successfully downloaded and converted audio to {title}.opus")
                return True
                
            except youtube_dl.DownloadError as e:
                self.logger.error(f"DownloadError during download: {str(e)}")
                await ctx.respond(content=f"Error downloading the audio file: {str(e)}", ephemeral=True)
                return False
            except Exception as e:
                self.logger.error(f"Unexpected error during download: {str(e)}")
                await ctx.respond(content=f"Unexpected error while downloading: {str(e)}", ephemeral=True)
                return False

    async def trim_audio(self, ctx, title, begin, end):
        try:
            if not os.path.exists(f'{title}.opus'):
                await ctx.respond(content="The downloaded audio file is missing. Please try again.", ephemeral=True)
                return False

            ffmpeg_cmd = ['ffmpeg', '-i', f'{title}.opus', '-ab', '189k', '-ss', str(begin), '-t', str(end - begin), '-acodec', 'libopus', f'{title}.ogg']
            process = await asyncio.create_subprocess_exec(*ffmpeg_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                error_msg = stderr.decode() if stderr else "Unknown error"
                await ctx.respond(content=f"Error trimming the audio file: {error_msg}", ephemeral=True)
                return False

            if not os.path.exists(f'{title}.ogg'):
                await ctx.respond(content="Failed to create the trimmed audio file. Please try again.", ephemeral=True)
                return False

            return True
        except Exception as e:
            await ctx.respond(content=f"Unexpected error while trimming audio: {str(e)}", ephemeral=True)
            return False

    async def send_audio(self, ctx, title):
        try:
            filepath = f'{title}.ogg'
            self.logger.debug(f"Calculated title: {title}")
            
            if not os.path.exists(filepath):
                await ctx.respond(content="Error finding the audio file.", ephemeral=True)
                return

            # Check file size
            file_size = os.path.getsize(filepath)
            if file_size > 25 * 1024 * 1024:  # 25MB limit
                await ctx.respond(content="The audio file is too large to send. Please try a shorter clip.", ephemeral=True)
                return

            # Step 2: Send the message with the uploaded file
            audio = AudioSegment.from_ogg(filepath)
            duration_secs = round(len(audio) / 1000.0, 2)

            samples = audio.get_array_of_samples()
            step = max(1, len(samples) // 100)
            waveform = [abs(samples[i]) for i in range(0, len(samples), step)]
            max_val = max(waveform) if waveform else 1
            waveform = [int((val / max_val) * 255) for val in waveform]
            waveform = waveform[:100]
            waveform_data = base64.b64encode(bytes(waveform)).decode('utf-8')
            
            await ctx.respond(file=discord.VoiceMessage(f'{title}.ogg', waveform=waveform_data, duration_secs=duration_secs, filename="voice-message.ogg", description="some song idk"))
            # send another message stating the title of the song above
            title_msg = title.split("_")[:-1]
            title_msg = "_".join(title_msg)
            await ctx.followup.send(content=f"**{title_msg}**")
        except Exception as e:
            await ctx.respond(content=f"Error sending the audio file: {str(e)}", ephemeral=True)
        finally:
            # Clean up files
            for extension in ['.opus', '.ogg']:
                try:
                    audio_file = Path(f'{title}{extension}')
                    if audio_file.is_file():
                        audio_file.unlink()
                except Exception as e:
                    self.logger.error(f"Error deleting file {title}{extension}: {str(e)}")

    @ytdlp_cog.command(integration_types={discord.IntegrationType.guild_install, discord.IntegrationType.user_install}, name="get-yt-link", description="Get the Youtube link based of a link to some music e.g. Spotify link")
    async def get_yt_link(self, ctx: discord.ApplicationContext, 
                    url: str = discord.Option(name="url", description="The link to the music", required=True),
                    ephemeral: str = discord.Option(name="ephemeral", description="Whether to send the response as an ephemeral message", required=False, default="True", choices=["True", "False"])):
        """
        Command to get the Youtube link based of a link to some music e.g. Spotify link
        """
        if not await self.is_user_allowed(ctx.author):
            await ctx.respond(content="You are not allowed to use this command.", ephemeral=True)
            return
        ephemeral = True if ephemeral == "True" else False

        self.logger.info(f"{ctx.author} used /get-yt-link command in {ctx.channel} on {ctx.guild} with URL: {url} and ephemeral: {ephemeral}.")
        
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

def setup(bot):
    bot.add_cog(YoutubeDLPCog(bot))