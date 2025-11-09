from keep_alive import keep_alive
import discord
from discord.ext import commands, tasks
import asyncio
import json
import re
import time
import datetime
from openai import OpenAI
import os
from typing import Dict, Optional

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

bot = commands.Bot(command_prefix="n ", intents=intents, help_command=None)

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
    "t": "tower",
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
notified_users = set()

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
    matches = re.findall(r"(\d+)\s*(second|seconds|sec|minute|minutes|min|hour|hours|h|day|days|d)", text.lower())
    for amount, unit in matches:
        amount = int(amount)
        if "second" in unit or unit == "sec":
            total_seconds += amount
        elif "minute" in unit or unit == "min":
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
                f"{emoji} **{cmd.upper()} READY!** Believe it! Time to get back out there, {user.mention}!",
                f"{emoji} Your **{cmd}** cooldown is complete! The ninja way never stops, {user.mention}!",
                f"{emoji} **{cmd.upper()}** is ready to go! Show them what you're made of, {user.mention}!",
                f"{emoji} Cooldown finished for **{cmd}**! Let's do this, {user.mention}!",
            ]
            
            import random
            message = random.choice(messages)
            
            if channel_id:
                try:
                    channel = bot.get_channel(channel_id)
                    if channel:
                        await channel.send(message)
                except:
                    pass
            
            try:
                await user.send(f"🎉 **Cooldown Alert!**\n{message}")
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
    
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="Naruto Botto cooldowns"
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
        
        if "cooldown" in full_text.lower() and not ("your **" in full_text.lower() and "has been noted" in full_text.lower()):
            time_secs = parse_time_string(full_text)
            if time_secs > 0 and message.mentions:
                user = message.mentions[0]
                detected = None
                for name in cooldown_times.keys():
                    if name in full_text.lower():
                        detected = name
                        break
                if not detected:
                    detected = "mission"
                
                cooldowns.setdefault(user.id, {})[detected] = {
                    "expires_at": time.time() + time_secs,
                    "channel_id": message.channel.id,
                    "notified": False
                }
                save_cooldowns()
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
    await track_cooldown(ctx, "mission")

@bot.command(name="r", aliases=["report"])
async def track_report(ctx):
    await track_cooldown(ctx, "report")

@bot.command(name="t", aliases=["tower"])
async def track_tower(ctx):
    await track_cooldown(ctx, "tower")

@bot.command(name="d", aliases=["daily"])
async def track_daily(ctx):
    await track_cooldown(ctx, "daily")

@bot.command(name="w", aliases=["weekly"])
async def track_weekly(ctx):
    await track_cooldown(ctx, "weekly")

async def track_cooldown(ctx, cmd):
    user_id = ctx.author.id
    remaining = get_remaining_time(user_id, cmd)
    emoji = cooldown_emojis.get(cmd, "⏰")
    
    if remaining > 0:
        time_str = format_time(remaining)
        messages = [
            f"{emoji} Whoa there, {ctx.author.mention}! Your **{cmd}** is still cooling down. **{time_str}** to go!",
            f"{emoji} Not so fast! You need to wait **{time_str}** before your **{cmd}** is ready, {ctx.author.mention}!",
            f"{emoji} Patience, young shinobi! Your **{cmd}** will be ready in **{time_str}**, {ctx.author.mention}!",
        ]
        import random
        await ctx.send(random.choice(messages))
        return
    
    cooldowns.setdefault(user_id, {})[cmd] = {
        "expires_at": time.time() + cooldown_times[cmd],
        "channel_id": ctx.channel.id,
        "notified": False
    }
    save_cooldowns()
    
    duration = format_time(cooldown_times[cmd])
    messages = [
        f"{emoji} **{cmd.upper()}** tracked! I'll ping you in **{duration}**, {ctx.author.mention}! Believe it! 🍜",
        f"{emoji} Got it! Your **{cmd}** is now on cooldown for **{duration}**, {ctx.author.mention}! 🥷",
        f"{emoji} Roger that! **{cmd}** cooldown started. See you in **{duration}**, {ctx.author.mention}! ⚡",
        f"{emoji} Alright! I'll remind you about **{cmd}** in **{duration}**, {ctx.author.mention}! Let's go! 🔥",
    ]
    import random
    await ctx.send(random.choice(messages))

@bot.group(name="cooldown", aliases=["cd", "cooldowns"])
async def cooldown_group(ctx):
    if ctx.invoked_subcommand is None:
        await ctx.send("📋 Use `n cooldown list`, `n cooldown user @mention`, or `n cooldown clear @mention [activity]`")

@cooldown_group.command(name="list", aliases=["all", "show"])
@commands.has_permissions(manage_guild=True)
async def list_cooldowns(ctx):
    if not cooldowns:
        await ctx.send("📋 No active cooldowns right now! Everyone's ready to go!")
        return
    
    embed = discord.Embed(
        title="🎌 Active Cooldowns",
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
        description="Your personal cooldown tracker and companion!",
        color=discord.Color.gold()
    )
    
    embed.add_field(
        name="⚔️ Track Cooldowns",
        value="```\nn m / n mission  - Track mission (1min)\nn r / n report   - Track report (10min)\nn t / n tower    - Track tower (6hrs)\nn d / n daily    - Track daily (24hrs)\nn w / n weekly   - Track weekly (7days)```",
        inline=False
    )
    
    embed.add_field(
        name="📊 Check Cooldowns",
        value="```\nn cd user @member     - Check someone's cooldowns\nn cd user             - Check your own cooldowns```",
        inline=False
    )
    
    embed.add_field(
        name="🛡️ Admin Commands (Manage Server permission required)",
        value="```\nn cd list              - Show all active cooldowns\nn cd clear @member     - Clear all cooldowns for a user\nn cd clear @member mission - Clear specific cooldown```",
        inline=False
    )
    
    embed.add_field(
        name="🔔 Auto Features",
        value="• **Auto-tracking**: Detects Naruto Botto cooldowns automatically\n• **Reminders**: Pings you when cooldowns expire\n• **DM Notifications**: Sends you a DM when ready",
        inline=False
    )
    
    embed.set_footer(text="Made with ❤️ for Naruto Botto players")
    await ctx.send(embed=embed)

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
