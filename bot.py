from keep_alive import keep_alive
import discord
from discord.ext import commands, tasks
import asyncio
import json
import re
import time
from openai import OpenAI
import os
from typing import Dict, Optional
import random

openai_client = None
try:
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        openai_client = OpenAI(api_key=api_key)
except:
    pass

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")

if not DISCORD_TOKEN:
    print("❌ Discord token not found in environment variables!")
else:
    print(f"✅ Loaded token: {DISCORD_TOKEN[:10]}... (length={len(DISCORD_TOKEN)})")

ENABLE_GPT = os.getenv("ENABLE_GPT", "false").lower() == "true"

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

# Case insensitive commands enabled
bot = commands.Bot(command_prefix="n ", intents=intents, help_command=None, case_insensitive=True)

cooldown_times = {
    "mission": 60,
    "report": 600,
    "tower": 6 * 3600,
    "daily": 24 * 3600,
    "weekly": 7 * 24 * 3600
}

aliases = {
    "m": "mission",
    "r": "report",
    "to": "tower",
    "d": "daily",
    "w": "weekly"
}

cooldown_emojis = {
    "mission": "⚔️",
    "report": "📋",
    "tower": "🗼",
    "daily": "📅",
    "weekly": "🎁"
}

cooldowns = {}

def format_time(seconds):
    if seconds <= 0:
        return "Ready!"
    days, remainder = divmod(int(seconds), 86400)
    hours, remainder = divmod(remainder, 3600)
    mins, secs = divmod(remainder, 60)
    parts = []
    if days > 0: parts.append(f"{days}d")
    if hours > 0: parts.append(f"{hours}h")
    if mins > 0: parts.append(f"{mins}m")
    if secs > 0 and days == 0: parts.append(f"{secs}s")
    return " ".join(parts)

def parse_time_string(text):
    total_seconds = 0
    text = text.lower()
    matches = re.findall(r"(\d+)\s*(day|days|d|hour|hours|h|minute|minutes|min|second|seconds|sec|s)", text)
    for amount, unit in matches:
        amount = int(amount)
        if "day" in unit or unit == "d":
            total_seconds += amount * 86400
        elif "hour" in unit or unit == "h":
            total_seconds += amount * 3600
        elif "minute" in unit or unit == "min":
            total_seconds += amount * 60
        elif "second" in unit or unit == "sec" or unit == "s":
            total_seconds += amount
    return total_seconds

def save_cooldowns():
    try:
        with open("cooldowns.json", "w") as f:
            json.dump(cooldowns, f, indent=2)
    except Exception as e:
        print(f"❌ Error saving cooldowns: {e}")

def load_cooldowns():
    global cooldowns
    try:
        with open("cooldowns.json") as f:
            cooldowns = json.load(f)
        print(f"📂 Loaded cooldowns for {len(cooldowns)} users")
    except FileNotFoundError:
        print("📂 No existing cooldowns file found, starting fresh")
        cooldowns = {}

def get_remaining_time(user_id, cmd):
    if user_id in cooldowns and cmd in cooldowns[user_id]:
        remaining = cooldowns[user_id][cmd]["expires_at"] - time.time()
        return max(0, remaining)
    return 0

@tasks.loop(seconds=30)
async def check_expired_cooldowns():
    now = time.time()
    expired = []
    for user_id, cmds in list(cooldowns.items()):
        for cmd, data in list(cmds.items()):
            if data["expires_at"] <= now and not data.get("notified", False):
                expired.append((user_id, cmd, data.get("channel_id")))
                cooldowns[user_id][cmd]["notified"] = True

    for user_id, cmd, channel_id in expired:
        try:
            user = await bot.fetch_user(user_id)
            emoji = cooldown_emojis.get(cmd, "✅")
            msg = f"{emoji} **{cmd.upper()} READY!** Time to get back out there, {user.mention}!"
            if channel_id:
                channel = bot.get_channel(channel_id)
                if channel:
                    await channel.send(msg)
            try:
                await user.send(msg)
            except:
                pass
        except Exception as e:
            print(f"❌ Error notifying user {user_id} for {cmd}: {e}")
    save_cooldowns()

@bot.event
async def on_ready():
    load_cooldowns()
    check_expired_cooldowns.start()
    print(f"🎌 {bot.user} is online and tracking cooldowns!")

# 🧠 Detect Naruto Botto messages and sync cooldowns
@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    # Detect official Naruto Botto message
    if message.author.bot and "naruto botto" in message.author.name.lower():
        text = message.content.lower()

        # Detect cooldown lines like "Wait 19h 46m 47s for your next daily ..."
        match = re.search(r"wait\s+([\dhms\s]+)\s+for your next\s+(\w+)", text)
        if match:
            time_text = match.group(1)
            activity = match.group(2)
            seconds = parse_time_string(time_text)
            if seconds > 0 and message.mentions:
                user = message.mentions[0]
                cooldowns.setdefault(str(user.id), {})[activity] = {
                    "expires_at": time.time() + seconds,
                    "channel_id": message.channel.id,
                    "notified": False
                }
                save_cooldowns()
                print(f"⏳ Synced {activity} cooldown for {user.name}: {time_text}")
        return

    await bot.process_commands(message)

# --- Commands ---
async def track_cooldown(ctx, cmd):
    user_id = str(ctx.author.id)
    remaining = get_remaining_time(user_id, cmd)
    emoji = cooldown_emojis.get(cmd, "⏰")

    if remaining > 0:
        await ctx.send(f"{emoji} Not so fast! You need to wait **{format_time(remaining)}** before your **{cmd}** is ready, {ctx.author.mention}!")
        return

    cooldowns.setdefault(user_id, {})[cmd] = {
        "expires_at": time.time() + cooldown_times[cmd],
        "channel_id": ctx.channel.id,
        "notified": False
    }
    save_cooldowns()
    await ctx.send(f"{emoji} **{cmd.upper()}** tracked! I'll ping you in **{format_time(cooldown_times[cmd])}**, {ctx.author.mention}! Dattebayo!")

@bot.command(name="m", aliases=["mission"])
async def mission(ctx): await track_cooldown(ctx, "mission")

@bot.command(name="r", aliases=["report"])
async def report(ctx): await track_cooldown(ctx, "report")

@bot.command(name="to", aliases=["tower"])
async def tower(ctx): await track_cooldown(ctx, "tower")

@bot.command(name="d", aliases=["daily"])
async def daily(ctx): await track_cooldown(ctx, "daily")

@bot.command(name="w", aliases=["weekly"])
async def weekly(ctx): await track_cooldown(ctx, "weekly")

# Help command
@bot.command(name="help", aliases=["commands", "h"])
async def help_command(ctx):
    embed = discord.Embed(
        title="🎌 Naruto Botto Companion - Commands",
        description="Your personal cooldown tracker and companion!",
        color=discord.Color.gold()
    )
    embed.add_field(
        name="⚔️ Track Cooldowns",
        value="```\nn m / n mission  - Track mission (1min)\nn r / n report   - Track report (10min)\nn to / n tower    - Track tower (6hrs)\nn d / n daily    - Track daily (24hrs)\nn w / n weekly   - Track weekly (7days)```",
        inline=False
    )
    embed.set_footer(text="Made with ❤️ for Naruto Botto players")
    await ctx.send(embed=embed)

keep_alive()
bot.run(DISCORD_TOKEN)
