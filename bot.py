from keep_alive import keep_alive
import discord
from discord import app_commands
from discord.ext import commands, tasks
import asyncio
import json
import re
import time
import datetime
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
ENABLE_GPT = os.getenv("ENABLE_GPT", "false").lower() == "true"

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

# MODIFIED LINE BELOW: Added list for prefix and case_insensitive=True
bot = commands.Bot(command_prefix=["n ", "N "], case_insensitive=True, intents=intents, help_command=None)

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
        with open("cooldowns.json", "w") as f:
            json.dump(cooldowns, f, indent=2)
    except Exception as e:
        print(f"❌ Error saving cooldowns: {e}")

def load_cooldowns():
    global cooldowns
    try:
        with open("cooldowns.json") as f:
            data = json.load(f)
            for uid_str, cmds in data.items():
                uid = int(uid_str)
                cooldowns[uid] = {}
                for cmd, cmd_data in cmds.items():
                    if isinstance(cmd_data, (int, float)):
                        cooldowns[uid][cmd] = {
                            "expires_at": float(cmd_data),
                            "channel_id": None,
                            "notified": False
                        }
                    else:
                        cooldowns[uid][cmd] = cmd_data
        print(f"📂 Loaded cooldowns for {len(cooldowns)} users")
    except FileNotFoundError:
        print("📂 No existing cooldowns file found, starting fresh")
        cooldowns = {}
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
    
    if message.author.bot and "naruto botto" in message.author.name.lower():
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
        
        if pending_smart_tracks and ("wait" in full_text.lower() or "cooldown" in full_text.lower()):
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
                        
                        print(f"📝 Processing {cmd_to_process} for user {user_id}")
                        print(f"⏰ Existing cooldown found: {time_secs}s for {cmd_to_process}")
                        
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
                                description=f"Found existing cooldown from Naruto Botto!",
                                color=cooldown_colors.get(detected_cmd, discord.Color.blue())
                            )
                            
                            total_time = cooldown_times.get(detected_cmd, time_secs)
                            elapsed = max(0, total_time - time_secs)
                            progress = get_progress_bar(elapsed, total_time)
                            
                            embed.add_field(name="⏰ Time Remaining", value=f"**{time_str}**", inline=True)
                            embed.add_field(name="📊 Progress", value=progress, inline=False)
                            embed.add_field(name="✅ Status", value="I'll ping you when it's ready!", inline=False)
                            embed.set_footer(text=f"Cooldown tracked from Naruto Botto")
                            
                            try:
                                channel = bot.get_channel(channel_id)
                                if channel:
                                    await channel.send(embed=embed)
                            except Exception as e:
                                print(f"Error sending existing cooldown: {e}")
                        else:
                            fun_msg = random.choice(entertaining_messages.get(detected_cmd, ["Tracked!"]))
                            message_text = f"{emoji} **{detected_cmd.upper()}** detected! {time_str} remaining. I'll ping you!\n{fun_msg}"
                            
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
        
        if ENABLE_GPT and openai_client:
            has_question = "?" in full_text or "Who" in full_text or "What" in full_text
            has_numbered_options = (":one:" in full_text or ":two:" in full_text or ":three:" in full_text or 
                                   "1️⃣" in full_text or "2️⃣" in full_text or 
                                   ("1" in full_text and "2" in full_text))
            
            if has_question and has_numbered_options:
                response = await ask_gpt(full_text)
                if response:
                    await message.channel.send(response)
            return
    
    await bot.process_commands(message)

@bot.command(name="m", aliases=["mission"])
async def track_mission(ctx):
    await track_cooldown_smart(ctx, "mission")

@bot.command(name="r", aliases=["report"])
async def track_report(ctx):
    await track_cooldown_smart(ctx, "report")

@bot.command(name="to", aliases=["tower"])
async def track_tower(ctx):
    await track_cooldown_smart(ctx, "tower")

@bot.command(name="d", aliases=["daily"])
async def track_daily(ctx):
    await track_cooldown_smart(ctx, "daily")

@bot.command(name="w", aliases=["weekly"])
async def track_weekly(ctx):
    await track_cooldown_smart(ctx, "weekly")

@bot.command(name="ch", aliases=["challenge"])
async def track_challenge(ctx):
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
    
    pending_smart_tracks[user_id][cmd] = {
        "channel_id": ctx.channel.id,
        "timestamp": time.time()
    }
    
    print(f"⏳ Waiting for Naruto Botto response for {cmd} (user: {user_id})")
    
    await asyncio.sleep(2.0)
    
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
                description=f"Your **{cmd}** cooldown has been tracked!",
                color=cooldown_colors.get(cmd, discord.Color.green())
            )
            
            embed.add_field(name="⏰ Duration", value=f"**{duration}**", inline=True)
            embed.add_field(name="🔔 Reminder", value=f"I'll ping {ctx.author.mention} when ready!", inline=True)
            embed.add_field(name="📊 Progress", value=get_progress_bar(0, cooldown_times[cmd]), inline=False)
            
            messages = [
                "Dattebayo! 🍜",
                "The ninja way never stops! 🥷",
                "Believe it! ⚡",
                "Let's go! 🔥"
            ]
            embed.set_footer(text=random.choice(messages))
            
            await ctx.send(embed=embed)
        else:
            fun_msg = random.choice(entertaining_messages.get(cmd, ["Let's go!"]))
            message = f"{emoji} **{cmd.upper()}** tracked! Ready in {duration}. I'll ping you!\n{fun_msg}"
            await ctx.send(message)
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
            value="```\nn cd list              - Show all server cooldowns\nn cd user @member      - Check user's cooldowns\nn cd clear @member     - Clear all user cooldowns```",
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
        value="```\nn cd list              - Show all active cooldowns\nn cd clear @member     - Clear all user cooldowns\nn cd clear @member cmd - Clear specific cooldown```",
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

async def ask_gpt(question_text):
    if not openai_client:
        return None
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a Naruto expert. For multiple choice questions, respond with ONLY the number (1, 2, or 3) of the correct answer. Do not include any explanation."},
                {"role": "user", "content": question_text}
            ],
            max_tokens=10,
            timeout=5
        )
        return response.choices[0].message.content.strip()
    except:
        return None

keep_alive()
bot.run(DISCORD_TOKEN)
