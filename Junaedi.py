import datetime
import logging
import logging.handlers
import os

import discord
import openai
import requests
import textract
import tiktoken
from discord.ext import commands, tasks

from Tokens import *


def split_string_into_chunks(s, chunk_size):
    for i in range(0, len(s), chunk_size):
        yield s[i:i+chunk_size]

def download_file(url, filename):
    filepath = f"Files/{filename}"
    response = requests.get(url, stream=True)
    if response.status_code == 200:
        with open(filepath, 'wb') as out_file:
            for chunk in response.iter_content(chunk_size=8192):
                out_file.write(chunk)
        print(f"File downloaded and saved as {filepath}")
        return filepath, filename
    else:
        print(f"Failed to download file. Status code: {response.status_code}")
        return None

def count_tokens_from_conversation(conversation):
    tokens_per_message = 4
    num_tokens = 0
    for message in conversation:
        num_tokens += tokens_per_message
        for key, value in message.items():
            num_tokens += len(token_counter.encode(value))
    return num_tokens

def truncate_conversation(conversation):
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

ai_client = openai.OpenAI(
    api_key=groq_token,
    base_url="https://api.groq.com/openai/v1",
)

f = open("System Prompt.txt", "r")
system_prompt_base = f.read()
f.close()

token_counter = tiktoken.get_encoding("cl100k_base")

if not os.path.isdir("Files"):
    os.makedirs("Files")

max_tokens = 16384
message_size = 1950
conversation_history = {}

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
# intents.presences = True

bot = commands.Bot(command_prefix=None, intents=intents)

@bot.event
async def on_ready():
    print(f"We have logged in as {bot.user}")
    try:
        synced_commands = await bot.tree.sync()
        print(f"Synced {len(synced_commands)} commands")
    except Exception as error:
        print(f"ERROR syncing commands: {error}")

@bot.tree.command(name="reset-chat", description="Resets chat history for AI responses")
async def reset_chat(interaction: discord.Interaction):
    current_date = datetime.datetime.now().strftime("%A, %B %d, %Y")
    system_prompt = system_prompt_base.format(current_date=current_date)

    conversation_history[interaction.guild.id] = [  
        {"role": "system", "content": system_prompt}
    ]
    await interaction.response.send_message(content=f"Chat history has been reset")

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    # if message.content.startswith("$hello"):
    #     await message.channel.send("Hello!")

    if bot.user in message.mentions:
        current_date = datetime.datetime.now().strftime("%A, %B %d, %Y")
        system_prompt = system_prompt_base.format(current_date=current_date)

        if message.guild.id not in conversation_history:
            conversation_history[message.guild.id] = [  
                {"role": "system", "content": system_prompt}
            ]

        if message.attachments:
            file_attachment_prompt = "User uploaded the following file:"
        for attachments in message.attachments:
            try:
                attachment_filepath, attachment_filename = download_file(url=attachments.url, filename=attachments.filename)
                attached_text = textract.process(attachment_filepath)
                file_attachment_prompt += f"\n\nFilename: {attachment_filename}\nContent: {attached_text}"
                os.remove(attachment_filename)
            except Exception as error:
                print(f"ERROR processing attachment: {error}")
        if message.attachments:
            conversation_history[message.guild.id].append({"role": "system",  "content": file_attachment_prompt})

        author = str(message.author)
        content = f"{author}: {message.content}"

        for user in message.mentions:
            mention_str = f'<@{user.id}>'
            username_str = f'@{user.name}'
            content = content.replace(mention_str, username_str)

        conversation_history[message.guild.id].append({"role": "user", "name": author, "content": content})

        total_tokens = count_tokens_from_conversation(conversation_history[message.guild.id])
        while total_tokens > max_tokens:
            conversation_history[message.guild.id] = truncate_conversation(conversation_history[message.guild.id])
            total_tokens = count_tokens_from_conversation(conversation_history[message.guild.id])

        response = ai_client.chat.completions.create(
            model="llama-3.1-70b-versatile",
            messages=conversation_history[message.guild.id],
            temperature=0.7,
            max_tokens=2048,
        )

        # print(f"Message:\n{message.author}: {content}")
        # print(f"Response:\n{response.choices[0].message.content}")

        print(response, end="\n\n")

        if len(response.choices[0].message.content) > message_size:
            for chunk in split_string_into_chunks(response.choices[0].message.content, message_size):
                await message.channel.send(chunk)
        else:
            await message.channel.send(response.choices[0].message.content)

handler = logging.handlers.RotatingFileHandler(
    filename='Logs/discord.log',
    encoding='utf-8',
    maxBytes=1024 * 1024 * 8,  # x MiB
    backupCount=7,
)

bot.run(discord_token, log_handler=handler, log_level=logging.DEBUG)