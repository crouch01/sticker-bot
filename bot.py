import os
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

# --- VIDEO PROCESSING ---
async def convert_to_webm(input_path, output_path):
    try:
        # 1. Get dimensions and duration
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", 
             "-show_entries", "stream=width,height,duration", "-of", "csv=s=x:p=0", input_path],
            capture_output=True, text=True
        )
        # Parse probe results (width, height, duration)
        # Note: sometimes duration is missing or N/A, so we default to 3s if unknown
        parts = probe.stdout.strip().split('x')
        w, h = int(parts[0]), int(parts[1])
        try:
            duration = float(parts[2])
        except (IndexError, ValueError):
            duration = 3.0

        # Scale logic: One side 512, other side <= 512
        scale = "scale=512:-1" if w >= h else "scale=-1:512"

        # 2. First Pass: Try High Quality (CRF 30)
        # We use a temporary file for the first attempt
        temp_output = output_path + ".temp.webm"
        
        cmd_hq = [
            "ffmpeg", "-y", "-i", input_path,
            "-c:v", "libvpx-vp9", "-an",
            "-vf", f"{scale},fps=30",
            "-t", "00:00:02.900", # Hard cap at 2.9s
            "-b:v", "0", "-crf", "30", # Constant Quality
            temp_output
        ]
        subprocess.run(cmd_hq, check=True)

        # 3. Check File Size
        file_size_kb = os.path.getsize(temp_output) / 1024

        if file_size_kb <= 256:
            # It fits! Rename temp to actual output
            os.rename(temp_output, output_path)
            return True
        else:
            # 4. It's too big! Calculate target bitrate to force fit.
            # Target size: 250KB (leave safety margin)
            # Formula: (Target KB * 8192) / duration_seconds = Bitrate in bits/sec
            target_total_bits = 250 * 8192
            target_bitrate = int(target_total_bits / duration)
            
            print(f"File too big ({file_size_kb:.2f}KB). Compressing with bitrate: {target_bitrate}...")

            cmd_compress = [
                "ffmpeg", "-y", "-i", input_path,
                "-c:v", "libvpx-vp9", "-an",
                "-vf", f"{scale},fps=30",
                "-t", "00:00:02.900",
                "-b:v", str(target_bitrate), # Force calculated bitrate
                "-minrate", str(int(target_bitrate * 0.7)),
                "-maxrate", str(int(target_bitrate * 1.3)),
                output_path
            ]
            subprocess.run(cmd_compress, check=True)
            
            # Clean up temp file
            if os.path.exists(temp_output):
                os.remove(temp_output)
            return True

    except Exception as e:
        print(f"FFmpeg Error: {e}")
        return False

# --- STICKER PACK MANAGEMENT ---
async def add_to_pack(user_id, sticker_file_path, context):
    bot = context.bot
    bot_name = context.bot.username
    
    # Pack name must be unique and end with _by_BotUsername
    pack_name = f"videopack_{user_id}_by_{bot_name}"
    pack_title = f"Video Stickers {user_id}"
    
    # We need to open the file to upload it
    with open(sticker_file_path, 'rb') as f:
        sticker_file = f.read()

    try:
        # 1. Try to add to existing pack
        # standard emoji is 💿 for video stickers usually
        await bot.add_sticker_to_set(
            user_id=user_id,
            name=pack_name,
            sticker=InputSticker(sticker=sticker_file, format="video", emoji_list=["💿"])
        )
        return f"✅ Added to your pack!\n\n🔗 t.me/addstickers/{pack_name}"

    except TelegramError as e:
        if "Stickerset_invalid" in str(e):
            # 2. Pack doesn't exist, create it
            try:
                await bot.create_new_sticker_set(
                    user_id=user_id,
                    name=pack_name,
                    title=pack_title,
                    stickers=[InputSticker(sticker=sticker_file, format="video", emoji_list=["💿"])],
                    sticker_format="video"
                )
                return f"🎉 New pack created!\n\n🔗 t.me/addstickers/{pack_name}"
            except Exception as create_error:
                return f"❌ Failed to create pack: {create_error}"
        elif "Stickers_too_much" in str(e):
            return "❌ Pack is full (120 stickers). Delete some or ask dev for a new pack feature."
        else:
            return f"❌ Telegram Error: {e}"

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.message
    
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
            await status.edit_text("✨ Adding to sticker pack...")
            
            # Call the new function to add directly to pack
            result_text = await add_to_pack(user.id, output_f, context)
            
            await status.edit_text(result_text)
            
            # Optional: Send the file anyway just in case
            # await msg.reply_document(open(output_f, 'rb'), filename="sticker.webm")
            
        else:
            await status.edit_text("❌ Failed. Video might be too complex.")
            
    except Exception as e:
        print(e)
        await status.edit_text("❌ Error occurred.")
    finally:
        if os.path.exists(input_f): os.remove(input_f)
        if os.path.exists(output_f): os.remove(output_f)

if __name__ == '__main__':
    Thread(target=run_http_server).start()
    
    if not BOT_TOKEN:
        print("Error: BOT_TOKEN is missing!")
    else:
        app_bot = ApplicationBuilder().token(BOT_TOKEN).build()
        app_bot.add_handler(MessageHandler(filters.ANIMATION | filters.VIDEO | filters.Document.VIDEO, handle_document))
        print("Bot started...")
        app_bot.run_polling()