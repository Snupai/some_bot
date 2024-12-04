if __name__ == "__main__":
    print("This is a cog file and cannot be run directly.")
    exit()

import logging
import discord
from discord.ext import commands
from discord.ui import Button, View

logger = logging.getLogger('bot.py')

class GenericCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.slash_command(integration_types={discord.IntegrationType.guild_install, discord.IntegrationType.user_install})
    async def ping(self, ctx):
        """
        Command to check if the bot is online.
        """
        # Assuming logger is defined and make_ephemeral is a function that determines if the response should be ephemeral
        logger.info(f"{ctx.author} used /ping command in {ctx.channel} on {ctx.guild}.")
        await ctx.respond(f'Pong! {round(self.bot.latency * 1000)}ms', ephemeral=True)

    @commands.slash_command(integration_types={discord.IntegrationType.guild_install, discord.IntegrationType.user_install})
    async def about(self, ctx):
        """
        Command to display information about the bot.
        """
        logger.info(f"{ctx.author} used /about command in {ctx.channel} on {ctx.guild}.")

        # Create an embed
        embed = discord.Embed(title="About the bot")
        embed.description = "This discord bot is an easy interface for the Custom Glyph tools. It uses the scripts created by <@429776328833761280> to create and visualize custom glyphs. You can find the source code for the tools at the following links:"
        embed.set_footer(text="Click a button to navigate to the according Github Repo.")

        # Create a button row
        row = View()

        # Create buttons
        button1 = Button(style=discord.ButtonStyle.primary, label="SebiAi/custom-nothing-glyph-tools", url="https://github.com/SebiAi/custom-nothing-glyph-tools", emoji="🔧")
        button2 = Button(style=discord.ButtonStyle.primary, label="SebiAi/GlyphVisualizer", url="https://github.com/SebiAi/GlyphVisualizer", emoji="🔍")

        # Add buttons to the row
        row.add_item(button1)
        row.add_item(button2)

        # Add the button row to the embed
        await ctx.respond(embed=embed, view=row, ephemeral=True)

    @commands.slash_command(integration_types={discord.IntegrationType.guild_install, discord.IntegrationType.user_install}, name="help")
    async def help(self, ctx):
        """
        Command to display the help message.
        """
        logger.info(f"{ctx.author} used /help command in {ctx.channel} on {ctx.guild}.")

        # Create an embed
        embed = discord.Embed(title="Help")
        embed.description = "This bot provides an easy interface for the Custom Glyph tools. You can use the following commands to create and visualize custom glyphs:"
        embed.add_field(name="/ping", value="Check if the bot is online.", inline=False)
        embed.add_field(name="/about", value="Display information about the bot.", inline=False)
        embed.add_field(name="/create", value="Create a custom glyph.", inline=False)
        embed.add_field(name="/visualize", value="Visualize a custom glyph.", inline=False)
        embed.add_field(name="/publish", value="Publish a custom glyph to our database.", inline=False)
        embed.add_field(name="/search", value="Search for a custom glyph.", inline=False)
        embed.add_field(name="/help", value="Display this help message.", inline=False)

        # Send the embed
        await ctx.respond(embed=embed, ephemeral=True)

def setup(bot):
    bot.add_cog(GenericCog(bot))