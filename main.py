import asyncio
import tempfile
import aiohttp
import requests
import os
import functools
import re
from flask import Flask, request, jsonify
from pyrogram import Client, filters
from pyrogram.types import Message
from pytgcalls import PyTgCalls, idle
from pytgcalls.types import MediaStream

# Initialize Flask app
app = Flask(__name__)

# Get session string from environment variable
ASSISTANT_SESSION = os.environ.get("ASSISTANT_SESSION")

if not ASSISTANT_SESSION:
    raise ValueError("ASSISTANT_SESSION environment variable not set")

# Initialize Pyrogram client
assistant = Client("assistant_account", session_string=ASSISTANT_SESSION)

# Initialize PyTgCalls
py_tgcalls = PyTgCalls(assistant)

# Download API URL
DOWNLOAD_API_URL = "https://frozen-youtube-api-search-link-ksog.onrender.com/download?url="

# Caching setup
search_cache = {}
download_cache = {}

async def download_audio(url):
    """Downloads the audio from a given URL and returns the file path."""
    if url in download_cache:
        return download_cache[url]
    
    try:
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.mp3')
        file_name = temp_file.name
        download_url = f"{DOWNLOAD_API_URL}{url}"
        async with aiohttp.ClientSession() as session:
            async with session.get(download_url) as response:
                if response.status == 200:
                    content = await response.read()
                    with open(file_name, 'wb') as f:
                        f.write(content)
                    download_cache[url] = file_name
                    return file_name
                else:
                    raise Exception(f"Failed to download audio. HTTP status: {response.status}")
    except Exception as e:
        raise Exception(f"Error downloading audio: {e}")

@functools.lru_cache(maxsize=100)
def search_video(title):
    """Searches for a video using the external API and caches the result."""
    search_response = requests.get(f"https://odd-block-a945.tenopno.workers.dev/search?title={title}")
    if search_response.status_code != 200:
        return None
    return search_response.json()

async def play_media(chat_id, video_url, title):
    """Downloads and plays the media in the specified chat."""
    media_path = await download_audio(video_url)
    await py_tgcalls.play(
        chat_id,
        MediaStream(
            media_path,
            video_flags=MediaStream.Flags.IGNORE,
        ),
    )

@assistant.on_message(filters.command(["join"], "/"))
async def join(client: Client, message: Message):
    input_text = message.text.split(" ", 1)[1] if len(message.text.split()) > 1 else None
    processing_msg = await message.reply_text("Processing...")

    if not input_text:
        await processing_msg.edit("❌ Please provide a valid group/channel link or username.")
        return

    if re.match(r"https://t\.me/[\w_]+/?", input_text):
        input_text = input_text.split("https://t.me/")[1].strip("/")
    elif input_text.startswith("@"):
        input_text = input_text[1:]

    try:
        await client.join_chat(input_text)
        await processing_msg.edit(f"**Successfully Joined Group/Channel:** {input_text}")
    except Exception as error:
        error_message = str(error)
        if "USERNAME_INVALID" in error_message:
            await processing_msg.edit("❌ ERROR: Invalid username or link.")
        elif "INVITE_HASH_INVALID" in error_message:
            await processing_msg.edit("❌ ERROR: Invalid invite link.")
        elif "USER_ALREADY_PARTICIPANT" in error_message:
            await processing_msg.edit(f"✅ Already a member of {input_text}.")
        else:
            await processing_msg.edit(f"**ERROR:** \n\n{error_message}")

@app.route('/play', methods=['GET'])
async def play_route():
    chatid = request.args.get('chatid')
    title = request.args.get('title')

    if not chatid or not title:
        return jsonify({'error': 'Missing chatid or title'}), 400

    try:
        chat_id = int(chatid)
    except ValueError:
        return jsonify({'error': 'Invalid chatid'}), 400

    search_result = search_video(title)
    if not search_result:
        return jsonify({'error': 'Search failed'}), 500

    video_url = search_result.get("link")
    video_title = search_result.get("title")

    if not video_url:
        return jsonify({'error': 'No video found'}), 404

    await play_media(chat_id, video_url, video_title)
    return jsonify({'message': 'Playing', 'chatid': chatid, 'title': video_title})

@app.route('/stop', methods=['GET'])
async def stop_route():
    chatid = request.args.get('chatid')

    if not chatid:
        return jsonify({'error': 'Missing chatid'}), 400

    try:
        chat_id = int(chatid)
    except ValueError:
        return jsonify({'error': 'Invalid chatid'}), 400

    try:
        await py_tgcalls.leave_call(chat_id)
        return jsonify({'message': 'Stopped', 'chatid': chatid})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

async def run_bot():
    await assistant.start()
    await py_tgcalls.start()
    await idle()

def start_bot():
    asyncio.run(run_bot())

if __name__ == '__main__':
    import threading
    from hypercorn.asyncio import serve
    from hypercorn.config import Config

    # Start the Pyrogram/PyTgCalls client in a separate thread
    threading.Thread(target=start_bot, daemon=True).start()

    # Configure and run Hypercorn
    config = Config()
    config.bind = ["0.0.0.0:8000"]
    asyncio.run(serve(app, config))


