import asyncio
import re
from telethon import events, Button
from config import OWNER_ID, LOG_CHANNEL
import database

WELCOM_MSG = """🤖 Welcome to DVA Escrow Bot!
Use in groups for managing safe deals.
Type /help for commands."""

def parse_deal_info(text):
    if not text:
        return None, None
    text_lower = text.lower()
    matches = re.findall(r'(\d+)\s*(inr|usdt|₹|\$|usd)', text_lower)
    if not matches:
        amounts = re.findall(r'(\d+)', text_lower)
        if amounts:
            if any(k in text_lower for k in ['inr', '₹']):
                return int(amounts[0]), 'inr'
            if any(k in text_lower for k in ['usdt', '$', 'usd']):
                return int(amounts[0]), 'usdt'
        return None, None
    amount = int(matches[0][0])
    currency_raw = matches[0][1]
    if currency_raw in ['inr', '₹']:
        currency = 'inr'
    else:
        currency = 'usdt'
    return amount, currency

async def is_bot_owner(user_id):
    return user_id == OWNER_ID

async def is_group_owner(client, chat_id, user_id):
    # Bot Owner is NOT automatically the GC owner in a group.
    try:
        # 1. Primary check: get_permissions (standard Telethon 1.x)
        perms = await client.get_permissions(chat_id, user_id)
        if perms.is_creator:
            return True
            
        # 2. Secondary check for "Founder" status in large groups
        try:
            p = await client.get_participant(chat_id, user_id)
            from telethon.tl.types import ChannelParticipantCreator, ChatParticipantCreator
            if hasattr(p, 'participant') and isinstance(p.participant, (ChannelParticipantCreator, ChatParticipantCreator)):
                return True
            if isinstance(p, (ChannelParticipantCreator, ChatParticipantCreator)):
                return True
        except:
            pass
            
    except Exception as e:
        print(f"DEBUG: Permission check error for {user_id} in {chat_id}: {e}")
    return False

async def get_deal(msg_id):
    return await database.get_deal(msg_id)

def register_handlers(client):
    
    @client.on(events.NewMessage(pattern='/start', chats=OWNER_ID))
    @client.on(events.NewMessage(pattern='/start', func=lambda e: e.is_private))
    async def start_handler(event):
        await event.reply(WELCOM_MSG)

    @client.on(events.NewMessage(pattern='/help', func=lambda e: e.is_private))
    async def help_handler(event):
        help_text = "Commands:\n/form - (Owner only) Update the form message."
        await event.reply(help_text)

    # Owner command to update form message (Global owner or Group Owner in their group)
    @client.on(events.NewMessage(pattern='/form'))
    async def update_form(event):
        # Allow bot owner in DM
        if event.is_private and await is_bot_owner(event.sender_id):
            async with client.conversation(OWNER_ID) as conv:
                await conv.send_message("Please send the new GLOBAL form message. I will preserve the formatting.")
                msg = await conv.get_response()
                await database.update_form_message(msg.text, msg.entities)
                await conv.send_message("✅ Global Form message updated!")
            return

        # Allow Group Owner in Group
        if event.is_group:
            if await is_group_owner(client, event.chat_id, event.sender_id):
                async with client.conversation(event.sender_id) as conv:
                    await conv.send_message(f"Please send the new form message for the group `{event.chat.title}`.")
                    msg = await conv.get_response()
                    # Store per-chat form
                    await database.update_form_message(msg.text, msg.entities, chat_id=event.chat_id)
                    await conv.send_message(f"✅ Form message for `{event.chat.title}` updated!")
                await event.reply("Check your DM to update the form.")
            else:
                await event.reply("⚠️ Only the **Group Owner** can change the form.")

    # Group trigger for "form"
    @client.on(events.NewMessage(func=lambda e: e.is_group and e.text.lower() == "form"))
    async def form_trigger(event):
        text, entities_dict = await database.get_form_data(chat_id=event.chat_id)
        
        # Safer deserialization for Telethon entities
        actual_entities = []
        if entities_dict:
            try:
                from telethon.tl.all_tlobjects import tlobjects
                for e_dict in entities_dict:
                    type_name = e_dict.get('_')
                    if type_name in tlobjects:
                        actual_entities.append(tlobjects[type_name].from_dict(e_dict))
            except Exception as e:
                print(f"Error deserializing entities: {e}")

        await event.reply(text, formatting_entities=actual_entities)

    # Tip trigger
    @client.on(events.NewMessage(func=lambda e: e.is_group and e.text.lower() == "tip"))
    async def tip_handler(event):
        if not event.is_reply:
            return

        # Check if the sender is an admin
        permissions = await client.get_permissions(event.chat_id, event.sender_id)
        if not permissions.is_admin and event.sender_id != OWNER_ID:
            return

        reply_msg = await event.get_reply_message()
        if not reply_msg:
            return

        # 1. Check if already tipped
        tipped_admin = await database.get_tipped_admin(reply_msg.id)
        if tipped_admin and tipped_admin != event.sender_id:
            try:
                await event.delete()
            except:
                pass
            return

        # 2. Parse amount and check limit
        amount, currency = parse_deal_info(reply_msg.text)
        if amount and currency:
            limit_data = await database.get_admin_limit(event.sender_id)
            if not limit_data["is_mod"] and event.sender_id != OWNER_ID:
                limit = limit_data.get(currency, 0)
                if amount > limit:
                    try:
                        await event.delete()
                    except:
                        pass
                    return

        # Mark as tipped
        await database.mark_as_tipped(reply_msg.id, event.sender_id)

    @client.on(events.NewMessage(pattern=r'/admin (@?\w+|\d+|me) (mmod|mod|\d+) ?(inr|usdt|₹|\$)?', func=lambda e: e.is_group))
    async def set_limit(event):
        # Strictly GC Owner or MMod can set limits.
        is_gc_owner = await is_group_owner(client, event.chat_id, event.sender_id)
        limit_data_sender = await database.get_admin_limit(event.sender_id)
        
        if not is_gc_owner and not limit_data_sender.get("is_mmod"):
            return

        user_match = event.pattern_match.group(1)
        role_or_limit = event.pattern_match.group(2)
        currency_input = event.pattern_match.group(3)

        target_id = None
        if user_match == 'me':
            target_id = event.sender_id
        else:
            try:
                # Support for User IDs and Usernames
                if user_match.isdigit():
                    target_id = int(user_match)
                else:
                    user = await client.get_entity(user_match)
                    target_id = user.id
            except Exception as e:
                await event.reply(f"❌ Error: Could not find user `{user_match}`. Use their User ID if the username doesn't work.")
                return

        if role_or_limit.lower() == 'mmod':
            await database.set_admin_limit(target_id, is_mmod=True)
            await event.reply(f"✅ User `{user_match}` is now a **Master Mod** (Unlimited + Can manage others).")
        elif role_or_limit.lower() == 'mod':
            await database.set_admin_limit(target_id, is_mod=True)
            await event.reply(f"✅ User `{user_match}` is now a **Moderator** (Unlimited Escrow).")
        else:
            amount = int(role_or_limit)
            currency = "inr" if currency_input in ["inr", "₹", None] else "usdt"
            await database.set_admin_limit(target_id, amount=amount, currency=currency)
            await event.reply(f"✅ Escrow limit for `{user_match}` set to **{amount} {currency.upper()}**.")

    @client.on(events.NewMessage(pattern=r'/unadmin (@?\w+|\d+|me)', func=lambda e: e.is_group))
    async def unadmin_handler(event):
        is_gc_owner = await is_group_owner(client, event.chat_id, event.sender_id)
        limit_data_sender = await database.get_admin_limit(event.sender_id)
        
        if not is_gc_owner and not limit_data_sender.get("is_mmod"):
            return

        user_match = event.pattern_match.group(1)
        target_id = None
        if user_match == 'me':
            target_id = event.sender_id
        else:
            try:
                if user_match.isdigit():
                    target_id = int(user_match)
                else:
                    user = await client.get_entity(user_match)
                    target_id = user.id
            except:
                await event.reply("❌ User not found.")
                return

        await database.set_admin_limit(target_id, amount=0, currency="inr", is_mod=False, is_mmod=False)
        await database.set_admin_limit(target_id, amount=0, currency="usdt", is_mod=False, is_mmod=False)
        await event.reply(f"✅ Removed all admin/limit privileges for `{user_match}`.")

    # Cancel command
    @client.on(events.NewMessage(pattern='/cancel', func=lambda e: e.is_group))
    async def cancel_deal(event):
        if not event.is_reply:
            return
        
        is_owner = await is_group_owner(client, event.chat_id, event.sender_id)
        permissions = await client.get_permissions(event.chat_id, event.sender_id)
        if not permissions.is_admin and not is_owner:
            return

        reply_msg = await event.get_reply_message()
        deal_data = await get_deal(reply_msg.id)
        
        if not deal_data:
            await event.reply("❌ This is not an active deal message.")
            return

        # Send new cancellation message
        cancel_text = f"❌ **DEAL CANCELLED**\n\n{reply_msg.text}"
        await event.respond(cancel_text)

        # Delete original message
        try:
            await reply_msg.delete()
        except:
            pass

        # Cleanup
        await database.remove_deal(reply_msg.id)
        await database.decrement_deal(deal_data["currency"])
        
        # Lock the original FORM message too
        form_id = deal_data.get("form_id")
        if form_id:
            await database.mark_processed(form_id, "cancelled")
        
        await database.mark_processed(reply_msg.id, "cancelled")

    # Transient lock for /add to prevent race conditions
    deal_locks = {}

    @client.on(events.NewMessage(pattern=r'/add (\d+) (inr|usdt|₹|\$)', func=lambda e: e.is_group))
    async def add_deal(event):
        if not event.is_reply:
            await event.reply("Please reply to a message with `/add <amount> <currency>`")
            return

        reply_msg = await event.get_reply_message()
        
        # 1. Race Condition Prevention - Transient Lock
        lock_key = (event.chat_id, reply_msg.id)
        if lock_key in deal_locks:
            return 
        deal_locks[lock_key] = True

        try:
            # 2. Check if message was already used (completed/cancelled)
            processed_status = await database.get_processed_status(reply_msg.id)
            if processed_status:
                await event.reply(f"❌ This deal has been **{processed_status}**. You cannot use this message again.")
                return

            # 3. ATOMIC START CHECK (The Ultimate Defense against duplicates)
            if not await database.atomic_start_deal(reply_msg.id):
                await event.reply("❌ **ERROR**: A deal is already being started or is already running for this message.\nYou cannot start multiple deals on the same form.")
                return

            try:
                # 4. Permissions & Limits
                is_gc_owner = await is_group_owner(client, event.chat_id, event.sender_id)
                limit_data = await database.get_admin_limit(event.sender_id)
                
                # GC Owner gets implicit unlimited. Bot Owner is treated like normal user.
                has_unlimited = is_gc_owner or limit_data.get("is_mod") or limit_data.get("is_mmod")
                
                amount_val = int(event.pattern_match.group(1))
                currency_input = event.pattern_match.group(2).lower()
                currency = "inr" if currency_input in ["inr", "₹"] else "usdt"

                if not has_unlimited:
                    # Check if admin has group permissions
                    permissions = await client.get_permissions(event.chat_id, event.sender_id)
                    if not permissions.is_admin:
                        # Auto-reset: If they are no longer an admin, clear their limits
                        await database.set_admin_limit(event.sender_id)
                        raise ValueError("STOP_REVOKED")

                    limit = limit_data.get(currency, 0)
                    if amount_val > limit:
                        await event.reply(f"❌ Your limit is not enough for this deal.\nYour limit for {currency.upper()} is {limit}.")
                        raise ValueError("STOP_LIMIT")

                # 5. Currency & Amount Validation
                form_amount, form_currency = parse_deal_info(reply_msg.text)
                if form_currency and form_currency != currency:
                    await event.reply(f"❌ Mismatch! This is a **{form_currency.upper()}** deal according to the form.")
                    raise ValueError("STOP_CURRENCY")
                
                if form_amount and amount_val < form_amount:
                    await event.reply(f"❌ Your dealing amount is less than actual deal ({form_amount} {form_currency.upper()}).")
                    raise ValueError("STOP_AMOUNT")

                symbol = "₹" if currency == "inr" else "$"
                deal_num = await database.increment_deal(currency)
                deal_id = f"#Escrow{deal_num}"
                
                # Get buyer/seller
                buyer_mention = "@Buyer"
                seller_mention = "@Seller"
                if reply_msg and reply_msg.text:
                    text_to_parse = reply_msg.text
                    seller_match = re.search(r'Seller:\s*(@?\w+)', text_to_parse, re.IGNORECASE)
                    if seller_match: seller_mention = seller_match.group(1)
                    buyer_match = re.search(r'Buyer:\s*(@?\w+)', text_to_parse, re.IGNORECASE)
                    if buyer_match: buyer_mention = buyer_match.group(1)

                sender = await event.get_sender()
                admin_mention = f"@{sender.username}" if getattr(sender, 'username', None) else f"[{sender.first_name}](tg://user?id={sender.id})"

                text = f"""┏━━━━━━━━━━━━━━━━━━━┓
┃  🤝 **Escrow Received**
┃━━━━━━━━━━━━━━━━━━━┃
┃ 💰 **Amount**: {amount_val}{symbol}
┃ 👤 **Buyer**: {buyer_mention}
┃ 👨‍💼 **Seller**: {seller_mention}
┃ 🆔 **ID**: {deal_id}
┃ 🛡️ **Admin**: {admin_mention}
┗━━━━━━━━━━━━━━━━━━━┛"""

                buttons = [Button.inline("Complete Deal", data=f"comp_{event.sender_id}")]
                sent_msg = await event.respond(text, buttons=buttons)
                
                try:
                    await client.pin_message(event.chat_id, sent_msg)
                except:
                    pass

                # Store deal info
                await database.store_deal(sent_msg.id, reply_msg.id, {
                    "admin_id": event.sender_id,
                    "admin_mention": admin_mention,
                    "amount": amount_val,
                    "currency": currency,
                    "deal_id": deal_id,
                    "buyer": buyer_mention,
                    "seller": seller_mention
                })
            except Exception as e:
                # If we failed before store_deal, we MUST unmark the form Busy state
                db = await database.load_db()
                if str(reply_msg.id) in db.get("active_forms", {}):
                    if db["active_forms"][str(reply_msg.id)] == "processing":
                        del db["active_forms"][str(reply_msg.id)]
                        await database.save_db(db)
                if not str(e).startswith("STOP_"):
                    print(f"DEBUG: Error in add_deal logic: {e}")
                return
        finally:
            # Release the transient lock
            if lock_key in deal_locks:
                del deal_locks[lock_key]

    # Callback for Complete Deal
    @client.on(events.CallbackQuery(pattern=br'comp_(\d+)'))
    async def complete_callback(event):
        authorized_admin_id = int(event.data.decode().split('_')[1])
        
        if event.sender_id != authorized_admin_id:
            deal_data = await database.get_deal(event.message_id)
            admin_mention = deal_data["admin_mention"] if deal_data else "the admin"
            await event.answer(f"YOU didn't start the deal it can be completed by {admin_mention}", alert=True)
            return

        # Fetch the message object properly
        msg = await event.get_message()
        if not msg:
            await event.answer("Error: Message not found.", alert=True)
            return

        deal_data = await get_deal(msg.id)
        if not deal_data:
            await event.answer("This deal has already been processed or is not active.", alert=True)
            return

        # Prepare completed message text
        original_text = msg.text
        completed_text = f"✅ **DEAL COMPLETED**\n\n{original_text}"

        # Answer the callback
        await event.answer("Deal Completed!", alert=True)

        # Update stats
        amount, currency = parse_deal_info(original_text)
        if amount and currency:
            admin_id = deal_data.get("admin_id") or authorized_admin_id
            # Try to get better mention for admin
            try:
                a_user = await client.get_entity(admin_id)
                admin_mention = f"@{a_user.username}" if a_user.username else a_user.first_name
            except:
                admin_mention = deal_data.get("admin_mention", "Admin")
            await database.update_stats(admin_id, amount, currency, is_admin=True, username=admin_mention)
        
        # Send new completed message
        await event.respond(completed_text)

        # Delete original message
        try:
            await msg.delete()
        except:
            pass

        # Cleanup
        await database.remove_deal(msg.id)
        
        # Lock the original FORM message
        form_id = deal_data.get("form_id")
        if form_id:
            await database.mark_processed(form_id, "completed")
            
        await database.mark_processed(msg.id, "completed")

        # Log to channel
        try:
            await client.send_message(LOG_CHANNEL, completed_text)
        except Exception as e:
            print(f"Error logging to channel: {e}")

    # Stats commands
    @client.on(events.NewMessage(pattern='/mytotal', func=lambda e: e.is_group))
    async def my_total(event):
        permissions = await client.get_permissions(event.chat_id, event.sender_id)
        if not permissions.is_admin and not await is_group_owner(client, event.chat_id, event.sender_id):
            await event.reply("⚠️ You are not an admin of this group.")
            return
            
        stats = await database.get_stats(event.sender_id, is_admin=True)
        sender = await event.get_sender()
        mention = f"@{sender.username}" if getattr(sender, 'username', None) else sender.first_name
        
        await event.reply(f"┏━━━━━━━━━━━━━━━━━━━┓\n┃ 📌 **Your Admin Stats**\n┃━━━━━━━━━━━━━━━━━━━┃\n┃ 👤 **Admin**: {mention}\n┃ ✅ **Deals**: {stats['deals']}\n┃ 💰 **Total**: {stats['amount_usdt']}$ | {stats['amount_inr']}₹\n┗━━━━━━━━━━━━━━━━━━━┛")

    @client.on(events.NewMessage(pattern='/mydeals', func=lambda e: e.is_group))
    async def my_deals(event):
        stats = await database.get_stats(event.sender_id, is_admin=False)
        sender = await event.get_sender()
        mention = f"@{sender.username}" if getattr(sender, 'username', None) else sender.first_name
        
        await event.reply(f"┏━━━━━━━━━━━━━━━━━━━┓\n┃ 📌 **Your Deal Summary**\n┃━━━━━━━━━━━━━━━━━━━┃\n┃ 👤 **User**: {mention}\n┃ ✅ **Total Deals**: {stats['deals']}\n┃ 💰 **Total Amount**: {stats['amount_usdt']}$ | {stats['amount_inr']}₹\n┗━━━━━━━━━━━━━━━━━━━┛")

    @client.on(events.NewMessage(pattern='/leaderboard', func=lambda e: e.is_group))
    async def leaderboard(event):
        admins, total_deals, total_inr, total_usdt = await database.get_leaderboard()
        
        text = f"""┏━━━━━━━━━━━━━━━━━━━┓
┃  📊 **Admin Leaderboard**
┃━━━━━━━━━━━━━━━━━━━┃
┃ 📌 **Total Deals**: {total_deals}
┃ 💰 **Total Amount**: {total_usdt}$ | {total_inr}₹
┗━━━━━━━━━━━━━━━━━━━┛\n\n"""
        
        for i, (uid, stats) in enumerate(admins[:15], 1):
            name = stats.get("username", "Unknown")
            deals = stats['deals']
            amt_usdt = stats['amount_usdt']
            amt_inr = stats['amount_inr']
            text += f"**{i}.** {name} — `{deals}` | `{amt_usdt}$` | `{amt_inr}₹`\n"
            
        await event.reply(text)

    @client.on(events.NewMessage(pattern='/running', func=lambda e: e.is_group))
    async def running_deals(event):
        deals = await database.get_running_deals()
        if not deals:
            await event.reply("⏳ **No running deals currently.**")
            return

        total_amt_usdt = 0
        total_amt_inr = 0
        deal_lines = []
        
        for mid, data in deals.items():
            amt = float(data.get("amount", 0))
            curr = data.get("currency", "inr")
            if curr == "usdt": total_amt_usdt += amt
            else: total_amt_inr += amt
            
            symbol = "$" if curr == "usdt" else "₹"
            buyer = data.get("buyer", "@Buyer")
            seller = data.get("seller", "@Seller")
            admin = data.get("admin_mention", "@Admin")
            did = data.get("deal_id", "#Escrow")
            
            deal_lines.append(f"• **{did}** — `{amt}{symbol}` | B: {buyer} | S: {seller} | Adm: {admin}")

        text = f"""┏━━━━━━━━━━━━━━━━━━━┓
┃  ⏳ **Running Deals**
┃━━━━━━━━━━━━━━━━━━━┃
┃ 📌 **Count**: {len(deals)}
┃ 💰 **Total**: {total_amt_usdt}$ | {total_amt_inr}₹
┗━━━━━━━━━━━━━━━━━━━┛\n\n"""
        
        text += "\n".join(deal_lines)
        await event.reply(text)

    @client.on(events.NewMessage(pattern='/dreport', func=lambda e: e.is_group))
    async def daily_report(event):
        permissions = await client.get_permissions(event.chat_id, event.sender_id)
        is_gc = await is_group_owner(client, event.chat_id, event.sender_id)
        
        if not permissions.is_admin and not is_gc:
            await event.reply("⚠️ Only **Admins** and the **Group Owner** can view reports.")
            return
        
        count, inr, usdt = await database.get_report(24*3600)
        text = f"""┏━━━━━━━━━━━━━━━━━━━┓
┃  📅 **Daily Report**
┃━━━━━━━━━━━━━━━━━━━┃
┃ 📊 **Total Deals**: {count}
┃ 💰 **Total Volume**: {usdt}$ | {inr}₹
┗━━━━━━━━━━━━━━━━━━━┛"""
        await event.reply(text)

    @client.on(events.NewMessage(pattern='/wreport', func=lambda e: e.is_group))
    async def weekly_report(event):
        permissions = await client.get_permissions(event.chat_id, event.sender_id)
        is_gc = await is_group_owner(client, event.chat_id, event.sender_id)

        if not permissions.is_admin and not is_gc:
            await event.reply("⚠️ Only **Admins** and the **Group Owner** can view reports.")
            return
        
        count, inr, usdt = await database.get_report(7*24*3600)
        text = f"""┏━━━━━━━━━━━━━━━━━━━┓
┃  📆 **Weekly Report**
┃━━━━━━━━━━━━━━━━━━━┃
┃ 📊 **Total Deals**: {count}
┃ 💰 **Total Volume**: {usdt}$ | {inr}₹
┗━━━━━━━━━━━━━━━━━━━┛"""
        await event.reply(text)
