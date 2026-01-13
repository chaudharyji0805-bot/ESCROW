import asyncio
import re
from telethon import events, Button
from config import OWNER_ID, LOG_CHANNEL
import database

# ===================== BASIC =====================

WELCOME_MSG = """🤖 **DVA Escrow Bot**

• Works only in **authorized escrow groups**
• Each group has:
  - Separate escrow form
  - Separate proof channel

Type /help to see commands.
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

# ===================== AUTH GROUP =====================

async def authorize_group(chat_id):
    database.db.settings.update_one(
        {"_id": "auth_group"},
        {"$set": {"chat_id": int(chat_id)}},
        upsert=True
    )

async def deauthorize_group():
    database.db.settings.delete_one({"_id": "auth_group"})

async def is_authorized_group(chat_id):
    doc = database.db.settings.find_one({"_id": "auth_group"})
    return bool(doc and doc.get("chat_id") == int(chat_id))

# ===================== PROOF CHANNEL =====================

async def set_proof_channel(group_id, channel_id):
    database.db.settings.update_one(
        {"_id": f"proof_{group_id}"},
        {"$set": {"channel_id": int(channel_id)}},
        upsert=True
    )

async def unset_proof_channel(group_id):
    database.db.settings.delete_one({"_id": f"proof_{group_id}"})

async def get_proof_channel(group_id):
    doc = database.db.settings.find_one({"_id": f"proof_{group_id}"})
    return doc.get("channel_id") if doc else None

# ===================== HANDLERS =====================

def register_handlers(client):

    # ---------- START ----------
    @client.on(events.NewMessage(pattern="/start"))
    async def start(event):
        await event.reply(WELCOME_MSG)

    # ---------- HELP ----------
    @client.on(events.NewMessage(pattern="/help"))
    async def help_cmd(event):
        await event.reply("""
📖 **DVA Escrow Bot – Command List**

🔐 **OWNER**
/authgroup – Authorize current group  
/deauthgroup – Disable escrow  

📝 **GROUP OWNER**
/form – Set escrow form for group  
/setproof @channel – Set proof channel  
/unsetproof – Remove proof channel  

🤝 **ESCROW**
form – Show escrow form  
/add <amount> <inr|usdt>  
/cancel – Cancel deal  

📊 **REPORTS**
/dreport – Daily report  
/wreport – Weekly report  

📈 **STATS**
/mytotal – Your admin stats  
/mydeals – Your deals  
/leaderboard – Admin leaderboard  
/running – Running deals  

⚠️ Each escrow group has its own:
• Form  
• Proof channel
""")

    # ---------- AUTH GROUP ----------
    @client.on(events.NewMessage(pattern="/authgroup", func=lambda e: e.is_group))
    async def auth_group(event):
        if event.sender_id != OWNER_ID:
            return await event.reply("❌ Owner only.")
        await authorize_group(event.chat_id)
        await event.reply("✅ Group authorized for escrow.")

    @client.on(events.NewMessage(pattern="/deauthgroup"))
    async def deauth_group(event):
        if event.sender_id != OWNER_ID:
            return await event.reply("❌ Owner only.")
        await deauthorize_group()
        await event.reply("🚫 Escrow disabled.")

    # ---------- FORM ----------
    @client.on(events.NewMessage(pattern="/form"))
    async def update_form(event):
        if event.is_private and await is_bot_owner(event.sender_id):
            async with client.conversation(event.sender_id, timeout=60) as conv:
                await conv.send_message("Send GLOBAL form text:")
                try:
                    msg = await conv.get_response()
                except asyncio.TimeoutError:
                    return
                await database.update_form_message(msg.text or "")
                return await conv.send_message("✅ Global form updated.")

        if event.is_group:
            if not await is_authorized_group(event.chat_id):
                return await event.reply("❌ Unauthorized group.")
            if not await is_group_owner(client, event.chat_id, event.sender_id):
                return await event.reply("⚠️ Only group owner.")

            async with client.conversation(event.sender_id, timeout=60) as conv:
                await conv.send_message("Send GROUP form text:")
                try:
                    msg = await conv.get_response()
                except asyncio.TimeoutError:
                    return
                await database.update_form_message(msg.text or "", chat_id=event.chat_id)
                await conv.send_message("✅ Group form updated.")
                await event.reply("📩 Check DM.")

    @client.on(events.NewMessage(func=lambda e: e.is_group and e.text and e.text.lower() == "form"))
    async def show_form(event):
        if not await is_authorized_group(event.chat_id):
            return
        text, _ = await database.get_form_data(chat_id=event.chat_id)
        await event.reply(text)

    # ---------- PROOF CHANNEL ----------
    @client.on(events.NewMessage(pattern=r'/setproof (.+)', func=lambda e: e.is_group))
    async def set_proof(event):
        if not await is_group_owner(client, event.chat_id, event.sender_id):
            return await event.reply("❌ Only group owner.")
        try:
            ch = await client.get_entity(event.pattern_match.group(1))
            await set_proof_channel(event.chat_id, ch.id)
            await event.reply("✅ Proof channel set.")
        except:
            await event.reply("❌ Invalid channel or bot not admin.")

    @client.on(events.NewMessage(pattern="/unsetproof", func=lambda e: e.is_group))
    async def unset_proof(event):
        if not await is_group_owner(client, event.chat_id, event.sender_id):
            return await event.reply("❌ Only group owner.")
        await unset_proof_channel(event.chat_id)
        await event.reply("🗑️ Proof channel removed.")

    # ---------- ADD DEAL ----------
    deal_locks = {}

    @client.on(events.NewMessage(pattern=r'/add (\d+) (inr|usdt|₹|\$)', func=lambda e: e.is_group))
    async def add_deal(event):
        if not await is_authorized_group(event.chat_id):
            return
        if not event.is_reply:
            return await event.reply("Reply to a message with /add <amount> <currency>")

        reply = await event.get_reply_message()
        key = (event.chat_id, reply.id)
        if key in deal_locks:
            return
        deal_locks[key] = True

        try:
            if await database.get_processed_status(reply.id):
                return await event.reply("❌ Already used.")

            if not await database.atomic_start_deal(reply.id):
                return await event.reply("❌ Deal already running.")

            amt = int(event.pattern_match.group(1))
            cur = "inr" if event.pattern_match.group(2) in ["inr", "₹"] else "usdt"
            sym = "₹" if cur == "inr" else "$"

            deal_no = await database.increment_deal(cur)
            deal_id = f"#Escrow{deal_no}"

            sender = await event.get_sender()
            admin = f"@{sender.username}" if sender.username else sender.first_name

            text = f"""🤝 **ESCROW STARTED**
💰 Amount: {amt}{sym}
🆔 ID: {deal_id}
🛡️ Admin: {admin}"""

            btn = [Button.inline("Complete Deal", data=f"comp_{event.sender_id}")]
            sent = await event.respond(text, buttons=btn)

            await database.store_deal(sent.id, reply.id, {
                "admin_id": event.sender_id,
                "amount": amt,
                "currency": cur,
                "deal_id": deal_id,
                "group_id": event.chat_id
            })
        finally:
            deal_locks.pop(key, None)

    # ---------- COMPLETE DEAL ----------
    @client.on(events.CallbackQuery(pattern=br'comp_(\d+)'))
    async def complete(event):
        admin_id = int(event.data.decode().split("_")[1])
        if event.sender_id != admin_id:
            return await event.answer("Not your deal.", alert=True)

        msg = await event.get_message()
        deal = await get_deal(msg.id)
        if not deal:
            return await event.answer("Already completed.", alert=True)

        proof_text = "✅ **DEAL COMPLETED**\n\n" + msg.text
        await event.answer("Deal completed!", alert=True)
        await event.respond(proof_text)

        proof_ch = await get_proof_channel(event.chat_id)
        if proof_ch:
            try:
                await client.send_message(proof_ch, proof_text)
            except:
                pass

        if LOG_CHANNEL:
            try:
                await client.send_message(LOG_CHANNEL, proof_text)
            except:
                pass

        await database.remove_deal(msg.id)
        await database.mark_processed(msg.id, "completed")

        try:
            await msg.delete()
        except:
            pass

    # ---------- REPORTS ----------
    @client.on(events.NewMessage(pattern="/dreport", func=lambda e: e.is_group))
    async def dreport(event):
        count, inr, usdt = await database.get_report(86400)
        await event.reply(f"📅 **Daily Report**\nDeals: {count}\nVolume: {usdt}$ | {inr}₹")

    @client.on(events.NewMessage(pattern="/wreport", func=lambda e: e.is_group))
    async def wreport(event):
        count, inr, usdt = await database.get_report(7 * 86400)
        await event.reply(f"📆 **Weekly Report**\nDeals: {count}\nVolume: {usdt}$ | {inr}₹")
