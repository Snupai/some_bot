import discord
from discord import default_permissions
from discord.ext import commands, tasks
import feedparser
import sqlite3
import logging

class RSSFeed(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger('bot.py')
        self.db_path = 'rss_feed.sqlite'
        self.initialize_database()
        self.check_feeds.start()

    def cog_unload(self):
        self.check_feeds.cancel()

    def initialize_database(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS feed_channels (
                guild_id TEXT PRIMARY KEY,
                channel_id INTEGER
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS feeds (
                guild_id TEXT,
                name TEXT,
                url TEXT,
                last_entry TEXT,
                PRIMARY KEY (guild_id, name)
            )
        ''')

        conn.commit()
        conn.close()
        self.logger.debug("Database initialized")

    def get_connection(self):
        self.logger.debug("Getting database connection")
        return sqlite3.connect(self.db_path)

    rss = discord.SlashCommandGroup(name="rss", description="Manage RSS feeds")

    @rss.command(integration_types={discord.IntegrationType.guild_install}, name="add_feed", description="Add a new RSS feed to monitor")
    @default_permissions(administrator=True)
    async def add_feed(self, ctx, name: discord.Option(str, "Name of the feed"), url: discord.Option(str, "URL of the RSS feed")):
        guild_id = str(ctx.guild.id)
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('INSERT OR REPLACE INTO feeds (guild_id, name, url, last_entry) VALUES (?, ?, ?, ?)',
                       (guild_id, name, url, None))
        conn.commit()
        conn.close()

        self.logger.info(f"Added RSS feed '{name}' with URL: {url} for guild {guild_id}")
        await ctx.respond(f"Added RSS feed '{name}' with URL: {url}")

    @rss.command(integration_types={discord.IntegrationType.guild_install}, name="remove_feed", description="Remove an RSS feed from monitoring")
    @default_permissions(administrator=True)
    async def remove_feed(self, ctx, name: discord.Option(str, "Name of the feed to remove")):
        guild_id = str(ctx.guild.id)
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('DELETE FROM feeds WHERE guild_id = ? AND name = ?', (guild_id, name))
        if cursor.rowcount > 0:
            conn.commit()
            self.logger.info(f"Removed RSS feed '{name}' for guild {guild_id}")
            await ctx.respond(f"Removed RSS feed '{name}'")
        else:
            self.logger.warning(f"No RSS feed found with name '{name}' for guild {guild_id}")
            await ctx.respond(f"No RSS feed found with name '{name}'")

        conn.close()

    @rss.command(integration_types={discord.IntegrationType.guild_install}, name="set_feed_channel", description="Set the channel for RSS feed updates")
    @default_permissions(administrator=True)
    async def set_feed_channel(self, ctx, channel: discord.Option(discord.TextChannel, "The channel to send RSS updates to")):
        guild_id = str(ctx.guild.id)
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('INSERT OR REPLACE INTO feed_channels (guild_id, channel_id) VALUES (?, ?)',
                       (guild_id, channel.id))
        conn.commit()
        conn.close()

        self.logger.info(f"Set RSS feed channel to {channel.id} for guild {guild_id}")
        await ctx.respond(f"RSS feed updates will now be sent to {channel.mention}")

    @rss.command(integration_types={discord.IntegrationType.guild_install}, name="list_feeds", description="List all current RSS feeds")
    async def list_feeds(self, ctx):
        guild_id = str(ctx.guild.id)
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('SELECT name, url FROM feeds WHERE guild_id = ?', (guild_id,))
        feeds = cursor.fetchall()
        conn.close()

        if not feeds:
            self.logger.info(f"No RSS feeds found for guild {guild_id}")
            await ctx.respond("No RSS feeds are currently set up.")
        else:
            feed_list = "\n".join([f"{name}: {url}" for name, url in feeds])
            self.logger.info(f"Listing RSS feeds for guild {guild_id}: {feed_list}")
            await ctx.respond(f"Current RSS feeds:\n{feed_list}")
    
    def get_feed_names(self):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT name FROM feeds')
        feed_names = cursor.fetchall()
        conn.close()
        self.logger.debug("Fetched feed names from database")
        return [name[0] for name in feed_names]

    @rss.command(integration_types={discord.IntegrationType.guild_install}, name="get_last_feed", description="Get the last post from an RSS feed")
    async def get_last_feed(self, ctx, name: discord.Option(str, "Name of the feed")):
        if name not in self.get_feed_names():
            self.logger.warning(f"No RSS feed found with name '{name}' for guild {ctx.guild.id}")
            await ctx.respond(f"No RSS feed found with name '{name}' in this server.\nCheck the list_feeds command to see the available feeds.")
            return
        guild_id = str(ctx.guild.id)
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('SELECT last_entry, url FROM feeds WHERE guild_id = ? AND name = ?', (guild_id, name))
        result = cursor.fetchone()
        last_entry, url = result if result else (None, None)

        if not url:
            self.logger.warning(f"No RSS feed found with name '{name}' for guild {guild_id}")
            await ctx.respond(f"No RSS feed found with name '{name}'")
            conn.close()
            return
        
        parsed_feed = feedparser.parse(url)
        if not parsed_feed.entries:
            self.logger.info(f"No entries found in the feed '{name}' for guild {guild_id}")
            await ctx.respond(f"No entries found in the feed '{name}'")
            conn.close()
            return

        if not last_entry:
            latest_entry = parsed_feed.entries[0]
            self.logger.info(f"Latest post from '{name}' for guild {guild_id}: {latest_entry.title}")
            await ctx.respond(f"Latest post from '{name}':\n**{latest_entry.title}**\n{latest_entry.link}")
        else:
            latest_entry = next((entry for entry in parsed_feed.entries if entry.id == last_entry), None)
            if latest_entry:
                self.logger.info(f"Last post from '{name}' for guild {guild_id}: {latest_entry.title}")
                await ctx.respond(f"Last post from '{name}':\n**{latest_entry.title}**\n{latest_entry.link}")
            else:
                latest_entry = parsed_feed.entries[0]
                self.logger.info(f"Latest post from '{name}' for guild {guild_id}: {latest_entry.title}")
                await ctx.respond(f"Latest post from '{name}':\n**{latest_entry.title}**\n{latest_entry.link}")

        conn.close()

    @tasks.loop(minutes=5)
    async def check_feeds(self):
        self.logger.debug("Checking feeds for updates")
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('SELECT guild_id, channel_id FROM feed_channels')
        channels = {row[0]: row[1] for row in cursor.fetchall()}

        for guild_id, channel_id in channels.items():
            channel = self.bot.get_channel(channel_id)
            if not channel:
                self.logger.warning(f"Channel {channel_id} not found for guild {guild_id}")
                continue

            cursor.execute('SELECT name, url, last_entry FROM feeds WHERE guild_id = ?', (guild_id,))
            feeds = cursor.fetchall()

            for name, url, last_entry in feeds:
                parsed_feed = feedparser.parse(url)
                if not parsed_feed.entries:
                    self.logger.info(f"No entries found in the feed '{name}' for guild {guild_id}")
                    continue

                latest_entry = parsed_feed.entries[0]
                if last_entry != latest_entry.id:
                    cursor.execute('UPDATE feeds SET last_entry = ? WHERE guild_id = ? AND name = ?',
                                   (latest_entry.id, guild_id, name))
                    await channel.send(
                        f"New post in '{name}':\n"
                        f"**{latest_entry.title}**\n"
                        f"{latest_entry.link}"
                    )
                    self.logger.info(f"New post in '{name}' for guild {guild_id}: {latest_entry.title}")

        conn.commit()
        conn.close()

    @check_feeds.before_loop
    async def before_check_feeds(self):
        await self.bot.wait_until_ready()
        self.logger.debug("Bot is ready, starting feed check loop")

def setup(bot):
    bot.add_cog(RSSFeed(bot))
