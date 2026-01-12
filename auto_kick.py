import asyncio
import time
from database import deals, users
from pyrogram.enums import ChatMemberStatus

AUTO_KICK_TIME = 600  # 10 minutes

async def auto_kick_worker(app):
    while True:
        now = int(time.time())

        for deal in deals.find({"status": "completed"}):
            if now - deal["completed_at"] >= AUTO_KICK_TIME:
                for uid in [deal["buyer"], deal["seller"]]:
                    try:
                        member = await app.get_chat_member(deal["group_id"], uid)
                        if member.status not in (
                            ChatMemberStatus.ADMINISTRATOR,
                            ChatMemberStatus.OWNER
                        ):
                            await app.ban_chat_member(deal["group_id"], uid)
                            await app.unban_chat_member(deal["group_id"], uid)
                    except:
                        pass

        await asyncio.sleep(30)
