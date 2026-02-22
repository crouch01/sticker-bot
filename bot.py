import os
import uuid
import logging
import subprocess
from threading import Thread
from flask import Flask
from telegram import Update, InputSticker
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
from telegram.error import TelegramError

# --- CONFIGURATION ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
TEMP_FOLDER = "temp_files"
os.makedirs(TEMP_FOLDER, exist_ok=True)

# --- WEB SERVER (KEEPS BOT ALIVE) ---
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Bot is running!"

def run_http_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- SMART VIDEO PROCESSING ---
async def convert_to_webm(input_path, output_path):
    try:
        # 1. Get dimensions
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", 
             "-show_entries", "stream=width,height,duration", "-of", "csv=s=x:p=0", input_path],
            capture_output=True, text=True
        )
        parts = probe.stdout.strip().split('x')
        w, h = int(parts[0]), int(parts[1])
        try:
            duration = float(parts[2])
        except (IndexError, ValueError):
            duration = 3.0

        # Scale logic
        scale = "scale=512:-1" if w >= h else "scale=-1:512"

        # 2. First Pass: Try High Quality
        temp_output = output_path + ".temp.webm"
        cmd_hq = [
            "ffmpeg", "-y", "-i", input_path,
            "-c:v", "libvpx-vp9", "-an",
            "-vf", f"{scale},fps=30",
            "-t", "00:00:02.900",
            "-b:v", "0", "-crf", "30",
            temp_output
        ]
        subprocess.run(cmd_hq, check=True)

        # 3. Check Size
        file_size_kb = os.path.getsize(temp_output) / 1024

        if file_size_kb <= 256:
            if os.path.exists(output_path): os.remove(output_path)
            os.rename(temp_output, output_path)
            return True
        else:
            # 4. Resize if too big
            target_bitrate = int((250 * 8192) / duration)
            cmd_compress = [
                "ffmpeg", "-y", "-i", input_path,
                "-c:v", "libvpx-vp9", "-an",
                "-vf", f"{scale},fps=30",
                "-t", "00:00:02.900",
                "-b:v", str(target_bitrate),
                "-minrate", str(int(target_bitrate * 0.7)),
                "-maxrate", str(int(target_bitrate * 1.3)),
                output_path
            ]
            subprocess.run(cmd_compress, check=True)
            if os.path.exists(temp_output): os.remove(temp_output)
            return True
    except Exception as e:
        print(f"FFmpeg Error: {e}")
        return False

# --- AUTOMATIC PACK MANAGEMENT (FIXED) ---
async def add_to_pack(user_id, sticker_path, emoji, context):
    bot = context.bot
    bot_name = context.bot.username
    pack_name = f"videopack_{user_id}_by_{bot_name}"
    pack_title = f"Video Stickers {user_id}"

    with open(sticker_path, 'rb') as f:
        sticker_data = f.read()

    try:
        # Try adding to existing pack
        await bot.add_sticker_to_set(
            user_id=user_id,
            name=pack_name,
            sticker=InputSticker(sticker=sticker_data, emoji_list=[emoji])
        )
        return f"✅ Added to pack! (Emoji: {emoji})\n🔗 t.me/addstickers/{pack_name}"

    except TelegramError as e:
        if "Stickerset_invalid" in str(e):
            # Create new pack if it doesn't exist
            try:
                await bot.create_new_sticker_set(
                    user_id=user_id,
                    name=pack_name,
                    title=pack_title,
                    stickers=[InputSticker(sticker=sticker_data, emoji_list=[emoji])],
                    sticker_format="video"  # <--- This is the correct place for it in v21
                )
                return f"🎉 New pack created!\n🔗 t.me/addstickers/{pack_name}"
            except Exception as x:
                return f"❌ Creation failed: {x}"
        elif "Stickers_too_much" in str(e):
            return "❌ Pack is full (120 stickers)."
        else:
            return f"❌ Error: {e}"

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.message
    
    # Get Emoji
    user_emoji = msg.caption if msg.caption else "🎬"

    file_id = None
    if msg.animation: file_id = msg.animation.file_id
    elif msg.video: file_id = msg.video.file_id
    elif msg.document and "video" in msg.document.mime_type: file_id = msg.document.file_id
    
    if not file_id:
        await msg.reply_text("Please send a GIF or Video.")
        return

    status = await msg.reply_text("⬇️ Processing...")

    try:
        new_file = await context.bot.get_file(file_id)
        
        # Unique Filename
        unique_id = str(uuid.uuid4())
        input_f = f"{TEMP_FOLDER}/{unique_id}_in"
        output_f = f"{TEMP_FOLDER}/{unique_id}_sticker.webm"
        
        await new_file.download_to_drive(input_f)

        if await convert_to_webm(input_f, output_f):
            await status.edit_text(f"✨ Adding to pack...")
            result = await add_to_pack(user.id, output_f, user_emoji, context)
            await status.edit_text(result)
        else:
            await status.edit_text("❌ Processing failed.")
            
    except Exception as e:
        await status.edit_text(f"❌ Error: {e}")
    finally:
        if os.path.exists(input_f): os.remove(input_f)
        if os.path.exists(output_f): os.remove(output_f)

if __name__ == '__main__':
    Thread(target=run_http_server).start()
    if not BOT_TOKEN:
        print("Error: BOT_TOKEN missing")
    else:
        app_bot = ApplicationBuilder().token(BOT_TOKEN).build()
        app_bot.add_handler(MessageHandler(filters.ANIMATION | filters.VIDEO | filters.Document.VIDEO, handle_document))
        app_bot.run_polling()