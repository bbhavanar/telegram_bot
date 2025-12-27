"""
MongoDB Database Module - COMPLETE FIXED VERSION
"""

from pymongo import MongoClient, ASCENDING, DESCENDING
from datetime import datetime
import logging
import config
from bson.objectid import ObjectId

logger = logging.getLogger(__name__)

# MongoDB Client
client = None
db = None

def init_db():
    """Initialize MongoDB connection"""
    global client, db
    
    try:
        mongodb_url = config.MONGODB_URL
        
        logger.info(f"🔗 Connecting to MongoDB...")
        
        client = MongoClient(
            mongodb_url,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=10000,
            socketTimeoutMS=10000
        )
        
        # Test connection
        client.admin.command('ping')
        
        # Get database
        db = client.telegram_bot
        
        logger.info("✅ MongoDB connected successfully")
        
        create_indexes()
        create_default_settings()
        
        return True
        
    except Exception as e:
        logger.error(f"❌ MongoDB connection failed: {e}")
        raise Exception(f"Cannot connect to MongoDB: {e}")

def create_indexes():
    """Create database indexes"""
    try:
        db.users.create_index([("telegram_id", ASCENDING)], unique=True)
        db.sessions.create_index([("is_sold", ASCENDING)])
        db.sessions.create_index([("country", ASCENDING)])
        db.sessions.create_index([("uploader_id", ASCENDING)])
        db.transactions.create_index([("user_id", ASCENDING)])
        db.transactions.create_index([("status", ASCENDING)])
        db.transactions.create_index([("payment_id", ASCENDING)])
        db.purchases.create_index([("user_id", ASCENDING)])
        db.seller_applications.create_index([("telegram_id", ASCENDING)])
        db.seller_applications.create_index([("status", ASCENDING)])
        db.withdrawals.create_index([("user_id", ASCENDING)])
        db.withdrawals.create_index([("status", ASCENDING)])
        db.pending_uploads.create_index([("uploader_id", ASCENDING)])
        db.pending_uploads.create_index([("status", ASCENDING)])
        
        logger.info("✅ Indexes created")
    except Exception as e:
        logger.warning(f"⚠️ Index creation: {e}")

def create_default_settings():
    """Create default settings"""
    try:
        settings = db.settings.find_one({"_id": "system"})
        if not settings:
            db.settings.insert_one({
                "_id": "system",
                "min_deposit": 1.0,
                "inr_to_usd_rate": 0.012,
                "ton_manual_price": None,
                "updated_at": datetime.utcnow()
            })
            logger.info("✅ Default settings created")
    except Exception as e:
        logger.warning(f"⚠️ Settings: {e}")

def get_db():
    """Get MongoDB database - RETURNS DATABASE OBJECT"""
    global db
    if db is None:
        init_db()
    return db

# ============================================
# USER CLASS - FIXED FOR MONGODB
# ============================================

class User:
    """User operations - MongoDB compatible"""
    
    @staticmethod
    def create(telegram_id, username=None):
        """Create new user"""
        database = get_db()
        try:
            user_data = {
                "telegram_id": telegram_id,
                "username": username,
                "balance": 0.0,
                "created_at": datetime.utcnow()
            }
            result = database.users.insert_one(user_data)
            logger.info(f"✅ User created: {telegram_id}")
            return result.inserted_id
        except Exception as e:
            logger.debug(f"User creation: {e}")
            return None
    
    @staticmethod
    def get_by_telegram_id(telegram_id):
        """Get user by telegram ID"""
        database = get_db()
        return database.users.find_one({"telegram_id": telegram_id})
    
    @staticmethod
    def update_balance(telegram_id, amount, operation='add'):
        """Update user balance"""
        database = get_db()
        
        if operation == 'add':
            result = database.users.update_one(
                {"telegram_id": telegram_id},
                {"$inc": {"balance": amount}}
            )
        elif operation == 'subtract':
            result = database.users.update_one(
                {"telegram_id": telegram_id},
                {"$inc": {"balance": -amount}}
            )
        elif operation == 'set':
            result = database.users.update_one(
                {"telegram_id": telegram_id},
                {"$set": {"balance": amount}}
            )
        
        return result.modified_count > 0
    
    @staticmethod
    def get_all(limit=20):
        """Get all users"""
        database = get_db()
        return list(database.users.find().sort("created_at", DESCENDING).limit(limit))
    
    @staticmethod
    def count():
        """Count users"""
        database = get_db()
        return database.users.count_documents({})

# ============================================
# SESSION CLASS - FIXED FOR MONGODB
# ============================================

class TelegramSession:
    """Session operations - MongoDB compatible"""
    
    @staticmethod
    def create(session_string, phone_number, country, has_2fa=False, 
               two_fa_password=None, price=1.0, uploader_id=None, info=None, spam_status='Unknown'):
        """Create session with optional info and spam status"""
        database = get_db()
        session_data = {
            "session_string": session_string,
            "phone_number": phone_number,
            "country": country,
            "has_2fa": has_2fa,
            "two_fa_password": two_fa_password,
            "is_sold": False,
            "buyer_id": None,
            "price": price,
            "uploader_id": uploader_id,
            "info": info,
            "spam_status": spam_status,
            "created_at": datetime.utcnow(),
            "sold_at": None
        }
        result = database.sessions.insert_one(session_data)
        logger.info(f"✅ Session created: {phone_number} (Status: {spam_status})")
        return result.inserted_id
    
    @staticmethod
    def get_by_id(session_id):
        """Get session by ID"""
        database = get_db()
        return database.sessions.find_one({"_id": ObjectId(session_id)})
    
    @staticmethod
    def get_available_by_country(country, limit=20):
        """Get available sessions"""
        database = get_db()
        return list(database.sessions.find({
            "country": country,
            "is_sold": False
        }).limit(limit))
    
    @staticmethod
    def get_available_countries():
        """Get countries"""
        database = get_db()
        pipeline = [
            {"$match": {"is_sold": False}},
            {"$group": {
                "_id": "$country",
                "count": {"$sum": 1},
                "min_price": {"$min": "$price"}
            }},
            {"$sort": {"count": -1}}
        ]
        return list(database.sessions.aggregate(pipeline))
    
    @staticmethod
    def mark_as_sold(session_id, buyer_id):
        """Mark as sold"""
        database = get_db()
        result = database.sessions.update_one(
            {"_id": ObjectId(session_id)},
            {"$set": {
                "is_sold": True,
                "buyer_id": buyer_id,
                "sold_at": datetime.utcnow()
            }}
        )
        return result.modified_count > 0
    
    @staticmethod
    def count_available():
        """Count available"""
        database = get_db()
        return database.sessions.count_documents({"is_sold": False})
    
    @staticmethod
    def count_sold():
        """Count sold"""
        database = get_db()
        return database.sessions.count_documents({"is_sold": True})
    
    @staticmethod
    def count_total():
        """Count total"""
        database = get_db()
        return database.sessions.count_documents({})
    
    @staticmethod
    def get_total_revenue():
        """Total revenue"""
        database = get_db()
        pipeline = [
            {"$match": {"is_sold": True}},
            {"$group": {"_id": None, "total": {"$sum": "$price"}}}
        ]
        result = list(database.sessions.aggregate(pipeline))
        return result[0]['total'] if result else 0.0
    
    @staticmethod
    def filter_by(**kwargs):
        """Generic filter - for compatibility"""
        database = get_db()
        return database.sessions.find(kwargs)
    
    @staticmethod
    def group_by_country():
        """Group by country with counts"""
        database = get_db()
        pipeline = [
            {"$match": {"is_sold": False}},
            {"$group": {
                "_id": "$country",
                "count": {"$sum": 1}
            }}
        ]
        results = list(database.sessions.aggregate(pipeline))
        # Return as list of tuples (country, count) for compatibility
        return [(r['_id'], r['count']) for r in results]
    
    @staticmethod
    def get_leader_stats(uploader_id):
        """Get statistics for a specific leader"""
        database = get_db()
        from datetime import datetime, timedelta
        
        # Total uploaded
        total_uploaded = database.sessions.count_documents({"uploader_id": uploader_id})
        
        # Total sold
        total_sold = database.sessions.count_documents({
            "uploader_id": uploader_id,
            "is_sold": True
        })
        
        # Total revenue
        revenue_pipeline = [
            {"$match": {"uploader_id": uploader_id, "is_sold": True}},
            {"$group": {"_id": None, "total": {"$sum": "$price"}}}
        ]
        revenue_result = list(database.sessions.aggregate(revenue_pipeline))
        total_revenue = revenue_result[0]['total'] if revenue_result else 0.0
        
        # Last 24 hours stats
        time_24h_ago = datetime.utcnow() - timedelta(hours=24)
        sold_24h = database.sessions.count_documents({
            "uploader_id": uploader_id,
            "is_sold": True,
            "sold_at": {"$gte": time_24h_ago}
        })
        
        revenue_24h_pipeline = [
            {
                "$match": {
                    "uploader_id": uploader_id,
                    "is_sold": True,
                    "sold_at": {"$gte": time_24h_ago}
                }
            },
            {"$group": {"_id": None, "total": {"$sum": "$price"}}}
        ]
        revenue_24h_result = list(database.sessions.aggregate(revenue_24h_pipeline))
        revenue_24h = revenue_24h_result[0]['total'] if revenue_24h_result else 0.0
        
        return {
            'total_uploaded': total_uploaded,
            'total_sold': total_sold,
            'total_revenue': total_revenue,
            'sold_24h': sold_24h,
            'revenue_24h': revenue_24h
        }

# ============================================
# TRANSACTION CLASS - FIXED FOR MONGODB
# ============================================

class Transaction:
    """Transaction operations - MongoDB compatible"""
    
    @staticmethod
    def create(user_id, amount, payment_method, transaction_type='deposit',
               amount_inr=None, payment_id=None, order_id=None):
        """Create transaction"""
        database = get_db()
        transaction_data = {
            "user_id": user_id,
            "amount": amount,
            "amount_inr": amount_inr,
            "type": transaction_type,
            "payment_method": payment_method,
            "status": "pending",
            "payment_id": payment_id,
            "order_id": order_id,
            "charge_id": None,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        result = database.transactions.insert_one(transaction_data)
        logger.info(f"✅ Transaction created: {result.inserted_id}")
        return result.inserted_id
    
    @staticmethod
    def get_by_id(transaction_id):
        """Get by ID"""
        database = get_db()
        return database.transactions.find_one({"_id": ObjectId(transaction_id)})
    
    @staticmethod
    def get_by_order_id(order_id):
        """Get by order ID"""
        database = get_db()
        return database.transactions.find_one({"order_id": order_id})
    
    @staticmethod
    def get_by_payment_id(payment_id):
        """Get by payment ID"""
        database = get_db()
        return database.transactions.find_one({"payment_id": payment_id})
    
    @staticmethod
    def update_status(transaction_id, status, charge_id=None):
        """Update status"""
        database = get_db()
        update_data = {
            "status": status,
            "updated_at": datetime.utcnow()
        }
        if charge_id:
            update_data["charge_id"] = charge_id
        
        result = database.transactions.update_one(
            {"_id": ObjectId(transaction_id)},
            {"$set": update_data}
        )
        return result.modified_count > 0
    
    @staticmethod
    def get_recent(limit=15):
        """Get recent"""
        database = get_db()
        return list(database.transactions.find().sort("created_at", DESCENDING).limit(limit))
    
    @staticmethod
    def filter_by(**kwargs):
        """Filter by - compatibility"""
        database = get_db()
        return database.transactions.find(kwargs)
    
    @staticmethod
    def count_by_method(payment_method, status=None):
        """Count by method"""
        database = get_db()
        query = {"payment_method": payment_method}
        if status:
            query["status"] = status
        return database.transactions.count_documents(query)
    
    @staticmethod
    def get_total_amount(payment_method=None, status='completed'):
        """Get total amount"""
        database = get_db()
        match_query = {"type": "deposit", "status": status}
        if payment_method:
            match_query["payment_method"] = payment_method
        
        pipeline = [
            {"$match": match_query},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
        ]
        result = list(database.transactions.aggregate(pipeline))
        return result[0]['total'] if result else 0.0

# ============================================
# PURCHASE CLASS - FIXED FOR MONGODB
# ============================================

class Purchase:
    """Purchase operations - MongoDB compatible"""
    
    @staticmethod
    def create(user_id, session_id, phone_number, country, has_2fa=False,
               two_fa_password=None, purchase_type='session'):
        """Create purchase"""
        database = get_db()
        purchase_data = {
            "user_id": user_id,
            "session_id": str(session_id),
            "phone_number": phone_number,
            "country": country,
            "has_2fa": has_2fa,
            "two_fa_password": two_fa_password,
            "purchase_type": purchase_type,
            "purchased_at": datetime.utcnow()
        }
        result = database.purchases.insert_one(purchase_data)
        logger.info(f"✅ Purchase created for user {user_id}")
        return result.inserted_id
    
    @staticmethod
    def get_by_user(user_id, limit=50):
        """Get by user"""
        database = get_db()
        return list(database.purchases.find({
            "user_id": user_id
        }).sort("purchased_at", DESCENDING).limit(limit))
    
    @staticmethod
    def count_by_user(user_id):
        """Count by user"""
        database = get_db()
        return database.purchases.count_documents({"user_id": user_id})
    
    @staticmethod
    def filter_by(**kwargs):
        """Filter - compatibility"""
        database = get_db()
        return database.purchases.find(kwargs)

# ============================================
# SYSTEM SETTINGS - FIXED FOR MONGODB
# ============================================

class SystemSettings:
    """System settings - MongoDB compatible"""
    
    @staticmethod
    def get():
        """Get settings"""
        database = get_db()
        settings = database.settings.find_one({"_id": "system"})
        if not settings:
            # Create default with $1 minimum
            settings = {
                "_id": "system",
                "min_deposit": 1.0,
                "inr_to_usd_rate": 0.012,
                "ton_manual_price": None,
                "updated_at": datetime.utcnow()
            }
            database.settings.insert_one(settings)
            logger.info("✅ Default settings created with $1 minimum")
        return settings
    
    @staticmethod
    def update(min_deposit=None, inr_to_usd_rate=None, ton_manual_price=None):
        """Update settings"""
        database = get_db()
        update_data = {"updated_at": datetime.utcnow()}
        
        if min_deposit is not None:
            update_data["min_deposit"] = float(min_deposit)
            logger.info(f"📝 Updating min_deposit to ${min_deposit}")
        
        if inr_to_usd_rate is not None:
            update_data["inr_to_usd_rate"] = inr_to_usd_rate
            logger.info(f"📝 Updating INR rate to {inr_to_usd_rate}")
        
        if ton_manual_price is not None:
            update_data["ton_manual_price"] = ton_manual_price
            logger.info(f"📝 Updating TON price to {ton_manual_price}")
        
        result = database.settings.update_one(
            {"_id": "system"},
            {"$set": update_data},
            upsert=True
        )
        
        logger.info(f"✅ Settings updated: {result.modified_count} modified, {result.upserted_id if result.upserted_id else 'existing'}")
        return result.modified_count > 0 or result.upserted_id is not None
    
    @staticmethod
    def first():
        """Get first - compatibility"""
        return SystemSettings.get()

# ============================================
# HELPER FUNCTIONS
# ============================================

def close_db():
    """Close connection"""
    global client
    if client:
        client.close()
        logger.info("🔒 MongoDB closed")

# Initialize
try:
    init_db()
except Exception as e:
    logger.error(f"Init failed: {e}")