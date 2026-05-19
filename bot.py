from keep_alive import keep_alive
import asyncio
import datetime
import json
import os
import random
import re
import sqlite3
import time
from typing import Dict, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks
from google import genai
from google.genai import types

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
ENABLE_GPT = os.getenv("ENABLE_GPT", "false").lower() == "true"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
SMART_TRACK_WAIT_SECONDS = float(os.getenv("SMART_TRACK_WAIT_SECONDS", "3.5"))
QUIZ_ALLOW_LOCAL_FALLBACK = os.getenv("QUIZ_ALLOW_LOCAL_FALLBACK", "false").lower() == "true"
QUIZ_DEBUG = os.getenv("QUIZ_DEBUG", "false").lower() == "true"
NARUTO_BOTTO_USER_ID = None

try:
    raw_bot_id = os.getenv("NARUTO_BOTTO_USER_ID", "").strip()
    if raw_bot_id:
        NARUTO_BOTTO_USER_ID = int(raw_bot_id)
except ValueError:
    print("⚠️ Invalid NARUTO_BOTTO_USER_ID value; falling back to name matching.")

gemini_client = None
if GEMINI_API_KEY:
    try:
        gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        print(f"❌ Failed to initialize Gemini client: {e}")

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

# MODIFIED LINE BELOW: Added list for prefix and case_insensitive=True
bot = commands.Bot(command_prefix=["n ", "N "], case_insensitive=True, intents=intents, help_command=None)

DB_PATH = "cooldowns.sqlite3"
LEGACY_JSON_PATH = "cooldowns.json"

cooldown_times = {
    "mission": 60,
    "report": 600,
    "tower": 6 * 3600,
    "daily": 24 * 3600,
    "weekly": 7 * 24 * 3600,
    "challenge": 30 * 60
}

aliases = {
    "m": "mission",
    "r": "report",
    "to": "tower",
    "d": "daily",
    "w": "weekly",
    "ch": "challenge"
}

cooldown_emojis = {
    "mission": "⚔️",
    "report": "📋",
    "tower": "🗼",
    "daily": "📅",
    "weekly": "🎁",
    "challenge": "🥊"
}

cooldown_colors = {
    "mission": discord.Color.red(),
    "report": discord.Color.blue(),
    "tower": discord.Color.purple(),
    "daily": discord.Color.gold(),
    "weekly": discord.Color.green(),
    "challenge": discord.Color.orange()
}

cooldowns = {}
pending_smart_tracks = {}

def init_database():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cooldowns (
                user_id INTEGER NOT NULL,
                command TEXT NOT NULL,
                expires_at REAL NOT NULL,
                channel_id INTEGER,
                notified INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, command)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cooldowns_expires_at ON cooldowns(expires_at)"
        )

def is_naruto_botto_author(author) -> bool:
    if not author:
        return False

    if NARUTO_BOTTO_USER_ID and getattr(author, "id", None) == NARUTO_BOTTO_USER_ID:
        return True

    author_name = " ".join(
        str(value)
        for value in [
            getattr(author, "name", ""),
            getattr(author, "display_name", ""),
            getattr(author, "global_name", ""),
            getattr(author, "nick", ""),
            getattr(author, "username", ""),
        ]
        if value
    ).lower()
    return "naruto botto" in author_name

def cooldown_row_to_dict(row):
    return {
        "expires_at": float(row["expires_at"]),
        "channel_id": int(row["channel_id"]) if row["channel_id"] is not None else None,
        "notified": bool(row["notified"]),
    }

def should_show_progress_bar(cmd):
    return cmd in ["daily", "weekly"]

entertaining_messages = {
    "mission": [
        "Quick mission break! Almost ready, ninja! ⚡",
        "Missions coming back soon, Dattebayo! 🍜",
        "Training hard? Rest up quick! 🥷",
        "Back in action shortly! Dattebayo! 🔥"
    ],
    "report": [
        "Report cooldown active! Hang tight! 📝",
        "Paperwork takes time, even for ninjas! 📋",
        "Almost done filing that report! ✨",
        "Reports filing... ninja patience! 🎯"
    ],
    "tower": [
        "Tower climb in progress! Take a breather! 🗼",
        "The tower awaits your return! 💪",
        "Climbing takes time, rest those ninja legs! ⛰️",
        "Tower cooldown... prepare for the ascent! 🌟"
    ],
    "challenge": [
        "Challenge cooldown! Train hard, fight harder! 🥊",
        "Next challenge coming soon! Stay sharp! ⚔️",
        "Recover and prepare for the next battle! 💥",
        "Challenge mode recharging! Get ready! 🎮"
    ]
}

tracking_started_messages = {
    "mission": [
        "Mission log stamped. Kakashi would approve. 📘",
        "Quest radar locked. Go be dramatic, ninja. 🌪️",
        "Mission queued! Don't trip over your own kunai. 🗡️"
    ],
    "report": [
        "Report filed with 97% less paperwork pain. ✍️",
        "Intel secured. Time to look mysterious. 🕶️",
        "Report timer armed. Bureaucracy defeated (for now). 🗂️"
    ],
    "tower": [
        "Tower timer set. Stretch those shinobi calves. 🗼",
        "Ascent cooldown recorded. No elevator, sorry. 🧗",
        "Tower run locked in. Gravity remains undefeated. 🌌"
    ],
    "daily": [
        "Daily secured! Ramen budget: protected. 🍜",
        "Daily timer set. Wallet-kun says thank you. 💰",
        "Daily reward logged. Responsible ninja behavior detected. ✅"
    ],
    "weekly": [
        "Weekly recorded. Future-you sends gratitude. 🎁",
        "Weekly timer armed. Legendary patience mode activated. ⏳",
        "Weekly locked. Big reward energy building up. ⚡"
    ],
    "challenge": [
        "Challenge timer set. Main character aura intensifies. 🥊",
        "Battle cooldown logged. Dramatic comeback loading... 🎬",
        "Challenge recorded. Keep the hype alive. 🔥"
    ]
}

def get_friendly_tracking_message(cmd: str, duration: str) -> str:
    emoji = cooldown_emojis.get(cmd, "⏰")
    funny_line = random.choice(tracking_started_messages.get(cmd, ["Cooldown tracked!"]))
    return (
        f"{emoji} **{cmd.upper()} locked in!** Next run in **{duration}**.\n"
        f"🔔 I'll tag you the moment it's ready again.\n"
        f"{funny_line}"
    )

def format_time(seconds):
    if seconds <= 0:
        return "Ready!"
    
    days, remainder = divmod(int(seconds), 86400)
    hours, remainder = divmod(remainder, 3600)
    mins, secs = divmod(remainder, 60)
    
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if mins > 0:
        parts.append(f"{mins}m")
    if secs > 0 and days == 0:
        parts.append(f"{secs}s")
    
    return " ".join(parts) if parts else "Ready!"

def get_progress_bar(current, total, length=10):
    filled = int((current / total) * length)
    empty = length - filled
    percentage = int((current / total) * 100)
    
    bar = "█" * filled + "░" * empty
    return f"{bar} {percentage}%"

def get_cooldown_data(user_id: int, cmd: str) -> Optional[Dict]:
    if user_id in cooldowns and cmd in cooldowns[user_id]:
        return cooldowns[user_id][cmd]
    return None

def get_remaining_time(user_id: int, cmd: str) -> float:
    data = get_cooldown_data(user_id, cmd)
    if data:
        remaining = data["expires_at"] - time.time()
        return remaining if remaining > 0 else 0
    return 0

def save_cooldowns():
    try:
        init_database()
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("DELETE FROM cooldowns")
            rows = []
            for user_id, cmds in cooldowns.items():
                for cmd, data in cmds.items():
                    rows.append(
                        (
                            int(user_id),
                            cmd,
                            float(data["expires_at"]),
                            int(data["channel_id"]) if data.get("channel_id") is not None else None,
                            1 if data.get("notified", False) else 0,
                        )
                    )
            conn.executemany(
                """
                INSERT OR REPLACE INTO cooldowns (user_id, command, expires_at, channel_id, notified)
                VALUES (?, ?, ?, ?, ?)
                """,
                rows,
            )
    except Exception as e:
        print(f"❌ Error saving cooldowns: {e}")

def _load_legacy_json_cooldowns():
    legacy_data = {}
    try:
        with open(LEGACY_JSON_PATH) as f:
            data = json.load(f)
        for uid_str, cmds in data.items():
            uid = int(uid_str)
            legacy_data[uid] = {}
            for cmd, cmd_data in cmds.items():
                if isinstance(cmd_data, (int, float)):
                    legacy_data[uid][cmd] = {
                        "expires_at": float(cmd_data),
                        "channel_id": None,
                        "notified": False,
                    }
                else:
                    legacy_data[uid][cmd] = {
                        "expires_at": float(cmd_data.get("expires_at", 0)),
                        "channel_id": cmd_data.get("channel_id"),
                        "notified": bool(cmd_data.get("notified", False)),
                    }
        print(f"📦 Migrated {len(legacy_data)} user cooldown record(s) from legacy JSON")
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"❌ Error loading legacy cooldowns: {e}")
    return legacy_data

def load_cooldowns():
    global cooldowns
    try:
        init_database()
        loaded_count = 0
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT user_id, command, expires_at, channel_id, notified
                FROM cooldowns
                """
            ).fetchall()

        if rows:
            cooldowns = {}
            for row in rows:
                user_id = int(row["user_id"])
                cooldowns.setdefault(user_id, {})[row["command"]] = cooldown_row_to_dict(row)
                loaded_count += 1
            print(f"📂 Loaded {loaded_count} cooldown record(s) from SQLite")
            return

        legacy_data = _load_legacy_json_cooldowns()
        cooldowns = legacy_data
        if cooldowns:
            save_cooldowns()
            loaded_count = sum(len(cmds) for cmds in cooldowns.values())
            print(f"📂 Loaded {loaded_count} cooldown record(s) and migrated them to SQLite")
        else:
            print("📂 No existing cooldown storage found, starting fresh")
    except Exception as e:
        print(f"❌ Error loading cooldowns: {e}")
        cooldowns = {}

def parse_time_string(text):
    total_seconds = 0
    matches = re.findall(r"(\d+)\s*(second|seconds|sec|s|minute|minutes|min|m|hour|hours|h|day|days|d)\b", text.lower())
    for amount, unit in matches:
        amount = int(amount)
        if "second" in unit or unit == "s" or unit == "sec":
            total_seconds += amount
        elif "minute" in unit or unit == "min" or unit == "m":
            total_seconds += amount * 60
        elif "hour" in unit or unit == "h":
            total_seconds += amount * 3600
        elif "day" in unit or unit == "d":
            total_seconds += amount * 86400
    return total_seconds

@tasks.loop(seconds=30)
async def check_expired_cooldowns():
    now = time.time()
    expired_notifications = []
    
    for user_id, cmds in list(cooldowns.items()):
        for cmd, data in list(cmds.items()):
            if data["expires_at"] <= now:
                if not data.get("notified", False):
                    expired_notifications.append((user_id, cmd, data.get("channel_id")))
                    data["notified"] = True
    
    for user_id, cmd, channel_id in expired_notifications:
        try:
            user = await bot.fetch_user(user_id)
            emoji = cooldown_emojis.get(cmd, "✅")
            
            messages = [
                f"{emoji} **{cmd.upper()} READY!** Dattebayo! Time to get back out there, {user.mention}!",
                f"{emoji} Your **{cmd}** cooldown is complete! The ninja way never stops, {user.mention}!",
                f"{emoji} **{cmd.upper()}** is ready to go! Show them what you're made of, {user.mention}!",
                f"{emoji} Cooldown finished for **{cmd}**! Let's do this, {user.mention}!",
            ]
            
            message = random.choice(messages)
            
            if channel_id:
                try:
                    channel = bot.get_channel(channel_id)
                    if channel:
                        await channel.send(message)
                except:
                    pass
                
        except Exception as e:
            print(f"Error notifying user {user_id} for {cmd}: {e}")
    
    to_remove = []
    for user_id, cmds in list(cooldowns.items()):
        for cmd in list(cmds.keys()):
            if cmds[cmd]["expires_at"] <= now - 3600:
                cmds.pop(cmd)
        if not cmds:
            to_remove.append(user_id)
    
    for uid in to_remove:
        cooldowns.pop(uid, None)
    
    if expired_notifications or to_remove:
        save_cooldowns()

@bot.event
async def on_ready():
    print(f"🎌 {bot.user} is now online!")
    print(f"📊 Connected to {len(bot.guilds)} server(s)")
    load_cooldowns()
    if not check_expired_cooldowns.is_running():
        check_expired_cooldowns.start()
    
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"❌ Failed to sync slash commands: {e}")
    
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="Naruto Botto cooldowns | n help"
        )
    )

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    if QUIZ_DEBUG and (message.author.bot or message.embeds):
        author_bits = [
            f"id={getattr(message.author, 'id', None)}",
            f"name={getattr(message.author, 'name', None)!r}",
            f"display_name={getattr(message.author, 'display_name', None)!r}",
            f"global_name={getattr(message.author, 'global_name', None)!r}",
            f"nick={getattr(message.author, 'nick', None)!r}",
            f"bot={getattr(message.author, 'bot', None)}",
            f"is_naruto={is_naruto_botto_author(message.author)}",
            f"embeds={len(message.embeds)}",
            f"content={message.content[:120]!r}",
        ]
        print("[QUIZ] Message seen: " + " | ".join(author_bits), flush=True)

    # Quiz handling should not depend on the bot-name filter; some bot/app
    # messages do not present a stable author name even though they are valid quiz embeds.
    if ENABLE_GPT and message.embeds and message.author != bot.user:
        await maybe_answer_quiz(message)

    if message.author.bot and is_naruto_botto_author(message.author):
        full_text = message.content
        
        if message.embeds:
            for embed in message.embeds:
                if embed.description:
                    full_text += "\n" + embed.description
                if embed.title:
                    full_text += "\n" + embed.title
                for field in embed.fields:
                    full_text += f"\n{field.name}: {field.value}"
        
        print(f"🔍 Naruto Botto message detected: {full_text[:200]}")
        quiz_log("Naruto Botto message passed the author filter.")
        
        if pending_smart_tracks:
            time_secs = parse_time_string(full_text)
            
            if time_secs > 0:
                detected_cmd_from_message = None
                for name in cooldown_times.keys():
                    if name.lower() in full_text.lower():
                        detected_cmd_from_message = name
                        break
                
                for user_id, user_commands in list(pending_smart_tracks.items()):
                    cmd_to_process = None
                    
                    if detected_cmd_from_message and detected_cmd_from_message in user_commands:
                        cmd_to_process = detected_cmd_from_message
                    elif len(user_commands) == 1:
                        cmd_to_process = list(user_commands.keys())[0]
                    
                    if cmd_to_process and message.channel.id == user_commands[cmd_to_process]["channel_id"]:
                        track_info = user_commands[cmd_to_process]
                        channel_id = track_info["channel_id"]
                        track_event = track_info.get("event")
                        
                        print(f"📝 Processing {cmd_to_process} for user {user_id}")
                        print(f"⏰ Existing cooldown found: {time_secs}s for {cmd_to_process}")
                        
                        if track_event and not track_event.is_set():
                            track_event.set()

                        del pending_smart_tracks[user_id][cmd_to_process]
                        if not pending_smart_tracks[user_id]:
                            del pending_smart_tracks[user_id]
                        
                        detected_cmd = None
                        for name in cooldown_times.keys():
                            if name in full_text.lower():
                                detected_cmd = name
                                break
                        if not detected_cmd:
                            detected_cmd = cmd_to_process
                        
                        cooldowns.setdefault(user_id, {})[detected_cmd] = {
                            "expires_at": time.time() + time_secs,
                            "channel_id": channel_id,
                            "notified": False
                        }
                        save_cooldowns()
                        
                        emoji = cooldown_emojis.get(detected_cmd, "⏰")
                        time_str = format_time(time_secs)
                        
                        if should_show_progress_bar(detected_cmd):
                            embed = discord.Embed(
                                title=f"{emoji} {detected_cmd.upper()} Cooldown Detected!",
                                description=f"Naruto Botto already had this on cooldown—synced instantly!",
                                color=cooldown_colors.get(detected_cmd, discord.Color.blue())
                            )
                            
                            total_time = cooldown_times.get(detected_cmd, time_secs)
                            elapsed = max(0, total_time - time_secs)
                            progress = get_progress_bar(elapsed, total_time)
                            
                            embed.add_field(name="⏰ Time Remaining", value=f"**{time_str}**", inline=True)
                            embed.add_field(name="📊 Progress", value=progress, inline=False)
                            embed.add_field(name="✅ Status", value="Reminder armed. I'll ping you exactly on time!", inline=False)
                            embed.set_footer(text=f"Synced live from Naruto Botto ⚡")
                            
                            try:
                                channel = bot.get_channel(channel_id)
                                if channel:
                                    await channel.send(embed=embed)
                            except Exception as e:
                                print(f"Error sending existing cooldown: {e}")
                        else:
                            fun_msg = random.choice(entertaining_messages.get(detected_cmd, ["Tracked!"]))
                            message_text = (
                                f"{emoji} **{detected_cmd.upper()} synced!** {time_str} left on cooldown.\n"
                                f"🔔 Reminder is set—I'll ping you when it's game time.\n{fun_msg}"
                            )
                            
                            try:
                                channel = bot.get_channel(channel_id)
                                if channel:
                                    await channel.send(message_text)
                            except Exception as e:
                                print(f"Error sending existing cooldown: {e}")
                        
                        emoji_react = cooldown_emojis.get(detected_cmd, "⏰")
                        try:
                            await message.add_reaction(emoji_react)
                        except:
                            pass
                        
                        return
        
        if "cooldown" in full_text.lower() and message.mentions:
            time_secs = parse_time_string(full_text)
            if time_secs > 0:
                user = message.mentions[0]
                detected = None
                for name in cooldown_times.keys():
                    if name in full_text.lower():
                        detected = name
                        break
                if not detected:
                    detected = "mission"
                
                if user.id not in pending_smart_tracks or detected not in pending_smart_tracks.get(user.id, {}):
                    cooldowns.setdefault(user.id, {})[detected] = {
                        "expires_at": time.time() + time_secs,
                        "channel_id": message.channel.id,
                        "notified": False
                    }
                    save_cooldowns()
                    
                    emoji = cooldown_emojis.get(detected, "⏰")
                    try:
                        await message.add_reaction(emoji)
                    except:
                        pass
                    print(f"✅ Auto-tracked {detected} cooldown for {user.display_name}")
            return
        
        return
    
    await bot.process_commands(message)

@bot.command(name="m", aliases=["mission"])
async def track_mission(ctx):
    if QUIZ_DEBUG:
        print(f"[CMD] {ctx.author} ran mission in #{getattr(ctx.channel, 'name', 'unknown')}", flush=True)
    await track_cooldown_smart(ctx, "mission")

@bot.command(name="r", aliases=["report"])
async def track_report(ctx):
    if QUIZ_DEBUG:
        print(f"[CMD] {ctx.author} ran report in #{getattr(ctx.channel, 'name', 'unknown')}", flush=True)
    await track_cooldown_smart(ctx, "report")

@bot.command(name="to", aliases=["tower"])
async def track_tower(ctx):
    if QUIZ_DEBUG:
        print(f"[CMD] {ctx.author} ran tower in #{getattr(ctx.channel, 'name', 'unknown')}", flush=True)
    await track_cooldown_smart(ctx, "tower")

@bot.command(name="d", aliases=["daily"])
async def track_daily(ctx):
    if QUIZ_DEBUG:
        print(f"[CMD] {ctx.author} ran daily in #{getattr(ctx.channel, 'name', 'unknown')}", flush=True)
    await track_cooldown_smart(ctx, "daily")

@bot.command(name="w", aliases=["weekly"])
async def track_weekly(ctx):
    if QUIZ_DEBUG:
        print(f"[CMD] {ctx.author} ran weekly in #{getattr(ctx.channel, 'name', 'unknown')}", flush=True)
    await track_cooldown_smart(ctx, "weekly")

@bot.command(name="ch", aliases=["challenge"])
async def track_challenge(ctx):
    if QUIZ_DEBUG:
        print(f"[CMD] {ctx.author} ran challenge in #{getattr(ctx.channel, 'name', 'unknown')}", flush=True)
    await track_cooldown_smart(ctx, "challenge")

async def track_cooldown_smart(ctx, cmd):
    user_id = ctx.author.id
    emoji = cooldown_emojis.get(cmd, "⏰")
    
    remaining = get_remaining_time(user_id, cmd)
    if remaining > 0:
        time_str = format_time(remaining)
        
        if should_show_progress_bar(cmd):
            embed = discord.Embed(
                title=f"{emoji} {cmd.upper()} - Already on Cooldown",
                description=f"Your **{cmd}** is still cooling down.",
                color=discord.Color.orange()
            )
            
            total_time = cooldown_times[cmd]
            elapsed = total_time - remaining
            progress = get_progress_bar(elapsed, total_time)
            
            embed.add_field(name="⏰ Time Remaining", value=f"**{time_str}**", inline=True)
            embed.add_field(name="📊 Progress", value=progress, inline=False)
            embed.set_footer(text=f"Requested by {ctx.author.display_name}")
            
            await ctx.send(embed=embed)
        else:
            fun_msg = random.choice(entertaining_messages.get(cmd, ["Hang tight!"]))
            message = f"{emoji} **{cmd.upper()}** on cooldown! {time_str} remaining.\n{fun_msg}"
            await ctx.send(message)
        return
    
    if user_id not in pending_smart_tracks:
        pending_smart_tracks[user_id] = {}

    track_event = asyncio.Event()
    pending_smart_tracks[user_id][cmd] = {
        "channel_id": ctx.channel.id,
        "timestamp": time.time(),
        "event": track_event
    }

    print(f"⏳ Waiting for Naruto Botto response for {cmd} (user: {user_id})")

    try:
        await ctx.message.add_reaction("👀")
    except Exception:
        pass

    try:
        await asyncio.wait_for(track_event.wait(), timeout=SMART_TRACK_WAIT_SECONDS)
    except asyncio.TimeoutError:
        pass
    
    if user_id in pending_smart_tracks and cmd in pending_smart_tracks[user_id]:
        print(f"⏰ No Naruto Botto response detected, starting fresh timer for {cmd}")
        
        del pending_smart_tracks[user_id][cmd]
        if not pending_smart_tracks[user_id]:
            del pending_smart_tracks[user_id]
        
        cooldowns.setdefault(user_id, {})[cmd] = {
            "expires_at": time.time() + cooldown_times[cmd],
            "channel_id": ctx.channel.id,
            "notified": False
        }
        save_cooldowns()
        
        duration = format_time(cooldown_times[cmd])
        
        if should_show_progress_bar(cmd):
            embed = discord.Embed(
                title=f"{emoji} {cmd.upper()} Cooldown Started!",
                description="Timer is now locked and loaded.",
                color=cooldown_colors.get(cmd, discord.Color.green())
            )

            embed.add_field(name="⏰ Next Run", value=f"**{duration}**", inline=True)
            embed.add_field(name="🔔 Reminder", value=f"I'll tag {ctx.author.mention} right on time!", inline=True)
            embed.add_field(name="📊 Progress", value=get_progress_bar(0, cooldown_times[cmd]), inline=False)
            embed.set_footer(text=random.choice(tracking_started_messages.get(cmd, ["Dattebayo! 🍜"])))

            await ctx.send(embed=embed)
        else:
            await ctx.send(get_friendly_tracking_message(cmd, duration))
    else:
        print(f"✅ Naruto Botto responded! Message already sent by on_message handler")

@bot.command(name="dashboard", aliases=["db", "status"])
async def dashboard(ctx, member: discord.Member = None):
    if member is None:
        member = ctx.author
    
    user_cooldowns = cooldowns.get(member.id, {})
    
    if not user_cooldowns:
        embed = discord.Embed(
            title=f"🎌 {member.display_name}'s Dashboard",
            description="✅ **All systems ready!** No active cooldowns!",
            color=discord.Color.green()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(
            name="💪 Status", 
            value="You're ready for action! Time to jump back into the game!", 
            inline=False
        )
        embed.set_footer(text="Use 'n help' to see all available commands")
        await ctx.send(embed=embed)
        return
    
    embed = discord.Embed(
        title=f"🎌 {member.display_name}'s Dashboard",
        description=f"📊 **Active Cooldowns Overview**",
        color=discord.Color.blue()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    
    now = time.time()
    active_cooldowns = []
    ready_activities = []
    next_ready_time = float('inf')
    next_ready_cmd = None
    
    for cmd, data in user_cooldowns.items():
        remaining = data["expires_at"] - now
        emoji = cooldown_emojis.get(cmd, "⏰")
        
        if remaining > 0:
            time_str = format_time(remaining)
            total_time = cooldown_times.get(cmd, 3600)
            elapsed = total_time - remaining
            progress = get_progress_bar(elapsed, total_time)
            
            active_cooldowns.append({
                "cmd": cmd,
                "emoji": emoji,
                "time": time_str,
                "progress": progress,
                "remaining_secs": remaining
            })
            
            if remaining < next_ready_time:
                next_ready_time = remaining
                next_ready_cmd = cmd
        else:
            ready_activities.append(f"{emoji} **{cmd.upper()}**")
    
    active_cooldowns.sort(key=lambda x: x["remaining_secs"])
    
    if active_cooldowns:
        for cd in active_cooldowns:
            if should_show_progress_bar(cd['cmd']):
                embed.add_field(
                    name=f"{cd['emoji']} {cd['cmd'].upper()}",
                    value=f"⏰ `{cd['time']}`\n{cd['progress']}",
                    inline=True
                )
            else:
                embed.add_field(
                    name=f"{cd['emoji']} {cd['cmd'].upper()}",
                    value=f"⏰ `{cd['time']}`",
                    inline=True
                )
    
    if ready_activities:
        embed.add_field(
            name="✅ Ready Now",
            value="\n".join(ready_activities),
            inline=False
        )
    
    if next_ready_cmd:
        embed.add_field(
            name="⏭️ Next Ready",
            value=f"{cooldown_emojis.get(next_ready_cmd, '⏰')} **{next_ready_cmd.upper()}** in `{format_time(next_ready_time)}`",
            inline=False
        )
    
    total_active = len(active_cooldowns)
    total_ready = len(ready_activities)
    embed.set_footer(text=f"Active: {total_active} | Ready: {total_ready} | Total tracked: {total_active + total_ready}")
    
    await ctx.send(embed=embed)

@bot.group(name="cooldown", aliases=["cd", "cooldowns"])
async def cooldown_group(ctx):
    if ctx.invoked_subcommand is None:
        embed = discord.Embed(
            title="📋 Cooldown Management",
            description="Manage and view cooldowns",
            color=discord.Color.blue()
        )
        embed.add_field(
            name="Available Commands",
            value="```\nn cd list              - Show all server cooldowns\nn cd user @member      - Check user's cooldowns\nn cd clear @member     - Clear all user cooldowns\nn cd db                - Inspect the SQLite database```",
            inline=False
        )
        await ctx.send(embed=embed)

@cooldown_group.command(name="list", aliases=["all", "show"])
@commands.has_permissions(manage_guild=True)
async def list_cooldowns(ctx):
    if not cooldowns:
        await ctx.send("📋 No active cooldowns right now! Everyone's ready to go!")
        return
    
    embed = discord.Embed(
        title="🎌 Active Cooldowns - Server Overview",
        description="All tracked cooldowns across the server",
        color=discord.Color.orange()
    )
    
    now = time.time()
    total_cooldowns = 0
    
    for user_id, cmds in cooldowns.items():
        try:
            user = await bot.fetch_user(user_id)
            user_cooldowns = []
            for cmd, data in cmds.items():
                remaining = data["expires_at"] - now
                if remaining > 0:
                    emoji = cooldown_emojis.get(cmd, "⏰")
                    time_str = format_time(remaining)
                    user_cooldowns.append(f"{emoji} **{cmd}**: {time_str}")
                    total_cooldowns += 1
            
            if user_cooldowns:
                embed.add_field(
                    name=f"👤 {user.display_name}",
                    value="\n".join(user_cooldowns),
                    inline=False
                )
        except:
            pass
    
    embed.set_footer(text=f"Total: {total_cooldowns} active cooldowns")
    await ctx.send(embed=embed)

@cooldown_group.command(name="user", aliases=["check", "u"])
async def check_user(ctx, member: discord.Member = None):
    if member is None:
        member = ctx.author
    
    user_cooldowns = cooldowns.get(member.id, {})
    
    if not user_cooldowns:
        await ctx.send(f"✅ {member.mention} has no active cooldowns! Ready for action!")
        return
    
    embed = discord.Embed(
        title=f"🎌 {member.display_name}'s Cooldowns",
        color=discord.Color.blue()
    )
    
    now = time.time()
    active = []
    ready = []
    
    for cmd, data in user_cooldowns.items():
        remaining = data["expires_at"] - now
        emoji = cooldown_emojis.get(cmd, "⏰")
        
        if remaining > 0:
            time_str = format_time(remaining)
            active.append(f"{emoji} **{cmd.upper()}**: {time_str}")
        else:
            ready.append(f"✅ **{cmd.upper()}**: Ready!")
    
    if active:
        embed.add_field(name="⏳ Cooling Down", value="\n".join(active), inline=False)
    if ready:
        embed.add_field(name="✅ Ready", value="\n".join(ready), inline=False)
    
    await ctx.send(embed=embed)

@cooldown_group.command(name="clear", aliases=["reset", "remove"])
@commands.has_permissions(manage_guild=True)
async def clear_cooldown(ctx, member: discord.Member, activity: str = None):
    if member.id not in cooldowns:
        await ctx.send(f"❌ {member.mention} has no active cooldowns!")
        return
    
    if activity:
        activity = aliases.get(activity.lower(), activity.lower())
        if activity in cooldowns[member.id]:
            cooldowns[member.id].pop(activity)
            if not cooldowns[member.id]:
                cooldowns.pop(member.id)
            save_cooldowns()
            emoji = cooldown_emojis.get(activity, "✅")
            await ctx.send(f"{emoji} Cleared **{activity}** cooldown for {member.mention}!")
        else:
            await ctx.send(f"❌ {member.mention} doesn't have an active **{activity}** cooldown!")
    else:
        cooldowns.pop(member.id)
        save_cooldowns()
        await ctx.send(f"✅ Cleared all cooldowns for {member.mention}!")

@cooldown_group.command(name="db", aliases=["inspect", "sqlite", "raw"])
@commands.has_permissions(manage_guild=True)
async def inspect_cooldown_db(ctx):
    init_database()
    now = time.time()

    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT user_id, command, expires_at, channel_id, notified
                FROM cooldowns
                ORDER BY expires_at ASC
                """
            ).fetchall()
    except Exception as e:
        await ctx.send(f"❌ Failed to inspect SQLite database: {e}")
        return

    if not rows:
        await ctx.send("📭 SQLite database is empty. No cooldowns are stored right now.")
        return

    active_count = sum(1 for row in rows if float(row["expires_at"]) > now)
    expired_count = len(rows) - active_count

    embed = discord.Embed(
        title="🗄️ Cooldown Database Snapshot",
        description="Live view of the SQLite cooldown store.",
        color=discord.Color.teal(),
    )
    embed.add_field(name="Total Rows", value=str(len(rows)), inline=True)
    embed.add_field(name="Active", value=str(active_count), inline=True)
    embed.add_field(name="Expired", value=str(expired_count), inline=True)

    lines = []
    for row in rows[:10]:
        remaining = float(row["expires_at"]) - now
        status = "active" if remaining > 0 else "expired"
        lines.append(
            f"<@{row['user_id']}> | `{row['command']}` | {status} | `{format_time(max(0, remaining))}`"
        )

    embed.add_field(
        name="First Rows",
        value="\n".join(lines),
        inline=False,
    )
    embed.set_footer(text="Use n cd list or n cd user for friendlier views")
    await ctx.send(embed=embed)

@bot.command(name="help", aliases=["commands", "h"])
async def help_command(ctx):
    embed = discord.Embed(
        title="🎌 Naruto Botto Companion - Commands",
        description="Your personal cooldown tracker with smart detection!",
        color=discord.Color.gold()
    )
    
    embed.add_field(
        name="⚔️ Track Cooldowns (Smart Detection Enabled!)",
        value="```\nn m / n mission   - Mission (1min)\nn r / n report    - Report (10min)\nn to / n tower    - Tower (6hrs)\nn d / n daily     - Daily (24hrs)\nn w / n weekly    - Weekly (7days)\nn ch / n challenge - Challenge (30min)```",
        inline=False
    )
    
    embed.add_field(
        name="📊 View Status",
        value="```\nn dashboard / n db    - Visual dashboard\nn cd user             - Check your cooldowns\nn cd user @member     - Check someone's cooldowns```",
        inline=False
    )
    
    embed.add_field(
        name="🛡️ Admin Commands (Manage Server permission required)",
        value="```\nn cd list              - Show all active cooldowns\nn cd db                - Inspect the SQLite database\nn cd clear @member     - Clear all user cooldowns\nn cd clear @member cmd - Clear specific cooldown```",
        inline=False
    )
    
    embed.add_field(
        name="✨ Smart Detection Features",
        value="• **Instant Check**: Detects existing Naruto Botto cooldowns in 2 seconds\n• **Auto-tracking**: Monitors all Naruto Botto messages\n• **Smart Timer**: Only starts new timer if no existing cooldown found\n• **Server Pings**: Get notified when cooldowns expire",
        inline=False
    )
    
    embed.add_field(
        name="⚡ Slash Commands",
        value="```\n/ping - Check bot status and latency```",
        inline=False
    )
    
    embed.set_footer(text="Made with ❤️ for Naruto Botto players")
    await ctx.send(embed=embed)

@bot.tree.command(name="ping", description="Check if the bot is responsive")
async def ping_slash(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    
    embed = discord.Embed(
        title="🏓 Pong!",
        description=f"Bot is online and responsive!",
        color=discord.Color.green()
    )
    
    embed.add_field(name="⚡ Latency", value=f"`{latency}ms`", inline=True)
    embed.add_field(name="📊 Status", value="`Online ✅`", inline=True)
    embed.add_field(
        name="🎌 Server",
        value=f"`{interaction.guild.name if interaction.guild else 'DM'}`",
        inline=False
    )
    
    embed.set_footer(text=f"Requested by {interaction.user.display_name}")
    
    await interaction.response.send_message(embed=embed)

def _normalize_quiz_text(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text.lower())).strip()

def quiz_log(message: str):
    if QUIZ_DEBUG:
        print(f"[QUIZ] {message}", flush=True)

def _extract_quiz_payload(message):
    chunks = []
    options = []
    question_bits = []
    title_bits = []

    option_label_re = re.compile(r"^(?:\d+|[1-3]️⃣|:one:|:two:|:three:|[A-C])$", re.IGNORECASE)
    question_line_re = re.compile(r"^(?:who|what|which|when|where|why|how)\b.*\??$", re.IGNORECASE)
    numbered_line_re = re.compile(
        r"^(?:[•\-*]|\d+[\.\):]|[A-C][\.\):]|[1-3]️⃣|:one:|:two:|:three:)\s*(.+)$",
        re.IGNORECASE,
    )
    noise_line_re = re.compile(
        r"^(?:you earned|correct answer|answer|result|rewards?|mission|rank|cooldown|time left|next run|dattebayo|congratul|completed?)",
        re.IGNORECASE,
    )

    def add_option(option_text: str):
        option = re.sub(r"\s+", " ", option_text).strip(" `*_")
        if option and option not in options and not question_line_re.match(option):
            options.append(option)

    def add_question_text(text: str):
        cleaned = re.sub(r"\s+", " ", text).strip()
        if cleaned and not noise_line_re.match(cleaned):
            question_bits.append(cleaned)

    def is_question_like(text: str) -> bool:
        cleaned = re.sub(r"\s+", " ", text).strip()
        return bool(cleaned) and (
            cleaned.endswith("?")
            or question_line_re.match(cleaned)
            or cleaned.lower().startswith(("who ", "what ", "which ", "when ", "where ", "why ", "how "))
        )

    if message.content:
        chunks.append(message.content)
        for line in message.content.splitlines():
            line = line.strip()
            if not line:
                continue
            match = numbered_line_re.match(line)
            if match:
                add_option(match.group(1))
            else:
                add_question_text(line)

    for embed in message.embeds:
        if embed.title:
            title_bits.append(embed.title.strip())
        if embed.description:
            chunks.append(embed.description)
            for line in embed.description.splitlines():
                line = line.strip()
                if not line:
                    continue
                match = numbered_line_re.match(line)
                if match:
                    add_option(match.group(1))
                    if QUIZ_DEBUG:
                        quiz_log(f"Matched description option line: {line!r} -> {match.group(1)!r}")
                else:
                    if is_question_like(line):
                        add_question_text(line)
                    elif not noise_line_re.match(line):
                        add_question_text(line)
        for field in embed.fields:
            field_name = (field.name or "").strip()
            field_value = (field.value or "").strip()

            if QUIZ_DEBUG:
                quiz_log(f"Field seen name={field_name!r} value={field_value[:120]!r}")

            if field_name and option_label_re.match(field_name):
                if field_value:
                    add_option(field_value)
                    if QUIZ_DEBUG:
                        quiz_log(f"Field treated as option label {field_name!r} -> option {field_value!r}")
                continue

            if field_value:
                match = numbered_line_re.match(field_value)
                if match:
                    add_option(match.group(1))
                    if QUIZ_DEBUG:
                        quiz_log(f"Matched field option value: {field_value!r} -> {match.group(1)!r}")
                else:
                    if is_question_like(field_value):
                        add_question_text(field_value)
                    elif not noise_line_re.match(field_value):
                        add_question_text(field_value)

            if field_name and not option_label_re.match(field_name):
                if is_question_like(field_name):
                    add_question_text(field_name)
                elif not noise_line_re.match(field_name):
                    add_question_text(field_name)

    full_text = "\n".join(chunks + title_bits + question_bits).strip()
    question_text = " ".join(question_bits).strip()

    quiz_log(
        "Extracted payload "
        f"title={title_bits[:1]!r} "
        f"question={question_text[:240]!r} "
        f"options={options!r}"
    )
    return question_text or full_text, options

def _pick_local_quiz_answer(question_text: str, options):
    if not options:
        return None

    question_tokens = set(_normalize_quiz_text(question_text).split())
    if not question_tokens:
        return options[0]

    best_option = options[0]
    best_score = -1

    for option in options:
        option_tokens = set(_normalize_quiz_text(option).split())
        score = len(question_tokens & option_tokens)
        if score > best_score:
            best_score = score
            best_option = option

    return best_option

def _parse_gemini_quiz_response(raw_text: str, options):
    try:
        data = json.loads(raw_text)
    except Exception:
        return None

    answer_text = str(data.get("answer_text", "")).strip()
    answer_index = data.get("answer_index")

    if answer_text:
        for option in options:
            if _normalize_quiz_text(option) == _normalize_quiz_text(answer_text):
                return option

    try:
        answer_index = int(answer_index)
    except Exception:
        answer_index = None

    if answer_index is not None and 1 <= answer_index <= len(options):
        return options[answer_index - 1]

    return None

async def ask_gpt(question_text, options=None):
    if not options:
        quiz_log("Skipping Gemini call because no options were detected.")
        return None

    if gemini_client and ENABLE_GPT:
        try:
            quiz_log(
                f"Sending question to Gemini. question={question_text[:240]!r} options={options!r}"
            )
            prompt = (
                "Pick the correct option for this Naruto Botto quiz.\n"
                "Return JSON only with answer_index and answer_text.\n"
                "Rules:\n"
                "- answer_index must be the 1-based index of one option from the list.\n"
                "- answer_text must exactly match the chosen option text.\n"
                "- The answer must be based on Naruto knowledge, not text similarity.\n"
                "- If the question is asking for a specific Naruto fact, choose the factual option.\n"
                "- Do not add explanations.\n\n"
                f"Question:\n{question_text}\n\n"
                "Options:\n"
                + "\n".join(f"{idx + 1}. {option}" for idx, option in enumerate(options))
            )

            response = await asyncio.to_thread(
                lambda: gemini_client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=(
                            "You answer multiple-choice quiz questions. "
                            "Use only the provided options and return JSON only."
                        ),
                        response_mime_type="application/json",
                        response_schema={
                            "type": "object",
                            "properties": {
                                "answer_index": {
                                    "type": "integer",
                                    "description": "1-based index of the chosen option.",
                                },
                                "answer_text": {
                                    "type": "string",
                                    "description": "Exact text of the chosen option.",
                                },
                            },
                            "required": ["answer_index", "answer_text"],
                            "additionalProperties": False,
                        },
                        temperature=0,
                        max_output_tokens=64,
                    ),
                )
            )

            quiz_log(f"Gemini raw response: {response.text[:240]!r}")
            parsed = _parse_gemini_quiz_response(response.text, options)
            if parsed:
                quiz_log(f"Gemini parsed answer: {parsed!r}")
                return options.index(parsed) + 1
            quiz_log("Gemini response could not be mapped to one of the supplied options.")
        except Exception as e:
            print(f"❌ Gemini quiz helper failed: {e}")

    if QUIZ_ALLOW_LOCAL_FALLBACK:
        quiz_log("Using local fallback because QUIZ_ALLOW_LOCAL_FALLBACK=true.")
        local_answer = _pick_local_quiz_answer(question_text, options)
        if local_answer and local_answer in options:
            quiz_log(f"Local fallback selected: {local_answer!r}")
            return options.index(local_answer) + 1
        quiz_log("Local fallback could not find a confident answer.")
    else:
        quiz_log("No fallback allowed; skipping answer.")
    return None

async def maybe_answer_quiz(message):
    if not ENABLE_GPT:
        return

    full_text, options = _extract_quiz_payload(message)
    has_question = bool(full_text) and any(
        token in full_text.lower()
        for token in ("who", "what", "which", "when", "where", "why", "how", "?")
    )
    quiz_log(
        f"Detection check has_question={has_question} options={len(options)} text={full_text[:240]!r}"
    )

    if not has_question or len(options) < 2:
        quiz_log(
            "Skipping because the message does not look like a quiz prompt or options were missing."
        )
        return

    answer = await ask_gpt(full_text, options)
    if answer:
        quiz_log(f"Sending answer number: {answer}")
        await message.channel.send(str(answer))
    else:
        quiz_log("No answer sent.")

keep_alive()
bot.run(DISCORD_TOKEN)
