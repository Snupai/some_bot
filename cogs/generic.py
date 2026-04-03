if __name__ == "__main__":
    print("This is a cog file and cannot be run directly.")
    exit()

import logging
import discord
from discord.ext import commands
from discord.ui import Button, View
import datetime
import time

class GenericCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger('bot.py')

    @commands.slash_command(integration_types={discord.IntegrationType.guild_install, discord.IntegrationType.user_install})
    async def ping(self, ctx):
        """
        Command to check if the bot is online and display latency metrics.
        """
        # Log the command usage
        self.logger.info(f"{ctx.author} used /ping command in {ctx.channel} on {ctx.guild}.")

        # Discord WebSocket latency
        discord_latency = self.bot.latency * 1000  # Convert latency to ms

        # Record the time before sending the response to measure REST API latency
        start_time = time.perf_counter()
        initial_response = await ctx.respond("Talking to Discord <a:typing:1322188075358752789>", ephemeral=True)  # Initial response
        rest_latency = (time.perf_counter() - start_time) * 1000  # Convert to ms

        # Measure message edit latency
        edit_start_time = time.perf_counter()
        response_message = await initial_response.original_response()  # Retrieve the original response message
        await response_message.edit(content="Updating ping details...")
        edit_latency = (time.perf_counter() - edit_start_time) * 1000  # Convert to ms

        # Update the message with the final metrics
        await response_message.edit(content=(
            f"<:pencil:1322186625727467550> Edit message: `{edit_latency:.0f}ms`\n"
            f"<:discord:1322186512170876948> Discord: `{discord_latency:.0f}ms`\n"
            f"<:download:1322186564461264940> RestAction: `{rest_latency:.0f}ms`"
        ))

    @commands.slash_command(integration_types={discord.IntegrationType.guild_install, discord.IntegrationType.user_install}, name="uptime", description="Returns the bot's uptime")
    async def uptime(self, ctx: discord.ApplicationContext):
        """
        A slash command to return the bot's uptime.
        """
        current_time = time.time()
        uptime_seconds = int(current_time - self.bot.start_time)
        uptime_string = str(datetime.timedelta(seconds=uptime_seconds))
        await ctx.respond(f"Uptime: {uptime_string}", ephemeral=True)

    @commands.slash_command(integration_types={discord.IntegrationType.guild_install, discord.IntegrationType.user_install}, name="about", description="Display information about the bot.")
    async def about(self, ctx):
        """
        Command to display information about the bot.
        """
        self.logger.info(f"{ctx.author} used /about command in {ctx.channel} on {ctx.guild}.")

        # Create an embed
        embed = discord.Embed(title="About the bot")
        embed.description = "This bot is just a little test and playground for <@239809113125552129> :3"
        embed.set_footer(text=f"Snupai~ | {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        # Create a button row
        row = View()

        # Create buttons
        button1 = Button(style=discord.ButtonStyle.primary, label="Snupai", url="https://github.com/Snupai/", emoji=discord.PartialEmoji(name="github", id=1315636762051350559))

        # Add buttons to the row
        row.add_item(button1)

        # Add the button row to the embed
        await ctx.respond(embed=embed, view=row, ephemeral=True)

    @commands.slash_command(integration_types={discord.IntegrationType.guild_install, discord.IntegrationType.user_install}, name="shards", description="Display shard information.")
    async def shards(self, ctx):
        """
        Command to display shard information.
        """
        shard_info = []
        for shard_id, shard in self.bot.shards.items():
            shard_info.append(f"Shard ID: {shard_id}, Latency: {shard.latency * 1000:.2f}ms")

        shard_info_string = "\n".join(shard_info)
        guild_shard_id = ctx.guild.shard_id if ctx.guild else "N/A"

        embed = discord.Embed(title="Shard Information")
        embed.description = f"```\n{shard_info_string}\n```"
        embed.add_field(name="Guild Shard ID", value=guild_shard_id, inline=True)
        embed.set_footer(text=f"Requested by {ctx.author} | {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        await ctx.respond(embed=embed, ephemeral=True)

def setup(bot):
    bot.add_cog(GenericCog(bot))