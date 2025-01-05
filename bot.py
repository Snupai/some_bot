import logging
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
import os
import datetime
import time

COOKIES_FILE = 'cookies.txt'

GUILD_INSTALL_LINK = "https://discord.com/oauth2/authorize?client_id=1219270011164688514&permissions=1759214275783799&redirect_uri=https%3A%2F%2Fdiscord.com%2Foauth2%2Fauthorize%3Fclient_id%3D1219270011164688514&integration_type=0&scope=bot"
USER_INSTALL_LINK = "https://discord.com/oauth2/authorize?client_id=1219270011164688514&redirect_uri=https%3A%2F%2Fdiscord.com%2Foauth2%2Fauthorize%3Fclient_id%3D1219270011164688514&integration_type=1&scope=applications.commands"

# Load the environment variables from .env file
load_dotenv()

# Create a new bot instance
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.voice_states = True

bot = commands.AutoShardedBot(intents=intents, sync_commands=True)

timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

# Set the start time when the bot starts
bot.start_time = time.time()

# Create a logger with timestamp in the file name
def setup_logger():
    """
    Setup the logger.
    """
    log_file = f"bot_{timestamp}.log"
    logger = logging.getLogger('bot.py')
    logger.setLevel(logging.DEBUG)

    # Create a file handler and set the log level
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)

    # Create a console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    # Create a formatter and add it to the file handler
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    # Add the handlers to the logger
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger

logger = setup_logger()

# load all cogs within the cogs directory
for filename in os.listdir('./cogs'):
    if filename.endswith('.py'):
        try:
            bot.load_extension(f'cogs.{filename[:-3]}')
            logger.info(f"Loaded extension: {filename}")
        except Exception as e:
            logger.error(f"Failed to load extension: {filename}")
            logger.error(f"Error: {str(e)}")
            print(f"Error loading extension: {filename}")
            print(f"Error: {str(e)}")

DEFAULT_ACTIVITY: str = "Meaw~"
activity: str = DEFAULT_ACTIVITY

@bot.event
async def on_command_error(ctx, error):
    """
    Event triggered when a command fails.
    """
    logger.error(f"Command {ctx.command} failed with error: {str(error)}")

@bot.event
async def on_ready():
    """
    Event triggered when the bot is ready.
    """
    logger.info(f'Logged in as {bot.user.name}')
    logger.info(f'ID: {bot.user.id}')
    logger.info(f"Guild install link: {GUILD_INSTALL_LINK}")
    logger.info(f"User install link: {USER_INSTALL_LINK}") 

    activity = DEFAULT_ACTIVITY

    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.playing, name=activity))

    # delete all commands and recreate them
    await bot.sync_commands()

@tasks.loop(minutes=1)
async def change_activity():
    """
    Change the bot's activity every 60 seconds.
    """
    # Set the activity
    global activity # make 'activity' a global variable so it can be accessed by the function

    
    # Cycle through a list of activities
    activities = ["Having fun", "Nya", "Meaw~"]
    activity = activities[(activities.index(activity) + 1) % len(activities)]

    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.playing, name=activity))

change_activity.start()

@bot.slash_command(integration_types={discord.IntegrationType.user_install}, name="reload_cogs", description="Reload specified cogs or all cogs.")
@commands.is_owner()
async def reload_cogs(ctx: discord.ApplicationContext, cog_names: str = None):
    """
    Reload specified cogs or all cogs.
    Parameters:
        cog_names (str): Optional. Comma-separated list of cog names to reload. If not provided, all cogs will be reloaded.
    """
    if cog_names:
        # Split the cog names and remove any whitespace
        cogs_to_reload = [name.strip() for name in cog_names.split(',')]
    else:
        # If no cogs specified, get all cogs
        cogs_to_reload = [filename[:-3] for filename in os.listdir('./cogs') if filename.endswith('.py')]

    success_cogs = []
    failed_cogs = []

    for cog_name in cogs_to_reload:
        try:
            extension_name = f'cogs.{cog_name}'
            # Check if the extension exists
            if not os.path.exists(f'./cogs/{cog_name}.py'):
                failed_cogs.append((cog_name, "Cog file not found"))
                continue
                
            # Attempt to reload the extension
            bot.reload_extension(extension_name)
            logger.info(f"Reloaded extension: {cog_name}")
            success_cogs.append(cog_name)
        except Exception as e:
            logger.error(f"Failed to reload extension: {cog_name}")
            logger.error(f"Error: {str(e)}")
            failed_cogs.append((cog_name, str(e)))

    # Create embed
    embed = discord.Embed(
        title="Cog Reload Status",
        color=discord.Color.blue(),
        timestamp=datetime.datetime.now()
    )

    # Add successful reloads if any
    if success_cogs:
        embed.add_field(
            name="✅ Successfully Reloaded",
            value=", ".join(success_cogs),
            inline=False
        )

    # Add failed reloads if any
    if failed_cogs:
        failed_text = "\n".join([f"• **{cog}**: {error}" for cog, error in failed_cogs])
        embed.add_field(
            name="❌ Failed to Reload",
            value=failed_text or "None",
            inline=False
        )

    # Add footer
    embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.avatar.url if ctx.author.avatar else None)
    await bot.sync_commands()
    await ctx.respond(embed=embed, ephemeral=True)


# Run the bot
bot.run(os.getenv('BOT_TOKEN'))