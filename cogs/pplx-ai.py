if __name__ == "__main__":
    print("This is a cog file and cannot be run directly.")
    exit()

import discord
from discord.ext import commands
import asyncio
import logging
from openai import OpenAI
import os
import enum
import re

class PPLXAI_Models(enum.Enum):
    LLAMA_3_1_SONAR_SMALL_128K_ONLINE = "llama-3.1-sonar-small-128k-online"
    LLAMA_3_1_SONAR_LARGE_128K_ONLINE = "llama-3.1-sonar-large-128k-online"
    LLAMA_3_1_SONAR_HUGE_128K_ONLINE = "llama-3.1-sonar-huge-128k-online"


class PPLXAICog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger('bot.py')


    def split_string_by_newline(self, input_string: str, max_length: int = 2000) -> list[str]:
        """
        Splits a string into chunks of a maximum length.
        
        Args:
            input_string (str): The input string to split.
            max_length (int, optional): The maximum length of each chunk. Defaults to 2000.
        
        Returns:
            list[str]: A list of chunks of the input string.
        """
        # Split the input string into lines
        lines = input_string.splitlines()
        result = []
        current_chunk = []
        current_length = 0
        open_code_block = False  # Tracks if a code block is open
        code_block_header = None  # Tracks the opening line of the current code block

        for line in lines:
            # Replace #### with ### to make it compatible with Discord
            line = re.sub(r"^(####)", "###", line)

            line_length = len(line) + 1  # Account for newline character
            
            # Detect if this line opens or closes a code block
            if line.strip().startswith("```"):
                if open_code_block:
                    open_code_block = False  # Closing the block
                    code_block_header = None
                else:
                    open_code_block = True  # Opening the block
                    code_block_header = line

            # If adding this line would exceed the max_length
            if not open_code_block and current_length + line_length > max_length:
                # Find the last suitable splitting point (line starting with # or digit)
                for i in range(len(current_chunk) - 1, -1, -1):
                    if re.match(r"^[#\d]", current_chunk[i]):
                        # Split here
                        result.append("\n".join(current_chunk[:i + 1]))
                        current_chunk = current_chunk[i + 1:]
                        current_length = sum(len(l) + 1 for l in current_chunk)
                        break
                else:
                    # If no suitable split point is found, split anyway
                    result.append("\n".join(current_chunk))
                    current_chunk = []
                    current_length = 0
            
            # Handle open code blocks during splitting
            if open_code_block and current_length + line_length > max_length:
                # Close the current chunk and start a new one with the repeated code block header
                if code_block_header:
                    current_chunk.append("```")  # Close the block in the current chunk
                    result.append("\n".join(current_chunk))
                    # Start the new chunk with the repeated code block header
                    current_chunk = [code_block_header]
                    current_length = len(code_block_header) + 1  # Reset length tracker
                continue

            # Add the current line to the chunk
            current_chunk.append(line)
            current_length += line_length

        # Add any remaining lines in the last chunk
        if current_chunk:
            if open_code_block:
                current_chunk.append("```")  # Close any unclosed code block
            result.append("\n".join(current_chunk))
        
        return result


    @discord.slash_command(integration_types={discord.IntegrationType.user_install}, name="pplx-ai", description="Ask PPLX AI something")
    async def pplx_ai(self, ctx: discord.ApplicationContext,
                    prompt: str = discord.Option(name="prompt", description="The prompt to send to PPLX AI", required=True),
                    model: str = discord.Option(name="model", description="The model to use", required=False, choices=[x.value for x in PPLXAI_Models])):
        """
        Command to ask PPLX AI something
        """
        self.logger.info(f"{ctx.author} used /pplx-ai command in {ctx.channel} on {ctx.guild}.")

        await ctx.defer()

        if model is None:
            model = PPLXAI_Models.LLAMA_3_1_SONAR_SMALL_128K_ONLINE.value

        pplxai = OpenAI(api_key=os.getenv('PPLX_TOKEN'), base_url="https://api.perplexity.ai")
        response = pplxai.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an artificial intelligence assistant and you need to "
                        "engage in a helpful, detailed, polite conversation with a user."
                    ),
                },
                {   
                    "role": "user",
                    "content": (
                        prompt
                    ),
                },
            ]
        )
        content = response.choices[0].message.content
        if len(content) > 2000:  
                chunks = self.split_string_by_newline(content)
                await ctx.respond(content=chunks[0])
                for chunk in chunks[1:]:
                    await ctx.respond(content=chunk)
        else:
                await ctx.respond(content=f"{content}")

def setup(bot):
    bot.add_cog(PPLXAICog(bot))