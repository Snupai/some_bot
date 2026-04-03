import discord
from discord.ext import commands
import sqlite3

class AllowedUsersCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.conn = sqlite3.connect('allowed_users.sqlite')
        self.cursor = self.conn.cursor()
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS allowed_users (
                user_id INTEGER PRIMARY KEY
            )
        ''')
        self.conn.commit()
    
    usr_mng = discord.SlashCommandGroup(integration_types={discord.IntegrationType.user_install}, name='usr-mgmt', description='User management commands')

    @usr_mng.command(integration_types={discord.IntegrationType.user_install}, name='add_user', description='Add a user to the allowed users list.')
    @commands.is_owner()
    async def add_user(self, ctx, user: discord.User):
        """Add a user to the allowed users list."""
        self.cursor.execute('INSERT OR IGNORE INTO allowed_users (user_id) VALUES (?)', (user.id,))
        self.conn.commit()
        await ctx.respond(f'User {user} has been added to the allowed users list.')

    @usr_mng.command(integration_types={discord.IntegrationType.user_install}, name='remove_user', description='Remove a user from the allowed users list.')
    @commands.is_owner()
    async def remove_user(self, ctx, user: discord.User):
        """Remove a user from the allowed users list."""
        self.cursor.execute('DELETE FROM allowed_users WHERE user_id = ?', (user.id,))
        self.conn.commit()
        await ctx.respond(f'User {user} has been removed from the allowed users list.')

    @usr_mng.command(integration_types={discord.IntegrationType.user_install}, name='list_users', description='List all allowed users.')
    @commands.is_owner()
    async def list_users(self, ctx):
        """List all allowed users."""
        self.cursor.execute('SELECT user_id FROM allowed_users')
        users = self.cursor.fetchall()
        if users:
            user_mentions = [self.bot.get_user(user_id).mention for (user_id,) in users]
            await ctx.respond('Allowed users: ' + ', '.join(user_mentions))
        else:
            await ctx.respond('No allowed users found.')

    def cog_unload(self):
        self.conn.close()
        
    @usr_mng.command(integration_types={discord.IntegrationType.user_install}, name='search', description='Search if a user is allowed.')
    @commands.is_owner()
    async def search(self, ctx, user: discord.User):
        """Search if a user is allowed."""
        self.cursor.execute('SELECT user_id FROM allowed_users WHERE user_id = ?', (user.id,))
        result = self.cursor.fetchone()
        if result:
            await ctx.respond(f'User {user} is allowed.')
        else:
            await ctx.respond(f'User {user} is not allowed.')

def setup(bot):
    bot.add_cog(AllowedUsersCog(bot))