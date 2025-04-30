import discord
from discord.ext import commands, tasks
import requests
import json
import os
import asyncio
from dotenv import load_dotenv
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Set, Dict, Any

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

# Parse ALLOWED_USER_IDS (same as before)
ALLOWED_USER_IDS: Set[int] = set()
if ALLOWED_USER_IDS_STR:
    try:
        ALLOWED_USER_IDS = {int(uid.strip()) for uid in ALLOWED_USER_IDS_STR.split(',') if uid.strip()}
        print(f"Loaded {len(ALLOWED_USER_IDS)} allowed user IDs.")
    except ValueError:
        print("Error parsing ALLOWED_USER_IDS from .env file.")
else:
     print("Warning: No ALLOWED_USER_IDS defined in .env.")

# --- Logging Setup (same as before) ---
log_formatter = logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s')
log_file_handler = logging.FileHandler('bot.log', encoding='utf-8', mode='a')
log_file_handler.setFormatter(log_formatter)
# Console handler removed for systemd compatibility
# log_console_handler = logging.StreamHandler()
# log_console_handler.setFormatter(log_formatter)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(log_file_handler)
# logger.addHandler(log_console_handler) # Keep commented out

discord_logger = logging.getLogger('discord')
discord_logger.setLevel(logging.INFO)
discord_logger.addHandler(log_file_handler)
# discord_logger.addHandler(log_console_handler) # Keep commented out

tasks_logger = logging.getLogger('discord.ext.tasks')
tasks_logger.setLevel(logging.INFO)
tasks_logger.addHandler(log_file_handler)
# tasks_logger.addHandler(log_console_handler) # Keep commented out

# --- Bot Setup (same as before) ---
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.guilds = True
bot = commands.Bot(command_prefix='!', intents=intents)

# --- NEW: Data File Paths ---
VIP_STREAMERS_FILE = 'vip_streamers.json'
MOD_STREAMERS_FILE = 'mod_streamers.json'
LIVE_MESSAGES_VIP_FILE = 'live_messages_vip.json'
LIVE_MESSAGES_MOD_FILE = 'live_messages_mod.json'

# --- Helper Functions (load_data, save_data, get_twitch_app_access_token, get_stream_status are UNCHANGED) ---

def load_data(filename):
    """Loads data from a JSON file."""
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            # Handle empty files returning default structure
            content = f.read()
            if not content.strip():
                 if 'streamers' in filename: return []
                 if 'live_messages' in filename: return {}
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

# get_twitch_app_access_token() remains the same
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
        response = await asyncio.to_thread(requests.post, url, data=payload, timeout=10)
        response.raise_for_status()
        data = response.json()
        twitch_access_token = data['access_token']
        expires_in = data.get('expires_in', 3600)
        token_expiry_time = now + timedelta(seconds=expires_in)
        logger.info(f"Successfully obtained/refreshed Twitch App Access Token. Expires in ~{expires_in // 60} minutes.")
        return twitch_access_token
    except requests.exceptions.Timeout:
        logger.error("Timeout occurred while getting Twitch token.")
        return None
    except requests.exceptions.RequestException as e:
        response_text = getattr(e.response, 'text', 'No response text available')
        response_status = getattr(e.response, 'status_code', 'N/A')
        logger.error(f"Error getting Twitch token (Status: {response_status}): {e} - Response: {response_text}")
        twitch_access_token = None
        token_expiry_time = datetime.now(timezone.utc)
        return None
    except KeyError as e:
        response_text = getattr(response, 'text', 'No response text available')
        logger.error(f"Error parsing Twitch token response (Missing key: {e}): {response_text}")
        twitch_access_token = None
        token_expiry_time = datetime.now(timezone.utc)
        return None
    except Exception as e:
        logger.error(f"Unexpected error getting Twitch token: {e}", exc_info=True)
        twitch_access_token = None
        token_expiry_time = datetime.now(timezone.utc)
        return None

# get_stream_status() remains the same
async def get_stream_status(streamer_login):
    """Checks if a Twitch streamer is live using their login name."""
    token = await get_twitch_app_access_token()
    if not token:
        logger.warning(f"Cannot check status for {streamer_login}, no valid Twitch token available.")
        return None
    headers = {'Client-ID': TWITCH_CLIENT_ID, 'Authorization': f'Bearer {token}'}
    params = {'user_login': streamer_login}
    url = 'https://api.twitch.tv/helix/streams'
    try:
        response = await asyncio.to_thread(requests.get, url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get('data'):
            stream_info = data['data'][0]
            return {'live': True, 'title': stream_info.get('title', 'No Title'), 'game_name': stream_info.get('game_name', 'No Game'), 'viewer_count': stream_info.get('viewer_count', 0), 'thumbnail_url': stream_info.get('thumbnail_url', '').replace('{width}', '320').replace('{height}', '180')}
        else:
            return {'live': False}
    except requests.exceptions.Timeout:
        logger.error(f"Timeout occurred while checking stream status for {streamer_login}.")
        return None
    except requests.exceptions.RequestException as e:
        response_status = getattr(e.response, 'status_code', 'N/A')
        response_text = getattr(e.response, 'text', 'No response text available')
        logger.error(f"Error checking Twitch stream status for {streamer_login} (Status: {response_status}): {e} - Response: {response_text}")
        if response_status == 401:
             logger.warning(f"Twitch token might be invalid (401 Unauthorized) for {streamer_login}. Forcing refresh on next cycle.")
             global twitch_access_token
             twitch_access_token = None
             token_expiry_time = datetime.now(timezone.utc)
        elif response_status == 403: logger.warning(f"Twitch returned 403 Forbidden for {streamer_login}.")
        elif response_status == 429: logger.warning("Twitch API rate limit possibly hit.")
        return None
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        response_text = getattr(response, 'text', 'No response text available')
        logger.error(f"Error parsing Twitch stream data for {streamer_login}: {e} - Response: {response_text}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error checking stream status for {streamer_login}: {e}", exc_info=True)
        return None

# --- NEW: Refactored Tier Checking Logic ---
async def check_and_notify_tier(bot_instance: commands.Bot, tier_name: str, streamer_logins: List[str], target_channel_id: int, live_messages_file: str):
    """Checks a specific tier of streamers and handles notifications."""
    if not streamer_logins:
        # logger.info(f"No streamers to check for {tier_name} tier.")
        return # Nothing to do for this tier

    logger.info(f"Performing check for {tier_name} tier: {', '.join(streamer_logins)}")
    live_messages: Dict[str, int] = load_data(live_messages_file)
    channel = bot_instance.get_channel(target_channel_id)

    if not channel:
        logger.error(f"{tier_name} target channel ({target_channel_id}) not found. Cannot send/delete messages for this tier.")
        return # Cannot proceed without the channel

    current_live_mapping = live_messages.copy()
    changes_made = False

    for streamer_login in streamer_logins:
        try:
            status = await get_stream_status(streamer_login)
            await asyncio.sleep(0.2) # Pace API calls

            if status is None:
                logger.warning(f"[{tier_name}] Skipping update for {streamer_login} due to API/fetch error.")
                continue

            is_live = status.get('live', False)
            is_currently_posted = streamer_login in current_live_mapping

            # --- Scenario 1: Streamer went LIVE ---
            if is_live and not is_currently_posted:
                logger.info(f"[{tier_name}] {streamer_login} went LIVE!")
                try:
                    embed = discord.Embed(
                        title=f"🔴 {streamer_login} is now LIVE! ({tier_name.upper()})", # Indicate tier in title
                        url=f"https://twitch.tv/{streamer_login}",
                        description=status.get('title', 'No Title Provided'),
                        color=discord.Color.purple(),
                        timestamp=datetime.now(timezone.utc)
                    )
                    embed.add_field(name="Game", value=status.get('game_name', 'N/A'), inline=True)
                    embed.add_field(name="Viewers", value=f"{status.get('viewer_count', 'N/A'):,}", inline=True)
                    if status.get('thumbnail_url'):
                         embed.set_image(url=status['thumbnail_url'])
                    embed.set_footer(text="Click the title to watch!")
                    # Add specific ping/mention if desired for the tier
                    mention = "@everyone" # Customize per tier if needed
                    message = await channel.send(f"🎉 Hey {mention}! `{streamer_login}` ({tier_name}) just went live! 🎉", embed=embed, allowed_mentions=discord.AllowedMentions(everyone=True)) # Adjust allowed_mentions

                    live_messages[streamer_login] = message.id
                    changes_made = True
                    logger.info(f"[{tier_name}] Posted live notification for {streamer_login} (Message ID: {message.id})")

                except discord.Forbidden:
                    logger.error(f"[{tier_name}] Bot lacks permissions (Send Messages/Embed Links) in channel {target_channel_id}.")
                except discord.HTTPException as e:
                     logger.error(f"[{tier_name}] HTTP error sending live notification for {streamer_login}: {e.status} {e.text}")
                except Exception as e:
                    logger.error(f"[{tier_name}] Error sending live notification for {streamer_login}: {e}", exc_info=True)

            # --- Scenario 2: Streamer went OFFLINE ---
            elif not is_live and is_currently_posted:
                logger.info(f"[{tier_name}] {streamer_login} went OFFLINE.")
                message_id = current_live_mapping.get(streamer_login)
                if not message_id:
                     logger.warning(f"[{tier_name}] Tracked message ID missing for offline streamer {streamer_login}.")
                     if streamer_login in live_messages:
                         del live_messages[streamer_login]
                         changes_made = True
                     continue

                try:
                    # Fetch message using the specific channel object
                    message = await channel.fetch_message(message_id)
                    await message.delete()
                    logger.info(f"[{tier_name}] Deleted notification for {streamer_login} (Message ID: {message_id})")
                except discord.NotFound:
                    logger.warning(f"[{tier_name}] Message {message_id} for {streamer_login} not found.")
                except discord.Forbidden:
                    logger.error(f"[{tier_name}] Bot lacks permissions (Manage Messages) to delete message {message_id} in channel {target_channel_id}.")
                except discord.HTTPException as e:
                    logger.error(f"[{tier_name}] HTTP error deleting message {message_id} for {streamer_login}: {e.status} {e.text}")
                except Exception as e:
                    logger.error(f"[{tier_name}] Error deleting message {message_id} for {streamer_login}: {e}", exc_info=True)

                if streamer_login in live_messages:
                     del live_messages[streamer_login]
                     changes_made = True

        except Exception as e:
            logger.error(f"[{tier_name}] Unexpected error in check loop for {streamer_login}: {e}", exc_info=True)

    if changes_made:
        save_data(live_messages_file, live_messages)
        logger.info(f"[{tier_name}] Live messages file ({live_messages_file}) updated.")


# --- NEW: Main Check Function - Delegates to Tier Check ---
async def perform_stream_check(bot_instance: commands.Bot):
    """Loads data for each tier and calls the tier-specific check function."""

    # Check VIP Tier
    vip_streamers = load_data(VIP_STREAMERS_FILE)
    await check_and_notify_tier(bot_instance, "VIP", vip_streamers, VIP_CHANNEL_ID, LIVE_MESSAGES_VIP_FILE)

    # Check Mod Tier
    mod_streamers = load_data(MOD_STREAMERS_FILE)
    await check_and_notify_tier(bot_instance, "Mod", mod_streamers, MOD_CHANNEL_ID, LIVE_MESSAGES_MOD_FILE)

    # Update last successful check time only after attempting both tiers
    global last_successful_check_time
    last_successful_check_time = datetime.now(timezone.utc)
    logger.info("Stream check cycle completed for all tiers.")
    return True # Indicate overall cycle completed


# --- Bot Events (on_ready is UNCHANGED) ---
@bot.event
async def on_ready():
    """Event triggered when the bot is connected and ready."""
    logger.info(f'Logged in as {bot.user.name} (ID: {bot.user.id})')
    logger.info(f'discord.py version: {discord.__version__}')
    logger.info('Bot is ready. Starting background check loop.')
    check_streams.start()

# --- Custom Check for Allowed Users (is_allowed_user is UNCHANGED) ---
def is_allowed_user():
    async def predicate(ctx):
        if not ALLOWED_USER_IDS:
            logger.warning(f"Command '{ctx.command.name}' invoked by {ctx.author} ({ctx.author.id}), but ALLOWED_USER_IDS is empty. Denying.")
            return False
        is_allowed = ctx.author.id in ALLOWED_USER_IDS
        if not is_allowed:
            logger.warning(f"Unauthorized command attempt for '{ctx.command.name}' by {ctx.author} ({ctx.author.id})")
        return is_allowed
    return commands.check(predicate)

# --- Bot Commands ---

# --- NEW: VIP Streamer Commands ---
@bot.command(name='addvipstreamer', help='Adds a VIP Twitch streamer. Usage: !addvipstreamer <username>')
@is_allowed_user()
async def add_vip_streamer(ctx, twitch_username: str):
    streamer_login = twitch_username.lower().strip()
    if not streamer_login: await ctx.send("⚠️ Please provide a valid Twitch username."); return
    streamers = load_data(VIP_STREAMERS_FILE)
    if streamer_login in streamers: await ctx.send(f"`{streamer_login}` is already on the VIP list."); return
    # Optional: Check if already on Mod list?
    streamers.append(streamer_login)
    save_data(VIP_STREAMERS_FILE, streamers)
    await ctx.send(f"✅ Added `{streamer_login}` to the VIP watchlist.")
    logger.info(f"User {ctx.author} added VIP streamer: {streamer_login}")

@add_vip_streamer.error
async def add_vip_streamer_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument): await ctx.send("Usage: `!addvipstreamer <twitch_username>`")
    elif isinstance(error, commands.CheckFailure): await ctx.send("🚫 You are not authorized.")
    else: logger.error(f"Error in addvipstreamer: {error}", exc_info=True); await ctx.send("❌ Error.")

@bot.command(name='removevipstreamer', help='Removes a VIP streamer. Usage: !removevipstreamer <username>')
@is_allowed_user()
async def remove_vip_streamer(ctx, twitch_username: str):
    streamer_login = twitch_username.lower().strip()
    if not streamer_login: await ctx.send("⚠️ Please provide a valid Twitch username."); return
    streamers = load_data(VIP_STREAMERS_FILE)
    live_messages = load_data(LIVE_MESSAGES_VIP_FILE)
    changes_made_live = False
    if streamer_login in streamers:
        streamers.remove(streamer_login)
        save_data(VIP_STREAMERS_FILE, streamers)
        channel = bot.get_channel(VIP_CHANNEL_ID)
        message_id = live_messages.pop(streamer_login, None)
        if message_id and channel:
            changes_made_live = True
            try:
                message = await channel.fetch_message(message_id); await message.delete()
                logger.info(f"[VIP] Deleted message {message_id} for removed {streamer_login}")
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e: logger.warning(f"[VIP] Could not delete msg {message_id}: {e}")
            except Exception as e: logger.error(f"[VIP] Error deleting msg {message_id}: {e}", exc_info=True)
        elif streamer_login in live_messages: changes_made_live = True # Needs saving even if deletion failed/skipped
        if changes_made_live: save_data(LIVE_MESSAGES_VIP_FILE, live_messages)
        await ctx.send(f"🗑️ Removed `{streamer_login}` from the VIP watchlist.")
        logger.info(f"User {ctx.author} removed VIP streamer: {streamer_login}")
    else: await ctx.send(f"`{streamer_login}` not found on the VIP list.")

@remove_vip_streamer.error
async def remove_vip_streamer_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument): await ctx.send("Usage: `!removevipstreamer <twitch_username>`")
    elif isinstance(error, commands.CheckFailure): await ctx.send("🚫 You are not authorized.")
    else: logger.error(f"Error in removevipstreamer: {error}", exc_info=True); await ctx.send("❌ Error.")

@bot.command(name='listvipstreamers', help='Lists watched VIP streamers.')
@is_allowed_user()
async def list_vip_streamers(ctx):
    streamers = load_data(VIP_STREAMERS_FILE)
    if not streamers: await ctx.send("The VIP watchlist is empty.")
    else:
        embed = discord.Embed(title="👑 VIP Watchlist Streamers", description="\n".join([f"- `{s}`" for s in sorted(streamers)]), color=discord.Color.gold())
        await ctx.send(embed=embed)

@list_vip_streamers.error
async def list_vip_streamers_error(ctx, error):
    if isinstance(error, commands.CheckFailure): await ctx.send("🚫 You are not authorized.")
    else: logger.error(f"Error in listvipstreamers: {error}", exc_info=True); await ctx.send("❌ Error.")

# --- NEW: Mod Streamer Commands (Similar to VIP) ---
@bot.command(name='addmodstreamer', help='Adds a Mod Twitch streamer. Usage: !addmodstreamer <username>')
@is_allowed_user()
async def add_mod_streamer(ctx, twitch_username: str):
    streamer_login = twitch_username.lower().strip()
    if not streamer_login: await ctx.send("⚠️ Please provide a valid Twitch username."); return
    streamers = load_data(MOD_STREAMERS_FILE)
    if streamer_login in streamers: await ctx.send(f"`{streamer_login}` is already on the Mod list."); return
    # Optional: Check if already on VIP list?
    streamers.append(streamer_login)
    save_data(MOD_STREAMERS_FILE, streamers)
    await ctx.send(f"✅ Added `{streamer_login}` to the Mod watchlist.")
    logger.info(f"User {ctx.author} added Mod streamer: {streamer_login}")

@add_mod_streamer.error
async def add_mod_streamer_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument): await ctx.send("Usage: `!addmodstreamer <twitch_username>`")
    elif isinstance(error, commands.CheckFailure): await ctx.send("🚫 You are not authorized.")
    else: logger.error(f"Error in addmodstreamer: {error}", exc_info=True); await ctx.send("❌ Error.")

@bot.command(name='removemodstreamer', help='Removes a Mod streamer. Usage: !removemodstreamer <username>')
@is_allowed_user()
async def remove_mod_streamer(ctx, twitch_username: str):
    streamer_login = twitch_username.lower().strip()
    if not streamer_login: await ctx.send("⚠️ Please provide a valid Twitch username."); return
    streamers = load_data(MOD_STREAMERS_FILE)
    live_messages = load_data(LIVE_MESSAGES_MOD_FILE)
    changes_made_live = False
    if streamer_login in streamers:
        streamers.remove(streamer_login)
        save_data(MOD_STREAMERS_FILE, streamers)
        channel = bot.get_channel(MOD_CHANNEL_ID)
        message_id = live_messages.pop(streamer_login, None)
        if message_id and channel:
            changes_made_live = True
            try:
                message = await channel.fetch_message(message_id); await message.delete()
                logger.info(f"[Mod] Deleted message {message_id} for removed {streamer_login}")
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e: logger.warning(f"[Mod] Could not delete msg {message_id}: {e}")
            except Exception as e: logger.error(f"[Mod] Error deleting msg {message_id}: {e}", exc_info=True)
        elif streamer_login in live_messages: changes_made_live = True
        if changes_made_live: save_data(LIVE_MESSAGES_MOD_FILE, live_messages)
        await ctx.send(f"🗑️ Removed `{streamer_login}` from the Mod watchlist.")
        logger.info(f"User {ctx.author} removed Mod streamer: {streamer_login}")
    else: await ctx.send(f"`{streamer_login}` not found on the Mod list.")

@remove_mod_streamer.error
async def remove_mod_streamer_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument): await ctx.send("Usage: `!removemodstreamer <twitch_username>`")
    elif isinstance(error, commands.CheckFailure): await ctx.send("🚫 You are not authorized.")
    else: logger.error(f"Error in removemodstreamer: {error}", exc_info=True); await ctx.send("❌ Error.")

@bot.command(name='listmodstreamers', help='Lists watched Mod streamers.')
@is_allowed_user()
async def list_mod_streamers(ctx):
    streamers = load_data(MOD_STREAMERS_FILE)
    if not streamers: await ctx.send("The Mod watchlist is empty.")
    else:
        embed = discord.Embed(title="🛡️ Mod Watchlist Streamers", description="\n".join([f"- `{s}`" for s in sorted(streamers)]), color=discord.Color.green()) # Mod color
        await ctx.send(embed=embed)

@list_mod_streamers.error
async def list_mod_streamers_error(ctx, error):
    if isinstance(error, commands.CheckFailure): await ctx.send("🚫 You are not authorized.")
    else: logger.error(f"Error in listmodstreamers: {error}", exc_info=True); await ctx.send("❌ Error.")


# --- UPDATED: Status Command ---
@bot.command(name='status', help='Checks the bot\'s operational status.')
@is_allowed_user()
async def status(ctx):
    global last_successful_check_time
    embed = discord.Embed(title="Bot Status Report", color=discord.Color.blue())
    loop_running = check_streams.is_running()
    embed.add_field(name="Stream Check Loop Active?", value=f"{'✅ Yes' if loop_running else '❌ No'}", inline=False) # Changed to False inline for clarity
    if not loop_running:
        embed.color = discord.Color.orange()
        try:
            exception = check_streams.get_task().exception()
            if exception:
                embed.add_field(name="Loop Error", value=f"```\n{str(exception)[:1000]}\n```", inline=False)
                embed.color = discord.Color.red()
        except Exception: pass
    if last_successful_check_time:
        timestamp_str = f"<t:{int(last_successful_check_time.timestamp())}:R>"
        embed.add_field(name="Last Successful Check Cycle", value=timestamp_str, inline=True)
    else:
        embed.add_field(name="Last Successful Check Cycle", value="N/A", inline=True)

    # Report counts per tier
    vip_streamers = load_data(VIP_STREAMERS_FILE)
    mod_streamers = load_data(MOD_STREAMERS_FILE)
    embed.add_field(name="VIP Watchlist Size", value=f"{len(vip_streamers)}", inline=True)
    embed.add_field(name="Mod Watchlist Size", value=f"{len(mod_streamers)}", inline=True)

    embed.set_footer(text=f"Checked at: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z')}")
    await ctx.send(embed=embed)

@status.error # Error handler remains the same
async def status_error(ctx, error):
    if isinstance(error, commands.CheckFailure): await ctx.send("🚫 You are not authorized.")
    else: logger.error(f"Error in status command: {error}", exc_info=True); await ctx.send("❌ Error.")


# --- Background Task (check_streams and helpers are UNCHANGED from previous state) ---
@tasks.loop(minutes=1.0)
async def check_streams():
    """Background task wrapper for perform_stream_check."""
    await perform_stream_check(bot)

@check_streams.before_loop
async def before_check_streams():
    await bot.wait_until_ready()
    logger.info("Bot is ready, background stream check loop starting.")

@check_streams.after_loop
async def after_check_streams():
    if check_streams.is_being_cancelled(): logger.info("Stream check loop cancelled.")
    else: logger.error(f"Stream check loop stopped unexpectedly! Reason: {check_streams.get_task().exception()}")


# --- Run the Bot (Mostly UNCHANGED, check env vars) ---
if __name__ == "__main__":
    # Check essential credentials including NEW channel IDs
    if not all([DISCORD_TOKEN, TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET, VIP_CHANNEL_ID, MOD_CHANNEL_ID]):
        print("FATAL ERROR: One or more required environment variables (DISCORD_TOKEN, TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET, VIP_CHANNEL_ID, MOD_CHANNEL_ID) are missing or invalid in .env")
        exit()
    else:
        try:
            print("Attempting to run bot...")
            bot.run(DISCORD_TOKEN, log_handler=None)
        except discord.LoginFailure: logger.critical("Login Failed: Invalid Discord Bot Token.")
        except discord.PrivilegedIntentsRequired: logger.critical("Intents Error: Missing required Privileged Gateway Intents.")
        except Exception as e: logger.critical(f"FATAL ERROR running bot: {e}", exc_info=True)