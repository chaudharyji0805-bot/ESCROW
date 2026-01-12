import os
import time
from database import db

LOG_CHANNEL = os.getenv("LOG_CHANNEL")

logs_col = db.logs if "logs" in db.list_collection_names() else db.create_collection("logs")

async def send_log(client, text: str):
    # Save log in MongoDB
    logs_col.insert_one({
        "text": text,
        "time": time.time()
    })

    # Send to Telegram log channel (if set)
    if LOG_CHANNEL:
        try:
            await client.send_message(LOG_CHANNEL, text)
        except Exception:
            pass
