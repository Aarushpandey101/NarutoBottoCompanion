# Naruto Botto Companion

## Overview
A Python Discord bot that serves as a companion for the Naruto Botto game bot. This bot helps players track cooldowns for various game commands and automatically answers Naruto-related quiz questions using GPT.

## Purpose
- Monitor Naruto Botto bot messages and automatically detect cooldowns
- Track manual cooldowns for players using simple commands
- Auto-answer Naruto trivia and quiz questions
- Provide user-friendly cooldown warnings with formatted time remaining

## Features

### Cooldown Tracking
The bot tracks cooldowns for five main game commands:
- **Mission** (n m): 1 minute cooldown
- **Report** (n r): 10 minutes cooldown
- **Tower** (n t): 6 hours cooldown
- **Daily** (n d): 24 hours cooldown
- **Weekly** (n w): 7 days cooldown

### How It Works
1. **Auto-detection**: When Naruto Botto posts a cooldown message mentioning a user, the companion bot automatically parses the time and tracks it
2. **Manual tracking**: Users can trigger cooldowns manually with commands like `n mission`, `n m`, `n report`, etc.
3. **Persistent storage**: Cooldowns are saved to `cooldowns.json` and survive bot restarts
4. **Auto-cleanup**: Expired cooldowns are automatically removed every minute

### GPT Quiz Assistant
When Naruto Botto posts quiz or trivia questions, the companion bot uses OpenAI's GPT-4o-mini model to automatically answer them.

## Commands

### 🎮 Cooldown Tracking
All commands use the prefix `n ` (lowercase n followed by a space):

- `n mission` or `n m` - Track mission cooldown (1 minute)
- `n report` or `n r` - Track report cooldown (10 minutes)
- `n tower` or `n t` - Track tower cooldown (6 hours)
- `n daily` or `n d` - Track daily cooldown (24 hours)
- `n weekly` or `n w` - Track weekly cooldown (7 days)

### 📊 Check Cooldowns
- `n cooldown user` or `n cd user` - Check your own cooldowns
- `n cooldown user @mention` - Check someone else's cooldowns

### 🛡️ Admin Commands (Requires Manage Server permission)
- `n cooldown list` or `n cd list` - Show all active cooldowns across the server
- `n cooldown clear @mention` - Clear all cooldowns for a user
- `n cooldown clear @mention mission` - Clear specific cooldown for a user

### ℹ️ Help
- `n help` - Show all available commands

## Project Architecture

### File Structure
```
.
├── bot.py                 # Main bot application
├── keep_alive.py          # Flask server for bot uptime monitoring
├── cooldowns.json         # Persistent cooldown storage (auto-generated)
├── .gitignore            # Python gitignore
├── replit.md             # This documentation file
├── requirements.txt      # Python dependencies
└── pyproject.toml        # Python project configuration
```

### Dependencies
- **discord.py**: Discord API wrapper for bot functionality
- **openai**: OpenAI API client for GPT quiz answers
- **flask**: Web server for keep-alive functionality
- **asyncio**: Async task handling
- **json**: Cooldown data persistence

### Key Components

#### Cooldown System
- `cooldown_times`: Dictionary defining cooldown durations for each command
- `aliases`: Short command aliases (m, r, t, d, w)
- `cooldowns`: In-memory storage of active cooldowns
- `save_cooldowns()`: Persists cooldowns to JSON file
- `load_cooldowns()`: Loads cooldowns from JSON file on startup
- `get_remaining_time()`: Calculates remaining cooldown time for a user

#### Time Parsing
- `parse_time_string()`: Regex-based parser that extracts time durations from Naruto Botto messages
- Supports: seconds, minutes, hours, days

#### Background Tasks
- `remove_expired_cooldowns()`: Runs every minute to clean up expired cooldowns and save to disk

#### Event Handlers
- `on_ready()`: Initializes bot, loads saved cooldowns, starts background tasks
- `on_message()`: Handles both Naruto Botto messages and player commands

#### GPT Integration
- `ask_gpt()`: Sends quiz questions to OpenAI GPT-4o-mini for automatic answers
- System prompt instructs GPT to answer Naruto trivia concisely

#### Keep-Alive System
- Flask web server runs on port 8080 in a separate thread
- Provides a health check endpoint for monitoring bot status
- Helps keep the bot running 24/7 on cloud platforms

## Environment Variables
- `DISCORD_TOKEN`: Discord bot token (required)
- `OPENAI_API_KEY`: OpenAI API key for quiz answers (required)

## Setup Instructions

1. **Discord Bot Setup**:
   - Go to [Discord Developer Portal](https://discord.com/developers/applications)
   - Create a new application
   - Enable the bot and get your bot token
   - Enable these Privileged Gateway Intents:
     - Presence Intent
     - Server Members Intent
     - Message Content Intent
   - Add the bot to your Discord server

2. **Environment Setup**:
   - Add `DISCORD_TOKEN` secret with your Discord bot token
   - Add `OPENAI_API_KEY` secret with your OpenAI API key

3. **Running the Bot**:
   - Install dependencies: Dependencies are auto-installed from pyproject.toml
   - Run the bot: The workflow runs `python bot.py`

## Recent Changes
- **2025-11-09**: Major bot redesign and feature additions
  - **Fixed critical bug**: Bot no longer responds to its own messages (eliminated duplicate notifications)
  - **Expiration reminders**: Bot now pings users in channel and DM when cooldowns finish
  - **Admin commands**: Added `cooldown list`, `cooldown user`, `cooldown clear` for server moderation
  - **Better messaging**: Fun, engaging Naruto-themed messages with randomized responses
  - **Improved data model**: Cooldowns now track channel IDs for proper notification targeting
  - **GPT made optional**: Set `ENABLE_GPT=true` environment variable to enable quiz features
  - **Error handling**: Graceful handling of API errors without log spam
  - **Help command**: Added comprehensive help with `n help`
  - **Better UX**: Emoji indicators, embedded rich responses, formatted time displays

- **2025-11-07**: Initial project setup on Replit
  - Created main bot.py with cooldown tracking system
  - Integrated OpenAI for quiz auto-answers
  - Added persistent cooldown storage
  - Set up workflow for continuous bot operation
  - Configured Python dependencies with discord.py and openai
  - Added Flask keep-alive server for 24/7 uptime

## User Preferences
- Language: Python 3.11
- Framework: discord.py
- AI Model: GPT-4o-mini (lightweight, fast responses)
- Storage: JSON file-based persistence
- Deployment: Replit with VM deployment for 24/7 operation

## Usage Notes
- The bot needs "Presence Intent", "Server Members Intent", and "Message Content Intent" enabled in Discord Developer Portal
- Cooldown data is automatically saved and loaded
- The bot can run 24/7 to monitor Naruto Botto and help players
- Quiz auto-answer feature works best when Naruto Botto's messages contain keywords like "quiz", "question", or "?"
- The Flask server runs on port 8080 for keep-alive monitoring
