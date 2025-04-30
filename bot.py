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

# --- NEW: Custom Help Command ---
@bot.command(name='help', help='Shows this help message with all available commands.')
@is_allowed_user() # Keep restricted or remove decorator to make public
async def custom_help(ctx):
    """Displays a custom help message listing all commands."""
    embed = discord.Embed(
        title="Bot Command Help",
        description="Here are the available commands:",
        color=discord.Color.blurple()
    )

    # --- VIP Management ---
    # Use direct command reference (e.g., add_vip_streamer.help)
    vip_cmds = ""
    vip_cmds += f"`{bot.command_prefix}addvipstreamer <username>` - {add_vip_streamer.help}\n"
    vip_cmds += f"`{bot.command_prefix}removevipstreamer <username>` - {remove_vip_streamer.help}\n"
    vip_cmds += f"`{bot.command_prefix}listvipstreamers` - {list_vip_streamers.help}\n"
    embed.add_field(name="👑 VIP Streamer Management", value=vip_cmds, inline=False)

    # --- Mod Management ---
    mod_cmds = ""
    mod_cmds += f"`{bot.command_prefix}addmodstreamer <username>` - {add_mod_streamer.help}\n"
    mod_cmds += f"`{bot.command_prefix}removemodstreamer <username>` - {remove_mod_streamer.help}\n"
    mod_cmds += f"`{bot.command_prefix}listmodstreamers` - {list_mod_streamers.help}\n"
    embed.add_field(name="🛡️ Mod Streamer Management", value=mod_cmds, inline=False)

    # --- Configuration ---
    config_cmds = ""
    config_cmds += f"`{bot.command_prefix}setvipchannel <channel_id>` - {set_vip_channel.help}\n"
    config_cmds += f"`{bot.command_prefix}setmodchannel <channel_id>` - {set_mod_channel.help}\n"
    config_cmds += f"`{bot.command_prefix}adduser <user_id_or_@mention>` - {add_user.help}\n"
    config_cmds += f"`{bot.command_prefix}removeuser <user_id_or_@mention>` - {remove_user.help}\n"
    embed.add_field(name="⚙️ Configuration", value=config_cmds, inline=False)

    # --- General Commands ---
    general_cmds = ""
    general_cmds += f"`{bot.command_prefix}checknow` - {check_now.help}\n"
    general_cmds += f"`{bot.command_prefix}status` - {status.help}\n"
    general_cmds += f"`{bot.command_prefix}help` - {custom_help.help}\n" # Reference itself by function name
    embed.add_field(name="ℹ️ General / Status", value=general_cmds, inline=False)

    embed.set_footer(text="Use the specified commands in DMs or server channels.")
    await ctx.send(embed=embed)

@custom_help.error
async def custom_help_error(ctx, error):
    # Handle the specific case where the error comes from invoking help
    if isinstance(error, commands.CommandInvokeError):
        original_error = error.original
        # Log the original error for debugging if it's not just the check failure
        if not isinstance(original_error, commands.CheckFailure):
             logger.error(f"Error invoking help command: {original_error}", exc_info=original_error)
        # Check if the original error was the CheckFailure from is_allowed_user
        if isinstance(original_error, commands.CheckFailure):
             await ctx.send("🚫 You are not authorized to use the help command.")
             return # Prevent further default error handling for this specific case

    # Handle CheckFailure if it's raised directly (e.g., if help itself was restricted)
    if isinstance(error, commands.CheckFailure):
         await ctx.send("🚫 You are not authorized to use the help command.")
    else:
        # Log other unexpected errors
        logger.error(f"Error in help command processing: {error}", exc_info=True)
        await ctx.send("❌ An unexpected error occurred while displaying help.")

# --- NEW: User Management Commands ---

async def update_allowed_users_env():
    """Helper function to update the ALLOWED_USER_IDS string in .env"""
    global ALLOWED_USER_IDS
    try:
        dotenv_path = find_dotenv()
        if not dotenv_path:
            dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
            if not os.path.exists(dotenv_path):
                logger.error("Could not find .env file path to update allowed users.")
                return False # Indicate failure

        # Convert the set back to a comma-separated string
        ids_string = ",".join(map(str, sorted(list(ALLOWED_USER_IDS))))
        success = set_key(dotenv_path, "ALLOWED_USER_IDS", ids_string)
        if not success:
            logger.error(f"Failed to update ALLOWED_USER_IDS in .env file at {dotenv_path}")
            return False
        return True
    except Exception as e:
        logger.error(f"Error updating ALLOWED_USER_IDS in .env: {e}", exc_info=True)
        return False

@bot.command(name='adduser', help='Adds a user to the authorized list. Usage: !adduser <user_id_or_@mention>')
@is_allowed_user()
async def add_user(ctx, user_input: str):
    """Adds a user ID to the allowed list and updates .env"""
    global ALLOWED_USER_IDS

    user_id = None
    # Try converting mention to ID first
    if user_input.startswith('<@') and user_input.endswith('>'):
        try:
            # Extract ID from mention (handles <@!ID> and <@ID>)
            user_id = int(user_input.strip('<@!>'))
        except ValueError:
            pass # Will try direct int conversion next

    # Try converting string directly to ID
    if user_id is None:
        try:
            user_id = int(user_input)
        except ValueError:
            await ctx.send("⚠️ Invalid input. Please provide a valid User ID (numbers only) or mention the user (`@User`).")
            return

    # Check if user is already allowed
    if user_id in ALLOWED_USER_IDS:
        await ctx.send(f"User ID `{user_id}` is already authorized.")
        return

    # Optional: Verify user exists in Discord? (Needs fetch_user, can be slow/rate-limited)
    # try:
    #     target_user = await bot.fetch_user(user_id)
    #     display_name = f"{target_user.name}#{target_user.discriminator}" # Older format
    #     # display_name = target_user.display_name # Newer format
    # except discord.NotFound:
    #     await ctx.send(f"⚠️ Could not find a Discord user with ID `{user_id}`.")
    #     return
    # except discord.HTTPException:
    #     await ctx.send("⚠️ Failed to verify user ID with Discord.")
    #     return # Or proceed cautiously

    # Add to the set (in memory)
    ALLOWED_USER_IDS.add(user_id)

    # Update the .env file
    if await update_allowed_users_env():
        logger.info(f"User {ctx.author} added authorized user ID: {user_id}")
        # Use display_name if fetched, otherwise just the ID
        await ctx.send(f"✅ Successfully added User ID `{user_id}` to the authorized list.")
    else:
        # Revert the change in memory if saving failed
        ALLOWED_USER_IDS.remove(user_id)
        await ctx.send("❌ Error: Failed to update the configuration file. The user was not added.")

@add_user.error
async def add_user_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("⚠️ Please provide the User ID or mention the user. Usage: `!adduser <user_id_or_@mention>`")
    elif isinstance(error, commands.CheckFailure):
         await ctx.send("🚫 You are not authorized to use this command.")
    else:
        logger.error(f"Error in adduser command: {error}", exc_info=True)
        await ctx.send("❌ An unexpected error occurred.")


@bot.command(name='removeuser', help='Removes a user from the authorized list. Usage: !removeuser <user_id_or_@mention>')
@is_allowed_user()
async def remove_user(ctx, user_input: str):
    """Removes a user ID from the allowed list and updates .env"""
    global ALLOWED_USER_IDS

    user_id = None
    if user_input.startswith('<@') and user_input.endswith('>'):
        try:
            user_id = int(user_input.strip('<@!>'))
        except ValueError: pass
    if user_id is None:
        try:
            user_id = int(user_input)
        except ValueError:
            await ctx.send("⚠️ Invalid input. Please provide a valid User ID (numbers only) or mention the user (`@User`).")
            return

    # Prevent removing the last user? Or removing self?
    # if user_id == ctx.author.id:
    #     await ctx.send("🚫 You cannot remove yourself from the authorized list.")
    #     return
    if len(ALLOWED_USER_IDS) <= 1 and user_id in ALLOWED_USER_IDS:
        await ctx.send("🚫 Cannot remove the last authorized user.")
        return

    if user_id not in ALLOWED_USER_IDS:
        await ctx.send(f"User ID `{user_id}` is not currently in the authorized list.")
        return

    # Remove from the set (in memory)
    ALLOWED_USER_IDS.remove(user_id)

    # Update the .env file
    if await update_allowed_users_env():
        logger.info(f"User {ctx.author} removed authorized user ID: {user_id}")
        await ctx.send(f"✅ Successfully removed User ID `{user_id}` from the authorized list.")
    else:
        # Revert the change in memory if saving failed
        ALLOWED_USER_IDS.add(user_id) # Add it back
        await ctx.send("❌ Error: Failed to update the configuration file. The user was not removed.")

@remove_user.error
async def remove_user_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("⚠️ Please provide the User ID or mention the user. Usage: `!removeuser <user_id_or_@mention>`")
    elif isinstance(error, commands.CheckFailure):
         await ctx.send("🚫 You are not authorized to use this command.")
    else:
        logger.error(f"Error in removeuser command: {error}", exc_info=True)
        await ctx.send("❌ An unexpected error occurred.")

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


@bot.command(name='setvipchannel', help='Sets the channel ID for VIP notifications. Usage: !setvipchannel <channel_id>')
@is_allowed_user()
async def set_vip_channel(ctx, channel_id_str: str):
    """Sets the VIP notification channel ID persistently."""
    global VIP_CHANNEL_ID # Declare intent to modify global variable

    try:
        new_channel_id = int(channel_id_str)
    except ValueError:
        await ctx.send("⚠️ Invalid input. Please provide a valid channel ID (numbers only).")
        return

    # Verify the channel exists and the bot can see it
    target_channel = ctx.bot.get_channel(new_channel_id)
    if target_channel is None:
        await ctx.send(f"⚠️ Cannot find channel with ID `{new_channel_id}` or the bot doesn't have access to it.")
        return
    # Optional: Check channel type (must be text channel)
    if not isinstance(target_channel, discord.TextChannel):
         await ctx.send(f"⚠️ Channel `{target_channel.name}` (ID: {new_channel_id}) is not a text channel.")
         return
    # Optional: Check permissions in the new channel? (Send Messages, Embed Links, Manage Messages)
    # bot_perms = target_channel.permissions_for(ctx.guild.me) # Requires ctx.guild, might fail in DMs
    # if not bot_perms.send_messages or not bot_perms.embed_links or not bot_perms.manage_messages:
    #     await ctx.send(f"⚠️ Bot might lack necessary permissions (Send/Embed/Manage) in channel {target_channel.mention}.")
    #     # You might choose to proceed anyway or stop here

    try:
        # Find the .env file path
        dotenv_path = find_dotenv()
        if not dotenv_path:
             # Fallback if find_dotenv fails (e.g., if .env is not in standard locations)
             dotenv_path = os.path.join(os.path.dirname(__file__), '.env') # Assumes .env is in same dir as bot.py
             if not os.path.exists(dotenv_path):
                 logger.error("Could not find .env file path to update.")
                 await ctx.send("❌ Critical error: Could not locate the .env file to update.")
                 return

        # Update the .env file
        success = set_key(dotenv_path, "VIP_CHANNEL_ID", str(new_channel_id))

        if success:
            # Update the global variable in the running script
            VIP_CHANNEL_ID = new_channel_id
            logger.info(f"VIP Channel ID updated to {new_channel_id} by {ctx.author} ({ctx.author.id})")
            await ctx.send(f"✅ VIP notification channel successfully set to {target_channel.mention} (ID: `{new_channel_id}`). Change is active immediately.")
        else:
            # This might happen if set_key fails for some reason (e.g., permissions)
            logger.error(f"Failed to update VIP_CHANNEL_ID in .env file at {dotenv_path}")
            await ctx.send("❌ Error: Failed to update the configuration file. Check file permissions.")

    except Exception as e:
        logger.error(f"Error setting VIP channel ID: {e}", exc_info=True)
        await ctx.send("❌ An unexpected error occurred while updating the channel configuration.")

@set_vip_channel.error
async def set_vip_channel_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("⚠️ Please provide the channel ID. Usage: `!setvipchannel <channel_id>`")
    elif isinstance(error, commands.CheckFailure):
         await ctx.send("🚫 You are not authorized to use this command.")
    else:
        logger.error(f"Error in setvipchannel command: {error}", exc_info=True)
        await ctx.send("❌ An unexpected error occurred.")


@bot.command(name='setmodchannel', help='Sets the channel ID for Mod notifications. Usage: !setmodchannel <channel_id>')
@is_allowed_user()
async def set_mod_channel(ctx, channel_id_str: str):
    """Sets the Mod notification channel ID persistently."""
    global MOD_CHANNEL_ID # Declare intent to modify global variable

    try:
        new_channel_id = int(channel_id_str)
    except ValueError:
        await ctx.send("⚠️ Invalid input. Please provide a valid channel ID (numbers only).")
        return

    target_channel = ctx.bot.get_channel(new_channel_id)
    if target_channel is None:
        await ctx.send(f"⚠️ Cannot find channel with ID `{new_channel_id}` or the bot doesn't have access to it.")
        return
    if not isinstance(target_channel, discord.TextChannel):
         await ctx.send(f"⚠️ Channel `{target_channel.name}` (ID: {new_channel_id}) is not a text channel.")
         return

    try:
        dotenv_path = find_dotenv()
        if not dotenv_path:
             dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
             if not os.path.exists(dotenv_path):
                 logger.error("Could not find .env file path to update.")
                 await ctx.send("❌ Critical error: Could not locate the .env file to update.")
                 return

        success = set_key(dotenv_path, "MOD_CHANNEL_ID", str(new_channel_id))

        if success:
            MOD_CHANNEL_ID = new_channel_id
            logger.info(f"Mod Channel ID updated to {new_channel_id} by {ctx.author} ({ctx.author.id})")
            await ctx.send(f"✅ Mod notification channel successfully set to {target_channel.mention} (ID: `{new_channel_id}`). Change is active immediately.")
        else:
            logger.error(f"Failed to update MOD_CHANNEL_ID in .env file at {dotenv_path}")
            await ctx.send("❌ Error: Failed to update the configuration file. Check file permissions.")

    except Exception as e:
        logger.error(f"Error setting Mod channel ID: {e}", exc_info=True)
        await ctx.send("❌ An unexpected error occurred while updating the channel configuration.")

@set_mod_channel.error
async def set_mod_channel_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("⚠️ Please provide the channel ID. Usage: `!setmodchannel <channel_id>`")
    elif isinstance(error, commands.CheckFailure):
         await ctx.send("🚫 You are not authorized to use this command.")
    else:
        logger.error(f"Error in setmodchannel command: {error}", exc_info=True)
        await ctx.send("❌ An unexpected error occurred.")

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