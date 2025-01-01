import discord
from discord import default_permissions, Option, TextChannel, IntegrationType, SlashCommandGroup
from discord.ext import commands, tasks
import feedparser
import sqlite3
import logging
import hashlib
from urllib.parse import urlparse
import requests
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
            CREATE TABLE IF NOT EXISTS FeedForwards (
                subscription_id TEXT,
                message_id TEXT,
                FOREIGN KEY (subscription_id) REFERENCES FeedSubscription(id),
                FOREIGN KEY (message_id) REFERENCES RssMessage(id),
                PRIMARY KEY (subscription_id, message_id)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS RssMessage (
                id TEXT PRIMARY KEY,
                feed_id TEXT,
                title TEXT,
                link TEXT,
                description TEXT,
                enclosure_href TEXT,
                category TEXT,
                pub_date TEXT,
                FOREIGN KEY (feed_id) REFERENCES RssFeed(id)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS FeedSubscription (
                id TEXT PRIMARY KEY,
                feed_id TEXT,
                guild_channel_id TEXT,
                name TEXT,
                FOREIGN KEY (feed_id) REFERENCES RssFeed(id),
                FOREIGN KEY (guild_channel_id) REFERENCES GuildChannel(id)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS RssFeed (
            id TEXT PRIMARY KEY,
            rss_feed_url TEXT
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS GuildChannel (
                id TEXT PRIMARY KEY,
                discord_channel_id TEXT,
                discord_guild_id TEXT
            )
        ''')

        conn.commit()
        conn.close()
        self.logger.debug("Database initialized")

    async def is_user_allowed(self, user):
        # check the allowed_users.sqlite file for the user
        conn = sqlite3.connect('allowed_users.sqlite')
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM allowed_users WHERE user_id = ?', (user.id,))
        result = cursor.fetchone()
        conn.close()
        if result:
            return True
        return False
    
    def get_hash(self, string: str):
        return hashlib.md5(string.encode()).hexdigest()
        
    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        return conn
        
    rss = SlashCommandGroup(integration_types={discord.IntegrationType.guild_install}, name="rss", description="Manage RSS feeds")

    def add_entry_to_database(self, table, id, **kwargs):
        conn = self.get_connection()
        cursor = conn.cursor()
        # check if the id exists in the table
        cursor.execute(f'SELECT id FROM {table} WHERE id = ?', (id,))
        result = cursor.fetchone()
        if result:
            self.logger.debug(f"Entry with id {id} already exists in the {table} table")
            conn.close()
            return id

        columns = ', '.join(kwargs.keys())
        placeholders = ', '.join('?' * len(kwargs))
        values = tuple(kwargs.values())
        cursor.execute(f'INSERT OR REPLACE INTO {table} (id, {columns}) VALUES (?, {placeholders})', (id, *values))
        conn.commit()
        conn.close()
        self.logger.debug(f"Entry with id {id} added to the {table} table")
        return id

    def add_feed_to_database(self, url):
        id = self.get_hash(str(url))
        return self.add_entry_to_database('RssFeed', id, rss_feed_url=url)

    def add_guild_channel_to_database(self, guild_id, channel_id):
        id = self.get_hash(str(channel_id) + str(guild_id))
        return self.add_entry_to_database('GuildChannel', id, discord_channel_id=channel_id, discord_guild_id=guild_id)

    def add_feed_subscription_to_database(self, name, feed_id, guild_channel_id):
        id = self.get_hash(str(feed_id) + str(guild_channel_id))
        return self.add_entry_to_database('FeedSubscription', id, feed_id=feed_id, guild_channel_id=guild_channel_id, name=name)

    def check_feed_channel_exists_go(self, guild_id):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM GuildChannel WHERE discord_guild_id = ?', (guild_id,))
        result = cursor.fetchone()
        conn.close()
        return result

    @rss.command(integration_types={IntegrationType.guild_install}, name="add_feed", description="Add a new RSS feed to monitor")
    @default_permissions(administrator=True)
    async def add_feed(self, ctx: discord.ApplicationContext, 
                       name: Option(str, "Name of the feed"), 
                       url: Option(str, "URL of the RSS feed"), 
                       notification_role: Option(discord.Role, "Role to send notifications to", required=False)):
        
        if not await self.is_user_allowed(ctx.author):
            await ctx.respond(content="You are not allowed to use this command.", ephemeral=True)
            return
        await ctx.defer()
        # Add a new feed to the database
        feed_table_id = self.add_feed_to_database(url)
        # Add a new GuildChannel entry to the database
        guild_channel_table_id = self.check_feed_channel_exists_go(ctx.guild.id)
        if not guild_channel_table_id:
            await ctx.respond(f"No feed channel found for guild {ctx.guild.id}. Please set a feed channel using the set_feed_channel command.")
            return
        guild_channel_table_id = guild_channel_table_id[0]
        # Add a new FeedSubscription entry to the database
        feed_subscription_table_id = self.add_feed_subscription_to_database(name, feed_table_id, guild_channel_table_id)
        self.logger.debug(f"FeedSubscription '{feed_subscription_table_id}' with name '{name}', feed_id '{feed_table_id}' and guild_channel_id '{guild_channel_table_id}' added to the database")
        await ctx.respond(f"Added FeedSubscription '{name}' ({url}) to the database")

    def remove_feed_subscription_from_database(self, name: str, guild_id: str):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM FeedSubscription WHERE name = ?', (name,))
        result = cursor.fetchone()
        if not result:
            self.logger.debug(f"No FeedSubscription found with name '{name}'")
            conn.close()
            return None
        id = result[0]
        feed_id = result[1]
        guild_channel_id = result[2]
        # check if the guild_id matches the guild_id in the GuildChannel table under the guild_channel_id
        cursor.execute('SELECT discord_guild_id FROM GuildChannel WHERE id = ?', (guild_channel_id,))
        result = cursor.fetchone()
        if not result:
            self.logger.debug(f"No GuildChannel found with id '{guild_channel_id}'")
            conn.close()
            return None
        discord_guild_id: str = result[2]
        if discord_guild_id != guild_id:
            self.logger.debug(f"GuildChannel with id '{guild_channel_id}' does not belong to guild '{guild_id}'")
            conn.close()
            return None
        cursor.execute('DELETE FROM FeedSubscription WHERE id = ?', (id,))
        conn.commit()
        conn.close()
        self.logger.debug(f"FeedSubscription with id '{id}' removed from the database")
        return id

    @rss.command(integration_types={IntegrationType.guild_install}, name="remove_feed", description="Remove an RSS feed from monitoring")
    @default_permissions(administrator=True)
    async def remove_feed(self, ctx: discord.ApplicationContext, 
                       name: Option(str, "Name of the feed to remove")):
        if not await self.is_user_allowed(ctx.author):
            await ctx.respond(content="You are not allowed to use this command.", ephemeral=True)
            return
        await ctx.defer()

        # Remove the FeedSubscription entry from the database
        feed_subscription_table_id = self.remove_feed_subscription_from_database(name)
        if not feed_subscription_table_id:
            await ctx.respond(f"No FeedSubscription found with name '{name}' for guild {ctx.guild.id}")
            return
        await ctx.respond(f"Removed FeedSubscription '{name}' from the database")

    def check_feed_channel_exists(self, guild_id, channel_id):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM GuildChannel WHERE discord_guild_id = ? AND discord_channel_id = ?', (guild_id, channel_id))
        result = cursor.fetchone()
        conn.close()
        return result

    @rss.command(integration_types={IntegrationType.guild_install}, name="set_feed_channel", description="Set the channel for RSS feed updates")
    @default_permissions(administrator=True)
    async def set_feed_channel(self, ctx: discord.ApplicationContext, 
                       channel: Option(discord.TextChannel, "The channel to send RSS updates to")):
        if not await self.is_user_allowed(ctx.author):
            await ctx.respond(content="You are not allowed to use this command.", ephemeral=True)
            return
        await ctx.defer()

        # Check if the channel is already in the database
        guild_channel_table_id = self.check_feed_channel_exists(ctx.guild.id, channel.id)
        if guild_channel_table_id:
            await ctx.respond(f"Channel {channel.id} is already set as the feed channel for guild {ctx.guild.id}")
            return

        # Add a new GuildChannel entry to the database
        guild_channel_table_id = self.add_guild_channel_to_database(ctx.guild.id, channel.id)
        await ctx.respond(f"Channel {channel.id} set as the feed channel for guild {ctx.guild.id}")

    @rss.command(integration_types={IntegrationType.guild_install}, name="list_feeds", description="List all current RSS feeds")
    async def list_feeds(self, ctx: discord.ApplicationContext):
        if not await self.is_user_allowed(ctx.author):
            await ctx.respond(content="You are not allowed to use this command.", ephemeral=True)
            return
        await ctx.defer()

        guild_channel_table_id = self.check_feed_channel_exists_go(ctx.guild.id)
        if not guild_channel_table_id:
            await ctx.respond(f"No feed channel found for guild {ctx.guild.id}. Please set a feed channel using the set_feed_channel command.")
            return
        guild_channel_table_id = guild_channel_table_id[0]
        # Get all feed_ids and names in FeedSubscription entries for the guild
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT feed_id, name FROM FeedSubscription WHERE guild_channel_id = ?', (guild_channel_table_id,))
        feeds = cursor.fetchall()
        conn.close()
        if not feeds:
            await ctx.respond(f"No feeds found for guild {ctx.guild.id}")
            return
        
        # Get all feed_urls for all feed_ids in feeds in RssFeed entries
        feed_urls = []
        for feed_id, name in feeds:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT rss_feed_url FROM RssFeed WHERE id = ?', (feed_id,))
            result = cursor.fetchone()
            if not result:
                conn.close()
                continue
            feed_url = result[0]
            feed_urls.append((feed_url, name))
            conn.close()
        
        # Send the feed URLs to the user
        embed = discord.Embed(title="RSS Feeds", description="Here are the RSS feeds you have set up:")
        for feed_url, name in feed_urls:
            embed.add_field(name=name, value=feed_url, inline=False)
        await ctx.respond(embed=embed)

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
        
        # 1. Get all items from RssFeed table
        cursor = conn.cursor()
        cursor.execute('SELECT id, rss_feed_url FROM RssFeed')
        rss_feeds = cursor.fetchall()
        
        for feed_id, rss_feed_url in rss_feeds:
            # 2. Fetch messages for the feed
            feedparser_data = feedparser.parse(rss_feed_url)
            
            for entry in feedparser_data.entries:
                # 3. Save the entry to RssMessage
                message_id = self.get_hash(str(entry.guid))
                cursor.execute('SELECT * FROM RssMessage WHERE id = ?', (message_id,))
                if cursor.fetchone() is None:
                    # Save the new message
                    self.add_entry_to_database('RssMessage', message_id, 
                                                feed_id=feed_id,
                                                title=entry.title,
                                                link=entry.link,
                                                description=entry.description,
                                                enclosure_href=getattr(entry, 'enclosure', {}).get('href', None),
                                                category=getattr(entry, 'category', None),
                                                pub_date=entry.published)
                    conn.commit()

                    cursor.execute('SELECT * FROM FeedSubscription WHERE feed_id = ?', (feed_id,))
                    subscriptions = cursor.fetchall()

                    for sub_id, _, guild_channel_id, name in subscriptions:
                        cursor.execute('SELECT * FROM FeedForwards WHERE subscription_id = ? AND message_id = ?', (sub_id, message_id)) # check if the message has already been forwarded
                        if cursor.fetchone() is None: # if not, send the embed
                            # 5. Send embed to the channel and add to FeedForwards table
                            # get the guild and channel id from the guild_channel_id
                            cursor.execute('SELECT discord_guild_id, discord_channel_id FROM GuildChannel WHERE id = ?', (guild_channel_id,))
                            guild_id, channel_id = cursor.fetchone()
                            guild = self.bot.get_guild(int(guild_id))
                            channel = guild.get_channel(int(channel_id))
                            channel = self.bot.get_channel(int(channel_id))
                            title = entry.title
                            if self.is_spiegel_plus(entry.link):
                                title = f"(S+) {title}"
                            # send the embed of the message to the channel
                            embed = discord.Embed(title=title, url=entry.link, description=entry.description)
                            if entry.enclosures:
                                embed.set_image(url=entry.enclosures[0].href)
                            if entry.category:
                                embed.add_field(name="Category", value=entry.category, inline=True)
                            if entry.published:
                                embed.add_field(name="Published Date", value=entry.published, inline=True)
                            if self.is_spiegel_plus(entry.link):
                                embed.add_field(name="RemovePaywall", value=f"[Try it out!](https://www.removepaywall.com/search?url={entry.link})", inline=False)
                            embed.set_footer(text=f"Feed: {name}")
                            await channel.send(embed=embed)

                            # save the new forward
                            cursor.execute('INSERT INTO FeedForwards (subscription_id, message_id) VALUES (?, ?)', (sub_id, self.get_hash(str(entry.guid))))
                            conn.commit()
        conn.commit()
        conn.close()
        

    @check_feeds.before_loop
    async def before_check_feeds(self):
        await self.bot.wait_until_ready()
        self.logger.debug("Bot is ready, starting feed check loop")

def setup(bot):
    bot.add_cog(RSSFeed(bot))
