import os
import asyncio
import tempfile
import aiohttp
import requests
from flask import Flask, request, jsonify
from pyrogram import Client
from pytgcalls import PyTgCalls, idle
from pytgcalls.types import MediaStream
from threading import Thread

# Initialize Flask app
app = Flask(__name__)

# Initialize Pyrogram client with the provided session string
ASSISTANT_SESSION = "BQHAYsoAjIfG9yz9qTvjd2Vr73WlBAYW_-NgrwQPRsb_3A3aG9QotWET_ORDF4vppFUW9lIOoaMENTZrjcrYMTUBvBr0eHWUS6zogw95HuaiYExVP21VIUbJjO8Joq79YArSw0HR9gfa6keOkBSUkKO8ThQRDmm5I7QAYYev1b4SJR-h3JbyK1YmjcDY_zAeUKCU2Y30tj7fnLrmD5W7c77g66anI-LeUyNTeAl-bO-MYcGcSs3VhT9FrWaWEYMTnjmbRPGAXhUKlcW8JkfD0BTYoITBiFrnLESwFtJdcEvXSwa23ZPRONLZAp49JoOV3W2Uiuo6-8LP9s2TEL7LSBr_NBhaRwAAAAE6CvCVAA"
assistant = Client("assistant_account", session_string=ASSISTANT_SESSION)

# Initialize PyTgCalls
py_tgcalls = PyTgCalls(assistant)

# Download API URL
DOWNLOAD_API_URL = "https://frozen-youtube-api-search-link-ksog.onrender.com/download?url="

# Start Pyrogram client and PyTgCalls
client_started = False

async def start_clients():
    global client_started
    if not client_started:
        await assistant.start()
        await py_tgcalls.start()
        client_started = True

async def download_audio(url):
    """Downloads the audio from a given URL and returns the file path."""
    try:
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.mp3')
        file_name = temp_file.name
        download_url = f"{DOWNLOAD_API_URL}{url}"
        async with aiohttp.ClientSession() as session:
            async with session.get(download_url) as response:
                if response.status == 200:
                    with open(file_name, 'wb') as f:
                        f.write(await response.read())
                    return file_name
                else:
                    raise Exception(f"Failed to download audio. HTTP status: {response.status}")
    except Exception as e:
        raise Exception(f"Error downloading audio: {e}")

async def play_media(chat_id, video_url):
    media_path = await download_audio(video_url)
    await py_tgcalls.play(
        chat_id,
        MediaStream(
            media_path,
            video_flags=MediaStream.Flags.IGNORE,
        ),
    )

@app.route('/')
def home():
    return "Frozen is Up & Running!"

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
    
    search_response = requests.get(f"https://odd-block-a945.tenopno.workers.dev/search?title={title}")
    if search_response.status_code != 200:
        return jsonify({'error': 'Failed to search video'}), 500
    
    search_result = search_response.json()
    video_url = search_result.get("link")
    
    if not video_url:
        return jsonify({'error': 'No video found'}), 404
    
    asyncio.create_task(start_clients())
    asyncio.create_task(play_media(chat_id, video_url))
    
    return jsonify({'message': 'Playing media', 'chatid': chatid, 'title': title})

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))

async def main():
    await start_clients()
    await idle()

if __name__ == '__main__':
    flask_thread = Thread(target=run_flask)
    flask_thread.start()
    asyncio.run(main())
