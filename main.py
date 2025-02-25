from pyrogram import Client, filters
from pytgcalls import idle, PyTgCalls
from pytgcalls.types import MediaStream
import aiohttp
import asyncio
from pyrogram.types import Message
import isodate
import os
import re
import time
import psutil
from datetime import datetime, timedelta
from isodate import parse_duration
import uuid
from dotenv import load_dotenv

load_dotenv()

# Your session string
STRING_SESSION = os.getenv("STRING_SESSION")
app = Client("mus1223331ic_bot", session_string=STRING_SESSION)

# Initialize PyTgCalls
call_py = PyTgCalls(app)

# Containers for song queues per chat/group
chat_containers = {}

bot_start_time = time.time()

# API endpoint for searching YouTube links
API_URL = "https://odd-block-a945.tenopno.workers.dev/search?title="
DOWNLOAD_API_URL = "https://frozen-youtube-api-search-link-ksog.onrender.com/download?url="

# Utility function to convert ISO 8601 duration to HH:MM:SS
def iso8601_to_human_readable(iso_duration):
    try:
        duration = isodate.parse_duration(iso_duration)
        total_seconds = int(duration.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours > 0:
            return f"{hours}:{minutes:02}:{seconds:02}"
        return f"{minutes}:{seconds:02}"
    except Exception as e:
        return "Unknown duration"

# Function to fetch YouTube link using the API
async def fetch_youtube_link(query):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{API_URL}{query}") as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("link"), data.get("title"), data.get("duration")
                else:
                    raise Exception(f"API returned status code {response.status}")
    except Exception as e:
        raise Exception(f"Failed to fetch YouTube link: {str(e)}")

# Command to handle /start
@app.on_message(filters.command("start"))
async def start_handler(client, message):
    try:
        await message.reply(
            "\U0001F44B **Welcome to the Music Bot!**\n\n"
            "\U0001F3B5 Use `/play <song name>` to search and play music in your voice chat.\n"
            "\u23F9 Use `/stop` to stop the music.\n"
            "\u23F8 Use `/pause` to pause the music.\n"
            "\u25B6\uFE0F Use `/resume` to resume the music.\n\n"
            "Happy listening! \U0001F3A7"
        )
    except Exception as e:
        await message.reply(f"\u274C Failed to process the command. Error: {str(e)}")

@app.on_message(filters.regex(r'^/play(?: (?P<query>.+))?$'))
async def play_handler(client, message):
    chat_id = message.chat.id
    try:
        query = message.matches[0]['query']  # Extract query from the command

        if not query:
            await message.reply("\u2753 Please provide a song name to play.\nExample: /play Shape of You")
            return

        await_message = await message.reply("\U0001F50D Searching for the song...")

        # Fetch YouTube link from the API
        video_url, video_title, video_duration = await fetch_youtube_link(query)

        if not video_url:
            await await_message.edit("\u274C Could not find the song. Please try another query.")
            return

        # Convert ISO 8601 duration to human-readable format
        readable_duration = iso8601_to_human_readable(video_duration)

        # Add the song to the chat-specific queue
        if chat_id not in chat_containers:
            chat_containers[chat_id] = []

        chat_containers[chat_id].append({
            "url": video_url,
            "title": video_title,
            "duration": readable_duration,
            "duration_seconds": isodate.parse_duration(video_duration).total_seconds(),
            "requester": message.from_user.mention if message.from_user else "Unknown",
        })

        # If the queue has only one song, start playing immediately
        if len(chat_containers[chat_id]) == 1:
            await skip_to_next_song(chat_id, await_message)
        else:
            await await_message.edit(
                f"\u2705 Added to queue:\n\n"
                f"**Title:** {video_title}\n\n"
                f"**Duration:** {readable_duration}\n"
                f"**Requested by:** {message.from_user.mention if message.from_user else 'Unknown'}",
                disable_web_page_preview=True
            )

    except Exception as e:
        await message.reply(f"\u274C Failed to play the song. Error: {str(e)}")

async def skip_to_next_song(chat_id, await_message):
    try:
        while chat_id in chat_containers and chat_containers[chat_id]:
            song_info = chat_containers[chat_id][0]  # Get the first song in the queue

            video_url = song_info.get('url')
            if not video_url:
                print(f"Invalid video URL for song: {song_info}")
                chat_containers[chat_id].pop(0)
                continue

            try:
                # Send the video URL to the new API for download
                await await_message.edit(f"\U0001F916 Sending URL to download bot for\n\n **{song_info['title']}**...")
                media_path = await download_audio(video_url)
                await await_message.edit(f"✅ **Download completed for**\n\n {song_info['title']}. \n\n **Playing now...**")

                # Play the media using pytgcalls
                try:
                    await call_py.play(
                        chat_id,
                        MediaStream(
                            media_path,
                            video_flags=MediaStream.Flags.IGNORE,
                        ),
                    )
                    # Notify the group about the currently playing song
                    await await_message.edit(
                        f"🎵 **Now Playing**\n"
                        f"**Title:** {song_info['title']}\n"
                        f"**Duration:** {song_info['duration']}\n"
                        f"**Requested by:** {song_info['requester']}",
                        disable_web_page_preview=True,
                    )

                    # Wait for the song to finish
                    await asyncio.sleep(song_info['duration_seconds'] + 20)  # Added extra 20 seconds
                except Exception as playback_error:
                    print(f"Error during playback: {playback_error}")
                    await await_message.edit(
                        f"❌ Playback error for **{song_info['title']}**. Skipping to the next song...",
                    )

            except Exception as download_error:
                print(f"Error during download or processing: {download_error}")
                await await_message.edit(
                    f"❌ Error retrieving or processing audio for **{song_info['title']}**. Skipping...",
                )

            finally:
                # Clean up: remove the song from the queue
                chat_containers[chat_id].pop(0)

        # Leave the voice chat if the queue is empty
        if chat_id in chat_containers and not chat_containers[chat_id]:
            try:
                await call_py.leave_call(chat_id)
                await await_message.edit("✅ Queue finished. Leaving the voice chat.")
            except Exception as leave_error:
                print(f"Error leaving call: {leave_error}")

    except Exception as e:
        print(f"Unexpected error in skip_to_next_song: {str(e)}")

async def download_audio(url):
    """Downloads the audio from a given URL and returns the file path."""
    try:
        file_name = f"downloads/{uuid.uuid4()}.mp3"
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

# Command to stop the bot from playing
@app.on_message(filters.command(["stop", "end"]))
async def stop_handler(client, message):
    chat_id = message.chat.id

    try:
        # Leave the voice chat (handles cases where the bot is not in VC)
        await call_py.leave_call(chat_id)
    except Exception as e:
        # Handle cases where the bot is not in the voice chat
        if "not in a call" in str(e).lower():
            await message.reply("❌ The bot is not currently in a voice chat.")
        else:
            await message.reply(f"❌ An error occurred while leaving the voice chat: {str(e)}")
        return

    # Clear the chat-specific queue
    if chat_id in chat_containers:
        for song in chat_containers[chat_id]:
            try:
                os.remove(song.get('file_path', ''))
            except Exception as e:
                print(f"Error deleting file: {e}")
        chat_containers.pop(chat_id)

    await message.reply("⏹ Stopped the music and cleared the queue.")

# Command to pause the stream
@app.on_message(filters.command("pause"))
async def pause_handler(client, message):
    try:
        await call_py.pause_stream(message.chat.id)
        await message.reply("⏸ Paused the stream.")
    except Exception as e:
        await message.reply(f"❌ Failed to pause the stream. Error: {str(e)}")

# Command to resume the stream
@app.on_message(filters.command("resume"))
async def resume_handler(client, message):
    try:
        await call_py.resume_stream(message.chat.id)
        await message.reply("▶️ Resumed the stream.")
    except Exception as e:
        await message.reply(f"❌ Failed to resume the stream. Error: {str(e)}")

# Command to skip the current song
@app.on_message(filters.command("skip"))
async def skip_handler(client, message):
    chat_id = message.chat.id

    try:
        if chat_id not in chat_containers or not chat_containers[chat_id]:
            await message.reply("❌ No songs in the queue to skip.")
            return

        # Remove the current song from the chat-specific queue
        skipped_song = chat_containers[chat_id].pop(0)

        # End playback and skip first, then delete the file
        await call_py.leave_call(chat_id)
        await asyncio.sleep(3)
        try:
            os.remove(skipped_song.get('file_path', ''))
        except Exception as e:
            print(f"Error deleting file: {e}")

        if not chat_containers[chat_id]:  # If no songs left in the queue
            await message.reply(f"⏩ Skipped **{skipped_song['title']}**.\n\n🎵 No more songs in the queue.")
        else:
            # Play the next song in the queue
            await message.reply(f"⏩ Skipped **{skipped_song['title']}**.\n\n🎵 Playing the next song...")
            await skip_to_next_song(client, chat_id)  # Adjusted call

    except Exception as e:
        await message.reply(f"❌ Failed to skip the song. Error: {str(e)}")

@app.on_message(filters.command(["join"], "/"))
async def join(client: Client, message: Message):
    input_text = message.text.split(" ", 1)[1] if len(message.text.split()) > 1 else None
    processing_msg = await message.reply_text("`Processing...`")

    if not input_text:
        await processing_msg.edit("❌ Please provide a valid group/channel link or username.")
        return

    # Validate and process the input
    if re.match(r"https://t\.me/[\w_]+/?", input_text):
        input_text = input_text.split("https://t.me/")[1].strip("/")
    elif input_text.startswith("@"):
        input_text = input_text[1:]

    try:
        # Attempt to join the group/channel
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

@app.on_message(filters.private)
async def dm_message_handler(client, message):
    try:
        # Ignore bots and the bot's own messages
        if message.from_user.is_bot or message.from_user.id == client.me.id:
            return

        await message.reply(
            "👋 **Welcome to the Music Bot!**\n\n"
            "🎵 Use `/play <song name>` to search and play music in your voice chat.\n"
            "⏹ Use `/stop` to stop the music.\n"
            "⏸ Use `/pause` to pause the music.\n"
            "▶️ Use `/resume` to resume the music.\n\n"
            "Happy listening! 🎧"
        )
    except Exception as e:
        await message.reply(f"❌ Failed to process the command. Error: {str(e)}")

# Command to clear the queue for the current chat and reboot (/reboot)
@app.on_message(filters.command("reboot"))
async def reboot_handler(client, message):
    chat_id = message.chat.id

    try:
        if chat_id in chat_containers:
            for song in chat_containers[chat_id]:
                try:
                    os.remove(song.get('file_path', ''))
                except Exception as e:
                    print(f"Error deleting file: {e}")
            await call_py.leave_call(chat_id)

            # Remove stored audio files for each song in the queue
            for song in chat_containers[chat_id]:
                try:
                    os.remove(song.get('file_path', ''))
                except Exception as e:
                    print(f"Error deleting file: {e}")

            # Clear the queue for this chat
            chat_containers.pop(chat_id, None)

            await message.reply("🔄 rebooted for this chat and queue is cleared.")
        else:
            await message.reply("❌ No active queue to clear in this chat.")
    except Exception as e:
        await message.reply(f"❌ Failed to reboot. Error: {str(e)}")

@app.on_message(filters.command("ping"))
async def ping_handler(client, message):
    try:
        # Calculate uptime
        current_time = time.time()
        uptime_seconds = int(current_time - bot_start_time)
        uptime_str = str(timedelta(seconds=uptime_seconds))

        # Get system stats
        cpu_usage = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        ram_usage = f"{memory.used // (1024 ** 2)}MB / {memory.total // (1024 ** 2)}MB ({memory.percent}%)"
        disk = psutil.disk_usage('/')
        disk_usage = f"{disk.used // (1024 ** 3)}GB / {disk.total // (1024 ** 3)}GB ({disk.percent}%)"

        # Create response message
        response = (
            f"🏓 **Pong!**\n\n"
            f"**Uptime:** `{uptime_str}`\n"
            f"**CPU Usage:** `{cpu_usage}%`\n"
            f"**RAM Usage:** `{ram_usage}`\n"
            f"**Disk Usage:** `{disk_usage}`\n"
        )

        await message.reply(response)
    except Exception as e:
        await message.reply(f"❌ Failed to execute the command. Error: {str(e)}")

# Start PyTgCalls and the Pyrogram Client
try:
    call_py.start()
    print("Bot is running. Use /play to search and stream music.")
    idle()
except Exception as e:
    print(f"Critical error: {str(e)}")

