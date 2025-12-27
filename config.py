import os
from dotenv import load_dotenv

load_dotenv()

# Bot Configuration
BOT_TOKEN = os.getenv('BOT_TOKEN')
TELEGRAM_API_ID = int(os.getenv('TELEGRAM_API_ID', 0))
TELEGRAM_API_HASH = os.getenv('TELEGRAM_API_HASH')
OWNER_ID = int(os.getenv('OWNER_ID', 'id here'))

# Admin IDs (for TON payment management)
ADMIN_IDS = [OWNER_ID]

# Coinbase (Crypto) Configuration
COINBASE_API_KEY = os.getenv('COINBASE_API_KEY')
COINBASE_WEBHOOK_SECRET = os.getenv('COINBASE_WEBHOOK_SECRET')

# Razorpay (INR) Configuration
RAZORPAY_KEY_ID = os.getenv('RAZORPAY_KEY_ID')
RAZORPAY_KEY_SECRET = os.getenv('RAZORPAY_KEY_SECRET')

# TON Payment Configuration
TON_MASTER_WALLET = os.getenv('TON_MASTER_WALLET')
TON_API_KEY = os.getenv('TON_API_KEY')

# MongoDB Configuration
MONGODB_URL = os.getenv('MONGODB_URL', 'url')

# If no MONGODB_URL is set, you can use MongoDB Atlas free tier
# Format: mongodb+srv://<username>:<password>@cluster.mongodb.net/telegram_bot

STORAGE_CHANNEL_ID = 'enter here'

# Session settings
SESSION_PRICE = 1.0

# Webhook settings
WEBHOOK_ENABLED = os.getenv('WEBHOOK_ENABLED', 'false').lower() == 'true'
WEBHOOK_PORT = int(os.getenv('PORT', 5000))
# Add these lines to your config.py file

# NOWPayments Configuration
NOWPAYMENTS_API_KEY = os.getenv('NOWPAYMENTS_API_KEY', 'apikey')
NOWPAYMENTS_IPN_SECRET = os.getenv('NOWPAYMENTS_IPN_SECRET', 'secret')