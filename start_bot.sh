#!/bin/bash

# GitDocs Bot startup script

# Wait for any initialization
sleep 2

# Ensure necessary directories exist
mkdir -p user_repos logs

# Start the bot
python bot.py