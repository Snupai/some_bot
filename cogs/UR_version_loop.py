import discord
from discord import SlashCommandGroup, IntegrationType, Option, Colour
from discord.ext import commands, tasks
import datetime
import json
import logging
from utils.UR_Version_check import URVersionChecker
import requests

class URVersionLoop(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot: commands.Bot = bot
        self.logger = logging.getLogger('bot.py')
        self.version_checker = URVersionChecker()
        self.check_version.start()
        self.version_file = 'last_version.json'
    def cog_unload(self):
        self.check_version.cancel()

    @tasks.loop(time=datetime.time(hour=8))
    async def check_version(self):
        result = await self.version_checker.check_version()
        # if result is not None and not an exception, send messages
        if result is not None and not isinstance(result, Exception):
            message = f"New UR version found: {result['version']}"
            
            # Send DM to bot owner
            owner = self.bot.get_user(239809113125552129)
            if owner is not None:
                await owner.send(f"New version found: [{result['version']}]({result['link']})")
            else:
                self.logger.error("Could not find bot owner to send DM")

            # Send ntfy notification
            try:
                requests.post("http://snupai.info/",
                    data=json.dumps({
                        "topic": "UR_Version-Check",
                        "message": message,
                        "title": f"New UR Version {result['version']}",
                        "tags": ["robot", "new"],
                        "priority": "default",
                        "click": result['link']
                    })
                )
            except Exception as e:
                self.logger.error(f"Failed to send ntfy notification: {e}")

    ur_group = SlashCommandGroup(integration_types={IntegrationType.user_install}, name="ur", description="Universal Robots commands")
    
    @ur_group.command(name="check_version", description="check the version")
    @commands.is_owner()
    async def check_version_command(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        
        # Run version check with force=True to always return the latest version
        result = await self.version_checker.check_version(force=True)
        
        if result is None:
            await ctx.respond("No new version found.")
        elif isinstance(result, Exception):
            await ctx.respond(f"Error checking version: {result}")
        else:
            embed = discord.Embed(
                title=f"Universal Robots Version: {result['version']}",
                description=f"Latest version found: [{result['version']}]({result['link']})",
                color=Colour.blue()
            )
            embed.add_field(name="Download Link", value=result['link'])
            await ctx.respond(embed=embed)

def setup(bot):
    bot.add_cog(URVersionLoop(bot))