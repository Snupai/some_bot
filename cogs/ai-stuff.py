import logging
import discord
from discord.ext import commands
from discord.ui import Button, View
import os
import aiohttp
import json
import base64
from pydub import AudioSegment
import asyncio

class AIcommandsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger('bot.py')

    @discord.slash_command(
        integration_types={discord.IntegrationType.guild_install, discord.IntegrationType.user_install}, 
        name="send_voice"
    )
    async def send_voice(self, ctx: discord.ApplicationContext):
        """Send a voice message"""
        file_path = "audio_file.ogg"
        if not os.path.exists(file_path):
            return

        try:
            # Step 1: Upload the file
            file_size = os.path.getsize(file_path)
            file_name = os.path.basename(file_path)



            # Request upload URL
            upload_url = f"https://discord.com/api/v9/channels/{ctx.channel.id}/attachments"
            headers = {'Authorization': f'Bot {self.bot.http.token}'}
            payload = {
                "files": [{"filename": file_name, "file_size": file_size, "id": "0"}]
            }
                    
            async with aiohttp.ClientSession() as session:
                async with session.post(upload_url, headers=headers, json=payload) as resp:
                    if resp.status != 200:
                        raise Exception(f"Failed to get upload URL. Status: {resp.status}")
                    upload_data = await resp.json()
            
                    # Debug logging to inspect the upload_data structure
                    print(f"Upload data: {json.dumps(upload_data, indent=2)}")  # Add this line
            
                # Upload the file
                put_url = upload_data['attachments'][0]['upload_url']  # Check if upload_data is valid
                put_headers = {
                    'Content-Type': 'audio/ogg',
                    'Content-Length': str(file_size)
                }
                with open(file_path, 'rb') as file:
                    async with session.put(put_url, headers=put_headers, data=file) as resp:
                        if resp.status != 200:
                            raise Exception(f"Failed to upload file. Status: {resp.status}")

                # Step 2: Send the message with the uploaded file
                audio = AudioSegment.from_ogg(file_path)
                duration_secs = round(len(audio) / 1000.0, 2)

                samples = audio.get_array_of_samples()
                step = max(1, len(samples) // 100)
                waveform = [abs(samples[i]) for i in range(0, len(samples), step)]
                max_val = max(waveform) if waveform else 1
                waveform = [int((val / max_val) * 255) for val in waveform]
                waveform = waveform[:100]
                waveform_data = base64.b64encode(bytes(waveform)).decode('utf-8')

                message_url = f"https://discord.com/api/v10/channels/{ctx.channel.id}/messages"
                message_payload = {
                    "content": "",
                    "tts": False,
                    "flags": 8192,
                    "attachments": [{
                        "id": "0",
                        "filename": upload_data['attachments'][0]['upload_filename'],  # Corrected line
                        "uploaded_filename": upload_data['attachments'][0]['upload_filename'],
                        "size": file_size,
                        "duration_secs": duration_secs,
                        "waveform": waveform_data
                    }]
                }

                async with session.post(message_url, headers=headers, json=message_payload) as resp:
                    
                    if resp.status not in (200, 201):
                        response_text = await resp.text()
                await ctx.delete()
        except Exception as e:
            await ctx.respond(f"Am i not in the user list?\nError sending voice message: {e}", ephemeral=True)



def setup(bot):
    bot.add_cog(AIcommandsCog(bot))