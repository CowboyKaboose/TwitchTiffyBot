# --- START OF FILE bot.py ---

import discord
from discord.ext import commands, tasks
import requests
import json
import os
import asyncio
from dotenv import load_dotenv, set_key, find_dotenv
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Set, Dict, Optional, Any # Added Optional, Any

# --- Configuration and Setup ---
load_dotenv()

DISCORD_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
TWITCH_CLIENT_ID = os.getenv('TWITCH_CLIENT_ID')
TWITCH_CLIENT_SECRET = os.getenv('TWITCH_CLIENT_SECRET')
VIP_CHANNEL_ID_STR = os.getenv('VIP_CHANNEL_ID')
MOD_CHANNEL_ID_STR = os.getenv('MOD_CHANNEL_ID')
ALLOWED_USER_IDS_STR = os.getenv('ALLOWED_USER_IDS', '')

# --- Global Variables ---
twitch_access_token = None
token_expiry_time = datetime.now(timezone.utc)
last_successful_check_time = None

# Validate Channel IDs
VIP_CHANNEL_ID: int = 0
MOD_CHANNEL_ID: int = 0
try:
    if VIP_CHANNEL_ID_STR: VIP_CHANNEL_ID = int(VIP_CHANNEL_ID_STR)
    else: raise ValueError("VIP_CHANNEL_ID is missing in .env")
    if MOD_CHANNEL_ID_STR: MOD_CHANNEL_ID = int(MOD_CHANNEL_ID_STR)
    else: raise ValueError("MOD_CHANNEL_ID is missing in .env")
except (TypeError, ValueError) as e:
    print(f"Error: Invalid Channel ID in .env file. Details: {e}")
    exit()

# Parse ALLOWED_USER_IDS
ALLOWED_USER_IDS: Set[int] = set()
if ALLOWED_USER_IDS_STR:
    try:
        ALLOWED_USER_IDS = {int(uid.strip()) for uid in ALLOWED_USER_IDS_STR.split(',') if uid.strip()}
        print(f"Loaded {len(ALLOWED_USER_IDS)} allowed user IDs.")
    except ValueError:
        print("Error parsing ALLOWED_USER_IDS from .env file.")
else:
     print("Warning: No ALLOWED_USER_IDS defined in .env.")

# --- Logging Setup ---
log_formatter = logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s')
log_file_handler = logging.FileHandler('bot.log', encoding='utf-8', mode='a')
log_file_handler.setFormatter(log_formatter)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(log_file_handler)

discord_logger = logging.getLogger('discord')
discord_logger.setLevel(logging.INFO)
discord_logger.addHandler(log_file_handler)

tasks_logger = logging.getLogger('discord.ext.tasks')
tasks_logger.setLevel(logging.INFO)
tasks_logger.addHandler(log_file_handler)

# --- Bot Setup ---
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.guilds = True
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# --- Global Check for DMs Only ---
@bot.check
async def globally_block_guilds(ctx):
    """Prevents commands from running in server channels (Guilds)."""
    is_dm = ctx.guild is None
    if not is_dm:
        logger.debug(f"Command '{ctx.command.name if ctx.command else 'Unknown'}' ignored in guild {ctx.guild.id} by {ctx.author} ({ctx.author.id})")
    return is_dm

# --- Data File Paths ---
VIP_STREAMERS_FILE = 'vip_streamers.json'
MOD_STREAMERS_FILE = 'mod_streamers.json'
LIVE_MESSAGES_VIP_FILE = 'live_messages_vip.json'
LIVE_MESSAGES_MOD_FILE = 'live_messages_mod.json'

# --- Helper Functions ---

def load_data(filename):
    """Loads data from a JSON file."""
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            content = f.read()
            if not content.strip():
                 if 'streamers' in filename: return []
                 if 'live_messages' in filename: return {}
                 return {}
            return json.loads(content)
    except FileNotFoundError:
        logger.warning(f"{filename} not found, returning default structure.")
        if 'streamers' in filename: return []
        if 'live_messages' in filename: return {}
        return None
    except json.JSONDecodeError:
        logger.error(f"Error decoding JSON from {filename}. Returning default structure.")
        if 'streamers' in filename: return []
        if 'live_messages' in filename: return {}
        return None

def save_data(filename, data):
    """Saves data to a JSON file."""
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except IOError as e:
        logger.error(f"Error saving data to {filename}: {e}")

async def get_twitch_app_access_token():
    """Gets or refreshes the Twitch App Access Token."""
    global twitch_access_token, token_expiry_time
    now = datetime.now(timezone.utc)
    if twitch_access_token and now < (token_expiry_time - timedelta(minutes=5)):
        return twitch_access_token
    logger.info("Attempting to get/refresh Twitch App Access Token...")
    url = "https://id.twitch.tv/oauth2/token"
    payload = {'client_id': TWITCH_CLIENT_ID, 'client_secret': TWITCH_CLIENT_SECRET, 'grant_type': 'client_credentials'}
    try:
        response = await asyncio.to_thread(requests.post, url, data=payload, timeout=15)
        response.raise_for_status()
        data = response.json()
        twitch_access_token = data['access_token']
        expires_in = data.get('expires_in', 3600)
        token_expiry_time = now + timedelta(seconds=expires_in)
        logger.info(f"Successfully obtained/refreshed Twitch App Access Token. Expires in ~{expires_in // 60} minutes.")
        return twitch_access_token
    except requests.exceptions.Timeout: logger.error("Timeout getting Twitch token."); return None
    except requests.exceptions.RequestException as e:
        response_text = getattr(e.response, 'text', 'N/A'); response_status = getattr(e.response, 'status_code', 'N/A')
        logger.error(f"Error getting Twitch token (Status: {response_status}): {e} - Response: {response_text}")
        twitch_access_token = None; token_expiry_time = datetime.now(timezone.utc); return None
    except KeyError as e:
        response_text = getattr(response, 'text', 'N/A');
        logger.error(f"Error parsing Twitch token response (Key: {e}): {response_text}")
        twitch_access_token = None; token_expiry_time = datetime.now(timezone.utc); return None
    except Exception as e:
        logger.error(f"Unexpected error getting Twitch token: {e}", exc_info=True)
        twitch_access_token = None; token_expiry_time = datetime.now(timezone.utc); return None

async def get_stream_status(streamer_login):
    """Checks if a Twitch streamer is live using their login name. Includes started_at."""
    token = await get_twitch_app_access_token()
    if not token: logger.warning(f"Cannot check status for {streamer_login}: no valid Twitch token."); return None
    headers = {'Client-ID': TWITCH_CLIENT_ID, 'Authorization': f'Bearer {token}'}
    params = {'user_login': streamer_login}
    url = 'https://api.twitch.tv/helix/streams'
    try:
        response = await asyncio.to_thread(requests.get, url, headers=headers, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        if data.get('data'):
            stream_info = data['data'][0]
            # Include user_login in the status dict if available, crucial for embed creation helper
            status_data = {
                'live': True,
                'user_login': stream_info.get('user_login', streamer_login), # Prefer API login name
                'title': stream_info.get('title', 'No Title'),
                'game_name': stream_info.get('game_name', 'No Game'),
                'viewer_count': stream_info.get('viewer_count', 0),
                'thumbnail_url': stream_info.get('thumbnail_url', '').replace('{width}', '320').replace('{height}', '180'),
                'started_at': stream_info.get('started_at')
            }
            return status_data
        else:
            return {'live': False, 'user_login': streamer_login} # Still include login when offline
    except requests.exceptions.Timeout: logger.error(f"Timeout checking status for {streamer_login}."); return None
    except requests.exceptions.RequestException as e:
        response_status = getattr(e.response, 'status_code', 'N/A'); response_text = getattr(e.response, 'text', 'N/A')
        logger.error(f"Error checking Twitch status for {streamer_login} (Status: {response_status}): {e} - Response: {response_text}")
        if response_status == 401: logger.warning(f"Twitch token invalid (401) for {streamer_login}. Forcing refresh."); global twitch_access_token; twitch_access_token = None; token_expiry_time = datetime.now(timezone.utc)
        elif response_status == 403: logger.warning(f"Twitch returned 403 Forbidden for {streamer_login}.")
        elif response_status == 429: logger.warning("Twitch API rate limit possibly hit.")
        return None
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        response_text = getattr(response, 'text', 'N/A')
        logger.error(f"Error parsing Twitch stream data for {streamer_login}: {e} - Response: {response_text}")
        return None
    except Exception as e: logger.error(f"Unexpected error checking status for {streamer_login}: {e}", exc_info=True); return None

def format_timedelta(duration: timedelta) -> str:
    """Formats a timedelta duration into a string like '1h 23m' or '45m'."""
    if not isinstance(duration, timedelta) or duration.total_seconds() < 0: return "N/A"
    total_seconds = int(duration.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    if hours > 0: return f"{hours}h {minutes}m"
    elif minutes > 0: return f"{minutes}m"
    else: return "< 1m"

async def create_live_embed(status: Dict[str, Any], tier_name: str, current_time: datetime) -> Optional[discord.Embed]:
    """Helper to create the standard live embed, including duration."""
    streamer_login = status.get('user_login', 'Unknown') # Use the login returned by API
    start_time_str = status.get('started_at')
    duration_str = "N/A"
    if start_time_str:
        try:
            start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
            duration = current_time - start_time
            duration_str = format_timedelta(duration)
        except (ValueError, TypeError) as e: logger.warning(f"[{tier_name}] Error parsing started_at '{start_time_str}' for {streamer_login}: {e}"); duration_str = "Error"

    embed = discord.Embed(title=f"{streamer_login} is LIVE! ({tier_name.upper()})", url=f"https://twitch.tv/{streamer_login}", description=status.get('title', 'No Title Provided'), color=discord.Color.purple(), timestamp=current_time)
    embed.add_field(name="Game", value=status.get('game_name', 'N/A'), inline=True)
    embed.add_field(name="Viewers", value=f"{status.get('viewer_count', 'N/A'):,}", inline=True)
    embed.add_field(name="Live For", value=duration_str, inline=True)
    thumbnail_url = status.get('thumbnail_url')
    if thumbnail_url:
        timestamp_query = f"?t={int(current_time.timestamp())}"
        embed.set_image(url=thumbnail_url + timestamp_query)
    embed.set_footer(text="Click the title to watch!")
    return embed


async def check_and_notify_tier(bot_instance: commands.Bot, tier_name: str, streamer_logins: List[str], target_channel_id: int, live_messages_file: str):
    """Checks a specific tier of streamers and handles notifications using delete/repost for updates."""
    if not streamer_logins: return
    live_messages: Dict[str, int] = load_data(live_messages_file)
    if not isinstance(live_messages, dict): logger.error(f"[{tier_name}] Corrupted data in {live_messages_file}. Resetting."); live_messages = {}; save_data(live_messages_file, live_messages)
    logger.info(f"Performing check for {tier_name} tier: {', '.join(streamer_logins)}")
    channel = bot_instance.get_channel(target_channel_id)
    if not channel: logger.error(f"{tier_name} target channel ({target_channel_id}) not found."); return

    current_live_mapping = live_messages.copy()
    changes_made_to_tracker = False
    now = datetime.now(timezone.utc)

    for streamer_login in streamer_logins:
        message_id_to_remove_from_tracker = None
        try:
            status = await get_stream_status(streamer_login)
            await asyncio.sleep(0.2)
            if status is None: logger.warning(f"[{tier_name}] Skipping {streamer_login}: API error."); continue
            actual_login = status.get('user_login', streamer_login) # Use login returned by API for consistency
            is_live = status.get('live', False)
            is_currently_posted = actual_login in current_live_mapping

            # Scenario 1: Went LIVE
            if is_live and not is_currently_posted:
                logger.info(f"[{tier_name}] {actual_login} went LIVE! Posting initial notification.")
                try:
                    live_embed = await create_live_embed(status, tier_name, now)
                    if live_embed:
                        message_text = f"🎉 {actual_login} ({tier_name}) just went live! 🎉"
                        message = await channel.send(message_text, embed=live_embed)
                        live_messages[actual_login] = message.id
                        changes_made_to_tracker = True
                        logger.info(f"[{tier_name}] Posted live notification for {actual_login} (Msg ID: {message.id})")
                    else: logger.error(f"[{tier_name}] Failed to create embed for {actual_login}")
                except discord.Forbidden: logger.error(f"[{tier_name}] Bot lacks permissions (Send/Embed) in {target_channel_id}.")
                except discord.HTTPException as e: logger.error(f"[{tier_name}] HTTP error posting msg for {actual_login}: {e.status} {e.text}")
                except Exception as e: logger.error(f"[{tier_name}] Error posting msg for {actual_login}: {e}", exc_info=True)

            # Scenario 2: Went OFFLINE
            elif not is_live and is_currently_posted:
                logger.info(f"[{tier_name}] {actual_login} went OFFLINE. Deleting notification.")
                old_message_id = current_live_mapping.get(actual_login)
                if old_message_id:
                    try:
                        message = await channel.fetch_message(old_message_id); await message.delete()
                        logger.info(f"[{tier_name}] Deleted notification for {actual_login} (Msg ID: {old_message_id})")
                    except discord.NotFound: logger.warning(f"[{tier_name}] Msg {old_message_id} for {actual_login} not found.")
                    except discord.Forbidden: logger.error(f"[{tier_name}] Bot lacks permissions (Manage Msgs) in {target_channel_id}.")
                    except discord.HTTPException as e: logger.error(f"[{tier_name}] HTTP error deleting msg {old_message_id}: {e.status} {e.text}")
                    except Exception as e: logger.error(f"[{tier_name}] Error deleting msg {old_message_id}: {e}", exc_info=True)
                else: logger.warning(f"[{tier_name}] Tracked message ID missing for offline {actual_login}.")
                message_id_to_remove_from_tracker = actual_login

            # Scenario 3: Still LIVE - Update on interval
            elif is_live and is_currently_posted:
                if now.minute % 5 == 0:
                    logger.info(f"[{tier_name}] Updating notification for {actual_login} (Delete/Repost on minute {now.minute}).")
                    old_message_id = current_live_mapping.get(actual_login)
                    # Delete old (best effort)
                    if old_message_id:
                        try:
                            old_message = await channel.fetch_message(old_message_id); await old_message.delete()
                            logger.info(f"[{tier_name}] Deleted old msg {old_message_id} for update.")
                        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e: logger.warning(f"[{tier_name}] Failed to delete old msg {old_message_id} for update: {e}")
                        except Exception as e: logger.error(f"[{tier_name}] Error deleting old msg {old_message_id}: {e}")
                    else: logger.warning(f"[{tier_name}] Msg ID missing for update of {actual_login}. Will post new.")
                    # Post new
                    try:
                        live_embed = await create_live_embed(status, tier_name, now)
                        if live_embed:
                            message_text = f" {actual_login} ({tier_name}) is live!"
                            new_message = await channel.send(message_text, embed=live_embed)
                            live_messages[actual_login] = new_message.id # Update tracker with NEW ID
                            changes_made_to_tracker = True
                            logger.info(f"[{tier_name}] Reposted updated notification for {actual_login} (New Msg ID: {new_message.id})")
                        else: logger.error(f"[{tier_name}] Failed to create updated embed for {actual_login}.");
                    except discord.Forbidden: logger.error(f"[{tier_name}] Cannot repost message for {actual_login} (Forbidden).")
                    except discord.HTTPException as e: logger.error(f"[{tier_name}] HTTP error reposting msg for {actual_login}: {e.status} {e.text}")
                    except Exception as e: logger.error(f"[{tier_name}] Error reposting msg for {actual_login}: {e}", exc_info=True)

        except Exception as e: logger.error(f"[{tier_name}] Unexpected error in outer loop for {streamer_login}: {e}", exc_info=True)

        if message_id_to_remove_from_tracker:
            if message_id_to_remove_from_tracker in live_messages:
                del live_messages[message_id_to_remove_from_tracker]
                changes_made_to_tracker = True

    if changes_made_to_tracker:
        save_data(live_messages_file, live_messages)
        logger.info(f"[{tier_name}] Live messages file ({live_messages_file}) updated.")


# --- Main Check Function ---
async def perform_stream_check(bot_instance: commands.Bot):
    """Loads data for each tier and calls the tier-specific check function."""
    logger.info("Starting stream check cycle...")
    vip_streamers = load_data(VIP_STREAMERS_FILE)
    await check_and_notify_tier(bot_instance, "VIP", vip_streamers, VIP_CHANNEL_ID, LIVE_MESSAGES_VIP_FILE)
    mod_streamers = load_data(MOD_STREAMERS_FILE)
    await check_and_notify_tier(bot_instance, "Mod", mod_streamers, MOD_CHANNEL_ID, LIVE_MESSAGES_MOD_FILE)
    global last_successful_check_time
    last_successful_check_time = datetime.now(timezone.utc)
    logger.info("Stream check cycle completed for all tiers.")
    return True


# --- Bot Events ---
@bot.event
async def on_ready():
    logger.info(f'Logged in as {bot.user.name} (ID: {bot.user.id})')
    logger.info(f'discord.py version: {discord.__version__}')
    logger.info('Bot is ready. Starting background check loop.')
    check_streams.start()

@bot.event
async def on_command_error(ctx, error):
    """Handles errors globally, silences CheckFailure in guilds."""
    if isinstance(error, commands.CheckFailure) and ctx.guild is not None:
        logger.debug(f"Silently ignoring CheckFailure for '{ctx.command.name if ctx.command else 'Unknown'}' in guild {ctx.guild.id}")
        return
    if isinstance(error, commands.CommandNotFound):
        logger.debug(f"Command not found: {ctx.message.content}")
        return
    if not hasattr(ctx.command, 'on_error'):
        logger.error(f'Unhandled error in {ctx.command if ctx.command else "Unknown"}: {error}', exc_info=error)
        try:
            if ctx.guild is None: # Only reply in DMs for unhandled errors
                 await ctx.send("❌ An unexpected error occurred.")
        except Exception as e: logger.error(f"Failed to send generic error message: {e}")


# --- Custom Check for Allowed Users ---
def is_allowed_user():
    async def predicate(ctx):
        if not ALLOWED_USER_IDS: logger.warning(f"Cmd '{ctx.command.name}' denied for {ctx.author}: ALLOWED_USER_IDS empty."); return False
        is_allowed = ctx.author.id in ALLOWED_USER_IDS
        if not is_allowed: logger.warning(f"Unauthorized cmd '{ctx.command.name}' attempt by {ctx.author}")
        return is_allowed
    return commands.check(predicate)


# --- Bot Commands ---
# [COMMAND DEFINITIONS START HERE]

# --- VIP Streamer Commands ---
@bot.command(name='addvipstreamer', help='Adds a VIP Twitch streamer.')
@is_allowed_user()
async def add_vip_streamer(ctx, twitch_username: str):
    streamer_login = twitch_username.lower().strip()
    if not streamer_login: await ctx.send("⚠️ Invalid username."); return
    streamers = load_data(VIP_STREAMERS_FILE)
    if streamer_login in streamers: await ctx.send(f"`{streamer_login}` already on VIP list."); return
    streamers.append(streamer_login); save_data(VIP_STREAMERS_FILE, streamers)
    await ctx.send(f"✅ Added `{streamer_login}` to VIP watchlist.")
    logger.info(f"User {ctx.author} added VIP: {streamer_login}")

@add_vip_streamer.error
async def add_vip_streamer_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument): await ctx.send("Usage: `!addvipstreamer <username>`")
    elif isinstance(error, commands.CheckFailure): await ctx.send("🚫 Unauthorized.")
    else: logger.error(f"Error in addvipstreamer: {error}", exc_info=True); await ctx.send("❌ Error.")

@bot.command(name='removevipstreamer', help='Removes a VIP streamer.')
@is_allowed_user()
async def remove_vip_streamer(ctx, twitch_username: str):
    streamer_login = twitch_username.lower().strip()
    if not streamer_login: await ctx.send("⚠️ Invalid username."); return
    streamers = load_data(VIP_STREAMERS_FILE); live_messages = load_data(LIVE_MESSAGES_VIP_FILE)
    changes_made_live = False
    if streamer_login in streamers:
        streamers.remove(streamer_login); save_data(VIP_STREAMERS_FILE, streamers)
        channel = bot.get_channel(VIP_CHANNEL_ID)
        message_id = live_messages.pop(streamer_login, None)
        if message_id and channel:
            changes_made_live = True
            try: msg = await channel.fetch_message(message_id); await msg.delete(); logger.info(f"[VIP] Deleted msg {message_id}")
            except Exception as e: logger.warning(f"[VIP] Could not delete msg {message_id}: {e}")
        if changes_made_live: save_data(LIVE_MESSAGES_VIP_FILE, live_messages) # Save if popped or deletion attempted
        await ctx.send(f"🗑️ Removed `{streamer_login}` from VIP watchlist.")
        logger.info(f"User {ctx.author} removed VIP: {streamer_login}")
    else: await ctx.send(f"`{streamer_login}` not found on VIP list.")

@remove_vip_streamer.error
async def remove_vip_streamer_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument): await ctx.send("Usage: `!removevipstreamer <username>`")
    elif isinstance(error, commands.CheckFailure): await ctx.send("🚫 Unauthorized.")
    else: logger.error(f"Error in removevipstreamer: {error}", exc_info=True); await ctx.send("❌ Error.")

@bot.command(name='listvipstreamers', help='Lists watched VIP streamers.')
@is_allowed_user()
async def list_vip_streamers(ctx):
    streamers = load_data(VIP_STREAMERS_FILE)
    if not streamers: await ctx.send("VIP watchlist is empty.")
    else: await ctx.send(embed=discord.Embed(title="👑 VIP Watchlist", description="\n".join([f"- `{s}`" for s in sorted(streamers)]), color=discord.Color.gold()))

@list_vip_streamers.error
async def list_vip_streamers_error(ctx, error):
    if isinstance(error, commands.CheckFailure): await ctx.send("🚫 Unauthorized.")
    else: logger.error(f"Error in listvipstreamers: {error}", exc_info=True); await ctx.send("❌ Error.")

# --- Mod Streamer Commands ---
@bot.command(name='addmodstreamer', help='Adds a Mod Twitch streamer.')
@is_allowed_user()
async def add_mod_streamer(ctx, twitch_username: str):
    streamer_login = twitch_username.lower().strip()
    if not streamer_login: await ctx.send("⚠️ Invalid username."); return
    streamers = load_data(MOD_STREAMERS_FILE)
    if streamer_login in streamers: await ctx.send(f"`{streamer_login}` already on Mod list."); return
    streamers.append(streamer_login); save_data(MOD_STREAMERS_FILE, streamers)
    await ctx.send(f"✅ Added `{streamer_login}` to Mod watchlist.")
    logger.info(f"User {ctx.author} added Mod: {streamer_login}")

@add_mod_streamer.error
async def add_mod_streamer_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument): await ctx.send("Usage: `!addmodstreamer <username>`")
    elif isinstance(error, commands.CheckFailure): await ctx.send("🚫 Unauthorized.")
    else: logger.error(f"Error in addmodstreamer: {error}", exc_info=True); await ctx.send("❌ Error.")

@bot.command(name='removemodstreamer', help='Removes a Mod streamer.')
@is_allowed_user()
async def remove_mod_streamer(ctx, twitch_username: str):
    streamer_login = twitch_username.lower().strip()
    if not streamer_login: await ctx.send("⚠️ Invalid username."); return
    streamers = load_data(MOD_STREAMERS_FILE); live_messages = load_data(LIVE_MESSAGES_MOD_FILE)
    changes_made_live = False
    if streamer_login in streamers:
        streamers.remove(streamer_login); save_data(MOD_STREAMERS_FILE, streamers)
        channel = bot.get_channel(MOD_CHANNEL_ID)
        message_id = live_messages.pop(streamer_login, None)
        if message_id and channel:
            changes_made_live = True
            try: msg = await channel.fetch_message(message_id); await msg.delete(); logger.info(f"[Mod] Deleted msg {message_id}")
            except Exception as e: logger.warning(f"[Mod] Could not delete msg {message_id}: {e}")
        if changes_made_live: save_data(LIVE_MESSAGES_MOD_FILE, live_messages)
        await ctx.send(f"🗑️ Removed `{streamer_login}` from Mod watchlist.")
        logger.info(f"User {ctx.author} removed Mod: {streamer_login}")
    else: await ctx.send(f"`{streamer_login}` not found on Mod list.")

@remove_mod_streamer.error
async def remove_mod_streamer_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument): await ctx.send("Usage: `!removemodstreamer <username>`")
    elif isinstance(error, commands.CheckFailure): await ctx.send("🚫 Unauthorized.")
    else: logger.error(f"Error in removemodstreamer: {error}", exc_info=True); await ctx.send("❌ Error.")

@bot.command(name='listmodstreamers', help='Lists watched Mod streamers.')
@is_allowed_user()
async def list_mod_streamers(ctx):
    streamers = load_data(MOD_STREAMERS_FILE)
    if not streamers: await ctx.send("Mod watchlist is empty.")
    else: await ctx.send(embed=discord.Embed(title="🛡️ Mod Watchlist", description="\n".join([f"- `{s}`" for s in sorted(streamers)]), color=discord.Color.green()))

@list_mod_streamers.error
async def list_mod_streamers_error(ctx, error):
    if isinstance(error, commands.CheckFailure): await ctx.send("🚫 Unauthorized.")
    else: logger.error(f"Error in listmodstreamers: {error}", exc_info=True); await ctx.send("❌ Error.")

# --- Channel Configuration Commands ---
@bot.command(name='setvipchannel', help='Sets channel ID for VIP alerts.')
@is_allowed_user()
async def set_vip_channel(ctx, channel_id_str: str):
    global VIP_CHANNEL_ID
    try: new_channel_id = int(channel_id_str)
    except ValueError: await ctx.send("⚠️ Invalid Channel ID."); return
    target_channel = ctx.bot.get_channel(new_channel_id)
    if not target_channel or not isinstance(target_channel, discord.TextChannel): await ctx.send(f"⚠️ Invalid/inaccessible text channel ID `{new_channel_id}`."); return
    try:
        dotenv_path = find_dotenv() or os.path.join(os.path.dirname(__file__), '.env')
        if not os.path.exists(dotenv_path): logger.error("Could not find .env file."); await ctx.send("❌ Critical error: .env file missing."); return
        if set_key(dotenv_path, "VIP_CHANNEL_ID", str(new_channel_id)):
            VIP_CHANNEL_ID = new_channel_id; logger.info(f"VIP Channel set to {new_channel_id} by {ctx.author}")
            await ctx.send(f"✅ VIP channel set to {target_channel.mention}.")
        else: logger.error(f"Failed to update VIP_CHANNEL_ID in {dotenv_path}"); await ctx.send("❌ Error updating config.")
    except Exception as e: logger.error(f"Error setting VIP channel: {e}", exc_info=True); await ctx.send("❌ Error.")

@set_vip_channel.error
async def set_vip_channel_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument): await ctx.send("Usage: `!setvipchannel <channel_id>`")
    elif isinstance(error, commands.CheckFailure): await ctx.send("🚫 Unauthorized.")
    else: logger.error(f"Error in setvipchannel: {error}", exc_info=True); await ctx.send("❌ Error.")

@bot.command(name='setmodchannel', help='Sets channel ID for Mod alerts.')
@is_allowed_user()
async def set_mod_channel(ctx, channel_id_str: str):
    global MOD_CHANNEL_ID
    try: new_channel_id = int(channel_id_str)
    except ValueError: await ctx.send("⚠️ Invalid Channel ID."); return
    target_channel = ctx.bot.get_channel(new_channel_id)
    if not target_channel or not isinstance(target_channel, discord.TextChannel): await ctx.send(f"⚠️ Invalid/inaccessible text channel ID `{new_channel_id}`."); return
    try:
        dotenv_path = find_dotenv() or os.path.join(os.path.dirname(__file__), '.env')
        if not os.path.exists(dotenv_path): logger.error("Could not find .env file."); await ctx.send("❌ Critical error: .env file missing."); return
        if set_key(dotenv_path, "MOD_CHANNEL_ID", str(new_channel_id)):
            MOD_CHANNEL_ID = new_channel_id; logger.info(f"Mod Channel set to {new_channel_id} by {ctx.author}")
            await ctx.send(f"✅ Mod channel set to {target_channel.mention}.")
        else: logger.error(f"Failed to update MOD_CHANNEL_ID in {dotenv_path}"); await ctx.send("❌ Error updating config.")
    except Exception as e: logger.error(f"Error setting Mod channel: {e}", exc_info=True); await ctx.send("❌ Error.")

@set_mod_channel.error
async def set_mod_channel_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument): await ctx.send("Usage: `!setmodchannel <channel_id>`")
    elif isinstance(error, commands.CheckFailure): await ctx.send("🚫 Unauthorized.")
    else: logger.error(f"Error in setmodchannel: {error}", exc_info=True); await ctx.send("❌ Error.")

# --- User Management Commands ---
async def update_allowed_users_env():
    """Helper to update ALLOWED_USER_IDS in .env"""
    global ALLOWED_USER_IDS
    try:
        dotenv_path = find_dotenv() or os.path.join(os.path.dirname(__file__), '.env')
        if not os.path.exists(dotenv_path): logger.error("Cannot find .env to update allowed users."); return False
        ids_string = ",".join(map(str, sorted(list(ALLOWED_USER_IDS)))); success = set_key(dotenv_path, "ALLOWED_USER_IDS", ids_string)
        if not success: logger.error(f"Failed updating ALLOWED_USER_IDS in {dotenv_path}"); return False
        return True
    except Exception as e: logger.error(f"Error updating ALLOWED_USER_IDS: {e}", exc_info=True); return False

@bot.command(name='adduser', help='Authorizes a user ID to use commands.')
@is_allowed_user()
async def add_user(ctx, user_input: str):
    global ALLOWED_USER_IDS; user_id = None
    if user_input.startswith('<@') and user_input.endswith('>'):
        try: user_id = int(user_input.strip('<@!>'))
        except ValueError: pass
    if user_id is None:
        try: user_id = int(user_input)
        except ValueError: await ctx.send("⚠️ Invalid User ID or @mention."); return
    if user_id in ALLOWED_USER_IDS: await ctx.send(f"ID `{user_id}` already authorized."); return
    ALLOWED_USER_IDS.add(user_id)
    if await update_allowed_users_env(): logger.info(f"{ctx.author} added authorized user: {user_id}"); await ctx.send(f"✅ Added User ID `{user_id}`.")
    else: ALLOWED_USER_IDS.remove(user_id); await ctx.send("❌ Error saving config. User not added.")

@add_user.error
async def add_user_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument): await ctx.send("Usage: `!adduser <user_id_or_@mention>`")
    elif isinstance(error, commands.CheckFailure): await ctx.send("🚫 Unauthorized.")
    else: logger.error(f"Error in adduser: {error}", exc_info=True); await ctx.send("❌ Error.")

@bot.command(name='removeuser', help='Removes user ID authorization.')
@is_allowed_user()
async def remove_user(ctx, user_input: str):
    global ALLOWED_USER_IDS; user_id = None
    if user_input.startswith('<@') and user_input.endswith('>'):
        try: user_id = int(user_input.strip('<@!>'))
        except ValueError: pass
    if user_id is None:
        try: user_id = int(user_input)
        except ValueError: await ctx.send("⚠️ Invalid User ID or @mention."); return
    if len(ALLOWED_USER_IDS) <= 1 and user_id in ALLOWED_USER_IDS: await ctx.send("🚫 Cannot remove the last authorized user."); return
    if user_id not in ALLOWED_USER_IDS: await ctx.send(f"ID `{user_id}` not authorized."); return
    ALLOWED_USER_IDS.remove(user_id)
    if await update_allowed_users_env(): logger.info(f"{ctx.author} removed authorized user: {user_id}"); await ctx.send(f"✅ Removed User ID `{user_id}`.")
    else: ALLOWED_USER_IDS.add(user_id); await ctx.send("❌ Error saving config. User not removed.")

@remove_user.error
async def remove_user_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument): await ctx.send("Usage: `!removeuser <user_id_or_@mention>`")
    elif isinstance(error, commands.CheckFailure): await ctx.send("🚫 Unauthorized.")
    else: logger.error(f"Error in removeuser: {error}", exc_info=True); await ctx.send("❌ Error.")

# --- General / Status Commands ---
@bot.command(name='checknow', help='Manually triggers a stream check.')
@is_allowed_user()
async def check_now(ctx):
    await ctx.send("⏳ Kicking off manual stream check...")
    logger.info(f"Manual check triggered by {ctx.author}")
    if await perform_stream_check(bot): await ctx.send("✅ Manual check complete.")
    else: await ctx.send("⚠️ Manual check issue. Check logs.")

@check_now.error
async def check_now_error(ctx, error):
     if isinstance(error, commands.CheckFailure): await ctx.send("🚫 Unauthorized.")
     else: logger.error(f"Error in checknow: {error}", exc_info=True); await ctx.send("❌ Error.")

@bot.command(name='status', help='Checks the bot operational status.')
@is_allowed_user()
async def status(ctx):
    global last_successful_check_time; embed = discord.Embed(title="Bot Status", color=discord.Color.blue())
    loop_running = check_streams.is_running()
    embed.add_field(name="Loop Active?", value=f"{'✅ Yes' if loop_running else '❌ No'}", inline=False)
    if not loop_running:
        embed.color = discord.Color.orange()
        try: # Try get exception info if loop stopped
            task = check_streams.get_task(); exception = task.exception() if task else None
            if exception: embed.add_field(name="Loop Error", value=f"```\n{str(exception)[:1000]}\n```", inline=False); embed.color = discord.Color.red()
        except Exception: pass # Ignore errors getting loop exception
    if last_successful_check_time: embed.add_field(name="Last Check", value=f"<t:{int(last_successful_check_time.timestamp())}:R>", inline=True)
    else: embed.add_field(name="Last Check", value="N/A", inline=True)
    vip_streamers = load_data(VIP_STREAMERS_FILE); mod_streamers = load_data(MOD_STREAMERS_FILE)
    embed.add_field(name="VIPs", value=f"{len(vip_streamers)}", inline=True)
    embed.add_field(name="Mods", value=f"{len(mod_streamers)}", inline=True)
    embed.set_footer(text=f"Checked at: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z')}")
    await ctx.send(embed=embed)

@status.error
async def status_error(ctx, error):
    if isinstance(error, commands.CheckFailure): await ctx.send("🚫 Unauthorized.")
    else: logger.error(f"Error in status: {error}", exc_info=True); await ctx.send("❌ Error.")


# --- Custom Help Command (DEFINED LAST) ---
@bot.command(name='help', help='Shows available commands.')
@is_allowed_user()
async def custom_help(ctx):
    """Displays a custom help message listing all commands."""
    embed = discord.Embed(title="Bot Command Help", description="Commands only work via Direct Message (DM).", color=discord.Color.blurple())
    try:
        vip_cmds = f"`{bot.command_prefix}addvipstreamer <username>` - {add_vip_streamer.help}\n" \
                   f"`{bot.command_prefix}removevipstreamer <username>` - {remove_vip_streamer.help}\n" \
                   f"`{bot.command_prefix}listvipstreamers` - {list_vip_streamers.help}\n"
        embed.add_field(name="👑 VIP Streamer Management", value=vip_cmds, inline=False)

        mod_cmds = f"`{bot.command_prefix}addmodstreamer <username>` - {add_mod_streamer.help}\n" \
                   f"`{bot.command_prefix}removemodstreamer <username>` - {remove_mod_streamer.help}\n" \
                   f"`{bot.command_prefix}listmodstreamers` - {list_mod_streamers.help}\n"
        embed.add_field(name="🛡️ Mod Streamer Management", value=mod_cmds, inline=False)

        config_cmds = f"`{bot.command_prefix}setvipchannel <channel_id>` - {set_vip_channel.help}\n" \
                      f"`{bot.command_prefix}setmodchannel <channel_id>` - {set_mod_channel.help}\n" \
                      f"`{bot.command_prefix}adduser <user_id_or_@mention>` - {add_user.help}\n" \
                      f"`{bot.command_prefix}removeuser <user_id_or_@mention>` - {remove_user.help}\n"
        embed.add_field(name="⚙️ Configuration", value=config_cmds, inline=False)

        general_cmds = f"`{bot.command_prefix}checknow` - {check_now.help}\n" \
                       f"`{bot.command_prefix}status` - {status.help}\n" \
                       f"`{bot.command_prefix}help` - {custom_help.help}\n"
        embed.add_field(name="ℹ️ General / Status", value=general_cmds, inline=False)

        embed.set_footer(text="Commands only work when sent via Direct Message (DM).")
        await ctx.send(embed=embed)
    except NameError as e: logger.error(f"Help failed - command definition order issue?: {e}"); await ctx.send("❌ Error building help.")
    except Exception as e: logger.error(f"Unexpected help error: {e}", exc_info=True); await ctx.send("❌ Error generating help.")

@custom_help.error
async def custom_help_error(ctx, error):
    if isinstance(error, commands.CommandInvokeError): error = error.original
    if isinstance(error, commands.CheckFailure): await ctx.send("🚫 Unauthorized to use help.")
    else: logger.error(f"Error in help processing: {error}", exc_info=True); await ctx.send("❌ Error displaying help.")


# [COMMAND DEFINITIONS END HERE]


# --- Background Task ---
@tasks.loop(minutes=1.0)
async def check_streams():
    await perform_stream_check(bot)

@check_streams.before_loop
async def before_check_streams():
    await bot.wait_until_ready()
    logger.info("Bot ready, starting stream check loop.")

@check_streams.after_loop
async def after_check_streams():
    if check_streams.is_being_cancelled(): logger.info("Stream check loop cancelled.")
    elif check_streams.get_task() and check_streams.get_task().done() and check_streams.get_task().exception():
         task_exception = check_streams.get_task().exception()
         logger.error(f"Stream check loop stopped unexpectedly! Reason: {task_exception}", exc_info=task_exception)
    else: logger.warning("Stream check loop finished or state unclear.")


# --- Run the Bot ---
if __name__ == "__main__":
    if not all([DISCORD_TOKEN, TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET, VIP_CHANNEL_ID, MOD_CHANNEL_ID]):
        print("FATAL ERROR: Ensure DISCORD_TOKEN, TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET, VIP_CHANNEL_ID, MOD_CHANNEL_ID are set in .env")
        exit()
    else:
        try:
            print("Attempting to run bot...")
            bot.run(DISCORD_TOKEN, log_handler=None) # Use our handlers via logger setup
        except discord.LoginFailure: logger.critical("Login Failed: Invalid Discord Bot Token.")
        except discord.PrivilegedIntentsRequired: logger.critical("Intents Error: Missing required Privileged Gateway Intents.")
        except Exception as e: logger.critical(f"FATAL ERROR running bot: {e}", exc_info=True)

# --- END OF FILE bot.py ---