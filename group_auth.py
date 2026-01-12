from database import db

settings = db.settings

# Save authorized group
async def authorize_group(chat_id: int):
    settings.update_one(
        {"_id": "auth_group"},
        {"$set": {"chat_id": int(chat_id)}},
        upsert=True
    )

# Remove authorization
async def deauthorize_group():
    settings.delete_one({"_id": "auth_group"})

# Check authorization
async def is_authorized_group(chat_id: int) -> bool:
    doc = settings.find_one({"_id": "auth_group"})
    if not doc:
        return False
    return doc.get("chat_id") == int(chat_id)
