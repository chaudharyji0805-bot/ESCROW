from pymongo import MongoClient
import asyncio
import json
import os
import time
import os

mongo = MongoClient(os.getenv("MONGO_URI"))
db = mongo["escrow_bot"]

users = db.users
deals = db.deals
settings = db.settings

DB_FILE = "database.json"
_db_lock = asyncio.Lock()

DEFAULT_DB = {
    "deal_count_inr": 0,
    "deal_count_usdt": 0,
    "form_message": "Fill the form below to start a deal.",
    "form_entities": [], # Store serialized entities
    "chat_forms": {}, # chat_id -> {"message": text, "entities": entities}
    "deals": {}, 
    "limits": {}, # user_id -> {"inr": limit, "usdt": limit, "is_mod": bool, "is_mmod": bool}
    "tipped_messages": {}, # msg_id -> admin_id
    "admin_stats": {}, # user_id -> {"deals": 0, "amount_inr": 0, "amount_usdt": 0, "username": ""}
    "user_stats": {},   # user_id -> {"deals": 0, "amount_inr": 0, "amount_usdt": 0, "username": ""}
    "processed_messages": {}, # msg_id -> status (completed/cancelled)
    "active_forms": {}, # form_msg_id -> escrow_msg_id/status
    "report_history": []
}

async def load_db():
    async with _db_lock:
        if not os.path.exists(DB_FILE):
            with open(DB_FILE, "w") as f:
                json.dump(DEFAULT_DB, f, indent=4)
            return DEFAULT_DB
        
        with open(DB_FILE, "r") as f:
            try:
                data = json.load(f)
                # Merge missing keys from DEFAULT_DB
                updated = False
                for k, v in DEFAULT_DB.items():
                    if k not in data:
                        data[k] = v
                        updated = True
                if updated:
                    # Save merged version back
                    with open(DB_FILE, "w") as f2:
                        json.dump(data, f2, indent=4)
                return data
            except:
                return DEFAULT_DB

async def save_db(db):
    async with _db_lock:
        with open(DB_FILE, "w") as f:
            json.dump(db, f, indent=4)

async def mark_as_tipped(msg_id, admin_id):
    db = await load_db()
    db["tipped_messages"][str(msg_id)] = str(admin_id)
    await save_db(db)

async def get_tipped_admin(msg_id):
    db = await load_db()
    admin_id = db["tipped_messages"].get(str(msg_id))
    return int(admin_id) if admin_id else None

async def update_form_message(new_msg, entities=None, chat_id=None):
    db = await load_db()
    serialized_entities = []
    if entities:
        serialized_entities = [e.to_dict() for e in entities]

    if chat_id:
        db["chat_forms"][str(chat_id)] = {
            "message": new_msg,
            "entities": serialized_entities
        }
    else:
        db["form_message"] = new_msg
        db["form_entities"] = serialized_entities
    
    await save_db(db)

async def get_form_data(chat_id=None):
    db = await load_db()
    if chat_id and str(chat_id) in db.get("chat_forms", {}):
        data = db["chat_forms"][str(chat_id)]
        return data["message"], data.get("entities", [])
    return db["form_message"], db.get("form_entities", [])

async def set_admin_limit(user_id, amount=None, currency=None, is_mod=False, is_mmod=False):
    db = await load_db()
    user_id = str(user_id)
    if user_id not in db["limits"]:
        db["limits"][user_id] = {"inr": 0, "usdt": 0, "is_mod": False, "is_mmod": False}
    
    if is_mmod:
        db["limits"][user_id]["is_mmod"] = True
        db["limits"][user_id]["is_mod"] = True
    elif is_mod:
        db["limits"][user_id]["is_mod"] = True
        db["limits"][user_id]["is_mmod"] = False
    elif amount is not None and currency:
        db["limits"][user_id][currency.lower()] = int(amount)
        db["limits"][user_id]["is_mod"] = False
        db["limits"][user_id]["is_mmod"] = False
    else:
        # If no values provided, this is likely a reset (unadmin)
        db["limits"][user_id] = {"inr": 0, "usdt": 0, "is_mod": False, "is_mmod": False}
    
    await save_db(db)

async def get_admin_limit(user_id):
    db = await load_db()
    user_id = str(user_id)
    return db["limits"].get(user_id, {"inr": 0, "usdt": 0, "is_mod": False, "is_mmod": False})

async def increment_deal(currency="inr"):
    db = await load_db()
    key = f"deal_count_{currency.lower()}"
    db[key] += 1
    count = db[key]
    await save_db(db)
    return count

async def decrement_deal(currency="inr"):
    db = await load_db()
    key = f"deal_count_{currency.lower()}"
    if db[key] > 0:
        db[key] -= 1
    await save_db(db)

async def atomic_start_deal(form_msg_id):
    async with _db_lock:
        data = DEFAULT_DB
        if os.path.exists(DB_FILE):
            with open(DB_FILE, "r") as f:
                try:
                    data = json.load(f)
                except:
                    pass
        
        fid = str(form_msg_id)
        if fid in data.get("active_forms", {}):
            return False # Already active or processing
        
        if "active_forms" not in data:
            data["active_forms"] = {}
        
        data["active_forms"][fid] = "processing"
        with open(DB_FILE, "w") as f:
            json.dump(data, f, indent=4)
        return True

async def store_deal(escrow_msg_id, form_msg_id, deal_data):
    db = await load_db()
    deal_data["status"] = "active"
    deal_data["time"] = time.time()
    deal_data["form_id"] = str(form_msg_id)
    db["deals"][str(escrow_msg_id)] = deal_data
    db["active_forms"][str(form_msg_id)] = str(escrow_msg_id)
    await save_db(db)

async def is_form_active(form_msg_id):
    db = await load_db()
    return str(form_msg_id) in db.get("active_forms", {})

async def remove_deal(escrow_msg_id):
    db = await load_db()
    mid = str(escrow_msg_id)
    if mid in db["deals"]:
        form_id = db["deals"][mid].get("form_id")
        if form_id and str(form_id) in db["active_forms"]:
            del db["active_forms"][str(form_id)]
        del db["deals"][mid]
    await save_db(db)

async def get_deal(escrow_msg_id):
    db = await load_db()
    return db["deals"].get(str(escrow_msg_id))

async def get_running_deals():
    db = await load_db()
    return {k: v for k, v in db["deals"].items() if v.get("status") == "active"}

async def mark_processed(msg_id, status="completed"):
    db = await load_db()
    db["processed_messages"][str(msg_id)] = status
    await save_db(db)

async def get_processed_status(msg_id):
    db = await load_db()
    return db["processed_messages"].get(str(msg_id))

async def update_stats(user_id, amount, currency, is_admin=False, username=None):
    db = await load_db()
    stats_key = "admin_stats" if is_admin else "user_stats"
    uid = str(user_id)
    
    if uid not in db[stats_key]:
        db[stats_key][uid] = {"deals": 0, "amount_inr": 0, "amount_usdt": 0, "username": username or ""}
    
    if username:
        db[stats_key][uid]["username"] = username
        
    db[stats_key][uid]["deals"] += 1
    amt_key = f"amount_{currency.lower()}"
    db[stats_key][uid][amt_key] += float(amount)
    
    if "report_history" not in db: db["report_history"] = []
    db["report_history"].append({
        "time": time.time(),
        "amount": float(amount),
        "currency": currency.lower()
    })
    await save_db(db)

async def get_stats(user_id, is_admin=False):
    db = await load_db()
    stats_key = "admin_stats" if is_admin else "user_stats"
    return db[stats_key].get(str(user_id), {"deals": 0, "amount_inr": 0, "amount_usdt": 0})

async def get_leaderboard():
    db = await load_db()
    sorted_admins = sorted(db["admin_stats"].items(), key=lambda x: x[1]["deals"], reverse=True)
    total_deals = sum(a["deals"] for a in db["admin_stats"].values())
    total_amt_inr = sum(a["amount_inr"] for a in db["admin_stats"].values())
    total_amt_usdt = sum(a["amount_usdt"] for a in db["admin_stats"].values())
    return sorted_admins, total_deals, total_amt_inr, total_amt_usdt

async def get_report(seconds):
    db = await load_db()
    cutoff = time.time() - seconds
    history = db.get("report_history", [])
    deals_count = 0
    total_inr = 0
    total_usdt = 0
    for deal in history:
        if deal["time"] >= cutoff:
            deals_count += 1
            if deal["currency"] == "usdt":
                total_usdt += deal["amount"]
            else:
                total_inr += deal["amount"]
    return deals_count, total_inr, total_usdt

