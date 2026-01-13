import asyncio
import re
from telethon import events, Button
from config import OWNER_ID, LOG_CHANNEL
import database

# ================== AUTH GROUP HELPERS ==================

async def authorize_group(chat_id: int):
    database.db.settings.update_one(
        {"_id": "auth_group"},
        {"$set": {"chat_id": int(chat_id)}},
        upsert=True
    )

async def deauthorize_group():
    database.db.settings.delete_one({"_id": "auth_group"})

async def is_authorized_group(chat_id: int) -> bool:
    doc = database.db.settings.find_one({"_id": "auth_group"})
    return bool(doc and doc.get("chat_id") == int(chat_id))

# ================== BASIC HELPERS ==================

WELCOM_MSG = """🤖 **Welcome to DVA Escrow Bot**

Use this bot in **authorized groups only**  
to manage safe escrow deals.

Type /help to see all commands.
"""

def parse_deal_info(text):
    if not text:
        return None, None
    t = text.lower()
    m = re.findall(r'(\d+)\s*(inr|usdt|₹|\$|usd)', t)
    if m:
        return int(m[0][0]), ("inr" if m[0][1] in ["inr", "₹"] else "usdt")
    nums = re.findall(r'(\d+)', t)
    if nums:
        if "inr" in t or "₹" in t:
            return int(nums[0]), "inr"
        if "usdt" in t or "$" in t or "usd" in t:
            return int(nums[0]), "usdt"
    return None, None

async def is_bot_owner(uid):
    return uid == OWNER_ID

async def is_group_owner(client, chat_id, uid):
    try:
        perms = await client.get_permissions(chat_id, uid)
        return perms.is_creator
    except:
        return False

async def get_deal(msg_id):
    return await database.get_deal(msg_id)

# ================== REGISTER HANDLERS ==================

def register_handlers(client):

    # ---------- START ----------

    @client.on(events.NewMessage(pattern="/start"))
    async def start_handler(event):
        await event.reply(WELCOM_MSG)

    # ---------- HELP (FULL COMMAND LIST) ----------

    @client.on(events.NewMessage(pattern="/help"))
    async def help_handler(event):
        help_text = f"""
📖 **DVA Escrow Bot – Command List**

🔐 **OWNER COMMANDS**
/authgroup – Authorize a group for escrow  
/deauthgroup – Remove escrow access from group  

📝 **FORM COMMANDS**
/form – Update escrow form  
• Owner can set **global form (DM)**  
• Group owner can set **group-specific form**  
• Each authorized group has its **own form**

🤝 **ESCROW COMMANDS (Authorized Groups Only)**
form – Show escrow form  
/add <amount> <inr|usdt> – Start escrow deal  
/cancel – Cancel an active deal (reply required)

📊 **STATS & REPORTS**
/mytotal – Your admin escrow stats  
/mydeals – Your personal deal summary  
/leaderboard – Admin leaderboard  
/running – Running escrow deals  
/dreport – Daily report  
/wreport – Weekly report

⚠️ **IMPORTANT**
• Escrow works **only in authorized groups**  
• Each group has **separate form & data**
"""
        await event.reply(help_text)

    # ---------- AUTH GROUP ----------

    @client.on(events.NewMessage(pattern="/authgroup"))
    async def auth_group(event):
        if event.sender_id != OWNER_ID or not event.is_group:
            return await event.reply("❌ Only bot OWNER can authorize a group.")
        await authorize_group(event.chat_id)
        await event.reply("✅ This group is now **AUTHORIZED** for escrow.")

    @client.on(events.NewMessage(pattern="/deauthgroup"))
    async def deauth_group(event):
        if event.sender_id != OWNER_ID:
            return await event.reply("❌ Only bot OWNER can de-authorize.")
        await deauthorize_group()
        await event.reply("🚫 Escrow access removed from authorized group.")

    # ---------- FORM UPDATE (SAFE, PER-GROUP) ----------

    @client.on(events.NewMessage(pattern="/form"))
    async def update_form(event):

        # OWNER in DM → GLOBAL FORM
        if event.is_private and await is_bot_owner(event.sender_id):
            async with client.conversation(event.sender_id, timeout=60) as conv:
                await conv.send_message("✏️ Send new **GLOBAL** form text:")
                try:
                    msg = await conv.get_response()
                except asyncio.TimeoutError:
                    return await conv.send_message("❌ Timeout.")
                await database.update_form_message(msg.text or "")
                return await conv.send_message("✅ Global form updated.")

        # GROUP OWNER → GROUP FORM
        if event.is_group:
            if not await is_authorized_group(event.chat_id):
                return await event.reply("❌ This group is not authorized.")
            if not await is_group_owner(client, event.chat_id, event.sender_id):
                return await event.reply("⚠️ Only **Group Owner** can update the form.")

            async with client.conversation(event.sender_id, timeout=60) as conv:
                await conv.send_message("✏️ Send new **GROUP** form text:")
                try:
                    msg = await conv.get_response()
                except asyncio.TimeoutError:
                    return await conv.send_message("❌ Timeout.")
                await database.update_form_message(msg.text or "", chat_id=event.chat_id)
                await conv.send_message("✅ Group form updated.")
                await event.reply("📩 Check your DM to update the form.")

    # ---------- FORM TRIGGER ----------

    @client.on(events.NewMessage(func=lambda e: e.is_group and e.text and e.text.lower() == "form"))
    async def form_trigger(event):
        if not await is_authorized_group(event.chat_id):
            return
        text, _ = await database.get_form_data(chat_id=event.chat_id)
        await event.reply(text)

    # ---------- CANCEL DEAL ----------

    @client.on(events.NewMessage(pattern="/cancel", func=lambda e: e.is_group))
    async def cancel_deal(event):
        if not await is_authorized_group(event.chat_id) or not event.is_reply:
            return
        reply = await event.get_reply_message()
        deal = await get_deal(reply.id)
        if not deal:
            return await event.reply("❌ This is not an active deal.")
        await event.respond("❌ **DEAL CANCELLED**\n\n" + reply.text)
        await database.remove_deal(reply.id)
        await database.mark_processed(reply.id, "cancelled")
        try:
            await reply.delete()
        except:
            pass

    # ---------- ADD DEAL ----------

    deal_locks = {}

    @client.on(events.NewMessage(pattern=r'/add (\d+) (inr|usdt|₹|\$)', func=lambda e: e.is_group))
    async def add_deal(event):
        if not await is_authorized_group(event.chat_id):
            return await event.reply("❌ This group is not authorized.")
        if not event.is_reply:
            return await event.reply("Reply to a message with `/add <amount> <currency>`")

        reply = await event.get_reply_message()
        key = (event.chat_id, reply.id)
        if key in deal_locks:
            return
        deal_locks[key] = True

        try:
            if await database.get_processed_status(reply.id):
                return await event.reply("❌ This message was already used.")

            if not await database.atomic_start_deal(reply.id):
                return await event.reply("❌ A deal is already running on this message.")

            amt = int(event.pattern_match.group(1))
            cur = "inr" if event.pattern_match.group(2) in ["inr", "₹"] else "usdt"
            sym = "₹" if cur == "inr" else "$"

            deal_no = await database.increment_deal(cur)
            deal_id = f"#Escrow{deal_no}"

            sender = await event.get_sender()
            admin_mention = f"@{sender.username}" if sender.username else sender.first_name

            text = f"""
🤝 **ESCROW STARTED**
💰 Amount: {amt}{sym}
🆔 ID: {deal_id}
🛡️ Admin: {admin_mention}
"""
            btn = [Button.inline("Complete Deal", data=f"comp_{event.sender_id}")]
            sent = await event.respond(text, buttons=btn)

            await database.store_deal(sent.id, reply.id, {
                "admin_id": event.sender_id,
                "amount": amt,
                "currency": cur,
                "deal_id": deal_id
            })
        finally:
            deal_locks.pop(key, None)

    # ---------- COMPLETE DEAL ----------

    @client.on(events.CallbackQuery(pattern=br'comp_(\d+)'))
    async def complete_callback(event):
        admin_id = int(event.data.decode().split("_")[1])
        if event.sender_id != admin_id:
            return await event.answer("Only the deal admin can complete.", alert=True)

        msg = await event.get_message()
        deal = await get_deal(msg.id)
        if not deal:
            return await event.answer("Deal already processed.", alert=True)

        await event.answer("Deal completed!", alert=True)
        await event.respond("✅ **DEAL COMPLETED**\n\n" + msg.text)
        await database.remove_deal(msg.id)
        await database.mark_processed(msg.id, "completed")

        try:
            await msg.delete()
        except:
            pass

        if LOG_CHANNEL:
            try:
                await client.send_message(LOG_CHANNEL, f"✅ Deal completed: {deal['deal_id']}")
            except:
                pass
