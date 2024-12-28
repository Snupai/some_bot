import discord
from discord import default_permissions, Option, TextChannel, IntegrationType, SlashCommandGroup
from discord.ext import commands, tasks
import feedparser
import sqlite3
import logging
import requests
from urllib.parse import urlparse
from bs4 import BeautifulSoup

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

    async def is_user_allowed(self, user):
        # check the allowed_users.sqlite file for the user
        conn = sqlite3.connect('allowed_users.sqlite')
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM allowed_users WHERE user_id = ?', (user.id,))
        result = cursor.fetchone()
        if result:
            return True
        return False
    
    rss = SlashCommandGroup(integration_types={IntegrationType.guild_install}, name="rss", description="Manage RSS feeds")

    @rss.command(integration_types={IntegrationType.guild_install}, name="add_feed", description="Add a new RSS feed to monitor")
    @default_permissions(administrator=True)
    async def add_feed(self, ctx, name: Option(str, "Name of the feed"), url: Option(str, "URL of the RSS feed")):
        if not await self.is_user_allowed(ctx.author):
            await ctx.respond(content="You are not allowed to use this command.", ephemeral=True)
            return
        guild_id = str(ctx.guild.id)
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('INSERT OR REPLACE INTO feeds (guild_id, name, url, last_entry) VALUES (?, ?, ?, ?)',
                       (guild_id, name, url, None))
        conn.commit()
        conn.close()

        self.logger.info(f"Added RSS feed '{name}' with URL: {url} for guild {guild_id}")
        await ctx.respond(f"Added RSS feed '{name}' with URL: {url}")

    @rss.command(integration_types={IntegrationType.guild_install}, name="remove_feed", description="Remove an RSS feed from monitoring")
    @default_permissions(administrator=True)
    async def remove_feed(self, ctx, name: Option(str, "Name of the feed to remove")):
        if not await self.is_user_allowed(ctx.author):
            await ctx.respond(content="You are not allowed to use this command.", ephemeral=True)
            return
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

    @rss.command(integration_types={IntegrationType.guild_install}, name="set_feed_channel", description="Set the channel for RSS feed updates")
    @default_permissions(administrator=True)
    async def set_feed_channel(self, ctx, channel: Option(TextChannel, "The channel to send RSS updates to")):
        if not await self.is_user_allowed(ctx.author):
            await ctx.respond(content="You are not allowed to use this command.", ephemeral=True)
            return
        guild_id = str(ctx.guild.id)
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('INSERT OR REPLACE INTO feed_channels (guild_id, channel_id) VALUES (?, ?)',
                       (guild_id, channel.id))
        conn.commit()
        conn.close()

        self.logger.info(f"Set RSS feed channel to {channel.id} for guild {guild_id}")
        await ctx.respond(f"RSS feed updates will now be sent to {channel.mention}")

    @rss.command(integration_types={IntegrationType.guild_install}, name="list_feeds", description="List all current RSS feeds")
    async def list_feeds(self, ctx):
        if not await self.is_user_allowed(ctx.author):
            await ctx.respond(content="You are not allowed to use this command.", ephemeral=True)
            return
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

    @rss.command(integration_types={IntegrationType.guild_install}, name="get_last_feed", description="Get the last post from an RSS feed")
    async def get_last_feed(self, ctx, name: Option(str, "Name of the feed")):
        if not await self.is_user_allowed(ctx.author):
            await ctx.respond(content="You are not allowed to use this command.", ephemeral=True)
            return
        if not await self.feed_exists(ctx, name):
            return

        guild_id = str(ctx.guild.id)
        last_entry, url = self.get_feed_info(guild_id, name)

        if not url:
            await ctx.respond(f"No RSS feed found with name '{name}'")
            return

        parsed_feed = feedparser.parse(url)
        if not parsed_feed.entries:
            self.logger.info(f"No entries found in the feed '{name}' for guild {guild_id}")
            await ctx.respond(f"No entries found in the feed '{name}'")
            return

        await self.respond_with_feed(ctx, name, last_entry, parsed_feed)

    async def feed_exists(self, ctx, name):
        if name not in self.get_feed_names():
            self.logger.warning(f"No RSS feed found with name '{name}' for guild {ctx.guild.id}")
            await ctx.respond(f"No RSS feed found with name '{name}' in this server.\nCheck the list_feeds command to see the available feeds.")
            return False
        return True

    def get_feed_info(self, guild_id, name):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT last_entry, url FROM feeds WHERE guild_id = ? AND name = ?', (guild_id, name))
        result = cursor.fetchone()
        conn.close()
        return result if result else (None, None)

    async def respond_with_feed(self, ctx, name, last_entry, parsed_feed: feedparser.FeedParserDict):
        guild_id = str(ctx.guild.id)
        if not last_entry:
            latest_entry = parsed_feed.entries[0]
        else:
            latest_entry = next((entry for entry in parsed_feed.entries if entry.id == last_entry), None)
            if not latest_entry:
                latest_entry = parsed_feed.entries[0]
        
        is_spiegel_plus = self.is_spiegel_plus(latest_entry.link)
        if is_spiegel_plus:
            latest_entry.title = f"(S+) {latest_entry.title}"

        embed = discord.Embed(
            title=latest_entry.title,
            url=latest_entry.link,
            description=latest_entry.description,
            color=discord.Color.blue()
        )
        
        if 'enclosures' in latest_entry:
            for enclosure in latest_entry.enclosures:
                if enclosure.type.startswith('image'):
                    embed.set_image(url=enclosure.href)
                    break

        embed.add_field(name="Category", value=latest_entry.get("category", []), inline=False)
        embed.add_field(name="Published Date", value=latest_entry.published, inline=False)
        embed.set_author(name=parsed_feed.feed.title)
        if is_spiegel_plus:
            embed.add_field(name="RemovePaywall", value=f"[Versuche es aus!](https://www.removepaywall.com/search?url={latest_entry.link})", inline=False)
        embed.set_footer(text=f"RSS Feed: {name}")

        self.logger.info(f"Sending post from '{name}' for guild {guild_id}: {latest_entry.title}")
        await ctx.respond(embed=embed)

    async def send_feed_update(self, channel, name, entry, parsed_feed: feedparser.FeedParserDict):
        is_spiegel_plus = self.is_spiegel_plus(entry.link)
        if is_spiegel_plus:
            entry.title = f"(S+) {entry.title}"

        embed = discord.Embed(
            title=entry.title,
            url=entry.link,
            description=entry.description,
            color=discord.Color.blue()
        )
        
        if 'enclosures' in entry:
            for enclosure in entry.enclosures:
                if enclosure.type.startswith('image'):
                    embed.set_image(url=enclosure.href)
                    break
        
        embed.add_field(name="Category", value=entry.get("category", []), inline=False)
        embed.add_field(name="Published Date", value=entry.published, inline=False)
        embed.set_author(name=parsed_feed.feed.title)
        if is_spiegel_plus:
            embed.add_field(name="RemovePaywall", value=f"[Versuche es aus!](https://www.removepaywall.com/search?url={entry.link})", inline=False)
        embed.set_footer(text=f"RSS Feed: {name}")

        await channel.send(embed=embed)

    def is_spiegel_article(self, article_url):
        try:
            # Parse the URL to check the domain
            parsed_url = urlparse(article_url)
            return 'spiegel.de' in parsed_url.netloc
        except Exception as e:
            print(f"Error checking URL validity: {e}")
            return False

    def is_spiegel_plus(self, article_url):
        if not self.is_spiegel_article(article_url):
            print("The provided URL is not a SPIEGEL article.")
            return None
        
        try:
            # Fetch the article page
            response = requests.get(article_url)
            response.raise_for_status()  # Raise an error if the request failed
            soup = BeautifulSoup(response.content, 'html.parser')

            # Find the <meta> tag with property="og:title"
            meta_tag = soup.find('meta', {'property': 'og:title'})
            if meta_tag and meta_tag.get('content', '').startswith('(S+)'):
                return True  # Indicates a SPIEGEL+ article

            return False  # Otherwise, it's likely free to read
        except Exception as e:
            print(f"Error checking article: {e}")
            return None

    @tasks.loop(minutes=5)
    async def check_feeds(self):
        self.logger.debug("Checking feeds for updates")
        conn = self.get_connection()
        cursor = conn.cursor()

        channels = self.get_channels(cursor)
        for guild_id, channel_id in channels.items():
            await self.process_guild_feeds(cursor, guild_id, channel_id)

        conn.commit()
        conn.close()

    def get_channels(self, cursor):
        cursor.execute('SELECT guild_id, channel_id FROM feed_channels')
        return {row[0]: row[1] for row in cursor.fetchall()}

    async def process_guild_feeds(self, cursor, guild_id, channel_id):
        channel = self.bot.get_channel(channel_id)
        if not channel:
            self.logger.warning(f"Channel {channel_id} not found for guild {guild_id}")
            return

        cursor.execute('SELECT name, url, last_entry FROM feeds WHERE guild_id = ?', (guild_id,))
        feeds = cursor.fetchall()

        for name, url, last_entry in feeds:
            await self.process_feed(cursor, guild_id, (name, url, last_entry), channel)

    async def process_feed(self, cursor, guild_id, feed, channel):
        name, url, last_entry = feed
        parsed_feed = feedparser.parse(url)
        if not parsed_feed.entries:
            self.logger.debug(f"No entries found in the feed '{name}' for guild {guild_id}")
            return

        new_entries = self.get_new_entries(parsed_feed, last_entry)
        if new_entries:
            cursor.execute('UPDATE feeds SET last_entry = ? WHERE guild_id = ? AND name = ?',
                           (new_entries[0].id, guild_id, name))
            for entry in reversed(new_entries):
                await self.send_feed_update(channel, name, entry, parsed_feed)
                self.logger.debug(f"New post in '{name}' for guild {guild_id}: {entry.title}")

    def get_new_entries(self, parsed_feed, last_entry):
        new_entries = []
        for entry in parsed_feed.entries:
            if entry.id == last_entry:
                break
            new_entries.append(entry)
        return new_entries

    @check_feeds.before_loop
    async def before_check_feeds(self):
        await self.bot.wait_until_ready()
        self.logger.debug("Bot is ready, starting feed check loop")

def setup(bot):
    bot.add_cog(RSSFeed(bot))
