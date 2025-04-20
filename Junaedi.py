import datetime
import logging
import logging.handlers
import os
import subprocess
import re

import discord
import openai
import requests
import textract
import tiktoken
from discord.ext import commands, tasks
from discord import app_commands
from yt_dlp import YoutubeDL
from dotenv import load_dotenv


async def split_string_into_chunks(s, chunk_size):
    for i in range(0, len(s), chunk_size):
        yield s[i : i + chunk_size]


async def count_tokens_from_conversation(conversation):
    tokens_per_message = 4
    num_tokens = 0
    for message in conversation:
        num_tokens += tokens_per_message
        for key, value in message.items():
            num_tokens += len(token_counter.encode(value))
    return num_tokens


async def truncate_conversation(conversation):
    removed_indices = []
    for idx, message in enumerate(conversation):
        if idx == 0:
            continue
        removed_indices.append(idx)
        if message["role"] == "assistant":
            break
    removed_indices.reverse()
    for i in removed_indices:
        conversation.pop(i)
    return conversation


async def download_file(url, filename):
    filepath = f"Files/{filename}"
    response = requests.get(url, stream=True)
    if response.status_code == 200:
        with open(filepath, "wb") as out_file:
            for chunk in response.iter_content(chunk_size=8192):
                out_file.write(chunk)
        print(f"File downloaded and saved as {filepath}")
        return filepath, filename
    else:
        print(f"Failed to download file. Status code: {response.status_code}")
        return None
    

async def download_media(query):
    output = subprocess.run(
        [
            "yt-dlp",
            "-o",
            "Files/%(title)s.%(ext)s",
            "-x",
            "-f",
            "ba",
            "--audio-format",
            "best",
            "--print",
            "%(title)s.%(ext)s",
            "--no-simulate",
            "--default-search",
            "auto",
            "--max-downloads",
            "1",
            query,
        ],
        timeout=30,
        capture_output=True,
        text=True,
    )
    logging.debug(output.stdout)
    logging.warning(output.stderr)
    return output.stdout


load_dotenv()
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
DISCORD_API_KEY = os.getenv("DISCORD_API_KEY")

ai_client = openai.OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1",
)

with open("Prompts/System Prompt.txt", "r", encoding="utf-8") as f:
    system_prompt_base = f.read()

token_counter = tiktoken.get_encoding("cl100k_base")

if not os.path.isdir("Files"):
    os.makedirs("Files")
if not os.path.isdir("Logs"):
    os.makedirs("Logs")

max_tokens = 16384
message_size = 1950
conversation_history = {}

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
# intents.presences = True

username_pattern = r'<@([a-zA-Z0-9_.]+)>'

bot = commands.Bot(command_prefix=None, intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    logging.info(f"Logged in as {bot.user}")
    # try:
    #     synced_commands = await bot.tree.sync()
    #     print(f"Synced {len(synced_commands)} commands")
    #     logging.info(f"Synced {len(synced_commands)} commands")
    # except Exception as error:
    #     print(f"ERROR syncing commands: {error}")
    #     logging.error(f"ERROR syncing commands: {error}")

dev_commands = app_commands.Group(name="debug", description="Debug commands for develoment purposes only")

@dev_commands.command(name="sync-commands", description="Debug command for develoment purposes only")
async def sync_commands(interaction: discord.Interaction):
    try:
        synced_commands = await bot.tree.sync()
        print(f"Synced {len(synced_commands)} commands")
        logging.info(f"Synced {len(synced_commands)} commands")
        await interaction.response.send_message(content="Done", ephemeral=True)
    except Exception as error:
        print(f"ERROR syncing commands: {error}")
        logging.error(f"ERROR syncing commands: {error}")
        await interaction.response.send_message(content="Error syncing commands", ephemeral=True)

@dev_commands.command(name="list-servers", description="Debug command for develoment purposes only")
async def list_servers(interaction: discord.Interaction):
    servers = bot.guilds
    print(f"List of joined servers: \n{servers}")
    logging.debug(f"List of joined servers: \n{servers}")
    await interaction.response.send_message(content="Done", ephemeral=True)

bot.tree.add_command(dev_commands)

@bot.tree.command(name="reset-chat", description="Resets chat history for AI responses")
async def reset_chat(interaction: discord.Interaction):
    current_date = datetime.datetime.now().strftime("%A, %B %d, %Y")
    system_prompt = system_prompt_base.format(current_date=current_date)

    conversation_history[interaction.guild.id] = [
        {"role": "system", "content": system_prompt}
    ]
    await interaction.response.send_message(content=f"Chat history has been reset")


@bot.tree.command(
    name="play", description="Plays an audio by providing a URL or name to search"
)
@discord.app_commands.describe(query="Provide URL or search query")
@discord.app_commands.rename(query="input")
@discord.app_commands.guild_only()
async def play(interaction: discord.Interaction, query: str):
    await interaction.response.defer(ephemeral=True, thinking=True)
    if not interaction.user.voice:
        await interaction.followup.send(content="u stupid (masuk voice channel dulu kocag)", ephemeral=True)
        return
    if not discord.utils.get(bot.voice_clients, guild=interaction.guild):
        await interaction.user.voice.channel.connect(self_deaf=True)
    stdout = await download_media(query)
    filename = str(stdout).split("\n")[0]
    source = await discord.FFmpegOpusAudio.from_probe(f"Files/{filename}")
    voice_client = discord.utils.get(bot.voice_clients, guild=interaction.guild)
    voice_client.play(source)
    await interaction.followup.send(content=f"u stupid", ephemeral=True)


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    if bot.user in message.mentions:
        current_date = datetime.datetime.today().strftime("%A, %d %B %Y")
        system_prompt = system_prompt_base.format(current_date=current_date)

        if message.guild.id not in conversation_history:
            conversation_history[message.guild.id] = [
                {"role": "system", "content": system_prompt}
            ]

        if message.attachments:
            file_attachment_prompt = "User uploaded the following file:"
        for attachments in message.attachments:
            try:
                attachment_filepath, attachment_filename = await download_file(
                    url=attachments.url, filename=attachments.filename
                )
                attached_text = textract.process(attachment_filepath)
                file_attachment_prompt += (
                    f"\n\nFilename: {attachment_filename}\nContent: {attached_text}"
                )
                os.remove(attachment_filename)
            except Exception as error:
                print(f"Error processing attachment: {error}")
                logging.error(f"Error processing attachment: {error}")
        if message.attachments:
            conversation_history[message.guild.id].append(
                {"role": "system", "content": file_attachment_prompt}
            )

        author = str(message.author)
        content = f"{author}: {message.content}"

        for user in message.mentions:
            mention_str = f"<@{user.id}>"
            username_str = f"<@{user.name}>"
            content = content.replace(mention_str, username_str)

        conversation_history[message.guild.id].append(
            {"role": "user", "name": author, "content": content}
        )

        total_tokens = await count_tokens_from_conversation(
            conversation_history[message.guild.id]
        )
        while total_tokens > max_tokens:
            conversation_history[message.guild.id] = await truncate_conversation(
                conversation_history[message.guild.id]
            )
            total_tokens = await count_tokens_from_conversation(
                conversation_history[message.guild.id]
            )

        response = ai_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=conversation_history[message.guild.id],
            temperature=0.7,
            top_p=0.95,
            max_tokens=768,
        )
        logging.debug(f"AI Response: {response}")

        current_response = response.choices[0].message.content
        conversation_history[message.guild.id].append(
            {"role": "assistant", "name": "Junaedi", "content": current_response}
        )

        username_matches = re.findall(username_pattern, current_response)
        for username in username_matches:
            user_id = message.guild.get_member_named(username).id
            current_response = current_response.replace(f"<@{username}>", f"<@{user.id}>")

        if len(current_response) > message_size:
            async for chunk in split_string_into_chunks(
                current_response, message_size
            ):
                await message.channel.send(chunk)
        else:
            await message.channel.send(current_response)


handler = logging.handlers.RotatingFileHandler(
    filename="Logs/junaedi.log",
    encoding="utf-8",
    maxBytes=1024 * 1024 * 16,  # MiB
    backupCount=7,
)

bot.run(DISCORD_API_KEY, log_handler=handler, log_level=logging.DEBUG, root_logger=True)
