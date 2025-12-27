"""
Seller Application System (Frontend for Leaders)
- Users apply to become sellers
- Admin reviews and approves
- Admin manually adds approved IDs to leaders.py LEADERS list
- Withdrawal system for leaders
"""

import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    CommandHandler, 
    MessageHandler, 
    filters, 
    ConversationHandler,
    ContextTypes,
    CallbackQueryHandler
)
import config
from database import get_db
from bson.objectid import ObjectId

logger = logging.getLogger(__name__)

# Conversation states
SELLER_GET_COUNTRIES, SELLER_GET_PRICES, WITHDRAWAL_GET_AMOUNT, WITHDRAWAL_GET_ADDRESS = range(4)

# ============================================
# SELLER APPLICATION (USER-FACING)
# ============================================

async def seller_apply_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start seller application"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    database = get_db()
    
    # Check if already applied
    application = database.seller_applications.find_one({"telegram_id": user_id})
    if application:
        if application['status'] == 'pending':
            await query.edit_message_text(
                "⏳ Your application is pending admin review.\n\n"
                "Please wait for approval."
            )
            return ConversationHandler.END
        elif application['status'] == 'approved':
            await query.edit_message_text(
                "✅ You're already approved!\n\n"
                "Use /leader to access your seller panel."
            )
            return ConversationHandler.END
        elif application['status'] == 'rejected':
            # Allow reapplication
            database.seller_applications.delete_one({"telegram_id": user_id})
    
    # Start application
    keyboard = [
        [InlineKeyboardButton("✅ Yes, Apply", callback_data='seller_confirm_start')],
        [InlineKeyboardButton("❌ Cancel", callback_data='back_menu')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "🎯 Become a Seller\n\n"
        "As a seller, you can:\n"
        "• Upload Telegram sessions\n"
        "• Earn from each sale\n"
        "• Track your earnings\n"
        "• Request withdrawals\n\n"
        "⚠️ IMPORTANT:\n"
        "• 15% commission goes to platform owner\n"
        "• You receive 85% of sale price\n"
        "• Set your prices accordingly!\n\n"
        "Requirements:\n"
        "1️⃣ Countries you can provide\n"
        "2️⃣ Your price per session\n\n"
        "Admin will review your application.\n\n"
        "Ready to apply?",
        reply_markup=reply_markup
    )
    return SELLER_GET_COUNTRIES

async def seller_confirm_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ask for countries"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "📍 Step 1: Countries\n\n"
        "Which countries can you provide?\n\n"
        "Examples:\n"
        "• India, USA, UK\n"
        "• India\n"
        "• USA, Canada\n\n"
        "Enter countries (comma-separated):"
    )
    return SELLER_GET_COUNTRIES

async def seller_receive_countries(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive countries, ask for prices"""
    countries = update.message.text.strip()
    
    if len(countries) < 2:
        await update.message.reply_text(
            "❌ Please enter at least one country.\n\nTry again:"
        )
        return SELLER_GET_COUNTRIES
    
    context.user_data['seller_countries'] = countries
    
    await update.message.reply_text(
        f"💰 Step 2: Pricing\n\n"
        f"Countries: {countries}\n\n"
        "What's your price per session? (USD)\n\n"
        "⚠️ Remember: 15% commission to owner\n"
        "You earn 85% of the price you set\n\n"
        "Examples:\n"
        "• Set $2.00 → You earn $1.70\n"
        "• Set $1.50 → You earn $1.28\n\n"
        "Enter your price:"
    )
    return SELLER_GET_PRICES

async def seller_receive_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive prices and submit application"""
    price_text = update.message.text.strip()
    
    try:
        price = float(price_text)
        if price <= 0 or price > 100:
            raise ValueError()
    except:
        await update.message.reply_text(
            "❌ Invalid price. Enter a number between 0 and 100.\n\nTry again:"
        )
        return SELLER_GET_PRICES
    
    user = update.effective_user
    countries = context.user_data.get('seller_countries', '')
    
    # Save application to database
    database = get_db()
    application_data = {
        "telegram_id": user.id,
        "username": user.username or user.first_name,
        "first_name": user.first_name,
        "countries": countries,
        "price": price,
        "status": "pending",
        "created_at": datetime.utcnow()
    }
    database.seller_applications.insert_one(application_data)
    
    # Show confirmation to user
    await update.message.reply_text(
        "✅ Application Submitted!\n\n"
        f"📍 Countries: {countries}\n"
        f"💰 Price: ${price:.2f} per session\n\n"
        "Your application has been sent to admin for review.\n"
        "You'll be notified once it's approved!"
    )
    
    # Forward to admin
    try:
        keyboard = [
            [
                InlineKeyboardButton("✅ Approve", callback_data=f'seller_approve_{user.id}'),
                InlineKeyboardButton("❌ Reject", callback_data=f'seller_reject_{user.id}')
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await context.bot.send_message(
            config.OWNER_ID,
            f"🆕 New Seller Application\n\n"
            f"👤 User: {user.first_name}\n"
            f"🆔 ID: {user.id}\n"
            f"📝 Username: {'@' + user.username if user.username else 'No username'}\n"
            f"📍 Countries: {countries}\n"
            f"💰 Price: ${price:.2f}\n"
            f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
            f"To activate: Add {user.id} to LEADERS list in leaders.py",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Failed to notify admin: {e}")
    
    context.user_data.clear()
    return ConversationHandler.END

async def seller_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel application"""
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    
    await query.edit_message_text("❌ Application cancelled.")
    return ConversationHandler.END

# ============================================
# ADMIN APPROVAL HANDLERS
# ============================================

async def admin_seller_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin approves seller application"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != config.OWNER_ID:
        await query.answer("❌ Admin only", show_alert=True)
        return
    
    user_id = int(query.data.replace('seller_approve_', ''))
    database = get_db()
    
    # Update application status
    result = database.seller_applications.update_one(
        {"telegram_id": user_id},
        {"$set": {
            "status": "approved",
            "approved_at": datetime.utcnow(),
            "approved_by": query.from_user.id
        }}
    )
    
    if result.modified_count > 0:
        # Get application details
        app = database.seller_applications.find_one({"telegram_id": user_id})
        
        await query.edit_message_text(
            f"✅ Seller Approved\n\n"
            f"👤 {app['first_name']}\n"
            f"🆔 {user_id}\n"
            f"📍 {app['countries']}\n"
            f"💰 ${app['price']:.2f}\n\n"
            f"IMPORTANT: Add this ID to leaders.py LEADERS list:\n"
            f"{user_id},"
        )
        
        # Notify user
        try:
            await context.bot.send_message(
                user_id,
                "🎉 Congratulations!\n\n"
                "Your seller application has been approved!\n\n"
                "You can now:\n"
                "• Use /leader to access seller panel\n"
                "• Upload sessions\n"
                "• Track earnings\n"
                "• Request withdrawals\n\n"
                "Get started now with /leader"
            )
        except Exception as e:
            logger.error(f"Failed to notify user: {e}")
    else:
        await query.edit_message_text("❌ Application not found or already processed.")

async def admin_seller_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin rejects seller application"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != config.OWNER_ID:
        await query.answer("❌ Admin only", show_alert=True)
        return
    
    user_id = int(query.data.replace('seller_reject_', ''))
    database = get_db()
    
    # Update application status
    result = database.seller_applications.update_one(
        {"telegram_id": user_id},
        {"$set": {
            "status": "rejected",
            "rejected_at": datetime.utcnow(),
            "rejected_by": query.from_user.id
        }}
    )
    
    if result.modified_count > 0:
        app = database.seller_applications.find_one({"telegram_id": user_id})
        
        await query.edit_message_text(
            f"❌ Seller Rejected\n\n"
            f"👤 {app['first_name']}\n"
            f"🆔 {user_id}"
        )
        
        # Notify user
        try:
            await context.bot.send_message(
                user_id,
                "❌ Application Rejected\n\n"
                "Unfortunately, your seller application was not approved.\n\n"
                "Please contact support for more information."
            )
        except Exception as e:
            logger.error(f"Failed to notify user: {e}")
    else:
        await query.edit_message_text("❌ Application not found or already processed.")

# ============================================
# WITHDRAWAL SYSTEM (FOR LEADERS)
# ============================================

async def leader_withdrawal_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show withdrawal history"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    database = get_db()
    
    # Get withdrawals
    withdrawals = list(database.withdrawals.find(
        {"user_id": user_id}
    ).sort("created_at", -1).limit(10))
    
    if not withdrawals:
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data='leader_back')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "📜 Withdrawal History\n\n"
            "No withdrawals yet.",
            reply_markup=reply_markup
        )
        return
    
    # Build history text
    text = "📜 Withdrawal History\n\n"
    for w in withdrawals:
        status_emoji = {
            'pending': '⏳',
            'completed': '✅',
            'rejected': '❌'
        }.get(w['status'], '❓')
        
        text += f"{status_emoji} ${w['amount']:.2f} - {w['status'].title()}\n"
        text += f"   {w['created_at'].strftime('%Y-%m-%d %H:%M')}\n"
        if w.get('address'):
            text += f"   Address: {w['address'][:20]}...\n"
        text += "\n"
    
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data='leader_back')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        text,
        reply_markup=reply_markup
    )

async def leader_request_withdrawal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start withdrawal request"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    database = get_db()
    
    # Get leader stats
    stats = database.sessions.aggregate([
        {
            "$match": {
                "uploader_id": user_id,
                "is_sold": True
            }
        },
        {
            "$group": {
                "_id": None,
                "total_revenue": {"$sum": "$price"}
            }
        }
    ])
    stats_list = list(stats)
    total_sales = stats_list[0]['total_revenue'] if stats_list else 0.0
    
    # Calculate earnings after 15% commission
    total_earnings = total_sales * 0.85
    commission = total_sales * 0.15
    
    # Get total withdrawn
    withdrawn = database.withdrawals.aggregate([
        {
            "$match": {
                "user_id": user_id,
                "status": "completed"
            }
        },
        {
            "$group": {
                "_id": None,
                "total": {"$sum": "$amount"}
            }
        }
    ])
    withdrawn_list = list(withdrawn)
    total_withdrawn = withdrawn_list[0]['total'] if withdrawn_list else 0.0
    
    available_balance = total_earnings - total_withdrawn
    
    if available_balance < 1.0:
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data='leader_back')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"💰 Financial Summary\n\n"
            f"📊 Total Sales: ${total_sales:.2f}\n"
            f"💵 Your Earnings (85%): ${total_earnings:.2f}\n"
            f"🏢 Commission (15%): ${commission:.2f}\n"
            f"💸 Already Withdrawn: ${total_withdrawn:.2f}\n\n"
            f"💰 Available: ${available_balance:.2f}\n\n"
            "❌ Minimum withdrawal: $1.00\n\n"
            "Keep selling to increase your balance!",
            reply_markup=reply_markup
        )
        return ConversationHandler.END
    
    # Store available balance for validation
    context.user_data['available_balance'] = available_balance
    context.user_data['total_sales'] = total_sales
    context.user_data['total_earnings'] = total_earnings
    
    await query.edit_message_text(
        f"💰 Request Withdrawal\n\n"
        f"📊 Total Sales: ${total_sales:.2f}\n"
        f"💵 Your Earnings (85%): ${total_earnings:.2f}\n"
        f"💸 Withdrawn: ${total_withdrawn:.2f}\n"
        f"💰 Available: ${available_balance:.2f}\n\n"
        "How much do you want to withdraw? (USD)\n\n"
        f"Enter amount (Min: $1.00, Max: ${available_balance:.2f}):"
    )
    return WITHDRAWAL_GET_AMOUNT

async def leader_receive_withdrawal_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive withdrawal amount and ask for address"""
    amount_text = update.message.text.strip()
    
    try:
        amount = float(amount_text)
        available_balance = context.user_data.get('available_balance', 0)
        
        if amount < 1.0:
            await update.message.reply_text(
                f"❌ Minimum withdrawal is $1.00\n\n"
                f"Available: ${available_balance:.2f}\n"
                "Please enter a valid amount:"
            )
            return WITHDRAWAL_GET_AMOUNT
        
        if amount > available_balance:
            await update.message.reply_text(
                f"❌ Insufficient balance!\n\n"
                f"Available: ${available_balance:.2f}\n"
                f"You entered: ${amount:.2f}\n\n"
                "Please enter a valid amount:"
            )
            return WITHDRAWAL_GET_AMOUNT
        
        # Store the amount
        context.user_data['withdrawal_amount'] = amount
        
        await update.message.reply_text(
            f"💰 Withdrawal Amount: ${amount:.2f}\n\n"
            "Now, please send your USDT BEP20 address:\n\n"
            "(Make sure it's a valid BEP20 address)"
        )
        return WITHDRAWAL_GET_ADDRESS
        
    except ValueError:
        available_balance = context.user_data.get('available_balance', 0)
        await update.message.reply_text(
            f"❌ Invalid amount!\n\n"
            f"Available: ${available_balance:.2f}\n"
            "Please enter a number (e.g., 10.50):"
        )
        return WITHDRAWAL_GET_AMOUNT

async def leader_receive_withdrawal_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive USDT address and create withdrawal request"""
    address = update.message.text.strip()
    
    # Basic validation
    if len(address) < 20 or ' ' in address:
        await update.message.reply_text(
            "❌ Invalid address format.\n\n"
            "Please send a valid USDT BEP20 address:"
        )
        return WITHDRAWAL_GET_ADDRESS
    
    user = update.effective_user
    amount = context.user_data.get('withdrawal_amount', 0)
    
    # Create withdrawal request
    database = get_db()
    withdrawal_data = {
        "user_id": user.id,
        "username": user.username or user.first_name,
        "amount": amount,
        "address": address,
        "status": "pending",
        "created_at": datetime.utcnow()
    }
    result = database.withdrawals.insert_one(withdrawal_data)
    withdrawal_id = result.inserted_id
    
    # Confirm to user
    await update.message.reply_text(
        "✅ Withdrawal Request Submitted\n\n"
        f"💰 Amount: ${amount:.2f}\n"
        f"📍 Address: {address[:20]}...\n\n"
        "Admin will process your request soon.\n"
        "You'll be notified once completed!"
    )
    
    # Forward to admin
    try:
        keyboard = [
            [
                InlineKeyboardButton("✅ Paid", callback_data=f'withdrawal_complete_{withdrawal_id}'),
                InlineKeyboardButton("❌ Reject", callback_data=f'withdrawal_reject_{withdrawal_id}')
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await context.bot.send_message(
            config.OWNER_ID,
            f"💸 Withdrawal Request\n\n"
            f"👤 Seller: {user.first_name}\n"
            f"🆔 ID: {user.id}\n"
            f"📝 {'@' + user.username if user.username else 'No username'}\n"
            f"💰 Amount: ${amount:.2f}\n"
            f"📍 USDT BEP20:\n{address}\n"
            f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Failed to notify admin: {e}")
    
    context.user_data.clear()
    return ConversationHandler.END

async def withdrawal_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel withdrawal"""
    context.user_data.clear()
    await update.message.reply_text("❌ Withdrawal cancelled.")
    return ConversationHandler.END

# ============================================
# ADMIN WITHDRAWAL HANDLERS
# ============================================

async def admin_withdrawal_complete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin marks withdrawal as completed"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != config.OWNER_ID:
        await query.answer("❌ Admin only", show_alert=True)
        return
    
    withdrawal_id = query.data.replace('withdrawal_complete_', '')
    database = get_db()
    
    # Update withdrawal
    withdrawal = database.withdrawals.find_one({"_id": ObjectId(withdrawal_id)})
    if not withdrawal:
        await query.edit_message_text("❌ Withdrawal not found.")
        return
    
    database.withdrawals.update_one(
        {"_id": ObjectId(withdrawal_id)},
        {"$set": {
            "status": "completed",
            "completed_at": datetime.utcnow(),
            "completed_by": query.from_user.id
        }}
    )
    
    await query.edit_message_text(
        f"✅ Withdrawal Completed\n\n"
        f"👤 User ID: {withdrawal['user_id']}\n"
        f"💰 Amount: ${withdrawal['amount']:.2f}\n"
        f"📍 Address: {withdrawal['address'][:30]}..."
    )
    
    # Notify user
    try:
        await context.bot.send_message(
            withdrawal['user_id'],
            f"✅ Withdrawal Completed!\n\n"
            f"💰 ${withdrawal['amount']:.2f} has been sent to:\n"
            f"{withdrawal['address']}\n\n"
            "Thank you for being a seller!"
        )
    except Exception as e:
        logger.error(f"Failed to notify user: {e}")

async def admin_withdrawal_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin rejects withdrawal"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != config.OWNER_ID:
        await query.answer("❌ Admin only", show_alert=True)
        return
    
    withdrawal_id = query.data.replace('withdrawal_reject_', '')
    database = get_db()
    
    withdrawal = database.withdrawals.find_one({"_id": ObjectId(withdrawal_id)})
    if not withdrawal:
        await query.edit_message_text("❌ Withdrawal not found.")
        return
    
    database.withdrawals.update_one(
        {"_id": ObjectId(withdrawal_id)},
        {"$set": {
            "status": "rejected",
            "rejected_at": datetime.utcnow(),
            "rejected_by": query.from_user.id
        }}
    )
    
    await query.edit_message_text(
        f"❌ Withdrawal Rejected\n\n"
        f"👤 User ID: {withdrawal['user_id']}\n"
        f"💰 Amount: ${withdrawal['amount']:.2f}"
    )
    
    # Notify user
    try:
        await context.bot.send_message(
            withdrawal['user_id'],
            "❌ Withdrawal Rejected\n\n"
            "Your withdrawal request was not approved.\n"
            "Please contact support for more information."
        )
    except Exception as e:
        logger.error(f"Failed to notify user: {e}")

# ============================================
# SETUP HANDLERS
# ============================================

def setup_seller_handlers(application):
    """Setup seller application and withdrawal handlers"""
    logger.info("✅ Setting up seller handlers...")
    
    # Seller application conversation
    seller_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(seller_apply_start, pattern='^become_seller$')],
        states={
            SELLER_GET_COUNTRIES: [
                CallbackQueryHandler(seller_confirm_start, pattern='^seller_confirm_start$'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, seller_receive_countries)
            ],
            SELLER_GET_PRICES: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, seller_receive_prices)
            ]
        },
        fallbacks=[
            CallbackQueryHandler(seller_cancel, pattern='^seller_cancel$'),
            CommandHandler('cancel', withdrawal_cancel)
        ],
        allow_reentry=True,
        name="seller_application",
        persistent=False
    )
    application.add_handler(seller_conv)
    
    # Withdrawal conversation
    withdrawal_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(leader_request_withdrawal, pattern='^leader_withdrawal$')],
        states={
            WITHDRAWAL_GET_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, leader_receive_withdrawal_amount)
            ],
            WITHDRAWAL_GET_ADDRESS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, leader_receive_withdrawal_address)
            ]
        },
        fallbacks=[CommandHandler('cancel', withdrawal_cancel)],
        allow_reentry=True,
        name="withdrawal_conversation",
        persistent=False
    )
    application.add_handler(withdrawal_conv)
    
    # Callback handlers
    application.add_handler(CallbackQueryHandler(admin_seller_approve, pattern='^seller_approve_'))
    application.add_handler(CallbackQueryHandler(admin_seller_reject, pattern='^seller_reject_'))
    application.add_handler(CallbackQueryHandler(leader_withdrawal_history, pattern='^leader_withdrawal_history$'))
    application.add_handler(CallbackQueryHandler(admin_withdrawal_complete, pattern='^withdrawal_complete_'))
    application.add_handler(CallbackQueryHandler(admin_withdrawal_reject, pattern='^withdrawal_reject_'))
    
    logger.info("✅ Seller handlers registered")