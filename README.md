# Naruto Botto Companion 🍜⚡

An intelligent Discord bot that helps you track cooldowns for the Naruto Botto game bot with smart detection, visual dashboards, and automatic notifications.

## Features ✨

- **Smart Cooldown Detection** - Automatically detects existing cooldowns before starting new timers
- **Auto-Detection** - Monitors Naruto Botto messages and tracks cooldowns automatically
- **Visual Dashboard** - Beautiful progress bars and color-coded status indicators
- **Server Notifications** - Get pinged in your server when cooldowns expire
- **Quiz Auto-Answer** - Optional Gemini-powered automatic Naruto trivia answers

## Quick Start 🚀

### 1. Set Up Discord Bot
1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a new application
3. Enable these **Privileged Gateway Intents**:
   - ✓ Presence Intent
   - ✓ Server Members Intent
   - ✓ Message Content Intent
4. Copy your bot token

### 2. Add Secrets in Replit
Click the Secrets tab (🔒) and add:
- `DISCORD_TOKEN` - Your Discord bot token (required)
- `GEMINI_API_KEY` - Your Gemini API key from Google AI Studio (optional, for quiz feature)
- `ENABLE_GPT` - Set to `true` to enable quiz auto-answer (optional)
- `NARUTO_BOTTO_USER_ID` - Optional Naruto Botto user ID for more reliable detection
- `SMART_TRACK_WAIT_SECONDS` - Optional smart-detection delay in seconds, default `3.5`

### 3. Invite Bot to Your Server
Use the OAuth2 URL generator in Discord Developer Portal with these permissions:
- Send Messages
- Read Message History
- Mention Everyone (for pings)

## Commands 🎮

### Track Cooldowns
- `n m` / `n mission` - Mission (1 minute)
- `n r` / `n report` - Report (10 minutes)
- `n to` / `n tower` - Tower (6 hours)
- `n d` / `n daily` - Daily (24 hours) with smart detection
- `n w` / `n weekly` - Weekly (7 days)
- `n ch` / `n challenge` - Challenge (30 minutes)

### View Status
- `n dashboard` / `n db` - Beautiful visual dashboard
- `n status` - Alternative dashboard command
- `n cd user` - Check your cooldowns (text format)
- `n cd user @mention` - Check someone's cooldowns

### Admin Commands
- `n cd list` - View all server cooldowns
- `n cd clear @user` - Clear all cooldowns for a user
- `n cd clear @user mission` - Clear specific cooldown

### Help
- `n help` - Show all commands

## How It Works 🔧

1. **Smart Detection**: When you use `n d`, the bot waits about 3.5 seconds to check if Naruto Botto reports an existing cooldown
2. **Auto-Tracking**: The bot monitors Naruto Botto's messages and automatically tracks cooldowns
3. **Notifications**: When a cooldown expires, you get pinged in the channel where you started it
4. **Persistence**: All cooldowns are saved to `cooldowns.sqlite3` and survive restarts

## Tech Stack 💻

- **Python 3.11** - Main language
- **discord.py** - Discord bot framework
- **Google Gemini** - Quiz auto-answer (optional)
- **Flask** - Keep-alive web server
- **SQLite** - Persistent data storage

## Files 📁

- `bot.py` - Main bot code
- `keep_alive.py` - Flask server for uptime
- `requirements.txt` - Python dependencies
- `cooldowns.sqlite3` - Persistent cooldown storage (auto-generated)
- `replit.md` - Detailed technical documentation

## Deployment 🚀

This bot is designed to run 24/7 on Replit. Once you've added your secrets, the bot will automatically start and stay online.

To deploy to production with a custom domain:
1. Click the "Deploy" button
2. Choose "VM" deployment for always-on operation
3. Follow the deployment wizard

## Support 💬

For detailed documentation, see `replit.md`.

---

Made with ❤️ for Naruto Botto players
