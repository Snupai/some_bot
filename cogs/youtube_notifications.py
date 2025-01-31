import discord
from discord.ext import commands, tasks
import sqlite3
from datetime import datetime, timedelta
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import asyncio
from utils.youtube_helpers import YouTubeRateLimiter, YouTubeCache, safe_api_call
import os
import logging

class YouTubeNotifications(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger('bot.py')
        self.db = sqlite3.connect('youtube_notifications.sqlite')
        self.cursor = self.db.cursor()
        self.create_tables()
        self.youtube = build('youtube', 'v3', developerKey=os.getenv('YOUTUBE_DATA_API_KEY'))
        self.rate_limiter = YouTubeRateLimiter()
        self.cache = YouTubeCache()
        self.check_new_videos.start()

    yt_commands=discord.SlashCommandGroup("yt", "YouTube notifications commands")

    def create_tables(self):
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS youtube_subscriptions (
                guild_id INTEGER,
                youtube_channel_id TEXT,
                discord_channel_id INTEGER,
                last_video_id TEXT,
                notification_count INTEGER DEFAULT 0,
                ping_role_id INTEGER
            )
        ''')
        self.db.commit()

    async def fetch_latest_video(self, channel_id):
        # Check cache first
        cached_result = self.cache.get(f"latest_video_{channel_id}")
        if cached_result:
            return cached_result

        await self.rate_limiter.wait_if_needed()
        
        try:
            request = self.youtube.search().list(
                part='id',
                channelId=channel_id,
                order='date',
                maxResults=1,
                type='video'
            )
            response = safe_api_call(request)
            
            if response.get('items'):
                video_id = response['items'][0]['id']['videoId']
                
                # Fetch additional video details
                video_request = self.youtube.videos().list(
                    part='snippet,player',
                    id=video_id
                )
                video_response = safe_api_call(video_request)
                
                if video_response.get('items'):
                    video_data = video_response['items'][0]['snippet']
                    # Get best available thumbnail
                    thumbnails = video_data['thumbnails']
                    thumbnail_url = None
                    # Try thumbnails in order of quality
                    for quality in ['maxresdefault', 'high', 'medium', 'default']:
                        if quality in thumbnails:
                            thumbnail_url = thumbnails[quality]['url']
                            break
                    
                    video_info = {
                        'id': video_id,
                        'title': video_data['title'],
                        'description': video_data['description'],
                        'thumbnail': thumbnail_url or '',  # Use empty string if no thumbnail found
                        'channel_name': video_data['channelTitle'],
                        'channel_url': f"https://www.youtube.com/channel/{video_data['channelId']}"
                    }
                    self.cache.set(f"latest_video_{channel_id}", video_info)
                    return video_info
            return None
            
        except Exception as e:
            self.bot.logger.error(f"Error fetching latest video: {str(e)}")
            return None

    def get_channel_id_from_url(self, url):
        try:
            # Extract channel identifier from URL
            if 'youtube.com/' in url:
                path = url.split('youtube.com/')[-1]
                identifier = path.split('/')[-1]
            else:
                identifier = url

            # Try username first
            try:
                request = self.youtube.channels().list(
                    part='id',
                    forUsername=identifier
                )
                response = safe_api_call(request)
                
                if response.get('items'):
                    return response['items'][0]['id']
            except HttpError:
                pass

            # Try channel ID
            try:
                request = self.youtube.channels().list(
                    part='id',
                    id=identifier
                )
                response = safe_api_call(request)
                
                if response.get('items'):
                    return response['items'][0]['id']
            except HttpError:
                pass

            # Try handle
            try:
                request = self.youtube.channels().list(
                    part='id',
                    forHandle=identifier
                )
                response = safe_api_call(request)
                
                if response.get('items'):
                    return response['items'][0]['id']
            except HttpError:
                pass

            return None
            
        except HttpError as e:
            if e.resp.status == 403:
                raise Exception("YouTube API quota exceeded")
            raise Exception(f"YouTube API error: {str(e)}")

    @yt_commands.command(name="get-yt-notification", description="Subscribe to a YouTube channel's notifications")
    async def get_yt_notification(
        self, 
        ctx: discord.ApplicationContext, 
        yt_channel: str = discord.Option(str, "YouTube channel URL", required=True),
        dc_channel: discord.TextChannel = discord.Option(discord.TextChannel, "Discord channel for notifications", required=True),
        ping_role: discord.Role = discord.Option(discord.Role, "Role to ping for notifications", required=False)
    ):
        try:
            channel_id = self.get_channel_id_from_url(yt_channel)
            if not channel_id:
                await ctx.respond("Invalid YouTube channel URL!")
                return

            latest_video = await self.fetch_latest_video(channel_id)
            
            self.cursor.execute('''
                INSERT INTO youtube_subscriptions 
                (guild_id, youtube_channel_id, discord_channel_id, last_video_id, ping_role_id)
                VALUES (?, ?, ?, ?, ?)
            ''', (ctx.guild.id, channel_id, dc_channel.id, latest_video['id'], ping_role.id if ping_role else None))
            self.db.commit()
            
            self.cache.add_channel_subscriber(channel_id, ctx.guild.id)
            
            response = f"Successfully subscribed to notifications in {dc_channel.mention}"
            if ping_role:
                response += f" with notifications pinging {ping_role.mention}"
            await ctx.respond(response)
            
        except Exception as e:
            await ctx.respond(f"Error: {str(e)}")

    @yt_commands.command(name="remove-yt-notification", description="Unsubscribe from a YouTube channel's notifications")
    async def remove_yt_notification(self, ctx: discord.ApplicationContext):
        self.cursor.execute('''
            SELECT youtube_channel_id FROM youtube_subscriptions 
            WHERE guild_id = ?
        ''', (ctx.guild.id,))
        channels = self.cursor.fetchall()

        if not channels:
            await ctx.respond("No YouTube channels are currently subscribed!")
            return

        options = []
        for channel in channels:
            try:
                response = self.youtube.channels().list(
                    part='snippet',
                    id=channel[0]
                ).execute()
                channel_name = response['items'][0]['snippet']['title']
                options.append(discord.SelectOption(label=channel_name, value=channel[0]))
            except HttpError:
                options.append(discord.SelectOption(label=channel[0], value=channel[0]))

        select = discord.ui.Select(placeholder="Choose a channel to unsubscribe", options=options)
        
        async def select_callback(interaction):
            channel_id = select.values[0]
            self.cursor.execute('''
                DELETE FROM youtube_subscriptions 
                WHERE guild_id = ? AND youtube_channel_id = ?
            ''', (ctx.guild.id, channel_id))
            self.db.commit()
            
            self.cache.remove_channel_subscriber(channel_id, ctx.guild.id)
            await interaction.response.send_message("Successfully unsubscribed!")

        select.callback = select_callback
        view = discord.ui.View()
        view.add_item(select)
        await ctx.respond("Select a channel to unsubscribe:", view=view)

    @yt_commands.command(name="list-yt-notifications", description="List all subscribed YouTube channels")
    async def list_yt_notifications(self, ctx: discord.ApplicationContext):
        self.cursor.execute('''
            SELECT youtube_channel_id, discord_channel_id 
            FROM youtube_subscriptions 
            WHERE guild_id = ?
        ''', (ctx.guild.id,))
        subscriptions = self.cursor.fetchall()

        if not subscriptions:
            await ctx.respond("No active YouTube subscriptions!")
            return

        embed = discord.Embed(title="YouTube Subscriptions", color=discord.Color.red())
        
        for yt_id, dc_id in subscriptions:
            try:
                response = self.youtube.channels().list(
                    part='snippet',
                    id=yt_id
                ).execute()
                channel_name = response['items'][0]['snippet']['title']
                dc_channel = self.bot.get_channel(dc_id)
                embed.add_field(
                    name=channel_name,
                    value=f"Notifications in: {dc_channel.mention}",
                    inline=False
                )
            except HttpError:
                continue

        await ctx.respond(embed=embed)

    @yt_commands.command(name="stats-yt-notifications", description="Show notification statistics")
    async def stats_yt_notifications(self, ctx: discord.ApplicationContext):
        self.cursor.execute('''
            SELECT youtube_channel_id, notification_count 
            FROM youtube_subscriptions 
            WHERE guild_id = ?
        ''', (ctx.guild.id,))
        stats = self.cursor.fetchall()

        if not stats:
            await ctx.respond("No YouTube statistics available!")
            return

        embed = discord.Embed(title="YouTube Notification Statistics", color=discord.Color.blue())
        
        for yt_id, count in stats:
            try:
                response = self.youtube.channels().list(
                    part='snippet',
                    id=yt_id
                ).execute()
                channel_name = response['items'][0]['snippet']['title']
                embed.add_field(
                    name=channel_name,
                    value=f"Notifications sent: {count}",
                    inline=False
                )
            except HttpError:
                continue

        await ctx.respond(embed=embed)

    @yt_commands.command(name="last-video", description="Get the last uploaded video from a YouTube channel")
    async def last_video(
        self, 
        ctx: discord.ApplicationContext,
        yt_channel: str = discord.Option(str, "YouTube channel URL", required=True)
    ):
        try:
            channel_id = self.get_channel_id_from_url(yt_channel)
            if not channel_id:
                await ctx.respond("Invalid YouTube channel URL!")
                return

            video_info = await self.fetch_latest_video(channel_id)
            if not video_info:
                await ctx.respond("Couldn't find any videos for this channel!")
                return

            video_url = f"https://www.youtube.com/watch?v={video_info['id']}"
            message_content = f"### {video_info['title']}\n{video_url}"
            
            await ctx.respond(content=message_content)

        except Exception as e:
            await ctx.respond(f"Error: {str(e)}")

    @tasks.loop(minutes=5)
    async def check_new_videos(self):
        self.cursor.execute('SELECT DISTINCT youtube_channel_id FROM youtube_subscriptions')
        unique_channels = self.cursor.fetchall()

        for (channel_id,) in unique_channels:
            try:
                video_info = await self.fetch_latest_video(channel_id)
                if not video_info:
                    continue

                self.cursor.execute('''
                    SELECT guild_id, discord_channel_id, last_video_id, ping_role_id
                    FROM youtube_subscriptions 
                    WHERE youtube_channel_id = ?
                ''', (channel_id,))
                subscriptions = self.cursor.fetchall()

                for guild_id, discord_channel_id, last_video_id, ping_role_id in subscriptions:
                    if video_info['id'] != last_video_id:
                        channel = self.bot.get_channel(discord_channel_id)
                        if channel:
                            video_url = f"https://www.youtube.com/watch?v={video_info['id']}"
                            message_content = f"### {video_info['title']}\n{video_url}"
                            
                            # Add role mention if configured
                            if ping_role_id:
                                message_content = f"||<@&{ping_role_id}>||\n{message_content}"
                            
                            await channel.send(content=message_content)
                            
                            self.cursor.execute('''
                                UPDATE youtube_subscriptions 
                                SET last_video_id = ?, notification_count = notification_count + 1 
                                WHERE guild_id = ? AND youtube_channel_id = ?
                            ''', (video_info['id'], guild_id, channel_id))
                            self.db.commit()

            except Exception as e:
                self.logger.error(f"Error checking videos: {str(e)}")
                continue

            await asyncio.sleep(1)

    @check_new_videos.before_loop
    async def before_check_new_videos(self):
        await self.bot.wait_until_ready()

    def cog_unload(self):
        self.check_new_videos.cancel()
        self.db.close()

def setup(bot):
    bot.add_cog(YouTubeNotifications(bot))
