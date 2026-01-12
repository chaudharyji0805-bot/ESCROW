import asyncio
import time
from telethon.tl.functions.channels import EditBannedRequest
from telethon.tl.types import ChatBannedRights

from database import deals_col

AUTO_KICK_TIME = 600  # 10 minutes

BANNED_RIGHTS = ChatBannedRights(
    until_date=None,
    view_messages=True
)

async def auto_kick_worker(client):
    while True:
        now = time.time()

        # completed deals fetch
        deals = deals_col.find({"status": "completed"})

        async for deal in _iterate(deals):
            if now - deal.get("completed_at", 0) >= AUTO_KICK_TIME:
                chat_id = deal.get("group_id")

                for user_id in (deal.get("buyer"), deal.get("seller")):
                    if not user_id or not chat_id:
                        continue

                    try:
                        await client(EditBannedRequest(
                            channel=chat_id,
                            participant=user_id,
                            banned_rights=BANNED_RIGHTS
                        ))
                    except Exception:
                        pass

                # mark as archived to avoid double kick
                deals_col.update_one(
                    {"_id": deal["_id"]},
                    {"$set": {"status": "archived"}}
                )

        await asyncio.sleep(30)


# 🔁 helper: async mongo cursor
async def _iterate(cursor):
    for doc in cursor:
        yield doc
