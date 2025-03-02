import asyncio
import tempfile
import aiohttp
import requests
import os
import functools
import threading
import re
from flask import Flask, request, jsonify
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.handlers import MessageHandler  # For adding message handlers
from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream
from pytgcalls import filters as pt_filters
from pytgcalls.types import Update

# Initialize Flask app
app = Flask(__name__)

# Download API URL
DOWNLOAD_API_URL = "https://frozen-youtube-api-search-link-ksog.onrender.com/download?url="

# Caching setup
search_cache = {}
download_cache = {}

# Global variables for the async clients (to be created in the dedicated loop)
assistant = None
py_tgcalls = None
clients_initialized = False

# Global dict to store status message IDs keyed by chat id.
stream_status_messages = {}

# Global dict to record the currently playing songs keyed by chat id.
# When a stream is forwarded to the secondary API, its chat id will not be added here.
current_playing = {}

# Create a dedicated asyncio event loop for all async operations
tgcalls_loop = asyncio.new_event_loop()

def start_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()

# Start the dedicated loop in its own daemon thread
tgcalls_thread = threading.Thread(target=start_loop, args=(tgcalls_loop,), daemon=True)
tgcalls_thread.start()

# Global list to hold pending update handlers.
pending_update_handlers = []

def delayed_on_update(filter_):
    """
    A decorator that defers registration of update handlers until py_tgcalls is ready.
    """
    def decorator(func):
        pending_update_handlers.append((filter_, func))
        return func
    return decorator

@delayed_on_update(pt_filters.stream_end)
async def stream_end_handler(_: PyTgCalls, update: Update):
    chat_id = update.chat_id
    try:
        # Leave the call first.
        await py_tgcalls.leave_call(chat_id)
        # Send a message indicating that the stream ended.
        await assistant.send_message(
            "@vcmusiclubot",
            f"Stream ended in chat id {chat_id}"
        )
        # Update the playback record by removing the entry for this chat id.
        current_playing.pop(chat_id, None)
    except Exception as e:
        print(f"Error leaving voice chat: {e}")

# Global event to signal frozen check confirmation.
frozen_check_event = asyncio.Event()
# Flag to ensure the frozen check loop is only started once.
frozen_check_loop_started = False

async def restart_bot():
    """
    Triggers a bot restart by calling the RENDER_DEPLOY_URL.
    """
    RENDER_DEPLOY_URL = os.getenv("RENDER_DEPLOY_URL", "https://api.render.com/deploy/srv-cuqb40bv2p9s739h68i0?key=qBdP4Go4h9c")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(RENDER_DEPLOY_URL) as response:
                if response.status == 200:
                    print("Bot restart triggered successfully.")
                else:
                    print(f"Failed to trigger bot restart. Status code: {response.status}")
    except Exception as e:
        print(f"Error triggering bot restart: {e}")

async def frozen_check_loop():
    """
    Periodically sends a /frozen_check command to @vcmusiclubot.
    If the expected response is not received within 30 seconds,
    it triggers a restart.
    """
    while True:
        try:
            # Clear the event before sending a new check.
            frozen_check_event.clear()
            await assistant.send_message("@vcmusiclubot", "/frozen_check")
            print("Sent /frozen_check command to @vcmusiclubot")
            try:
                await asyncio.wait_for(frozen_check_event.wait(), timeout=30)
                print("Received frozen check confirmation.")
            except asyncio.TimeoutError:
                print("Frozen check response not received. Restarting bot.")
                await restart_bot()
        except Exception as e:
            print(f"Error in frozen_check_loop: {e}")
        await asyncio.sleep(60)  # Wait 60 seconds before the next check.

# Handler to process incoming frozen check responses.
async def frozen_check_response_handler(client: Client, message: Message):
    # Wait for 2 seconds before checking the message content.
    await asyncio.sleep(2)
    if "frozen check successful ✨" in message.text:
        frozen_check_event.set()

async def init_clients():
    """
    Lazily creates and starts the Pyrogram client and PyTgCalls instance
    on the dedicated event loop, registers pending update handlers and
    the frozen check response handler, and starts the frozen check loop.
    """
    global assistant, py_tgcalls, clients_initialized, frozen_check_loop_started
    if not clients_initialized:
        assistant = Client(
            "assistant_account",
            session_string=os.environ.get("ASSISTANT_SESSION", "")
        )
        await assistant.start()
        # Add a message handler to catch frozen check responses from @vcmusiclubot.
        assistant.add_handler(
            MessageHandler(frozen_check_response_handler, filters=filters.chat("@vcmusiclubot") & filters.text)
        )
        py_tgcalls = PyTgCalls(assistant)
        await py_tgcalls.start()
        clients_initialized = True
        # Register all pending update handlers now that py_tgcalls is initialized.
        for filter_, handler in pending_update_handlers:
            py_tgcalls.on_update(filter_)(handler)
    if not frozen_check_loop_started:
        # Start the frozen check loop in the dedicated event loop.
        tgcalls_loop.create_task(frozen_check_loop())
        frozen_check_loop_started = True

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
        )
    )
    # Record the currently playing song.
    current_playing[chat_id] = {"title": title, "video_url": video_url}

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

    # Check the active voice chat limit. If the chat is new and we already have 4 active chats,
    # forward the play request to a secondary API.
    if chat_id not in current_playing and len(current_playing) >= 4:
        secondary_api_url = os.environ.get("SECONDARY_API_URL", "http://secondary_api_url")
        params = {"chatid": chatid, "title": title}
        try:
            response = requests.get(f"{secondary_api_url}/play", params=params)
            return jsonify(response.json())
        except Exception as e:
            return jsonify({'error': f"Secondary API error: {str(e)}"}), 500

    try:
        # Initialize the clients on the dedicated loop if needed.
        asyncio.run_coroutine_threadsafe(init_clients(), tgcalls_loop).result()
        # Schedule play_media on the dedicated loop.
        asyncio.run_coroutine_threadsafe(play_media(chat_id, video_url, video_title), tgcalls_loop).result()
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

    # If the stream for this chat id is handled by the secondary API, forward the request.
    if chat_id not in current_playing:
        secondary_api_url = os.environ.get("SECONDARY_API_URL", "http://secondary_api_url")
        try:
            response = requests.get(f"{secondary_api_url}/stop", params={"chatid": chatid})
            return jsonify(response.json())
        except Exception as e:
            return jsonify({'error': f"Secondary API error: {str(e)}"}), 500

    try:
        if not clients_initialized:
            asyncio.run_coroutine_threadsafe(init_clients(), tgcalls_loop).result()

        async def leave_call_wrapper(cid):
            await asyncio.sleep(0)
            result = await py_tgcalls.leave_call(cid)
            return result

        asyncio.run_coroutine_threadsafe(leave_call_wrapper(chat_id), tgcalls_loop).result()
        # Remove the chat from the current playing record.
        current_playing.pop(chat_id, None)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    return jsonify({'message': 'Stopped media', 'chatid': chatid})

@app.route('/pause', methods=['GET'])
def pause():
    chatid = request.args.get('chatid')
    if not chatid:
        return jsonify({'error': 'Missing chatid parameter'}), 400
    try:
        chat_id = int(chatid)
    except ValueError:
        return jsonify({'error': 'Invalid chatid parameter'}), 400

    # If the stream for this chat id is handled by the secondary API, forward the request.
    if chat_id not in current_playing:
        secondary_api_url = os.environ.get("SECONDARY_API_URL", "http://secondary_api_url")
        try:
            response = requests.get(f"{secondary_api_url}/pause", params={"chatid": chatid})
            return jsonify(response.json())
        except Exception as e:
            return jsonify({'error': f"Secondary API error: {str(e)}"}), 500

    try:
        if not clients_initialized:
            asyncio.run_coroutine_threadsafe(init_clients(), tgcalls_loop).result()

        async def pause_stream_wrapper(cid):
            await asyncio.sleep(0)
            return await py_tgcalls.pause_stream(cid)
        asyncio.run_coroutine_threadsafe(pause_stream_wrapper(chat_id), tgcalls_loop).result()
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    return jsonify({'message': 'Paused media', 'chatid': chatid})

@app.route('/resume', methods=['GET'])
def resume():
    chatid = request.args.get('chatid')
    if not chatid:
        return jsonify({'error': 'Missing chatid parameter'}), 400
    try:
        chat_id = int(chatid)
    except ValueError:
        return jsonify({'error': 'Invalid chatid parameter'}), 400

    # If the stream for this chat id is handled by the secondary API, forward the request.
    if chat_id not in current_playing:
        secondary_api_url = os.environ.get("SECONDARY_API_URL", "http://secondary_api_url")
        try:
            response = requests.get(f"{secondary_api_url}/resume", params={"chatid": chatid})
            return jsonify(response.json())
        except Exception as e:
            return jsonify({'error': f"Secondary API error: {str(e)}"}), 500

    try:
        if not clients_initialized:
            asyncio.run_coroutine_threadsafe(init_clients(), tgcalls_loop).result()

        async def resume_stream_wrapper(cid):
            await asyncio.sleep(0)
            return await py_tgcalls.resume_stream(cid)
        asyncio.run_coroutine_threadsafe(resume_stream_wrapper(chat_id), tgcalls_loop).result()
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    return jsonify({'message': 'Resumed media', 'chatid': chatid})

if __name__ == '__main__':
    # Optionally initialize the clients and frozen check loop at startup.
    asyncio.run_coroutine_threadsafe(init_clients(), tgcalls_loop).result()
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)




