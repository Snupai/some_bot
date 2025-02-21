import logging
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
import os
import datetime
import time
import signal
import sys
import asyncio

COOKIES_FILE = 'cookies.txt'

GUILD_INSTALL_LINK = "https://discord.com/oauth2/authorize?client_id=1219270011164688514&permissions=1759214275783799&redirect_uri=https%3A%2F%2Fdiscord.com%2Foauth2%2Fauthorize%3Fclient_id%3D1219270011164688514&integration_type=0&scope=bot"
USER_INSTALL_LINK = "https://discord.com/oauth2/authorize?client_id=1219270011164688514&redirect_uri=https%3A%2F%2Fdiscord.com%2Foauth2%2Fauthorize%3Fclient_id%3D1219270011164688514&integration_type=1&scope=applications.commands"

# Load the environment variables from .env file
load_dotenv()

class Bot(commands.AutoShardedBot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        intents.voice_states = True
        
        super().__init__(intents=intents, sync_commands=True)
        self.start_time = time.time()
        self.logger = setup_logger()
        
        # Load all cogs
        self.load_extensions()
        
        # Register signal handlers
        self.setup_signal_handlers()
        
    def load_extensions(self) -> None:
        """Load all cogs from the cogs directory"""
        for filename in os.listdir('./cogs'):
            if filename.endswith('.py'):
                try:
                    self.load_extension(f'cogs.{filename[:-3]}')
                    self.logger.info(f"Loaded extension: {filename}")
                except Exception as e:
                    self.logger.error(f"Failed to load extension: {filename}")
                    self.logger.error(f"Error: {str(e)}")
                    print(f"Error loading extension: {filename}")
                    print(f"Error: {str(e)}")

    def setup_signal_handlers(self) -> None:
        """Setup handlers for various termination signals"""
        signals = (signal.SIGTERM, signal.SIGINT, signal.SIGBREAK if sys.platform == "win32" else signal.SIGQUIT)
        for sig in signals:
            signal.signal(sig, self.handle_signal)
            
    def handle_signal(self, signum, frame) -> None:
        """Handle termination signals"""
        self.logger.info(f"Received signal {signum}. Starting clean shutdown...")
        
        # Create an asyncio task for cleanup
        if sys.platform == "win32":
            loop = asyncio.get_event_loop()
        else:
            loop = asyncio.get_running_loop()
            
        loop.create_task(self.cleanup())
        
    async def cleanup(self):
        """Perform cleanup operations before shutdown"""
        self.logger.info("Starting cleanup...")
        
        # Cancel all running tasks
        tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
            
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            self.logger.error(f"Error during task cleanup: {e}")
            
        # Unload all cogs
        for extension in list(self.extensions):
            try:
                await self.unload_extension(extension)
                self.logger.info(f"Unloaded extension: {extension}")
            except Exception as e:
                self.logger.error(f"Error unloading extension {extension}: {e}")
                
        # Close the bot connection
        try:
            await self.close()
        except Exception as e:
            self.logger.error(f"Error closing bot connection: {e}")
            
        self.logger.info("Cleanup completed. Shutting down...")
        sys.exit(0)

# Create a logger with timestamp in the file name
def setup_logger():
    """
    Setup the logger.
    """
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
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

def run_bot():
    """Initialize and run the bot"""
    load_dotenv()
    bot = Bot()
    
    try:
        bot.run(os.getenv('BOT_TOKEN'))
        bot.logger.info("Bot started")
        bot.logger.info(f"Bot is running as {bot.user.name} in {len(bot.guilds)} guilds")
    except Exception as e:
        bot.logger.error(f"Error running bot: {e}")
        sys.exit(1)

if __name__ == "__main__":
    run_bot()