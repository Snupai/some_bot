import discord
from discord.ext import commands
import logging
import sqlite3
from datetime import datetime, timedelta
import time
from openai import OpenAI
import os
import tiktoken
import asyncio

bot_owner_id = 239809113125552129
DB_FILE = "user_threads.sqlite"
ASSISTANT_ID = "asst_n2rpn7o0MVIwSMihnPUuV3LI"
ai_model = "gpt-4o-mini"

model_token_encodings: dict = {
    "gpt-4o": "o200k_base",
    "gpt-4o-mini": "o200k_base",
    "gpt-4-turbo": "cl100k_base",
    "gpt-4": "cl100k_base",
    "gpt-3.5-turbo": "cl100k_base",
    "text-embedding-ada-002": "cl100k_base",
    "text-embedding-3-small": "cl100k_base",
    "text-embedding-3-large": "cl100k_base",
    "text-davinci-002": "p50k_base",
    "text-davinci-003": "p50k_base",
}

class BotDMCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger('bot.py')
        self.client = OpenAI(api_key=os.getenv('OPENAI_TOKEN'))
        self.initialize_db()

    def initialize_db(self):
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_threads (
                user_id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                expiry TIMESTAMP NOT NULL
            )
        ''')
        conn.commit()
        conn.close()

    async def wait_on_run(self, run, thread_id):
        timekeeper = time.time()
        while run.status in ["queued", "in_progress"]:
            run = self.client.beta.threads.runs.retrieve(
                thread_id=thread_id,
                run_id=run.id,
            )
            await asyncio.sleep(0.5)
            if time.time() - timekeeper > 90.0:  # timeout after 90 seconds
                raise Exception("Timeout: The operation took longer than expected.")
        return run

    async def manage_user_thread(self, user_id, message_content):
        current_time = datetime.now()
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()

        try:
            # Check if user has an existing thread and if it's expired
            cursor.execute("SELECT thread_id, expiry FROM user_threads WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()

            if row:
                thread_id, expiry = row
                expiry = datetime.fromisoformat(expiry)
                if current_time > expiry:
                    # Delete the old thread
                    self.client.beta.threads.delete(thread_id=thread_id)
                    cursor.execute("DELETE FROM user_threads WHERE user_id = ?", (user_id,))
                    conn.commit()
                    row = None

            # Create a new thread if user doesn't have one
            if not row:
                thread = self.client.beta.threads.create()
                thread_id = thread.id
                expiry = current_time + timedelta(hours=12)
                cursor.execute("INSERT INTO user_threads (user_id, thread_id, expiry) VALUES (?, ?, ?)",
                               (user_id, thread_id, expiry.isoformat()))
                conn.commit()

            self.logger.debug(f"thread {thread_id} for user {user_id}")

            # Add the message to the thread
            self.client.beta.threads.messages.create(
                thread_id=thread_id,
                role="user",
                content=message_content
            )

            # Create and run the assistant
            run = self.client.beta.threads.runs.create(
                thread_id=thread_id,
                assistant_id=ASSISTANT_ID
            )
            
            self.logger.debug(f"run {run} for thread {thread_id}")

            # Wait for the run to complete
            try:
                run = await self.wait_on_run(run, thread_id)
            except Exception as e:
                return f"Your request timed out. Please try again.\n{e}" 

            self.logger.debug(f"run {run} for thread {thread_id} completed")

            # Get the assistant's response
            messages = self.client.beta.threads.messages.list(thread_id=thread_id, order="desc", limit=1)
            response = messages.data[0].content[0].text.value

            # Update the expiry time
            expiry = current_time + timedelta(hours=12)
            cursor.execute("UPDATE user_threads SET expiry = ? WHERE user_id = ?", (expiry.isoformat(), user_id))
            conn.commit()

            return response

        except Exception as e:
            self.logger.error(f"Error in manage_user_thread: {e}")
            return "An error occurred while processing your request."

        finally:
            conn.close()
        
    async def delete_user_thread(self, user_id):
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        try:
            cursor.execute("DELETE FROM user_threads WHERE user_id = ?", (user_id,))
            conn.commit()
        except Exception as e:
            self.logger.error(f"Error in delete_user_thread: {e}")
            return "An error occurred while processing your request."
        finally:
            conn.close()

    async def get_token_count(self, text, model):
        encoding = model_token_encodings[model]
        tokens = tiktoken.get_encoding(encoding).encode(text)
        return len(tokens)

    @commands.Cog.listener()
    async def on_message(self, message):
        # Check if the message is a DM and not from a bot
        if message.channel.type == discord.ChannelType.private and message.author.id == bot_owner_id:
            # if message mentions the bot
            if message.mentions and message.content.startswith("!clear"):
                # delete the current user thread if it exists 
                await self.delete_user_thread(str(message.author.id))
            else:
                try:
                    # Handle AI response using manage_user_thread function
                    user_id = str(message.author.id)
                    user_name = message.author.display_name
                    message_content = f"{user_id}, {user_name}\n{message.content}"
                    user_message_tokens = await self.get_token_count(message_content, ai_model)
                    response = await self.manage_user_thread(user_id, message_content)
                    response_tokens = await self.get_token_count(response, ai_model)
                    response += f"\n-# prompt: {user_message_tokens} | response: {response_tokens} | total: {user_message_tokens + response_tokens}"

                    
                    # Send the assistant's response back to the user in DM
                    await message.channel.send(response)
                except Exception as e:
                    self.logger.error(f"Error handling DM: {e}")
                    await message.channel.send("An error occurred while processing your request.")

def setup(bot):
    bot.add_cog(BotDMCog(bot))
