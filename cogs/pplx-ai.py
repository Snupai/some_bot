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

class PplxAiModels(enum.Enum):
    LLAMA_3_1_SONAR_SMALL_128K_ONLINE = "llama-3.1-sonar-small-128k-online"
    LLAMA_3_1_SONAR_LARGE_128K_ONLINE = "llama-3.1-sonar-large-128k-online"
    LLAMA_3_1_SONAR_HUGE_128K_ONLINE = "llama-3.1-sonar-huge-128k-online"


class PPLXAICog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger('bot.py')

    def split_text(self, text: str, max_length: int = 2000) -> list[str]:
        """
        Splits text into segments of a maximum length.

        Args:
            text (str): The text to split.
            max_length (int, optional): The maximum length of each segment. Defaults to 2000.

        Returns:
            list[str]: A list of segments of the text.
        """

        def find_split_point(text, max_len, min_len, priority_patterns):
            for pattern in priority_patterns:
                matches = list(re.finditer(pattern, text))
                for match in reversed(matches):  # Start checking from the closest match before max_len
                    if min_len <= match.start() <= max_len:
                        return match.start()
            return None

        priority_patterns = [
            r'\n# ',      # Highest priority
            r'\n## ',     # Next priority
            r'\n### ',    # Followed by
            r'\n\d',     # followed by a digit
            r'\n\n',     # Two newlines
            r'\n'         # Single newline
        ]

        min_length = max_length // 2
        segments = []

        while len(text) > max_length:
            split_point = find_split_point(text, max_length, min_length, priority_patterns)
            if split_point is None:  # No suitable split point found, force a split at max_length
                split_point = max_length

            segments.append(text[:split_point].rstrip())
            text = text[split_point:].lstrip()

        if text:  # Add any remaining text
            segments.append(text)

        return segments


    @discord.slash_command(integration_types={discord.IntegrationType.user_install}, name="pplx-ai", description="Ask PPLX AI something")
    async def pplx_ai(self, ctx: discord.ApplicationContext,
                    prompt: str = discord.Option(name="prompt", description="The prompt to send to PPLX AI", required=True),
                    model: str = discord.Option(name="model", description="The model to use", required=False, choices=[x.value for x in PplxAiModels])):
        """
        Command to ask PPLX AI something
        """
        self.logger.info(f"{ctx.author} used /pplx-ai command in {ctx.channel} on {ctx.guild}.")

        await ctx.defer()

        if model is None:
            model = PplxAiModels.LLAMA_3_1_SONAR_SMALL_128K_ONLINE.value

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
                chunks = self.split_text(content)
                await ctx.respond(content=chunks[0])
                for chunk in chunks[1:]:
                    await ctx.followup.send(content=chunk)
        else:
                await ctx.respond(content=f"{content}")

def setup(bot):
    bot.add_cog(PPLXAICog(bot))