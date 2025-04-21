import datetime
import logging
import logging.handlers
import os
import subprocess
import re

import discord
import requests
import textract
import tiktoken
from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse
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


async def remove_old_messages(
    conversation: list[ModelMessage], max_age: int
) -> list[ModelMessage]:
    indices_to_remove = []
    for idx, message in enumerate(conversation):
        if (
            isinstance(message, ModelRequest)
            and (
                datetime.datetime.now(datetime.timezone.utc)
                - message.parts[0].timestamp
            ).days
            >= max_age
        ):
            indices_to_remove.append(idx)
        elif (
            isinstance(message, ModelResponse)
            and (datetime.datetime.now(datetime.timezone.utc) - message.timestamp).days
            >= max_age
        ):
            indices_to_remove.append(idx)
    indices_to_remove.reverse()
    for i in indices_to_remove:
        conversation.pop(i)
    return conversation


async def truncate_conversation(
    conversation: list[ModelMessage], max_messages: int, max_age: int
) -> list[ModelMessage]:
    conversation = await remove_old_messages(conversation, max_age)
    while len(conversation) > max_messages:
        conversation.pop(0)
        while isinstance(conversation[0], ModelResponse):
            conversation.pop(0)
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

model_settings = ModelSettings(
    max_tokens=768,
    temperature=0.7,
    top_p=0.95,
)
agent = Agent(
    model="groq:meta-llama/llama-4-scout-17b-16e-instruct",
    model_settings=model_settings,
)

with open("Prompts/System Prompt.txt", "r", encoding="utf-8") as f:
    system_prompt_base = f.read()


@agent.instructions
def add_instructions() -> str:
    return system_prompt_base.format(
        current_date=datetime.datetime.now(datetime.timezone.utc).strftime(
            "%A, %d %B %Y"
        )
    )


token_counter = tiktoken.get_encoding("cl100k_base")

if not os.path.isdir("Files"):
    os.makedirs("Files")
if not os.path.isdir("Logs"):
    os.makedirs("Logs")

max_tokens = 16384
message_size = 1950
max_messages = 40  # Maximum amount of messages kept in conversation history, including AI responses
max_message_age = 2  # Days
attachment_max_chars = 10000
conversation_history = {}

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
# intents.presences = True

username_pattern1 = r"<@([a-zA-Z0-9_.]+)>"
username_pattern2 = r"@<([a-zA-Z0-9_.]+)>"

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


dev_commands = app_commands.Group(
    name="debug", description="Debug commands for development purposes only"
)


@dev_commands.command(
    name="sync-commands", description="Debug command for development purposes only"
)
async def sync_commands(interaction: discord.Interaction):
    try:
        synced_commands = await bot.tree.sync()
        print(f"Synced {len(synced_commands)} commands")
        logging.info(f"Synced {len(synced_commands)} commands")
        await interaction.response.send_message(content="Done", ephemeral=True)
    except Exception as error:
        print(f"ERROR syncing commands: {error}")
        logging.error(f"ERROR syncing commands: {error}")
        await interaction.response.send_message(
            content="Error syncing commands", ephemeral=True
        )


@dev_commands.command(
    name="list-servers", description="Debug command for development purposes only"
)
async def list_servers(interaction: discord.Interaction):
    servers = bot.guilds
    print(f"List of joined servers: \n{servers}")
    logging.debug(f"List of joined servers: \n{servers}")
    await interaction.response.send_message(content="Done", ephemeral=True)


bot.tree.add_command(dev_commands)


@bot.tree.command(name="reset-chat", description="Resets chat history for AI responses")
async def reset_chat(interaction: discord.Interaction):
    conversation_history[interaction.guild.id] = []
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
        await interaction.followup.send(
            content="u stupid (masuk voice channel dulu kocag)", ephemeral=True
        )
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

        if message.guild.id not in conversation_history:
            conversation_history[message.guild.id] = []

        author = str(message.author)
        content = f"{author}: {message.content}"

        for user in message.mentions:
            mention_str = f"<@{user.id}>"
            username_str = f"<@{user.name}>"
            content = content.replace(mention_str, username_str)

        if message.attachments:
            file_attachment_prompt = f"{author} uploaded the following file:"
        for attachments in message.attachments:
            try:
                attachment_filepath, attachment_filename = await download_file(
                    url=attachments.url, filename=attachments.filename
                )
                attached_text = textract.process(attachment_filepath)
                file_attachment_prompt += f"\nFilename: {attachment_filename}\nContent: {attached_text[:attachment_max_chars]}"
                # os.remove(attachment_filename)
            except Exception as error:
                print(f"Error processing attachment: {error}")
                logging.error(f"Error processing attachment: {error}")

        current_request = []
        if message.attachments:
            current_request.append(file_attachment_prompt)
        current_request.append(content)
        conversation_history[message.guild.id] = await truncate_conversation(
            conversation_history[message.guild.id], max_messages, max_message_age
        )

        response = await agent.run(
            current_request, message_history=conversation_history[message.guild.id]
        )
        logging.debug(f"New response: {response.output}")

        conversation_history[message.guild.id].extend(response.new_messages())

        current_response = response.output

        username_matches = re.findall(username_pattern1, current_response)
        for username in username_matches:
            user_id = message.guild.get_member_named(username).id
            current_response = current_response.replace(
                f"<@{username}>", f"<@{user_id}>"
            )

        # We use 2 regex patterns because llms are stupid and they sometimes do @<username> instead of <@username>
        username_matches = re.findall(username_pattern2, current_response)
        for username in username_matches:
            user_id = message.guild.get_member_named(username).id
            current_response = current_response.replace(
                f"@<{username}>", f"<@{user_id}>"
            )

        if len(current_response) > message_size:
            async for chunk in split_string_into_chunks(current_response, message_size):
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
