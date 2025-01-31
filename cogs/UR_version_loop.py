import discord
from discord import SlashCommandGroup, IntegrationType, Option, Colour
from discord.ext import commands, tasks
import datetime
import json
import logging
from utils.UR_Version_check import URVersionChecker

class URVersionLoop(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger('bot.py')
        self.version_checker = URVersionChecker()
        self.check_version.start()
        self.version_file = 'last_version.json'
    def cog_unload(self):
        self.check_version.cancel()

    @tasks.loop(time=datetime.time(hour=7))
    async def check_version(self):
        result = await self.version_checker.check_version()
        # if result is not None and not an exception, send a message to the channel
        if result is not None and not isinstance(result, Exception):
            # send a dm to bot owner
            await self.bot.get_user(self.bot.owner_id).send(f"New version found: {result}")

    ur_group = SlashCommandGroup(integration_types={IntegrationType.user_install}, name="ur", description="Universal Robots commands")
    
    @ur_group.command(name="check_version", description="check the version")
    @commands.is_owner()
    async def check_version_command(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        result = await self.version_checker.check_version()
        if isinstance(result, Exception):
            await ctx.respond(f"Error checking version: {result}")
        elif result is not None:
            await ctx.respond(f"New version found: {result}")
        else:
            # read the version file and get the version and last_check
            with open(self.version_file, 'r') as f:
                data = json.load(f)
                version = data['version']
                last_check = data['last_check']
            await ctx.respond(f"No new version found. Current version: {version} (last updated: {last_check})")

def setup(bot):
    bot.add_cog(URVersionLoop(bot))