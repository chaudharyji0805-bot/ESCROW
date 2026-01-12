import asyncio
import logging
import os

from telethon import TelegramClient
from config import API_ID, API_HASH, BOT_TOKEN
from handlers import register_handlers

# 🔥 MongoDB init (IMPORTANT)
try:
    import database  # just importing = mongo connection + indexes
except Exception as e:
    print("❌ MongoDB connection failed:", e)
    raise SystemExit("Fix MONGO_URI and restart bot")

from auto_kick import auto_kick_worker
from admin_logs import send_log

# ================= LOGGING =================

logging.basicConfig(
    format='[%(levelname)s/%(asctime)s] %(name)s: %(message)s',
    level=logging.INFO
)
LOGGER = logging.getLogger(__name__)

# ================= MAIN =================

async def main():
    # Create Telegram client
    client = TelegramClient(
        "bot_session",
        API_ID,
        API_HASH
    )

    await client.start(bot_token=BOT_TOKEN)
    LOGGER.info("🤖 Escrow Bot Started")

    # Register handlers
    register_handlers(client)

    # Background tasks
    asyncio.create_task(auto_kick_worker(client))

    # Startup log
    try:
        await send_log(client, "✅ Escrow Bot started & MongoDB connected")
    except Exception as e:
        LOGGER.warning(f"Log channel error: {e}")

    # Run forever
    await client.run_until_disconnected()


# ================= ENTRY =================

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        LOGGER.info("Bot stopped manually")
