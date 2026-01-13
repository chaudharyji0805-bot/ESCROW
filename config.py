import os

# ================== TELEGRAM CONFIG ==================

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID"))

# ================== MONGODB CONFIG ==================

MONGO_URI = os.getenv("MONGO_URI")

# ================== VALIDATION ==================

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
    raise RuntimeError(f"❌ Missing required env vars: {', '.join(missing)}")
