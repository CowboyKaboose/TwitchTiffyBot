# Twitch Live Notification Discord Bot

✨ A Discord bot that monitors specified Twitch streamers and posts notifications in designated channels when they go live or offline. Features VIP and Moderator tiers with separate notification channels and user management.

## Features

*   Posts Twitch stream links when streamers go live.
*   Removes the notification post when streamers go offline.
*   Supports separate **VIP** and **Moderator** streamer tiers.
*   Posts notifications for each tier to different Discord channels.
*   Commands to add/remove streamers for each tier.
*   Commands to configure notification channels.
*   Commands to manage authorized users who can control the bot.
*   Commands work only via Direct Messages (DMs) with the bot for security/privacy.
*   Custom `!help` command.
*   Persistent configuration using `.env` file.
*   Designed for 24/7 operation on a server (e.g., AWS EC2).

## Prerequisites

Before you begin, ensure you have the following:

1.  **Python:** Version 3.8 or newer installed. ([Download Python](https://www.python.org/downloads/))
2.  **Pip:** Python's package installer (usually included with Python).
3.  **Git:** (Recommended) For cloning the repository and getting updates. ([Download Git](https://git-scm.com/downloads))
4.  **Discord Account:** To create the bot application and invite it.
5.  **Twitch Account:** To create the Twitch application.
6.  **Server (Optional but Recommended for 24/7):** A Linux server (like Ubuntu on AWS EC2, Raspberry Pi, etc.) if you want the bot running continuously.

 Setup Instructions

Follow these steps carefully to set up the bot.

### 1. Get the Bot Code

Clone the repository using Git (recommended):
```
git clone https://github.com/CowboyKaboose/TwitchTiffyBot.git
cd TwitchTiffyBot
```

Alternatively, download the code as a ZIP file from GitHub and extract it.

### 2. Create a Discord Bot Application
Follow these steps precisely:

Go to the Discord Developer Portal and log in.

Click "New Application". Give it a name (e.g., "Twitch Notifier") and click "Create".

Go to the "Bot" tab on the left sidebar.

(may not prompt this) Click "Add Bot", then confirm "Yes, do it!". 

TOKEN: Under the bot's username, click "Reset Token" (or "Copy" if visible). Copy this token immediately and save it somewhere secure temporarily. ⚠️ THIS IS YOUR BOT'S PASSWORD - KEEP IT SECRET! ⚠️ We'll put it in the .env file later.

INTENTS: Scroll down to "Privileged Gateway Intents". Enable:

✅ MESSAGE CONTENT INTENT (Crucial for reading commands)

INVITE: Go to the "OAuth2" -> "URL Generator" tab on the left.

Under "SCOPES", check the box for bot.

Under "BOT PERMISSIONS", check the boxes for:

Send Messages

Manage Messages (To delete offline notifications)

Embed Links (To show stream previews nicely)

Read Message History (Often needed with Manage Messages)

Copy the Generated URL at the bottom of the page.

Paste the URL into your browser and invite the bot to your Discord server.

### 3. Create a Twitch Application

Go to the Twitch Developer Console and log in.

Click "+ Register Your Application" on the right (or navigate via the left sidebar).

Fill in the details:

Name: Give it a name (e.g., "My Discord Notifier").

OAuth Redirect URLs: Enter http://localhost (it won't be used, but it's required).

Category: Select "Chat Bot" or "Application Integration".

Click "Create".

Find your new application in the list and click "Manage".

CLIENT ID: Copy the "Client ID" value. Save it temporarily.

CLIENT SECRET: Click the "New Secret" button. Copy the generated "Client Secret" value immediately. ⚠️ THIS IS A PASSWORD - KEEP IT SECRET! ⚠️ Save it temporarily.

### 4. Configure the Bot (.env File) - ⚠️ CRITICAL SECURITY STEP ⚠️

This bot uses a .env file to store sensitive information like your tokens and channel IDs. This file should NEVER be shared or committed to Git.

Check .gitignore: Ensure the file named .gitignore in the project directory contains a line that simply says .env. This tells Git to ignore the file. If .gitignore doesn't exist or doesn't have .env listed, add it before proceeding.

```
# Example .gitignore content
venv/
.venv/
__pycache__/
*.pyc
*.log
*.pem

# ---> IMPORTANT <---
.env
# ---> IMPORTANT <---
```

Create .env: In the main directory of the bot code, find the file named .env.example. Copy this file and rename the copy to simply .env.

Edit .env: Open the .env file with a text editor. You will see lines like VARIABLE_NAME=PLACEHOLDER. Replace the PLACEHOLDER values with the actual tokens and IDs you saved temporarily in the previous steps:

DISCORD_BOT_TOKEN: Paste the Discord Bot Token you copied.

VIP_CHANNEL_ID: Get the Channel ID of the Discord channel where VIP notifications should go.

How to get Channel ID: Enable Developer Mode in Discord (User Settings -> Advanced). Then, right-click the desired channel -> Copy Channel ID. Paste the number here.

MOD_CHANNEL_ID: Get the Channel ID for Moderator notifications (same method as above).

ALLOWED_USER_IDS: Get the User IDs of the Discord users who should be allowed to manage the bot (add/remove streamers, users, set channels). Separate multiple IDs with commas ONLY (no spaces).

How to get User ID: Enable Developer Mode. Right-click the desired user -> Copy User ID. Paste the number(s) here. Example: 123456789012345678,987654321098765432

TWITCH_CLIENT_ID: Paste the Twitch Application Client ID you copied.

TWITCH_CLIENT_SECRET: Paste the Twitch Application Client Secret you copied.

SAVE THE .env FILE.

🔒 DOUBLE-CHECK: Ensure this .env file is NOT visible in your Git changes if you are using Git (git status). It should be ignored because of .gitignore. DO NOT COMMIT THIS FILE.

### 5. Install Dependencies

Open a Terminal or Command Prompt in the bot's project directory.

(You can click the address bar of the folder and type cmd) 

Create a Virtual Environment: (Highly Recommended) This isolates the bot's libraries from your system Python.
```
python -m venv venv
# Or: python3 -m venv venv
```
Activate the Virtual Environment:

Windows (Command Prompt): 
```
.\venv\Scripts\activate
```
Windows (PowerShell): 
```
.\venv\Scripts\Activate.ps1 (You might need to set execution policy: Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process)
```
macOS/Linux:
```
source venv/bin/activate
```
(Your terminal prompt should now start with (venv))

Install Required Libraries:
```
pip install -r requirements.txt
```

Running the Bot
You have two main ways to run the bot:

## A. Local Testing ("Offline" Mode)

This is useful for quick tests but the bot will stop when you close the terminal or shut down your computer.

Ensure your virtual environment is activated ((venv) should be in your prompt).

Make sure your .env file is correctly filled out in the project directory.

Run the bot script:
```
python bot.py
```

Look for log messages indicating the bot has logged in and started the check loop. Test commands by DMing the bot.

Press Ctrl+C in the terminal to stop the bot.

## If not running online skip to Usage

## B. Persistent Server Deployment ("Online" Mode - e.g., AWS EC2)

This is required for the bot to run 24/7. Setting up a server is beyond this scope, but here are the key steps once you have a basic Linux (e.g., Ubuntu) server running and can connect via SSH:

Connect to your server via SSH.

Install Prerequisites: sudo apt update && sudo apt upgrade -y && sudo apt install python3 python3-pip python3-venv git -y

Clone Code: git clone ... (Clone your repository) and cd into the directory.

Setup Environment: Create and activate a virtual environment (python3 -m venv venv, source venv/bin/activate).

Install Dependencies: pip install -r requirements.txt.

## ⚠️ CREATE .env FILE MANUALLY: DO NOT clone your .env file via Git. Create it directly on the server using a text editor like nano:

```
nano .env
```

Paste the contents of your local .env file (with your actual secrets) into nano. Save and exit (Ctrl+X, Y, Enter).

Create Placeholder Data Files: If they don't exist: touch vip_streamers.json mod_streamers.json live_messages_vip.json live_messages_mod.json

Run Persistently (Recommended: systemd):

Follow the systemd setup guide provided previously in the chat to create a service file (/etc/systemd/system/discord_twitch_bot.service) that points to your bot's python executable inside the venv and the bot.py script. Configure it to restart automatically.

Enable and start the service: sudo systemctl enable discord_twitch_bot.service && sudo systemctl start discord_twitch_bot.service.

Check status: sudo systemctl status discord_twitch_bot.service.

Check logs: tail -f /path/to/your/bot/bot.log (adjust path).

(Alternative: screen or tmux are simpler but less robust - see previous guides if needed).

## Usage (Commands)

Interact with the bot by sending it Direct Messages (DMs). Only users listed in ALLOWED_USER_IDS in your .env file can use these commands.

!help: Shows the list of available commands.

!addvipstreamer <username>: Adds a streamer to the VIP list.

!removevipstreamer <username>: Removes a streamer from the VIP list.

!listvipstreamers: Lists all streamers on the VIP list.

!addmodstreamer <username>: Adds a streamer to the Mod list.

!removemodstreamer <username>: Removes a streamer from the Mod list.

!listmodstreamers: Lists all streamers on the Mod list.

!setvipchannel <channel_id>: Sets the Discord channel for VIP notifications.

!setmodchannel <channel_id>: Sets the Discord channel for Mod notifications.

!adduser <user_id_or_@mention>: Authorizes a user to use bot commands.

!removeuser <user_id_or_@mention>: De-authorizes a user.

!checknow: Manually triggers a check for all streamers.

!status: Shows the bot's operational status and watchlist counts.

🔒 Important Security Reminders 🔒
NEVER commit your .env file to GitHub or any public repository. Use .gitignore!

NEVER share your Discord Bot Token or Twitch Client Secret. Treat them like passwords. If you suspect a leak, reset them immediately in the developer portals.

If deploying on a server, keep your SSH keys secure and update your server regularly (sudo apt update && sudo apt upgrade -y).

Ensure file permissions on .env on the server are restrictive (e.g., only readable by the user running the bot).
