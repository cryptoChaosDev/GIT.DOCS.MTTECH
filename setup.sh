#!/bin/bash
# Setup script for Git Docs Bot

echo "🚀 Setting up Git Docs Telegram Bot..."

# Check if .env file exists
if [ ! -f ".env" ]; then
    echo "📝 Creating .env file from template..."
    cp .env.example .env
    echo "✅ Created .env file"
    echo "⚠️  Please edit .env file and add your BOT_TOKEN and ADMIN_IDS"
else
    echo "✅ .env file already exists"
fi

# Create directories for volumes
echo "📁 Creating data directories..."
mkdir -p data user_repos logs
echo "✅ Data directories created"

# Set permissions
echo "🔧 Setting permissions..."
chmod 755 data user_repos logs
echo "✅ Permissions set"

echo "🎉 Setup complete!"
echo "Next steps:"
echo "1. Edit .env file with your configuration"
echo "2. Run: docker-compose up -d --build"