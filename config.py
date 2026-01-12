import os

# ================== TELEGRAM CONFIG ==================

API_ID = int(os.getenv("API_ID", "31573732"))
API_HASH = os.getenv("API_HASH", "ecebdea86eae3370dd8138bc0b9a385b")
BOT_TOKEN = os.getenv("BOT_TOKEN","5453914355:AAEv4RA7Y_BtXQO-re4xIu-JU0KN10qirKM")  # MUST be set in env

OWNER_ID = int(os.getenv("OWNER_ID", "7538572906"))

# ================== MONGODB CONFIG ==================

MONGO_URI = os.getenv("MONGO_URI")

# ================== LOG CHANNEL ==================
# Recommended: numeric channel ID (-100xxxx)
LOG_CHANNEL = os.getenv("LOG_CHANNEL", "@BotColony")

# ================== VALIDATION (VERY IMPORTANT) ==================

missing = []

if not API_ID:
    missing.append("API_ID")
if not API_HASH:
    missing.append("API_HASH")
if not BOT_TOKEN:
    missing.append("BOT_TOKEN")
if not OWNER_ID:
    missing.append("OWNER_ID")
if not MONGO_URI:
    missing.append("MONGO_URI")

if missing:
    raise RuntimeError(
        f"❌ Missing required config values: {', '.join(missing)}"
    )
