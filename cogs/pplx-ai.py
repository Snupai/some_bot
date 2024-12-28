if __name__ == "__main__":
    print("This is a cog file and cannot be run directly.")
    exit()

import discord
from discord.ext import commands
import logging
from openai import OpenAI
import os
import enum
import re
import sqlite3

class PplxAiModels(enum.Enum):
    LLAMA_3_1_SONAR_SMALL_128K_ONLINE = "llama-3.1-sonar-small-128k-online"
    LLAMA_3_1_SONAR_LARGE_128K_ONLINE = "llama-3.1-sonar-large-128k-online"
    LLAMA_3_1_SONAR_HUGE_128K_ONLINE = "llama-3.1-sonar-huge-128k-online"


class PPLXAICog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger('bot.py')
        
    pplxai = discord.SlashCommandGroup(integration_types={discord.IntegrationType.user_install}, name="pplx-ai", description="Perplexity AI API")

    def split_text(self, text: str, max_length: int = 2000) -> list[str]:
        """
        Splits text into segments of a maximum length while preserving code blocks.

        Args:
            text (str): The text to split.
            max_length (int, optional): The maximum length of each segment. Defaults to 2000.

        Returns:
            list[str]: A list of segments of the text.
        """

        code_block_pattern = re.compile(r'```(.*?)\n(.*?)(?=\n```|\Z)', re.DOTALL)
        segments = []

        while len(text) > max_length:
            match = code_block_pattern.search(text)
            if match:
                text = self._handle_code_block(text, match, segments, max_length)
            else:
                text = self._handle_text_segment(text, segments, max_length)

        if text:
            segments.append(text)

        return segments

    def _handle_code_block(self, text, match, segments, max_length):
        start, end = match.span()
        if start > 0:
            segments.append(text[:start].rstrip())
            text = text[start:]

        code_block_content = match.group(0)
        if len(code_block_content) > max_length:
            split_code_blocks = self.split_text(code_block_content, max_length)
            segments.extend(split_code_blocks)
        else:
            segments.append(code_block_content)

        return text[end:].lstrip()

    def _handle_text_segment(self, text, segments, max_length):
        split_point = self._find_split_point(text, max_length)
        if split_point is None:
            split_point = max_length

        segments.append(text[:split_point].rstrip())
        return text[split_point:].lstrip()

    def _find_split_point(self, text, max_len):
        min_len = max_len // 2
        priority_patterns = [
            r'\n# ',      # Highest priority
            r'\n## ',     # Next priority
            r'\n### ',    # Followed by
            r'\n\d',     # followed by a digit
            r'\n\n',     # Two newlines
            r'\n'         # Single newline
        ]
        for pattern in priority_patterns:
            matches = list(re.finditer(pattern, text))
            for match in reversed(matches):
                if min_len <= match.start() <= max_len:
                    return match.start()
        return None

    async def is_user_allowed(self, user):
        # check the allowed_users.sqlite file for the user
        conn = sqlite3.connect('allowed_users.sqlite')
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM allowed_users WHERE user_id = ?', (user.id,))
        result = cursor.fetchone()
        if result:
            return True
        return False
        

    @pplxai.command(integration_types={discord.IntegrationType.user_install}, name="ask", description="Ask Perplexity AI something")
    async def ask_pplx_ai(self, ctx: discord.ApplicationContext,
                    prompt: str = discord.Option(name="prompt", description="The prompt to send to PPLX AI", required=True),
                    model: str = discord.Option(name="model", description="The model to use", required=False, choices=[x.value for x in PplxAiModels])):
        """
        Command to ask PPLX AI something
        """
        if not await self.is_user_allowed(ctx.author):
            await ctx.respond(content="You are not allowed to use this command.", ephemeral=True)
            return
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
        citations = response.citations
        content = response.choices[0].message.content
        content = content.replace("####", "###") # for discord compatibility

        # Replace occurrences of [n] with [n](citations[n])
        for index, citation in enumerate(citations):
            content = content.replace(f"[{index}]", f"[{index}](<{citation}>)")

        if len(content) > 2000:  
                chunks = self.split_text(content)
                await ctx.respond(content=chunks[0])
                for chunk in chunks[1:]:
                    await ctx.followup.send(content=chunk)
        else:
                await ctx.respond(content=f"{content}")

def setup(bot):
    bot.add_cog(PPLXAICog(bot))