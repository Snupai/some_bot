from io import BytesIO
import aiohttp
import discord
from discord import SlashCommandGroup, IntegrationType, Option, Colour
from discord.ext import commands
import datetime
import asyncio
import logging
import sqlite3
from purrbot_site_api_wrapper import OwoApi, OWOifyRequest, OWOifySuccess, ImgSuccess, SfwApi, NsfwApi
from purrbot_site_api_wrapper.rest import ApiException
import requests

def hex_to_rgb(hex_string):
    hex_string = hex_string.lstrip('#')
    return tuple(int(hex_string[i:i+2], 16) for i in (0, 2, 4))

class OwoCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger('bot.py')
        self.embed_colour = Colour.from_rgb(*hex_to_rgb("#d9adfa"))

    purr_group = SlashCommandGroup(integration_types={IntegrationType.user_install, IntegrationType.guild_install}, name="purr", description="Purr API commands")

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

    @purr_group.command(integration_types={IntegrationType.guild_install, IntegrationType.user_install}, name="owoify", description="OwOify a message")
    async def owoify(self, ctx: discord.ApplicationContext, 
                     message: str = discord.Option(name="message", description="The message to owoify")):
        await ctx.defer()
        try:
            owo = OwoApi()
            response: OWOifySuccess = owo.owoify_post(body=OWOifyRequest(text=message))
            if response.error:
                raise ApiException(response.error)
            await ctx.respond(content=response.text)
        except ApiException as e:
            self.logger.error(f"Error owoifying message: {e}")
            await ctx.respond(content=f"Error owoifying message: {e}", ephemeral=True)

    purr_sfw_group = purr_group.create_subgroup(name="sfw", description="Purr SFW API commands")

    async def handle_sfw_command(self, ctx: discord.ApplicationContext, api_call, description: str):
        await ctx.defer()
        try:
            response: ImgSuccess = api_call()
            if response.error:
                raise ApiException(response.error)
            
            # Validate the image URL
            if not response.link or not response.link.startswith(('http://', 'https://')):
                self.logger.error(f"Invalid image URL received: {response.link}")
                await ctx.respond(content="Error: Invalid image URL received from the API.", ephemeral=True)
                return
            filetype = response.link.split(".")[-1]
            async with aiohttp.ClientSession() as session:
                async with session.get(response.link) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.read()
            file = discord.File(BytesIO(data), filename=f"idk.{filetype}")
            embed = discord.Embed(colour=self.embed_colour, description=description, timestamp=datetime.datetime.now())
            embed.set_image(url=f"attachment://idk.{filetype}")
            self.logger.debug(f"got response: '{response.link}'")
            
            # Try to send the embed and handle any potential errors
            try:
                await ctx.respond(embed=embed, file=file)
            except discord.HTTPException as e:
                self.logger.error(f"Failed to send embed with image: {e}")
                await ctx.respond(content="Error: Failed to display the image. The image URL might be invalid or expired.", ephemeral=True)
        except ApiException as e:
            self.logger.error(f"Error getting {description} gif: {e}")
            await ctx.respond(content=f"Error getting {description} gif: {e}", ephemeral=True)

    @purr_sfw_group.command(integration_types={IntegrationType.guild_install, IntegrationType.user_install}, name="angry", description="angry")
    async def angry(self, ctx: discord.ApplicationContext):
        await self.handle_sfw_command(ctx, SfwApi().img_sfw_angry_gif_get, f"{ctx.author.mention} is angry")
    
    @purr_sfw_group.command(integration_types={IntegrationType.guild_install, IntegrationType.user_install}, name="bite", description="bite someone")
    async def bite(self, ctx: discord.ApplicationContext, 
                   user: discord.User = Option(discord.User, description="The user to bite", required=True)):
        await self.handle_sfw_command(ctx, SfwApi().img_sfw_bite_gif_get, f"{ctx.author.mention} bites {user.mention}")

    @purr_sfw_group.command(integration_types={IntegrationType.guild_install, IntegrationType.user_install}, name="blush", description="blush")
    async def blush(self, ctx: discord.ApplicationContext):
        await self.handle_sfw_command(ctx, SfwApi().img_sfw_blush_gif_get, f"{ctx.author.mention} blushes")

    @purr_sfw_group.command(integration_types={IntegrationType.guild_install, IntegrationType.user_install}, name="comfy", description="comfy")
    async def comfy(self, ctx: discord.ApplicationContext):
        await self.handle_sfw_command(ctx, SfwApi().img_sfw_comfy_gif_get, f"{ctx.author.mention} is comfy")

    @purr_sfw_group.command(integration_types={IntegrationType.guild_install, IntegrationType.user_install}, name="cry", description="cry")
    async def cry(self, ctx: discord.ApplicationContext):
        await self.handle_sfw_command(ctx, SfwApi().img_sfw_cry_gif_get, f"{ctx.author.mention} is crying")

    @purr_sfw_group.command(integration_types={IntegrationType.guild_install, IntegrationType.user_install}, name="cuddle", description="cuddle")
    async def cuddle(self, ctx: discord.ApplicationContext, 
                     user: discord.User = Option(discord.User, description="The user to cuddle", required=True)):
        await self.handle_sfw_command(ctx, SfwApi().img_sfw_cuddle_gif_get, f"{ctx.author.mention} cuddles {user.mention}")

    @purr_sfw_group.command(integration_types={IntegrationType.guild_install, IntegrationType.user_install}, name="dance", description="dance")
    async def dance(self, ctx: discord.ApplicationContext):
        await self.handle_sfw_command(ctx, SfwApi().img_sfw_dance_gif_get, f"{ctx.author.mention} dances")

    @purr_sfw_group.command(integration_types={IntegrationType.guild_install, IntegrationType.user_install}, name="fluff", description="fluff")
    async def fluff(self, ctx: discord.ApplicationContext,
                    user: discord.User = Option(discord.User, description="The user to fluff", required=True)):
        await self.handle_sfw_command(ctx, SfwApi().img_sfw_fluff_gif_get, f"{ctx.author.mention} fluffs {user.mention}")

    @purr_sfw_group.command(integration_types={IntegrationType.guild_install, IntegrationType.user_install}, name="hug", description="hug")
    async def hug(self, ctx: discord.ApplicationContext, 
                  user: discord.User = Option(discord.User, description="The user to hug", required=True)):
        await self.handle_sfw_command(ctx, SfwApi().img_sfw_hug_gif_get, f"{ctx.author.mention} hugs {user.mention}")

    @purr_sfw_group.command(integration_types={IntegrationType.guild_install, IntegrationType.user_install}, name="kiss", description="kiss")
    async def kiss(self, ctx: discord.ApplicationContext, 
                   user: discord.User = Option(discord.User, description="The user to kiss", required=True)):
        await self.handle_sfw_command(ctx, SfwApi().img_sfw_kiss_gif_get, f"{ctx.author.mention} kisses {user.mention}")

    @purr_sfw_group.command(integration_types={IntegrationType.guild_install, IntegrationType.user_install}, name="lay", description="lay")
    async def lay(self, ctx: discord.ApplicationContext):
        await self.handle_sfw_command(ctx, SfwApi().img_sfw_lay_gif_get, f"{ctx.author.mention} lays down")
    
    @purr_sfw_group.command(integration_types={IntegrationType.guild_install, IntegrationType.user_install}, name="lick", description="lick")
    async def lick(self, ctx: discord.ApplicationContext,
                   user: discord.User = Option(discord.User, description="The user to lick", required=True)):
        await self.handle_sfw_command(ctx, SfwApi().img_sfw_lick_gif_get, f"{ctx.author.mention} licks {user.mention}")
    
    @purr_sfw_group.command(integration_types={IntegrationType.guild_install, IntegrationType.user_install}, name="pat", description="pat")
    async def pat(self, ctx: discord.ApplicationContext, 
                  user: discord.User = Option(discord.User, description="The user to pat", required=True)):
        await self.handle_sfw_command(ctx, SfwApi().img_sfw_pat_gif_get, f"{ctx.author.mention} pats {user.mention}")

    @purr_sfw_group.command(integration_types={IntegrationType.guild_install, IntegrationType.user_install}, name="poke", description="poke")
    async def poke(self, ctx: discord.ApplicationContext, 
                   user: discord.User = Option(discord.User, description="The user to poke", required=True)):
        await self.handle_sfw_command(ctx, SfwApi().img_sfw_poke_gif_get, f"{ctx.author.mention} pokes {user.mention}")

    @purr_sfw_group.command(integration_types={IntegrationType.guild_install, IntegrationType.user_install}, name="pout", description="pout")
    async def pout(self, ctx: discord.ApplicationContext):
        await self.handle_sfw_command(ctx, SfwApi().img_sfw_pout_gif_get, f"{ctx.author.mention} pouts")
    
    @purr_sfw_group.command(integration_types={IntegrationType.guild_install, IntegrationType.user_install}, name="slap", description="slap")
    async def slap(self, ctx: discord.ApplicationContext, 
                   user: discord.User = Option(discord.User, description="The user to slap", required=True)):
        await self.handle_sfw_command(ctx, SfwApi().img_sfw_slap_gif_get, f"{ctx.author.mention} slaps {user.mention}")

    @purr_sfw_group.command(integration_types={IntegrationType.guild_install, IntegrationType.user_install}, name="smile", description="smile")
    async def smile(self, ctx: discord.ApplicationContext):
        await self.handle_sfw_command(ctx, SfwApi().img_sfw_smile_gif_get, f"{ctx.author.mention} smiles")
    
    @purr_sfw_group.command(integration_types={IntegrationType.guild_install, IntegrationType.user_install}, name="tail", description="tail")
    async def stare(self, ctx: discord.ApplicationContext):
        await self.handle_sfw_command(ctx, SfwApi().img_sfw_tail_gif_get, f"{ctx.author.mention} wags their tail.")

    @purr_sfw_group.command(integration_types={IntegrationType.guild_install, IntegrationType.user_install}, name="tickle", description="tickle")
    async def tickle(self, ctx: discord.ApplicationContext, 
                     user: discord.User = Option(discord.User, description="The user to tickle", required=True)):
        await self.handle_sfw_command(ctx, SfwApi().img_sfw_tickle_gif_get, f"{ctx.author.mention} tickles {user.mention}")



    purr_sfw_img_group = purr_group.create_subgroup(name="img", description="Purr SFW Image API commands")

    @purr_sfw_img_group.command(integration_types={IntegrationType.guild_install, IntegrationType.user_install}, name="holo", description="holo")
    async def holo(self, ctx: discord.ApplicationContext):
        await self.handle_sfw_command(ctx, SfwApi().img_sfw_holo_img_get, f"{ctx.author.mention} here is a holo image")
         
    @purr_sfw_img_group.command(integration_types={IntegrationType.guild_install, IntegrationType.user_install}, name="icon", description="get an icon")
    async def icon(self, ctx: discord.ApplicationContext):
        await self.handle_sfw_command(ctx, SfwApi().img_sfw_icon_img_get, f"{ctx.author.mention} here is an icon")

    @purr_sfw_img_group.command(integration_types={IntegrationType.guild_install, IntegrationType.user_install}, name="kitsune", description="kitsune")
    async def kitsune(self, ctx: discord.ApplicationContext):
        await self.handle_sfw_command(ctx, SfwApi().img_sfw_kitsune_img_get, f"{ctx.author.mention} here is a kitsune")

    @purr_sfw_img_group.command(integration_types={IntegrationType.guild_install, IntegrationType.user_install}, name="neko", description="neko")
    async def neko(self, ctx: discord.ApplicationContext, 
                   type: str = Option(name="type", description="The type of neko to get", required=True, choices=["gif", "img"])):
        await self.handle_sfw_command(ctx, lambda:SfwApi().img_sfw_neko_type_get(type), f"{ctx.author.mention} here's a neko")

    @purr_sfw_img_group.command(integration_types={IntegrationType.guild_install, IntegrationType.user_install}, name="okami", description="okami")
    async def okami(self, ctx: discord.ApplicationContext):
        await self.handle_sfw_command(ctx, SfwApi().img_sfw_okami_img_get, f"{ctx.author.mention} here's an okami")

    @purr_sfw_img_group.command(integration_types={IntegrationType.guild_install, IntegrationType.user_install}, name="senko", description="senko")
    async def senko(self, ctx: discord.ApplicationContext):
        await self.handle_sfw_command(ctx, SfwApi().img_sfw_senko_img_get, f"{ctx.author.mention} here's a senko")

    @purr_sfw_img_group.command(integration_types={IntegrationType.guild_install, IntegrationType.user_install}, name="shiro", description="shiro")
    async def shiro(self, ctx: discord.ApplicationContext):
        await self.handle_sfw_command(ctx, SfwApi().img_sfw_shiro_img_get, f"{ctx.author.mention} here's a shiro")

    @purr_sfw_img_group.command(integration_types={IntegrationType.guild_install, IntegrationType.user_install}, name="eevee", description="eevee")
    async def eevee(self, ctx: discord.ApplicationContext,
                    type: str = Option(str, description="The image type", choices=["gif", "img"], required=True)):
        await self.handle_sfw_command(ctx, lambda: SfwApi().img_sfw_eevee_type_get(type=type), f"Look at this eevee {ctx.author.mention}")



    purr_nsfw_group = purr_group.create_subgroup(name="nsfw", description="Purr NSFW API commands", nsfw=True)

    async def handle_nsfw_interaction_command(self, ctx: discord.ApplicationContext, user: discord.User, command_name: str, initial_message: str, description: str, api_call: callable):
        await ctx.defer()
        class ConfirmView(discord.ui.View):
            def __init__(self, target_user: discord.User):
                super().__init__(timeout=30)
                self.target_user = target_user
                self.value = None

            async def interaction_check(self, interaction: discord.Interaction) -> bool:
                return interaction.user.id == self.target_user.id

            @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
            async def accept_button(self, button: discord.ui.Button, interaction: discord.Interaction):
                self.value = True
                for child in self.children:
                    child.disabled = True
                await interaction.response.edit_message(content="Accepted", view=self)
                self.stop()

            @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger)
            async def deny_button(self, button: discord.ui.Button, interaction: discord.Interaction):
                self.value = False
                for child in self.children:
                    child.disabled = True
                await interaction.response.edit_message(content="Denied", view=self)
                self.stop()

        view = ConfirmView(user)
        msg = await ctx.respond(f"{initial_message}. Do you accept {user.mention}?", view=view)
        await view.wait()
        if view.value is None:
            await msg.edit(content=f"User {user.mention} did not respond.", view=None)
        elif view.value:
            # handle acceptance
            try:
                response: ImgSuccess = api_call()
                if response.error:
                    raise ApiException(response.error)
                embed = discord.Embed(colour=self.embed_colour, description=description, timestamp=datetime.datetime.now())
                embed.set_image(url=response.link)
                self.logger.debug(f"got response: '{response.link}'")
                await msg.edit(content=None, embed=embed, view=None)
            except ApiException as e:
                self.logger.error(f"Error getting {command_name} gif: {e}")
                await ctx.respond(content=f"Error getting {command_name} gif: {e}", ephemeral=True)
        else:
            # handle denial
            await msg.edit(content=f"{user.mention} denied your request.", view=None)

    @purr_nsfw_group.command(integration_types={IntegrationType.guild_install, IntegrationType.user_install}, name="anal", description="anal")
    async def anal(self, ctx: discord.ApplicationContext, 
                   user: discord.User = Option(discord.User, description="The user to have anal sex with", required=True)):
        await self.handle_nsfw_interaction_command(ctx, user, "anal", f"{ctx.author.mention} wants to have anal sex with you", f"{ctx.author.mention} has anal sex with {user.mention}", NsfwApi().img_nsfw_anal_gif_get)

    @purr_nsfw_group.command(integration_types={IntegrationType.guild_install, IntegrationType.user_install}, name="blowjob", description="blowjob")
    async def blowjob(self, ctx: discord.ApplicationContext, 
                   user: discord.User = Option(discord.User, description="The user to give a blowjob to", required=True)):
        await self.handle_nsfw_interaction_command(ctx, user, "blowjob", f"{ctx.author.mention} wants to give you a blowjob", f"{ctx.author.mention} gives {user.mention} a blowjob", NsfwApi().img_nsfw_blowjob_gif_get)

    @purr_nsfw_group.command(integration_types={IntegrationType.guild_install, IntegrationType.user_install}, name="fuck", description="fuck")
    async def fuck(self, ctx: discord.ApplicationContext, 
                   user: discord.User = Option(discord.User, description="The user you want to have sex with", required=True)):
        await self.handle_nsfw_interaction_command(ctx, user, "fuck", f"{ctx.author.mention} wants to have sex with you", f"{ctx.author.mention} fucks {user.mention}", NsfwApi().img_nsfw_fuck_gif_get)

    @purr_nsfw_group.command(integration_types={IntegrationType.guild_install, IntegrationType.user_install}, name="pussylick", description="pussylick")
    async def pussylick(self, ctx: discord.ApplicationContext, 
                        user: discord.User = Option(discord.User, description="The user you want to lick the pussy of", required=True)):
        await self.handle_nsfw_interaction_command(ctx, user, "pussylick", f"{ctx.author.mention} wants to lick your pussy", f"{ctx.author.mention} licks the pussy of {user.mention}", NsfwApi().img_nsfw_pussylick_gif_get)

    async def handle_nsfw_command(self, ctx: discord.ApplicationContext, command_name: str, description: str, api_call: callable):
        await ctx.defer()
        try:
            response: ImgSuccess = api_call()
            if response.error:
                raise ApiException(response.error)
            
            # Validate the image URL
            if not response.link or not response.link.startswith(('http://', 'https://')):
                self.logger.error(f"Invalid image URL received: {response.link}")
                await ctx.respond(content="Error: Invalid image URL received from the API.", ephemeral=True)
                return
                
            embed = discord.Embed(colour=self.embed_colour, description=description, timestamp=datetime.datetime.now())
            embed.set_image(url=response.link)
            self.logger.debug(f"got response: '{response.link}'")
            
            # Try to send the embed and handle any potential errors
            try:
                await ctx.respond(embed=embed)
            except discord.HTTPException as e:
                self.logger.error(f"Failed to send embed with image: {e}")
                await ctx.respond(content="Error: Failed to display the image. The image URL might be invalid or expired.", ephemeral=True)
        except ApiException as e:
            self.logger.error(f"Error getting {command_name} gif: {e}")
            await ctx.respond(content=f"Error getting {command_name} gif: {e}", ephemeral=True)

    @purr_nsfw_group.command(integration_types={IntegrationType.guild_install, IntegrationType.user_install}, name="cum", description="cum")
    async def cum(self, ctx: discord.ApplicationContext):
        await self.handle_nsfw_command(ctx, "cum", f"{ctx.author.mention} is cumming", NsfwApi().img_nsfw_cum_gif_get)

    @purr_nsfw_group.command(integration_types={IntegrationType.guild_install, IntegrationType.user_install}, name="solo", description="solo")
    async def solo(self, ctx: discord.ApplicationContext, 
                   sex: str = Option(name="sex", description="the sex", required=True, choices=["male", "female"])): 
        match sex:
            case "male":
                await self.handle_nsfw_command(ctx, "solomale", f"{ctx.author.mention} is masturbating", NsfwApi().img_nsfw_solo_male_gif_get)
            case "female":
                await self.handle_nsfw_command(ctx, "solofemale", f"{ctx.author.mention} is masturbating", NsfwApi().img_nsfw_solo_gif_get)
            case _:
                await ctx.respond(content="Invalid sex", ephemeral=True)
                return
    
    # TODO: add threesome with 2 users to choose from and type of fff ffm or mmf
    
    purr_nsfw_img_group = purr_group.create_subgroup(name="nsfw-img", description="Purr NSFW Image API commands")

    @purr_nsfw_img_group.command(integration_types={IntegrationType.guild_install, IntegrationType.user_install}, name="yaoi", description="yaoi")
    async def yaoi(self, ctx: discord.ApplicationContext):
        await self.handle_nsfw_command(ctx, "yaoi", f"{ctx.author.mention} here is a yaoi gif", NsfwApi().img_nsfw_yaoi_gif_get)

    @purr_nsfw_img_group.command(integration_types={IntegrationType.guild_install, IntegrationType.user_install}, name="yuri", description="yuri")
    async def yuri(self, ctx: discord.ApplicationContext):
        await self.handle_nsfw_command(ctx, "yuri", f"{ctx.author.mention} here is a yuri gif", NsfwApi().img_nsfw_yuri_gif_get)

    @purr_nsfw_img_group.command(integration_types={IntegrationType.guild_install, IntegrationType.user_install}, name="neko", description="neko")
    async def neko(self, ctx: discord.ApplicationContext,
                   type: str = Option(name="type", description="The type of neko to get", required=True, choices=["gif", "img"])):
        await self.handle_nsfw_command(ctx, f"neko-{type}", f"Here's a neko {ctx.author.mention}", lambda:NsfwApi().img_nsfw_neko_type_get(type))


def setup(bot):
    bot.add_cog(OwoCog(bot))