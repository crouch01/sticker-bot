# Use a lightweight Python setup
FROM python:3.9

# Install the video processing tool (FFmpeg)
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Set up the folder
WORKDIR /app

# Install the python libraries
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy your bot code
COPY . .

# Start the bot

CMD ["python", "bot.py"]
