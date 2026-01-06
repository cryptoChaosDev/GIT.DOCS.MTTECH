# GitDocs Bot

A Telegram bot that manages DOCX documents in Git repositories through a chat interface.

## Features

- Clone and manage Git repositories
- View and download DOCX documents
- Lock documents to prevent concurrent edits
- Upload updated documents
- Git operations (pull, commit, push)
- Admin functions for repository management
- Per-user repository isolation

## Prerequisites

- Docker and Docker Compose
- A Telegram bot token (get one from [@BotFather](https://t.me/BotFather))
- Git LFS enabled repository

## Quick Deployment with Docker

### 1. Clone the repository

```bash
git clone <your-repo-url>
cd git-docs-bot
```

### 2. Configure environment variables

Copy the example environment file and update with your values:

```bash
cp .env.example .env
# Edit the .env file with your bot token and other settings
```

### 3. Deploy with Docker

```bash
chmod +x deploy.sh
./deploy.sh
```

## Manual Deployment

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Set environment variables

Create a `.env` file with the following content:

```env
BOT_TOKEN=your_telegram_bot_token
ADMIN_IDS=309462378
AUTO_UNLOCK_ON_UPLOAD=false
```

### 3. Run the bot

```bash
python bot.py
```

## Configuration

- `BOT_TOKEN`: Your Telegram bot token from BotFather
- `ADMIN_IDS`: Comma-separated list of user IDs with admin privileges
- `AUTO_UNLOCK_ON_UPLOAD`: Whether to automatically unlock documents after upload (true/false)

## Volumes

The Docker deployment persists data in these volumes:

- `user_repos/`: Per-user repository clones
- `logs/`: Application logs
- `locks.json`: Document lock information
- `user_repos.json`: User repository mappings

## Management Commands

When using Docker Compose:

```bash
# View logs
docker-compose logs -f git-docs-bot

# Restart the bot
docker-compose restart git-docs-bot

# Stop the bot
docker-compose down

# Update the bot (rebuild and restart)
docker-compose up -d --build
```

## Security Notes

- Never commit your bot token to version control
- The bot stores repository credentials temporarily in memory
- Rate limiting is implemented to prevent abuse
- Only configured admin users can perform sensitive operations

## Troubleshooting

If the bot fails to start, check the logs:

```bash
docker-compose logs git-docs-bot
```

Make sure:
- Your bot token is correct
- The bot has necessary permissions in your Git repositories
- Git LFS is properly configured in your repositories