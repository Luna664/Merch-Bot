# Merch-Bot
# Discord shop bot with cart, stock management, product embeds with image, and private order channel creation.
# Data persisted to a local JSON file (data.json).
# Requirements: discord.py >=2.3

import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import json
import os
import uuid
from typing import Dict, Any

# -------------- CONFIG ----------------
TOKEN = "YOUR_BOT_TOKEN_HERE"  # <- Replace with your bot token
DATA_FILE = "data.json"
GUILD_ID = None  # Optionally lock slash commands to a guild (set to int guild id) or None
# Mention this user ID when a new order is created (from your request):
ORDER_ADMIN_MENTION_ID = 268397787283062786
# Optional: set a staff role ID to be given access to order channels. If None, will try to use role named 'Vendeur'
STAFF_ROLE_ID = None
# Channel name prefix for order channels
ORDER_CHANNEL_PREFIX = "commande-"
# Minimum permissions to use admin commands: manage_guild or manage_messages (adjust as needed)
# --------------------------------------

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- Data helpers ------------

def ensure_data_file():
    if not os.path.exists(DATA_FILE):
        base = {"products": {}, "carts": {}}  # products keyed by product_id
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(base, f, indent=4)


def load_data() -> Dict[str, Any]:
    ensure_data_file()
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data(data: Dict[str, Any]):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

# ---------- Product model helpers ----------

def add_product(name: str, price: int, stock: int, min_qty: int, image_url: str) -> str:
    data = load_data()
    pid = str(uuid.uuid4())[:8]
    data["products"][pid] = {
        "name": name,
        "price": price,
        "stock": stock,
        "min_qty": min_qty,
        "image_url": image_url
    }
    save_data(data)
    return pid


def update_stock(pid: str, new_stock: int):
    data = load_data()
    if pid in data["products"]:
        data["products"][pid]["stock"] = new_stock
        save_data(data)
        return True
    return False


def remove_product(pid: str):
    data = load_data()
    if pid in data["products"]:
        del data["products"][pid]
        save_data(data)
        return True
    return False

# ---------- Cart helpers ----------

def get_cart(user_id: int) -> Dict[str, int]:
    data = load_data()
    return data.get("carts", {}).get(str(user_id), {})


def save_cart(user_id: int, cart: Dict[str, int]):
    data = load_data()
    if "carts" not in data:
        data["carts"] = {}
    data["carts"][str(user_id)] = cart
    save_data(data)


def clear_cart(user_id: int):
    data = load_data()
    if "carts" in data and str(user_id) in data["carts"]:
        del data["carts"][str(user_id)]
        save_data(data)

# ---------- Views & Buttons ----------

class AddToCartButton(discord.ui.Button):
    def __init__(self, product_id: str):
        super().__init__(label="Add to Cart", style=discord.ButtonStyle.primary, custom_id=f"add_{product_id}")
        self.product_id = product_id

    async def callback(self, interaction: discord.Interaction):
        user = interaction.user
        data = load_data()
        prod = data["products"].get(self.product_id)
        if not prod:
            await interaction.response.send_message("Produit introuvable.", ephemeral=True)
            return
        if prod["stock"] <= 0:
            await interaction.response.send_message("Ce produit est en rupture de stock.", ephemeral=True)
            return
        cart = get_cart(user.id)
        cart[self.product_id] = cart.get(self.product_id, 0) + 1
        save_cart(user.id, cart)
        await interaction.response.send_message(f"✅ Ajouté au panier: **{prod['name']}** (Quantité: {cart[self.product_id]})", ephemeral=True)


class CartView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.add_item(CheckOutButton(user_id))
        self.add_item(ViewCartButton(user_id))


class ViewCartButton(discord.ui.Button):
    def __init__(self, user_id: int):
        super().__init__(label="View Cart", style=discord.ButtonStyle.secondary, custom_id=f"view_{user_id}")
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Ce panier ne t'appartient pas.", ephemeral=True)
            return
        await send_cart_embed(interaction, interaction.user)


class CheckOutButton(discord.ui.Button):
    def __init__(self, user_id: int):
        super().__init__(label="Checkout", style=discord.ButtonStyle.success, custom_id=f"checkout_{user_id}")
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Tu ne peux pas valider ce panier.", ephemeral=True)
            return
        await handle_checkout(interaction, interaction.user)

# ---------- Utility embed builders ----------

async def send_product_embed(channel: discord.abc.Messageable, product_id: str, ephemeral: bool = False):
    data = load_data()
    prod = data["products"].get(product_id)
    if not prod:
        await channel.send("Produit non trouvé.")
        return
    embed = discord.Embed(title=prod["name"], description=f"Price: **{prod['price']} AUEC**\nYou need to buy a minimum of {prod['min_qty']}!", color=0x2F3136)
    embed.set_image(url=prod.get("image_url"))
    embed.set_footer(text=f"Stock: {prod['stock']}")
    view = discord.ui.View(timeout=None)
    view.add_item(AddToCartButton(product_id))
    await channel.send(embed=embed, view=view)


async def send_cart_embed(interaction_or_channel, user: discord.User):
    data = load_data()
    cart = get_cart(user.id)
    if not cart:
        await interaction_or_channel.response.send_message("Ton panier est vide.", ephemeral=True)
        return
    data = load_data()
    embed = discord.Embed(title=f"{user.display_name}'s Cart", description="View your cart, remove items, or proceed to checkout.")
    total = 0
    for pid, qty in cart.items():
        prod = data["products"].get(pid)
        if not prod:
            continue
        embed.add_field(name=prod["name"], value=f"Qty: {qty} — Price each: {prod['price']} — Subtotal: {prod['price']*qty}", inline=False)
        total += prod['price'] * qty
    embed.set_footer(text=f"Total: {total} AUEC")

    # Build a view with Checkout + Clear buttons
    view = discord.ui.View(timeout=None)
    view.add_item(CheckOutButton(user.id))

    async def clear_callback(interaction: discord.Interaction):
        clear_cart(user.id)
        await interaction.response.send_message("Panier vidé.", ephemeral=True)

    clear_btn = discord.ui.Button(label="Clear Cart", style=discord.ButtonStyle.danger)
    clear_btn.callback = clear_callback
    view.add_item(clear_btn)

    # If called from a slash/button interaction
    if isinstance(interaction_or_channel, discord.Interaction):
        await interaction_or_channel.response.send_message(embed=embed, view=view, ephemeral=True)
    else:
        await interaction_or_channel.send(embed=embed, view=view)

# ---------- Checkout handling ----------

async def handle_checkout(interaction: discord.Interaction, user: discord.User):
    data = load_data()
    cart = get_cart(user.id)
    if not cart:
        await interaction.response.send_message("Ton panier est vide.", ephemeral=True)
        return

    # Verify stock & minimums
    for pid, qty in cart.items():
        prod = data["products"].get(pid)
        if not prod:
            await interaction.response.send_message(f"Produit introuvable: {pid}", ephemeral=True)
            return
        if qty < prod.get("min_qty", 1):
            await interaction.response.send_message(f"Tu dois commander au moins {prod['min_qty']} de {prod['name']}", ephemeral=True)
            return
        if qty > prod.get("stock", 0):
            await interaction.response.send_message(f"Stock insuffisant pour {prod['name']} (stock: {prod['stock']})", ephemeral=True)
            return

    # Decrement stock
    for pid, qty in cart.items():
        data["products"][pid]["stock"] -= qty

    # Save remaining stock and clear cart
    save_data(data)
    clear_cart(user.id)

    # Create private channel
    guild: discord.Guild = interaction.guild
    if not guild:
        await interaction.response.send_message("Commande: impossible hors d'un serveur.", ephemeral=True)
        return

    # Determine role/user overwrites
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        guild.me: discord.PermissionOverwrite(read_messages=True),
        user: discord.PermissionOverwrite(read_messages=True, send_messages=True)
    }

    # Add staff role if exists
    staff_role = None
    if STAFF_ROLE_ID:
        staff_role = guild.get_role(int(STAFF_ROLE_ID))
    else:
        # try to find role named 'Vendeur'
        staff_role = discord.utils.get(guild.roles, name="Vendeur")
    if staff_role:
        overwrites[staff_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

    # Create channel name safe
    safe_name = f"{ORDER_CHANNEL_PREFIX}{user.display_name}-{str(uuid.uuid4())[:6]}".lower().replace(' ', '-')
    order_channel = await guild.create_text_channel(safe_name, overwrites=overwrites)

    # Prepare order summary
    embed = discord.Embed(title="Nouvelle commande", description=f"Commande de {user.mention}")
    total = 0
    for pid, qty in cart.items():
        prod = data["products"].get(pid)
        if not prod:
            continue
        embed.add_field(name=prod['name'], value=f"Qty: {qty} — Price each: {prod['price']} — Subtotal: {prod['price']*qty}", inline=False)
        total += prod['price'] * qty
    embed.set_footer(text=f"Total: {total} AUEC")

    # Mention the provided admin ID as requested
    admin_mention = f"<@{ORDER_ADMIN_MENTION_ID}>"
    await order_channel.send(content=f"{admin_mention} Nouvelle commande!", embed=embed)

    await interaction.response.send_message(f"✅ Commande enregistrée — salon créé: {order_channel.mention}", ephemeral=True)

# ---------- Bot events & commands ----------

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    if GUILD_ID:
        guild = discord.Object(id=GUILD_ID)
        try:
            await bot.tree.sync(guild=guild)
            print("Slash commands synced to guild")
        except Exception as e:
            print("Failed to sync:", e)
    else:
        try:
            await bot.tree.sync()
            print("Global slash commands synced")
        except Exception as e:
            print("Failed to sync:", e)


# ---------- Slash commands ----------

@app_commands.command(name="shop", description="Afficher la boutique")
async def shop(interaction: discord.Interaction):
    data = load_data()
    products = data.get("products", {})
    if not products:
        await interaction.response.send_message("Aucun produit disponible.", ephemeral=True)
        return
    # Send one embed per product (could be paginated later)
    await interaction.response.defer(ephemeral=True)
    for pid in products:
        await send_product_embed(interaction.channel, pid)
    await interaction.followup.send("Produits affichés.", ephemeral=True)


@app_commands.command(name="add_product", description="(Admin) Ajouter un produit")
@app_commands.describe(name="Nom du produit", price="Prix en AUEC", stock="Quantité en stock", min_qty="Quantité minimale à commander", image_url="URL de l'image du produit")
async def add_product_cmd(interaction: discord.Interaction, name: str, price: int, stock: int, min_qty: int = 1, image_url: str = ""):
    # Simple permission check
    if not (interaction.user.guild_permissions.manage_guild or interaction.user.guild_permissions.manage_messages):
        await interaction.response.send_message("Tu n'as pas la permission.", ephemeral=True)
        return
    pid = add_product(name, price, stock, min_qty, image_url)
    await interaction.response.send_message(f"Produit ajouté: {name} (id: {pid})", ephemeral=True)


@app_commands.command(name="list_products", description="(Admin) Lister les produits avec leurs IDs")
async def list_products_cmd(interaction: discord.Interaction):
    data = load_data()
    products = data.get("products", {})
    if not products:
        await interaction.response.send_message("Aucun produit.", ephemeral=True)
        return
    txt = ""
    for pid, p in products.items():
        txt += f"{pid} — {p['name']} — Price: {p['price']} — Stock: {p['stock']}\n"
    await interaction.response.send_message(f"```
{txt}
```", ephemeral=True)


@app_commands.command(name="view_cart", description="Afficher ton panier")
async def view_cart_cmd(interaction: discord.Interaction):
    await send_cart_embed(interaction, interaction.user)


@app_commands.command(name="clear_cart", description="Vider ton panier")
async def clear_cart_cmd(interaction: discord.Interaction):
    clear_cart(interaction.user.id)
    await interaction.response.send_message("Panier vidé.", ephemeral=True)


# Add commands to tree
bot.tree.add_command(shop)
bot.tree.add_command(add_product_cmd)
bot.tree.add_command(list_products_cmd)
bot.tree.add_command(view_cart_cmd)
bot.tree.add_command(clear_cart_cmd)

# ---------- Start bot ----------

if __name__ == "__main__":
    ensure_data_file()
    bot.run(TOKEN)
