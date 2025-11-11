# Naruto Botto Companion

## Overview
An enhanced Python Discord bot that serves as an intelligent companion for the Naruto Botto game bot. This bot features smart cooldown detection, visual progress tracking, and automatic notifications to help players manage their game timers efficiently.

## Purpose
- Monitor Naruto Botto bot messages and automatically detect cooldowns
- Smart tracking that detects existing cooldowns before starting new ones
- Track manual cooldowns for players using simple commands
- Ping users in the server when cooldowns expire
- Provide beautiful visual dashboards with progress bars
- Auto-answer Naruto trivia and quiz questions (optional)

## Key Features

### Smart Cooldown Detection ✨
The bot now intelligently checks if you already have an active cooldown before starting a new timer:
- When you use `n d` (daily), the bot waits 5 seconds to see if Naruto Botto reports an existing cooldown
- If an existing cooldown is detected, the bot uses that time instead of starting a new timer
- If no cooldown exists, it starts a fresh timer
- Works seamlessly with auto-detection from Naruto Botto messages

### Cooldown Tracking
The bot tracks cooldowns for six main game commands:
- **Mission** (n m): 1 minute cooldown
- **Report** (n r): 10 minutes cooldown
- **Tower** (n to): 6 hours cooldown - **NOW USES 'n to' INSTEAD OF 'n t'**
- **Daily** (n d): 24 hours cooldown - **WITH SMART DETECTION**
- **Weekly** (n w): 7 days cooldown
- **Challenge** (n ch): 30 minutes cooldown - **NEW COMMAND**

### Visual Dashboard 📊
Access your personalized cooldown dashboard with:
- Beautiful card-based layout
- Visual progress bars for each cooldown
- Color-coded status indicators
- Next-ready countdown
- Activity statistics

### Enhanced UI
- Rich Discord embeds for all commands
- Progress bars showing cooldown completion
- Color-coded status (ready, active, expiring)
- Emoji indicators for each activity type
- Naruto-themed messages and reactions

## Commands

### 🎮 Cooldown Tracking
All commands use the prefix `n ` (lowercase n followed by a space):

- `n mission` or `n m` - Track mission cooldown (1 minute)
- `n report` or `n r` - Track report cooldown (10 minutes)
- `n tower` or `n to` - Track tower cooldown (6 hours) **[CHANGED FROM n t]**
- `n daily` or `n d` - Track daily cooldown (24 hours) **[SMART DETECTION]**
- `n weekly` or `n w` - Track weekly cooldown (7 days)
- `n challenge` or `n ch` - Track challenge cooldown (30 minutes) **[NEW]**

### 📊 Dashboard & Status
- `n dashboard` or `n db` - View your beautiful cooldown dashboard **[NEW]**
- `n status` - Alternative command for dashboard
- `n cooldown user` or `n cd user` - Check your own cooldowns (text format)
- `n cooldown user @mention` - Check someone else's cooldowns

### 🛡️ Admin Commands (Requires Manage Server permission)
- `n cooldown list` or `n cd list` - Show all active cooldowns across the server
- `n cooldown clear @mention` - Clear all cooldowns for a user
- `n cooldown clear @mention mission` - Clear specific cooldown for a user

### ℹ️ Help
- `n help` - Show all available commands with descriptions

## How It Works

### Smart Detection Flow (for n daily)
1. User runs `n d` command
2. Bot checks if there's already an active cooldown tracked
3. If yes, shows the existing cooldown with progress bar
4. If no, bot waits 5 seconds while monitoring Naruto Botto's response
5. If Naruto Botto posts a cooldown message, bot captures that time
6. If no response detected, bot starts a fresh 24-hour timer
7. User gets pinged in the server channel when cooldown expires

### Auto-Detection
- When Naruto Botto posts a cooldown message mentioning a user, the companion bot automatically parses the time and tracks it
- The bot monitors message content and embeds for time information
- Supports various time formats: seconds, minutes, hours, days

### Notification System
- **Server Pings Only**: Users are now pinged in the channel where they started the cooldown
- **No More DMs**: All notifications happen in the server for better visibility
- Notifications include fun, randomized Naruto-themed messages
- Automatic reaction emojis on detected cooldowns

## Project Architecture

### File Structure
```
.
├── bot.py                 # Main bot application with all features
├── keep_alive.py          # Flask server for bot uptime monitoring
├── requirements.txt       # Python dependencies (pip format)
├── pyproject.toml        # Python project configuration
├── README.md             # User-facing documentation
├── replit.md             # Technical documentation (this file)
├── .env.example          # Example environment variables
├── .gitignore            # Python gitignore
└── cooldowns.json        # Persistent cooldown storage (auto-generated)
```

### Dependencies
Managed via `requirements.txt`:
- **discord.py**: Discord API wrapper for bot functionality
- **openai**: OpenAI API client for GPT quiz answers (optional)
- **flask**: Web server for keep-alive functionality

Built-in Python modules:
- **asyncio**: Async task handling
- **json**: Cooldown data persistence
- **re**: Time parsing from messages
- **datetime**: Time calculations

### Key Components

#### Cooldown System
- `cooldown_times`: Dictionary defining cooldown durations for each command
- `aliases`: Short command aliases (m, r, to, d, w, ch)
- `cooldowns`: In-memory storage of active cooldowns
- `cooldown_colors`: Color coding for different activity types
- `cooldown_emojis`: Emoji indicators for each activity
- `save_cooldowns()`: Persists cooldowns to JSON file
- `load_cooldowns()`: Loads cooldowns from JSON file on startup
- `get_remaining_time()`: Calculates remaining cooldown time for a user

#### Smart Tracking
- `track_cooldown_smart()`: Implements intelligent cooldown detection
- `user_command_tracking`: Tracks user commands for smart detection
- Waits for Naruto Botto response before deciding to start new timer

#### Visual Components
- `get_progress_bar()`: Creates visual progress bars
- `format_time()`: Formats time into readable strings
- Rich Discord embeds with color coding
- Thumbnail images and footer messages

#### Time Parsing
- `parse_time_string()`: Regex-based parser that extracts time durations from Naruto Botto messages
- Supports: seconds, minutes, hours, days

#### Background Tasks
- `check_expired_cooldowns()`: Runs every 30 seconds to check for expired cooldowns
- Sends notifications to server channels when cooldowns finish
- Automatic cleanup of old expired cooldowns

#### Event Handlers
- `on_ready()`: Initializes bot, loads saved cooldowns, starts background tasks
- `on_message()`: Handles both Naruto Botto messages and player commands

#### GPT Integration (Optional)
- `ask_gpt()`: Sends quiz questions to OpenAI GPT-4o-mini for automatic answers
- System prompt instructs GPT to answer Naruto trivia concisely
- Only enabled when `ENABLE_GPT=true` environment variable is set

#### Keep-Alive System
- Flask web server runs on port 8080 in a separate thread
- Provides a health check endpoint for monitoring bot status
- Helps keep the bot running 24/7 on cloud platforms

## Environment Variables
- `DISCORD_TOKEN`: Discord bot token (required)
- `OPENAI_API_KEY`: OpenAI API key for quiz answers (optional)
- `ENABLE_GPT`: Set to "true" to enable quiz auto-answer feature (optional, default: false)

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
   - Optionally add `OPENAI_API_KEY` secret for quiz features
   - Optionally set `ENABLE_GPT=true` to enable quiz auto-answer

3. **Running the Bot**:
   - Dependencies are auto-installed from pyproject.toml
   - The workflow runs `python bot.py`
   - Bot will connect and start monitoring

## Recent Changes

### 2025-11-11: Major Enhancement Update (v0.2.0)
- **Smart Cooldown Detection**: `n d` (daily) now checks for existing Naruto Botto cooldowns before starting new timers
- **Tower Command Fix**: Changed abbreviation from `n t` to `n to` as requested
- **New Challenge Command**: Added `n challenge` or `n ch` with 30-minute timer
- **Notification System Update**: Removed DMs, now only pings users in server channels
- **Visual Dashboard**: New `n dashboard` command with beautiful card layout and progress bars
- **Enhanced UI**: All commands now use rich embeds with color-coded status and progress bars
- **Progress Tracking**: Visual progress bars showing cooldown completion percentage
- **Improved Help**: Updated help command with all new features and better organization
- **Better UX**: Smoother interactions, better error messages, more engaging responses

### 2025-11-09: Initial Feature Set
- Fixed critical bug: Bot no longer responds to its own messages
- Expiration reminders: Bot pings users in channel when cooldowns finish
- Admin commands: Added cooldown list, user check, and clear commands
- Better messaging: Fun, engaging Naruto-themed messages
- Improved data model: Cooldowns track channel IDs for proper notifications
- GPT made optional: Set ENABLE_GPT environment variable
- Error handling: Graceful handling of API errors

### 2025-11-07: Initial Release
- Created main bot.py with cooldown tracking system
- Integrated OpenAI for quiz auto-answers
- Added persistent cooldown storage
- Set up workflow for continuous bot operation
- Configured Python dependencies

## User Preferences
- Language: Python 3.11
- Framework: discord.py
- AI Model: GPT-4o-mini (lightweight, fast responses)
- Storage: JSON file-based persistence
- Deployment: Replit with VM deployment for 24/7 operation
- UI Style: Rich Discord embeds with visual indicators

## Usage Notes
- The bot needs "Presence Intent", "Server Members Intent", and "Message Content Intent" enabled in Discord Developer Portal
- Cooldown data is automatically saved and loaded
- The bot can run 24/7 to monitor Naruto Botto and help players
- Smart detection works best when bot has time to observe Naruto Botto's response
- Progress bars update in real-time as you check your status
- Dashboard provides a comprehensive overview of all your activities
- The Flask server runs on port 8080 for keep-alive monitoring

## Tips for Users
- Use `n dashboard` for a quick visual overview of all your cooldowns
- The `n d` command is smart - it won't create duplicate timers
- Check your progress anytime with `n cd user`
- Admins can use `n cd list` to see all server activity
- All notifications now happen in the server - no more DMs!
