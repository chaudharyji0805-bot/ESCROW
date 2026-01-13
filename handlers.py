# ===================== IMPORTS =====================
import asyncio
import re
from telethon import events, Button
from telethon.tl.types import ChannelParticipantsAdmins
from config import OWNER_ID, LOG_CHANNEL
import database

# ===================== BASIC =====================

WELCOME_MSG = """🤖 **DVA Escrow Bot**

• Works only in authorized escrow groups
• Each group has:
  - Separate escrow form
  - Separate proof channel

Type /help to see all commands.
"""

# ===================== UTILS =====================

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

async def is_group_owner(client, chat_id, user_id):
    try:
        perms = await client.get_permissions(chat_id, user_id)
        return perms.is_creator
    except:
        return False

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
📖 **DVA Escrow – Command List**

🔐 **BOT OWNER**
/authgroup – Authorize group  
/deauthgroup – Disable escrow  

📝 **GROUP OWNER**
/form – Set escrow form  
/setproof @channel  
/unsetproof  

👮 **ADMIN / MOD**
/admin @user <amount> <inr|usdt>  
/mod @user – Unlimited  
/smod @user – Can manage admins  
/unadmin @user  
/unmod @user  

🤝 **ESCROW**
form – Show form  
/add <amount> <currency>  
/cancel – Cancel deal  
rlz / release – Seller release request  

📊 **STATS**
/mytotal  
/mydeals  
/leaderboard  
/running  
/dreport  
/wreport
""")

    # ===================== ADD DEAL =====================
    deal_locks = {}

    @client.on(events.NewMessage(pattern=r'/add (\d+) (inr|usdt|₹|\$)', func=lambda e: e.is_group))
    async def add_deal(event):
        if not event.is_reply:
            return await event.reply("Reply to form with `/add amount currency`")

        reply = await event.get_reply_message()
        lock = (event.chat_id, reply.id)
        if lock in deal_locks:
            return
        deal_locks[lock] = True

        try:
            admin_id = event.sender_id
            amount = int(event.pattern_match.group(1))
            currency = "inr" if event.pattern_match.group(2) in ["inr", "₹"] else "usdt"

            # LIMIT CHECK
            limit = await database.get_admin_limit(admin_id)
            if not limit.get("is_mod") and not limit.get("is_mmod"):
                max_limit = limit.get(currency, 0)
                if amount > max_limit:
                    warn = await event.reply(
                        f"❌ @{event.sender.username}\nYour limit is less than deal amount."
                    )
                    await asyncio.sleep(60)
                    await warn.delete()
                    await event.delete()
                    return

            deal_id = f"#Escrow{await database.increment_deal(currency)}"
            sender = await event.get_sender()

            text = f"""📌 **ESCROW STARTED**
💰 Amount: {amount} {currency.upper()}
🆔 ID: {deal_id}
🛡️ Admin: @{sender.username or sender.first_name}"""

            btn = [Button.inline("Complete Deal", data=f"comp_{admin_id}")]
            sent = await event.respond(text, buttons=btn)
            await client.pin_message(event.chat_id, sent)

            await database.store_deal(sent.id, reply.id, {
                "admin_id": admin_id,
                "admin_mention": f"@{sender.username}",
                "amount": amount,
                "currency": currency,
                "seller": reply.sender.username,
                "seller_id": reply.sender_id,
                "buyer": "Unknown",
                "buyer_id": None,
            })

        finally:
            deal_locks.pop(lock, None)

    # ===================== COMPLETE DEAL =====================
    @client.on(events.CallbackQuery(pattern=br'comp_(\d+)'))
    async def complete(event):
        admin_id = int(event.data.decode().split("_")[1])
        if event.sender_id != admin_id:
            return await event.answer("Only deal admin can complete.", alert=True)

        msg = await event.get_message()
        deal = await database.get_deal(msg.id)
        if not deal:
            return

        text = "✅ **DEAL COMPLETED**\n\n" + msg.text
        await event.respond(text)

        # proof + log
        proof = await database.get_proof_channel(event.chat_id)
        if proof:
            await client.send_message(proof, text)
        if LOG_CHANNEL:
            await client.send_message(LOG_CHANNEL, text)

        await client.unpin_message(event.chat_id, msg)
        await msg.delete()
        await database.remove_deal(msg.id)

    # ===================== CANCEL DEAL =====================
    @client.on(events.NewMessage(pattern="/cancel", func=lambda e: e.is_group))
    async def cancel(event):
        if not event.is_reply:
            return

        reply = await event.get_reply_message()
        deal = await database.get_deal(reply.id)
        if not deal:
            return

        if event.sender_id != deal["admin_id"]:
            return await event.reply("❌ Only deal admin can cancel.")

        text = "❌ **DEAL CANCELLED**\n\n" + reply.text
        await event.respond(text)

        await client.unpin_message(event.chat_id, reply)
        await reply.delete()
        await database.remove_deal(reply.id)

    # ===================== RELEASE (RLZ) =====================
    @client.on(events.NewMessage(func=lambda e: e.is_group and e.text.lower() in ["rlz", "release"]))
    async def release(event):
        for deal_id, deal in (await database.get_running_deals()).items():
            if event.sender_id == deal.get("seller_id"):
                await event.reply(
                    f"🔔 Seller requested release\n\n"
                    f"👤 Seller: @{deal['seller']}\n"
                    f"👤 Buyer: @{deal.get('buyer','Unknown')}\n\n"
                    f"⚠️ {deal['admin_mention']} please verify & release."
                )
                return

            # clone seller
            if event.sender.username == deal.get("seller"):
                admins = await client.get_participants(
                    event.chat_id, filter=ChannelParticipantsAdmins
                )
                tags = " ".join([f"[{a.first_name}](tg://user?id={a.id})" for a in admins])
                await event.reply(
                    f"🚨 **CLONE SELLER ALERT**\n\n"
                    f"Real Seller: @{deal['seller']} ({deal['seller_id']})\n"
                    f"Clone User: @{event.sender.username} ({event.sender_id})\n\n"
                    f"{tags}"
                )
                return
