import discord
from discord.ext import commands, tasks
import requests
import json
import os
import asyncio
from dotenv import load_dotenv
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Set # Added for type hinting

# --- Configuration and Setup ---
load_dotenv()  # Load environment variables from .env file

DISCORD_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
TWITCH_CLIENT_ID = os.getenv('TWITCH_CLIENT_ID')
TWITCH_CLIENT_SECRET = os.getenv('TWITCH_CLIENT_SECRET')
TARGET_CHANNEL_ID_STR = os.getenv('TARGET_CHANNEL_ID')
ALLOWED_USER_IDS_STR = os.getenv('ALLOWED_USER_IDS', '')

# --- Global Variables ---
twitch_access_token = None
token_expiry_time = datetime.now(timezone.utc)
last_successful_check_time = None # Add this line

# Validate TARGET_CHANNEL_ID
TARGET_CHANNEL_ID: int = 0 # Default or placeholder
try:
    if TARGET_CHANNEL_ID_STR:
        TARGET_CHANNEL_ID = int(TARGET_CHANNEL_ID_STR)
    else:
        raise ValueError("TARGET_CHANNEL_ID is missing in the .env file.")
except (TypeError, ValueError) as e:
    print(f"Error: Invalid TARGET_CHANNEL_ID in .env file. Please provide a valid channel ID. Details: {e}")
    exit()

# Parse ALLOWED_USER_IDS
ALLOWED_USER_IDS: Set[int] = set()
if ALLOWED_USER_IDS_STR:
    try:
        # Split by comma, strip whitespace, convert to int, filter out empty strings
        ALLOWED_USER_IDS = {int(uid.strip()) for uid in ALLOWED_USER_IDS_STR.split(',') if uid.strip()}
        print(f"Loaded {len(ALLOWED_USER_IDS)} allowed user IDs.")
    except ValueError:
        print("Error parsing ALLOWED_USER_IDS from .env file. Ensure it's a comma-separated list of numbers.")
        # Decide if you want to exit or continue with restricted commands possibly failing
        # exit() # Uncomment to force exit if IDs are invalid
else:
     print("Warning: No ALLOWED_USER_IDS defined in .env. DM commands will only work if the list is populated.")


# --- Logging Setup ---
# Setup logging to file and console
log_formatter = logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s')
log_file_handler = logging.FileHandler('bot.log', encoding='utf-8', mode='a') # Append mode
log_file_handler.setFormatter(log_formatter)
log_console_handler = logging.StreamHandler()
log_console_handler.setFormatter(log_formatter)

logger = logging.getLogger(__name__) # Get logger for this module
logger.setLevel(logging.INFO)
logger.addHandler(log_file_handler)
#logger.addHandler(log_console_handler)

discord_logger = logging.getLogger('discord') # Configure discord.py's logger
discord_logger.setLevel(logging.INFO) # Adjust level as needed (e.g., INFO, WARNING)
discord_logger.addHandler(log_file_handler)
#discord_logger.addHandler(log_console_handler)

tasks_logger = logging.getLogger('discord.ext.tasks') # Configure tasks logger
tasks_logger.setLevel(logging.INFO)
tasks_logger.addHandler(log_file_handler)
#tasks_logger.addHandler(log_console_handler)


# --- Bot Setup ---
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix='!', intents=intents)

# --- Data File Paths ---
STREAMERS_FILE = 'streamers.json'
LIVE_MESSAGES_FILE = 'live_messages.json'

# --- Global Variables ---
twitch_access_token = None
token_expiry_time = datetime.now(timezone.utc)

# --- Helper Functions ---

def load_data(filename):
    """Loads data from a JSON file."""
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning(f"{filename} not found, returning default structure.")
        if filename == STREAMERS_FILE: return []
        if filename == LIVE_MESSAGES_FILE: return {}
        return None
    except json.JSONDecodeError:
        logger.error(f"Error decoding JSON from {filename}. Returning default structure.")
        if filename == STREAMERS_FILE: return []
        if filename == LIVE_MESSAGES_FILE: return {}
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

    if twitch_access_token and now < (token_expiry_time - timedelta(minutes=5)): # Increased buffer
        # logger.debug("Using cached Twitch token.")
        return twitch_access_token

    logger.info("Attempting to get/refresh Twitch App Access Token...")
    url = "https://id.twitch.tv/oauth2/token"
    payload = {
        'client_id': TWITCH_CLIENT_ID,
        'client_secret': TWITCH_CLIENT_SECRET,
        'grant_type': 'client_credentials'
    }
    try:
        response = await asyncio.to_thread(requests.post, url, data=payload, timeout=10) # Added timeout
        response.raise_for_status()
        data = response.json()
        twitch_access_token = data['access_token']
        expires_in = data.get('expires_in', 3600)
        token_expiry_time = now + timedelta(seconds=expires_in)
        logger.info(f"Successfully obtained/refreshed Twitch App Access Token. Expires in ~{expires_in // 60} minutes.")
        return twitch_access_token
    except requests.exceptions.Timeout:
        logger.error("Timeout occurred while getting Twitch token.")
        return None # Indicate failure
    except requests.exceptions.RequestException as e:
        response_text = getattr(e.response, 'text', 'No response text available')
        response_status = getattr(e.response, 'status_code', 'N/A')
        logger.error(f"Error getting Twitch token (Status: {response_status}): {e} - Response: {response_text}")
        twitch_access_token = None
        token_expiry_time = datetime.now(timezone.utc) # Reset expiry on failure
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

async def get_stream_status(streamer_login):
    """Checks if a Twitch streamer is live using their login name."""
    token = await get_twitch_app_access_token()
    if not token:
        logger.warning(f"Cannot check status for {streamer_login}, no valid Twitch token available.")
        return None

    headers = {
        'Client-ID': TWITCH_CLIENT_ID,
        'Authorization': f'Bearer {token}'
    }
    params = {'user_login': streamer_login}
    url = 'https://api.twitch.tv/helix/streams'

    try:
        # logger.debug(f"Checking stream status for {streamer_login}...")
        response = await asyncio.to_thread(requests.get, url, headers=headers, params=params, timeout=10) # Added timeout
        response.raise_for_status()
        data = response.json()

        if data.get('data'):
            stream_info = data['data'][0]
            # logger.debug(f"{streamer_login} is LIVE.")
            return {
                'live': True,
                'title': stream_info.get('title', 'No Title'),
                'game_name': stream_info.get('game_name', 'No Game'),
                'viewer_count': stream_info.get('viewer_count', 0),
                'thumbnail_url': stream_info.get('thumbnail_url', '').replace('{width}', '320').replace('{height}', '180')
            }
        else:
            # logger.debug(f"{streamer_login} is OFFLINE.")
            return {'live': False}
    except requests.exceptions.Timeout:
        logger.error(f"Timeout occurred while checking stream status for {streamer_login}.")
        return None # Indicate failure
    except requests.exceptions.RequestException as e:
        response_status = getattr(e.response, 'status_code', 'N/A')
        response_text = getattr(e.response, 'text', 'No response text available')
        logger.error(f"Error checking Twitch stream status for {streamer_login} (Status: {response_status}): {e} - Response: {response_text}")
        if response_status == 401:
             logger.warning(f"Twitch token might be invalid (401 Unauthorized) for {streamer_login}. Forcing refresh on next cycle.")
             global twitch_access_token # Mark token as invalid
             twitch_access_token = None
             token_expiry_time = datetime.now(timezone.utc) # Also reset expiry
        elif response_status == 403:
             logger.warning(f"Twitch returned 403 Forbidden for {streamer_login}. Check Client-ID or token scopes if applicable.")
        elif response_status == 429:
            logger.warning("Twitch API rate limit possibly hit.")
        return None
    except (KeyError, IndexError, json.JSONDecodeError) as e: # Added JSONDecodeError
        response_text = getattr(response, 'text', 'No response text available')
        logger.error(f"Error parsing Twitch stream data for {streamer_login}: {e} - Response: {response_text}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error checking stream status for {streamer_login}: {e}", exc_info=True)
        return None


async def perform_stream_check(bot_instance: commands.Bot):
    """Performs the actual check for live streams and updates Discord."""
    streamers_to_check = load_data(STREAMERS_FILE)
    live_messages = load_data(LIVE_MESSAGES_FILE)
    channel = bot_instance.get_channel(TARGET_CHANNEL_ID)


    if not channel:
        # Log error only once per certain interval or if state changes? For now, log each time.
        logger.error(f"Target channel with ID {TARGET_CHANNEL_ID} not found during check. Bot might lack access or ID is wrong.")
        return False # Indicate failure

    if not streamers_to_check:
        # logger.info("Watchlist is empty, skipping stream check.") # Reduce noise maybe
        return True # Indicate success (nothing to do)

    logger.info(f"Performing check for: {', '.join(streamers_to_check)}")

    current_live_mapping = live_messages.copy()
    changes_made = False

    # Use asyncio.gather for potentially faster checks (be mindful of rate limits)
    # status_tasks = [get_stream_status(login) for login in streamers_to_check]
    # results = await asyncio.gather(*status_tasks, return_exceptions=True)

    # Sequential check (safer for rate limits, easier to debug)
    for streamer_login in streamers_to_check:
        try:
            status = await get_stream_status(streamer_login)
            # Optional small delay between checks if rate limits are a concern even sequentially
            await asyncio.sleep(0.2)

            if status is None:
                logger.warning(f"Skipping update for {streamer_login} due to previous API/fetch error.")
                continue # Skip this streamer for this cycle

            is_live = status.get('live', False)
            is_currently_posted = streamer_login in current_live_mapping

            # --- Scenario 1: Streamer went LIVE ---
            if is_live and not is_currently_posted:
                logger.info(f"{streamer_login} went LIVE!")
                try:
                    embed = discord.Embed(
                        title=f"🔴 {streamer_login} is now LIVE on Twitch!",
                        url=f"https://twitch.tv/{streamer_login}",
                        description=status.get('title', 'No Title Provided'), # Use description for title
                        color=discord.Color.purple(),
                        timestamp=datetime.now(timezone.utc) # Add timestamp
                    )
                    embed.add_field(name="Game", value=status.get('game_name', 'N/A'), inline=True)
                    embed.add_field(name="Viewers", value=f"{status.get('viewer_count', 'N/A'):,}", inline=True) # Format viewers
                    if status.get('thumbnail_url'):
                         embed.set_image(url=status['thumbnail_url'])
                    # Optional: Fetch streamer profile picture for thumbnail
                    # embed.set_thumbnail(url=...)
                    embed.set_footer(text="Click the title to watch!")

                    # Consider adding allowed_mentions=discord.AllowedMentions(everyone=True) if you want @everyone
                    message = await channel.send(f"🎉 Hey @everyone! `{streamer_login}` just went live! 🎉", embed=embed, allowed_mentions=discord.AllowedMentions(everyone=True))

                    live_messages[streamer_login] = message.id
                    changes_made = True
                    logger.info(f"Posted live notification for {streamer_login} (Message ID: {message.id})")

                except discord.Forbidden:
                    logger.error(f"Bot lacks permissions (Send Messages/Embed Links) in channel {TARGET_CHANNEL_ID}.")
                except discord.HTTPException as e:
                     logger.error(f"HTTP error sending live notification for {streamer_login}: {e.status} {e.text}")
                except Exception as e:
                    logger.error(f"Error sending live notification for {streamer_login}: {e}", exc_info=True)

            # --- Scenario 2: Streamer went OFFLINE ---
            elif not is_live and is_currently_posted:
                logger.info(f"{streamer_login} went OFFLINE.")
                message_id = current_live_mapping.get(streamer_login) # Use .get for safety
                if not message_id:
                     logger.warning(f"Tracked message ID missing for offline streamer {streamer_login}. Removing from tracker.")
                     if streamer_login in live_messages:
                         del live_messages[streamer_login]
                         changes_made = True
                     continue # Skip deletion attempt

                try:
                    message = await channel.fetch_message(message_id)
                    await message.delete()
                    logger.info(f"Deleted notification for {streamer_login} (Message ID: {message_id})")
                except discord.NotFound:
                    logger.warning(f"Message {message_id} for {streamer_login} not found (already deleted?). Removing from tracker.")
                except discord.Forbidden:
                    logger.error(f"Bot lacks permissions (Manage Messages) to delete message {message_id} in channel {TARGET_CHANNEL_ID}.")
                except discord.HTTPException as e:
                    logger.error(f"HTTP error deleting offline message {message_id} for {streamer_login}: {e.status} {e.text}")
                except Exception as e:
                    logger.error(f"Error deleting offline notification message {message_id} for {streamer_login}: {e}", exc_info=True)

                # Remove from live messages regardless of deletion success/failure to prevent repeated attempts
                if streamer_login in live_messages:
                     del live_messages[streamer_login]
                     changes_made = True

            # --- Scenario 3: Streamer is still LIVE ---
            # Potential improvement: Update message title/game? More complex.
            # elif is_live and is_currently_posted:
            #     logger.debug(f"{streamer_login} is still live. No action.")

            # --- Scenario 4: Streamer is still OFFLINE ---
            # elif not is_live and not is_currently_posted:
            #     logger.debug(f"{streamer_login} is still offline. No action.")

        except Exception as e:
            logger.error(f"An unexpected error occurred in the main check loop for {streamer_login}: {e}", exc_info=True)
            # Continue to the next streamer

    if changes_made:
        save_data(LIVE_MESSAGES_FILE, live_messages)
        logger.info("Live messages file updated.")

    global last_successful_check_time
    last_successful_check_time = datetime.now(timezone.utc)
    logger.info("Stream check cycle completed successfully.")
    return True # Indicate success/completion

# --- Bot Events ---
@bot.event
async def on_ready():
    """Event triggered when the bot is connected and ready."""
    logger.info(f'Logged in as {bot.user.name} (ID: {bot.user.id})')
    logger.info(f'discord.py version: {discord.__version__}')
    logger.info('Bot is ready. Starting background check loop.')
    check_streams.start() # Start the loop task

# --- Custom Check for Allowed Users ---
def is_allowed_user():
    async def predicate(ctx):
        if not ALLOWED_USER_IDS:
            logger.warning(f"Command '{ctx.command.name}' invoked by {ctx.author} ({ctx.author.id}), but ALLOWED_USER_IDS is empty. Denying.")
            # Optionally send a message to the user
            # await ctx.send("Sorry, this command is restricted and no users are currently authorized.", ephemeral=True) # ephemeral only works for interactions
            return False
        is_allowed = ctx.author.id in ALLOWED_USER_IDS
        if not is_allowed:
            logger.warning(f"Unauthorized command attempt for '{ctx.command.name}' by {ctx.author} ({ctx.author.id})")
        return is_allowed
    return commands.check(predicate)

# --- Bot Commands ---
@bot.command(name='addstreamer', help='Adds a Twitch streamer. Usage: !addstreamer <twitch_username> (Authorized users only)')
@is_allowed_user()
async def add_streamer(ctx, twitch_username: str):
    """Command to add a streamer (restricted)."""
    streamer_login = twitch_username.lower().strip() # Ensure lowercase and no surrounding whitespace
    if not streamer_login:
         await ctx.send("⚠️ Please provide a valid Twitch username.")
         return

    streamers = load_data(STREAMERS_FILE)

    if streamer_login in streamers:
        await ctx.send(f"`{streamer_login}` is already on the list.")
    else:
        # Optional: Basic validation if streamer exists? Could add another API call here but adds complexity/delay.
        streamers.append(streamer_login)
        save_data(STREAMERS_FILE, streamers)
        await ctx.send(f"✅ Added `{streamer_login}` to the watchlist.")
        logger.info(f"Authorized user {ctx.author} ({ctx.author.id}) added streamer: {streamer_login}")

@add_streamer.error
async def add_streamer_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("⚠️ Please provide the Twitch username. Usage: `!addstreamer <twitch_username>`")
    elif isinstance(error, commands.CheckFailure):
         await ctx.send("🚫 You are not authorized to use this command.")
    else:
        logger.error(f"Error in addstreamer command: {error}", exc_info=True)
        await ctx.send("❌ An unexpected error occurred.")

@bot.command(name='removestreamer', help='Removes a streamer. Usage: !removestreamer <twitch_username> (Authorized users only)')
@is_allowed_user()
async def remove_streamer(ctx, twitch_username: str):
    """Command to remove a streamer (restricted)."""
    streamer_login = twitch_username.lower().strip()
    if not streamer_login:
         await ctx.send("⚠️ Please provide a valid Twitch username.")
         return

    streamers = load_data(STREAMERS_FILE)
    live_messages = load_data(LIVE_MESSAGES_FILE)
    changes_made_live = False

    if streamer_login in streamers:
        streamers.remove(streamer_login)
        save_data(STREAMERS_FILE, streamers)

        # Attempt to delete message only if channel exists
        channel = bot.get_channel(TARGET_CHANNEL_ID)
        message_id = live_messages.pop(streamer_login, None) # Remove and get ID safely

        if message_id and channel:
            changes_made_live = True # Mark change even if deletion fails
            try:
                message = await channel.fetch_message(message_id)
                await message.delete()
                logger.info(f"Deleted live message for removed streamer {streamer_login} (Message ID: {message_id})")
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
                logger.warning(f"Could not delete message {message_id} for removed streamer {streamer_login}: {e}")
            except Exception as e:
                 logger.error(f"Unexpected error deleting message {message_id} for removed streamer {streamer_login}: {e}", exc_info=True)
        elif streamer_login in live_messages: # If it was in live_messages but no message_id/channel
             changes_made_live = True # Still need to save the removal

        if changes_made_live:
            save_data(LIVE_MESSAGES_FILE, live_messages) # Save updated live messages

        await ctx.send(f"🗑️ Removed `{streamer_login}` from the watchlist.")
        logger.info(f"Authorized user {ctx.author} ({ctx.author.id}) removed streamer: {streamer_login}")
    else:
        await ctx.send(f"`{streamer_login}` was not found on the list.")

@remove_streamer.error
async def remove_streamer_error(ctx, error):
     if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("⚠️ Please provide the Twitch username. Usage: `!removestreamer <twitch_username>`")
     elif isinstance(error, commands.CheckFailure):
         await ctx.send("🚫 You are not authorized to use this command.")
     else:
        logger.error(f"Error in removestreamer command: {error}", exc_info=True)
        await ctx.send("❌ An unexpected error occurred.")

@bot.command(name='liststreamers', help='Lists watched streamers. (Authorized users only)')
@is_allowed_user()
async def list_streamers(ctx):
    """Command to list monitored streamers (restricted)."""
    streamers = load_data(STREAMERS_FILE)
    if not streamers:
        await ctx.send("The watchlist is currently empty.")
    else:
        streamer_list = "\n".join([f"- `{s}`" for s in sorted(streamers)]) # Sort for consistency
        embed = discord.Embed(title="Watchlist Streamers", description=streamer_list, color=discord.Color.blue())
        await ctx.send(embed=embed)

@list_streamers.error
async def list_streamers_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
         await ctx.send("🚫 You are not authorized to use this command.")
    else:
        logger.error(f"Error in liststreamers command: {error}", exc_info=True)
        await ctx.send("❌ An unexpected error occurred.")

@bot.command(name='checknow', help='Manually triggers a stream check. (Authorized users only)')
@is_allowed_user()
async def check_now(ctx):
    """Command to manually trigger a stream check."""
    await ctx.send("⏳ Kicking off a manual stream check...")
    logger.info(f"Manual stream check triggered by {ctx.author} ({ctx.author.id})")
    # Ensure task isn't already running if checks take longer than interval? Less critical here.
    success = await perform_stream_check(bot)
    if success:
        await ctx.send("✅ Manual stream check complete.")
    else:
        await ctx.send("⚠️ Manual stream check encountered an issue (e.g., couldn't find channel). Check bot logs.")

@check_now.error
async def check_now_error(ctx, error):
     if isinstance(error, commands.CheckFailure):
         await ctx.send("🚫 You are not authorized to use this command.")
     else:
         logger.error(f"Error in checknow command: {error}", exc_info=True)
         await ctx.send("❌ An unexpected error occurred during manual check.")

@bot.command(name='status', help='Checks the bot\'s operational status. (Authorized users only)')
@is_allowed_user()
async def status(ctx):
    """Reports the status of the bot's background tasks."""
    global last_successful_check_time

    embed = discord.Embed(title="Bot Status Report", color=discord.Color.green())

    # Check background loop status
    loop_running = check_streams.is_running()
    embed.add_field(name="Stream Check Loop Active?", value=f"{'✅ Yes' if loop_running else '❌ No'}", inline=True)

    if not loop_running:
        embed.color = discord.Color.orange() # Change color if loop stopped
        try:
            # Attempt to get exception if loop stopped unexpectedly
            exception = check_streams.get_task().exception()
            if exception:
                embed.add_field(name="Loop Error", value=f"```\n{str(exception)[:1000]}\n```", inline=False) # Show first 1000 chars
                embed.color = discord.Color.red()
        except Exception:
            # Ignore if task/exception retrieval fails
            pass

    # Report last successful check time
    if last_successful_check_time:
        # Format timestamp for Discord <t:unix_timestamp:R> for relative time
        timestamp_str = f"<t:{int(last_successful_check_time.timestamp())}:R>"
        embed.add_field(name="Last Successful Check", value=timestamp_str, inline=True)
    else:
        embed.add_field(name="Last Successful Check", value="N/A (Bot recently started or loop hasn't completed)", inline=True)

    # Report watchlist size
    streamers = load_data(STREAMERS_FILE)
    embed.add_field(name="Watchlist Size", value=f"{len(streamers)} streamer(s)", inline=True)

    embed.set_footer(text=f"Checked at: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z')}")

    await ctx.send(embed=embed)

@status.error
async def status_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
         await ctx.send("🚫 You are not authorized to use this command.")
    else:
        logger.error(f"Error in status command: {error}", exc_info=True)
        await ctx.send("❌ An unexpected error occurred while checking status.")

# --- Background Task ---
@tasks.loop(minutes=1.0) # Check frequency
async def check_streams():
    """Background task wrapper for perform_stream_check."""
    # logger.debug("Background check loop triggered.") # Can be noisy
    await perform_stream_check(bot)

@check_streams.before_loop
async def before_check_streams():
    """Wait until the bot is ready before starting the loop."""
    await bot.wait_until_ready()
    logger.info("Bot is ready, background stream check loop starting.")

@check_streams.after_loop
async def after_check_streams():
    if check_streams.is_being_cancelled():
        logger.info("Stream check loop cancelled.")
    else:
         logger.error(f"Stream check loop stopped unexpectedly! Reason: {check_streams.get_task().exception()}")
         # Consider adding logic to attempt a restart? More complex.

# --- Run the Bot ---
if __name__ == "__main__":
    # Basic check for essential credentials
    if not all([DISCORD_TOKEN, TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET, TARGET_CHANNEL_ID]):
        print("FATAL ERROR: One or more required environment variables (DISCORD_TOKEN, TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET, TARGET_CHANNEL_ID) are missing or invalid in .env")
        exit()
    else:
        try:
            print("Attempting to run bot...")
            bot.run(DISCORD_TOKEN, log_handler=None) # Disable default discord.py handler, using ours
        except discord.LoginFailure:
             logger.critical("Login Failed: Invalid Discord Bot Token provided in .env. Check the token.")
        except discord.PrivilegedIntentsRequired:
             logger.critical("Intents Error: The bot is missing required Privileged Gateway Intents (especially Message Content). Please enable them in the Discord Developer Portal.")
        except Exception as e:
             logger.critical(f"FATAL ERROR running bot: {e}", exc_info=True)