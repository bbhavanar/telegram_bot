"""
Complete Admin Panel with Bulk Upload Support
Replace your entire admin.py with this file
"""

import os
import logging
import zipfile
import io
import asyncio
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
from telethon.errors import SessionPasswordNeededError
import config
import time
from datetime import datetime, timedelta
from database import get_db, TelegramSession, User, Transaction, Purchase

logger = logging.getLogger(__name__)
TEMP_DIR = "/tmp"
os.makedirs(TEMP_DIR, exist_ok=True)

async def extract_session_info(client) -> str:
    """
    Extract detailed info from session
    Returns formatted string with account details
    """
    try:
        from telethon.tl.functions.users import GetFullUserRequest
        
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
        
        # 4. Account age (approximate via user ID)
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
        
        # Combine all info (limit to 4 items)
        if info_parts:
            return " • ".join(info_parts[:4])
        else:
            return None
            
    except Exception as e:
        logger.error(f"Error extracting session info: {e}")
        return None

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
(UPLOAD_SESSION, UPLOAD_BULK, GET_COUNTRY, GET_PRICE, GET_2FA, 
 GET_INFO, CONFIRM_DETAILS, ADD_BALANCE_USER, ADD_BALANCE_AMOUNT,
 SETTINGS_MENU, SET_MIN_DEPOSIT, SET_INR_RATE, SET_TON_RATE,
 EDIT_INFO_COUNTRY, EDIT_INFO_TEXT,
 BROADCAST_SELECT, BROADCAST_MESSAGE, BROADCAST_CONFIRM,
 DELETE_SESSION_CONFIRM) = range(19)

def admin_only(func):
    """Decorator to restrict commands to admin only"""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id != config.OWNER_ID:
            await update.message.reply_text("❌ You don't have permission to use this command.")
            return
        return await func(update, context)
    return wrapper

@admin_only
async def admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin menu - UPDATED WITH DELETE SESSIONS"""
    keyboard = [
        [
            InlineKeyboardButton("📤 Upload", callback_data='admin_upload'),
            InlineKeyboardButton("📦 Bulk Upload", callback_data='admin_bulk_upload')
        ],
        [
            InlineKeyboardButton("💰 Add Balance", callback_data='admin_add_balance'),
            InlineKeyboardButton("📊 Statistics", callback_data='admin_stats')
        ],
        [
            InlineKeyboardButton("👥 Users", callback_data='admin_users'),
            InlineKeyboardButton("👨‍💼 Leaders", callback_data='admin_leaders')
        ],
        [
            InlineKeyboardButton("⚙️ Settings", callback_data='admin_settings'),
            InlineKeyboardButton("💳 Transactions", callback_data='admin_transactions')
        ],
        [
            InlineKeyboardButton("📦 Sessions", callback_data='admin_sessions'),
            InlineKeyboardButton("🗑️ Delete Sessions", callback_data='admin_delete_sessions')  # ✅ NEW
        ],
        [
            InlineKeyboardButton("📝 Edit Info", callback_data='admin_edit_info_btn'),
            InlineKeyboardButton("📢 Broadcast", callback_data='admin_broadcast_btn')
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🔧 Admin Panel\n\n"
        "Select an option below:",
        reply_markup=reply_markup
    )

async def check_account_with_spambot(client: TelegramClient, phone: str) -> dict:
    """Check account status using @SpamBot"""
    try:
        logger.info(f"🔍 Checking account status for {phone}...")
        
        # Start conversation with SpamBot
        spambot = await client.get_entity('SpamBot')
        
        # Send /start command
        await client.send_message(spambot, '/start')
        
        # Wait for response
        import asyncio
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

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show bot statistics - FIXED FOR MONGODB"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != config.OWNER_ID:
        await query.answer("❌ Unauthorized", show_alert=True)
        return
    
    try:
        # ✅ Get counts using MongoDB methods
        total_users = User.count()
        total_sessions = TelegramSession.count_total()
        available_sessions = TelegramSession.count_available()
        sold_sessions = TelegramSession.count_sold()
        
        # Get country counts using aggregation
        country_counts = TelegramSession.group_by_country()
        
        # Get revenue
        total_revenue = TelegramSession.get_total_revenue()
        
        # Get deposits
        total_deposits = Transaction.get_total_amount(status='completed')
        pending_deposits = Transaction.get_total_amount(status='pending')
        
        keyboard = [[InlineKeyboardButton("« Back", callback_data='admin_back')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        stats_text = (
            "📊 Bot Statistics\n\n"
            f"👥 Total Users: {total_users}\n"
            f"📱 Total Sessions: {total_sessions}\n"
            f"✅ Available: {available_sessions}\n"
        )
        
        if country_counts:
            for country, count in country_counts[:10]:  # Show top 10
                stats_text += f"  └─ {country}: {count}\n"
        
        stats_text += (
            f"❌ Sold: {sold_sessions}\n\n"
            f"💰 Revenue:\n"
            f"  • Total Revenue: ${total_revenue:.2f}\n"
            f"  • Total Deposits: ${total_deposits:.2f}\n"
            f"  • Pending Deposits: ${pending_deposits:.2f}\n"
        )
        
        await query.edit_message_text(stats_text, reply_markup=reply_markup)
        
    except Exception as e:
        logger.error(f"Stats error: {e}")
        import traceback
        traceback.print_exc()
        await query.edit_message_text(f"❌ Error: {str(e)}")

async def admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user list - FIXED FOR MONGODB"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != config.OWNER_ID:
        await query.answer("❌ Unauthorized", show_alert=True)
        return
    
    try:
        # ✅ Get users using MongoDB method
        users = User.get_all(limit=20)
        
        if not users:
            text = "No users found."
        else:
            text = "👥 Recent Users (Last 20):\n\n"
            for user in users:
                # Count purchases for this user
                purchases_count = Purchase.count_by_user(user['telegram_id'])
                
                text += (
                    f"ID: {user['telegram_id']}\n"
                    f"Username: @{user.get('username') or 'N/A'}\n"
                    f"Balance: ${user['balance']:.2f}\n"
                    f"Purchases: {purchases_count}\n"
                    f"Joined: {user['created_at'].strftime('%Y-%m-%d')}\n\n"
                )
        
        keyboard = [[InlineKeyboardButton("« Back", callback_data='admin_back')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Split message if too long
        if len(text) > 4000:
            chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
            for i, chunk in enumerate(chunks):
                if i == len(chunks) - 1:
                    await query.edit_message_text(chunk, reply_markup=reply_markup)
                else:
                    await context.bot.send_message(query.from_user.id, chunk)
        else:
            await query.edit_message_text(text, reply_markup=reply_markup)
        
    except Exception as e:
        logger.error(f"Users list error: {e}")
        import traceback
        traceback.print_exc()
        await query.edit_message_text(f"❌ Error: {str(e)}")


async def admin_transactions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show recent transactions - FIXED FOR MONGODB"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != config.OWNER_ID:
        await query.answer("❌ Unauthorized", show_alert=True)
        return
    
    try:
        # ✅ Get transactions using MongoDB method
        transactions = Transaction.get_recent(limit=15)
        
        if not transactions:
            text = "No transactions found."
        else:
            text = "💰 Recent Transactions (Last 15):\n\n"
            for txn in transactions:
                text += (
                    f"User ID: {txn['user_id']}\n"
                    f"Type: {txn['type'].title()}\n"
                    f"Amount: ${txn['amount']:.2f}\n"
                    f"Method: {txn.get('payment_method', 'N/A')}\n"
                    f"Status: {txn['status'].title()}\n"
                    f"Date: {txn['created_at'].strftime('%Y-%m-%d %H:%M')}\n\n"
                )
        
        keyboard = [[InlineKeyboardButton("« Back", callback_data='admin_back')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Split message if too long
        if len(text) > 4000:
            chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
            for i, chunk in enumerate(chunks):
                if i == len(chunks) - 1:
                    await query.edit_message_text(chunk, reply_markup=reply_markup)
                else:
                    await context.bot.send_message(query.from_user.id, chunk)
        else:
            await query.edit_message_text(text, reply_markup=reply_markup)
        
    except Exception as e:
        logger.error(f"Transactions error: {e}")
        import traceback
        traceback.print_exc()
        await query.edit_message_text(f"❌ Error: {str(e)}")

async def admin_sessions_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show available sessions by country - FIXED FOR MONGODB"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != config.OWNER_ID:
        await query.answer("❌ Unauthorized", show_alert=True)
        return
    
    try:
        # Get countries with session counts
        country_counts = TelegramSession.group_by_country()
        
        if not country_counts:
            text = "📦 No available sessions."
        else:
            text = f"📦 Available Sessions by Country:\n\n"
            for country, count in country_counts:
                text += f"{country}: {count} sessions\n"
            
            text += "\n--- Details ---\n\n"
            
            # Get all available sessions
            database = get_db()
            sessions = list(database.sessions.find({"is_sold": False}).limit(20))
            
            for session in sessions:
                text += (
                    f"ID: {session['_id']}\n"
                    f"Country: {session['country']}\n"
                    f"Phone: {session.get('phone_number') or 'N/A'}\n"
                    f"2FA: {'Yes 🔐' if session.get('has_2fa') else 'No'}\n"
                    f"Price: ${session['price']:.2f}\n"
                    f"Added: {session['created_at'].strftime('%Y-%m-%d')}\n\n"
                )
        
        keyboard = [[InlineKeyboardButton("« Back", callback_data='admin_back')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, reply_markup=reply_markup)
        
    except Exception as e:
        logger.error(f"Sessions list error: {e}")
        import traceback
        traceback.print_exc()
        await query.edit_message_text(f"❌ Error: {str(e)}")

async def admin_delete_session_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start session deletion - show ONLY AVAILABLE sessions with pagination"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != config.OWNER_ID:
        await query.answer("❌ Admin only", show_alert=True)
        return
    
    # Get page number from callback data or default to 1
    page = 1
    if query.data.startswith('admin_delete_page_'):
        page = int(query.data.replace('admin_delete_page_', ''))
    
    try:
        database = get_db()
        
        # ✅ FIXED: Get ONLY AVAILABLE (unsold) sessions
        total_sessions = database.sessions.count_documents({'is_sold': False})
        
        if total_sessions == 0:
            await query.edit_message_text(
                "❌ No available sessions to delete.\n\n"
                "All sessions are either sold or none uploaded yet."
            )
            return
        
        # Pagination settings
        per_page = 10
        total_pages = (total_sessions + per_page - 1) // per_page
        skip = (page - 1) * per_page
        
        # ✅ FIXED: Get ONLY unsold sessions, ALL COUNTRIES, sorted by creation
        sessions = list(database.sessions.find({
            'is_sold': False  # ✅ ONLY AVAILABLE SESSIONS
        }).sort('created_at', -1).skip(skip).limit(per_page))
        
        if not sessions:
            await query.edit_message_text("❌ No sessions found on this page")
            return
        
        keyboard = []
        
        # ✅ FIXED: Group by country for better organization
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
                    callback_data=f"delete_session_{session['_id']}"
                )])
        
        # ✅ NEW: Pagination controls
        pagination_row = []
        if page > 1:
            pagination_row.append(InlineKeyboardButton(
                "⬅️ Previous",
                callback_data=f'admin_delete_page_{page-1}'
            ))
        
        pagination_row.append(InlineKeyboardButton(
            f"📄 {page}/{total_pages}",
            callback_data='none'
        ))
        
        if page < total_pages:
            pagination_row.append(InlineKeyboardButton(
                "Next ➡️",
                callback_data=f'admin_delete_page_{page+1}'
            ))
        
        keyboard.append(pagination_row)
        keyboard.append([InlineKeyboardButton("« Back to Admin", callback_data='admin_back')])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"🗑️ **Delete Available Sessions**\n\n"
            f"Total Available: {total_sessions}\n"
            f"Page {page}/{total_pages}\n\n"
            f"⚠️ This shows ONLY unsold sessions\n"
            f"⚠️ Deletion cannot be undone!\n\n"
            f"Select a session to delete:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Delete session start error: {e}")
        import traceback
        traceback.print_exc()
        await query.edit_message_text(f"❌ Error: {str(e)}")


# ============================================================================
# FIXED - Admin Confirm Delete (no changes needed but included for completeness)
# ============================================================================

async def admin_confirm_delete_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm session deletion"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != config.OWNER_ID:
        await query.answer("❌ Admin only", show_alert=True)
        return
    
    try:
        from bson.objectid import ObjectId
        
        session_id = query.data.replace('delete_session_', '')
        
        database = get_db()
        session = database.sessions.find_one({'_id': ObjectId(session_id)})
        
        if not session:
            await query.edit_message_text("❌ Session not found")
            return
        
        phone = session.get('phone_number', 'Unknown')
        country = session.get('country', 'Unknown')
        price = session.get('price', 0)
        is_sold = session.get('is_sold', False)
        
        keyboard = [
            [
                InlineKeyboardButton("✅ Yes, Delete", callback_data=f"confirm_delete_{session_id}"),
                InlineKeyboardButton("❌ Cancel", callback_data='admin_delete_sessions')
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        status_text = "🔴 SOLD" if is_sold else "🟢 Available"
        
        await query.edit_message_text(
            f"🗑️ **Confirm Deletion**\n\n"
            f"Status: {status_text}\n"
            f"📱 Phone: `{phone}`\n"
            f"🌍 Country: {country}\n"
            f"💰 Price: ${price:.2f}\n\n"
            f"⚠️ Are you sure you want to delete this session?\n"
            f"This action cannot be undone!",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Confirm delete error: {e}")
        import traceback
        traceback.print_exc()
        await query.edit_message_text(f"❌ Error: {str(e)}")


async def admin_execute_delete_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Execute session deletion"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != config.OWNER_ID:
        await query.answer("❌ Admin only", show_alert=True)
        return
    
    try:
        from bson.objectid import ObjectId
        
        session_id = query.data.replace('confirm_delete_', '')
        
        database = get_db()
        session = database.sessions.find_one({'_id': ObjectId(session_id)})
        
        if not session:
            await query.edit_message_text("❌ Session not found")
            return
        
        phone = session.get('phone_number', 'Unknown')
        country = session.get('country', 'Unknown')
        
        # Delete the session
        result = database.sessions.delete_one({'_id': ObjectId(session_id)})
        
        if result.deleted_count > 0:
            # ✅ FIXED: Return to delete list instead of just showing success
            keyboard = [[InlineKeyboardButton("« Back to Delete List", callback_data='admin_delete_sessions')]]
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
            logger.info(f"✅ Session deleted: {phone} ({country}) by admin {query.from_user.id}")
        else:
            await query.edit_message_text("❌ Failed to delete session")
            
    except Exception as e:
        logger.error(f"Execute delete error: {e}")
        import traceback
        traceback.print_exc()
        await query.edit_message_text(f"❌ Error: {str(e)}")

async def admin_leaders_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show leaders management panel"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != config.OWNER_ID:
        await query.answer("❌ Unauthorized", show_alert=True)
        return
    
    try:
        from leaders import LEADERS
        
        if not LEADERS:
            keyboard = [[InlineKeyboardButton("« Back", callback_data='admin_back')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "👨‍💼 No leaders configured.\n\n"
                "Add leader IDs in leaders.py",
                reply_markup=reply_markup
            )
            return
        
        keyboard = []
        
        for leader_id in LEADERS:
            try:
                # Get leader info
                leader_user = await context.bot.get_chat(leader_id)
                leader_name = leader_user.username or leader_user.first_name or str(leader_id)
                
                keyboard.append([InlineKeyboardButton(
                    f"👤 @{leader_name}" if leader_user.username else f"👤 {leader_name}",
                    callback_data=f'admin_leader_detail_{leader_id}'
                )])
            except Exception as e:
                logger.error(f"Error getting leader {leader_id}: {e}")
                keyboard.append([InlineKeyboardButton(
                    f"👤 ID: {leader_id}",
                    callback_data=f'admin_leader_detail_{leader_id}'
                )])
        
        keyboard.append([InlineKeyboardButton("« Back", callback_data='admin_back')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "👨‍💼 Leaders Panel\n\n"
            f"Total Leaders: {len(LEADERS)}\n\n"
            "Select a leader to view details:",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        logger.error(f"Leaders panel error: {e}")
        import traceback
        traceback.print_exc()
        await query.edit_message_text(f"❌ Error: {str(e)}")


async def admin_leader_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show detailed stats for a specific leader"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != config.OWNER_ID:
        await query.answer("❌ Unauthorized", show_alert=True)
        return
    
    try:
        # Extract leader_id from callback data
        leader_id = int(query.data.split('_')[-1])
        
        # Get leader info
        try:
            leader_user = await context.bot.get_chat(leader_id)
            leader_name = f"@{leader_user.username}" if leader_user.username else leader_user.first_name
        except:
            leader_name = f"ID: {leader_id}"
        
        # Get real stats using the new method
        stats = TelegramSession.get_leader_stats(leader_id)
        
        total_uploaded = stats['total_uploaded']
        total_sold = stats['total_sold']
        total_revenue = stats['total_revenue']
        sold_24h = stats['sold_24h']
        revenue_24h = stats['revenue_24h']
        
        # Calculate available sessions
        available = total_uploaded - total_sold
        
        keyboard = [
            [InlineKeyboardButton("« Back to Leaders", callback_data='admin_leaders')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"👨‍💼 Leader: {leader_name}\n"
            f"🆔 ID: `{leader_id}`\n\n"
            f"📊 **All Time Stats:**\n"
            f"  📤 Total Uploaded: {total_uploaded}\n"
            f"  ✅ Total Sold: {total_sold}\n"
            f"  📦 Available: {available}\n"
            f"  💰 Total Revenue: ${total_revenue:.2f}\n\n"
            f"📅 **Last 24 Hours:**\n"
            f"  ✅ Sold: {sold_24h}\n"
            f"  💵 Revenue: ${revenue_24h:.2f}\n\n"
            f"💡 Use this info to calculate payments",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Leader details error: {e}")
        import traceback
        traceback.print_exc()
        await query.edit_message_text(f"❌ Error: {str(e)}")

async def admin_edit_info_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle edit info button from admin menu"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != config.OWNER_ID:
        await query.answer("❌ Unauthorized", show_alert=True)
        return ConversationHandler.END  # ✅ FIXED: Return proper state
    
    # Call the edit_info start function and return its state
    return await admin_edit_info_start(update, context)  # ✅ FIXED: Added return

async def admin_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start broadcast - Select target audience"""
    user_id = update.effective_user.id
    
    if user_id != config.OWNER_ID:
        # Handle both callback and message
        if update.callback_query:
            await update.callback_query.answer("❌ Admin only", show_alert=True)
        else:
            await update.message.reply_text("❌ Admin only")
        return ConversationHandler.END
    
    try:
        # Get user counts
        from database import User, Purchase
        
        total_users = User.count()
        
        # Count users with purchases
        database = get_db()
        users_with_purchases = database.purchases.distinct('user_id')
        purchase_count = len(users_with_purchases)
        
        # Count users with balance
        users_with_balance = database.users.count_documents({'balance': {'$gt': 0}})
        
        keyboard = [
            [InlineKeyboardButton(
                f"📢 All Users ({total_users})",
                callback_data='broadcast_all'
            )],
            [InlineKeyboardButton(
                f"🛒 Users with Purchases ({purchase_count})",
                callback_data='broadcast_buyers'
            )],
            [InlineKeyboardButton(
                f"💰 Users with Balance ({users_with_balance})",
                callback_data='broadcast_balance'
            )],
            [InlineKeyboardButton("❌ Cancel", callback_data='broadcast_cancel')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message_text = (
            "📢 **Broadcast Message**\n\n"
            "Select target audience:\n\n"
            "⚠️ Use responsibly - all selected users will receive your message."
        )
        
        # Handle both callback query and regular message
        if update.callback_query:
            await update.callback_query.edit_message_text(
                message_text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(
                message_text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        
        return BROADCAST_SELECT
        
    except Exception as e:
        logger.error(f"Broadcast start error: {e}")
        import traceback
        traceback.print_exc()
        
        error_text = f"❌ Error: {str(e)}"
        if update.callback_query:
            await update.callback_query.edit_message_text(error_text)
        else:
            await update.message.reply_text(error_text)
        return ConversationHandler.END


async def receive_broadcast_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive broadcast target selection"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != config.OWNER_ID:
        await query.answer("❌ Unauthorized", show_alert=True)
        return ConversationHandler.END
    
    if query.data == 'broadcast_cancel':
        await query.edit_message_text("❌ Broadcast cancelled")
        context.user_data.clear()
        return ConversationHandler.END
    
    # Store target
    target = query.data.replace('broadcast_', '')
    context.user_data['broadcast_target'] = target
    
    # Get target count
    database = get_db()
    
    if target == 'all':
        count = database.users.count_documents({})
        target_name = "All Users"
    elif target == 'buyers':
        users_with_purchases = database.purchases.distinct('user_id')
        count = len(users_with_purchases)
        target_name = "Users with Purchases"
    elif target == 'balance':
        count = database.users.count_documents({'balance': {'$gt': 0}})
        target_name = "Users with Balance"
    
    context.user_data['broadcast_count'] = count
    context.user_data['broadcast_target_name'] = target_name
    
    await query.edit_message_text(
        f"📝 **Compose Broadcast Message**\n\n"
        f"📊 Target: **{target_name}** ({count} users)\n\n"
        f"Send your message now:\n\n"
        f"**You can send:**\n"
        f"• Text message\n"
        f"• Photo with caption\n"
        f"• Message with Markdown formatting\n\n"
        f"**Tips:**\n"
        f"• Keep it concise\n"
        f"• Use **bold** and *italic*\n"
        f"• Add important info only\n\n"
        f"Send /cancel to abort.",
        parse_mode='Markdown'
    )
    
    return BROADCAST_MESSAGE


async def receive_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive broadcast message content"""
    user_id = update.effective_user.id
    
    if user_id != config.OWNER_ID:
        await update.message.reply_text("❌ Unauthorized")
        return ConversationHandler.END
    
    # Store message data
    if update.message.photo:
        # Photo message
        context.user_data['broadcast_type'] = 'photo'
        context.user_data['broadcast_photo'] = update.message.photo[-1].file_id
        context.user_data['broadcast_text'] = update.message.caption or ""
    elif update.message.text:
        # Text message
        context.user_data['broadcast_type'] = 'text'
        context.user_data['broadcast_text'] = update.message.text
    else:
        await update.message.reply_text(
            "❌ Unsupported message type.\n\n"
            "Please send text or photo only."
        )
        return BROADCAST_MESSAGE
    
    target_name = context.user_data.get('broadcast_target_name')
    count = context.user_data.get('broadcast_count')
    
    # Show preview
    keyboard = [
        [InlineKeyboardButton("✅ Send Now", callback_data='broadcast_send')],
        [InlineKeyboardButton("❌ Cancel", callback_data='broadcast_cancel')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    preview_text = (
        f"📋 **Broadcast Preview**\n\n"
        f"📊 Target: {target_name}\n"
        f"👥 Recipients: {count} users\n"
        f"📝 Type: {context.user_data['broadcast_type'].title()}\n\n"
        f"**Your message:**\n"
        f"{'(See above)' if context.user_data['broadcast_type'] == 'photo' else ''}\n\n"
        f"⚠️ Ready to send?"
    )
    
    if context.user_data['broadcast_type'] == 'photo':
        await update.message.reply_photo(
            photo=context.user_data['broadcast_photo'],
            caption=preview_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            preview_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    return BROADCAST_CONFIRM


async def confirm_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm and send broadcast"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != config.OWNER_ID:
        await query.answer("❌ Unauthorized", show_alert=True)
        return ConversationHandler.END
    
    if query.data == 'broadcast_cancel':
        await query.edit_message_text("❌ Broadcast cancelled")
        context.user_data.clear()
        return ConversationHandler.END
    
    # Get broadcast data
    target = context.user_data.get('broadcast_target')
    broadcast_type = context.user_data.get('broadcast_type')
    broadcast_text = context.user_data.get('broadcast_text')
    broadcast_photo = context.user_data.get('broadcast_photo')
    
    await query.edit_message_text("⏳ Sending broadcast...\n\nThis may take a while.")
    
    try:
        database = get_db()
        
        # Get target users
        if target == 'all':
            users = list(database.users.find({}, {'telegram_id': 1}))
        elif target == 'buyers':
            user_ids = database.purchases.distinct('user_id')
            users = list(database.users.find({'telegram_id': {'$in': user_ids}}, {'telegram_id': 1}))
        elif target == 'balance':
            users = list(database.users.find({'balance': {'$gt': 0}}, {'telegram_id': 1}))
        else:
            await query.edit_message_text("❌ Invalid target")
            return ConversationHandler.END
        
        total = len(users)
        success = 0
        failed = 0
        blocked = 0
        
        # Progress tracking
        progress_msg = await context.bot.send_message(
            query.from_user.id,
            f"📤 Sending: 0/{total}\n✅ Success: 0\n❌ Failed: 0"
        )
        
        # Send to each user
        for idx, user in enumerate(users):
            user_id = user['telegram_id']
            
            try:
                if broadcast_type == 'photo':
                    await context.bot.send_photo(
                        chat_id=user_id,
                        photo=broadcast_photo,
                        caption=broadcast_text,
                        parse_mode='Markdown'
                    )
                else:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=broadcast_text,
                        parse_mode='Markdown'
                    )
                
                success += 1
                
            except Exception as e:
                error_str = str(e).lower()
                if 'blocked' in error_str or 'deactivated' in error_str:
                    blocked += 1
                else:
                    failed += 1
                logger.debug(f"Failed to send to {user_id}: {e}")
            
            # Update progress every 10 users
            if (idx + 1) % 10 == 0 or (idx + 1) == total:
                try:
                    await progress_msg.edit_text(
                        f"📤 Sending: {idx + 1}/{total}\n"
                        f"✅ Success: {success}\n"
                        f"❌ Failed: {failed}\n"
                        f"🚫 Blocked: {blocked}"
                    )
                except:
                    pass
            
            # Small delay to avoid flood limits
            await asyncio.sleep(0.05)
        
        # Final summary
        summary = (
            f"✅ **Broadcast Complete!**\n\n"
            f"📊 **Statistics:**\n"
            f"👥 Total: {total}\n"
            f"✅ Delivered: {success}\n"
            f"❌ Failed: {failed}\n"
            f"🚫 Blocked: {blocked}\n\n"
            f"📈 Success Rate: {(success/total*100):.1f}%"
        )
        
        await context.bot.send_message(
            query.from_user.id,
            summary,
            parse_mode='Markdown'
        )
        
        logger.info(f"✅ Broadcast sent by admin: {success}/{total} delivered")
        
    except Exception as e:
        logger.error(f"Broadcast error: {e}")
        import traceback
        traceback.print_exc()
        await context.bot.send_message(
            query.from_user.id,
            f"❌ Broadcast error: {str(e)}"
        )
    
    finally:
        context.user_data.clear()
    
    return ConversationHandler.END

async def admin_broadcast_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle broadcast button from admin menu"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != config.OWNER_ID:
        await query.answer("❌ Unauthorized", show_alert=True)
        return ConversationHandler.END  # ✅ FIXED: Return proper state
    
    # Simply call broadcast start with the update object and return its state
    return await admin_broadcast_start(update, context)  # ✅ FIXED: Added return

async def quick_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Quick broadcast to all users
    Usage: /quickcast Your message here
    """
    user_id = update.effective_user.id
    
    if user_id != config.OWNER_ID:
        await update.message.reply_text("❌ Admin only")
        return
    
    if not context.args:
        await update.message.reply_text(
            "📢 **Quick Broadcast**\n\n"
            "Usage: `/quickcast Your message here`\n\n"
            "This sends to ALL users immediately.\n"
            "Use /broadcast for more control.",
            parse_mode='Markdown'
        )
        return
    
    message = ' '.join(context.args)
    
    await update.message.reply_text("⏳ Broadcasting to all users...")
    
    try:
        from database import User
        
        users = User.get_all(limit=10000)
        total = len(users)
        success = 0
        failed = 0
        
        for user in users:
            try:
                await context.bot.send_message(
                    chat_id=user['telegram_id'],
                    text=message,
                    parse_mode='Markdown'
                )
                success += 1
            except Exception as e:
                failed += 1
                logger.debug(f"Failed to send to {user['telegram_id']}: {e}")
            
            await asyncio.sleep(0.05)
        
        await update.message.reply_text(
            f"✅ Broadcast Complete!\n\n"
            f"✅ Delivered: {success}/{total}\n"
            f"❌ Failed: {failed}"
        )
        
    except Exception as e:
        logger.error(f"Quick broadcast error: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")

# ============= SINGLE SESSION UPLOAD =============

async def admin_upload_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start single session upload"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != config.OWNER_ID:
        await query.answer("❌ Unauthorized", show_alert=True)
        return
    
    await query.edit_message_text(
        "📤 Upload Single Session\n\n"
        "Please send me ONE .session file.\n\n"
        "Send /cancel to abort."
    )
    
    return UPLOAD_SESSION

async def receive_session_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Receive session file - WITH AUTO INFO EXTRACTION"""
    user_id = update.effective_user.id
    
    if user_id != config.OWNER_ID:
        await update.message.reply_text("❌ Admin only")
        return ConversationHandler.END
    
    document = update.message.document
    
    if not document or not document.file_name.endswith('.session'):
        await update.message.reply_text("❌ Please send a .session file")
        return UPLOAD_SESSION
    
    try:
        # Download session file
        file = await context.bot.get_file(document.file_id)
        file_path = get_temp_path(document.file_name)
        await file.download_to_drive(file_path)
        
        session_name = document.file_name.replace('.session', '')
        
        await update.message.reply_text("⏳ Validating session and extracting info...")
        
        # Use absolute path
        import os
        abs_file_path = os.path.abspath(file_path)
        abs_session_name = abs_file_path.replace('.session', '')
        
        client = None
        try:
            from telethon.tl.functions.users import GetFullUserRequest
            
            client = TelegramClient(
                abs_session_name,
                config.TELEGRAM_API_ID,
                config.TELEGRAM_API_HASH,
                system_version="4.16.30-vxCUSTOM",
                connection_retries=2,
                retry_delay=1,
                timeout=10
            )
            
            await asyncio.wait_for(client.connect(), timeout=15.0)
            
            is_authorized = await asyncio.wait_for(
                client.is_user_authorized(), 
                timeout=10.0
            )
            
            if not is_authorized:
                cleanup_temp_files(abs_file_path, abs_session_name)
                await update.message.reply_text(
                    "❌ Invalid session - Not authorized\n\n"
                    "Possible reasons:\n"
                    "• Session is logged out\n"
                    "• Session has expired\n"
                    "• File is corrupted"
                )
                if client.is_connected():
                    await client.disconnect()
                return ConversationHandler.END
            
            # Get account info
            me = await asyncio.wait_for(client.get_me(), timeout=10.0)
            phone = me.phone if me.phone else "Unknown"
            
            # ✅ AUTO-EXTRACT SESSION INFO
            auto_info = await extract_session_info(client)
            
            # ✅ CHECK ACCOUNT WITH SPAMBOT
            spam_check = await check_account_with_spambot(client, phone)
            
            # Disconnect after checking
            if client.is_connected():
                await client.disconnect()
            
            # ✅ UPLOAD TO STORAGE CHANNEL
            from telegram import Bot
            bot = Bot(token=config.BOT_TOKEN)
            
            # Build caption with info
            caption_parts = [f"📱 Phone: {phone}"]
            
            # Add spam status
            status_emoji = {
                'Free': '🟢',
                'Frozen': '🔴',
                'Spam': '🟡',
                'Unknown': '❓',
                'Error': '❌'
            }
            emoji = status_emoji.get(spam_check['status'], '❓')
            caption_parts.append(f"{emoji} Status: {spam_check['status']}")
            
            # Add auto-extracted info
            if auto_info:
                caption_parts.append(f"ℹ️ {auto_info}")
            
            caption_parts.append(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}")
            
            with open(abs_file_path, 'rb') as f:
                channel_message = await bot.send_document(
                    chat_id=config.STORAGE_CHANNEL_ID,
                    document=f,
                    filename=document.file_name,
                    caption="\n".join(caption_parts)
                )
            
            message_id = channel_message.message_id
            
            cleanup_temp_files(abs_file_path, abs_session_name)
            
            # Store session info with auto-extracted data
            context.user_data['session_data'] = {
                'message_id': message_id,
                'phone': phone,
                'has_2fa': False,
                'spam_status': spam_check['status'],
                'auto_info': auto_info  # ✅ STORE AUTO INFO
            }
            
            # Show result with info
            warning_text = ""
            if spam_check['status'] in ['Frozen', 'Spam']:
                warning_text = "\n⚠️ **WARNING: This account has restrictions!**\n"
            elif spam_check['status'] == 'Free':
                warning_text = "\n✅ **Account looks clean and healthy!**\n"
            
            info_display = f"\n💡 **Auto-detected:** {auto_info}\n" if auto_info else ""
            
            await update.message.reply_text(
                f"✅ **Session Validated**\n\n"
                f"📱 Phone: `{phone}`\n"
                f"{emoji} **Status:** {spam_check['message']}\n"
                f"{warning_text}"
                f"{info_display}\n"
                f"Enter country code (e.g., Nigeria, USA, India):",
                parse_mode='Markdown'
            )
            
            return GET_COUNTRY
            
        except asyncio.TimeoutError:
            logger.error("Connection timeout")
            await update.message.reply_text(
                "❌ Connection timeout\n\n"
                "Try again in a few minutes."
            )
            if client and client.is_connected():
                await client.disconnect()
            cleanup_temp_files(abs_file_path, abs_session_name if 'abs_session_name' in locals() else file_path)
            return ConversationHandler.END
            
        except Exception as e:
            logger.error(f"Validation error: {e}")
            import traceback
            traceback.print_exc()
            await update.message.reply_text(
                f"❌ Validation Error\n\n"
                f"Error: {str(e)}"
            )
            if client and client.is_connected():
                await client.disconnect()
            cleanup_temp_files(abs_file_path, abs_session_name if 'abs_session_name' in locals() else file_path)
            return ConversationHandler.END
        
    except Exception as e:
        logger.error(f"Session file error: {e}")
        import traceback
        traceback.print_exc()
        await update.message.reply_text(f"❌ Error processing file: {str(e)}")
        return ConversationHandler.END
    
# ============= BULK SESSION UPLOAD =============

async def admin_bulk_upload_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start bulk session upload"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != config.OWNER_ID:
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
    return UPLOAD_BULK

async def receive_bulk_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive multiple session files or ZIP - WITH REAL-TIME SPAM STATUS"""
    if update.effective_user.id != config.OWNER_ID:
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
                return UPLOAD_BULK
            
            session_count = len(context.user_data['bulk_sessions'])
            await update.message.reply_text(
                f"✅ Loaded {session_count} sessions!\n\n"
                f"Now, enter the country name for ALL sessions:\n"
                f"Examples: Indo, USA, UK, Russia, Other"
            )
            return GET_COUNTRY
        
        else:
            await update.message.reply_text(
                "📦 Bulk Upload Mode\n\n"
                "📤 Send .session files or .zip file\n"
                "✅ When finished, type: /done\n\n"
                f"Current: {len(context.user_data.get('bulk_sessions', []))} sessions loaded"
            )
            return UPLOAD_BULK
    
    if not update.message.document:
        await update.message.reply_text("❌ Please send a .session file or .zip file!")
        return UPLOAD_BULK
    
    file = update.message.document
    file_name = file.file_name
    
    status_msg = await update.message.reply_text(f"⏳ Processing {file_name}...")
    
    try:
        new_file = await context.bot.get_file(file.file_id)
        
        # ============================================
        # Handle ZIP file
        # ============================================
        if file_name.endswith('.zip'):
            import zipfile
            import io
            
            zip_bytes = await new_file.download_as_bytearray()
            zip_file = zipfile.ZipFile(io.BytesIO(zip_bytes))
            
            processed = 0
            failed = 0
            failed_files = []
            
            # Track spam status
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
                await status_msg.edit_text(
                    "❌ No .session files found in ZIP!\n\n"
                    "Make sure your ZIP contains .session files."
                )
                return UPLOAD_BULK
            
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
                    
                    # ✅ CRITICAL: Send individual status message
                    processing_msg = await context.bot.send_message(
                        update.effective_user.id,
                        f"⏳ Processing {idx}/{total_files}: Checking session..."
                    )
                    
                    result = await process_single_session_bulk(temp_path, update.effective_user.id, context.bot)
                    
                    if result:
                        context.user_data['bulk_sessions'].append(result)
                        processed += 1
                        
                        # Get spam status
                        spam_status = result.get('spam_status', 'Unknown')
                        phone = result.get('phone', 'Unknown')
                        
                        # Count spam status
                        if spam_status in spam_stats:
                            spam_stats[spam_status] += 1
                        
                        # ✅ REAL-TIME STATUS MESSAGE WITH EMOJI
                        status_emoji = {
                            'Free': '🟢 CLEAN',
                            'Frozen': '🔴 FROZEN',
                            'Spam': '🟡 SPAM LIMITED',
                            'Unknown': '❓ UNKNOWN',
                            'Error': '❌ CHECK ERROR'
                        }
                        
                        status_text = status_emoji.get(spam_status, f'❓ {spam_status}')
                        
                        # Edit the processing message with result
                        await processing_msg.edit_text(
                            f"✅ {idx}/{total_files} Processed\n\n"
                            f"📱 Phone: {phone}\n"
                            f"📊 Status: {status_text}\n\n"
                            f"Progress: {processed} success, {failed} failed"
                        )
                        
                        logger.info(f"✅ {idx}/{total_files}: {phone} ({spam_status})")
                    else:
                        failed += 1
                        failed_files.append(os.path.basename(zip_info))
                        
                        # Show failure message
                        await processing_msg.edit_text(
                            f"❌ {idx}/{total_files} Failed\n\n"
                            f"File: {os.path.basename(zip_info)}\n"
                            f"Reason: Invalid or expired session\n\n"
                            f"Progress: {processed} success, {failed} failed"
                        )
                        
                        logger.warning(f"❌ Failed {idx}/{total_files}: {os.path.basename(zip_info)}")
                    
                    # Clean up temp file
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                    
                    # Small delay to avoid flooding
                    await asyncio.sleep(0.3)
                    
                except Exception as e:
                    logger.error(f"Error processing {zip_info}: {e}")
                    failed += 1
                    failed_files.append(os.path.basename(zip_info))
                    continue
            
            # ============================================
            # Final summary with spam stats
            # ============================================
            summary = (
                f"✅ **ZIP Processing Complete!**\n\n"
                f"📊 **Results:**\n"
                f"✅ Successful: {processed}/{total_files}\n"
                f"❌ Failed: {failed}/{total_files}\n\n"
            )
            
            # Add spam statistics
            if processed > 0:
                summary += "📊 **Account Status:**\n"
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
        
        # ============================================
        # Handle single .session file
        # ============================================
        elif file_name.endswith('.session'):
            temp_path = get_temp_path(f"single_{int(time.time())}_{file_name}")
            await new_file.download_to_drive(temp_path)
            
            # Show checking message
            await status_msg.edit_text(f"⏳ Checking {file_name}...")
            
            result = await process_single_session_bulk(temp_path, update.effective_user.id, context.bot)
            
            if result:
                context.user_data['bulk_sessions'].append(result)
                
                # Get spam status
                spam_status = result.get('spam_status', 'Unknown')
                phone = result.get('phone', 'Unknown')
                
                # Status emoji
                status_emoji = {
                    'Free': '🟢 CLEAN',
                    'Frozen': '🔴 FROZEN',
                    'Spam': '🟡 SPAM LIMITED',
                    'Unknown': '❓ UNKNOWN',
                    'Error': '❌ CHECK ERROR'
                }
                
                status_text = status_emoji.get(spam_status, f'❓ {spam_status}')
                
                await status_msg.edit_text(
                    f"✅ **Session Added**\n\n"
                    f"📱 Phone: {phone}\n"
                    f"📊 Status: {status_text}\n\n"
                    f"Total: {len(context.user_data['bulk_sessions'])} sessions\n\n"
                    f"📤 Send more files or type /done to continue.",
                    parse_mode='Markdown'
                )
            else:
                await status_msg.edit_text(
                    f"❌ **Failed to process** {file_name}\n\n"
                    f"Possible reasons:\n"
                    f"• Session not authorized\n"
                    f"• Invalid session file\n"
                    f"• Connection timeout\n\n"
                    f"Total loaded: {len(context.user_data.get('bulk_sessions', []))} sessions\n\n"
                    f"📤 Send more files or type /done to continue.",
                    parse_mode='Markdown'
                )
            
            # Clean up
            if os.path.exists(temp_path):
                os.remove(temp_path)
        
        else:
            await status_msg.edit_text(
                "❌ File must be .session or .zip!\n\n"
                f"Current: {len(context.user_data.get('bulk_sessions', []))} sessions loaded\n\n"
                f"📤 Send more files or type /done"
            )
        
        return UPLOAD_BULK
        
    except Exception as e:
        logger.error(f"Error in bulk upload: {e}")
        import traceback
        traceback.print_exc()
        await status_msg.edit_text(
            f"❌ Error processing file\n\n"
            f"Error: {str(e)}\n\n"
            f"Please try again or contact support."
        )
        return UPLOAD_BULK  


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


async def receive_country(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive country name"""
    if update.effective_user.id != config.OWNER_ID:
        await update.message.reply_text("❌ Unauthorized")
        return ConversationHandler.END
    
    country = update.message.text.strip()
    
    if not country or len(country) < 2:
        await update.message.reply_text("❌ Please enter a valid country name.")
        return GET_COUNTRY
    
    # Store country for all sessions
    if 'bulk_sessions' in context.user_data:
        for session in context.user_data['bulk_sessions']:
            session['country'] = country
    else:
        context.user_data['session_data']['country'] = country
    
    await update.message.reply_text(
        f"✅ Country set to: {country}\n\n"
        f"Now, enter the price (in USD):\n"
        f"Examples: 1.0, 0.8, 2.5\n\n"
        f"Send /cancel to abort."
    )
    
    return GET_PRICE

async def receive_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive price"""
    if update.effective_user.id != config.OWNER_ID:
        await update.message.reply_text("❌ Unauthorized")
        return ConversationHandler.END
    
    try:
        price = float(update.message.text.strip())
        
        if price <= 0:
            await update.message.reply_text("❌ Price must be greater than 0.")
            return GET_PRICE
        
        # Store price for all sessions
        if 'bulk_sessions' in context.user_data:
            for session in context.user_data['bulk_sessions']:
                session['price'] = price
        else:
            context.user_data['session_data']['price'] = price
        
        await update.message.reply_text(
            f"✅ Price set to: ${price:.2f}\n\n"
            f"Do these accounts have 2FA (cloud password)?\n\n"
            f"Send the 2FA password, or type 'no' if they don't have 2FA.\n\n"
            f"Send /cancel to abort."
        )
        
        return GET_2FA
        
    except ValueError:
        await update.message.reply_text("❌ Invalid price. Please enter a number (e.g., 1.0)")
        return GET_PRICE

async def receive_2fa_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive 2FA password or 'no' - THEN ASK FOR INFO"""
    if update.effective_user.id != config.OWNER_ID:
        await update.message.reply_text("❌ Unauthorized")
        return ConversationHandler.END
    
    password_input = update.message.text.strip()
    
    # Check if user said no
    if password_input.lower() in ['no', 'nil', 'none', 'n']:
        has_2fa = False
        two_fa_password = None
    else:
        has_2fa = True
        two_fa_password = password_input
    
    # Store 2FA for all sessions
    if 'bulk_sessions' in context.user_data:
        for session in context.user_data['bulk_sessions']:
            session['has_2fa'] = has_2fa
            session['two_fa_password'] = two_fa_password
    else:
        context.user_data['session_data']['has_2fa'] = has_2fa
        context.user_data['session_data']['two_fa_password'] = two_fa_password
    
    # ✅ IMPROVED - CLEARER OPTIONAL MESSAGE
    await update.message.reply_text(
        "📝 **Session Info** (OPTIONAL - You can skip)\n\n"
        "Add extra details buyers will see:\n\n"
        "**Examples:**\n"
        "• `1yr old account`\n"
        "• `DC 4 account`\n"
        "• `Premium account`\n"
        "• `Verified + username`\n"
        "• `6 months old, DC 3`\n\n"
        "✅ **Type 'skip' to skip this (recommended if no special info)**\n"
        "✅ Or enter custom info\n\n"
        "Send /cancel to abort.",
        parse_mode='Markdown'
    )
    
    return GET_INFO

async def receive_session_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive optional session info - WITH AUTO INFO"""
    if update.effective_user.id != config.OWNER_ID:
        await update.message.reply_text("❌ Unauthorized")
        return ConversationHandler.END
    
    info_input = update.message.text.strip()
    
    # Get auto-extracted info
    auto_info = context.user_data.get('session_data', {}).get('auto_info')
    
    # Check if user wants to use auto info or skip
    if info_input.lower() in ['auto', 'yes', 'y']:
        # Use auto-extracted info
        info = auto_info
        if info:
            info_display = f"✅ Using auto-detected: {info}"
        else:
            info = None
            info_display = "❌ No auto info available"
    elif info_input.lower() in ['no', 'nil', 'none', 'n', 'skip', 'na']:
        # Skip info
        info = None
        info_display = "❌ No extra info"
    else:
        # Use custom input
        info = info_input[:100]
        info_display = f"✅ Custom info: {info}"
    
    # Store info for all sessions
    if 'bulk_sessions' in context.user_data:
        for session in context.user_data['bulk_sessions']:
            # Use session's own auto_info if available, otherwise use custom
            if info_input.lower() in ['auto', 'yes', 'y']:
                session['info'] = session.get('auto_info') or info
            else:
                session['info'] = info
        
        keyboard = [
            [InlineKeyboardButton("✅ Confirm", callback_data='admin_confirm_yes')],
            [InlineKeyboardButton("❌ Cancel", callback_data='admin_confirm_no')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        first_session = context.user_data['bulk_sessions'][0]
        
        summary = (
            f"📋 Bulk Upload Summary:\n\n"
            f"📦 Total Sessions: {len(context.user_data['bulk_sessions'])}\n"
            f"🌍 Country: {first_session['country']}\n"
            f"💰 Price (each): ${first_session['price']:.2f}\n"
            f"🔐 2FA: {'Yes' if first_session['has_2fa'] else 'No'}\n"
        )
        
        if first_session['has_2fa']:
            summary += f"🔑 Password: {first_session['two_fa_password']}\n"
        
        summary += f"{info_display}\n"
        summary += f"\nConfirm to add all {len(context.user_data['bulk_sessions'])} sessions?"
        
        await update.message.reply_text(summary, reply_markup=reply_markup)
    else:
        context.user_data['session_data']['info'] = info
        
        keyboard = [
            [InlineKeyboardButton("✅ Confirm", callback_data='admin_confirm_yes')],
            [InlineKeyboardButton("❌ Cancel", callback_data='admin_confirm_no')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        session_data = context.user_data['session_data']
        
        summary = (
            "📋 Final Session Details:\n\n"
            f"📱 Phone: {session_data['phone']}\n"
            f"🌍 Country: {session_data['country']}\n"
            f"💰 Price: ${session_data['price']:.2f}\n"
            f"🔐 2FA: {'Yes' if session_data['has_2fa'] else 'No'}\n"
        )
        
        if session_data['has_2fa']:
            summary += f"🔑 Password: {session_data['two_fa_password']}\n"
        
        summary += f"{info_display}\n"
        summary += "\nConfirm to add this session to database?"
        
        await update.message.reply_text(summary, reply_markup=reply_markup)
    
    return CONFIRM_DETAILS

async def confirm_session_upload(update, context):
    """Save sessions to MongoDB - WITH SPAM STATUS STORAGE"""
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
                    # ✅ FIX: Add spam_status to database
                    TelegramSession.create(
                        session_string=str(session_data['message_id']),
                        phone_number=session_data['phone'],
                        country=session_data['country'],
                        has_2fa=session_data['has_2fa'],
                        two_fa_password=session_data.get('two_fa_password'),
                        price=session_data['price'],
                        info=session_data.get('info'),
                        spam_status=session_data.get('spam_status', 'Unknown')  # ✅ ADD THIS
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
                # ✅ FIX: Add spam_status to database
                TelegramSession.create(
                    session_string=str(session_data['message_id']),
                    phone_number=session_data['phone'],
                    country=session_data['country'],
                    has_2fa=session_data['has_2fa'],
                    two_fa_password=session_data.get('two_fa_password'),
                    price=session_data['price'],
                    info=session_data.get('info'),
                    spam_status=session_data.get('spam_status', 'Unknown')  # ✅ ADD THIS
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


async def manual_credit_ton(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually credit a TON payment"""
    try:
        # Parse: /credit_ton <transaction_id> <tx_hash>
        parts = context.args
        if len(parts) < 2:
            await update.message.reply_text("Usage: /credit_ton <transaction_id> <tx_hash>")
            return
        
        transaction_id = int(parts[0])
        tx_hash = parts[1]
        
        db = get_db()
        try:
            tx = db.query(Transaction).filter_by(id=transaction_id).first()
            if not tx:
                await update.message.reply_text("❌ Transaction not found")
                return
            
            if tx.status == 'completed':
                await update.message.reply_text("⚠️ Already credited")
                return
            
            # Credit user
            user = db.query(User).filter_by(telegram_id=tx.user_id).first()
            user.balance += tx.amount
            
            # Update transaction
            tx.status = 'completed'
            tx.charge_id = tx_hash
            tx.updated_at = datetime.utcnow()
            
            db.commit()
            
            await update.message.reply_text(
                f"✅ Credited ${tx.amount} to user {tx.user_id}\\n"
                f"TX Hash: {tx_hash}"
            )
            
            # Notify user
            await context.bot.send_message(
                chat_id=tx.user_id,
                text=f"✅ Payment verified!\\n💰 ${tx.amount} added to your balance"
            )
        finally:
            db.close()
            
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def list_pending_ton(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    List all pending TON payments
    Usage: /pending_ton
    """
    # Admin check
    if update.effective_user.id not in config.ADMIN_IDS:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    db = get_db()
    try:
        # Get pending TON transactions from last 48 hours
        time_limit = datetime.utcnow() - timedelta(hours=48)
        
        pending = db.query(Transaction).filter(
            Transaction.payment_method == 'ton',
            Transaction.status == 'pending',
            Transaction.created_at >= time_limit
        ).order_by(Transaction.created_at.desc()).all()
        
        if not pending:
            await update.message.reply_text("✅ No pending TON payments")
            return
        
        message = f"⏳ Pending TON Payments ({len(pending)}):\n\n"
        
        for tx in pending:
            age = datetime.utcnow() - tx.created_at
            age_str = f"{int(age.total_seconds() / 60)}m ago" if age.total_seconds() < 3600 else f"{int(age.total_seconds() / 3600)}h ago"
            
            message += (
                f"🆔 ID: {tx.id}\n"
                f"👤 User: {tx.user_id}\n"
                f"💰 Amount: ${tx.amount}\n"
                f"📝 Memo: `{tx.payment_id}`\n"
                f"⏰ Created: {age_str}\n"
                f"━━━━━━━━━━━━━━━━\n"
            )
        
        message += "\nℹ️ Use /credit_ton <tx_id> <tx_hash> to manually credit"
        
        await update.message.reply_text(message, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Error listing pending TON: {e}")
        await update.message.reply_text(f"❌ Error: {e}")
    finally:
        db.close()

async def credit_ton_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Manually credit a TON payment
    Usage: /credit_ton <transaction_id> <tx_hash>
    
    Example: /credit_ton 12 EQxxxxxxxxxxxxxx
    """
    # Admin check
    if update.effective_user.id not in config.ADMIN_IDS:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    try:
        # Parse arguments
        if len(context.args) < 1:
            await update.message.reply_text(
                "❌ Usage: /credit_ton <transaction_id> [tx_hash]\n\n"
                "Example: /credit_ton 12 EQxxxxxx\n"
                "TX hash is optional"
            )
            return
        
        transaction_id = int(context.args[0])
        tx_hash = context.args[1] if len(context.args) > 1 else "MANUAL_VERIFICATION"
        
        db = get_db()
        try:
            # Get transaction
            tx = db.query(Transaction).filter_by(id=transaction_id).first()
            
            if not tx:
                await update.message.reply_text("❌ Transaction not found")
                return
            
            if tx.payment_method != 'ton':
                await update.message.reply_text("❌ Not a TON transaction")
                return
            
            if tx.status == 'completed':
                await update.message.reply_text(
                    f"⚠️ Already credited!\n\n"
                    f"User: {tx.user_id}\n"
                    f"Amount: ${tx.amount}\n"
                    f"Credited at: {tx.updated_at}"
                )
                return
            
            # Get user
            user = db.query(User).filter_by(telegram_id=tx.user_id).first()
            
            if not user:
                await update.message.reply_text("❌ User not found")
                return
            
            old_balance = user.balance
            
            # Credit user
            user.balance += tx.amount
            
            # Update transaction
            tx.status = 'completed'
            tx.charge_id = tx_hash
            tx.updated_at = datetime.utcnow()
            
            db.commit()
            
            await update.message.reply_text(
                f"✅ Payment Credited!\n\n"
                f"👤 User: {tx.user_id}\n"
                f"💰 Amount: ${tx.amount}\n"
                f"📊 Balance: ${old_balance:.2f} → ${user.balance:.2f}\n"
                f"🔗 TX: {tx_hash[:20]}...\n\n"
                f"User will be notified."
            )
            
            # Notify user
            try:
                await context.bot.send_message(
                    chat_id=tx.user_id,
                    text=(
                        "✅ Payment Verified!\n\n"
                        f"💰 ${tx.amount} has been added to your balance.\n"
                        f"💳 New Balance: ${user.balance:.2f}\n\n"
                        "Thank you for your payment!"
                    )
                )
            except Exception as e:
                logger.error(f"Could not notify user {tx.user_id}: {e}")
                await update.message.reply_text(f"⚠️ Could not notify user: {e}")
            
        finally:
            db.close()
            
    except ValueError:
        await update.message.reply_text("❌ Invalid transaction ID (must be a number)")
    except Exception as e:
        logger.error(f"Error crediting TON payment: {e}")
        await update.message.reply_text(f"❌ Error: {e}")

async def check_ton_transaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Check TON transaction status
    Usage: /check_ton <transaction_id>
    """
    # Admin check
    if update.effective_user.id not in config.ADMIN_IDS:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    try:
        if len(context.args) < 1:
            await update.message.reply_text("Usage: /check_ton <transaction_id>")
            return
        
        transaction_id = int(context.args[0])
        
        db = get_db()
        try:
            tx = db.query(Transaction).filter_by(id=transaction_id).first()
            
            if not tx:
                await update.message.reply_text("❌ Transaction not found")
                return
            
            # Get user
            user = db.query(User).filter_by(telegram_id=tx.user_id).first()
            
            status_emoji = {
                'pending': '⏳',
                'completed': '✅',
                'failed': '❌',
                'cancelled': '🚫'
            }.get(tx.status, '❓')
            
            message = (
                f"🔍 Transaction Details\n\n"
                f"🆔 ID: {tx.id}\n"
                f"👤 User: {tx.user_id} ({user.username if user else 'Unknown'})\n"
                f"💰 Amount: ${tx.amount}\n"
                f"📝 Memo: `{tx.payment_id}`\n"
                f"{status_emoji} Status: {tx.status}\n"
                f"⏰ Created: {tx.created_at}\n"
            )
            
            if tx.charge_id:
                message += f"🔗 TX Hash: {tx.charge_id[:30]}...\n"
            
            if tx.status == 'completed':
                message += f"✅ Completed: {tx.updated_at}\n"
            
            if tx.status == 'pending':
                keyboard = [[
                    InlineKeyboardButton(
                        "✅ Credit Now",
                        callback_data=f'admin_credit_ton_{tx.id}'
                    )
                ]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.message.reply_text(message, parse_mode='Markdown', reply_markup=reply_markup)
            else:
                await update.message.reply_text(message, parse_mode='Markdown')
                
        finally:
            db.close()
            
    except ValueError:
        await update.message.reply_text("❌ Invalid transaction ID")
    except Exception as e:
        logger.error(f"Error checking TON transaction: {e}")
        await update.message.reply_text(f"❌ Error: {e}")

async def search_ton_by_memo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Search for transaction by memo
    Usage: /search_memo <memo>
    """
    # Admin check
    if update.effective_user.id not in config.ADMIN_IDS:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    try:
        if len(context.args) < 1:
            await update.message.reply_text("Usage: /search_memo <memo>")
            return
        
        memo = ' '.join(context.args)
        
        db = get_db()
        try:
            # Search for transactions with this memo
            transactions = db.query(Transaction).filter(
                Transaction.payment_method == 'ton',
                Transaction.payment_id.like(f'%{memo}%')
            ).all()
            
            if not transactions:
                await update.message.reply_text(f"❌ No transactions found with memo: {memo}")
                return
            
            message = f"🔍 Found {len(transactions)} transaction(s):\n\n"
            
            for tx in transactions:
                status_emoji = {
                    'pending': '⏳',
                    'completed': '✅',
                    'failed': '❌'
                }.get(tx.status, '❓')
                
                message += (
                    f"{status_emoji} ID: {tx.id}\n"
                    f"User: {tx.user_id}\n"
                    f"Amount: ${tx.amount}\n"
                    f"Status: {tx.status}\n"
                    f"Memo: `{tx.payment_id}`\n"
                    f"━━━━━━━━━━━━━━━━\n"
                )
            
            await update.message.reply_text(message, parse_mode='Markdown')
            
        finally:
            db.close()
            
    except Exception as e:
        logger.error(f"Error searching memo: {e}")
        await update.message.reply_text(f"❌ Error: {e}")

async def ton_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Show TON payment statistics
    Usage: /ton_stats
    """
    # Admin check
    if update.effective_user.id not in config.ADMIN_IDS:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    db = get_db()
    try:
        # Get stats
        total_ton = db.query(Transaction).filter_by(payment_method='ton').count()
        pending_ton = db.query(Transaction).filter_by(payment_method='ton', status='pending').count()
        completed_ton = db.query(Transaction).filter_by(payment_method='ton', status='completed').count()
        
        # Total amount
        from sqlalchemy import func
        total_amount = db.query(func.sum(Transaction.amount)).filter_by(
            payment_method='ton',
            status='completed'
        ).scalar() or 0
        
        # Today's transactions
        today = datetime.utcnow().date()
        today_tx = db.query(Transaction).filter(
            Transaction.payment_method == 'ton',
            Transaction.created_at >= datetime.combine(today, datetime.min.time())
        ).count()
        
        message = (
            "📊 TON Payment Statistics\n\n"
            f"💎 Total Transactions: {total_ton}\n"
            f"✅ Completed: {completed_ton}\n"
            f"⏳ Pending: {pending_ton}\n"
            f"💰 Total Volume: ${total_amount:.2f}\n"
            f"📅 Today: {today_tx} transactions\n"
        )
        
        if pending_ton > 0:
            message += f"\n⚠️ {pending_ton} payments need verification!"
        
        await update.message.reply_text(message)
        
    except Exception as e:
        logger.error(f"Error getting TON stats: {e}")
        await update.message.reply_text(f"❌ Error: {e}")
    finally:
        db.close()

async def admin_edit_info_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start editing info for sessions - ADMIN ONLY"""
    user_id = update.effective_user.id
    
    if user_id != config.OWNER_ID:
        if update.callback_query:
            await update.callback_query.answer("❌ Admin only", show_alert=True)
        else:
            await update.message.reply_text("❌ Admin only")
        return ConversationHandler.END
    
    try:
        # Get available countries
        countries = TelegramSession.get_available_countries()
        
        if not countries:
            text = "❌ No available sessions to edit"
            if update.callback_query:
                await update.callback_query.edit_message_text(text)
            else:
                await update.message.reply_text(text)
            return ConversationHandler.END
        
        keyboard = []
        for country_data in countries:
            country = country_data['_id']
            count = country_data['count']
            keyboard.append([InlineKeyboardButton(
                f"🌍 {country} ({count} sessions)",
                callback_data=f'edit_info_country_{country}'
            )])
        
        keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data='admin_cancel')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        text = (
            "📝 **Edit Session Info**\n\n"
            "Select a country to edit info for ALL sessions in that country:\n\n"
            "This will update info for all available (unsold) sessions."
        )
        
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        
        return EDIT_INFO_COUNTRY
        
    except Exception as e:
        logger.error(f"Edit info start error: {e}")
        error_text = f"❌ Error: {str(e)}"
        if update.callback_query:
            await update.callback_query.edit_message_text(error_text)
        else:
            await update.message.reply_text(error_text)
        return ConversationHandler.END
    
async def receive_edit_info_country(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive country selection for editing"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != config.OWNER_ID:
        await query.answer("❌ Unauthorized", show_alert=True)
        return ConversationHandler.END
    
    if query.data == 'admin_cancel':
        await query.edit_message_text("❌ Cancelled")
        context.user_data.clear()
        return ConversationHandler.END
    
    try:
        # Extract country
        country = query.data.replace('edit_info_country_', '')
        logger.info(f"📝 Editing info for country: {country}")
        
        # Get session count
        sessions = TelegramSession.get_available_by_country(country, limit=1000)
        count = len(sessions)
        
        logger.info(f"📊 Found {count} sessions in {country}")
        
        if count == 0:
            await query.edit_message_text(f"❌ No available sessions in {country}")
            return ConversationHandler.END
        
        # Store country
        context.user_data['edit_info_country'] = country
        context.user_data['edit_info_count'] = count
        
        await query.edit_message_text(
            f"📝 **Edit Info for {country}**\n\n"
            f"📊 Will update: **{count} sessions**\n\n"
            f"Enter new info for ALL {country} sessions:\n\n"
            f"**Examples:**\n"
            f"• `1yr old account`\n"
            f"• `DC 4 account`\n"
            f"• `Premium + verified`\n\n"
            f"Or type:\n"
            f"• `clear` - Remove info from all sessions\n"
            f"• `skip` - Cancel and go back\n\n"
            f"Send /cancel to abort.",
            parse_mode='Markdown'
        )
        
        logger.info(f"✅ Waiting for info text input for {country}")
        return EDIT_INFO_TEXT
        
    except Exception as e:
        logger.error(f"❌ Error in receive_edit_info_country: {e}")
        import traceback
        traceback.print_exc()
        await query.edit_message_text(f"❌ Error: {str(e)}")
        return ConversationHandler.END

async def receive_edit_info_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive new info text and update sessions"""
    user_id = update.effective_user.id
    
    if user_id != config.OWNER_ID:
        await update.message.reply_text("❌ Unauthorized")
        return ConversationHandler.END
    
    info_input = update.message.text.strip()
    
    # Check for skip
    if info_input.lower() in ['skip', 'cancel']:
        await update.message.reply_text("❌ Cancelled - No changes made")
        context.user_data.clear()
        return ConversationHandler.END
    
    country = context.user_data.get('edit_info_country')
    
    if not country:
        await update.message.reply_text("❌ Session expired. Please start again.")
        return ConversationHandler.END
    
    await update.message.reply_text("⏳ Updating sessions...")
    
    try:
        database = get_db()
        
        # Determine new info value
        if info_input.lower() == 'clear':
            new_info = None
            info_display = "❌ Cleared (no info)"
        else:
            new_info = info_input[:100]
            info_display = f"✅ Set to: {new_info}"
        
        # Update all available sessions in this country
        result = database.sessions.update_many(
            {
                "country": country,
                "is_sold": False
            },
            {
                "$set": {"info": new_info}
            }
        )
        
        updated_count = result.modified_count
        
        await update.message.reply_text(
            f"✅ **Info Updated Successfully!**\n\n"
            f"🌍 Country: {country}\n"
            f"📊 Sessions updated: {updated_count}\n"
            f"{info_display}\n\n"
            f"Buyers will now see this info when browsing.",
            parse_mode='Markdown'
        )
        
        logger.info(f"✅ Admin {user_id} updated info for {updated_count} {country} sessions")
        
    except Exception as e:
        logger.error(f"Error updating info: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")
    
    finally:
        context.user_data.clear()
    
    return ConversationHandler.END

# Callback handler for admin buttons
async def handle_admin_ton_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin TON payment callbacks"""
    query = update.callback_query
    await query.answer()
    
    # Admin check
    if query.from_user.id not in config.ADMIN_IDS:
        await query.answer("❌ Unauthorized", show_alert=True)
        return
    
    if query.data.startswith('admin_credit_ton_'):
        transaction_id = int(query.data.split('_')[-1])
        
        # Show confirmation buttons
        keyboard = [
            [
                InlineKeyboardButton("✅ Confirm Credit", callback_data=f'admin_confirm_credit_{transaction_id}'),
                InlineKeyboardButton("❌ Cancel", callback_data='admin_cancel')
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"⚠️ Confirm Manual Credit\n\n"
            f"Transaction ID: {transaction_id}\n\n"
            f"This will credit the user's balance.\n"
            f"Are you sure?",
            reply_markup=reply_markup
        )
    
    elif query.data.startswith('admin_confirm_credit_'):
        transaction_id = int(query.data.split('_')[-1])
        
        db = get_db()
        try:
            tx = db.query(Transaction).filter_by(id=transaction_id).first()
            
            if not tx:
                await query.edit_message_text("❌ Transaction not found")
                return
            
            if tx.status == 'completed':
                await query.edit_message_text("⚠️ Already credited")
                return
            
            # Get user
            user = db.query(User).filter_by(telegram_id=tx.user_id).first()
            
            if not user:
                await query.edit_message_text("❌ User not found")
                return
            
            # Credit user
            user.balance += tx.amount
            
            # Update transaction
            tx.status = 'completed'
            tx.charge_id = f'ADMIN_CREDIT_{query.from_user.id}'
            tx.updated_at = datetime.utcnow()
            
            db.commit()
            
            await query.edit_message_text(
                f"✅ Credited Successfully!\n\n"
                f"User: {tx.user_id}\n"
                f"Amount: ${tx.amount}\n"
                f"New Balance: ${user.balance:.2f}"
            )
            
            # Notify user
            try:
                await context.bot.send_message(
                    chat_id=tx.user_id,
                    text=(
                        "✅ Payment Verified!\n\n"
                        f"💰 ${tx.amount} added to your balance.\n"
                        f"💳 Balance: ${user.balance:.2f}"
                    )
                )
            except:
                pass
                
        finally:
            db.close()
    
    elif query.data == 'admin_cancel':
        await query.edit_message_text("❌ Cancelled")

async def admin_verify_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Verify manual crypto payment"""
    user_id = update.effective_user.id
    
    if user_id != config.OWNER_ID:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    try:
        # Usage: /verify_crypto <transaction_id> <tx_hash>
        if len(context.args) < 2:
            await update.message.reply_text(
                "❌ Usage: /verify_crypto <transaction_id> <tx_hash>\n\n"
                "Example: /verify_crypto 675b123... abc123def456\n\n"
                "Get pending payments with /pending_crypto"
            )
            return
        
        transaction_id = context.args[0]
        tx_hash = context.args[1]
        
        from payment_crypto_manual import verify_crypto_payment
        
        success = verify_crypto_payment(transaction_id, tx_hash)
        
        if success:
            # Get transaction details
            from bson.objectid import ObjectId
            transaction = Transaction.get_by_id(ObjectId(transaction_id))
            user = User.get_by_telegram_id(transaction['user_id'])
            
            await update.message.reply_text(
                f"✅✅✅ CRYPTO PAYMENT VERIFIED!\n\n"
                f"Transaction: {transaction_id}\n"
                f"User: {transaction['user_id']}\n"
                f"Amount: ${transaction['amount']:.2f}\n"
                f"New Balance: ${user['balance']:.2f}\n"
                f"TX Hash: {tx_hash}"
            )
            
            # Notify user
            try:
                await context.bot.send_message(
                    chat_id=transaction['user_id'],
                    text=(
                        f"✅ **Crypto Payment Verified!**\n\n"
                        f"💰 ${transaction['amount']:.2f} added to your balance\n"
                        f"💳 New Balance: ${user['balance']:.2f}\n\n"
                        f"Thank you!"
                    ),
                    parse_mode='Markdown'
                )
            except:
                pass
        else:
            await update.message.reply_text("❌ Verification failed or already completed")
            
    except Exception as e:
        logger.error(f"Verify crypto error: {e}")
        import traceback
        traceback.print_exc()
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def admin_pending_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show pending crypto payments"""
    user_id = update.effective_user.id
    
    if user_id != config.OWNER_ID:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    try:
        from datetime import datetime, timedelta
        
        database = get_db()
        time_limit = datetime.utcnow() - timedelta(hours=48)
        
        pending = list(database.transactions.find({
            'status': 'pending',
            'payment_method': 'crypto_manual',
            'created_at': {'$gte': time_limit}
        }).sort('created_at', -1))
        
        if not pending:
            await update.message.reply_text("✅ No pending crypto payments in last 48 hours")
            return
        
        message = f"📋 Pending Crypto Payments: {len(pending)}\n\n"
        
        for tx in pending:
            tx_id = str(tx['_id'])
            user_id_str = str(tx['user_id'])
            amount = f"{tx['amount']:.2f}"
            payment_id = tx.get('payment_id', 'N/A')
            created = tx['created_at'].strftime('%Y-%m-%d %H:%M')
            
            message += f"🆔 ID: {tx_id}\n"
            message += f"👤 User: {user_id_str}\n"
            message += f"💰 Amount: ${amount}\n"
            message += f"📝 Memo: {payment_id}\n"
            message += f"⏰ Time: {created}\n"
            message += "─────────────────\n"
        
        message += "\n💡 To verify:\n/verify_crypto <tx_id> <tx_hash>"
        
        # Split if too long
        if len(message) > 4000:
            chunks = [message[i:i+4000] for i in range(0, len(message), 4000)]
            for chunk in chunks:
                await update.message.reply_text(chunk)
        else:
            await update.message.reply_text(message)
        
    except Exception as e:
        logger.error(f"Error in pending_crypto: {e}")
        import traceback
        traceback.print_exc()
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def admin_verify_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Verify manual crypto payment"""
    user_id = update.effective_user.id
    
    if user_id != config.OWNER_ID:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    try:
        # Usage: /verify_crypto <transaction_id> <tx_hash>
        if len(context.args) < 2:
            await update.message.reply_text(
                "❌ Usage: /verify_crypto <transaction_id> <tx_hash>\n\n"
                "Example: /verify_crypto 675b123... abc123def456"
            )
            return
        
        transaction_id = context.args[0]
        tx_hash = context.args[1]
        
        from payment_crypto_manual import verify_crypto_payment
        
        success = verify_crypto_payment(transaction_id, tx_hash)
        
        if success:
            # Get transaction details
            from bson.objectid import ObjectId
            transaction = Transaction.get_by_id(ObjectId(transaction_id))
            user = User.get_by_telegram_id(transaction['user_id'])
            
            await update.message.reply_text(
                f"✅✅✅ CRYPTO PAYMENT VERIFIED!\n\n"
                f"Transaction: {transaction_id}\n"
                f"User: {transaction['user_id']}\n"
                f"Amount: ${transaction['amount']:.2f}\n"
                f"New Balance: ${user['balance']:.2f}\n"
                f"TX Hash: {tx_hash}"
            )
            
            # Notify user
            try:
                await context.bot.send_message(
                    chat_id=transaction['user_id'],
                    text=(
                        f"✅ **Crypto Payment Verified!**\n\n"
                        f"💰 ${transaction['amount']:.2f} added to your balance\n"
                        f"💳 New Balance: ${user['balance']:.2f}\n\n"
                        f"Thank you!"
                    ),
                    parse_mode='Markdown'
                )
            except:
                pass
        else:
            await update.message.reply_text("❌ Verification failed or already completed")
            
    except Exception as e:
        logger.error(f"Verify crypto error: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def admin_pending_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show pending crypto payments"""
    user_id = update.effective_user.id
    
    if user_id != config.OWNER_ID:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    try:
        from datetime import datetime, timedelta
        
        database = get_db()
        time_limit = datetime.utcnow() - timedelta(hours=48)
        
        pending = list(database.transactions.find({
            'status': 'pending',
            'payment_method': 'crypto_manual',
            'created_at': {'$gte': time_limit}
        }).sort('created_at', -1))
        
        if not pending:
            await update.message.reply_text("✅ No pending crypto payments")
            return
        
        message = f"📋 Pending Crypto Payments: {len(pending)}\n\n"
        
        for tx in pending:
            tx_id = str(tx['_id'])
            user_id_str = str(tx['user_id'])
            amount = f"{tx['amount']:.2f}"
            payment_id = tx.get('payment_id', 'N/A')
            created = tx['created_at'].strftime('%Y-%m-%d %H:%M')
            
            message += f"ID: {tx_id}\n"
            message += f"User: {user_id_str}\n"
            message += f"Amount: ${amount}\n"
            message += f"Memo: {payment_id}\n"
            message += f"Time: {created}\n"
            message += "─────────────────\n"
        
        message += "\n💡 To verify:\n/verify_crypto <tx_id> <tx_hash>"
        
        await update.message.reply_text(message)
        
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")

# ============= BALANCE MANAGEMENT =============

async def admin_add_balance_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start add balance process"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != config.OWNER_ID:
        await query.answer("❌ Unauthorized", show_alert=True)
        return
    
    await query.edit_message_text(
        "💰 Add Balance to User\n\n"
        "Please send the User ID:\n"
        "(You can find it in the user list or when they use /start)\n\n"
        "Send /cancel to abort."
    )
    
    return ADD_BALANCE_USER

async def receive_balance_user_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive user ID for balance addition - FIXED FOR MONGODB"""
    if update.effective_user.id != config.OWNER_ID:
        await update.message.reply_text("❌ Unauthorized")
        return ConversationHandler.END
    
    try:
        user_id = int(update.message.text.strip())
        
        # Use MongoDB method instead of db.query()
        user = User.get_by_telegram_id(user_id)
        
        if not user:
            await update.message.reply_text(
                f"❌ User with ID {user_id} not found.\n"
                "Please check the ID and try again."
            )
            return ADD_BALANCE_USER
        
        context.user_data['balance_user_id'] = user_id
        context.user_data['balance_username'] = user.get('username')
        context.user_data['current_balance'] = user['balance']
        
        await update.message.reply_text(
            f"📋 User Found:\n\n"
            f"ID: {user_id}\n"
            f"Username: @{user.get('username') or 'N/A'}\n"
            f"Current Balance: ${user['balance']:.2f}\n\n"
            "Now send the amount to add (in USD):"
        )
        
        return ADD_BALANCE_AMOUNT
            
    except ValueError:
        await update.message.reply_text("❌ Invalid User ID. Please send a valid number.")
        return ADD_BALANCE_USER
    except Exception as e:
        logger.error(f"Error in receive_balance_user_id: {e}")
        import traceback
        traceback.print_exc()
        await update.message.reply_text(f"❌ Error: {str(e)}")
        return ADD_BALANCE_USER

async def receive_balance_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive amount to add - FIXED FOR MONGODB"""
    if update.effective_user.id != config.OWNER_ID:
        await update.message.reply_text("❌ Unauthorized")
        return ConversationHandler.END
    
    try:
        amount = float(update.message.text.strip())
        
        if amount <= 0:
            await update.message.reply_text("❌ Amount must be greater than 0.")
            return ADD_BALANCE_AMOUNT
        
        user_id = context.user_data['balance_user_id']
        current_balance = context.user_data['current_balance']
        new_balance = current_balance + amount
        
        keyboard = [
            [InlineKeyboardButton("✅ Confirm", callback_data='admin_balance_confirm')],
            [InlineKeyboardButton("❌ Cancel", callback_data='admin_balance_cancel')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        context.user_data['balance_amount'] = amount
        
        await update.message.reply_text(
            f"💰 Confirm Balance Addition\n\n"
            f"User ID: {user_id}\n"
            f"Username: @{context.user_data.get('balance_username') or 'N/A'}\n"
            f"Current Balance: ${current_balance:.2f}\n"
            f"Amount to Add: ${amount:.2f}\n"
            f"New Balance: ${new_balance:.2f}\n\n"
            "Confirm?",
            reply_markup=reply_markup
        )
        
        return CONFIRM_DETAILS
        
    except ValueError:
        await update.message.reply_text("❌ Invalid amount. Please send a valid number.")
        return ADD_BALANCE_AMOUNT
    except Exception as e:
        logger.error(f"Error in receive_balance_amount: {e}")
        import traceback
        traceback.print_exc()
        await update.message.reply_text(f"❌ Error: {str(e)}")
        return ADD_BALANCE_AMOUNT

async def confirm_balance_addition(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm and add balance - FIXED FOR MONGODB"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != config.OWNER_ID:
        await query.answer("❌ Unauthorized", show_alert=True)
        return ConversationHandler.END
    
    if query.data == 'admin_balance_cancel':
        context.user_data.clear()
        await query.edit_message_text("❌ Balance addition cancelled.")
        return ConversationHandler.END
    
    user_id = context.user_data['balance_user_id']
    amount = context.user_data['balance_amount']
    
    try:
        # Update balance using MongoDB method
        success = User.update_balance(user_id, amount, operation='add')
        
        if success:
            # Get updated user data
            user = User.get_by_telegram_id(user_id)
            
            await query.edit_message_text(
                f"✅ Balance added successfully!\n\n"
                f"User ID: {user_id}\n"
                f"Username: @{user.get('username') or 'N/A'}\n"
                f"Amount Added: ${amount:.2f}\n"
                f"New Balance: ${user['balance']:.2f}"
            )
            
            # Notify user
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"💰 Your balance has been credited!\n\nAmount: ${amount:.2f}\nNew Balance: ${user['balance']:.2f}"
                )
            except Exception as e:
                logger.error(f"Could not notify user: {e}")
        else:
            await query.edit_message_text("❌ User not found or balance update failed.")
            
    except Exception as e:
        logger.error(f"Error adding balance: {e}")
        import traceback
        traceback.print_exc()
        await query.edit_message_text(f"❌ Error adding balance: {str(e)}")
    finally:
        context.user_data.clear()
    
    return ConversationHandler.END

async def cancel_operation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel any operation"""
    context.user_data.clear()
    await update.message.reply_text("❌ Operation cancelled.")
    return ConversationHandler.END

async def admin_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Go back to admin menu - 2 BUTTONS PER ROW"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != config.OWNER_ID:
        await query.answer("❌ Unauthorized", show_alert=True)
        return
    
    keyboard = [
        [
            InlineKeyboardButton("📤 Upload", callback_data='admin_upload'),
            InlineKeyboardButton("📦 Bulk Upload", callback_data='admin_bulk_upload')
        ],
        [
            InlineKeyboardButton("💰 Add Balance", callback_data='admin_add_balance'),
            InlineKeyboardButton("📊 Statistics", callback_data='admin_stats')
        ],
        [
            InlineKeyboardButton("👥 Users", callback_data='admin_users'),
            InlineKeyboardButton("👨‍💼 Leaders", callback_data='admin_leaders')
        ],
        [
            InlineKeyboardButton("⚙️ Settings", callback_data='admin_settings'),
            InlineKeyboardButton("💳 Transactions", callback_data='admin_transactions')
        ],
        [
            InlineKeyboardButton("📦 Sessions", callback_data='admin_sessions')
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "🔧 Admin Panel\n\n"
        "Select an option below:",
        reply_markup=reply_markup
    )

async def admin_settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show admin settings menu - FIXED FOR MONGODB"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != config.OWNER_ID:
        await query.answer("❌ Unauthorized", show_alert=True)
        return
    
    try:
        # ✅ Use MongoDB method instead of SQLAlchemy
        from database import SystemSettings
        settings = SystemSettings.get()
        
        if not settings:
            # Create default settings if they don't exist
            SystemSettings.update(min_deposit=5.0, inr_to_usd_rate=0.012)
            settings = SystemSettings.get()
        
        min_deposit = settings.get('min_deposit', 5.0)
        
        # Get current rates from payment modules
        from payment_razorpay import INR_TO_USD_RATE
        
        try:
            from payment_ton import get_ton_payment
            ton_handler = get_ton_payment()
            ton_rate = ton_handler.usd_to_ton(1.0)  # Get rate for 1 USD
        except:
            ton_rate = 0.5  # Fallback
        
        keyboard = [
            [InlineKeyboardButton(f"💵 Min Deposit: ${min_deposit:.2f}", callback_data='admin_set_min_deposit')],
            [InlineKeyboardButton(f"₹ INR Rate: {INR_TO_USD_RATE:.4f}", callback_data='admin_set_inr_rate')],
            [InlineKeyboardButton(f"💎 TON Rate: {ton_rate:.2f} TON/$", callback_data='admin_set_ton_rate')],
            [InlineKeyboardButton("« Back", callback_data='admin_back')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "⚙️ **Bot Settings**\n\n"
            f"💵 **Minimum Deposit:** ${min_deposit:.2f}\n"
            f"₹ **INR to USD Rate:** 1 INR = ${INR_TO_USD_RATE:.4f}\n"
            f"💎 **TON Price:** 1 USD = {ton_rate:.2f} TON\n\n"
            "Click a setting to change it.",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Settings menu error: {e}")
        import traceback
        traceback.print_exc()
        await query.edit_message_text(f"❌ Error loading settings: {str(e)}")


async def admin_set_min_deposit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start setting minimum deposit"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != config.OWNER_ID:
        await query.answer("❌ Unauthorized", show_alert=True)
        return
    
    await query.edit_message_text(
        "💵 **Set Minimum Deposit Amount**\n\n"
        "Current minimum: $5.00\n\n"
        "Enter new minimum deposit amount in USD:\n"
        "Example: 10\n\n"
        "Send /cancel to abort.",
        parse_mode='Markdown'
    )
    
    return SET_MIN_DEPOSIT

async def receive_min_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive and save minimum deposit amount - FIXED FOR MONGODB"""
    if update.effective_user.id != config.OWNER_ID:
        await update.message.reply_text("❌ Unauthorized")
        return ConversationHandler.END
    
    try:
        amount = float(update.message.text.strip())
        
        if amount <= 0:
            await update.message.reply_text("❌ Amount must be greater than 0.")
            return SET_MIN_DEPOSIT
        
        # ✅ Use MongoDB method instead of SQLAlchemy
        from database import SystemSettings
        
        success = SystemSettings.update(min_deposit=amount)
        
        if success:
            await update.message.reply_text(
                f"✅ Minimum deposit updated to ${amount:.2f}\n\n"
                "Users must now deposit at least this amount."
            )
        else:
            await update.message.reply_text("❌ Error updating settings.")
        
        return ConversationHandler.END
        
    except ValueError:
        await update.message.reply_text("❌ Invalid amount. Please enter a number.")
        return SET_MIN_DEPOSIT
    except Exception as e:
        logger.error(f"Error updating min deposit: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")
        return SET_MIN_DEPOSIT


async def admin_set_inr_rate_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start setting INR conversion rate"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != config.OWNER_ID:
        await query.answer("❌ Unauthorized", show_alert=True)
        return
    
    from payment_razorpay import INR_TO_USD_RATE
    
    await query.edit_message_text(
        "₹ **Set INR to USD Conversion Rate**\n\n"
        f"Current rate: 1 INR = ${INR_TO_USD_RATE:.4f}\n"
        f"(₹83 = $1 approximately)\n\n"
        "Enter new rate (how much USD for 1 INR):\n"
        "Example: 0.012 (means ₹83 = $1)\n\n"
        "Send /cancel to abort.",
        parse_mode='Markdown'
    )
    
    return SET_INR_RATE

async def receive_inr_rate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive and save INR rate"""
    if update.effective_user.id != config.OWNER_ID:
        await update.message.reply_text("❌ Unauthorized")
        return ConversationHandler.END
    
    try:
        rate = float(update.message.text.strip())
        
        if rate <= 0 or rate > 1:
            await update.message.reply_text("❌ Rate must be between 0 and 1.")
            return SET_INR_RATE
        
        # Update the rate in payment_razorpay module
        import payment_razorpay
        payment_razorpay.INR_TO_USD_RATE = rate
        
        # Also save to config file
        config_path = 'config.py'
        try:
            with open(config_path, 'r') as f:
                content = f.read()
            
            # Update or add INR_TO_USD_RATE
            if 'INR_TO_USD_RATE' in content:
                import re
                content = re.sub(
                    r'INR_TO_USD_RATE\s*=\s*[\d.]+',
                    f'INR_TO_USD_RATE = {rate}',
                    content
                )
            else:
                content += f'\nINR_TO_USD_RATE = {rate}\n'
            
            with open(config_path, 'w') as f:
                f.write(content)
        except Exception as e:
            logger.error(f"Could not save to config: {e}")
        
        inr_per_dollar = 1 / rate
        
        await update.message.reply_text(
            f"✅ INR conversion rate updated!\n\n"
            f"New rate: 1 INR = ${rate:.4f}\n"
            f"Or: ₹{inr_per_dollar:.2f} = $1"
        )
        
        return ConversationHandler.END
        
    except ValueError:
        await update.message.reply_text("❌ Invalid rate. Please enter a number.")
        return SET_INR_RATE

async def admin_set_ton_rate_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start setting TON price"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != config.OWNER_ID:
        await query.answer("❌ Unauthorized", show_alert=True)
        return
    
    await query.edit_message_text(
        "💎 **Set TON Price (Manual Override)**\n\n"
        "Current: Auto-fetched from CoinGecko\n\n"
        "Enter TON price in USD:\n"
        "Example: 2.5 (means 1 TON = $2.50)\n\n"
        "⚠️ This will override auto-fetching.\n"
        "Send 'auto' to re-enable auto-fetch.\n\n"
        "Send /cancel to abort.",
        parse_mode='Markdown'
    )
    
    return SET_TON_RATE

async def receive_ton_rate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive and save TON price"""
    if update.effective_user.id != config.OWNER_ID:
        await update.message.reply_text("❌ Unauthorized")
        return ConversationHandler.END
    
    input_text = update.message.text.strip().lower()
    
    try:
        if input_text == 'auto':
            # Re-enable auto-fetching
            try:
                import payment_ton
                if hasattr(payment_ton, 'MANUAL_TON_PRICE'):
                    delattr(payment_ton, 'MANUAL_TON_PRICE')
                
                await update.message.reply_text(
                    "✅ TON price set to AUTO-FETCH\n\n"
                    "Price will be fetched from CoinGecko API."
                )
            except Exception as e:
                await update.message.reply_text(f"❌ Error: {e}")
        else:
            price = float(input_text)
            
            if price <= 0:
                await update.message.reply_text("❌ Price must be greater than 0.")
                return SET_TON_RATE
            
            # Set manual price
            import payment_ton
            payment_ton.MANUAL_TON_PRICE = price
            
            ton_per_dollar = 1 / price
            
            await update.message.reply_text(
                f"✅ TON price updated!\n\n"
                f"New price: 1 TON = ${price:.2f}\n"
                f"Or: {ton_per_dollar:.3f} TON = $1\n\n"
                f"⚠️ Manual override active.\n"
                f"Send 'auto' to re-enable auto-fetch."
            )
        
        return ConversationHandler.END
        
    except ValueError:
        await update.message.reply_text("❌ Invalid price. Please enter a number or 'auto'.")
        return SET_TON_RATE

# ============================================================================
# ADD THESE TON ADMIN FUNCTIONS TO THE END OF YOUR admin.py FILE
# Add them BEFORE the setup_admin_handlers function (before line 1680)
# ============================================================================

# TON Payment Admin Functions

def setup_admin_handlers(application):
    """Setup admin command handlers - COMPLETE FIXED VERSION"""
    
    # ✅ OPTIONAL: Register extra TON admin tools
    # These are OPTIONAL - only register if you want them
    # The main TON commands are registered in bot.py
    if hasattr(config, 'OWNER_ID'):
        # Uncomment these if you want extra debugging tools:
        # application.add_handler(CommandHandler('check_ton', check_ton_transaction))
        # application.add_handler(CommandHandler('search_memo', search_ton_by_memo))
        # application.add_handler(CommandHandler('ton_stats', ton_stats))
        pass
    
    logger.info("✅ Admin handlers setup started")
    
    # Delete session handlers
    application.add_handler(CallbackQueryHandler(admin_delete_session_start, pattern='^admin_delete_page_'))
    application.add_handler(CallbackQueryHandler(admin_delete_session_start, pattern='^admin_delete_sessions$'))
    application.add_handler(CallbackQueryHandler(admin_confirm_delete_session, pattern='^delete_session_'))
    application.add_handler(CallbackQueryHandler(admin_execute_delete_session, pattern='^confirm_delete_'))
    logger.info("✅ Delete session handlers registered")
    # Main admin command
    application.add_handler(CommandHandler("admin", admin_start))
    application.add_handler(CommandHandler('quickcast', quick_broadcast))
    
    # ❌ REMOVED: application.add_handler(CallbackQueryHandler(admin_edit_info_button, pattern='^admin_edit_info_btn$'))
    # This was causing the issue - it must be in the conversation handler entry_points instead
    
    # ============================================================================
    # BROADCAST CONVERSATION - Already correct
    # ============================================================================
    broadcast_conv = ConversationHandler(
        entry_points=[
            CommandHandler('broadcast', admin_broadcast_start),
            CallbackQueryHandler(admin_broadcast_button, pattern='^admin_broadcast_btn$')
        ],
        states={
            BROADCAST_SELECT: [
                CallbackQueryHandler(receive_broadcast_target, pattern='^broadcast_'),
            ],
            BROADCAST_MESSAGE: [
                MessageHandler(filters.TEXT | filters.PHOTO, receive_broadcast_message)
            ],
            BROADCAST_CONFIRM: [
                CallbackQueryHandler(confirm_broadcast, pattern='^broadcast_')
            ]
        },
        fallbacks=[CommandHandler('cancel', cancel_operation)],
        allow_reentry=True
    )
    application.add_handler(broadcast_conv, group=0)
    logger.info("✅ Broadcast conversation registered")

    # ============================================================================
    # EDIT INFO CONVERSATION - FIXED: Added button to entry_points
    # ============================================================================
    edit_info_conv = ConversationHandler(
        entry_points=[
            CommandHandler('edit_info', admin_edit_info_start),
            CallbackQueryHandler(admin_edit_info_button, pattern='^admin_edit_info_btn$')  # ✅ ADDED THIS
        ],
        states={
            EDIT_INFO_COUNTRY: [
                CallbackQueryHandler(receive_edit_info_country, pattern='^edit_info_country_'),
                CallbackQueryHandler(receive_edit_info_country, pattern='^admin_cancel$')
            ],
            EDIT_INFO_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_edit_info_text)
            ]
        },
        fallbacks=[CommandHandler('cancel', cancel_operation)],
        allow_reentry=True
    )
    application.add_handler(edit_info_conv, group=0)
    logger.info("✅ Edit info conversation registered")

    # Single session upload conversation
    session_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_upload_start, pattern='^admin_upload$')],
        states={
            UPLOAD_SESSION: [MessageHandler(filters.Document.ALL, receive_session_file)],
            GET_COUNTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_country)],
            GET_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_price)],
            GET_2FA: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_2fa_password)],
            GET_INFO: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_session_info)],
            CONFIRM_DETAILS: [CallbackQueryHandler(confirm_session_upload, pattern='^admin_confirm_')]
        },
        fallbacks=[CommandHandler('cancel', cancel_operation)],
        allow_reentry=True
    )
    application.add_handler(session_conv)
    
    # Bulk upload conversation
    bulk_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(admin_bulk_upload_start, pattern='^admin_bulk_upload$')
        ],
        states={
            UPLOAD_BULK: [
                MessageHandler(filters.Document.ALL, receive_bulk_files),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_bulk_files),
                CommandHandler('done', receive_bulk_files)
            ],
            GET_COUNTRY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_country)
            ],
            GET_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_price)
            ],
            GET_2FA: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_2fa_password)
            ],
            GET_INFO: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_session_info)
            ],
            CONFIRM_DETAILS: [
                CallbackQueryHandler(confirm_session_upload, pattern='^admin_confirm_')
            ]
        },
        fallbacks=[
            CommandHandler('cancel', cancel_operation)
        ],
        allow_reentry=True
    )
    application.add_handler(bulk_conv)
    logger.info("✅ Bulk upload conversation handler registered")
    
    # Add balance conversation
    balance_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_balance_start, pattern='^admin_add_balance$')],
        states={
            ADD_BALANCE_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_balance_user_id)],
            ADD_BALANCE_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_balance_amount)],
            CONFIRM_DETAILS: [CallbackQueryHandler(confirm_balance_addition, pattern='^admin_balance_')]
        },
        fallbacks=[CommandHandler('cancel', cancel_operation)],
        allow_reentry=True
    )
    application.add_handler(balance_conv)
    
    # Settings conversations
    settings_menu_handler = CallbackQueryHandler(admin_settings_menu, pattern='^admin_settings$')
    application.add_handler(settings_menu_handler)
    
    min_deposit_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_set_min_deposit_start, pattern='^admin_set_min_deposit$')],
        states={
            SET_MIN_DEPOSIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_min_deposit)]
        },
        fallbacks=[CommandHandler('cancel', cancel_operation)],
        allow_reentry=True
    )
    application.add_handler(min_deposit_conv)
    
    inr_rate_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_set_inr_rate_start, pattern='^admin_set_inr_rate$')],
        states={
            SET_INR_RATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_inr_rate)]
        },
        fallbacks=[CommandHandler('cancel', cancel_operation)],
        allow_reentry=True
    )
    application.add_handler(inr_rate_conv)
    
    ton_rate_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_set_ton_rate_start, pattern='^admin_set_ton_rate$')],
        states={
            SET_TON_RATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_ton_rate)]
        },
        fallbacks=[CommandHandler('cancel', cancel_operation)],
        allow_reentry=True
    )
    application.add_handler(ton_rate_conv)
    
    # Callback handlers for admin panel
    application.add_handler(CallbackQueryHandler(admin_stats, pattern='^admin_stats$'))
    application.add_handler(CallbackQueryHandler(admin_users, pattern='^admin_users$'))
    application.add_handler(CallbackQueryHandler(admin_transactions, pattern='^admin_transactions$'))
    application.add_handler(CallbackQueryHandler(admin_sessions_list, pattern='^admin_sessions$'))
    application.add_handler(CallbackQueryHandler(admin_back, pattern='^admin_back$'))
    
    # Leaders management handlers
    application.add_handler(CallbackQueryHandler(admin_leaders_panel, pattern='^admin_leaders$'))
    application.add_handler(CallbackQueryHandler(admin_leader_details, pattern='^admin_leader_detail_'))
    logger.info("✅ Leaders management handlers registered")
    
    logger.info("✅ Admin handlers registered successfully")
    
    # Crypto manual verification commands
    application.add_handler(CommandHandler("verify_crypto", admin_verify_crypto))
    application.add_handler(CommandHandler("pending_crypto", admin_pending_crypto))
    logger.info("✅ Crypto manual verification commands registered")