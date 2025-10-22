if __name__ == "__main__":
    print("This is a cog file and cannot be run directly.")
    exit()

import logging
import sqlite3
import discord
from discord.ext import commands
import os

from utils.minecwaft.minecraft import Minecraft 

class MinecraftStuff(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger('bot.py')
        self.minecraft = Minecraft("116.202.215.54", 25575, "n32DCx#w")
        
    kaeseecke_mc = discord.SlashCommandGroup(integration_types={discord.IntegrationType.guild_install, discord.IntegrationType.user_install}, name="kaeseecke_mc", description="Minecraft stuff")
        
    @kaeseecke_mc.command(integration_types={discord.IntegrationType.guild_install, discord.IntegrationType.user_install}, name="whitelist", description="Manage the whitelist of the Minecraft server")
    async def whitelist(self, ctx, action: discord.Option(str, "Action to perform", choices=["add", "remove", "list"]), player: discord.Option(str, "Player name to add/remove", required=False) = None):
        self.logger.debug(f'{ctx.author} used the `/kaeseecke_mc whitelist {action} {player}` command')
        await ctx.defer()
        
        if action == "list":
            with self.minecraft as mc:
                whitelist = mc.player.whitelist_list()
                if whitelist:
                    await ctx.respond(f"Players on the whitelist: {whitelist}")
                else:
                    await ctx.respond("Failed to get whitelist or whitelist is empty.")
        elif action in ["add", "remove"]:
            if not player:
                await ctx.respond(f"Please provide a player name to {action}.")
                return
            with self.minecraft as mc:
                if action == "add":
                    response = mc.player.whitelist_add(player)
                elif action == "remove":
                    response = mc.player.whitelist_remove(player)
                if response:
                    await ctx.respond(f"Successfully {action}ed {player} {'to' if action == 'add' else 'from'} the whitelist.")
                else:
                    await ctx.respond(f"Failed to {action} {player} {'to' if action == 'add' else 'from'} the whitelist.")

    @kaeseecke_mc.command(integration_types={discord.IntegrationType.guild_install, discord.IntegrationType.user_install}, name="player", description="Player on the Minecraft server")
    async def player(self, ctx, 
                     option: discord.Option(str, "Option to get information about", choices=["list", "kick", "ban", "unban"], required=True),
                     player: discord.Option(str, "Player name to get information about") = None):
        self.logger.debug(f'{ctx.author} used the `/kaeseecke_mc player {option} {player}` command')
        await ctx.defer()
        if option == "list":
            with self.minecraft as mc:
                players = mc.player.list_players()
                await ctx.respond(f"Players on the Minecraft server: {players}")
                return
        if not player:
            await ctx.respond(f"Please provide a player name to {option}.")
            return
        if option == "kick":
            with self.minecraft as mc:
                mc.player.kick(player)
                await ctx.respond(f"Successfully kicked {player} from the server.")
                return
        elif option == "ban":
            with self.minecraft as mc:
                mc.player.ban(player)
                await ctx.respond(f"Successfully banned {player} from the server.")
                return 
        elif option == "unban":
            with self.minecraft as mc:
                mc.player.unban(player)
                await ctx.respond(f"Successfully unbanned {player} from the server.")
                return
            
    @kaeseecke_mc.command(integration_types={discord.IntegrationType.guild_install, discord.IntegrationType.user_install}, name="game", description="Game commands on the Minecraft server")
    async def game(self, ctx, 
                  option: discord.Option(str, "Option to get information about", choices=["gamemode", "gamerule", "spawnpoint", "trigger"], required=True),
                  target: discord.Option(str, "Either a player or coords (x,y,z)", required=False),
                  mode: discord.Option(str, "Mode to get information about", choices=["survival", "creative", "adventure", "spectator"], required=False),
                  rule: discord.Option(str, "Rule to get information about", required=False),
                  value: discord.Option(int, "Value to get information about", required=False)):
        self.logger.debug(f'{ctx.author} used the `/kaeseecke_mc game {option} {target} {mode} {rule} {value}` command')
        await ctx.defer()
        if option == "gamemode":
            if not target:
                await ctx.respond(f"Please provide a target to set the gamemode for.")
                return
            with self.minecraft as mc:
                mc.game.gamemode(target, mode)
                await ctx.respond(f"Successfully set {target}'s gamemode to {mode}.")
                return
        elif option == "gamerule":
            if not rule:
                await ctx.respond(f"Please provide a rule to set.")
                return
            with self.minecraft as mc:
                mc.game.gamerule(rule, value)
                await ctx.respond(f"Successfully set {rule} to {value}.")
                return
        elif option == "spawnpoint":
            if not target:
                await ctx.respond(f"Please provide a target to set the spawnpoint for.")
                return
            x, y, z = target.split(",")
            if not x or not y or not z:
                await ctx.respond(f"Please provide valid coordinates (x,y,z).")
                return
            with self.minecraft as mc:
                mc.game.spawnpoint(target, x, y, z)
                await ctx.respond(f"Successfully set {target}'s spawnpoint to {x}, {y}, {z}.")
                return
        elif option == "trigger":
            if not target:
                await ctx.respond(f"Please provide a target to trigger.")
                return
            with self.minecraft as mc:
                mc.game.trigger(target, value)
                await ctx.respond(f"Successfully triggered {target}.")
                return


def setup(bot):
    bot.add_cog(MinecraftStuff(bot))