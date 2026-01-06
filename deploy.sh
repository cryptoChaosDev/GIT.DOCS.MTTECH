#!/bin/bash

# GitDocs Bot Deployment Script

set -e  # Exit on any error

echo "🚀 Starting GitDocs Bot deployment..."

# Check if docker is installed
if ! [ -x "$(command -v docker)" ]; then
  echo "❌ Docker is not installed. Please install Docker first."
  exit 1
fi

# Check if docker-compose is installed
if ! [ -x "$(command -v docker-compose)" ]; then
  echo "⚠️  Docker Compose is not installed. Attempting to install..."
  if [ -x "$(command -v pip)" ]; then
    pip install docker-compose
  else
    echo "❌ Neither pip nor docker-compose is available. Please install docker-compose."
    exit 1
  fi
fi

# Check if .env file exists
if [ ! -f .env ]; then
  echo "⚠️  .env file not found. Creating a template..."
  cat > .env << EOL
# GitDocs Bot Configuration
BOT_TOKEN=your_bot_token_here
ADMIN_IDS=309462378
AUTO_UNLOCK_ON_UPLOAD=false
EOL
  echo "📝 Created .env template. Please update it with your bot token and other settings."
  echo "📋 Open the .env file and replace 'your_bot_token_here' with your actual bot token."
  exit 1
fi

# Load environment variables
source .env

# Validate BOT_TOKEN is not default
if [ "$BOT_TOKEN" = "your_bot_token_here" ] || [ -z "$BOT_TOKEN" ]; then
  echo "❌ BOT_TOKEN is not set properly in .env file. Please update it."
  exit 1
fi

echo "✅ Environment variables loaded successfully"

# Create necessary directories if they don't exist
mkdir -p user_repos logs

# Build and start the containers
echo "🐳 Building and starting Docker containers..."
docker-compose up -d --build

# Wait a moment for the container to start
sleep 5

# Check if the container is running
if [ "$(docker-compose ps -q git-docs-bot | wc -l)" -eq 0 ] || [ "$(docker-compose ps git-docs-bot | grep -c 'Up')" -eq 0 ]; then
  echo "❌ Docker container failed to start properly"
  echo "📋 Checking logs..."
  docker-compose logs git-docs-bot
  exit 1
fi

echo "✅ GitDocs Bot deployed successfully!"
echo "📊 Bot logs are available with: docker-compose logs -f git-docs-bot"
echo "🔄 To restart the bot: docker-compose restart git-docs-bot"
echo "🛑 To stop the bot: docker-compose down"