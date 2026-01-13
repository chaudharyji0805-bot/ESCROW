import os
import time
from pymongo import MongoClient, ASCENDING

# ================== Mongo Setup ==================

MONGO_URI = os.getenv("MONGO_URI")
if not MONGO_URI:
    raise RuntimeError("❌ MONGO_URI not set")

mongo = MongoClient(MONGO_URI)
db = mongo["escrow_bot"]

# ================== Collections ==================

meta = db.meta
deals_col = db.deals
limits_col = db.limits
stats_col = db.stats
forms_col = db.forms
processed_col = db.processed
reports_col = db.reports
active_forms_col = db.active_forms
tipped_col = db.tipped

# ================== Indexes ==================

deals_col.create_index([("status", ASCENDING)])
deals_col.create_index([("buyer", ASCENDING)])
deals_col.create_index([("seller", ASCENDING)])
deals_col.create_index([("time", ASCENDING)])

limits_col.create_index([("user_id", ASCENDING)], unique=True)
stats_col.create_index([("user_id", ASCENDING), ("is_admin", ASCENDING)])
processed_col.create_index([("msg_id", ASCENDING)], unique=True)
active_forms_col.create_index([("form_id", ASCENDING)], unique=True)

# ================== Defaults ==================

def _get_meta():
    meta.update_one(
        {"_id": "global"},
        {"$setOnInsert": {
            "deal_count_inr": 0,
            "deal_count_usdt": 0,
            "form_message": "Fill the form below to start a deal.",
            "form_entities": []
        }},
        upsert=True
    )
    return meta.find_one({"_id": "global"})

# ================== AUTH GROUP ==================

async def set_auth_group(chat_id: int):
    meta.update_one(
        {"_id": "auth_group"},
        {"$set": {"chat_id": int(chat_id)}},
        upsert=True
    )

async def remove_auth_group():
    meta.delete_one({"_id": "auth_group"})

async def is_auth_group(chat_id: int) -> bool:
    doc = meta.find_one({"_id": "auth_group"})
    return bool(doc and int(doc.get("chat_id")) == int(chat_id))

# ================== PROOF CHANNEL ==================

async def set_proof_channel(group_id: int, channel_id: int):
    meta.update_one(
        {"_id": f"proof_{group_id}"},
        {"$set": {"channel_id": int(channel_id)}},
        upsert=True
    )

async def unset_proof_channel(group_id: int):
    meta.delete_one({"_id": f"proof_{group_id}"})

async def get_proof_channel(group_id: int):
    doc = meta.find_one({"_id": f"proof_{group_id}"})
    return int(doc["channel_id"]) if doc and "channel_id" in doc else None

# ================== Tipped ==================

async def mark_as_tipped(msg_id, admin_id):
    tipped_col.update_one(
        {"msg_id": str(msg_id)},
        {"$set": {"admin_id": str(admin_id)}},
        upsert=True
    )

async def get_tipped_admin(msg_id):
    doc = tipped_col.find_one({"msg_id": str(msg_id)})
    return int(doc["admin_id"]) if doc else None

# ================== Form System ==================

async def update_form_message(new_msg, entities=None, chat_id=None):
    entities = entities or []
    serialized = [e.to_dict() for e in entities]

    if chat_id:
        forms_col.update_one(
            {"chat_id": str(chat_id)},
            {"$set": {"message": new_msg, "entities": serialized}},
            upsert=True
        )
    else:
        meta.update_one(
            {"_id": "global"},
            {"$set": {"form_message": new_msg, "form_entities": serialized}},
            upsert=True
        )

async def get_form_data(chat_id=None):
    if chat_id:
        doc = forms_col.find_one({"chat_id": str(chat_id)})
        if doc:
            return doc["message"], doc.get("entities", [])
    m = _get_meta()
    return m["form_message"], m.get("form_entities", [])

# ================== Admin Limits ==================

async def set_admin_limit(user_id, amount=None, currency=None, is_mod=False, is_mmod=False):
    data = {"inr": 0, "usdt": 0, "is_mod": False, "is_mmod": False}

    if is_mmod:
        data["is_mod"] = True
        data["is_mmod"] = True
    elif is_mod:
        data["is_mod"] = True
    elif amount is not None and currency:
        data[currency.lower()] = int(amount)

    limits_col.update_one(
        {"user_id": str(user_id)},
        {"$set": data},
        upsert=True
    )

async def get_admin_limit(user_id):
    doc = limits_col.find_one({"user_id": str(user_id)})
    return doc or {"inr": 0, "usdt": 0, "is_mod": False, "is_mmod": False}

# ================== Deal Counters ==================

async def increment_deal(currency="inr"):
    res = meta.find_one_and_update(
        {"_id": "global"},
        {"$inc": {f"deal_count_{currency.lower()}": 1}},
        upsert=True,
        return_document=True
    )
    return res[f"deal_count_{currency.lower()}"]

async def decrement_deal(currency="inr"):
    meta.update_one(
        {"_id": "global"},
        {"$inc": {f"deal_count_{currency.lower()}": -1}}
    )

# ================== Deal Flow ==================

async def atomic_start_deal(form_msg_id):
    try:
        active_forms_col.insert_one({"form_id": str(form_msg_id), "status": "processing"})
        return True
    except:
        return False

async def store_deal(escrow_msg_id, form_msg_id, deal_data):
    deal_data.update({
        "_id": str(escrow_msg_id),
        "status": "active",
        "time": time.time(),
        "form_id": str(form_msg_id)
    })
    deals_col.insert_one(deal_data)
    active_forms_col.update_one(
        {"form_id": str(form_msg_id)},
        {"$set": {"escrow_id": str(escrow_msg_id)}}
    )

async def remove_deal(escrow_msg_id):
    deal = deals_col.find_one({"_id": str(escrow_msg_id)})
    if deal and deal.get("form_id"):
        active_forms_col.delete_one({"form_id": deal["form_id"]})
    deals_col.delete_one({"_id": str(escrow_msg_id)})

async def get_deal(escrow_msg_id):
    return deals_col.find_one({"_id": str(escrow_msg_id)})

async def get_running_deals():
    return {d["_id"]: d for d in deals_col.find({"status": "active"})}

# ================== Processed ==================

async def mark_processed(msg_id, status="completed"):
    processed_col.update_one(
        {"msg_id": str(msg_id)},
        {"$set": {"status": status}},
        upsert=True
    )

async def get_processed_status(msg_id):
    doc = processed_col.find_one({"msg_id": str(msg_id)})
    return doc["status"] if doc else None

# ================== Stats & Reports ==================

async def update_stats(user_id, amount, currency, is_admin=False, username=None):
    stats_col.update_one(
        {"user_id": str(user_id), "is_admin": is_admin},
        {"$inc": {"deals": 1, f"amount_{currency.lower()}": float(amount)},
         "$set": {"username": username or ""}},
        upsert=True
    )

    reports_col.insert_one({
        "time": time.time(),
        "amount": float(amount),
        "currency": currency.lower()
    })

async def get_stats(user_id, is_admin=False):
    doc = stats_col.find_one({"user_id": str(user_id), "is_admin": is_admin})
    return doc or {"deals": 0, "amount_inr": 0, "amount_usdt": 0}

async def get_leaderboard():
    admins = list(stats_col.find({"is_admin": True}).sort("deals", -1))
    total_deals = sum(a.get("deals", 0) for a in admins)
    total_inr = sum(a.get("amount_inr", 0) for a in admins)
    total_usdt = sum(a.get("amount_usdt", 0) for a in admins)
    return admins, total_deals, total_inr, total_usdt

async def get_report(seconds):
    cutoff = time.time() - seconds
    cursor = reports_col.find({"time": {"$gte": cutoff}})
    deals = total_inr = total_usdt = 0
    for d in cursor:
        deals += 1
        if d["currency"] == "usdt":
            total_usdt += d["amount"]
        else:
            total_inr += d["amount"]
    return deals, total_inr, total_usdt
