"""
Session Handler - NON-BLOCKING OTP LISTENING
Uses background tasks to avoid blocking the main bot
"""
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, FloodWaitError
import config
from database import get_db, TelegramSession, Purchase, User
from datetime import datetime
import asyncio
import logging
import re
import time

logger = logging.getLogger(__name__)

# Global dictionary to track OTP listeners and clients
otp_listeners = {}
active_clients = {}

async def get_otp_from_session(session_string, phone_number, user_id, bot):
    """
    NON-BLOCKING OTP listener
    Starts background task and returns immediately
    """
    try:
        logger.info(f"🔍 Starting NON-BLOCKING OTP listener for user {user_id}")
        
        # Start background task (non-blocking)
        asyncio.create_task(
            _otp_listener_background(session_string, phone_number, user_id, bot)
        )
        
        # Return immediately - don't block!
        return {
            'success': True,
            'message': 'OTP listener started in background',
            'otp': None
        }
        
    except Exception as e:
        logger.error(f"❌ Error starting OTP listener: {e}")
        return {
            'success': False,
            'message': f'Error: {str(e)}',
            'otp': None
        }

async def _otp_listener_background(session_string, phone_number, user_id, bot):
    """
    BACKGROUND TASK - Runs independently without blocking bot
    """
    client = None
    try:
        logger.info(f"🎯 Background OTP listener started for user {user_id}")
        
        # ✅ FIXED: Use correct config variable names
        client = TelegramClient(
            StringSession(session_string),
            config.TELEGRAM_API_ID,  # ✅ FIXED
            config.TELEGRAM_API_HASH,  # ✅ FIXED
            connection_retries=3,
            retry_delay=1,
            timeout=15
        )
        
        # Connect with timeout
        await asyncio.wait_for(client.connect(), timeout=20.0)
        
        if not await client.is_user_authorized():
            await bot.send_message(
                user_id,
                "❌ Session expired or not authorized.\nPlease contact support."
            )
            if client.is_connected():
                await client.disconnect()
            return
        
        logger.info(f"✅ Client connected for user {user_id}")
        
        # Store client
        active_clients[user_id] = client
        otp_listeners[user_id] = {'found': False, 'code': None, 'task_active': True}
        
        # Flag to track if we found OTP
        otp_found = False
        
        # ========================================
        # MESSAGE HANDLER - Catches OTP from 777000
        # ========================================
        @client.on(events.NewMessage(from_users=777000))
        async def otp_handler(event):
            nonlocal otp_found
            
            try:
                message_text = event.raw_text
                logger.info(f"📨 Message from 777000: {message_text[:100]}")
                
                # Extract 5-digit OTP
                otp_match = re.search(r'\b(\d{5})\b', message_text)
                
                if otp_match:
                    otp_code = otp_match.group(0)
                    logger.info(f"🎯 ✅ OTP FOUND: {otp_code}")
                    
                    otp_found = True
                    
                    # Update listener status
                    if user_id in otp_listeners:
                        otp_listeners[user_id]['found'] = True
                        otp_listeners[user_id]['code'] = otp_code
                    
                    # Send to user
                    try:
                        await bot.send_message(
                            user_id,
                            f"✅ **LOGIN CODE RECEIVED!**\n\n"
                            f"🔑 **Code:** `{otp_code}`\n\n"
                            f"📱 Phone: `{phone_number}`\n\n"
                            f"**Enter this code in Telegram now!**",
                            parse_mode='Markdown'
                        )
                        logger.info(f"✅ OTP {otp_code} sent to user {user_id}")
                    except Exception as e:
                        logger.error(f"Error sending OTP: {e}")
            
            except Exception as e:
                logger.error(f"Error in otp_handler: {e}")
        
        # Send initial instructions
        try:
            await bot.send_message(
                user_id,
                f"📞 **OTP LISTENER ACTIVE**\n\n"
                f"📱 Phone: `{phone_number}`\n\n"
                f"**Steps:**\n"
                f"1. Open Telegram app\n"
                f"2. Enter phone: `{phone_number}`\n"
                f"3. Click 'Send Code'\n"
                f"4. I'll forward the OTP to you!\n\n"
                f"⏰ Listening for **5 minutes**\n"
                f"🤖 Bot is still active for other users!",
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Error sending instructions: {e}")
        
        # ========================================
        # LISTEN FOR 5 MINUTES (NON-BLOCKING)
        # ========================================
        
        # Keep client running in background
        # Check every 5 seconds for 5 minutes = 60 iterations
        for i in range(60):
            # Don't block - use asyncio.sleep
            await asyncio.sleep(5)
            
            # Check if OTP found
            if otp_found:
                logger.info(f"✅ OTP received after {(i+1)*5}s, keeping alive 30s more")
                await asyncio.sleep(30)  # Grace period
                break
            
            # Status update every minute
            if i > 0 and i % 12 == 0:
                minutes = i // 12
                try:
                    await bot.send_message(
                        user_id,
                        f"⏳ Still listening... ({minutes} min)\n"
                        f"Request the code from Telegram if you haven't!"
                    )
                except:
                    pass
        
        # Timeout or success
        if not otp_found:
            logger.warning(f"⏰ Timeout: No OTP in 5 min for user {user_id}")
            try:
                await bot.send_message(
                    user_id,
                    "⏰ **OTP Listener Stopped**\n\n"
                    "No code received in 5 minutes.\n\n"
                    "**Options:**\n"
                    "1. Try purchasing again\n"
                    "2. Contact support if issue persists\n\n"
                    "@Akash_support_bot"
                )
            except:
                pass
        
    except asyncio.TimeoutError:
        logger.error("⏰ Connection timeout")
        try:
            await bot.send_message(
                user_id,
                "❌ Connection timeout. Please try again."
            )
        except:
            pass
    
    except Exception as e:
        logger.error(f"❌ Background OTP error: {e}")
        import traceback
        traceback.print_exc()
        try:
            await bot.send_message(
                user_id,
                f"❌ Error in OTP listener.\n"
                f"Contact support: @Akash_support_bot"
            )
        except:
            pass
    
    finally:
        # CLEANUP
        await cleanup_client(user_id)

async def cleanup_client(user_id):
    """Clean up client and listener"""
    try:
        # Remove listener flag
        if user_id in otp_listeners:
            otp_listeners[user_id]['task_active'] = False
            del otp_listeners[user_id]
            logger.info(f"🗑️ Removed listener for user {user_id}")
        
        # Disconnect and remove client
        if user_id in active_clients:
            client = active_clients[user_id]
            if client.is_connected():
                try:
                    await asyncio.wait_for(client.disconnect(), timeout=5.0)
                    logger.info(f"🔌 Disconnected client for user {user_id}")
                except Exception as e:
                    logger.debug(f"Disconnect: {e}")
            del active_clients[user_id]
            logger.info(f"🧹 Cleaned up client for user {user_id}")
    
    except Exception as e:
        logger.error(f"Cleanup error: {e}")

async def get_available_sessions_by_country():
    """Get available sessions grouped by country"""
    try:
        countries = TelegramSession.get_available_countries()
        
        grouped = {}
        for country_data in countries:
            country = country_data['_id']
            grouped[country] = {
                'count': country_data['count'],
                'min_price': country_data['min_price']
            }
        
        return grouped
    except Exception as e:
        logger.error(f"Error getting sessions by country: {e}")
        return {}

async def purchase_session(user_id, session_id, purchase_type='session'):
    """Process session purchase"""
    try:
        # Get user
        user = User.get_by_telegram_id(user_id)
        if not user:
            return None, "User not found"
        
        # Get session
        session = TelegramSession.get_by_id(session_id)
        if not session:
            return None, "Session not found"
        
        if session.get('is_sold'):
            return None, "Session already sold"
        
        # Check balance
        if user['balance'] < session['price']:
            return None, f"Insufficient balance. Need ${session['price']:.2f}, have ${user['balance']:.2f}"
        
        # Deduct balance
        User.update_balance(user_id, session['price'], operation='subtract')
        
        # Mark as sold
        TelegramSession.mark_as_sold(session_id, user_id)
        
        # Create purchase record
        Purchase.create(
            user_id=user_id,
            session_id=session_id,
            phone_number=session['phone_number'],
            country=session['country'],
            has_2fa=session.get('has_2fa', False),
            two_fa_password=session.get('two_fa_password'),
            purchase_type=purchase_type
        )
        
        result = {
            'phone': session['phone_number'],
            'country': session['country'],
            'has_2fa': session.get('has_2fa', False),
            'two_fa_password': session.get('two_fa_password'),
            'session_string': session['session_string']
        }
        
        logger.info(f"✅ Purchase completed: User {user_id} bought {session['phone_number']}")
        return result, None
        
    except Exception as e:
        logger.error(f"Error processing purchase: {e}")
        import traceback
        traceback.print_exc()
        return None, str(e)

async def get_user_purchases(user_id):
    """Get all purchases by user"""
    try:
        purchases = Purchase.get_by_user(user_id)
        
        class PurchaseObj:
            def __init__(self, data):
                self.phone_number = data['phone_number']
                self.country = data['country']
                self.has_2fa = data.get('has_2fa', False)
                self.two_fa_password = data.get('two_fa_password')
                self.purchase_type = data.get('purchase_type', 'session')
                self.purchased_at = data['purchased_at']
        
        return [PurchaseObj(p) for p in purchases]
        
    except Exception as e:
        logger.error(f"Error getting purchases: {e}")
        return []

async def verify_session(session_string):
    """Verify if a session is still valid"""
    client = None
    try:
        # ✅ FIXED: Use correct config variable names
        client = TelegramClient(
            StringSession(session_string),
            config.TELEGRAM_API_ID,  # ✅ FIXED
            config.TELEGRAM_API_HASH  # ✅ FIXED
        )
        
        await client.connect()
        is_authorized = await client.is_user_authorized()
        await client.disconnect()
        
        return is_authorized
        
    except Exception as e:
        logger.error(f"Error verifying session: {e}")
        if client and client.is_connected():
            await client.disconnect()
        return False