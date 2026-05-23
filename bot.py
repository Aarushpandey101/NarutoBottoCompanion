from keep_alive import keep_alive
import asyncio
import datetime
import hashlib
import json
import math
import os
import random
import re
import sqlite3
import time
from typing import Dict, Optional
from urllib import error as urllib_error
from urllib import request as urllib_request

import discord
from discord import app_commands
from discord.ext import commands, tasks
from google import genai
from google.genai import types
from pydantic import BaseModel

try:
    from groq import AsyncGroq
except Exception:
    AsyncGroq = None

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
ENABLE_GPT = os.getenv("ENABLE_GPT", "false").lower() == "true"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
SMART_TRACK_WAIT_SECONDS = float(os.getenv("SMART_TRACK_WAIT_SECONDS", "3.5"))
QUIZ_ALLOW_LOCAL_FALLBACK = os.getenv("QUIZ_ALLOW_LOCAL_FALLBACK", "false").lower() == "true"
QUIZ_DEBUG = os.getenv("QUIZ_DEBUG", "false").lower() == "true"
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
NARUTO_BOTTO_USER_ID = None
LAST_QUIZ_TEMP_VIEWS = {}

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

groq_client = None
if GROQ_API_KEY and AsyncGroq is not None:
    try:
        groq_client = AsyncGroq(api_key=GROQ_API_KEY)
    except Exception as e:
        print(f"Failed to initialize Groq client: {e}")

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

# MODIFIED LINE BELOW: Added list for prefix and case_insensitive=True
bot = commands.Bot(command_prefix=["n ", "N "], case_insensitive=True, intents=intents, help_command=None)

DB_PATH = "cooldowns.sqlite3"
LEGACY_JSON_PATH = "cooldowns.json"
CF_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
CF_D1_DATABASE_ID = os.getenv("CLOUDFLARE_D1_DATABASE_ID", "").strip()
CF_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
USE_CLOUDFLARE_D1 = bool(CF_ACCOUNT_ID and CF_D1_DATABASE_ID and CF_API_TOKEN)
_LOCAL_SQLITE_CONNECT = sqlite3.connect


class _QueryResultCursor:
    def __init__(self, rows=None, rowcount=-1, lastrowid=None):
        self._rows = list(rows or [])
        self.rowcount = rowcount
        self.lastrowid = lastrowid

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


def _d1_api_request(payload):
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/d1/database/{CF_D1_DATABASE_ID}/query"
    request = urllib_request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {CF_API_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib_request.urlopen(request, timeout=30) as response:
        body = response.read().decode("utf-8")

    data = json.loads(body)
    if not data.get("success", False):
        errors = data.get("errors") or data.get("messages") or []
        raise RuntimeError(f"Cloudflare D1 query failed: {errors or body}")
    return data.get("result") or []


def _normalize_d1_params(params):
    return ["" if value is None else str(value) for value in list(params or [])]


class _D1Connection:
    def __init__(self):
        self.row_factory = None
        self._pending_writes = []
        self._in_context = False

    def __enter__(self):
        self._in_context = True
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None and self._pending_writes:
                payload = {
                    "batch": [
                        {"sql": sql, "params": params}
                        for sql, params in self._pending_writes
                    ]
                }
                _d1_api_request(payload)
        finally:
            self._pending_writes = []
            self._in_context = False
        return False

    def _is_read_query(self, sql: str) -> bool:
        statement = (sql or "").strip().lower()
        if not statement:
            return True
        return statement.startswith(("select", "pragma", "with", "explain"))

    def execute(self, sql, params=()):
        if self._is_read_query(sql):
            results = _d1_api_request({"sql": sql, "params": _normalize_d1_params(params)})
            first = results[0] if results else {}
            rows = first.get("results") or []
            return _QueryResultCursor(rows=rows)

        if self._in_context:
            self._pending_writes.append((sql, _normalize_d1_params(params)))
            return _QueryResultCursor(rows=[])

        results = _d1_api_request({"sql": sql, "params": _normalize_d1_params(params)})
        first = results[0] if results else {}
        return _QueryResultCursor(
            rows=first.get("results") or [],
            rowcount=int((first.get("meta") or {}).get("changes") or 0),
            lastrowid=(first.get("meta") or {}).get("last_row_id"),
        )

    def executemany(self, sql, seq_of_params):
        if self._in_context:
            for params in seq_of_params:
                self._pending_writes.append((sql, _normalize_d1_params(params)))
            return _QueryResultCursor(rows=[])

        payload = {"batch": [{"sql": sql, "params": _normalize_d1_params(params)} for params in seq_of_params]}
        results = _d1_api_request(payload)
        last = results[-1] if results else {}
        return _QueryResultCursor(
            rows=(last.get("results") or []),
            rowcount=int(sum(int((item.get("meta") or {}).get("changes") or 0) for item in results)),
            lastrowid=(last.get("meta") or {}).get("last_row_id"),
        )

    def commit(self):
        if self._pending_writes:
            payload = {
                "batch": [
                    {"sql": sql, "params": params}
                    for sql, params in self._pending_writes
                ]
            }
            _d1_api_request(payload)
            self._pending_writes = []

    def rollback(self):
        self._pending_writes = []

    def close(self):
        self._pending_writes = []


def _connect_database(*args, **kwargs):
    if USE_CLOUDFLARE_D1:
        return _D1Connection()
    return _LOCAL_SQLITE_CONNECT(*args, **kwargs)


sqlite3.connect = _connect_database

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
challenge_confirmation_states = {}
CHALLENGE_PENDING_TTL_SECONDS = 120


def _cleanup_stale_challenge_state():
    now = time.time()
    stale_users = []

    for user_id, state in list(challenge_confirmation_states.items()):
        if now - float(state.get("timestamp", 0)) > CHALLENGE_PENDING_TTL_SECONDS:
            stale_users.append(user_id)

    for user_id in stale_users:
        challenge_confirmation_states.pop(user_id, None)
        if user_id in pending_smart_tracks and "challenge" in pending_smart_tracks[user_id]:
            pending_smart_tracks[user_id].pop("challenge", None)
            if not pending_smart_tracks[user_id]:
                pending_smart_tracks.pop(user_id, None)

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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS quiz_cache (
                question_key TEXT PRIMARY KEY,
                question_text TEXT NOT NULL,
                options_text TEXT NOT NULL,
                answer_index INTEGER NOT NULL,
                answer_text TEXT NOT NULL,
                provider TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS quiz_review_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question_key TEXT NOT NULL,
                question_text TEXT NOT NULL,
                options_text TEXT NOT NULL,
                answer_index INTEGER NOT NULL,
                answer_text TEXT NOT NULL,
                provider TEXT,
                seen_count INTEGER NOT NULL DEFAULT 1,
                first_seen_at REAL NOT NULL,
                last_seen_at REAL NOT NULL,
                UNIQUE(question_key, provider, answer_index)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_quiz_review_candidates_last_seen ON quiz_review_candidates(last_seen_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_quiz_review_candidates_question_key ON quiz_review_candidates(question_key)"
        )
    _migrate_quiz_cache_keys_to_question_only()

def _migrate_quiz_cache_keys_to_question_only():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT question_key, question_text, options_text, answer_index, answer_text, provider, created_at, updated_at
            FROM quiz_cache
            ORDER BY updated_at DESC, created_at DESC
            """
        ).fetchall()

        if not rows:
            return

        migrated = {}
        for row in rows:
            new_key = hashlib.sha256(_normalize_quiz_text(row["question_text"]).encode("utf-8")).hexdigest()
            if new_key in migrated:
                continue
            migrated[new_key] = row

        if len(migrated) == len(rows) and all(row["question_key"] == hashlib.sha256(_normalize_quiz_text(row["question_text"]).encode("utf-8")).hexdigest() for row in rows):
            return

        conn.execute("DELETE FROM quiz_cache")
        for new_key, row in migrated.items():
            conn.execute(
                """
                INSERT INTO quiz_cache (
                    question_key, question_text, options_text, answer_index, answer_text,
                    provider, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_key,
                    row["question_text"],
                    row["options_text"],
                    row["answer_index"],
                    row["answer_text"],
                    row["provider"],
                    row["created_at"],
                    row["updated_at"],
                ),
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

    _cleanup_stale_challenge_state()

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

    if cmd == "challenge":
        challenge_confirmation_states[user_id] = {
            "channel_id": ctx.channel.id,
            "timestamp": time.time(),
        }

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
        if cmd == "challenge":
            challenge_confirmation_states[user_id] = {
                "channel_id": ctx.channel.id,
                "timestamp": time.time(),
                "waiting": True,
            }
            print("⏰ Challenge prompt seen; waiting for a confirmed challenge cooldown message instead")
            await ctx.send(
                "🥊 Challenge prompt detected. I’ll only sync this if Naruto Botto sends the actual accepted challenge cooldown message."
            )
            return

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

@bot.group(name="quiz", aliases=["qc", "quizcache"])
async def quiz_group(ctx):
    if ctx.invoked_subcommand is None:
        embed = discord.Embed(
            title="🧠 Quiz Cache Review",
            description="Review temporary quiz answers before saving them permanently.",
            color=discord.Color.purple(),
        )
        embed.add_field(
            name="n quiz temp [limit]",
            value="Show recent temporary quiz questions and candidate answers.",
            inline=False,
        )
        embed.add_field(
            name="n quiz confirm <ref>[,ref...]",
            value="Save one or more candidates to permanent cache. You can use temp list numbers, candidate IDs, or key prefixes.",
            inline=False,
        )
        embed.add_field(
            name="n quiz delete <ref>[,ref...] [question|all]",
            value="Delete one or more candidate rows, or purge the whole question from temp cache.",
            inline=False,
        )
        embed.set_footer(text="Manage Server permission required for quiz cache commands")
        await ctx.send(embed=embed)

@quiz_group.command(name="temp", aliases=["review", "queue", "list"])
@commands.has_permissions(manage_guild=True)
async def quiz_temp(ctx, limit: int = 5):
    limit = max(1, min(int(limit or 5), 10))
    groups = _quiz_get_review_candidates(limit)

    if not groups:
        await ctx.send("📭 No temporary quiz candidates right now.")
        return

    embed = discord.Embed(
        title="🧠 Temporary Quiz Cache",
        description="Recent questions waiting for manual review.",
        color=discord.Color.orange(),
    )

    for idx, group in enumerate(groups, start=1):
        candidates = _quiz_get_review_candidates_for_key(group["question_key"])
        question = _truncate_text(group["question_text"], 110)
        lines = [
            f"Key: `{group['question_key'][:10]}`",
            f"Candidates: {int(group['candidate_count'])}",
        ]
        for cand in candidates[:5]:
            answer_text = _truncate_text(cand["answer_text"], 50)
            provider = cand["provider"] or "unknown"
            lines.append(
                f"`{cand['id']}` {provider} -> {cand['answer_index']} {answer_text} (x{cand['seen_count']})"
            )
        if len(candidates) > 5:
            lines.append(f"... and {len(candidates) - 5} more")

        value = "\n".join(lines)
        if len(value) > 1000:
            value = value[:997] + "..."
        embed.add_field(
            name=f"{idx}. {question}",
            value=value,
            inline=False,
        )

    _quiz_store_temp_view(ctx, groups)
    embed.set_footer(text="Use n quiz confirm 1,2,3 to save by list number, or a candidate id/key prefix")
    await ctx.send(embed=embed)

@quiz_group.group(name="perm", aliases=["permanent", "saved"])
async def quiz_perm_group(ctx):
    if ctx.invoked_subcommand is None:
        await quiz_perm_list(ctx, 5)

@quiz_perm_group.command(name="list", aliases=["show", "recent"])
@commands.has_permissions(manage_guild=True)
async def quiz_perm_list(ctx, limit: int = 5):
    await quiz_perm_browse(ctx, page=1, limit=limit)

@quiz_perm_group.command(name="view", aliases=["get"])
@commands.has_permissions(manage_guild=True)
async def quiz_perm_view(ctx, question_ref: str = None):
    if not question_ref or not str(question_ref).strip():
        await ctx.send("❌ Usage: `n quiz perm view <ref>`")
        return
    entry, error = _quiz_resolve_permanent_reference(question_ref)
    if error == "ambiguous":
        await ctx.send(
            f"❌ Ref `{question_ref}` matches multiple permanent entries. Use a longer key prefix or more specific question text."
        )
        return
    if not entry:
        await ctx.send(f"❌ No permanent quiz cache entry found for `{question_ref}`.")
        return

    options = str(entry["options_text"] or "").split("\n") if entry["options_text"] else []
    lines = [
        f"Key: `{entry['question_key']}`",
        f"Question: {entry['question_text']}",
        f"Answer index: `{entry['answer_index']}`",
        f"Answer text: `{entry['answer_text']}`",
        f"Provider: `{entry['provider'] or 'unknown'}`",
        f"Updated: `{datetime.datetime.fromtimestamp(float(entry['updated_at'])).strftime('%Y-%m-%d %H:%M')}`",
    ]
    if options:
        lines.append("Options:")
        lines.extend(f"{i}. {opt}" for i, opt in enumerate(options, start=1))

    embed = discord.Embed(
        title="📚 Permanent Quiz Cache Entry",
        description="\n".join(lines),
        color=discord.Color.green(),
    )
    await ctx.send(embed=embed)

@quiz_perm_group.command(name="page", aliases=["pages", "browse"])
@commands.has_permissions(manage_guild=True)
async def quiz_perm_page(ctx, page: int = 1, limit: int = 5):
    await quiz_perm_browse(ctx, page=page, limit=limit)


async def quiz_perm_browse(ctx, page: int = 1, limit: int = 5):
    embed, rows, total_pages, page = _quiz_build_permanent_page_embed(page, limit)
    if not rows:
        await ctx.send(embed=embed)
        return

    view = QuizPermBrowseView(author_id=ctx.author.id, page=page, limit=limit)
    view.prev_button.disabled = page <= 1
    view.first_button.disabled = page <= 1
    view.next_button.disabled = page >= total_pages
    view.last_button.disabled = page >= total_pages
    await ctx.send(embed=embed, view=view)

@quiz_perm_group.command(name="edit", aliases=["set", "update"])
@commands.has_permissions(manage_guild=True)
async def quiz_perm_edit(ctx, question_ref: str, answer_index: int, *, answer_text: str = None):
    entry, error = _quiz_resolve_permanent_reference(question_ref)
    if error == "ambiguous":
        await ctx.send(
            f"❌ Ref `{question_ref}` matches multiple permanent entries. Use a longer key prefix or more specific question text."
        )
        return
    if not entry:
        await ctx.send(f"❌ No permanent quiz cache entry found for `{question_ref}`.")
        return

    options = str(entry["options_text"] or "").split("\n") if entry["options_text"] else []
    if answer_text is None or not str(answer_text).strip():
        if 1 <= int(answer_index) <= len(options):
            answer_text = options[int(answer_index) - 1]
        else:
            answer_text = str(entry["answer_text"] or "").strip()

    if not answer_text:
        await ctx.send("❌ Please provide an `answer_text`, or use a valid `answer_index` that matches the stored options.")
        return

    _quiz_update_permanent_entry(entry["question_key"], int(answer_index), str(answer_text).strip(), provider="manual")
    quiz_log(
        f"Manually edited permanent quiz entry question_key={entry['question_key'][:10]} answer={int(answer_index)}"
    )
    await ctx.send(
        f"✅ Updated permanent cache entry `{entry['question_key'][:10]}` to **{int(answer_index)}** - `{str(answer_text).strip()}`."
    )

@quiz_group.command(name="confirm", aliases=["save"])
@commands.has_permissions(manage_guild=True)
async def quiz_confirm(ctx, *, candidate_refs: str):
    candidates, error = _quiz_resolve_review_candidates(candidate_refs, ctx=ctx)
    if error == "empty":
        await ctx.send("❌ Provide at least one temporary quiz ref.")
        return
    if not candidates:
        await ctx.send(f"❌ No temporary quiz candidate found for `{candidate_refs}`.")
        return

    saved = []
    for candidate in candidates:
        options = candidate["options_text"].split("\n")
        _quiz_store_permanent(
            candidate["question_key"],
            candidate["question_text"],
            options,
            int(candidate["answer_index"]),
            candidate["answer_text"],
            candidate["provider"] or "manual",
        )
        _quiz_delete_review_candidates_for_key(candidate["question_key"])
        quiz_log(
            f"Manually confirmed quiz candidate id={candidate['id']} question_key={candidate['question_key'][:10]} answer={candidate['answer_index']}"
        )
        saved.append(f"`{candidate['id']}` → **{candidate['answer_index']}**")

    response = "✅ Saved " + ", ".join(saved) + " and cleared their temp queues."
    if error:
        response += f" Note: {error}."
    await ctx.send(response)

@quiz_group.command(name="delete", aliases=["reject", "remove", "purge"])
@commands.has_permissions(manage_guild=True)
async def quiz_delete(ctx, *, candidate_refs: str):
    tokens = _quiz_split_candidate_refs(candidate_refs)
    if not tokens:
        await ctx.send("❌ Provide at least one temporary quiz ref.")
        return

    scope_value = ""
    if tokens and tokens[-1].lower() in {"question", "all", "purge", "queue"}:
        scope_value = tokens.pop().lower()

    if not tokens:
        await ctx.send("❌ Provide at least one temporary quiz ref before the scope option.")
        return

    candidates, error = _quiz_resolve_review_candidates(" ".join(tokens), ctx=ctx)
    if not candidates:
        await ctx.send(f"❌ No temporary quiz candidate found for `{', '.join(tokens)}`.")
        return

    deleted = []
    if scope_value in {"question", "all", "purge", "queue"}:
        total_removed = 0
        for candidate in candidates:
            removed = len(_quiz_get_review_candidates_for_key(candidate["question_key"]))
            _quiz_delete_review_candidates_for_key(candidate["question_key"])
            total_removed += removed
            deleted.append(f"`{candidate['question_key'][:10]}`({removed})")
            quiz_log(
                f"Manually purged quiz temp queue question_key={candidate['question_key'][:10]} removed={removed}"
            )
        message = f"🗑️ Removed {total_removed} temp candidate(s) across {len(candidates)} question(s): {', '.join(deleted)}."
    else:
        for candidate in candidates:
            _quiz_delete_review_candidate(candidate["id"])
            deleted.append(f"`{candidate['id']}`")
            quiz_log(
                f"Manually deleted quiz temp candidate id={candidate['id']} question_key={candidate['question_key'][:10]}"
            )
        message = f"🗑️ Deleted temp candidate(s): {', '.join(deleted)}."

    if error:
        message += f" Note: {error}."
    await ctx.send(message)

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
        value="```\nn cd list              - Show all active cooldowns\nn cd db                - Inspect the SQLite database\nn quiz temp [limit]    - Review temporary quiz answers\nn quiz confirm <ref...> - Save one or more temp answers permanently\nn quiz delete <ref...>  - Delete one or more temp answers\nn quiz perm            - View recent permanent quiz cache entries\nn quiz perm page <p> [limit] - Browse permanent cache in pages\nn quiz perm view <ref> - Show one permanent cache entry\nn quiz perm edit <ref> <index> [text] - Edit permanent cache\nn cd clear @member     - Clear all user cooldowns\nn cd clear @member cmd - Clear specific cooldown```",
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

class QuizAnswer(BaseModel):
    answer_index: int
    answer_text: Optional[str] = None

def _quiz_options_text(options):
    return "\n".join(options)

def _quiz_question_key(question_text: str, options) -> str:
    normalized = _normalize_quiz_text(question_text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

def _quiz_lookup_permanent(question_key: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT question_key, question_text, options_text, answer_index, answer_text, provider, created_at, updated_at
            FROM quiz_cache
            WHERE question_key = ?
            """,
            (question_key,),
        ).fetchone()
    return row

def _quiz_lookup_candidate(question_key: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT id, answer_index, answer_text, provider, seen_count
            FROM quiz_review_candidates
            WHERE question_key = ?
            ORDER BY last_seen_at DESC, id DESC
            LIMIT 1
            """,
            (question_key,),
        ).fetchone()
    return row

def _quiz_store_candidate(question_key: str, question_text: str, options, answer_index: int, answer_text: str, provider: str):
    now = time.time()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO quiz_review_candidates (
                question_key, question_text, options_text, answer_index, answer_text,
                provider, seen_count, first_seen_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(question_key, provider, answer_index) DO UPDATE SET
                question_text=excluded.question_text,
                options_text=excluded.options_text,
                answer_text=excluded.answer_text,
                seen_count=quiz_review_candidates.seen_count + 1,
                last_seen_at=excluded.last_seen_at
            """,
            (
                question_key,
                question_text,
                _quiz_options_text(options),
                int(answer_index),
                answer_text,
                provider,
                now,
                now,
            ),
        )

def _quiz_promote_candidate(question_key: str, question_text: str, options, answer_index: int, answer_text: str, provider: str):
    now = time.time()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO quiz_cache (
                question_key, question_text, options_text, answer_index, answer_text,
                provider, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(question_key) DO UPDATE SET
                question_text=excluded.question_text,
                options_text=excluded.options_text,
                answer_index=excluded.answer_index,
                answer_text=excluded.answer_text,
                provider=excluded.provider,
                updated_at=excluded.updated_at
            """,
            (
                question_key,
                question_text,
                _quiz_options_text(options),
                int(answer_index),
                answer_text,
                provider,
                now,
                now,
            ),
        )
        conn.execute("DELETE FROM quiz_review_candidates WHERE question_key = ?", (question_key,))
    quiz_log(f"Promoted quiz question to permanent cache: {question_key}")

def _quiz_store_permanent(question_key: str, question_text: str, options, answer_index: int, answer_text: str, provider: str):
    now = time.time()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO quiz_cache (
                question_key, question_text, options_text, answer_index, answer_text,
                provider, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(question_key) DO UPDATE SET
                question_text=excluded.question_text,
                options_text=excluded.options_text,
                answer_index=excluded.answer_index,
                answer_text=excluded.answer_text,
                provider=excluded.provider,
                updated_at=excluded.updated_at
            """,
            (
                question_key,
                question_text,
                _quiz_options_text(options),
                int(answer_index),
                answer_text,
                provider,
                now,
                now,
            ),
        )
        conn.execute("DELETE FROM quiz_review_candidates WHERE question_key = ?", (question_key,))

def _quiz_list_permanent(limit_rows: int = 10, offset_rows: int = 0):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT question_key, question_text, options_text, answer_index, answer_text, provider, created_at, updated_at
            FROM quiz_cache
            ORDER BY updated_at DESC
            LIMIT ? OFFSET ?
            """,
            (int(limit_rows), int(offset_rows)),
        ).fetchall()
    return rows


def _quiz_count_permanent():
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT COUNT(*) AS total FROM quiz_cache").fetchone()
    return int(row["total"]) if row else 0


def _quiz_build_permanent_page_embed(page: int, limit: int):
    page = max(1, int(page or 1))
    limit = max(1, min(int(limit or 5), 20))
    total = _quiz_count_permanent()
    total_pages = max(1, math.ceil(total / limit)) if total else 1
    page = min(page, total_pages)
    offset = (page - 1) * limit
    rows = _quiz_list_permanent(limit, offset)

    embed = discord.Embed(
        title="📚 Permanent Quiz Cache",
        description=f"Page {page}/{total_pages} showing up to {limit} saved answers.",
        color=discord.Color.green(),
    )

    if not rows:
        embed.description = "No permanent quiz cache entries yet."
        embed.set_footer(text="Use n quiz temp and n quiz confirm to add answers.")
        return embed, rows, total_pages, page

    for idx, row in enumerate(rows, start=offset + 1):
        question = _truncate_text(row["question_text"], 110)
        answer_text = _truncate_text(row["answer_text"], 60)
        updated = datetime.datetime.fromtimestamp(float(row["updated_at"])).strftime("%Y-%m-%d %H:%M")
        value = "\n".join(
            [
                f"Key: `{row['question_key'][:10]}`",
                f"Answer: `{row['answer_index']}` {answer_text}",
                f"Provider: `{row['provider'] or 'unknown'}`",
                f"Updated: `{updated}`",
            ]
        )
        embed.add_field(
            name=f"{idx}. {question}",
            value=value,
            inline=False,
        )

    embed.set_footer(text="Use the buttons below to browse pages, or n quiz perm view <ref> to inspect one entry.")
    return embed, rows, total_pages, page


class QuizPermBrowseView(discord.ui.View):
    def __init__(self, author_id: int, page: int = 1, limit: int = 5):
        super().__init__(timeout=300)
        self.author_id = int(author_id)
        self.page = max(1, int(page or 1))
        self.limit = max(1, min(int(limit or 5), 20))

    async def _render(self, interaction: discord.Interaction):
        embed, rows, total_pages, current_page = _quiz_build_permanent_page_embed(self.page, self.limit)
        self.page = current_page
        self.prev_button.disabled = self.page <= 1
        self.next_button.disabled = self.page >= total_pages
        self.first_button.disabled = self.page <= 1
        self.last_button.disabled = self.page >= total_pages
        await interaction.response.edit_message(embed=embed, view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user and interaction.user.id == self.author_id:
            return True
        await interaction.response.send_message(
            "❌ Only the person who opened this cache browser can use these buttons.",
            ephemeral=True,
        )
        return False

    @discord.ui.button(label="First", emoji="⏮️", style=discord.ButtonStyle.secondary)
    async def first_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = 1
        await self._render(interaction)

    @discord.ui.button(label="Prev", emoji="◀️", style=discord.ButtonStyle.primary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(1, self.page - 1)
        await self._render(interaction)

    @discord.ui.button(label="Next", emoji="▶️", style=discord.ButtonStyle.primary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        await self._render(interaction)

    @discord.ui.button(label="Last", emoji="⏭️", style=discord.ButtonStyle.secondary)
    async def last_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        total = _quiz_count_permanent()
        total_pages = max(1, math.ceil(total / self.limit)) if total else 1
        self.page = total_pages
        await self._render(interaction)

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.danger)
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        await interaction.response.edit_message(view=self)

def _quiz_resolve_permanent_reference(question_ref: str):
    ref = str(question_ref or "").strip()
    if not ref:
        return None, "empty"

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        if len(ref) >= 8:
            rows = conn.execute(
                """
                SELECT question_key, question_text, options_text, answer_index, answer_text, provider, created_at, updated_at
                FROM quiz_cache
                WHERE question_key LIKE ?
                ORDER BY updated_at DESC
                """,
                (f"{ref}%",),
            ).fetchall()
        else:
            rows = []

        if not rows:
            rows = conn.execute(
                """
                SELECT question_key, question_text, options_text, answer_index, answer_text, provider, created_at, updated_at
                FROM quiz_cache
                WHERE LOWER(question_text) LIKE ?
                ORDER BY updated_at DESC
                """,
                (f"%{_normalize_quiz_text(ref)}%",),
            ).fetchall()

    if not rows:
        return None, "not_found"
    if len(rows) > 1:
        return rows, "ambiguous"
    return rows[0], None

def _quiz_update_permanent_entry(question_key: str, answer_index: int, answer_text: str, provider: str = "manual"):
    now = time.time()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            UPDATE quiz_cache
            SET answer_index = ?, answer_text = ?, provider = ?, updated_at = ?
            WHERE question_key = ?
            """,
            (int(answer_index), answer_text, provider, now, question_key),
        )

def _quiz_get_review_candidates(limit_questions: int = 10):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT question_key, question_text, options_text, COUNT(*) AS candidate_count, MAX(last_seen_at) AS last_seen_at
            FROM quiz_review_candidates
            GROUP BY question_key, question_text, options_text
            ORDER BY last_seen_at DESC
            LIMIT ?
            """,
            (int(limit_questions),),
        ).fetchall()
    return rows

def _quiz_get_review_candidates_for_key(question_key: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, question_key, question_text, options_text, answer_index, answer_text, provider, seen_count, first_seen_at, last_seen_at
            FROM quiz_review_candidates
            WHERE question_key = ?
            ORDER BY last_seen_at DESC, id DESC
            """,
            (question_key,),
        ).fetchall()
    return rows

def _quiz_temp_view_key(ctx):
    guild_id = getattr(getattr(ctx, "guild", None), "id", None)
    channel_id = getattr(getattr(ctx, "channel", None), "id", None)
    author_id = getattr(getattr(ctx, "author", None), "id", None)
    return (guild_id, channel_id, author_id)


def _quiz_store_temp_view(ctx, groups):
    LAST_QUIZ_TEMP_VIEWS[_quiz_temp_view_key(ctx)] = {
        "saved_at": time.time(),
        "groups": [
            {
                "index": idx,
                "question_key": row["question_key"],
                "question_text": row["question_text"],
                "options_text": row["options_text"],
            }
            for idx, row in enumerate(groups, start=1)
        ],
    }


def _quiz_get_temp_view(ctx):
    return LAST_QUIZ_TEMP_VIEWS.get(_quiz_temp_view_key(ctx))


def _quiz_split_candidate_refs(candidate_refs: str):
    raw = str(candidate_refs or "").strip()
    if not raw:
        return []
    return [token for token in re.split(r"[,\s]+", raw) if token.strip()]


def _quiz_get_review_candidate_by_id(candidate_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT id, question_key, question_text, options_text, answer_index, answer_text, provider, seen_count, first_seen_at, last_seen_at
            FROM quiz_review_candidates
            WHERE id = ?
            """,
            (int(candidate_id),),
        ).fetchone()
    return row


def _quiz_resolve_review_candidate_reference(candidate_ref: str, ctx=None):
    ref = str(candidate_ref or "").strip()
    if not ref:
        return None, "empty"

    if ref.isdigit():
        candidate = _quiz_get_review_candidate_by_id(int(ref))
        if candidate:
            return candidate, None

        if ctx is not None:
            temp_view = _quiz_get_temp_view(ctx)
            if temp_view:
                groups = temp_view.get("groups") or []
                index = int(ref)
                if 1 <= index <= len(groups):
                    question_key = groups[index - 1]["question_key"]
                    candidates = _quiz_get_review_candidates_for_key(question_key)
                    if candidates:
                        return candidates[0], None
                    return None, "not_found"

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, question_key, question_text, options_text, answer_index, answer_text, provider, seen_count, first_seen_at, last_seen_at
            FROM quiz_review_candidates
            WHERE question_key LIKE ?
            ORDER BY last_seen_at DESC, id DESC
            """,
            (f"{ref}%",),
        ).fetchall()

    if not rows:
        return None, "not_found"

    question_keys = {row["question_key"] for row in rows}
    if len(question_keys) > 1:
        return rows, "ambiguous"

    return rows[0], None


def _quiz_resolve_review_candidates(candidate_refs: str, ctx=None):
    tokens = _quiz_split_candidate_refs(candidate_refs)
    if not tokens:
        return [], "empty"

    resolved = []
    seen_keys = set()
    errors = []

    for token in tokens:
        candidate, error = _quiz_resolve_review_candidate_reference(token, ctx=ctx)
        if error == "ambiguous":
            errors.append(f"`{token}` matches multiple questions")
            continue
        if not candidate:
            errors.append(f"`{token}` not found")
            continue

        question_key = candidate["question_key"]
        if question_key in seen_keys:
            continue
        seen_keys.add(question_key)
        resolved.append(candidate)

    if errors and not resolved:
        return [], "; ".join(errors)
    if errors:
        return resolved, "; ".join(errors)
    return resolved, None

def _quiz_delete_review_candidates_for_key(question_key: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM quiz_review_candidates WHERE question_key = ?", (question_key,))

def _quiz_delete_review_candidate(candidate_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM quiz_review_candidates WHERE id = ?", (int(candidate_id),))

def _truncate_text(text: str, limit: int = 120) -> str:
    cleaned = re.sub(r"\s+", " ", str(text)).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 3)] + "..."

def _quiz_text_from_provider_response(raw_text: str):
    if not raw_text:
        return None
    raw_text = raw_text.strip()
    if raw_text.startswith("```"):
        raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text, flags=re.IGNORECASE)
        raw_text = re.sub(r"\s*```$", "", raw_text)

    # OpenRouter and other OpenAI-compatible providers often return a full
    # chat.completion envelope. Unwrap the assistant message content first.
    if raw_text.startswith("{") and '"choices"' in raw_text:
        try:
            payload = json.loads(raw_text)
            choices = payload.get("choices") or []
            if choices:
                message = choices[0].get("message") or {}
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    raw_text = content.strip()
        except Exception:
            pass

    start = raw_text.find("{")
    end = raw_text.rfind("}")
    if start != -1 and end != -1 and end > start:
        raw_text = raw_text[start:end + 1]
    return raw_text

def _normalize_answer_index(answer_index, options):
    try:
        answer_index = int(answer_index)
    except Exception:
        return None
    if 1 <= answer_index <= len(options):
        return answer_index
    return None

def _extract_quiz_payload(message):
    options = []
    question_bits = []
    title_bits = []

    option_label_re = re.compile(r"^(?:\d+|[1-3]️⃣|:one:|:two:|:three:|[A-C])$", re.IGNORECASE)
    question_line_re = re.compile(r"^(?:who|what|which|when|where|why|how)\b.*\??$", re.IGNORECASE)
    numbered_line_re = re.compile(
        r"^(?:[•\-]|(?:\d+|[A-C]|[1-3]️⃣|:one:|:two:|:three:)[\.\):]?)\s+(.+)$",
        re.IGNORECASE,
    )
    noise_line_re = re.compile(
        r"^(?:you earned|correct answer|answer|result|rewards?|mission|rank|cooldown|time left|next run|dattebayo|congratul|completed?)",
        re.IGNORECASE,
    )

    def clean_line(text: str) -> str:
        cleaned = re.sub(r"\s+", " ", text).strip()
        cleaned = cleaned.strip("`*_")
        cleaned = re.sub(r"^[\*\-_>\s]+", "", cleaned)
        cleaned = re.sub(r"[\*\-_>\s]+$", "", cleaned)
        return cleaned

    def add_option(option_text: str):
        option = clean_line(option_text)
        if option and option not in options and not question_line_re.match(option):
            options.append(option)

    def add_question_text(text: str):
        cleaned = clean_line(text)
        if cleaned and not noise_line_re.match(cleaned):
            question_bits.append(cleaned)

    def is_question_like(text: str) -> bool:
        cleaned = clean_line(text)
        return bool(cleaned) and (
            cleaned.endswith("?")
            or question_line_re.match(cleaned)
            or cleaned.lower().startswith(("who ", "what ", "which ", "when ", "where ", "why ", "how "))
        )

    if message.content:
        for line in message.content.splitlines():
            line = clean_line(line)
            if not line:
                continue
            if is_question_like(line):
                add_question_text(line)
                continue
            match = numbered_line_re.match(line)
            if match:
                add_option(match.group(1))
            else:
                add_question_text(line)

    for embed in message.embeds:
        if embed.title:
            title_bits.append(clean_line(embed.title))
        if embed.description:
            for line in embed.description.splitlines():
                line = clean_line(line)
                if not line:
                    continue
                if is_question_like(line):
                    add_question_text(line)
                    if QUIZ_DEBUG:
                        quiz_log(f"Matched description question line: {line!r}")
                    continue
                match = numbered_line_re.match(line)
                if match:
                    add_option(match.group(1))
                    if QUIZ_DEBUG:
                        quiz_log(f"Matched description option line: {line!r} -> {match.group(1)!r}")
                elif not noise_line_re.match(line):
                    add_question_text(line)
        for field in embed.fields:
            field_name = clean_line(field.name or "")
            field_value = clean_line(field.value or "")

            if QUIZ_DEBUG:
                quiz_log(f"Field seen name={field_name!r} value={field_value[:120]!r}")

            if field_name and option_label_re.match(field_name):
                if field_value:
                    add_option(field_value)
                    if QUIZ_DEBUG:
                        quiz_log(f"Field treated as option label {field_name!r} -> option {field_value!r}")
                continue

            if field_value:
                if is_question_like(field_value):
                    add_question_text(field_value)
                    if QUIZ_DEBUG:
                        quiz_log(f"Field treated as question text: {field_value!r}")
                    continue
                match = numbered_line_re.match(field_value)
                if match:
                    add_option(match.group(1))
                    if QUIZ_DEBUG:
                        quiz_log(f"Matched field option value: {field_value!r} -> {match.group(1)!r}")
                else:
                    if not noise_line_re.match(field_value):
                        add_question_text(field_value)

            if field_name and not option_label_re.match(field_name):
                if is_question_like(field_name):
                    add_question_text(field_name)
                elif not noise_line_re.match(field_name):
                    add_question_text(field_name)

    full_text = "\n".join(title_bits + question_bits + options).strip()
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

def _build_quiz_prompt(question_text: str, options):
    lines = [
        "Choose the correct option.",
        'Return JSON: {"answer_index": 1}',
        "Use Naruto canon, not text similarity or guessing.",
        f"Q: {question_text}",
    ]
    lines.extend(f"{idx + 1}. {option}" for idx, option in enumerate(options))
    return "\n".join(lines)

def _call_chat_completion(endpoint: str, api_key: str, model: str, question_text: str, options, provider_name: str, extra_headers=None):
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "Return JSON only with answer_index. Use Naruto canon.",
            },
            {
                "role": "user",
                "content": _build_quiz_prompt(question_text, options),
            },
        ],
        "temperature": 0,
        "max_completion_tokens": 12,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)

    request = urllib_request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urllib_request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8")
            quiz_log(f"{provider_name} raw response: {body[:240]!r}")
            return body
    except urllib_error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise RuntimeError(f"{provider_name} HTTP {e.code}: {body or e.reason}")
    except Exception as e:
        raise RuntimeError(f"{provider_name} request failed: {e}")

def _extract_provider_answer(raw_text: str, options):
    cleaned = _quiz_text_from_provider_response(raw_text)
    if not cleaned:
        raise ValueError("empty provider response")

    parsed = QuizAnswer.model_validate_json(cleaned)
    answer_index = _normalize_answer_index(parsed.answer_index, options)
    answer_text = str(parsed.answer_text).strip() if parsed.answer_text is not None else ""

    if answer_index is not None:
        if answer_text:
            return answer_index, answer_text
        return answer_index, options[answer_index - 1]

    for idx, option in enumerate(options, start=1):
        if _normalize_quiz_text(option) == _normalize_quiz_text(answer_text):
            return idx, answer_text

    raise ValueError("provider response did not match supplied options")

async def _ask_groq(question_text: str, options):
    if not groq_client:
        return None
    quiz_log(f"Sending question to Groq. question={question_text[:240]!r} options={options!r}")
    prompt = _build_quiz_prompt(question_text, options)
    response = await groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {
                "role": "system",
                "content": "Return JSON only with answer_index. Use Naruto canon.",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        max_tokens=12,
    )
    raw_text = response.choices[0].message.content or ""
    quiz_log(f"Groq raw response: {raw_text[:240]!r}")
    return _extract_provider_answer(raw_text, options)

async def _ask_gemini(question_text: str, options):
    if not gemini_client or not GEMINI_API_KEY:
        return None

    quiz_log(f"Sending question to Gemini. question={question_text[:240]!r} options={options!r}")
    prompt = _build_quiz_prompt(question_text, options)
    response = await asyncio.to_thread(
        lambda: gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=(
                    "Return JSON only with answer_index. Use Naruto canon."
                ),
                response_mime_type="application/json",
                temperature=0,
                max_output_tokens=12,
            ),
        )
    )
    quiz_log(f"Gemini raw response: {response.text[:240]!r}")
    return _extract_provider_answer(response.text, options)

async def _ask_openrouter(question_text: str, options):
    if not OPENROUTER_API_KEY:
        return None
    raw = await asyncio.to_thread(
        _call_chat_completion,
        "https://openrouter.ai/api/v1/chat/completions",
        OPENROUTER_API_KEY,
        OPENROUTER_MODEL,
        question_text,
        options,
        "OpenRouter",
        {
            "HTTP-Referer": "https://discord.com",
            "X-Title": "Naruto Botto Companion",
        },
    )
    return _extract_provider_answer(raw, options)

async def ask_gpt(question_text, options=None):
    if not options:
        quiz_log("Skipping quiz call because no options were detected.")
        return None

    question_key = _quiz_question_key(question_text, options)

    cached = _quiz_lookup_permanent(question_key)
    if cached:
        cached_answer_text = str(cached["answer_text"] or "").strip()
        if cached_answer_text:
            for idx, option in enumerate(options, start=1):
                if _normalize_quiz_text(option) == _normalize_quiz_text(cached_answer_text):
                    quiz_log(
                        f"Permanent cache hit for question_key={question_key[:10]} provider={cached['provider']!r} answer={idx}"
                    )
                    return idx
        answer_index = _normalize_answer_index(cached["answer_index"], options)
        if answer_index is not None:
            quiz_log(
                f"Permanent cache fallback hit for question_key={question_key[:10]} provider={cached['provider']!r} answer={answer_index}"
            )
            return answer_index

    for provider_name, provider_fn in [
        ("OpenRouter", _ask_openrouter),
        ("Gemini", _ask_gemini),
        ("Groq", _ask_groq),
    ]:
        try:
            result = await provider_fn(question_text, options)
            if result:
                answer_index, answer_text = result
                _quiz_store_candidate(question_key, question_text, options, answer_index, answer_text, provider_name)
                quiz_log(
                    f"Stored temporary quiz answer: question_key={question_key[:10]} answer={answer_index} provider={provider_name}"
                )
                return answer_index
        except Exception as e:
            quiz_log(f"{provider_name} failed: {e}")
            continue

    if QUIZ_ALLOW_LOCAL_FALLBACK:
        quiz_log("Using local fallback because QUIZ_ALLOW_LOCAL_FALLBACK=true.")
        local_answer = _pick_local_quiz_answer(question_text, options)
        if local_answer and local_answer in options:
            answer_index = options.index(local_answer) + 1
            answer_text = local_answer
            _quiz_store_candidate(question_key, question_text, options, answer_index, answer_text, "local")
            quiz_log(f"Local fallback selected: {local_answer!r}")
            return answer_index
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
        try:
            await message.reply(f"Answer: {answer}", mention_author=False)
        except Exception:
            await message.channel.send(f"Answer: {answer}")
    else:
        quiz_log("No answer sent.")

keep_alive()
bot.run(DISCORD_TOKEN)
