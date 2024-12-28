import logging
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
import os
import datetime

make_ephemeral = False
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

bot = commands.AutoShardedBot(intents=intents, sync_commands=True, help_command=None)

timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

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


# Run the bot
bot.run(os.getenv('BOT_TOKEN'))