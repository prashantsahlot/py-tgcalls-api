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
from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream
from pytgcalls import filters as fl
from pytgcalls.types import Update
from pyrogram.handlers import MessageHandler

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
    """A decorator that defers registration of update handlers until py_tgcalls is ready."""
    def decorator(func):
        pending_update_handlers.append((filter_, func))
        return func
    return decorator

@delayed_on_update(fl.stream_end)
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
    except Exception as e:
        print(f"Error leaving voice chat: {e}")

# Define the join handler function without using a decorator.
async def join(client: Client, message: Message):
    input_text = message.text.split(" ", 1)[1] if len(message.text.split()) > 1 else None
    processing_msg = await message.reply_text("`Processing...`")

    if not input_text:
        await processing_msg.edit("❌ Please provide a valid group/channel link or username.")
        return

    # Validate and process the input.
    if re.match(r"https://t\.me/[\w_]+/?", input_text):
        input_text = input_text.split("https://t.me/")[1].strip("/")
    elif input_text.startswith("@"):
        input_text = input_text[1:]

    try:
        # Attempt to join the group/channel.
        await client.join_chat(input_text)
        await processing_msg.edit(f"**Successfully Joined Group/Channel:** `{input_text}`")
    except Exception as error:
        error_message = str(error)
        if "USERNAME_INVALID" in error_message:
            await processing_msg.edit("❌ ERROR: Invalid username or link. Please check and try again.")
        elif "INVITE_HASH_INVALID" in error_message:
            await processing_msg.edit("❌ ERROR: Invalid invite link. Please verify and try again.")
        elif "USER_ALREADY_PARTICIPANT" in error_message:
            await processing_msg.edit(f"✅ You are already a member of `{input_text}`.")
        else:
            await processing_msg.edit(f"**ERROR:** \n\n{error_message}")

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

async def init_clients():
    """Lazily creates and starts the Pyrogram client and PyTgCalls instance on the dedicated event loop."""
    global assistant, py_tgcalls, clients_initialized
    if not clients_initialized:
        assistant = Client(
            "assistant_account",
            session_string="BQHAYsoAFoY2AkF2OPeroqp4UqWPqn_04KDTeB3V8x8pQCuWxnSomBB4jkNd50w6dAbNpzCCYhjhn_qfCekbhiuE4pazSQC1Ci0ThmzRJXaNChnU5TYbdGKHOtji4E3QRMqAbxq3vr83u6PSL2mtAPYYtqqixPpPUJ0J2-0KMgA4HBiNDGj7N-sTRlI3A9urJXXFOv1cEWgubYi_Hgaio_egajUhBPCvGXi9sRcmCmmkTKiYHlCJrey5-cZ4Z45x7IEzG0O1Cp1G910qjZO6GO1KTyevEuYy4p2VW-RSwoLr6ogngrFkHPcm7oLPQ2l3emn894zS1BCrkhqtbOojREDAKW6v5AAAAAE6CvCVAA"
        )
        await assistant.start()
        # Now that assistant is started, manually register the join handler:
        assistant.add_handler(MessageHandler(join, filters.command(["join"], "/")))
        py_tgcalls = PyTgCalls(assistant)
        await py_tgcalls.start()
        clients_initialized = True
        # Register all pending update handlers now that py_tgcalls is initialized.
        for filter_, handler in pending_update_handlers:
            py_tgcalls.on_update(filter_)(handler)

@app.route('/play', methods=['GET'])
def play():
    chatid = request.args.get('chatid')
    title = request.args.get('title')
    if not chatid or not title:
        return jsonify({'error': 'Missing chatid or title parameter'}), 400
    try:
        int(chatid)
    except ValueError:
        return jsonify({'error': 'Invalid chatid parameter'}), 400

    try:
        asyncio.run_coroutine_threadsafe(init_clients(), tgcalls_loop).result()
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    return jsonify({'message': 'Playing media', 'chatid': chatid, 'title': title})

@app.route('/stop', methods=['GET'])
def stop():
    chatid = request.args.get('chatid')
    if not chatid:
        return jsonify({'error': 'Missing chatid parameter'}), 400
    try:
        int(chatid)
    except ValueError:
        return jsonify({'error': 'Invalid chatid parameter'}), 400

    try:
        if not clients_initialized:
            asyncio.run_coroutine_threadsafe(init_clients(), tgcalls_loop).result()

        async def leave_call_wrapper(cid):
            await asyncio.sleep(0)
            return await py_tgcalls.leave_call(cid)

        asyncio.run_coroutine_threadsafe(leave_call_wrapper(int(chatid)), tgcalls_loop).result()
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    return jsonify({'message': 'Stopped media', 'chatid': chatid})

# New /join API endpoint
@app.route('/join', methods=['GET'])
def join_api():
    # Get the group/channel link or username from the request query parameters.
    input_text = request.args.get('input')
    if not input_text:
        return jsonify({'error': 'Missing input parameter (provide group/channel link or username)'}), 400

    # Ensure clients are initialized
    try:
        asyncio.run_coroutine_threadsafe(init_clients(), tgcalls_loop).result()
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    async def join_chat_api(input_text: str):
        # Process the input text in the same way as your existing join handler.
        if re.match(r"https://t\.me/[\w_]+/?", input_text):
            input_text = input_text.split("https://t.me/")[1].strip("/")
        elif input_text.startswith("@"):
            input_text = input_text[1:]
        try:
            # Attempt to join the group/channel.
            await assistant.join_chat(input_text)
            return {'message': f"Successfully joined group/channel: {input_text}"}
        except Exception as error:
            error_message = str(error)
            if "USERNAME_INVALID" in error_message:
                return {'error': 'Invalid username or link. Please check and try again.'}
            elif "INVITE_HASH_INVALID" in error_message:
                return {'error': 'Invalid invite link. Please verify and try again.'}
            elif "USER_ALREADY_PARTICIPANT" in error_message:
                return {'message': f"Already a member of {input_text}."}
            else:
                return {'error': error_message}

    try:
        result = asyncio.run_coroutine_threadsafe(join_chat_api(input_text), tgcalls_loop).result()
        if "error" in result:
            return jsonify(result), 400
        else:
            return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8000))
    # Note: Running Flask in debug mode or with auto-reload sometimes conflicts with threaded event loops.
    app.run(host="0.0.0.0", port=port)
