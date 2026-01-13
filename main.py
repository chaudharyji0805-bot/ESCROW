import asyncio
import logging
import sys

from telethon import TelegramClient
from config import API_ID, API_HASH, BOT_TOKEN
from handlers import register_handlers

# ================= LOGGING =================

logging.basicConfig(
    format='[%(levelname)s/%(asctime)s] %(name)s: %(message)s',
    level=logging.INFO
)
LOGGER = logging.getLogger(__name__)

# ================= MONGODB INIT =================

try:
    import database  # importing = mongo connect + indexes
    LOGGER.info("✅ MongoDB connected")
except Exception as e:
    LOGGER.error(f"❌ MongoDB connection failed: {e}")
    sys.exit("Fix MONGO_URI and restart bot")

# ================= OPTIONAL MODULES =================

# auto_kick (optional, safe)
try:
    from auto_kick import auto_kick_worker
except Exception:
    auto_kick_worker = None
    LOGGER.warning("⚠️ auto_kick.py not loaded (skipping)")

# ❌ admin_logs intentionally DISABLED
send_log = None

# ================= MAIN =================

async def main():
    client = TelegramClient(
        "bot_session",
        API_ID,
        API_HASH
    )

    await client.start(bot_token=BOT_TOKEN)
    LOGGER.info("🤖 Escrow Bot Started")

    # Register handlers
    register_handlers(client)

    # Background auto-kick task
    if auto_kick_worker:
        try:
            asyncio.create_task(auto_kick_worker(client))
            LOGGER.info("✅ Auto-kick worker started")
        except Exception as e:
            LOGGER.warning(f"Auto-kick start failed: {e}")

    # Run forever
    await client.run_until_disconnected()

# ================= ENTRY =================

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        LOGGER.info("🛑 Bot stopped")
