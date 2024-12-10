if __name__ == "__main__":
    print("This is a cog file and cannot be run directly.")
    exit()

import logging
import discord
from discord.ext import commands
from discord.ui import Button, View
import datetime



class GenericCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger('bot.py')

    @commands.slash_command(integration_types={discord.IntegrationType.guild_install, discord.IntegrationType.user_install})
    async def ping(self, ctx):
        """
        Command to check if the bot is online.
        """
        # Assuming logger is defined and make_ephemeral is a function that determines if the response should be ephemeral
        self.logger.info(f"{ctx.author} used /ping command in {ctx.channel} on {ctx.guild}.")
        await ctx.respond(f'Pong! {round(self.bot.latency * 1000)}ms', ephemeral=True)

    @commands.slash_command(integration_types={discord.IntegrationType.guild_install, discord.IntegrationType.user_install})
    async def about(self, ctx):
        """
        Command to display information about the bot.
        """
        self.logger.info(f"{ctx.author} used /about command in {ctx.channel} on {ctx.guild}.")

        # Create an embed
        embed = discord.Embed(title="About the bot")
        embed.description = "This bot is just a wittle test and playground for <@239809113125552129> :3"
        embed.set_footer(text=f"Snupai~ | {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        # Create a button row
        row = View()

        # Create buttons
        button1 = Button(style=discord.ButtonStyle.primary, label="Snupai", url="https://github.com/Snupai/", emoji=discord.PartialEmoji(name="github", id=1315636762051350559))

        # Add buttons to the row
        row.add_item(button1)

        # Add the button row to the embed
        await ctx.respond(embed=embed, view=row, ephemeral=True)

    @commands.slash_command(integration_types={discord.IntegrationType.guild_install, discord.IntegrationType.user_install}, name="help")
    async def help(self, ctx):
        """
        Command to display the help message.
        """
        self.logger.info(f"{ctx.author} used /help command in {ctx.channel} on {ctx.guild}.")

        # Create an embed
        embed = discord.Embed(title="Help")
        embed.description = "This bot provides an easy interface for the Custom Glyph tools. You can use the following commands to create and visualize custom glyphs:"
        embed.add_field(name="/ping", value="Check if the bot is online.", inline=False)
        embed.add_field(name="/about", value="Display information about the bot.", inline=False)
        embed.add_field(name="/get_yt_link", value="Get the Youtube link to a Spotify song.", inline=False)
        embed.add_field(name="/dl_trim", value="Download and trim a YouTube video. You can also provide a Spotify song link.", inline=False)
        embed.add_field(name="/help", value="Display this help message.", inline=False)
        embed.set_footer(text=f"Meaw~ | {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        # Send the embed
        await ctx.respond(embed=embed, ephemeral=True)

def setup(bot):
    bot.add_cog(GenericCog(bot))