import os
import logging
import zipfile
import io
import asyncio
import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    CommandHandler, 
    MessageHandler, 
    filters, 
    ConversationHandler,
    ContextTypes,
    CallbackQueryHandler
)
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.users import GetFullUserRequest
import config
import time
from datetime import datetime
from database import get_db, TelegramSession
from bson.objectid import ObjectId

# OpenTele imports for manual upload
try:
    from opentele.api import UseCurrentSession, API
    from opentele.td import TDesktop
    OPENTELE_AVAILABLE = True
    AVAILABLE_APIS = [
        API.TelegramAndroid,
        API.TelegramIOS,
    ]
except ImportError:
    OPENTELE_AVAILABLE = False
    logger = logging.getLogger(__name__)
    logger.warning("⚠️ OpenTele not installed. Manual uploads will NOT work safely.")

logger = logging.getLogger(__name__)
TEMP_DIR = "/tmp"
os.makedirs(TEMP_DIR, exist_ok=True)

# ============================================
# MULTIPLE API CREDENTIALS FOR PARALLEL PROCESSING
# ============================================

# Load all available API credentials
API_CREDENTIALS = []

# Main API (from .env)
if config.TELEGRAM_API_ID and config.TELEGRAM_API_HASH:
    API_CREDENTIALS.append({
        'api_id': config.TELEGRAM_API_ID,
        'api_hash': config.TELEGRAM_API_HASH,
        'name': 'Main API'
    })

# Additional APIs (from environment variables)
for i in range(2, 5):  # API 2, 3, 4
    api_id = os.getenv(f'API_ID_{i}')
    api_hash = os.getenv(f'API_HASH_{i}')
    if api_id and api_hash:
        try:
            API_CREDENTIALS.append({
                'api_id': int(api_id),
                'api_hash': api_hash,
                'name': f'API {i}'
            })
        except:
            logger.error(f"Invalid API_ID_{i}")

logger.info(f"✅ Loaded {len(API_CREDENTIALS)} API credentials for parallel processing")

# ============================================
# LEADERS LIST - ADD TELEGRAM IDs HERE
# ============================================
LEADERS = [
    12345567,
    12345678,  # Example leader ID - REPLACE WITH ACTUAL IDs
    # Add more leader IDs here:
    # 123456789,
    # 987654321,
]

async def check_account_with_spambot(client, phone: str) -> dict:
    """Check account status using @SpamBot"""
    try:
        logger.info(f"🔍 Checking account status for {phone}...")
        
        # Start conversation with SpamBot
        spambot = await client.get_entity('SpamBot')
        
        # Send /start command
        await client.send_message(spambot, '/start')
        
        # Wait for response
        await asyncio.sleep(5)
        
        # Get messages from SpamBot
        messages = await client.get_messages(spambot, limit=5)
        
        # Check messages for status indicators
        for msg in messages:
            if not msg.text:
                continue
            
            text = msg.text.lower()
            
            # Check for frozen/blocked
            if "account was blocked for violations" in text or "your account has been blocked" in text:
                return {
                    'status': 'Frozen',
                    'message': '🔴 Account is BLOCKED',
                    'success': True
                }
            
            # Check for spam restrictions
            if ("while the account is limited" in text or 
                "some actions can trigger a harsh response" in text or 
                "unfortunately, some phone numbers may trigger a harsh response" in text):
                return {
                    'status': 'Spam',
                    'message': '🟡 Account has SPAM limitations',
                    'success': True
                }
            
            # Check for clean account
            if "no limits are currently applied" in text or "free as a bird" in text:
                return {
                    'status': 'Free',
                    'message': '🟢 Account is CLEAN',
                    'success': True
                }
        
        return {
            'status': 'Unknown',
            'message': '❓ Status unclear',
            'success': False
        }
        
    except Exception as e:
        logger.error(f"SpamBot check error: {e}")
        return {
            'status': 'Error',
            'message': f'❌ Error: {str(e)}',
            'success': False
        }

async def extract_session_info(client) -> str:
    """
    Extract detailed info from session
    Returns formatted string with account details
    """
    try:
        info_parts = []
        
        # Get user info
        me = await client.get_me()
        
        # 1. Premium status
        if hasattr(me, 'premium') and me.premium:
            info_parts.append("Premium")
        
        # 2. Verified status
        if hasattr(me, 'verified') and me.verified:
            info_parts.append("Verified")
        
        # 3. Username
        if me.username:
            info_parts.append(f"@{me.username}")
        
        # 4. Get account creation date (approximate via user ID)
        # Lower user ID = older account
        user_id = me.id
        if user_id < 1000000:
            info_parts.append("Very old account")
        elif user_id < 100000000:
            info_parts.append("Old account (5+ years)")
        elif user_id < 500000000:
            info_parts.append("3+ years old")
        elif user_id < 1000000000:
            info_parts.append("2+ years old")
        elif user_id < 2000000000:
            info_parts.append("1+ year old")
        else:
            info_parts.append("New account")
        
        # 5. Get datacenter info
        try:
            full_user = await client(GetFullUserRequest(me.id))
            if hasattr(full_user, 'profile_photo') and full_user.profile_photo:
                # DC from profile photo
                if hasattr(full_user.profile_photo, 'dc_id'):
                    dc_id = full_user.profile_photo.dc_id
                    info_parts.append(f"DC{dc_id}")
        except Exception as e:
            logger.debug(f"Could not get DC info: {e}")
        
        # 6. Profile photo status
        if me.photo:
            info_parts.append("Has photo")
        
        # 7. Bot status
        if me.bot:
            info_parts.append("Bot account")
        
        # Combine all info
        if info_parts:
            return " • ".join(info_parts[:4])  # Limit to 4 items max
        else:
            return None
            
    except Exception as e:
        logger.error(f"Error extracting session info: {e}")
        return None

async def leader_delete_session_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Leader starts session deletion - shows only THEIR AVAILABLE sessions with pagination"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    # Check if user is a leader
    if user_id not in LEADERS and user_id != config.OWNER_ID:
        await query.answer("❌ Leader access required", show_alert=True)
        return
    
    # Get page number from callback data or default to 1
    page = 1
    if query.data.startswith('leader_delete_page_'):
        page = int(query.data.replace('leader_delete_page_', ''))
    
    try:
        database = get_db()
        
        # ✅ FIXED: Get ONLY THIS LEADER'S AVAILABLE sessions
        total_sessions = database.sessions.count_documents({
            'uploader_id': user_id,
            'is_sold': False  # ✅ ONLY UNSOLD
        })
        
        if total_sessions == 0:
            await query.edit_message_text(
                "❌ You don't have any available sessions to delete.\n\n"
                "All your sessions are either sold or you haven't uploaded any yet."
            )
            return
        
        # Pagination settings
        per_page = 10
        total_pages = (total_sessions + per_page - 1) // per_page
        skip = (page - 1) * per_page
        
        # ✅ FIXED: Get ONLY this leader's unsold sessions, ALL COUNTRIES
        sessions = list(database.sessions.find({
            'uploader_id': user_id,
            'is_sold': False  # ✅ ONLY AVAILABLE
        }).sort('created_at', -1).skip(skip).limit(per_page))
        
        if not sessions:
            await query.edit_message_text("❌ No sessions found on this page")
            return
        
        keyboard = []
        
        # ✅ FIXED: Group by country
        sessions_by_country = {}
        for session in sessions:
            country = session.get('country', 'Unknown')
            if country not in sessions_by_country:
                sessions_by_country[country] = []
            sessions_by_country[country].append(session)
        
        # Add sessions grouped by country
        for country, country_sessions in sorted(sessions_by_country.items()):
            # Country header
            keyboard.append([InlineKeyboardButton(
                f"🌍 {country} ({len(country_sessions)} sessions)",
                callback_data='none'
            )])
            
            # Sessions in this country
            for session in country_sessions:
                phone = session.get('phone_number', 'Unknown')
                price = session.get('price', 0)
                
                keyboard.append([InlineKeyboardButton(
                    f"  🟢 {phone} - ${price:.2f}",
                    callback_data=f"leader_delete_{session['_id']}"
                )])
        
        # ✅ NEW: Pagination controls
        pagination_row = []
        if page > 1:
            pagination_row.append(InlineKeyboardButton(
                "⬅️ Previous",
                callback_data=f'leader_delete_page_{page-1}'
            ))
        
        pagination_row.append(InlineKeyboardButton(
            f"📄 {page}/{total_pages}",
            callback_data='none'
        ))
        
        if page < total_pages:
            pagination_row.append(InlineKeyboardButton(
                "Next ➡️",
                callback_data=f'leader_delete_page_{page+1}'
            ))
        
        keyboard.append(pagination_row)
        keyboard.append([InlineKeyboardButton("« Back to Leader Menu", callback_data='leader_back')])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"🗑️ **Delete Your Available Sessions**\n\n"
            f"Your Available Sessions: {total_sessions}\n"
            f"Page {page}/{total_pages}\n\n"
            f"⚠️ You can only delete unsold sessions\n"
            f"⚠️ Deletion cannot be undone!\n\n"
            f"Select a session to delete:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Leader delete session start error: {e}")
        import traceback
        traceback.print_exc()
        await query.edit_message_text(f"❌ Error: {str(e)}")


async def leader_confirm_delete_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Leader confirms session deletion"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    if user_id not in LEADERS and user_id != config.OWNER_ID:
        await query.answer("❌ Leader access required", show_alert=True)
        return
    
    try:
        from bson.objectid import ObjectId
        
        session_id = query.data.replace('leader_delete_', '')
        
        database = get_db()
        session = database.sessions.find_one({
            '_id': ObjectId(session_id),
            'uploader_id': user_id  # Ensure leader owns this session
        })
        
        if not session:
            await query.edit_message_text("❌ Session not found or you don't own it")
            return
        
        # Check if sold
        if session.get('is_sold'):
            await query.edit_message_text("❌ Cannot delete sold sessions")
            return
        
        phone = session.get('phone_number', 'Unknown')
        country = session.get('country', 'Unknown')
        price = session.get('price', 0)
        
        keyboard = [
            [
                InlineKeyboardButton("✅ Yes, Delete", callback_data=f"leader_confirm_del_{session_id}"),
                InlineKeyboardButton("❌ Cancel", callback_data='leader_delete_sessions')
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"🗑️ **Confirm Deletion**\n\n"
            f"📱 Phone: `{phone}`\n"
            f"🌍 Country: {country}\n"
            f"💰 Price: ${price:.2f}\n\n"
            f"⚠️ Are you sure you want to delete this session?\n"
            f"This action cannot be undone!",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Leader confirm delete error: {e}")
        import traceback
        traceback.print_exc()
        await query.edit_message_text(f"❌ Error: {str(e)}")


async def leader_execute_delete_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Leader executes session deletion"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    if user_id not in LEADERS and user_id != config.OWNER_ID:
        await query.answer("❌ Leader access required", show_alert=True)
        return
    
    try:
        from bson.objectid import ObjectId
        
        session_id = query.data.replace('leader_confirm_del_', '')
        
        database = get_db()
        session = database.sessions.find_one({
            '_id': ObjectId(session_id),
            'uploader_id': user_id  # Ensure leader owns this session
        })
        
        if not session:
            await query.edit_message_text("❌ Session not found or you don't own it")
            return
        
        # Double check not sold
        if session.get('is_sold'):
            await query.edit_message_text("❌ Cannot delete sold sessions")
            return
        
        phone = session.get('phone_number', 'Unknown')
        country = session.get('country', 'Unknown')
        
        # Delete the session
        result = database.sessions.delete_one({
            '_id': ObjectId(session_id),
            'uploader_id': user_id
        })
        
        if result.deleted_count > 0:
            # ✅ FIXED: Return to delete list instead of just showing success
            keyboard = [[InlineKeyboardButton("« Back to Delete List", callback_data='leader_delete_sessions')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"✅ **Session Deleted Successfully**\n\n"
                f"📱 Phone: `{phone}`\n"
                f"🌍 Country: {country}\n\n"
                f"The session has been permanently removed.",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            
            # Log the deletion
            logger.info(f"✅ Session deleted: {phone} ({country}) by leader {user_id}")
            
            # Notify admin
            try:
                await context.bot.send_message(
                    config.OWNER_ID,
                    f"🗑️ **Session Deleted by Leader**\n\n"
                    f"Leader: {query.from_user.first_name} (@{query.from_user.username or 'N/A'})\n"
                    f"Leader ID: `{user_id}`\n"
                    f"Phone: `{phone}`\n"
                    f"Country: {country}",
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.error(f"Failed to notify admin: {e}")
        else:
            await query.edit_message_text("❌ Failed to delete session")
            
    except Exception as e:
        logger.error(f"Leader execute delete error: {e}")
        import traceback
        traceback.print_exc()
        await query.edit_message_text(f"❌ Error: {str(e)}")

def get_temp_path(filename):
    """Get safe path in /tmp directory"""
    safe_filename = os.path.basename(filename)
    return os.path.join(TEMP_DIR, safe_filename)

def cleanup_temp_files(file_path, session_name):
    """Clean up ALL temporary session files"""
    cleanup_paths = [
        file_path,
        f"{session_name}.session",
        session_name,
        f"{session_name}.session-journal",
        f"{file_path}.session-journal"
    ]
    
    for path in cleanup_paths:
        try:
            if os.path.exists(path):
                os.remove(path)
                logger.debug(f"🧹 Cleaned: {path}")
        except Exception as e:
            logger.debug(f"Could not remove {path}: {e}")

# Conversation states
(LEADER_UPLOAD_SESSION, LEADER_UPLOAD_BULK, LEADER_UPLOAD_NUMBER_COUNTRY, 
 LEADER_UPLOAD_NUMBER_PHONE, LEADER_UPLOAD_NUMBER_OTP, LEADER_UPLOAD_NUMBER_2FA,
 LEADER_UPLOAD_NUMBER_PRICE, LEADER_UPLOAD_NUMBER_INFO, LEADER_UPLOAD_NUMBER_CONFIRM,
 LEADER_GET_COUNTRY, LEADER_GET_PRICE, LEADER_GET_INFO, LEADER_GET_2FA, LEADER_CONFIRM_DETAILS) = range(14)

# ============================================
# ADMIN NOTIFICATION HELPER
# ============================================
async def notify_admin_upload(bot, leader_id, leader_username, session_count, country, price):
    """Notify admin when leader uploads sessions"""
    try:
        message = (
            f"📤 **New Leader Upload**\n\n"
            f"👨‍💼 Leader: @{leader_username or 'Unknown'} (ID: {leader_id})\n"
            f"📦 Sessions: {session_count}\n"
            f"🌍 Country: {country}\n"
            f"💰 Price: ${price:.2f} each\n"
            f"⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
        
        await bot.send_message(
            chat_id=config.OWNER_ID,
            text=message,
            parse_mode='Markdown'
        )
        
        logger.info(f"✅ Admin notified of upload by leader {leader_id}")
        
    except Exception as e:
        logger.error(f"❌ Error notifying admin: {e}")

def leader_only(func):
    """Decorator to restrict commands to leaders only"""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in LEADERS and user_id != config.OWNER_ID:
            await update.message.reply_text("❌ You don't have leader permissions.")
            return
        return await func(update, context)
    return wrapper

@leader_only
async def leader_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Leader menu"""
    keyboard = [
        [InlineKeyboardButton("📤 Upload Session", callback_data='leader_upload')],
        [InlineKeyboardButton("📱 Upload Number", callback_data='leader_upload_number')],
        [InlineKeyboardButton("📦 Bulk Upload", callback_data='leader_bulk_upload')],
        [InlineKeyboardButton("🗑️ Delete Sessions", callback_data='leader_delete_sessions')],
        [InlineKeyboardButton("📊 My Stats", callback_data='leader_stats')],
        [InlineKeyboardButton("💸 Request Withdrawal", callback_data='leader_withdrawal')],
        [InlineKeyboardButton("📜 Withdrawal History", callback_data='leader_withdrawal_history')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "👨‍💼 Seller Panel\n\n"
        "You can upload sessions and manage earnings.\n"
        "Select an option below:",
        reply_markup=reply_markup
    )

async def leader_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show leader's upload statistics"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if user_id not in LEADERS and user_id != config.OWNER_ID:
        await query.answer("❌ Unauthorized", show_alert=True)
        return
    
    try:
        # Get THIS leader's stats using the database method
        stats = TelegramSession.get_leader_stats(user_id)
        
        # Calculate earnings after 15% commission
        total_earnings = stats['total_revenue'] * 0.85
        earnings_24h = stats['revenue_24h'] * 0.85
        commission_total = stats['total_revenue'] * 0.15
        commission_24h = stats['revenue_24h'] * 0.15
        
        keyboard = [[InlineKeyboardButton("« Back", callback_data='leader_back')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"📊 Your Statistics\n\n"
            f"📤 Total Uploaded: {stats['total_uploaded']}\n"
            f"✅ Sold: {stats['total_sold']}\n"
            f"💰 Total Sales: ${stats['total_revenue']:.2f}\n"
            f"💵 Your Earnings (85%): ${total_earnings:.2f}\n"
            f"🏢 Commission (15%): ${commission_total:.2f}\n\n"
            f"📈 Last 24 Hours:\n"
            f"   Sold: {stats['sold_24h']}\n"
            f"   Sales: ${stats['revenue_24h']:.2f}\n"
            f"   Your Earnings: ${earnings_24h:.2f}\n\n"
            f"Keep uploading quality sessions! 🚀",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        logger.error(f"Stats error: {e}")
        await query.edit_message_text("❌ Error loading stats")

async def leader_upload_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start single session upload"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if user_id not in LEADERS and user_id != config.OWNER_ID:
        await query.answer("❌ Unauthorized", show_alert=True)
        return
    
    await query.edit_message_text(
        "📤 Upload Single Session\n\n"
        "Please send me ONE .session file.\n\n"
        "Send /cancel to abort."
    )
    
    return LEADER_UPLOAD_SESSION

async def leader_receive_session_file(update, context):
    """Receive and process single session file - WITH SPAMBOT CHECK"""
    user_id = update.effective_user.id
    if user_id not in LEADERS and user_id != config.OWNER_ID:
        await update.message.reply_text("❌ Unauthorized")
        return ConversationHandler.END
    
    if not update.message.document:
        await update.message.reply_text("❌ Please send a valid .session file!")
        return LEADER_UPLOAD_SESSION
    
    file = update.message.document
    
    if not file.file_name.endswith('.session'):
        await update.message.reply_text("❌ File must be a .session file!")
        return LEADER_UPLOAD_SESSION
    
    await update.message.reply_text("⏳ Processing session file...")
    
    timestamp = int(time.time())
    unique_name = f"upload_{user_id}_{timestamp}"
    file_path = get_temp_path(f"{unique_name}.session")
    
    client = None
    try:
        # Download file
        new_file = await context.bot.get_file(file.file_id)
        await new_file.download_to_drive(file_path)
        logger.info(f"✅ Downloaded to {file_path}")
        
        # Verify session
        abs_file_path = os.path.abspath(file_path)
        abs_session_name = abs_file_path.replace('.session', '')
        
        client = TelegramClient(
            abs_session_name,
            config.TELEGRAM_API_ID,
            config.TELEGRAM_API_HASH,
            system_version="4.16.30-vxCUSTOM",
            connection_retries=3,
            retry_delay=2,
            timeout=10
        )
        
        await asyncio.wait_for(client.connect(), timeout=15.0)
        logger.info("✅ Client connected")
        
        is_auth = await asyncio.wait_for(client.is_user_authorized(), timeout=10.0)
        
        if not is_auth:
            await update.message.reply_text(
                "❌ Session not authorized!\n\n"
                "Please ensure you're uploading a valid, active session file."
            )
            if client.is_connected():
                await client.disconnect()
            cleanup_temp_files(abs_file_path, abs_session_name)
            return ConversationHandler.END
        
        # Get user info
        me = await asyncio.wait_for(client.get_me(), timeout=10.0)
        phone = me.phone or "Unknown"
        
        # ✅ NEW: CHECK WITH SPAMBOT
        logger.info(f"🔍 Checking {phone} with SpamBot...")
        spam_check = await check_account_with_spambot(client, phone)
        
        logger.info(f"📊 SpamBot result: {spam_check['status']} - {spam_check['message']}")
        
        # Disconnect
        if client.is_connected():
            await client.disconnect()
        
        # Upload to storage channel
        try:
            # Status emoji
            status_emoji = {
                'Free': '🟢',
                'Frozen': '🔴',
                'Spam': '🟡',
                'Unknown': '❓',
                'Error': '❌'
            }
            
            emoji = status_emoji.get(spam_check['status'], '❓')
            
            with open(abs_file_path, 'rb') as f:
                channel_message = await context.bot.send_document(
                    chat_id=config.STORAGE_CHANNEL_ID,
                    document=f,
                    filename=file.file_name,
                    caption=(
                        f"📱 Phone: {phone}\n"
                        f"{emoji} Status: {spam_check['status']}\n"
                        f"👨‍💼 Uploaded by: {update.effective_user.username or user_id}\n"
                        f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                    )
                )
            
            message_id = channel_message.message_id
            
            logger.info(f"✅ Session uploaded to channel, message_id: {message_id}")
            
            context.user_data['session_data'] = {
                'message_id': message_id,
                'phone': phone,
                'uploader_id': user_id,
                'spam_status': spam_check['status']  # ✅ ADD THIS
            }
            
            cleanup_temp_files(abs_file_path, abs_session_name)
            
            # ✅ SHOW SPAM STATUS TO LEADER
            warning_text = ""
            if spam_check['status'] in ['Frozen', 'Spam']:
                warning_text = "\n⚠️ **WARNING: This account has restrictions!**\n"
            elif spam_check['status'] == 'Free':
                warning_text = "\n✅ **Account looks clean and healthy!**\n"
            
            await update.message.reply_text(
                f"✅ **Session Validated**\n\n"
                f"📱 Phone: `{phone}`\n"
                f"{emoji} **Status:** {spam_check['message']}\n"
                f"{warning_text}\n"
                f"Enter country code (e.g., Bangladesh, USA, India):",
                parse_mode='Markdown'
            )
            
            return LEADER_GET_COUNTRY
        
        except Exception as e:
            logger.error(f"❌ Error uploading to channel: {e}")
            await update.message.reply_text(
                f"❌ Error uploading to storage channel\n\n"
                f"Contact admin for help."
            )
            cleanup_temp_files(abs_file_path, abs_session_name)
            return ConversationHandler.END
            
    except asyncio.TimeoutError:
        logger.error("Connection timeout")
        await update.message.reply_text(
            "❌ Connection timeout\n\n"
            "The session took too long to connect.\n"
            "Try again in a few minutes."
        )
        if client and client.is_connected():
            await client.disconnect()
        cleanup_temp_files(abs_file_path if 'abs_file_path' in locals() else file_path, 
                         abs_session_name if 'abs_session_name' in locals() else file_path)
        return ConversationHandler.END
        
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        
        await update.message.reply_text(f"❌ Error processing session: {str(e)}")
        
        if client and client.is_connected():
            await client.disconnect()
        
        cleanup_temp_files(abs_file_path if 'abs_file_path' in locals() else file_path,
                         abs_session_name if 'abs_session_name' in locals() else file_path)
        return ConversationHandler.END

async def leader_bulk_upload_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start bulk session upload"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if user_id not in LEADERS and user_id != config.OWNER_ID:
        await query.answer("❌ Unauthorized", show_alert=True)
        return
    
    await query.edit_message_text(
        "📦 Bulk Upload Sessions\n\n"
        "Send me:\n"
        "• Multiple .session files (as separate documents), OR\n"
        "• A .zip file containing .session files\n\n"
        "All sessions will have the SAME country, price, and 2FA.\n\n"
        "When done, send /done\n"
        "Send /cancel to abort."
    )
    
    context.user_data['bulk_sessions'] = []
    return LEADER_UPLOAD_BULK

async def leader_receive_bulk_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive multiple session files or ZIP - WITH REAL-TIME SPAM STATUS"""
    user_id = update.effective_user.id
    if user_id not in LEADERS and user_id != config.OWNER_ID:
        await update.message.reply_text("❌ Unauthorized")
        return ConversationHandler.END
    
    if 'bulk_sessions' not in context.user_data:
        context.user_data['bulk_sessions'] = []
    
    # Handle /done command
    if update.message.text:
        text = update.message.text.strip().lower()
        
        if text == '/done' or text == 'done':
            if not context.user_data.get('bulk_sessions'):
                await update.message.reply_text(
                    "❌ No sessions uploaded yet!\n\n"
                    "Send .session files first, then type /done"
                )
                return LEADER_UPLOAD_BULK
            
            session_count = len(context.user_data['bulk_sessions'])
            await update.message.reply_text(
                f"✅ Loaded {session_count} sessions!\n\n"
                f"Now, enter the country name for ALL sessions:\n"
                f"Examples: Indo, USA, UK, Russia, Other"
            )
            return LEADER_GET_COUNTRY
        
        else:
            await update.message.reply_text(
                "📦 Bulk Upload Mode\n\n"
                "📤 Send .session files or .zip file\n"
                "✅ When finished, type: /done\n\n"
                f"Current: {len(context.user_data.get('bulk_sessions', []))} sessions loaded"
            )
            return LEADER_UPLOAD_BULK
    
    # Handle file uploads
    if not update.message.document:
        await update.message.reply_text("❌ Please send a .session file or .zip file!")
        return LEADER_UPLOAD_BULK
    
    file = update.message.document
    file_name = file.file_name
    
    status_msg = await update.message.reply_text(f"⏳ Processing {file_name}...")
    
    try:
        new_file = await context.bot.get_file(file.file_id)
        
        if file_name.endswith('.zip'):
            # Handle ZIP file
            zip_bytes = await new_file.download_as_bytearray()
            zip_file = zipfile.ZipFile(io.BytesIO(zip_bytes))
            
            processed = 0
            failed = 0
            failed_files = []
            
            # ✅ Track spam status
            spam_stats = {
                'Free': 0,
                'Spam': 0,
                'Frozen': 0,
                'Unknown': 0,
                'Error': 0
            }
            
            session_files = [f for f in zip_file.namelist() if f.endswith('.session')]
            total_files = len(session_files)
            
            if total_files == 0:
                await status_msg.edit_text("❌ No .session files found in ZIP!")
                return LEADER_UPLOAD_BULK
            
            await status_msg.edit_text(
                f"📦 Processing ZIP: {total_files} session files found...\n"
                f"⏳ This may take a while (~5 sec per session)\n\n"
                f"Progress will be shown below..."
            )
            
            for idx, zip_info in enumerate(session_files, 1):
                try:
                    session_bytes = zip_file.read(zip_info)
                    temp_path = get_temp_path(f"bulk_{idx}_{os.path.basename(zip_info)}")
                    
                    with open(temp_path, 'wb') as f:
                        f.write(session_bytes)
                    
                    # ✅ Send individual status message
                    processing_msg = await context.bot.send_message(
                        user_id,
                        f"⏳ Processing {idx}/{total_files}: Checking session..."
                    )
                    
                    result = await process_single_session_bulk(temp_path, user_id, context.bot)
                    
                    if result:
                        context.user_data['bulk_sessions'].append(result)
                        processed += 1
                        
                        # Get spam status
                        spam_status = result.get('spam_status', 'Unknown')
                        phone = result.get('phone', 'Unknown')
                        
                        # Count spam status
                        if spam_status in spam_stats:
                            spam_stats[spam_status] += 1
                        
                        # ✅ REAL-TIME STATUS MESSAGE
                        status_emoji_map = {
                            'Free': '🟢 CLEAN',
                            'Frozen': '🔴 FROZEN',
                            'Spam': '🟡 SPAM LIMITED',
                            'Unknown': '❓ UNKNOWN',
                            'Error': '❌ CHECK ERROR'
                        }
                        
                        status_text = status_emoji_map.get(spam_status, f'❓ {spam_status}')
                        
                        await processing_msg.edit_text(
                            f"✅ {idx}/{total_files} Processed\n\n"
                            f"📱 Phone: {phone}\n"
                            f"🔍 Status: {status_text}\n\n"
                            f"Progress: {processed} success, {failed} failed"
                        )
                        
                        logger.info(f"✅ {idx}/{total_files}: {phone} ({spam_status})")
                    else:
                        failed += 1
                        failed_files.append(os.path.basename(zip_info))
                        
                        await processing_msg.edit_text(
                            f"❌ {idx}/{total_files} Failed\n\n"
                            f"File: {os.path.basename(zip_info)}\n"
                            f"Reason: Invalid or expired session\n\n"
                            f"Progress: {processed} success, {failed} failed"
                        )
                        
                        logger.warning(f"❌ Failed {idx}/{total_files}: {os.path.basename(zip_info)}")
                    
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                    
                    await asyncio.sleep(0.3)
                    
                except Exception as e:
                    logger.error(f"Error processing {zip_info}: {e}")
                    failed += 1
                    failed_files.append(os.path.basename(zip_info))
                    continue
            
            # ✅ Final summary with spam stats
            summary = (
                f"✅ **ZIP Processing Complete!**\n\n"
                f"📊 **Results:**\n"
                f"✅ Successful: {processed}/{total_files}\n"
                f"❌ Failed: {failed}/{total_files}\n\n"
            )
            
            # Add spam statistics
            if processed > 0:
                summary += "🔍 **Account Status:**\n"
                if spam_stats['Free'] > 0:
                    summary += f"  🟢 Clean: {spam_stats['Free']}\n"
                if spam_stats['Spam'] > 0:
                    summary += f"  🟡 Spam Limited: {spam_stats['Spam']}\n"
                if spam_stats['Frozen'] > 0:
                    summary += f"  🔴 Frozen: {spam_stats['Frozen']}\n"
                if spam_stats['Unknown'] > 0:
                    summary += f"  ❓ Unknown: {spam_stats['Unknown']}\n"
                if spam_stats['Error'] > 0:
                    summary += f"  ❌ Check Error: {spam_stats['Error']}\n"
                summary += "\n"
            
            if failed > 0 and len(failed_files) <= 10:
                summary += "Failed files:\n"
                for f in failed_files[:10]:
                    summary += f"• {f}\n"
                if len(failed_files) > 10:
                    summary += f"... and {len(failed_files) - 10} more\n"
                summary += "\n"
            
            summary += (
                f"Total loaded: {len(context.user_data['bulk_sessions'])} sessions\n\n"
                f"✅ Type /done when finished uploading."
            )
            
            await status_msg.edit_text(summary, parse_mode='Markdown')
        
        elif file_name.endswith('.session'):
            # Handle single .session file
            temp_path = get_temp_path(f"single_{int(time.time())}_{file_name}")
            await new_file.download_to_drive(temp_path)
            
            await status_msg.edit_text(f"⏳ Checking {file_name}...")
            
            result = await process_single_session_bulk(temp_path, user_id, context.bot)
            
            if result:
                context.user_data['bulk_sessions'].append(result)
                
                spam_status = result.get('spam_status', 'Unknown')
                phone = result.get('phone', 'Unknown')
                
                status_emoji_map = {
                    'Free': '🟢 CLEAN',
                    'Frozen': '🔴 FROZEN',
                    'Spam': '🟡 SPAM LIMITED',
                    'Unknown': '❓ UNKNOWN',
                    'Error': '❌ CHECK ERROR'
                }
                
                status_text = status_emoji_map.get(spam_status, f'❓ {spam_status}')
                
                await status_msg.edit_text(
                    f"✅ **Session Added**\n\n"
                    f"📱 Phone: {phone}\n"
                    f"🔍 Status: {status_text}\n\n"
                    f"Total: {len(context.user_data['bulk_sessions'])} sessions\n\n"
                    f"📤 Send more files or type /done to continue.",
                    parse_mode='Markdown'
                )
            else:
                await status_msg.edit_text(
                    f"❌ **Failed to process** {file_name}\n\n"
                    f"Total loaded: {len(context.user_data.get('bulk_sessions', []))} sessions\n\n"
                    f"📤 Send more files or type /done to continue.",
                    parse_mode='Markdown'
                )
            
            if os.path.exists(temp_path):
                os.remove(temp_path)
        
        else:
            await status_msg.edit_text("❌ File must be .session or .zip!")
        
        return LEADER_UPLOAD_BULK
        
    except Exception as e:
        logger.error(f"Error in bulk upload: {e}")
        import traceback
        traceback.print_exc()
        await status_msg.edit_text(f"❌ Error processing file: {str(e)}")
        return LEADER_UPLOAD_BULK

async def process_single_session_bulk(file_path, user_id, bot):
    """Process session for bulk upload - WITH AUTO INFO"""
    session_name = file_path.replace('.session', '')
    client = None
    
    try:
        from telethon.tl.functions.users import GetFullUserRequest
        
        logger.info(f"📦 Processing: {file_path}")
        
        if not os.path.exists(file_path):
            logger.error(f"❌ File not found: {file_path}")
            return None
        
        abs_file_path = os.path.abspath(file_path)
        abs_session_name = abs_file_path.replace('.session', '')
        
        client = TelegramClient(
            abs_session_name,
            config.TELEGRAM_API_ID,
            config.TELEGRAM_API_HASH,
            system_version="4.16.30-vxCUSTOM",
            connection_retries=3,
            retry_delay=2,
            timeout=10
        )
        
        await asyncio.wait_for(client.connect(), timeout=15.0)
        
        is_authorized = await asyncio.wait_for(
            client.is_user_authorized(), 
            timeout=10.0
        )
        
        if not is_authorized:
            logger.warning(f"❌ Not authorized: {file_path}")
            if client.is_connected():
                await client.disconnect()
            cleanup_temp_files(abs_file_path, abs_session_name)
            return None
        
        me = await asyncio.wait_for(client.get_me(), timeout=10.0)
        phone = me.phone or "Unknown"
        
        # ✅ AUTO-EXTRACT INFO
        auto_info = await extract_session_info(client)
        
        # ✅ CHECK SPAMBOT
        spam_check = await check_account_with_spambot(client, phone)
        
        if client.is_connected():
            await client.disconnect()
        
        # Upload to channel
        status_emoji = {
            'Free': '🟢',
            'Frozen': '🔴',
            'Spam': '🟡',
            'Unknown': '❓',
            'Error': '❌'
        }
        
        emoji = status_emoji.get(spam_check['status'], '❓')
        
        caption_parts = [
            f"📱 Phone: {phone}",
            f"{emoji} Status: {spam_check['status']}"
        ]
        
        if auto_info:
            caption_parts.append(f"ℹ️ {auto_info}")
        
        caption_parts.append(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        
        with open(abs_file_path, 'rb') as f:
            channel_message = await bot.send_document(
                chat_id=config.STORAGE_CHANNEL_ID,
                document=f,
                filename=os.path.basename(abs_file_path),
                caption="\n".join(caption_parts)
            )
        
        message_id = channel_message.message_id
        
        cleanup_temp_files(abs_file_path, abs_session_name)
        
        logger.info(f"✅ Processed: {phone} ({spam_check['status']})")
        
        return {
            'message_id': message_id,
            'phone': phone,
            'spam_status': spam_check['status'],
            'auto_info': auto_info  # ✅ RETURN AUTO INFO
        }
        
    except Exception as e:
        logger.error(f"❌ Error: {file_path} - {e}")
        if client and client.is_connected():
            try:
                await asyncio.wait_for(client.disconnect(), timeout=5.0)
            except:
                pass
        cleanup_temp_files(abs_file_path if 'abs_file_path' in locals() else file_path,
                         abs_session_name if 'abs_session_name' in locals() else session_name)
        return None

async def leader_receive_country(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive country name"""
    user_id = update.effective_user.id
    if user_id not in LEADERS and user_id != config.OWNER_ID:
        await update.message.reply_text("❌ Unauthorized")
        return ConversationHandler.END
    
    country = update.message.text.strip()
    
    if not country or len(country) < 2:
        await update.message.reply_text("❌ Please enter a valid country name.")
        return LEADER_GET_COUNTRY
    
    if 'bulk_sessions' in context.user_data:
        for session in context.user_data['bulk_sessions']:
            session['country'] = country
    else:
        context.user_data['session_data']['country'] = country
    
    await update.message.reply_text(
        f"✅ Country set to: {country}\n\n"
        f"💰 Now, enter the price (in USD):\n\n"
        f"⚠️ Remember: 15% commission to owner\n"
        f"You earn 85% of the price\n\n"
        f"Examples:\n"
        f"• Set $2.00 → You earn $1.70\n"
        f"• Set $1.50 → You earn $1.28"
    )
    
    return LEADER_GET_PRICE

async def leader_receive_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive price"""
    user_id = update.effective_user.id
    if user_id not in LEADERS and user_id != config.OWNER_ID:
        await update.message.reply_text("❌ Unauthorized")
        return ConversationHandler.END
    
    try:
        price = float(update.message.text.strip())
        
        if price <= 0:
            await update.message.reply_text("❌ Price must be greater than 0.")
            return LEADER_GET_PRICE
        
        if 'bulk_sessions' in context.user_data:
            for session in context.user_data['bulk_sessions']:
                session['price'] = price
        else:
            context.user_data['session_data']['price'] = price
        
        await update.message.reply_text(
            f"✅ Price set to: ${price:.2f}\n\n"
            f"📝 Enter additional info about these accounts:\n\n"
            f"Examples:\n"
            f"• Premium\n"
            f"• Verified\n"
            f"• Premium + Verified\n"
            f"• Old Account\n\n"
            f"Or type 'none' to skip"
        )
        
        return LEADER_GET_INFO
        
    except ValueError:
        await update.message.reply_text("❌ Invalid price. Please enter a number (e.g., 1.0)")
        return LEADER_GET_PRICE

async def leader_receive_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive account info"""
    user_id = update.effective_user.id
    if user_id not in LEADERS and user_id != config.OWNER_ID:
        await update.message.reply_text("❌ Unauthorized")
        return ConversationHandler.END
    
    info_text = update.message.text.strip()
    
    # Set info or None if skipped
    info = None if info_text.lower() in ['none', 'no', 'skip'] else info_text
    
    if 'bulk_sessions' in context.user_data:
        for session in context.user_data['bulk_sessions']:
            session['info'] = info
    else:
        context.user_data['session_data']['info'] = info
    
    info_display = info if info else "None"
    
    await update.message.reply_text(
        f"✅ Info set to: {info_display}\n\n"
        f"Do these accounts have 2FA (cloud password)?\n\n"
        f"Send the 2FA password, or type 'no' if they don't have 2FA."
    )
    
    return LEADER_GET_2FA

async def leader_receive_2fa_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive 2FA password or 'no'"""
    user_id = update.effective_user.id
    if user_id not in LEADERS and user_id != config.OWNER_ID:
        await update.message.reply_text("❌ Unauthorized")
        return ConversationHandler.END
    
    password_input = update.message.text.strip()
    
    if password_input.lower() in ['no', 'nil', 'none', 'n']:
        has_2fa = False
        two_fa_password = None
    else:
        has_2fa = True
        two_fa_password = password_input
    
    if 'bulk_sessions' in context.user_data:
        for session in context.user_data['bulk_sessions']:
            session['has_2fa'] = has_2fa
            session['two_fa_password'] = two_fa_password
        
        keyboard = [
            [InlineKeyboardButton("✅ Confirm", callback_data='leader_upload_confirm_yes')],
            [InlineKeyboardButton("❌ Cancel", callback_data='leader_upload_confirm_no')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        first_session = context.user_data['bulk_sessions'][0]
        
        await update.message.reply_text(
            f"📋 Bulk Upload Summary:\n\n"
            f"📦 Total Sessions: {len(context.user_data['bulk_sessions'])}\n"
            f"🌍 Country: {first_session['country']}\n"
            f"💰 Price (each): ${first_session['price']:.2f}\n"
            f"🔐 2FA: {'Yes' if has_2fa else 'No'}\n"
            + (f"🔑 Password: {two_fa_password}\n\n" if has_2fa else "\n")
            + f"Confirm to add all {len(context.user_data['bulk_sessions'])} sessions?",
            reply_markup=reply_markup
        )
    else:
        context.user_data['session_data']['has_2fa'] = has_2fa
        context.user_data['session_data']['two_fa_password'] = two_fa_password
        
        keyboard = [
            [InlineKeyboardButton("✅ Confirm", callback_data='leader_upload_confirm_yes')],
            [InlineKeyboardButton("❌ Cancel", callback_data='leader_upload_confirm_no')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        session_data = context.user_data['session_data']
        
        await update.message.reply_text(
            "📋 Final Session Details:\n\n"
            f"📱 Phone: {session_data['phone']}\n"
            f"🌍 Country: {session_data['country']}\n"
            f"💰 Price: ${session_data['price']:.2f}\n"
            f"🔐 2FA: {'Yes' if has_2fa else 'No'}\n"
            + (f"🔑 Password: {two_fa_password}\n\n" if has_2fa else "\n")
            + "Confirm to add this session to database?",
            reply_markup=reply_markup
        )
    
    return LEADER_CONFIRM_DETAILS

async def confirm_session_upload(update, context):
    """Admin: Save sessions to MongoDB - WITH UPLOADER ID"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != config.OWNER_ID:
        await query.answer("❌ Unauthorized", show_alert=True)
        return ConversationHandler.END
    
    if query.data == 'admin_confirm_no':
        context.user_data.clear()
        await query.edit_message_text("❌ Cancelled")
        return ConversationHandler.END
    
    try:
        # Bulk upload
        if 'bulk_sessions' in context.user_data:
            sessions = context.user_data['bulk_sessions']
            added_count = 0
            failed_count = 0
            
            await query.edit_message_text(f"⏳ Adding {len(sessions)} sessions...")
            
            for session_data in sessions:
                try:
                    TelegramSession.create(
                        session_string=str(session_data['message_id']),
                        phone_number=session_data['phone'],
                        country=session_data['country'],
                        has_2fa=session_data['has_2fa'],
                        two_fa_password=session_data.get('two_fa_password'),
                        price=session_data['price'],
                        info=session_data.get('info'),
                        spam_status=session_data.get('spam_status', 'Unknown'),
                        uploader_id=query.from_user.id  # ✅ CRITICAL FIX
                    )
                    
                    added_count += 1
                    
                except Exception as e:
                    logger.error(f"❌ Error adding session: {e}")
                    failed_count += 1
                    continue
            
            result_msg = f"✅ Bulk Upload Complete!\n\n"
            result_msg += f"📦 Added: {added_count}/{len(sessions)} sessions\n"
            
            if failed_count > 0:
                result_msg += f"❌ Failed: {failed_count} sessions\n"
            
            await query.edit_message_text(result_msg)
        
        # Single upload
        else:
            session_data = context.user_data.get('session_data')
            
            if not session_data:
                await query.edit_message_text("❌ Session data not found")
                return ConversationHandler.END
            
            try:
                TelegramSession.create(
                    session_string=str(session_data['message_id']),
                    phone_number=session_data['phone'],
                    country=session_data['country'],
                    has_2fa=session_data['has_2fa'],
                    two_fa_password=session_data.get('two_fa_password'),
                    price=session_data['price'],
                    info=session_data.get('info'),
                    spam_status=session_data.get('spam_status', 'Unknown'),
                    uploader_id=query.from_user.id  # ✅ CRITICAL FIX
                )
                
                await query.edit_message_text(
                    f"✅ Session added!\n\n"
                    f"📱 Phone: {session_data['phone']}"
                )
                
            except Exception as e:
                logger.error(f"❌ Error: {e}")
                await query.edit_message_text(f"❌ Error: {str(e)}")
        
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        await query.edit_message_text(f"❌ Error: {str(e)}")
    
    finally:
        context.user_data.clear()
    
    return ConversationHandler.END

async def leader_cancel_operation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel any operation"""
    context.user_data.clear()
    await update.message.reply_text("❌ Operation cancelled.")
    return ConversationHandler.END

async def leader_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Go back to leader menu"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if user_id not in LEADERS and user_id != config.OWNER_ID:
        await query.answer("❌ Unauthorized", show_alert=True)
        return
    
    keyboard = [
        [InlineKeyboardButton("📤 Upload Session", callback_data='leader_upload')],
        [InlineKeyboardButton("📱 Upload Number", callback_data='leader_upload_number')],
        [InlineKeyboardButton("📦 Bulk Upload", callback_data='leader_bulk_upload')],
        [InlineKeyboardButton("🗑️ Delete Sessions", callback_data='leader_delete_sessions')],
        [InlineKeyboardButton("📊 My Stats", callback_data='leader_stats')],
        [InlineKeyboardButton("💸 Request Withdrawal", callback_data='leader_withdrawal')],
        [InlineKeyboardButton("📜 Withdrawal History", callback_data='leader_withdrawal_history')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "👨‍💼 Seller Panel\n\n"
        "Select an option below:",
        reply_markup=reply_markup
    )

def filter_spam_sessions(sessions_list):
    """
    Remove spam and frozen sessions from list
    Returns: (clean_sessions, removed_count, removed_details)
    """
    clean_sessions = []
    spam_removed = 0
    frozen_removed = 0
    
    for session in sessions_list:
        status = session.get('spam_status', 'Unknown')
        
        if status in ['Spam', 'Frozen']:
            if status == 'Spam':
                spam_removed += 1
            else:
                frozen_removed += 1
        else:
            # Keep Free and Unknown
            clean_sessions.append(session)
    
    removed_details = []
    if spam_removed > 0:
        removed_details.append(f"🟡 {spam_removed} Spam")
    if frozen_removed > 0:
        removed_details.append(f"🔴 {frozen_removed} Frozen")
    
    return clean_sessions, spam_removed + frozen_removed, removed_details

async def leader_confirm_session_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Leader: Send sessions for admin approval - with spam filtering"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if user_id not in LEADERS and user_id != config.OWNER_ID:
        await query.answer("❌ Unauthorized", show_alert=True)
        return ConversationHandler.END
    
    if query.data == 'leader_upload_confirm_no':
        context.user_data.clear()
        await query.edit_message_text("❌ Cancelled")
        return ConversationHandler.END
    
    try:
        database = get_db()
        
        # Bulk upload
        if 'bulk_sessions' in context.user_data:
            original_sessions = context.user_data['bulk_sessions']
            
            # ✅ FILTER SPAM SESSIONS
            clean_sessions, removed_count, removed_details = filter_spam_sessions(original_sessions)
            
            # Show filtering results if any removed
            if removed_count > 0:
                removed_text = ", ".join(removed_details)
                await query.edit_message_text(
                    f"🔍 Spam Filter Results\n\n"
                    f"📦 Total uploaded: {len(original_sessions)}\n"
                    f"✅ Clean sessions: {len(clean_sessions)}\n"
                    f"❌ Removed: {removed_count}\n"
                    f"   {removed_text}\n\n"
                    f"⏳ Proceeding with {len(clean_sessions)} clean sessions..."
                )
                await asyncio.sleep(2)
            
            # Check if any sessions left
            if len(clean_sessions) == 0:
                await query.edit_message_text(
                    "❌ All sessions were spam/frozen!\n\n"
                    "No sessions to upload.\n"
                    "Please upload different sessions."
                )
                context.user_data.clear()
                return ConversationHandler.END
            
            sessions = clean_sessions  # Use filtered sessions
            
            await query.edit_message_text(f"⏳ Submitting {len(sessions)} sessions for approval...")
            
            # Store in pending_uploads collection
            pending_data = {
                "uploader_id": user_id,
                "uploader_username": query.from_user.username or query.from_user.first_name,
                "sessions": sessions,
                "upload_type": "bulk",
                "status": "pending",
                "created_at": datetime.utcnow()
            }
            result = database.pending_uploads.insert_one(pending_data)
            pending_id = result.inserted_id
            
            # Send to admin for approval
            try:
                keyboard = [
                    [
                        InlineKeyboardButton("✅ Approve All", callback_data=f'approve_upload_{pending_id}'),
                        InlineKeyboardButton("❌ Reject All", callback_data=f'reject_upload_{pending_id}')
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                # Build admin message with filtering info
                admin_message = (
                    f"📤 New Bulk Upload Request\n\n"
                    f"👨‍💼 Leader: @{query.from_user.username or 'Unknown'} (ID: {user_id})\n"
                    f"📦 Sessions: {len(sessions)}\n"
                    f"🌍 Country: {sessions[0]['country']}\n"
                    f"💰 Price: ${sessions[0]['price']:.2f} each\n"
                    f"⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
                )
                
                if removed_count > 0:
                    admin_message += f"\n🔍 Spam Filtered: {removed_count} removed\n"
                
                admin_message += "\n⚠️ Sessions will NOT be added until you approve!"
                
                await context.bot.send_message(
                    config.OWNER_ID,
                    admin_message,
                    reply_markup=reply_markup
                )
            except Exception as e:
                logger.error(f"Failed to notify admin: {e}")
            
            # Build success message
            success_message = (
                f"✅ Upload Submitted!\n\n"
                f"📦 {len(sessions)} sessions sent for admin approval\n"
            )
            
            if removed_count > 0:
                removed_text = ", ".join(removed_details)
                success_message += f"🗑️ Filtered out: {removed_count} ({removed_text})\n"
            
            success_message += (
                f"⏳ Please wait for admin to review\n\n"
                "You'll be notified once approved!"
            )
            
            await query.edit_message_text(success_message)
        
        # Single upload
        else:
            session_data = context.user_data.get('session_data')
            
            if not session_data:
                await query.edit_message_text("❌ Session data not found")
                return ConversationHandler.END
            
            # Check spam status for single upload too
            spam_status = session_data.get('spam_status', 'Unknown')
            if spam_status in ['Spam', 'Frozen']:
                await query.edit_message_text(
                    f"❌ Cannot upload this session!\n\n"
                    f"📊 Status: {spam_status}\n\n"
                    "This session is flagged and cannot be uploaded.\n"
                    "Please use a different session."
                )
                context.user_data.clear()
                return ConversationHandler.END
            
            # Store in pending_uploads collection
            pending_data = {
                "uploader_id": user_id,
                "uploader_username": query.from_user.username or query.from_user.first_name,
                "sessions": [session_data],
                "upload_type": "single",
                "status": "pending",
                "created_at": datetime.utcnow()
            }
            result = database.pending_uploads.insert_one(pending_data)
            pending_id = result.inserted_id
            
            # Send to admin for approval
            try:
                keyboard = [
                    [
                        InlineKeyboardButton("✅ Approve", callback_data=f'approve_upload_{pending_id}'),
                        InlineKeyboardButton("❌ Reject", callback_data=f'reject_upload_{pending_id}')
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await context.bot.send_message(
                    config.OWNER_ID,
                    f"📤 New Session Upload Request\n\n"
                    f"👨‍💼 Leader: @{query.from_user.username or 'Unknown'} (ID: {user_id})\n"
                    f"📱 Phone: {session_data['phone']}\n"
                    f"🌍 Country: {session_data['country']}\n"
                    f"💰 Price: ${session_data['price']:.2f}\n"
                    f"🔒 2FA: {'Yes' if session_data['has_2fa'] else 'No'}\n"
                    f"📊 Status: {session_data.get('spam_status', 'Unknown')}\n"
                    f"⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
                    f"⚠️ Session will NOT be added until you approve!",
                    reply_markup=reply_markup
                )
            except Exception as e:
                logger.error(f"Failed to notify admin: {e}")
            
            await query.edit_message_text(
                f"✅ Upload Submitted!\n\n"
                f"📱 Phone: {session_data['phone']}\n"
                f"⏳ Waiting for admin approval\n\n"
                "You'll be notified once approved!"
            )
        
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        await query.edit_message_text(f"❌ Error: {str(e)}")
    
    finally:
        context.user_data.clear()
    
    return ConversationHandler.END

async def process_bulk_session_parallel(session_file_data, api_creds, bot):
    """
    Process a single session file with given API credentials
    Returns: success, session_data or None
    """
    try:
        # Extract session data
        file_path = session_file_data['file_path']
        phone = session_file_data['phone']
        
        # Create client
        client = TelegramClient(
            file_path.replace('.session', ''),
            api_creds['api_id'],
            api_creds['api_hash']
        )
        
        await client.connect()
        
        if not await client.is_user_authorized():
            await client.disconnect()
            return False, None
        
        # Get spam status
        spam_check = await check_account_with_spambot(client, phone)
        
        # Extract info
        auto_info = await extract_session_info(client)
        
        await client.disconnect()
        
        # Upload to storage channel
        with open(file_path, 'rb') as f:
            channel_message = await bot.send_document(
                chat_id=config.STORAGE_CHANNEL_ID,
                document=f,
                filename=os.path.basename(file_path),
                caption=f"📱 {phone}\n🔍 {spam_check['status']}\n⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            )
        
        return True, {
            'message_id': channel_message.message_id,
            'phone': phone,
            'spam_status': spam_check['status'],
            'info': auto_info
        }
        
    except Exception as e:
        logger.error(f"Error processing {session_file_data.get('phone')}: {e}")
        return False, None

async def admin_approve_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin approves leader's upload - adds sessions to database"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != config.OWNER_ID:
        await query.answer("❌ Admin only", show_alert=True)
        return
    
    pending_id = query.data.replace('approve_upload_', '')
    database = get_db()
    
    try:
        # Get pending upload
        pending = database.pending_uploads.find_one({"_id": ObjectId(pending_id)})
        
        if not pending:
            await query.edit_message_text("❌ Upload not found or already processed.")
            return
        
        if pending['status'] != 'pending':
            await query.edit_message_text(f"❌ Already {pending['status']}")
            return
        
        # Add all sessions to database
        sessions = pending['sessions']
        uploader_id = pending['uploader_id']
        added_count = 0
        failed_count = 0
        
        for session_data in sessions:
            try:
                TelegramSession.create(
                    session_string=str(session_data['message_id']),
                    phone_number=session_data['phone'],
                    country=session_data['country'],
                    has_2fa=session_data['has_2fa'],
                    two_fa_password=session_data.get('two_fa_password'),
                    price=session_data['price'],
                    info=session_data.get('info'),
                    spam_status=session_data.get('spam_status', 'Unknown'),
                    uploader_id=uploader_id
                )
                added_count += 1
            except Exception as e:
                logger.error(f"Error adding session: {e}")
                failed_count += 1
                continue
        
        # Update pending status
        database.pending_uploads.update_one(
            {"_id": ObjectId(pending_id)},
            {"$set": {
                "status": "approved",
                "approved_at": datetime.utcnow(),
                "approved_by": query.from_user.id,
                "added_count": added_count,
                "failed_count": failed_count
            }}
        )
        
        # Notify admin
        await query.edit_message_text(
            f"✅ Upload Approved!\n\n"
            f"👨‍💼 Leader ID: {uploader_id}\n"
            f"📦 Added: {added_count}/{len(sessions)} sessions\n"
            f"❌ Failed: {failed_count}\n\n"
            "Sessions are now live in the store!"
        )
        
        # Notify leader
        try:
            await context.bot.send_message(
                uploader_id,
                f"✅ Upload Approved!\n\n"
                f"📦 {added_count} session(s) approved by admin\n"
                f"💰 They are now available for sale!\n\n"
                "Thank you for contributing!"
            )
        except Exception as e:
            logger.error(f"Failed to notify leader: {e}")
    
    except Exception as e:
        logger.error(f"Error approving upload: {e}")
        await query.edit_message_text(f"❌ Error: {str(e)}")

async def admin_reject_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin rejects leader's upload - does not add to database"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != config.OWNER_ID:
        await query.answer("❌ Admin only", show_alert=True)
        return
    
    pending_id = query.data.replace('reject_upload_', '')
    database = get_db()
    
    try:
        # Get pending upload
        pending = database.pending_uploads.find_one({"_id": ObjectId(pending_id)})
        
        if not pending:
            await query.edit_message_text("❌ Upload not found or already processed.")
            return
        
        if pending['status'] != 'pending':
            await query.edit_message_text(f"❌ Already {pending['status']}")
            return
        
        # Update pending status
        database.pending_uploads.update_one(
            {"_id": ObjectId(pending_id)},
            {"$set": {
                "status": "rejected",
                "rejected_at": datetime.utcnow(),
                "rejected_by": query.from_user.id
            }}
        )
        
        uploader_id = pending['uploader_id']
        session_count = len(pending['sessions'])
        
        # Notify admin
        await query.edit_message_text(
            f"❌ Upload Rejected\n\n"
            f"👨‍💼 Leader ID: {uploader_id}\n"
            f"📦 Rejected: {session_count} session(s)\n\n"
            "Sessions were NOT added to database."
        )
        
        # Notify leader
        try:
            await context.bot.send_message(
                uploader_id,
                f"❌ Upload Rejected\n\n"
                f"📦 {session_count} session(s) were not approved\n\n"
                "Please ensure you're uploading quality sessions.\n"
                "Contact admin if you have questions."
            )
        except Exception as e:
            logger.error(f"Failed to notify leader: {e}")
    
    except Exception as e:
        logger.error(f"Error rejecting upload: {e}")
        await query.edit_message_text(f"❌ Error: {str(e)}")

# ============================================
# UPLOAD NUMBER (MANUAL SESSION CREATION)
# ============================================

async def leader_upload_number_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start upload number flow"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if user_id not in LEADERS and user_id != config.OWNER_ID:
        await query.answer("❌ Unauthorized", show_alert=True)
        return ConversationHandler.END
    
    await query.edit_message_text(
        "📱 Upload Number - Step 1\n\n"
        "Enter the country name:\n\n"
        "Examples: India, USA, UK, Russia\n\n"
        "Send /cancel to abort."
    )
    
    context.user_data.clear()
    return LEADER_UPLOAD_NUMBER_COUNTRY

async def leader_upload_number_receive_country(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive country for upload number"""
    user_id = update.effective_user.id
    if user_id not in LEADERS and user_id != config.OWNER_ID:
        await update.message.reply_text("❌ Unauthorized")
        return ConversationHandler.END
    
    country = update.message.text.strip()
    
    if len(country) < 2:
        await update.message.reply_text("❌ Please enter a valid country name.\n\nTry again:")
        return LEADER_UPLOAD_NUMBER_COUNTRY
    
    context.user_data['manual_country'] = country
    
    await update.message.reply_text(
        f"✅ Country: {country}\n\n"
        "📱 Step 2: Enter Phone Number\n\n"
        "Format: +1234567890 (with country code)\n\n"
        "Example: +918012345678"
    )
    
    return LEADER_UPLOAD_NUMBER_PHONE

async def leader_upload_number_receive_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive phone and request OTP using OpenTele"""
    user_id = update.effective_user.id
    if user_id not in LEADERS and user_id != config.OWNER_ID:
        await update.message.reply_text("❌ Unauthorized")
        return ConversationHandler.END
    
    phone = update.message.text.strip()
    
    if not phone.startswith('+') or len(phone) < 10:
        await update.message.reply_text("❌ Invalid phone format!\n\nExample: +918012345678")
        return LEADER_UPLOAD_NUMBER_PHONE
    
    context.user_data['manual_phone'] = phone
    
    if not OPENTELE_AVAILABLE:
        await update.message.reply_text(
            "❌ OpenTele not installed!\n\n"
            "Manual uploads require OpenTele.\n"
            "Contact admin to install: pip install opentele"
        )
        return ConversationHandler.END
    
    await update.message.reply_text(
        f"✅ Phone: {phone}\n\n"
        "⏳ Requesting OTP..."
    )
    
    try:
        import tempfile
        from opentele.tl import TelegramClient
        from opentele.api import API
        
        # Try different OpenTele APIs in order
        apis_to_try = [
            ('TelegramDesktop', API.TelegramDesktop),
            ('TelegramAndroid', API.TelegramAndroid),
            ('TelegramAndroidX', API.TelegramAndroidX),
            ('TelegramIOS', API.TelegramIOS),
            ('TelegramMacOS', API.TelegramMacOS)
        ]
        
        client = None
        sent_code = None
        selected_api_name = None
        session_name = None
        session_path = None
        
        for api_name, api in apis_to_try:
            try:
                logger.info(f"🔍 Trying OpenTele API: {api_name}")
                
                # Create unique session name
                temp_dir = tempfile.gettempdir()
                session_name = f"manual_upload_{user_id}_{int(time.time())}"
                session_path = os.path.join(temp_dir, session_name)
                
                # Remove any existing session files
                for ext in ['.session', '.session-journal']:
                    file_to_remove = f"{session_path}{ext}"
                    if os.path.exists(file_to_remove):
                        os.remove(file_to_remove)
                
                # ✅ IMPORTANT: Use session_name (not session_path) for OpenTele client
                # OpenTele will create the session in the current directory or temp
                client = TelegramClient(session_name, api=api)
                
                await client.connect()
                
                if not client.is_connected():
                    logger.warning(f"❌ Failed to connect with {api_name}")
                    if client:
                        try:
                            await client.disconnect()
                        except:
                            pass
                    client = None
                    continue
                
                # Send OTP request
                logger.info(f"📤 Sending OTP request to {phone} using {api_name}...")
                sent_code = await client.send_code_request(phone)
                
                # If we got here, it worked!
                selected_api_name = api_name
                logger.info(f"✅ Successfully sent OTP using {api_name}")
                break
                
            except Exception as e:
                logger.error(f"❌ Failed with {api_name}: {e}")
                if client and hasattr(client, 'is_connected'):
                    try:
                        if client.is_connected():
                            await client.disconnect()
                    except:
                        pass
                client = None
                continue
        
        if not client or not sent_code:
            raise Exception("Failed to send OTP with all available OpenTele APIs")
        
        # ✅ CRITICAL: Store both client AND the session_name AND session_path
        context.user_data['temp_client'] = client
        context.user_data['session_name'] = session_name  # Just the name
        context.user_data['session_path'] = session_path  # Full path without .session
        context.user_data['phone_code_hash'] = sent_code.phone_code_hash
        context.user_data['api_used'] = selected_api_name
        
        logger.info(f"✅ OTP sent successfully to {phone} via {selected_api_name}")
        logger.info(f"📁 Session name: {session_name}")
        logger.info(f"📁 Session path: {session_path}")
        
        await update.message.reply_text(
            f"✅ OTP sent to {phone}!\n"
            f"🔧 Using: {selected_api_name}\n\n"
            "📨 Enter the OTP code you received:"
        )
        
        return LEADER_UPLOAD_NUMBER_OTP
        
    except Exception as e:
        logger.error(f"❌ Error sending OTP: {e}")
        import traceback
        traceback.print_exc()
        
        # Cleanup on error
        if 'temp_client' in context.user_data:
            try:
                client = context.user_data['temp_client']
                if hasattr(client, 'is_connected') and client.is_connected():
                    await client.disconnect()
            except:
                pass
            context.user_data.pop('temp_client', None)
        
        await update.message.reply_text(
            f"❌ Error sending OTP\n\n"
            f"Details: {str(e)}\n\n"
            "Please try again or contact admin."
        )
        return LEADER_UPLOAD_NUMBER_PHONE

async def leader_upload_number_receive_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive OTP code"""
    user_id = update.effective_user.id
    if user_id not in LEADERS and user_id != config.OWNER_ID:
        await update.message.reply_text("❌ Unauthorized")
        return ConversationHandler.END
    
    otp = update.message.text.strip()
    
    if len(otp) < 5:
        await update.message.reply_text("❌ OTP seems too short.\n\nTry again:")
        return LEADER_UPLOAD_NUMBER_OTP
    
    context.user_data['manual_otp'] = otp
    
    await update.message.reply_text(
        f"✅ OTP: {otp}\n\n"
        "🔐 Step 4: 2FA Password\n\n"
        "Enter the 2FA password, or type 'no' if there's no 2FA:"
    )
    
    return LEADER_UPLOAD_NUMBER_2FA

async def leader_upload_number_receive_2fa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive 2FA password"""
    user_id = update.effective_user.id
    if user_id not in LEADERS and user_id != config.OWNER_ID:
        await update.message.reply_text("❌ Unauthorized")
        return ConversationHandler.END
    
    two_fa_text = update.message.text.strip()
    
    if two_fa_text.lower() in ['no', 'none', 'skip']:
        context.user_data['manual_has_2fa'] = False
        context.user_data['manual_2fa'] = None
        two_fa_display = "No 2FA"
    else:
        context.user_data['manual_has_2fa'] = True
        context.user_data['manual_2fa'] = two_fa_text
        two_fa_display = "Yes"
    
    await update.message.reply_text(
        f"✅ 2FA: {two_fa_display}\n\n"
        "💰 Step 5: Enter Price\n\n"
        "⚠️ Remember: 15% commission to owner\n"
        "You earn 85% of the price\n\n"
        "Examples:\n"
        "• Set $2.00 → You earn $1.70\n"
        "• Set $1.50 → You earn $1.28\n\n"
        "Enter price in USD:"
    )
    
    return LEADER_UPLOAD_NUMBER_PRICE

async def leader_upload_number_receive_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive price for manual session"""
    user_id = update.effective_user.id
    if user_id not in LEADERS and user_id != config.OWNER_ID:
        await update.message.reply_text("❌ Unauthorized")
        return ConversationHandler.END
    
    try:
        price = float(update.message.text.strip())
        
        if price <= 0:
            await update.message.reply_text("❌ Price must be greater than 0.\n\nTry again:")
            return LEADER_UPLOAD_NUMBER_PRICE
        
        context.user_data['manual_price'] = price
        
        await update.message.reply_text(
            f"✅ Price: ${price:.2f}\n\n"
            "📝 Step 6: Additional Info\n\n"
            "Enter account info (Premium, Verified, etc.)\n"
            "Or type 'none' to skip:"
        )
        
        return LEADER_UPLOAD_NUMBER_INFO
        
    except ValueError:
        await update.message.reply_text("❌ Invalid price. Enter a number (e.g., 1.5)\n\nTry again:")
        return LEADER_UPLOAD_NUMBER_PRICE

async def leader_upload_number_receive_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive info and show confirmation"""
    user_id = update.effective_user.id
    if user_id not in LEADERS and user_id != config.OWNER_ID:
        await update.message.reply_text("❌ Unauthorized")
        return ConversationHandler.END
    
    info_text = update.message.text.strip()
    
    if info_text.lower() in ['none', 'no', 'skip']:
        context.user_data['manual_info'] = None
        info_display = "None"
    else:
        context.user_data['manual_info'] = info_text
        info_display = info_text
    
    # Build confirmation message
    country = context.user_data.get('manual_country')
    phone = context.user_data.get('manual_phone')
    price = context.user_data.get('manual_price')
    has_2fa = context.user_data.get('manual_has_2fa', False)
    
    confirmation = (
        "📋 Confirm Upload\n\n"
        f"🌍 Country: {country}\n"
        f"📱 Phone: {phone}\n"
        f"💰 Price: ${price:.2f}\n"
        f"🔐 2FA: {'Yes' if has_2fa else 'No'}\n"
        f"ℹ️ Info: {info_display}\n\n"
        "⚠️ This will create and upload the session.\n\n"
        "Proceed?"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("✅ Confirm", callback_data='leader_upload_number_confirm'),
            InlineKeyboardButton("❌ Cancel", callback_data='leader_back')
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(confirmation, reply_markup=reply_markup)
    
    return LEADER_UPLOAD_NUMBER_CONFIRM

async def leader_upload_number_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create session string and upload to storage channel"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if user_id not in LEADERS and user_id != config.OWNER_ID:
        await query.answer("❌ Unauthorized", show_alert=True)
        return ConversationHandler.END
    
    await query.edit_message_text("⏳ Logging in to Telegram and creating session...")
    
    client = None
    session_file_path = None
    
    try:
        phone = context.user_data.get('manual_phone')
        otp = context.user_data.get('manual_otp')
        country = context.user_data.get('manual_country')
        price = context.user_data.get('manual_price')
        has_2fa = context.user_data.get('manual_has_2fa', False)
        two_fa = context.user_data.get('manual_2fa')
        info = context.user_data.get('manual_info')
        phone_code_hash = context.user_data.get('phone_code_hash')
        
        # Get the existing client and session path from context
        client = context.user_data.get('temp_client')
        session_path = context.user_data.get('session_path')  # This is WITHOUT .session extension
        
        if not client or not phone_code_hash or not session_path:
            await query.edit_message_text(
                "❌ Session expired!\n\n"
                "Please start over from the beginning."
            )
            context.user_data.clear()
            return ConversationHandler.END
        
        # Expected session file path
        session_file_path = f"{session_path}.session"
        logger.info(f"📁 Expected session file: {session_file_path}")
        
        # Import error types
        from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError
        
        try:
            await query.edit_message_text("🔐 Verifying OTP...")
            
            # Sign in with phone, code, and phone_code_hash
            try:
                await client.sign_in(phone, otp, phone_code_hash=phone_code_hash)
                
                await query.edit_message_text("✅ Login successful! Saving session...")
                
            except SessionPasswordNeededError:
                # 2FA required
                if not has_2fa or not two_fa:
                    if client.is_connected():
                        await client.disconnect()
                    await query.edit_message_text(
                        "❌ 2FA required but not provided!\n\n"
                        "Please start over and provide 2FA password."
                    )
                    context.user_data.clear()
                    return ConversationHandler.END
                
                await query.edit_message_text("🔐 Verifying 2FA password...")
                
                try:
                    await client.sign_in(password=two_fa)
                    
                    await query.edit_message_text("✅ 2FA verified! Saving session...")
                    
                except Exception as e:
                    if client.is_connected():
                        await client.disconnect()
                    await query.edit_message_text(
                        f"❌ 2FA verification failed!\n\n"
                        f"Error: {str(e)}\n\n"
                        "Please check your 2FA password and try again."
                    )
                    context.user_data.clear()
                    return ConversationHandler.END
            
            except PhoneCodeInvalidError:
                if client.is_connected():
                    await client.disconnect()
                await query.edit_message_text(
                    "❌ Invalid OTP code!\n\n"
                    "The code you entered is incorrect or expired.\n\n"
                    "Please start over with a fresh OTP."
                )
                context.user_data.clear()
                return ConversationHandler.END
            
            # ✅ CRITICAL: Disconnect to save the session file
            logger.info("💾 Disconnecting client to save session...")
            if client.is_connected():
                await client.disconnect()
            
            # Wait for session file to be written
            await asyncio.sleep(2)
            
            # Check all possible session file locations
            possible_paths = [
                session_file_path,  # /tmp/manual_upload_XXX.session
                f"{session_path}.session",  # Same as above
                os.path.join(os.getcwd(), f"{os.path.basename(session_path)}.session"),  # Current dir
                os.path.join(TEMP_DIR, f"{os.path.basename(session_path)}.session"),  # TEMP_DIR
            ]
            
            # Find the actual session file
            found_session_file = None
            for path in possible_paths:
                logger.info(f"🔍 Checking: {path}")
                if os.path.exists(path):
                    found_session_file = path
                    logger.info(f"✅ Found session file: {path}")
                    break
            
            if not found_session_file:
                # List all .session files in temp directory
                logger.error(f"❌ Session file not found. Searching in {TEMP_DIR}...")
                all_session_files = []
                for file in os.listdir(TEMP_DIR):
                    if file.endswith('.session') and 'manual_upload' in file:
                        full_path = os.path.join(TEMP_DIR, file)
                        all_session_files.append(full_path)
                        logger.info(f"📄 Found: {full_path}")
                
                if all_session_files:
                    # Use the most recent one
                    found_session_file = max(all_session_files, key=os.path.getmtime)
                    logger.info(f"✅ Using most recent: {found_session_file}")
                else:
                    await query.edit_message_text(
                        "❌ Failed to create session file!\n\n"
                        "The session was not saved properly.\n"
                        "Please try again."
                    )
                    context.user_data.clear()
                    return ConversationHandler.END
            
            # Update session_file_path to the found file
            session_file_path = found_session_file
            logger.info(f"✅ Using session file: {session_file_path}")
            
            await query.edit_message_text("🔍 Checking spam status...")
            
            # Check spam status using your API credentials
            spam_client = None
            spam_check = {'status': 'Unknown', 'message': 'Could not check'}
            
            try:
                # Create new client with YOUR API to check spam status
                spam_session_name = f"spam_check_{user_id}_{int(time.time())}"
                spam_session_path = os.path.join(TEMP_DIR, spam_session_name)
                
                # Copy the session file for spam check
                import shutil
                spam_session_file = f"{spam_session_path}.session"
                shutil.copy2(session_file_path, spam_session_file)
                logger.info(f"📋 Copied session to: {spam_session_file}")
                
                spam_client = TelegramClient(
                    spam_session_path,
                    config.TELEGRAM_API_ID,
                    config.TELEGRAM_API_HASH
                )
                
                await spam_client.connect()
                
                if await spam_client.is_user_authorized():
                    spam_check = await check_account_with_spambot(spam_client, phone)
                    logger.info(f"📊 SpamBot result: {spam_check['status']} - {spam_check['message']}")
                
                await spam_client.disconnect()
                
                # Clean up spam check session
                for ext in ['.session', '.session-journal']:
                    file_to_remove = f"{spam_session_path}{ext}"
                    if os.path.exists(file_to_remove):
                        try:
                            os.remove(file_to_remove)
                        except:
                            pass
                
            except Exception as e:
                logger.error(f"Spam check error: {e}")
                if spam_client and spam_client.is_connected():
                    try:
                        await spam_client.disconnect()
                    except:
                        pass
            
            await query.edit_message_text("📤 Uploading to storage channel...")
            
            # Get emoji for spam status
            status_emoji = {
                'Free': '🟢',
                'Frozen': '🔴',
                'Spam': '🟡',
                'Unknown': '❓',
                'Error': '❌'
            }
            emoji = status_emoji.get(spam_check['status'], '❓')
            
            # Upload session file to storage channel
            with open(session_file_path, 'rb') as f:
                caption_parts = [
                    f"📱 Phone: {phone}",
                    f"{emoji} Status: {spam_check['status']}"
                ]
                
                if info:
                    caption_parts.append(f"ℹ️ {info}")
                
                caption_parts.append(f"👨‍💼 Uploaded by: {query.from_user.username or user_id}")
                caption_parts.append(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}")
                
                channel_message = await context.bot.send_document(
                    chat_id=config.STORAGE_CHANNEL_ID,
                    document=f,
                    filename=f"{phone}.session",
                    caption="\n".join(caption_parts)
                )
            
            message_id = channel_message.message_id
            logger.info(f"✅ Session uploaded to channel, message_id: {message_id}")
            
        except Exception as login_error:
            # Handle login/session creation errors
            logger.error(f"Login error: {login_error}")
            import traceback
            traceback.print_exc()
            
            if client and client.is_connected():
                try:
                    await client.disconnect()
                except:
                    pass
            
            await query.edit_message_text(
                f"❌ Login Failed\n\n"
                f"Error: {str(login_error)}\n\n"
                "Please try again or contact support."
            )
            context.user_data.clear()
            return ConversationHandler.END
        
        # Store in pending_uploads for admin approval
        database = get_db()
        
        session_data = {
            'message_id': message_id,
            'phone': phone,
            'country': country,
            'has_2fa': has_2fa,
            'two_fa_password': two_fa,
            'price': price,
            'info': info,
            'spam_status': spam_check['status']
        }
        
        pending_data = {
            "uploader_id": user_id,
            "uploader_username": query.from_user.username or query.from_user.first_name,
            "sessions": [session_data],
            "upload_type": "manual_number",
            "status": "pending",
            "created_at": datetime.utcnow()
        }
        
        result = database.pending_uploads.insert_one(pending_data)
        pending_id = result.inserted_id
        
        # Send to admin for approval
        keyboard = [
            [
                InlineKeyboardButton("✅ Approve", callback_data=f'approve_upload_{pending_id}'),
                InlineKeyboardButton("❌ Reject", callback_data=f'reject_upload_{pending_id}')
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await context.bot.send_message(
            config.OWNER_ID,
            f"📱 New Manual Number Upload\n\n"
            f"👨‍💼 Seller: @{query.from_user.username or 'Unknown'} (ID: {user_id})\n"
            f"📱 Phone: {phone}\n"
            f"🌍 Country: {country}\n"
            f"💰 Price: ${price:.2f}\n"
            f"🔐 2FA: {'Yes' if has_2fa else 'No'}\n"
            f"ℹ️ Info: {info or 'None'}\n"
            f"📊 Status: {spam_check['status']}\n"
            f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
            f"⚠️ Session will NOT be added until you approve!",
            reply_markup=reply_markup
        )
        
        await query.edit_message_text(
            f"✅ Upload Submitted!\n\n"
            f"📱 Phone: {phone}\n"
            f"🌍 Country: {country}\n"
            f"💰 Price: ${price:.2f}\n"
            f"📊 Status: {spam_check['status']}\n\n"
            f"⏳ Waiting for admin approval\n\n"
            "You'll be notified once approved!"
        )
        
    except Exception as e:
        logger.error(f"Error uploading manual number: {e}")
        import traceback
        traceback.print_exc()
        await query.edit_message_text(f"❌ Error: {str(e)}")
    
    finally:
        # Clean up session files
        if session_file_path and os.path.exists(session_file_path):
            # Clean up all related session files
            base_path = session_file_path.replace('.session', '')
            for ext in ['.session', '.session-journal']:
                file_to_remove = f"{base_path}{ext}"
                if os.path.exists(file_to_remove):
                    try:
                        os.remove(file_to_remove)
                        logger.info(f"🧹 Cleaned up: {file_to_remove}")
                    except Exception as e:
                        logger.error(f"Failed to remove {file_to_remove}: {e}")
        
        context.user_data.clear()
    
    return ConversationHandler.END


def setup_leader_handlers(application):
    """Setup leader command handlers"""
    logger.info("✅ Leader handlers setup started")
    
    # Main leader command
    application.add_handler(CommandHandler("leader", leader_start))
    
    # Single session upload conversation
    session_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(leader_upload_start, pattern='^leader_upload$')],
        states={
            LEADER_UPLOAD_SESSION: [MessageHandler(filters.Document.ALL, leader_receive_session_file)],
            LEADER_GET_COUNTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, leader_receive_country)],
            LEADER_GET_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, leader_receive_price)],
            LEADER_GET_INFO: [MessageHandler(filters.TEXT & ~filters.COMMAND, leader_receive_info)],
            LEADER_GET_2FA: [MessageHandler(filters.TEXT & ~filters.COMMAND, leader_receive_2fa_password)],
            LEADER_CONFIRM_DETAILS: [CallbackQueryHandler(leader_confirm_session_upload, pattern='^leader_upload_confirm_')]
        },
        fallbacks=[CommandHandler('cancel', leader_cancel_operation)],
        allow_reentry=True
    )
    application.add_handler(session_conv)
    
    # Bulk upload conversation
    bulk_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(leader_bulk_upload_start, pattern='^leader_bulk_upload$')],
        states={
            LEADER_UPLOAD_BULK: [
                MessageHandler(filters.Document.ALL, leader_receive_bulk_files),
                MessageHandler(filters.TEXT & ~filters.COMMAND, leader_receive_bulk_files),
                CommandHandler('done', leader_receive_bulk_files)
            ],
            LEADER_GET_COUNTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, leader_receive_country)],
            LEADER_GET_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, leader_receive_price)],
            LEADER_GET_INFO: [MessageHandler(filters.TEXT & ~filters.COMMAND, leader_receive_info)],
            LEADER_GET_2FA: [MessageHandler(filters.TEXT & ~filters.COMMAND, leader_receive_2fa_password)],
            LEADER_CONFIRM_DETAILS: [CallbackQueryHandler(leader_confirm_session_upload, pattern='^leader_upload_confirm_')]
        },
        fallbacks=[CommandHandler('cancel', leader_cancel_operation)],
        allow_reentry=True
    )
    application.add_handler(bulk_conv)
    
    # Upload number (manual) conversation
    upload_number_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(leader_upload_number_start, pattern='^leader_upload_number$')],
        states={
            LEADER_UPLOAD_NUMBER_COUNTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, leader_upload_number_receive_country)],
            LEADER_UPLOAD_NUMBER_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, leader_upload_number_receive_phone)],
            LEADER_UPLOAD_NUMBER_OTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, leader_upload_number_receive_otp)],
            LEADER_UPLOAD_NUMBER_2FA: [MessageHandler(filters.TEXT & ~filters.COMMAND, leader_upload_number_receive_2fa)],
            LEADER_UPLOAD_NUMBER_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, leader_upload_number_receive_price)],
            LEADER_UPLOAD_NUMBER_INFO: [MessageHandler(filters.TEXT & ~filters.COMMAND, leader_upload_number_receive_info)],
            LEADER_UPLOAD_NUMBER_CONFIRM: [CallbackQueryHandler(leader_upload_number_confirm, pattern='^leader_upload_number_confirm$')]
        },
        fallbacks=[CommandHandler('cancel', leader_cancel_operation)],
        allow_reentry=True
    )
    application.add_handler(upload_number_conv)
    
    # Callback handlers
    application.add_handler(CallbackQueryHandler(leader_stats, pattern='^leader_stats$'))
    application.add_handler(CallbackQueryHandler(leader_back, pattern='^leader_back$'))
    application.add_handler(CallbackQueryHandler(leader_delete_session_start, pattern='^leader_delete_page_'))
    application.add_handler(CallbackQueryHandler(leader_delete_session_start, pattern='^leader_delete_sessions$'))
    application.add_handler(CallbackQueryHandler(leader_confirm_delete_session, pattern='^leader_delete_'))
    application.add_handler(CallbackQueryHandler(leader_execute_delete_session, pattern='^leader_confirm_del_'))
    
    # Admin upload approval handlers
    application.add_handler(CallbackQueryHandler(admin_approve_upload, pattern='^approve_upload_'))
    application.add_handler(CallbackQueryHandler(admin_reject_upload, pattern='^reject_upload_'))
    
    logger.info("✅ Leader handlers registered successfully")