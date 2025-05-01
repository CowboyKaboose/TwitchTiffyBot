import discord
from discord.ext import commands, tasks
import requests
import json
import os
import asyncio
from dotenv import load_dotenv, set_key, find_dotenv
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
# ----> ADD help_command=None <----
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# --- NEW: Global Check for DMs Only ---
@bot.check
async def globally_block_guilds(ctx):
    """Prevents commands from running in server channels (Guilds)."""
    # ctx.guild will be None if the command is in a DM
    # Return True if it's a DM (guild is None), False otherwise
    is_dm = ctx.guild is None
    if not is_dm:
        # Optionally log that a command was ignored in a guild
        logger.debug(f"Command '{ctx.command.name if ctx.command else 'Unknown'}' ignored in guild {ctx.guild.id} by {ctx.author} ({ctx.author.id})")
    return is_dm

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

# --- Refactored Tier Checking Logic ---
async def check_and_notify_tier(bot_instance: commands.Bot, tier_name: str, streamer_logins: List[str], target_channel_id: int, live_messages_file: str):
    """Checks a specific tier of streamers and handles notifications, including updates."""
    if not streamer_logins: return

    # --- Load live messages. Ensure it's a dictionary. ---
    live_messages: Dict[str, int] = load_data(live_messages_file)
    # Basic type check in case the file was corrupted/empty incorrectly
    if not isinstance(live_messages, dict):
        logger.error(f"[{tier_name}] Live messages data in {live_messages_file} is not a dictionary. Resetting. Data: {live_messages}")
        live_messages = {} # Reset to avoid errors
        save_data(live_messages_file, live_messages) # Save the reset state

    logger.info(f"Performing check for {tier_name} tier: {', '.join(streamer_logins)}")
    channel = bot_instance.get_channel(target_channel_id)

    if not channel:
        logger.error(f"{tier_name} target channel ({target_channel_id}) not found.")
        return

    current_live_mapping = live_messages.copy()
    changes_made = False
    now = datetime.now(timezone.utc) # Get current time once per check cycle

    for streamer_login in streamer_logins:
        message_id_to_delete_later = None # Flag if deletion needed after loop iteration
        try:
            status = await get_stream_status(streamer_login)
            await asyncio.sleep(0.2)

            if status is None:
                logger.warning(f"[{tier_name}] Skipping update for {streamer_login} due to API/fetch error.")
                continue

            is_live = status.get('live', False)
            is_currently_posted = streamer_login in current_live_mapping

            # --- Scenario 1: Streamer went LIVE ---
            if is_live and not is_currently_posted:
                # --- (Code for posting NEW message - UNCHANGED) ---
                logger.info(f"[{tier_name}] {streamer_login} went LIVE!")
                try:
                    embed = discord.Embed(
                        title=f"🔴 {streamer_login} is now LIVE! ({tier_name.upper()})",
                        url=f"https://twitch.tv/{streamer_login}",
                        description=status.get('title', 'No Title Provided'),
                        color=discord.Color.purple(), timestamp=now
                    )
                    embed.add_field(name="Game", value=status.get('game_name', 'N/A'), inline=True)
                    embed.add_field(name="Viewers", value=f"{status.get('viewer_count', 'N/A'):,}", inline=True)
                    if status.get('thumbnail_url'): embed.set_image(url=status['thumbnail_url'])
                    embed.set_footer(text="Click the title to watch!")
                    mention = "@everyone" # Customize per tier if needed
                    message = await channel.send(f"🎉 Hey {mention}! `{streamer_login}` ({tier_name}) just went live! 🎉", embed=embed, allowed_mentions=discord.AllowedMentions(everyone=True))

                    live_messages[streamer_login] = message.id # Store the ID
                    changes_made = True
                    logger.info(f"[{tier_name}] Posted live notification for {streamer_login} (Message ID: {message.id})")
                except discord.Forbidden: logger.error(f"[{tier_name}] Bot lacks permissions (Send/Embed) in {target_channel_id}.")
                except discord.HTTPException as e: logger.error(f"[{tier_name}] HTTP error sending msg for {streamer_login}: {e.status} {e.text}")
                except Exception as e: logger.error(f"[{tier_name}] Error sending msg for {streamer_login}: {e}", exc_info=True)
            # --- End Scenario 1 ---

            # --- Scenario 2: Streamer went OFFLINE ---
            elif not is_live and is_currently_posted:
                # --- (Code for DELETING message - UNCHANGED, but ensure it removes from live_messages dict) ---
                logger.info(f"[{tier_name}] {streamer_login} went OFFLINE.")
                message_id = current_live_mapping.get(streamer_login)
                if not message_id:
                     logger.warning(f"[{tier_name}] Tracked message ID missing for offline {streamer_login}.")
                     if streamer_login in live_messages: del live_messages[streamer_login]; changes_made = True
                     continue
                try:
                    message = await channel.fetch_message(message_id)
                    await message.delete()
                    logger.info(f"[{tier_name}] Deleted notification for {streamer_login} (Message ID: {message_id})")
                except discord.NotFound: logger.warning(f"[{tier_name}] Message {message_id} for {streamer_login} not found.")
                except discord.Forbidden: logger.error(f"[{tier_name}] Bot lacks permissions (Manage Msgs) in {target_channel_id}.")
                except discord.HTTPException as e: logger.error(f"[{tier_name}] HTTP error deleting msg {message_id}: {e.status} {e.text}")
                except Exception as e: logger.error(f"[{tier_name}] Error deleting msg {message_id}: {e}", exc_info=True)
                # Remove from live messages regardless of deletion success
                if streamer_login in live_messages: del live_messages[streamer_login]; changes_made = True
            # --- End Scenario 2 ---

            # --- Scenario 3: Streamer is still LIVE ---
            elif is_live and is_currently_posted:
                # Check if roughly 5 minutes have passed (using current minute)
                if now.minute % 5 == 0: # Trigger update approx every 5 minutes
                    logger.info(f"[{tier_name}] Updating embed for {streamer_login} (triggered on minute {now.minute}).")
                    message_id = current_live_mapping.get(streamer_login)
                    if not message_id:
                         logger.warning(f"[{tier_name}] Tracked message ID missing for live update {streamer_login}.")
                         if streamer_login in live_messages: del live_messages[streamer_login]; changes_made = True
                         continue
                    try:
                        message = await channel.fetch_message(message_id)
                        # Create a new embed with the latest info (title, game, viewers, thumbnail)
                        # Use the 'status' variable fetched at the start of this loop iteration
                        new_embed = discord.Embed(
                            title=f"🔴 {streamer_login} is LIVE! ({tier_name.upper()})",
                            url=f"https://twitch.tv/{streamer_login}",
                            description=status.get('title', 'No Title Provided'),
                            color=discord.Color.purple(),
                            timestamp=message.created_at # Keep original post time or use now? Let's keep original.
                        )
                        new_embed.add_field(name="Game", value=status.get('game_name', 'N/A'), inline=True)
                        new_embed.add_field(name="Viewers", value=f"{status.get('viewer_count', 'N/A'):,}", inline=True)
                        if status.get('thumbnail_url'): new_embed.set_image(url=status['thumbnail_url'])
                        else: new_embed.set_image(url=discord.Embed.Empty) # Clear image if not available
                        new_embed.set_footer(text="Click the title to watch! (Updated)")

                        await message.edit(embed=new_embed)
                        logger.info(f"[{tier_name}] Successfully updated embed for {streamer_login} (Message ID: {message_id})")

                    except discord.NotFound:
                        logger.warning(f"[{tier_name}] Message {message_id} for {streamer_login} not found during update. Removing from tracker.")
                        # Mark for deletion outside the except block to avoid modifying dict during iteration issues potentially
                        message_id_to_delete_later = streamer_login
                    except discord.Forbidden:
                        logger.error(f"[{tier_name}] Bot lacks permissions (Embed Links?) to edit msg {message_id} in {target_channel_id}.")
                        # Should we remove from tracker if we can't edit? Maybe not, try again later.
                    except discord.HTTPException as e:
                        logger.error(f"[{tier_name}] HTTP error editing msg {message_id} for {streamer_login}: {e.status} {e.text}")
                    except Exception as e:
                        logger.error(f"[{tier_name}] Error editing msg {message_id} for {streamer_login}: {e}", exc_info=True)
            # --- End Scenario 3 ---

            # --- Scenario 4: Streamer is still OFFLINE ---
            # elif not is_live and not is_currently_posted:
            #     pass # No action needed

        except Exception as e:
            logger.error(f"[{tier_name}] Unexpected error in outer check loop for {streamer_login}: {e}", exc_info=True)

        # Handle deletion marked earlier if message vanished during update attempt
        if message_id_to_delete_later:
            if message_id_to_delete_later in live_messages:
                del live_messages[message_id_to_delete_later]
                changes_made = True


    # Save changes only once after processing all streamers for the tier
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

# --- NEW: Global Error Handler ---
@bot.event
async def on_command_error(ctx, error):
    """Handles errors globally, silences CheckFailure in guilds."""
    # Check if the error is CheckFailure AND the command was used in a guild
    if isinstance(error, commands.CheckFailure) and ctx.guild is not None:
        # The globally_block_guilds check failed. Do nothing.
        logger.debug(f"Silently ignoring CheckFailure for command '{ctx.command.name if ctx.command else 'Unknown'}' in guild {ctx.guild.id}")
        return  # Prevents further error handling for this specific case

    # --- Optional: Handle CommandNotFound silently globally ---
    # Uncomment the following lines if you ALSO want to silence "Command not found" errors
    # if isinstance(error, commands.CommandNotFound):
    #     logger.debug(f"Command not found: {ctx.message.content}")
    #     return # Silently ignore invalid commands

    # If it's not a CheckFailure in a guild (or CommandNotFound if enabled above),
    # let other error handlers (like the ones defined with @command.error)
    # or the default library behavior handle it.
    # We can log unhandled errors here for debugging.
    if not hasattr(ctx.command, 'on_error'): # Log only if no specific error handler exists
        logger.error(f'Unhandled command error in command {ctx.command if ctx.command else "Unknown"}: {error}', exc_info=error)
    # else: The specific error handler defined for the command will handle it.

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

# --- VIP Streamer Commands (Define these first) ---
@bot.command(name='addvipstreamer', help='Adds a VIP Twitch streamer. Usage: !addvipstreamer <username>')
@is_allowed_user()
async def add_vip_streamer(ctx, twitch_username: str):
    streamer_login = twitch_username.lower().strip()
    if not streamer_login: await ctx.send("⚠️ Please provide a valid Twitch username."); return
    streamers = load_data(VIP_STREAMERS_FILE)
    if streamer_login in streamers: await ctx.send(f"`{streamer_login}` is already on the VIP list."); return
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
        elif streamer_login in live_messages: changes_made_live = True
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

# --- Mod Streamer Commands (Define these next) ---
@bot.command(name='addmodstreamer', help='Adds a Mod Twitch streamer. Usage: !addmodstreamer <username>')
@is_allowed_user()
async def add_mod_streamer(ctx, twitch_username: str):
    streamer_login = twitch_username.lower().strip()
    if not streamer_login: await ctx.send("⚠️ Please provide a valid Twitch username."); return
    streamers = load_data(MOD_STREAMERS_FILE)
    if streamer_login in streamers: await ctx.send(f"`{streamer_login}` is already on the Mod list."); return
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

# --- Channel Configuration Commands (Define these next) ---
@bot.command(name='setvipchannel', help='Sets the channel ID for VIP notifications. Usage: !setvipchannel <channel_id>')
@is_allowed_user()
async def set_vip_channel(ctx, channel_id_str: str):
    """Sets the VIP notification channel ID persistently."""
    global VIP_CHANNEL_ID
    try: new_channel_id = int(channel_id_str)
    except ValueError: await ctx.send("⚠️ Invalid input. Please provide a valid channel ID (numbers only)."); return
    target_channel = ctx.bot.get_channel(new_channel_id)
    if target_channel is None: await ctx.send(f"⚠️ Cannot find channel with ID `{new_channel_id}` or bot lacks access."); return
    if not isinstance(target_channel, discord.TextChannel): await ctx.send(f"⚠️ Channel `{target_channel.name}` (ID: {new_channel_id}) is not a text channel."); return
    try:
        dotenv_path = find_dotenv()
        if not dotenv_path: dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
        if not os.path.exists(dotenv_path): logger.error("Could not find .env file path."); await ctx.send("❌ Critical error: Could not locate .env file."); return
        success = set_key(dotenv_path, "VIP_CHANNEL_ID", str(new_channel_id))
        if success:
            VIP_CHANNEL_ID = new_channel_id
            logger.info(f"VIP Channel ID updated to {new_channel_id} by {ctx.author}")
            await ctx.send(f"✅ VIP notification channel set to {target_channel.mention} (ID: `{new_channel_id}`). Change active immediately.")
        else: logger.error(f"Failed to update VIP_CHANNEL_ID in {dotenv_path}"); await ctx.send("❌ Error updating config file.")
    except Exception as e: logger.error(f"Error setting VIP channel ID: {e}", exc_info=True); await ctx.send("❌ Error updating channel config.")

@set_vip_channel.error
async def set_vip_channel_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument): await ctx.send("Usage: `!setvipchannel <channel_id>`")
    elif isinstance(error, commands.CheckFailure): await ctx.send("🚫 You are not authorized.")
    else: logger.error(f"Error in setvipchannel: {error}", exc_info=True); await ctx.send("❌ Error.")

@bot.command(name='setmodchannel', help='Sets the channel ID for Mod notifications. Usage: !setmodchannel <channel_id>')
@is_allowed_user()
async def set_mod_channel(ctx, channel_id_str: str):
    """Sets the Mod notification channel ID persistently."""
    global MOD_CHANNEL_ID
    try: new_channel_id = int(channel_id_str)
    except ValueError: await ctx.send("⚠️ Invalid input. Please provide a valid channel ID."); return
    target_channel = ctx.bot.get_channel(new_channel_id)
    if target_channel is None: await ctx.send(f"⚠️ Cannot find channel with ID `{new_channel_id}` or bot lacks access."); return
    if not isinstance(target_channel, discord.TextChannel): await ctx.send(f"⚠️ Channel `{target_channel.name}` is not a text channel."); return
    try:
        dotenv_path = find_dotenv()
        if not dotenv_path: dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
        if not os.path.exists(dotenv_path): logger.error("Could not find .env path."); await ctx.send("❌ Critical error: Could not locate .env file."); return
        success = set_key(dotenv_path, "MOD_CHANNEL_ID", str(new_channel_id))
        if success:
            MOD_CHANNEL_ID = new_channel_id
            logger.info(f"Mod Channel ID updated to {new_channel_id} by {ctx.author}")
            await ctx.send(f"✅ Mod notification channel set to {target_channel.mention} (ID: `{new_channel_id}`). Change active immediately.")
        else: logger.error(f"Failed to update MOD_CHANNEL_ID in {dotenv_path}"); await ctx.send("❌ Error updating config file.")
    except Exception as e: logger.error(f"Error setting Mod channel ID: {e}", exc_info=True); await ctx.send("❌ Error updating channel config.")

@set_mod_channel.error
async def set_mod_channel_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument): await ctx.send("Usage: `!setmodchannel <channel_id>`")
    elif isinstance(error, commands.CheckFailure): await ctx.send("🚫 You are not authorized.")
    else: logger.error(f"Error in setmodchannel: {error}", exc_info=True); await ctx.send("❌ Error.")

# --- User Management Commands (Define these next) ---
async def update_allowed_users_env():
    """Helper function to update the ALLOWED_USER_IDS string in .env"""
    global ALLOWED_USER_IDS
    try:
        dotenv_path = find_dotenv()
        if not dotenv_path: dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
        if not os.path.exists(dotenv_path): logger.error("Could not find .env path to update allowed users."); return False
        ids_string = ",".join(map(str, sorted(list(ALLOWED_USER_IDS))))
        success = set_key(dotenv_path, "ALLOWED_USER_IDS", ids_string)
        if not success: logger.error(f"Failed to update ALLOWED_USER_IDS in {dotenv_path}"); return False
        return True
    except Exception as e: logger.error(f"Error updating ALLOWED_USER_IDS in .env: {e}", exc_info=True); return False

@bot.command(name='adduser', help='Adds a user to the authorized list. Usage: !adduser <user_id_or_@mention>')
@is_allowed_user()
async def add_user(ctx, user_input: str):
    """Adds a user ID to the allowed list and updates .env"""
    global ALLOWED_USER_IDS
    user_id = None
    if user_input.startswith('<@') and user_input.endswith('>'):
        try: user_id = int(user_input.strip('<@!>'))
        except ValueError: pass
    if user_id is None:
        try: user_id = int(user_input)
        except ValueError: await ctx.send("⚠️ Invalid input. Use User ID or @mention."); return
    if user_id in ALLOWED_USER_IDS: await ctx.send(f"User ID `{user_id}` is already authorized."); return
    ALLOWED_USER_IDS.add(user_id)
    if await update_allowed_users_env():
        logger.info(f"User {ctx.author} added authorized user ID: {user_id}")
        await ctx.send(f"✅ Added User ID `{user_id}` to authorized list.")
    else: ALLOWED_USER_IDS.remove(user_id); await ctx.send("❌ Error updating config file. User not added.")

@add_user.error
async def add_user_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument): await ctx.send("Usage: `!adduser <user_id_or_@mention>`")
    elif isinstance(error, commands.CheckFailure): await ctx.send("🚫 You are not authorized.")
    else: logger.error(f"Error in adduser: {error}", exc_info=True); await ctx.send("❌ Error.")

@bot.command(name='removeuser', help='Removes a user from the authorized list. Usage: !removeuser <user_id_or_@mention>')
@is_allowed_user()
async def remove_user(ctx, user_input: str):
    """Removes a user ID from the allowed list and updates .env"""
    global ALLOWED_USER_IDS
    user_id = None
    if user_input.startswith('<@') and user_input.endswith('>'):
        try: user_id = int(user_input.strip('<@!>'))
        except ValueError: pass
    if user_id is None:
        try: user_id = int(user_input)
        except ValueError: await ctx.send("⚠️ Invalid input. Use User ID or @mention."); return
    if len(ALLOWED_USER_IDS) <= 1 and user_id in ALLOWED_USER_IDS: await ctx.send("🚫 Cannot remove the last authorized user."); return
    if user_id not in ALLOWED_USER_IDS: await ctx.send(f"User ID `{user_id}` not in authorized list."); return
    ALLOWED_USER_IDS.remove(user_id)
    if await update_allowed_users_env():
        logger.info(f"User {ctx.author} removed authorized user ID: {user_id}")
        await ctx.send(f"✅ Removed User ID `{user_id}` from authorized list.")
    else: ALLOWED_USER_IDS.add(user_id); await ctx.send("❌ Error updating config file. User not removed.")

@remove_user.error
async def remove_user_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument): await ctx.send("Usage: `!removeuser <user_id_or_@mention>`")
    elif isinstance(error, commands.CheckFailure): await ctx.send("🚫 You are not authorized.")
    else: logger.error(f"Error in removeuser: {error}", exc_info=True); await ctx.send("❌ Error.")

# --- General / Status Commands (Define these next) ---
@bot.command(name='checknow', help='Manually triggers a stream check. (Authorized users only)')
@is_allowed_user()
async def check_now(ctx):
    """Command to manually trigger a stream check."""
    await ctx.send("⏳ Kicking off a manual stream check...")
    logger.info(f"Manual stream check triggered by {ctx.author}")
    success = await perform_stream_check(bot)
    if success: await ctx.send("✅ Manual stream check complete.")
    else: await ctx.send("⚠️ Manual stream check encountered an issue. Check logs.")

@check_now.error
async def check_now_error(ctx, error):
     if isinstance(error, commands.CheckFailure): await ctx.send("🚫 You are not authorized.")
     else: logger.error(f"Error in checknow: {error}", exc_info=True); await ctx.send("❌ Error during manual check.")

@bot.command(name='status', help='Checks the bot\'s operational status.')
@is_allowed_user()
async def status(ctx):
    """Reports the status of the bot's background tasks."""
    global last_successful_check_time
    embed = discord.Embed(title="Bot Status Report", color=discord.Color.blue())
    loop_running = check_streams.is_running()
    embed.add_field(name="Stream Check Loop Active?", value=f"{'✅ Yes' if loop_running else '❌ No'}", inline=False)
    if not loop_running:
        embed.color = discord.Color.orange()
        try:
            exception = check_streams.get_task().exception()
            if exception: embed.add_field(name="Loop Error", value=f"```\n{str(exception)[:1000]}\n```", inline=False); embed.color = discord.Color.red()
        except Exception: pass
    if last_successful_check_time: embed.add_field(name="Last Successful Check Cycle", value=f"<t:{int(last_successful_check_time.timestamp())}:R>", inline=True)
    else: embed.add_field(name="Last Successful Check Cycle", value="N/A", inline=True)
    vip_streamers = load_data(VIP_STREAMERS_FILE); mod_streamers = load_data(MOD_STREAMERS_FILE)
    embed.add_field(name="VIP Watchlist Size", value=f"{len(vip_streamers)}", inline=True)
    embed.add_field(name="Mod Watchlist Size", value=f"{len(mod_streamers)}", inline=True)
    embed.set_footer(text=f"Checked at: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z')}")
    await ctx.send(embed=embed)

@status.error
async def status_error(ctx, error):
    if isinstance(error, commands.CheckFailure): await ctx.send("🚫 You are not authorized.")
    else: logger.error(f"Error in status command: {error}", exc_info=True); await ctx.send("❌ Error checking status.")


# --- Custom Help Command (DEFINED LAST) ---
@bot.command(name='help', help='Shows this help message with all available commands.')
@is_allowed_user() # Keep restricted or remove decorator to make public
async def custom_help(ctx):
    """Displays a custom help message listing all commands."""
    embed = discord.Embed(title="Bot Command Help", description="Here are the available commands:", color=discord.Color.blurple())
    # VIP
    vip_cmds = f"`{bot.command_prefix}addvipstreamer <username>` - {add_vip_streamer.help}\n"
    vip_cmds += f"`{bot.command_prefix}removevipstreamer <username>` - {remove_vip_streamer.help}\n"
    vip_cmds += f"`{bot.command_prefix}listvipstreamers` - {list_vip_streamers.help}\n"
    embed.add_field(name="👑 VIP Streamer Management", value=vip_cmds, inline=False)
    # Mod
    mod_cmds = f"`{bot.command_prefix}addmodstreamer <username>` - {add_mod_streamer.help}\n"
    mod_cmds += f"`{bot.command_prefix}removemodstreamer <username>` - {remove_mod_streamer.help}\n"
    mod_cmds += f"`{bot.command_prefix}listmodstreamers` - {list_mod_streamers.help}\n"
    embed.add_field(name="🛡️ Mod Streamer Management", value=mod_cmds, inline=False)
    # Config
    config_cmds = f"`{bot.command_prefix}setvipchannel <channel_id>` - {set_vip_channel.help}\n"
    config_cmds += f"`{bot.command_prefix}setmodchannel <channel_id>` - {set_mod_channel.help}\n"
    config_cmds += f"`{bot.command_prefix}adduser <user_id_or_@mention>` - {add_user.help}\n"
    config_cmds += f"`{bot.command_prefix}removeuser <user_id_or_@mention>` - {remove_user.help}\n"
    embed.add_field(name="⚙️ Configuration", value=config_cmds, inline=False)
    # General
    general_cmds = f"`{bot.command_prefix}checknow` - {check_now.help}\n"
    general_cmds += f"`{bot.command_prefix}status` - {status.help}\n"
    general_cmds += f"`{bot.command_prefix}help` - {custom_help.help}\n" # Self reference okay
    embed.add_field(name="ℹ️ General / Status", value=general_cmds, inline=False)
    embed.set_footer(text="Use the specified commands in DMs or server channels.")
    await ctx.send(embed=embed)

@custom_help.error
async def custom_help_error(ctx, error):
    if isinstance(error, commands.CommandInvokeError):
        original_error = error.original
        if not isinstance(original_error, commands.CheckFailure): logger.error(f"Error invoking help command: {original_error}", exc_info=original_error)
        if isinstance(original_error, commands.CheckFailure): await ctx.send("🚫 You are not authorized to use the help command."); return
    if isinstance(error, commands.CheckFailure): await ctx.send("🚫 You are not authorized to use the help command.")
    else: logger.error(f"Error in help command processing: {error}", exc_info=True); await ctx.send("❌ Error displaying help.")


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