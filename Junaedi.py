import discord, logging, os
import logging.handlers
from Tokens import *
import google.generativeai as genai

def split_string_into_chunks(s, chunk_size):
    for i in range(0, len(s), chunk_size):
        yield s[i:i+chunk_size]

genai.configure(api_key=gemini_token)

generation_config = {
    "temperature": 1,
    "top_p": 0.95,
    "top_k": 64,
    "max_output_tokens": 8192,
    "response_mime_type": "text/plain",
}

safety_settings = [
    {
        "category": "HARM_CATEGORY_HARASSMENT",
        "threshold": "BLOCK_ONLY_HIGH",
    },
    {
        "category": "HARM_CATEGORY_HATE_SPEECH",
        "threshold": "BLOCK_ONLY_HIGH",
    },
    {
        "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
        "threshold": "BLOCK_ONLY_HIGH",
    },
    {
        "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
        "threshold": "BLOCK_ONLY_HIGH",
    },
]

model = genai.GenerativeModel(
    model_name="gemini-1.5-flash-latest",
    safety_settings=safety_settings,
    generation_config=generation_config,
    system_instruction="Your name is Junaedi. You are an AI assistant made by Zoont",
)

message_size = 1950
conversation_history = {}

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f"We have logged in as {client.user}")

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    # if message.content.startswith("$hello"):
    #     await message.channel.send("Hello!")

    if client.user in message.mentions:
        if message.guild.id not in conversation_history:
            conversation_history[message.guild.id] = []

        chat_session = model.start_chat(
            history=conversation_history[message.guild.id]
        )

        content = message.content
        for user in message.mentions:
            mention_str = f'<@{user.id}>'
            username_str = f'@{user.name}'
            content = content.replace(mention_str, username_str)

        response = chat_session.send_message(f"{message.author}: {content}")
        conversation_history[message.guild.id] = chat_session.history

        print(f"Message:\n{message.author}: {content}")
        print(f"Response:\n{response.text}")

        if len(response.text) > message_size:
            for chunk in split_string_into_chunks(response.text, message_size):
                await message.channel.send(chunk)
        else:
            await message.channel.send(response.text)

handler = logging.handlers.RotatingFileHandler(
    filename='discord.log',
    encoding='utf-8',
    maxBytes=32 * 1024 * 1024,  # 32 MiB
    backupCount=3,
)

client.run(discord_token, log_handler=handler, log_level=logging.DEBUG)