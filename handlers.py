import asyncio
import re
import time
from telethon import events, Button
from telethon.tl.types import User
from config import OWNER_ID
import database

# =====================================================
# BASIC
# =====================================================

WELCOM_MSG = """🤖 **DVA Escrow Bot**

• Works only in **authorized escrow groups**
• Each group has:
  - Separate escrow form
  - Separate proof channel

Type /help to see commands.
"""

# =====================================================
# UTILITIES
# =====================================================

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


# =====================================================
# ADMIN LIMIT WRAPPER (🔥 FIX FOR get_user_limit ERROR)
# =====================================================

async def get_user_limit(user_id, currency):
    """
    Returns:
    - None  → unlimited
    - int   → limit value
    """
    data = await database.get_admin_limit(user_id)

    # Super mod / mod = unlimited
    if data.get("is_mod") or data.get("is_mmod"):
        return None

    return int(data.get(currency.lower(), 0))


# =====================================================
# AUTH GROUP (ONLY BOT OWNER)
# =====================================================

async def authorize_group(chat_id):
    await database.set_auth_group(chat_id)


async def deauthorize_group():
    await database.remove_auth_group()


async def is_authorized_group(chat_id):
    return await database.is_auth_group(chat_id)


# =====================================================
# PROOF CHANNEL (PER GROUP)
# =====================================================

async def set_proof_channel(group_id, channel_id):
    await database.set_proof_channel(group_id, channel_id)


async def unset_proof_channel(group_id):
    await database.unset_proof_channel(group_id)


async def get_proof_channel(group_id):
    return await database.get_proof_channel(group_id)


# =====================================================
# HANDLERS
# =====================================================

def register_handlers(client):

    # -------------------------------------------------
    # START
    # -------------------------------------------------
    @client.on(events.NewMessage(pattern="/start"))
    async def start(event):
        await event.reply(WELCOM_MSG)

    # -------------------------------------------------
    # HELP
    # -------------------------------------------------
    @client.on(events.NewMessage(pattern="/help"))
    async def help_cmd(event):
        await event.reply("""
📖 **DVA Escrow Bot – Full Command List**

🔐 **BOT OWNER**
/authgroup – Authorize current group  
/deauthgroup – Deauthorize current group  

👑 **GROUP OWNER**
/form – Set group escrow form  
/setproof @channel – Set proof channel  
/unsetproof – Remove proof channel  

🛡️ **ADMIN SYSTEM**
/admin <user> – Admin (5000 INR + 50 USDT)
/admin <user> <amount> <inr|usdt> – Custom limit  
/mod <user> – Unlimited INR + USDT  
/smod <user> – Super Mod  
/unadmin <user> – Remove admin  
/unmod <user> – Remove mod/smod  

🤝 **ESCROW**
form – Show form  
/add <amount> <inr|usdt>  
/cancel – Cancel deal (starter admin only)  

📊 **STATS**
/mytotal  
/mydeals  
/leaderboard  
/running  

📈 **REPORTS**
/dreport  
/wreport  

⚠️ Each group has separate:
• Form
• Proof channel
""")

    # -------------------------------------------------
    # AUTH GROUP
    # -------------------------------------------------
    @client.on(events.NewMessage(pattern="/authgroup", func=lambda e: e.is_group))
    async def auth_group(event):
        if event.sender_id != OWNER_ID:
            return await event.reply("❌ Bot owner only.")
        await authorize_group(event.chat_id)
        await event.reply("✅ Group authorized for escrow.")

    @client.on(events.NewMessage(pattern="/deauthgroup", func=lambda e: e.is_group))
    async def deauth_group(event):
        if event.sender_id != OWNER_ID:
            return await event.reply("❌ Bot owner only.")
        await deauthorize_group()
        await event.reply("🚫 Group deauthorized.")

    # -------------------------------------------------
    # FORM
    # -------------------------------------------------
    @client.on(events.NewMessage(pattern="/form"))
    async def update_form(event):
        if event.is_private and await is_bot_owner(event.sender_id):
            async with client.conversation(event.sender_id, timeout=60) as conv:
                await conv.send_message("Send GLOBAL form:")
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
                await conv.send_message("Send GROUP form:")
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

    # -------------------------------------------------
    # ADD DEAL
    # -------------------------------------------------
    deal_locks = {}

    @client.on(events.NewMessage(pattern=r"/add (\d+) (inr|usdt|₹|\$)", func=lambda e: e.is_group))
    async def add_deal(event):
        if not await is_authorized_group(event.chat_id):
            return
        if not event.is_reply:
            return await event.reply("Reply to a form message.")

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

            limit = await get_user_limit(event.sender_id, cur)
            if limit is not None and amt > limit:
                warn = await event.reply(
                    f"❌ Your limit is less than deal amount."
                )
                await asyncio.sleep(60)
                await warn.delete()
                await event.delete()
                return

            sym = "₹" if cur == "inr" else "$"
            deal_no = await database.increment_deal(cur)
            deal_id = f"#Escrow{deal_no}"

            sender = await event.get_sender()
            admin = f"@{sender.username}" if sender.username else sender.first_name

            text = f"""┏━━━━━━━━━━━━━━━━━━━┓
┃ 🤝 **ESCROW STARTED**
┃━━━━━━━━━━━━━━━━━━━┃
┃ 💰 Amount: {amt}{sym}
┃ 🆔 ID: {deal_id}
┃ 🛡️ Admin: {admin}
┗━━━━━━━━━━━━━━━━━━━┛"""

            btn = [Button.inline("Complete Deal", data=f"comp_{event.sender_id}")]
            sent = await event.respond(text, buttons=btn)

            try:
                await client.pin_message(event.chat_id, sent)
            except:
                pass

            await database.store_deal(sent.id, reply.id, {
                "admin_id": event.sender_id,
                "amount": amt,
                "currency": cur,
                "deal_id": deal_id,
                "group_id": event.chat_id
            })
        finally:
            deal_locks.pop(key, None)

    # -------------------------------------------------
    # COMPLETE DEAL (NO GLOBAL LOG)
    # -------------------------------------------------
    @client.on(events.CallbackQuery(pattern=br"comp_(\d+)"))
    async def complete_deal(event):
        admin_id = int(event.data.decode().split("_")[1])
        if event.sender_id != admin_id:
            return await event.answer("Only deal admin can complete.", alert=True)

        msg = await event.get_message()
        deal = await get_deal(msg.id)
        if not deal:
            return await event.answer("Already processed.", alert=True)

        text = "✅ **DEAL COMPLETED**\n\n" + msg.text
        await event.answer("Deal completed!", alert=True)
        await event.respond(text)

        try:
            await client.unpin_message(event.chat_id, msg)
            await msg.delete()
        except:
            pass

        proof_ch = await get_proof_channel(event.chat_id)
        if proof_ch:
            try:
                await client.send_message(proof_ch, text)
            except:
                pass

        await database.remove_deal(msg.id)
        await database.mark_processed(msg.id, "completed")

    # -------------------------------------------------
    # CANCEL DEAL
    # -------------------------------------------------
    @client.on(events.NewMessage(pattern="/cancel", func=lambda e: e.is_group))
    async def cancel_deal(event):
        if not event.is_reply:
            return
        reply = await event.get_reply_message()
        deal = await get_deal(reply.id)
        if not deal:
            return
        if deal["admin_id"] != event.sender_id:
            return await event.reply("❌ Only deal admin can cancel.")

        text = "❌ **DEAL CANCELLED**\n\n" + reply.text
        await event.respond(text)

        try:
            await client.unpin_message(event.chat_id, reply)
            await reply.delete()
        except:
            pass

        await database.remove_deal(reply.id)
        await database.mark_processed(reply.id, "cancelled")
