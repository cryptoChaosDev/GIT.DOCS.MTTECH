FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies including git and git-lfs
RUN apt-get update && apt-get install -y \
    git \
    curl \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Install Git LFS
RUN apt-get update \
    && apt-get install -y git-lfs \
    && git lfs install --system \
    && git lfs version

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Create necessary directories
RUN mkdir -p logs user_repos

# Make the start script executable
RUN chmod +x start_bot.sh

# Expose port (though this is a polling bot, it's good practice)
EXPOSE 8443

# Run the bot
CMD ["python", "bot.py"]