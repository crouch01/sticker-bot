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
async def convert_to_webm(input_path, output_path):
    try:
        # Get video dimensions
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", 
             "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", input_path],
            capture_output=True, text=True
        )
        w, h = map(int, probe.stdout.strip().split('x'))

        # Scale logic: One side 512, other side <= 512
        scale = "scale=512:-1" if w >= h else "scale=-1:512"

        # Conversion command
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-c:v", "libvpx-vp9", "-an",
            "-vf", f"{scale},fps=30",
            "-t", "00:00:02.900",
            "-b:v", "0", "-crf", "30",
            "-fs", "255000",
            output_path
        ]
        subprocess.run(cmd, check=True)
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