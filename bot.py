import os
import logging
import subprocess
from threading import Thread
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

# --- CONFIGURATION ---
# We get the token from the server settings later
BOT_TOKEN = os.environ.get("BOT_TOKEN") 
TEMP_FOLDER = "temp_files"
os.makedirs(TEMP_FOLDER, exist_ok=True)

# --- WEB SERVER TO KEEP BOT ALIVE ---
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Bot is running!"

def run_http_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- VIDEO PROCESSING LOGIC ---
# --- SMART VIDEO PROCESSING LOGIC ---
async def convert_to_webm(input_path, output_path):
    try:
        # 1. Get video dimensions and duration
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", 
             "-show_entries", "stream=width,height,duration", "-of", "csv=s=x:p=0", input_path],
            capture_output=True, text=True
        )
        parts = probe.stdout.strip().split('x')
        w, h = int(parts[0]), int(parts[1])
        
        # Try to get duration, default to 3s if missing
        try:
            duration = float(parts[2])
        except (IndexError, ValueError):
            duration = 3.0

        # Scale logic: One side 512, other side <= 512
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

        # 3. Check File Size (Must be < 256KB)
        file_size_kb = os.path.getsize(temp_output) / 1024

        if file_size_kb <= 256:
            # It fits! Rename temp to actual output
            if os.path.exists(output_path): os.remove(output_path)
            os.rename(temp_output, output_path)
            return True
        else:
            # 4. Too big! Calculate exact bitrate to force fit
            # Target 250KB to be safe. Formula: (KB * 8192) / seconds = bits/sec
            target_bitrate = int((250 * 8192) / duration)
            print(f"resizing: {file_size_kb}kb is too big. Retrying with bitrate {target_bitrate}...")

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
        print(f"Error: {e}")
        return False

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.message
    
    # Identify file type
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
        input_f = f"{TEMP_FOLDER}/{user.id}_in"
        output_f = f"{TEMP_FOLDER}/{user.id}_sticker.webm"
        
        await new_file.download_to_drive(input_f)

        if await convert_to_webm(input_f, output_f):
            await status.edit_text("✅ Uploading...")
            await msg.reply_document(
                document=open(output_f, 'rb'),
                filename="sticker.webm",
                caption="Forward this file to @Stickers!"
            )
        else:
            await status.edit_text("❌ Failed. Video might be too complex.")
    except Exception as e:
        await status.edit_text("❌ Error occurred.")
    finally:
        if os.path.exists(input_f): os.remove(input_f)
        if os.path.exists(output_f): os.remove(output_f)

if __name__ == '__main__':
    # Start web server in background
    Thread(target=run_http_server).start()
    
    # Start Bot
    if not BOT_TOKEN:
        print("Error: BOT_TOKEN is missing!")
    else:
        app_bot = ApplicationBuilder().token(BOT_TOKEN).build()
        app_bot.add_handler(MessageHandler(filters.ANIMATION | filters.VIDEO | filters.Document.VIDEO, handle_document))
        print("Bot started...")
        app_bot.run_polling()