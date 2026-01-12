from database import settings

async def authorize_group(chat_id):
    settings.update_one(
        {"_id": "auth_group"},
        {"$set": {"chat_id": int(chat_id)}},
        upsert=True
    )

async def get_authorized_group():
    doc = settings.find_one({"_id": "auth_group"})
    return doc["chat_id"] if doc else None

async def is_authorized_group(chat_id):
    gid = await get_authorized_group()
    return gid == int(chat_id)
