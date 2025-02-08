import asyncio
import tempfile
import aiohttp
import requests
import os
import functools
import threading
import re
import time
from flask import Flask, request, jsonify
from pyrogram import Client, filters
from pyrogram.types import Message
from pytgcalls import PyTgCalls, idle
from pytgcalls.types import MediaStream

# Global variables to track client state and event loop
running_loop = None
client_started = False
client_ready_event = threading.Event()  # signals when clients have started

# Initialize Flask app
app = Flask(__name__)

# Initialize Pyrogram client with your session string
ASSISTANT_SESSION = "BQHAYsoAjIfG9yz9qTvjd2Vr73WlBAYW_-NgrwQPRsb_3A3aG9QotWET_ORDF4vppFUW9lIOoaMENTZrjcrYMTUBvBr0eHWUS6zogw95HuaiYExVP21VIUbJjO8Joq79YArSw0HR9gfa6keOkBSUkKO8ThQRDmm5I7QAYYev1b4SJR-h3JbyK1YmjcDY_zAeUKCU2Y30tj7fnLrmD5W7c77g66anI-LeUyNTeAl-bO-MYcGcSs3VhT9FrWaWEYMTnjmbRPGAXhUKlcW8JkfD0BTYoITBiFrnLESwFtJdcEvXSwa23ZPRONLZAp49JoOV3W2Uiuo6-8LP9s2TEL7LSBr_NBhaRwAAAAE6CvCVAA"
assistant = Client("assistant_account", session_string=ASSISTANT_SESSION)

# Initialize PyTgCalls with the Pyrogram client
py_tgcalls = PyTgCalls(assistant)

# Download API URL for audio downloads
DOWNLOAD_API_URL = "https://frozen-youtube-api-search-link-ksog.onrender.com/download?url="

# Caching dictionaries
search_cache = {}
download_cache = {}

async def start_clients():
    """Start Pyrogram and PyTgCalls and store the running event loop."""
    global client_started, running_loop
    if not client_started:
        await assistant.start()
        await py_tgcalls.start()
        client_started = True
        running_loop = asyncio.get_running_loop()
        client_ready_event.set()  # signal that the loop is ready

async def download_audio(url):
    """Download audio from the given URL and return the file path."""
    if url in download_cache:
        return download_cache[url]
    try:
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.mp3')
        file_name = temp_file.name
        download_url = f"{DOWNLOAD_API_URL}{url}"
        async with aiohttp.ClientSession() as session:
            async with session.get(download_url) as response:
                if response.status == 200:
                    with open(file_name, 'wb') as f:
                        f.write(await response.read())
                    download_cache[url] = file_name
                    return file_name
                else:
                    raise Exception(f"Failed to download audio. HTTP status: {response.status}")
    except Exception as e:
        raise Exception(f"Error downloading audio: {e}")

@functools.lru_cache(maxsize=100)
def search_video(title):
    """Search for a video using an external API and cache the result."""
    search_response = requests.get(f"https://odd-block-a945.tenopno.workers.dev/search?title={title}")
    if search_response.status_code != 200:
        return None
    return search_response.json()

async def play_media(chat_id, video_url, title):
    """Download audio for the video and play it in the specified chat."""
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

    # Process input if it's a Telegram link or starts with '@'
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
            await processing_msg.edit("❌ ERROR: Invalid username or link. Please check and try again.")
        elif "INVITE_HASH_INVALID" in error_message:
            await processing_msg.edit("❌ ERROR: Invalid invite link. Please verify and try again.")
        elif "USER_ALREADY_PARTICIPANT" in error_message:
            await processing_msg.edit(f"✅ You are already a member of {input_text}.")
        else:
            await processing_msg.edit(f"**ERROR:** \n\n{error_message}")

@app.route('/play', methods=['GET'])
def play():
    chatid = request.args.get('chatid')
    title = request.args.get('title')
    if not chatid or not title:
        return jsonify({'error': 'Missing chatid or title parameter'}), 400
    try:
        chat_id = int(chatid)
    except ValueError:
        return jsonify({'error': 'Invalid chatid parameter'}), 400

    search_result = search_video(title)
    if not search_result:
        return jsonify({'error': 'Failed to search video'}), 500

    video_url = search_result.get("link")
    video_title = search_result.get("title")
    if not video_url:
        return jsonify({'error': 'No video found'}), 404

    # Ensure the clients have started
    if not client_ready_event.is_set():
        if not client_ready_event.wait(timeout=10):
            return jsonify({'error': 'Clients did not start in time'}), 500

    # Schedule play_media on the existing event loop
    future = asyncio.run_coroutine_threadsafe(
        play_media(chat_id, video_url, video_title),
        running_loop
    )
    try:
        future.result()  # wait for completion if desired
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    return jsonify({'message': 'Playing media', 'chatid': chatid, 'title': video_title})

@app.route('/stop', methods=['GET'])
def stop():
    chatid = request.args.get('chatid')
    if not chatid:
        return jsonify({'error': 'Missing chatid parameter'}), 400
    try:
        chat_id = int(chatid)
    except ValueError:
        return jsonify({'error': 'Invalid chatid parameter'}), 400
    try:
        py_tgcalls.leave_call(chat_id)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({'message': 'Stopped media', 'chatid': chatid})

def run_flask():
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)

def start_async_clients():
    asyncio.run(start_clients())

if __name__ == '__main__':
    # Start the async clients in a daemon thread.
    threading.Thread(target=start_async_clients, daemon=True).start()
    
    # Wait until clients have started.
    if not client_ready_event.wait(timeout=10):
        print("Clients did not start in time.")
        exit(1)
    
    # Start Flask in a separate thread.
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()
    
    # Keep the PyTgCalls client running.
    asyncio.run(idle())


