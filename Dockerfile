# Use a newer, reliable Python version
FROM python:3.11

# Install FFmpeg (Video tools)
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Set up folder
WORKDIR /app

# Copy requirements first
COPY requirements.txt .

# CRITICAL FIX: Upgrade pip (the installer) first
RUN pip install --upgrade pip

# Install libraries
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the code
COPY . .

# Start the bot
CMD ["python", "bot.py"]