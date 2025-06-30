
import os
import json
import discord
import asyncio
from discord.ext import commands, tasks
from discord import app_commands
from discord.ui import View, Button
import aiohttp
import requests
from flask import Flask, jsonify
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta
import time
import logging
import threading

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
CONFIG_FILE = "channels.json"
LAST_STATE_FILE = "last_state.json"
WEBHOOK_URL = "https://discord.com/api/webhooks/1375215015056904302/pWuNmRgKzuJz_Zo98NwQTU5LRckxYRFghBU9eCKt52uemgEtDsPgq9RI_eKx3inl3UWr"

# --- Load and Save Channel IDs ---
server_configs = {}

def load_channels():
    global server_configs
    if not os.path.isfile(CONFIG_FILE):
        server_configs = {"servers": {}}
        return
    with open(CONFIG_FILE, "r") as f:
        data = json.load(f)
    
    # Migrate old format to new format
    if "servers" not in data:
        logging.info("⚠️ Migrating channels.json to new server-specific format")
        old_config = {
            "seed_channel_id": data.get("seed_channel_id"),
            "gear_channel_id": data.get("gear_channel_id"),
            "egg_channel_id": data.get("egg_channel_id"),
            "cosmetic_channel_id": data.get("cosmetic_channel_id"),
            "announcement_channel_id": data.get("announcement_channel_id"),
            "weather_channel_id": data.get("weather_channel_id"),
            "event_stock_channel_id": data.get("event_stock_channel_id")
        }
        server_configs = {"servers": {"legacy": {"server_name": "Legacy Server", **old_config}}}
        save_channels()
    else:
        server_configs = data

def save_channels():
    with open(CONFIG_FILE, "w") as f:
        json.dump(server_configs, f, indent=2)

def get_server_config(guild_id):
    """Get server configuration by guild ID"""
    guild_str = str(guild_id)
    if guild_str not in server_configs["servers"]:
        server_configs["servers"][guild_str] = {
            "server_name": "Unknown Server",
            "seed_channel_id": None,
            "gear_channel_id": None,
            "egg_channel_id": None,
            "cosmetic_channel_id": None,
            "announcement_channel_id": None,
            "weather_channel_id": None,
            "event_stock_channel_id": None
        }
    return server_configs["servers"][guild_str]

def update_server_config(guild_id, guild_name, channel_type, channel_id):
    """Update server configuration"""
    guild_str = str(guild_id)
    config = get_server_config(guild_id)
    config["server_name"] = guild_name
    config[f"{channel_type}_channel_id"] = channel_id
    save_channels()

# --- Load and Save Last Sent State ---
def load_last_state():
    global last_state
    if os.path.isfile(LAST_STATE_FILE):
        with open(LAST_STATE_FILE, "r") as f:
            last_state = json.load(f)
    else:
        last_state = {
            "seed": 0,
            "gear": 0,
            "egg": 0,
            "cosmetic": 0,
            "event_stock": 0,
            "announcement": 0,
            "weather": {}
        }
    # Convert legacy weather format (list) to new dict format
    if isinstance(last_state.get("weather"), list):
        logging.info("⚠️ Migrating weather state from list to dict")
        last_state["weather"] = {}

def save_last_state():
    with open(LAST_STATE_FILE, "w") as f:
        json.dump(last_state, f, indent=2)

# Webhook logging handler
class WebhookHandler(logging.Handler):
    def __init__(self, webhook_url):
        super().__init__()
        self.webhook_url = webhook_url
        self.session = None
    
    async def send_log(self, record):
        if not self.session:
            self.session = aiohttp.ClientSession()
        
        try:
            message = self.format(record)
            # Truncate message if too long for Discord
            if len(message) > 2000:
                message = message[:1997] + "..."
            
            payload = {
                "content": f"```\n{message}\n```",
                "username": "Bot Logger"
            }
            
            async with self.session.post(self.webhook_url, json=payload) as response:
                if response.status not in [200, 204]:
                    print(f"Failed to send webhook log: {response.status}")
        except Exception as e:
            print(f"Webhook logging error: {e}")
    
    def emit(self, record):
        # Schedule the async send_log coroutine
        try:
            loop = asyncio.get_event_loop()
            loop.create_task(self.send_log(record))
        except:
            pass  # Ignore if no event loop

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Add webhook handler
webhook_handler = WebhookHandler(WEBHOOK_URL)
webhook_handler.setLevel(logging.INFO)
webhook_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))

# Get the root logger and add webhook handler
logger = logging.getLogger()
logger.addHandler(webhook_handler)

# Flask Setup
app = Flask(__name__)

@app.route('/api/meme', methods=['GET'])
def get_meme():
    try:
        response = requests.get("https://meme-api.com/gimme")
        if response.status_code == 200:
            meme_data = response.json()
            return jsonify({
                "title": meme_data.get("title"),
                "url": meme_data.get("url"),
                "subreddit": meme_data.get("subreddit"),
                "author": meme_data.get("author"),
                "ups": meme_data.get("ups"),
                "nsfw": meme_data.get("nsfw"),
                "spoiler": meme_data.get("spoiler"),
                "postLink": meme_data.get("postLink")
            })
        else:
            return jsonify({"error": "Failed to fetch meme"}), response.status_code
    except requests.RequestException as e:
        return jsonify({"error": f"Request failed: {str(e)}"}), 500

# Bot Setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

load_channels()
load_last_state()

def get_channel_for_server(guild_id, channel_type):
    """Get channel ID for specific server and channel type"""
    config = get_server_config(guild_id)
    return config.get(f"{channel_type}_channel_id")

# Lock for state access
state_lock = asyncio.Lock()

# Constants
STOCK_API_URL = "https://api.joshlei.com/v2/growagarden/stock"
WEATHER_API_URL = "https://api.joshlei.com/v2/growagarden/weather"
INVITE_URL = "https://discord.com/oauth2/authorize?client_id=1382419526200594583&permissions=8&integration_type=0&scope=bot"

# Active events tracking
active_events = {
    "stock": {},
    "weather": {},
    "announcements": {}
}

# Stock category mapping
STOCK_CATEGORY_MAPPING = {
    "seed": ("seed_stock", "Seeds 🌱"),
    "gear": ("gear_stock", "Gear ⚙️"),
    "egg": ("egg_stock", "Eggs 🥚"),
    "cosmetic": ("cosmetic_stock", "Cosmetics 💄"),
    "event_stock": ("eventshop_stock", "Event Stock 🎉")
}

# Helper to get channel ID for stock category
def get_channel_for_category(category_key):
    if category_key == "seed":
        return seed_channel_id
    elif category_key == "gear":
        return gear_channel_id
    elif category_key == "egg":
        return egg_channel_id
    elif category_key == "cosmetic":
        return cosmetic_channel_id
    elif category_key == "event_stock":
        return event_stock_channel_id
    return None

# Create invite button view
def create_invite_view():
    view = View(timeout=None)
    button = Button(label="Invite Bot", url=INVITE_URL, style=discord.ButtonStyle.link)
    view.add_item(button)
    return view

# Create stock embed
def create_stock_embed(items, title, start_ts, end_ts):
    embed = discord.Embed(title=title, color=discord.Color.green())
    
    # Add items
    if items:
        item_text = ""
        for item in items[:10]:  # Limit to 10 items
            name = item.get("display_name", item.get("name", "Unknown Item"))
            price = item.get("price")
            quantity = item.get("quantity", 0)
            
            if price is not None:
                if quantity > 0:
                    item_text += f"• {name} - ${price:,} (Qty: {quantity})\n"
                else:
                    item_text += f"• {name} - ${price:,}\n"
            else:
                if quantity > 0:
                    item_text += f"• {name} (Qty: {quantity})\n"
                else:
                    item_text += f"• {name}\n"
        embed.add_field(name="📦 Items", value=item_text or "No items", inline=False)
        
        # Add thumbnail from first item's icon if available
        if items and items[0].get("icon"):
            embed.set_thumbnail(url=items[0]["icon"])
    
    # Add timing
    embed.add_field(name="🕒 Started", value=f"{time_ago(start_ts)}", inline=True)
    
    if end_ts:
        now = datetime.now(timezone.utc).timestamp()
        if end_ts > now:
            remaining = end_ts - now
            hours = int(remaining // 3600)
            mins = int((remaining % 3600) // 60)
            embed.add_field(name="⏱️ Ends In", value=f"{hours}h {mins}m", inline=True)
        else:
            embed.add_field(name="⏱️ Status", value="Expired", inline=True)
    
    return embed

# Create weather embed
def create_weather_embed(weather_data):
    name = weather_data.get("weather_name", "Unknown Weather")
    description = weather_data.get("description", "No description available")
    
    embed = discord.Embed(
        title=f"🌤️ {name}",
        description=description,
        color=discord.Color.blue()
    )
    
    start_ts = weather_data.get("start_duration_unix", 0)
    duration = weather_data.get("duration", 0)
    end_ts = weather_data.get("end_duration_unix")
    
    if end_ts is None and start_ts and duration:
        end_ts = start_ts + duration
    
    embed.add_field(name="🕒 Started", value=f"{time_ago(start_ts)}", inline=True)
    
    if end_ts:
        now = datetime.now(timezone.utc).timestamp()
        if end_ts > now:
            remaining = end_ts - now
            hours = int(remaining // 3600)
            mins = int((remaining % 3600) // 60)
            embed.add_field(name="⏱️ Ends In", value=f"{hours}h {mins}m", inline=True)
        else:
            embed.add_field(name="⏱️ Status", value="Expired", inline=True)
    
    return embed

# Time Ago Helper (UTC based)
def time_ago(ts: float) -> str:
    now = datetime.now(timezone.utc).timestamp()
    diff = int(now - ts)
    if diff < 60:
        return f"{diff} second{'s' if diff != 1 else ''} ago"
    if diff < 3600:
        m = diff // 60
        return f"{m} minute{'s' if m != 1 else ''} ago"
    if diff < 86400:
        h = diff // 3600
        return f"{h} hour{'s' if h != 1 else ''} ago"
    d = diff // 86400
    return f"{d} day{'s' if d != 1 else ''} ago"

# Weather and stock checking functions
async def check_new_weather(is_restart: bool = False):
    """Check for weather events, with option to handle restart cases"""
    logging.info("🌡️ Checking for weather events...")
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(WEATHER_API_URL) as r:
                if r.status == 200 and r.content_type == 'application/json':
                    data = await r.json()
                    wlist = data.get("weather", [])
                    logging.info(f"🌤️ Received {len(wlist)} weather events from API")
                else:
                    text = await r.text()
                    logging.warning(f"⚠️ Weather API returned non-JSON: {text[:200]}")
                    return
        except Exception as e:
            logging.error(f"⚠️ Weather API Error: {e}")
            return

    new_events_count = 0
    for guild in bot.guilds:
        weather_channel_id = get_channel_for_server(guild.id, "weather")
        if not weather_channel_id:
            continue
            
        for w in wlist:
            if not isinstance(w, dict):
                continue
            
            weather_id = w.get("weather_id")
            if not weather_id:
                continue
                
            start_ts = w.get("start_duration_unix", 0)
            active = w.get("active", False)
            
            if not active:
                continue
                
            async with state_lock:
                weather_key = f"{guild.id}_{weather_id}"
                stored_start = last_state["weather"].get(weather_key, 0)
                
                if start_ts != stored_start:
                    embed = create_weather_embed(w)
                    ch = bot.get_channel(weather_channel_id)
                    if ch:
                        msg = await ch.send(embed=embed, view=create_invite_view())
                        weather_name = w.get("weather_name", "Unknown Weather")
                        server_name = guild.name
                        logging.info(f"✅ Sent {'RESTART ' if is_restart else ''}weather event: {weather_name} to {server_name}")
                        
                        active_events["weather"][weather_key] = {
                            "message_id": msg.id,
                            "channel_id": weather_channel_id,
                            "weather": w,
                            "guild_id": guild.id
                        }
                        
                        last_state["weather"][weather_key] = start_ts
                        new_events_count += 1
    
    if new_events_count > 0:
        save_last_state()

# Full Data definitions (fruits, mutations, variants)
DATA = {
    "fruits": [
        {"item_id":"carrot","display_name":"Carrot","baseValue":20,"weightDivisor":0.275},
        {"item_id":"strawberry","display_name":"Strawberry","baseValue":15,"weightDivisor":0.3},
        {"item_id":"blueberry","display_name":"Blueberry","baseValue":20,"weightDivisor":0.2},
        {"item_id":"orange_tulip","display_name":"Orange Tulip","baseValue":850,"weightDivisor":0.05},
        {"item_id":"tomato","display_name":"Tomato","baseValue":30,"weightDivisor":0.5},
        {"item_id":"corn","display_name":"Corn","baseValue":40,"weightDivisor":2},
        {"item_id":"daffodil","display_name":"Daffodil","baseValue":1000,"weightDivisor":0.2},
        {"item_id":"watermelon","display_name":"Watermelon","baseValue":3000,"weightDivisor":7},
        {"item_id":"pumpkin","display_name":"Pumpkin","baseValue":3400,"weightDivisor":8},
        {"item_id":"apple","display_name":"Apple","baseValue":275,"weightDivisor":3}
    ],
    "mutations": [
        {"mutation_id":"windstruck","display_name":"Windstruck","multiplier":5},
        {"mutation_id":"twisted","display_name":"Twisted","multiplier":5},
        {"mutation_id":"voidtouched","display_name":"Voidtouched","multiplier":135},
        {"mutation_id":"moonlit","display_name":"Moonlit","multiplier":2},
        {"mutation_id":"pollinated","display_name":"Pollinated","multiplier":3}
    ],
    "variants": [
        {"variant_id":"normal","display_name":"Normal","multiplier":1},
        {"variant_id":"gold","display_name":"Gold","multiplier":20},
        {"variant_id":"rainbow","display_name":"Rainbow","multiplier":50}
    ]
}

# Lookup tables
FRUIT_DATA = DATA["fruits"]
MUTATIONS = {m["mutation_id"]: m["multiplier"] for m in DATA["mutations"]}
VARIANTS = {v["variant_id"]: v["multiplier"] for v in DATA["variants"]}

@bot.event
async def on_ready():
    logging.info(f"✅ Logged in as {bot.user}")
    try:
        await bot.tree.sync()
        logging.info("🔄 Slash commands synced")
    except Exception as e:
        logging.error(f"⚠️ Sync error: {e}")
    
    # Check for active weather immediately on startup for all servers
    await check_new_weather(is_restart=True)
    
    # Start background tasks
    fetch_updates.start()
    update_active_events.start()
    frequent_checks.start()
    logging.info("🚀 Background tasks started")

# Background tasks
@tasks.loop(minutes=5)
async def fetch_updates():
    """Check for new stock every 5 minutes"""
    logging.info("🔍 Running 5-minute stock checks...")
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(STOCK_API_URL) as r:
                if r.status == 200 and r.content_type == 'application/json':
                    raw = await r.json()
                    stock = raw[0] if isinstance(raw, list) else raw
                else:
                    return
        except Exception as e:
            logging.error(f"⚠️ Stock API Error: {e}")
            return

    # Check all stock categories for each server
    stock_categories = [
        ("seed_stock", "Seeds 🌱", "seed"),
        ("gear_stock", "Gear ⚙️", "gear"),
        ("egg_stock", "Eggs 🥚", "egg"),
        ("cosmetic_stock", "Cosmetics 💄", "cosmetic"),
        ("eventshop_stock", "Event Stock 🎉", "event_stock"),
    ]

    for guild in bot.guilds:
        for api_key, title, state_key in stock_categories:
            chan_id = get_channel_for_server(guild.id, state_key)
            if not chan_id:
                continue
                
            items = stock.get(api_key, [])
            if items:
                start_ts = max(i.get("start_date_unix", 0) for i in items)
                end_ts = max(i.get("end_date_unix", 0) for i in items)
                
                server_state_key = f"{guild.id}_{state_key}"
                if start_ts > last_state.get(server_state_key, 0):
                    embed = create_stock_embed(items, title, start_ts, end_ts)
                    ch = bot.get_channel(chan_id)
                    if ch:
                        msg = await ch.send(embed=embed, view=create_invite_view())
                        server_name = guild.name
                        logging.info(f"✅ Sent new {state_key} stock to {server_name}")
                        
                        active_events["stock"][server_state_key] = {
                            "message_id": msg.id,
                            "channel_id": chan_id,
                            "start_ts": start_ts,
                            "end_ts": end_ts,
                            "items": items,
                            "title": title,
                            "guild_id": guild.id
                        }
                        last_state[server_state_key] = start_ts

    save_last_state()

@tasks.loop(seconds=20)
async def frequent_checks():
    """Check for new weather and announcements every 20 seconds"""
    # Check if any server has weather channels configured
    has_weather_channels = any(
        get_channel_for_server(guild.id, "weather") 
        for guild in bot.guilds
    )
    if has_weather_channels:
        await check_new_weather()

@tasks.loop(seconds=5)
async def update_active_events():
    """Update active events every 5 seconds"""
    current_utc = datetime.now(timezone.utc).timestamp()
    
    # Update stock events
    for key, event in list(active_events["stock"].items()):
        try:
            if event["end_ts"] <= current_utc:
                del active_events["stock"][key]
                continue
                
            channel = bot.get_channel(event["channel_id"])
            if channel:
                message = await channel.fetch_message(event["message_id"])
                embed = create_stock_embed(
                    event["items"], event["title"], 
                    event["start_ts"], event["end_ts"]
                )
                await message.edit(embed=embed)
        except (discord.NotFound, Exception):
            if key in active_events["stock"]:
                del active_events["stock"][key]

# Slash Commands

@bot.tree.command(name="calculate", description="Calculate Grow a Garden item value")
@app_commands.describe(
    item_name="Name or ID of the item",
    weight="Weight of the item",
    mutation="Mutation type",
    variant="Variant type"
)
async def calculate(
    interaction: discord.Interaction,
    item_name: str,
    weight: float,
    mutation: str = "normal",
    variant: str = "normal"
):
    item_name = item_name.lower()
    fruit = next(
        (f for f in FRUIT_DATA if f["item_id"] == item_name or f["display_name"].lower() == item_name),
        None
    )
    
    if not fruit:
        await interaction.response.send_message(f"❌ Item '{item_name}' not found.", ephemeral=True)
        return

    mut_mult = MUTATIONS.get(mutation.lower(), 1)
    var_mult = VARIANTS.get(variant.lower(), 1)
    
    base = fruit["baseValue"]
    div = fruit["weightDivisor"]
    value = round(base * (weight / div) * mut_mult * var_mult, 2)

    embed = discord.Embed(title="🧮 Item Value Calculator", color=discord.Color.purple())
    embed.add_field(name="Item", value=fruit["display_name"], inline=True)
    embed.add_field(name="Weight", value=weight, inline=True)
    embed.add_field(name="Mutation", value=mutation.title(), inline=True)
    embed.add_field(name="Variant", value=variant.title(), inline=True)
    embed.add_field(name="Calculated Value", value=f"${value:,.2f}", inline=False)
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="setseed", description="Set seed stock channel (Admin only)")
async def set_seed(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        return
    
    update_server_config(interaction.guild.id, interaction.guild.name, "seed", interaction.channel.id)
    await interaction.response.send_message(f"✅ Seed stock channel set to {interaction.channel.mention}")

@bot.tree.command(name="setgear", description="Set gear stock channel (Admin only)")
async def set_gear(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        return
    
    update_server_config(interaction.guild.id, interaction.guild.name, "gear", interaction.channel.id)
    await interaction.response.send_message(f"✅ Gear stock channel set to {interaction.channel.mention}")

@bot.tree.command(name="setegg", description="Set egg stock channel (Admin only)")
async def set_egg(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        return
    
    update_server_config(interaction.guild.id, interaction.guild.name, "egg", interaction.channel.id)
    await interaction.response.send_message(f"✅ Egg stock channel set to {interaction.channel.mention}")

@bot.tree.command(name="setcosmetic", description="Set cosmetic stock channel (Admin only)")
async def set_cosmetic(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        return
    
    update_server_config(interaction.guild.id, interaction.guild.name, "cosmetic", interaction.channel.id)
    await interaction.response.send_message(f"✅ Cosmetic stock channel set to {interaction.channel.mention}")

@bot.tree.command(name="seteventstock", description="Set event stock channel (Admin only)")
async def set_event_stock(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        return
    
    update_server_config(interaction.guild.id, interaction.guild.name, "event_stock", interaction.channel.id)
    await interaction.response.send_message(f"✅ Event stock channel set to {interaction.channel.mention}")

@bot.tree.command(name="setannounce", description="Set announcements channel (Admin only)")
async def set_announce(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        return
    
    update_server_config(interaction.guild.id, interaction.guild.name, "announcement", interaction.channel.id)
    await interaction.response.send_message(f"✅ Announcements channel set to {interaction.channel.mention}")

@bot.tree.command(name="setweather", description="Set weather channel (Admin only)")
async def set_weather(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        return
    
    update_server_config(interaction.guild.id, interaction.guild.name, "weather", interaction.channel.id)
    await interaction.response.send_message(f"✅ Weather channel set to {interaction.channel.mention}")

@bot.tree.command(name="resetstock", description="Reset all stock channels (Admin only)")
async def reset_stock(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        return
    
    guild_str = str(interaction.guild.id)
    if guild_str in server_configs["servers"]:
        config = server_configs["servers"][guild_str]
        config["seed_channel_id"] = None
        config["gear_channel_id"] = None
        config["egg_channel_id"] = None
        config["cosmetic_channel_id"] = None
        config["event_stock_channel_id"] = None
        config["announcement_channel_id"] = None
        config["weather_channel_id"] = None
        save_channels()
    
    await interaction.response.send_message("✅ All stock channels have been reset. Use the set commands to configure new channels.")

@bot.tree.command(name="stock", description="Show current stock information")
async def stock_command(interaction: discord.Interaction):
    await interaction.response.defer()
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(STOCK_API_URL) as r:
                if r.status == 200 and r.content_type == 'application/json':
                    raw = await r.json()
                    stock = raw[0] if isinstance(raw, list) else raw
                else:
                    await interaction.followup.send("❌ Unable to fetch stock data. Please try again later.")
                    return
    except Exception as e:
        await interaction.followup.send("❌ There was an error! Please try again later.")
        logging.error(f"Stock command API error: {e}")
        return
    
    embed = discord.Embed(
        title="📦 Current Stock Information",
        color=0x0099ff,
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_author(name=bot.user.name, icon_url=bot.user.avatar.url if bot.user.avatar else None)
    embed.set_thumbnail(url=bot.user.avatar.url if bot.user.avatar else None)
    
    # Helper function to format stock items
    def format_stock_items(items, max_items=11):
        if not items:
            return "No items available"
        
        formatted = []
        for i, item in enumerate(items[:max_items]):
            item_id = item.get("item_id", "Unknown")
            quantity = item.get("quantity", 0)
            if quantity > 0:
                formatted.append(f"{item_id} x{quantity}")
            else:
                formatted.append(item_id)
        
        return "\n".join(formatted) if formatted else "No items available"
    
    # Add stock fields
    stock_categories = [
        ("gear_stock", "**GEAR STOCK**", "⚙️"),
        ("seed_stock", "**SEEDS STOCK**", "🌱"),
        ("egg_stock", "**EGG STOCK**", "🥚"),
        ("cosmetic_stock", "**COSMETICS STOCK**", "💄"),
        ("eventshop_stock", "**EVENT STOCK**", "🎉")
    ]
    
    for api_key, title, emoji in stock_categories:
        items = stock.get(api_key, [])
        formatted_items = format_stock_items(items)
        embed.add_field(
            name=f"{emoji} {title}",
            value=formatted_items,
            inline=True
        )
    
    await interaction.followup.send(embed=embed)

# Function to run Flask server
def run_flask():
    app.run(host='0.0.0.0', port=5000, debug=False)

# Start Flask server in a separate thread
flask_thread = threading.Thread(target=run_flask, daemon=True)
flask_thread.start()

# Run the bot
bot.run(TOKEN)
