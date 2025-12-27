"""
Admin Commands for Seller & Withdrawal Management
Complete admin interface for managing sellers
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler
import config
from database import get_db
from bson.objectid import ObjectId
from datetime import datetime

logger = logging.getLogger(__name__)

# ============================================
# PENDING SELLERS
# ============================================

async def admin_pending_sellers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all pending seller applications with action buttons"""
    if update.effective_user.id != config.OWNER_ID:
        await update.message.reply_text("❌ Admin only")
        return
    
    database = get_db()
    pending = list(database.seller_applications.find({"status": "pending"}).sort("created_at", -1))
    
    if not pending:
        await update.message.reply_text("✅ No pending seller applications")
        return
    
    await update.message.reply_text(f"📋 Found {len(pending)} pending application(s)\n")
    
    for app in pending:
        text = f"👤 **{app['first_name']}**\n"
        text += f"🆔 ID: `{app['telegram_id']}`\n"
        text += f"📝 Username: @{app.get('username', 'None')}\n"
        text += f"📍 Countries: {app['countries']}\n"
        text += f"💰 Price: ${app['price']:.2f}\n"
        text += f"⏰ Applied: {app['created_at'].strftime('%Y-%m-%d %H:%M')}\n"
        
        # Add action buttons for each application
        keyboard = [
            [
                InlineKeyboardButton("✅ Approve", callback_data=f'seller_approve_{app["telegram_id"]}'),
                InlineKeyboardButton("❌ Reject", callback_data=f'seller_reject_{app["telegram_id"]}')
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

async def admin_approved_sellers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all approved sellers"""
    if update.effective_user.id != config.OWNER_ID:
        await update.message.reply_text("❌ Admin only")
        return
    
    database = get_db()
    approved = list(database.seller_applications.find({"status": "approved"}).sort("approved_at", -1).limit(20))
    
    if not approved:
        await update.message.reply_text("📭 No approved sellers yet")
        return
    
    text = f"✅ **Approved Sellers** ({len(approved)})\n\n"
    
    for app in approved:
        text += f"👤 {app['first_name']} (@{app.get('username', 'None')})\n"
        text += f"🆔 `{app['telegram_id']}`\n"
        text += f"📍 {app['countries']}\n"
        text += f"💰 ${app['price']:.2f}\n"
        text += f"✅ {app.get('approved_at', app['created_at']).strftime('%Y-%m-%d')}\n\n"
    
    await update.message.reply_text(text, parse_mode='Markdown')

async def admin_all_sellers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all sellers (all statuses)"""
    if update.effective_user.id != config.OWNER_ID:
        await update.message.reply_text("❌ Admin only")
        return
    
    database = get_db()
    all_sellers = list(database.seller_applications.find().sort("created_at", -1))
    
    if not all_sellers:
        await update.message.reply_text("📭 No seller applications yet")
        return
    
    # Count by status
    pending = sum(1 for s in all_sellers if s['status'] == 'pending')
    approved = sum(1 for s in all_sellers if s['status'] == 'approved')
    rejected = sum(1 for s in all_sellers if s['status'] == 'rejected')
    
    text = f"👥 **All Seller Applications**\n\n"
    text += f"📊 Total: {len(all_sellers)}\n"
    text += f"⏳ Pending: {pending}\n"
    text += f"✅ Approved: {approved}\n"
    text += f"❌ Rejected: {rejected}\n\n"
    text += "─────────────────\n\n"
    
    for app in all_sellers[:15]:  # Show latest 15
        status_emoji = {
            'pending': '⏳',
            'approved': '✅',
            'rejected': '❌'
        }.get(app['status'], '❓')
        
        text += f"{status_emoji} {app['first_name']} - `{app['telegram_id']}`\n"
        text += f"   {app['countries']} - ${app['price']:.2f}\n"
        text += f"   {app['created_at'].strftime('%Y-%m-%d')}\n\n"
    
    if len(all_sellers) > 15:
        text += f"\n...and {len(all_sellers) - 15} more"
    
    await update.message.reply_text(text, parse_mode='Markdown')

# ============================================
# PENDING WITHDRAWALS
# ============================================

async def admin_pending_withdrawals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all pending withdrawals with action buttons"""
    if update.effective_user.id != config.OWNER_ID:
        await update.message.reply_text("❌ Admin only")
        return
    
    database = get_db()
    pending = list(database.withdrawals.find({"status": "pending"}).sort("created_at", -1))
    
    if not pending:
        await update.message.reply_text("✅ No pending withdrawals")
        return
    
    await update.message.reply_text(f"💸 Found {len(pending)} pending withdrawal(s)\n")
    
    for w in pending:
        # Get seller application info
        seller_app = database.seller_applications.find_one({"telegram_id": w['user_id']})
        seller_name = seller_app['first_name'] if seller_app else "Unknown"
        
        text = f"👤 **{seller_name}**\n"
        text += f"🆔 ID: `{w['user_id']}`\n"
        text += f"📝 Username: @{w.get('username', 'None')}\n"
        text += f"💰 Amount: **${w['amount']:.2f}**\n"
        text += f"📍 USDT BEP20:\n`{w['address']}`\n"
        text += f"⏰ Requested: {w['created_at'].strftime('%Y-%m-%d %H:%M')}\n"
        
        # Add action buttons
        keyboard = [
            [
                InlineKeyboardButton("✅ Mark as Paid", callback_data=f'withdrawal_complete_{w["_id"]}'),
                InlineKeyboardButton("❌ Reject", callback_data=f'withdrawal_reject_{w["_id"]}')
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

async def admin_completed_withdrawals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show recently completed withdrawals"""
    if update.effective_user.id != config.OWNER_ID:
        await update.message.reply_text("❌ Admin only")
        return
    
    database = get_db()
    completed = list(database.withdrawals.find({"status": "completed"}).sort("completed_at", -1).limit(20))
    
    if not completed:
        await update.message.reply_text("📭 No completed withdrawals yet")
        return
    
    text = f"✅ **Recently Completed Withdrawals** ({len(completed)})\n\n"
    total_paid = sum(w['amount'] for w in completed)
    
    text += f"💰 Total Paid: ${total_paid:.2f}\n\n"
    text += "─────────────────\n\n"
    
    for w in completed:
        text += f"👤 User: {w['user_id']}\n"
        text += f"💰 ${w['amount']:.2f}\n"
        text += f"✅ {w.get('completed_at', w['created_at']).strftime('%Y-%m-%d %H:%M')}\n\n"
    
    await update.message.reply_text(text, parse_mode='Markdown')

async def admin_all_withdrawals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all withdrawals summary"""
    if update.effective_user.id != config.OWNER_ID:
        await update.message.reply_text("❌ Admin only")
        return
    
    database = get_db()
    all_withdrawals = list(database.withdrawals.find().sort("created_at", -1))
    
    if not all_withdrawals:
        await update.message.reply_text("📭 No withdrawals yet")
        return
    
    # Calculate stats
    pending = [w for w in all_withdrawals if w['status'] == 'pending']
    completed = [w for w in all_withdrawals if w['status'] == 'completed']
    rejected = [w for w in all_withdrawals if w['status'] == 'rejected']
    
    total_pending_amount = sum(w['amount'] for w in pending)
    total_completed_amount = sum(w['amount'] for w in completed)
    
    text = f"💸 **All Withdrawals**\n\n"
    text += f"📊 Total Requests: {len(all_withdrawals)}\n"
    text += f"⏳ Pending: {len(pending)} (${total_pending_amount:.2f})\n"
    text += f"✅ Completed: {len(completed)} (${total_completed_amount:.2f})\n"
    text += f"❌ Rejected: {len(rejected)}\n\n"
    text += "─────────────────\n\n"
    
    text += "**Latest Withdrawals:**\n\n"
    for w in all_withdrawals[:10]:
        status_emoji = {
            'pending': '⏳',
            'completed': '✅',
            'rejected': '❌'
        }.get(w['status'], '❓')
        
        text += f"{status_emoji} ${w['amount']:.2f} - User {w['user_id']}\n"
        text += f"   {w['created_at'].strftime('%Y-%m-%d %H:%M')}\n\n"
    
    await update.message.reply_text(text, parse_mode='Markdown')

# ============================================
# SELLER STATS
# ============================================

async def admin_seller_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show detailed stats for a specific seller"""
    if update.effective_user.id != config.OWNER_ID:
        await update.message.reply_text("❌ Admin only")
        return
    
    # Get seller ID from command
    try:
        seller_id = int(context.args[0]) if context.args else None
    except (IndexError, ValueError):
        await update.message.reply_text(
            "Usage: /seller_stats <seller_id>\n\n"
            "Example: /seller_stats 1234567890"
        )
        return
    
    database = get_db()
    
    # Get seller info
    seller = database.seller_applications.find_one({"telegram_id": seller_id})
    if not seller:
        await update.message.reply_text(f"❌ Seller {seller_id} not found")
        return
    
    # Get sessions stats
    total_uploaded = database.sessions.count_documents({"uploader_id": seller_id})
    total_sold = database.sessions.count_documents({"uploader_id": seller_id, "is_sold": True})
    available = total_uploaded - total_sold
    
    # Calculate revenue
    revenue_pipeline = [
        {"$match": {"uploader_id": seller_id, "is_sold": True}},
        {"$group": {"_id": None, "total": {"$sum": "$price"}}}
    ]
    revenue_result = list(database.sessions.aggregate(revenue_pipeline))
    total_revenue = revenue_result[0]['total'] if revenue_result else 0.0
    
    # Get withdrawals
    withdrawals = list(database.withdrawals.find({"user_id": seller_id}))
    total_withdrawn = sum(w['amount'] for w in withdrawals if w['status'] == 'completed')
    pending_withdrawals = sum(w['amount'] for w in withdrawals if w['status'] == 'pending')
    
    available_balance = total_revenue - total_withdrawn
    
    text = f"👤 **Seller Statistics**\n\n"
    text += f"**Profile:**\n"
    text += f"Name: {seller['first_name']}\n"
    text += f"ID: `{seller_id}`\n"
    text += f"Username: @{seller.get('username', 'None')}\n"
    text += f"Status: {seller['status'].title()}\n"
    text += f"Countries: {seller['countries']}\n"
    text += f"Price: ${seller['price']:.2f}\n\n"
    
    text += f"**Sessions:**\n"
    text += f"📤 Uploaded: {total_uploaded}\n"
    text += f"✅ Sold: {total_sold}\n"
    text += f"📦 Available: {available}\n\n"
    
    text += f"**Financials:**\n"
    text += f"💰 Total Revenue: ${total_revenue:.2f}\n"
    text += f"💸 Withdrawn: ${total_withdrawn:.2f}\n"
    text += f"⏳ Pending: ${pending_withdrawals:.2f}\n"
    text += f"💵 Available: ${available_balance:.2f}\n\n"
    
    text += f"**Withdrawals:**\n"
    text += f"Total Requests: {len(withdrawals)}\n"
    text += f"Completed: {sum(1 for w in withdrawals if w['status'] == 'completed')}\n"
    text += f"Pending: {sum(1 for w in withdrawals if w['status'] == 'pending')}\n"
    
    await update.message.reply_text(text, parse_mode='Markdown')

# ============================================
# SEARCH & FIND
# ============================================

async def admin_find_seller(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Find seller by ID or username"""
    if update.effective_user.id != config.OWNER_ID:
        await update.message.reply_text("❌ Admin only")
        return
    
    if not context.args:
        await update.message.reply_text(
            "Usage: /find_seller <id or @username>\n\n"
            "Examples:\n"
            "/find_seller 1234567890\n"
            "/find_seller @john_doe"
        )
        return
    
    search_term = context.args[0]
    database = get_db()
    
    # Try to find by ID
    try:
        seller_id = int(search_term)
        seller = database.seller_applications.find_one({"telegram_id": seller_id})
    except ValueError:
        # Search by username
        username = search_term.replace('@', '')
        seller = database.seller_applications.find_one({"username": username})
    
    if not seller:
        await update.message.reply_text(f"❌ Seller not found: {search_term}")
        return
    
    status_emoji = {
        'pending': '⏳',
        'approved': '✅',
        'rejected': '❌'
    }.get(seller['status'], '❓')
    
    text = f"{status_emoji} **Seller Found**\n\n"
    text += f"👤 {seller['first_name']}\n"
    text += f"🆔 `{seller['telegram_id']}`\n"
    text += f"📝 @{seller.get('username', 'None')}\n"
    text += f"📍 {seller['countries']}\n"
    text += f"💰 ${seller['price']:.2f}\n"
    text += f"Status: {seller['status'].title()}\n"
    text += f"Applied: {seller['created_at'].strftime('%Y-%m-%d %H:%M')}\n\n"
    text += f"Use `/seller_stats {seller['telegram_id']}` for detailed stats"
    
    await update.message.reply_text(text, parse_mode='Markdown')

# ============================================
# HELP COMMAND
# ============================================

async def admin_seller_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all seller management commands"""
    if update.effective_user.id != config.OWNER_ID:
        await update.message.reply_text("❌ Admin only")
        return
    
    text = """📚 **Seller Management Commands**

**Applications:**
/pending_sellers - View pending applications
/approved_sellers - View approved sellers
/all_sellers - View all applications

**Withdrawals:**
/pending_withdrawals - View pending withdrawals
/completed_withdrawals - View paid withdrawals
/all_withdrawals - View all withdrawals

**Seller Info:**
/seller_stats <id> - Detailed seller stats
/find_seller <id/@username> - Find seller

**Quick Actions:**
• Click buttons in notifications to approve/reject
• Withdrawals: Mark as paid after manual USDT transfer
• After approving seller, add their ID to leaders.py

**Example Usage:**
`/seller_stats 1234567890`
`/find_seller @john_doe`
`/find_seller 1234567890`
"""
    
    await update.message.reply_text(text, parse_mode='Markdown')

# ============================================
# REGISTER HANDLERS
# ============================================

def register_admin_seller_commands(application):
    """Register all admin seller commands"""
    logger.info("Registering admin seller commands...")
    
    application.add_handler(CommandHandler("pending_sellers", admin_pending_sellers))
    application.add_handler(CommandHandler("approved_sellers", admin_approved_sellers))
    application.add_handler(CommandHandler("all_sellers", admin_all_sellers))
    
    application.add_handler(CommandHandler("pending_withdrawals", admin_pending_withdrawals))
    application.add_handler(CommandHandler("completed_withdrawals", admin_completed_withdrawals))
    application.add_handler(CommandHandler("all_withdrawals", admin_all_withdrawals))
    
    application.add_handler(CommandHandler("seller_stats", admin_seller_stats))
    application.add_handler(CommandHandler("find_seller", admin_find_seller))
    application.add_handler(CommandHandler("seller_help", admin_seller_help))
    
    logger.info("✅ Admin seller commands registered")