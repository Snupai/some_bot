import discord
from discord import SlashCommandGroup, IntegrationType
from discord.ext import commands
import logging
import sqlite3
from purrbot_site_api_wrapper import NsfwApi, OwoApi, SfwApi
from purrbot_site_api_wrapper.rest import ApiException

class OwoCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger('bot.py')

    owo_group = SlashCommandGroup(integration_types={IntegrationType.user_install, IntegrationType.guild_install}, name="purr", description="Purr API commands")

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

    @owo_group.command(integration_types={IntegrationType.guild_install, IntegrationType.user_install}, name="owoify", description="OwOify a message")
    async def owoify(self, ctx: discord.ApplicationContext, 
                     message: str = discord.Option(name="message", description="The message to owoify")):
        if not await self.is_user_allowed(ctx.author):
            await ctx.respond(content="You are not allowed to use this command.", ephemeral=True)
            return
        await ctx.defer()
        try:
            body = {
                "text": message
            }
            owo = OwoApi()
            response = owo.owoify_post(body=body)
            if response.error:
                raise ApiException(response.error)
            await ctx.respond(content=response.text)
        except ApiException as e:
            self.logger.error(f"Error owoifying message: {e}")
            await ctx.respond(content=f"Error owoifying message: {e}", ephemeral=True)

def setup(bot):
    bot.add_cog(OwoCog(bot))