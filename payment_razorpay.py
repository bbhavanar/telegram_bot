"""
Razorpay Payment Integration - MongoDB Version
"""

import razorpay
import logging
import config
from database import get_db, Transaction, User
from datetime import datetime

logger = logging.getLogger(__name__)

# Initialize Razorpay client
razorpay_client = razorpay.Client(auth=(config.RAZORPAY_KEY_ID, config.RAZORPAY_KEY_SECRET))

# INR to USD conversion rate
INR_TO_USD_RATE = 0.012

def inr_to_usd(amount_inr):
    """Convert INR to USD"""
    return round(amount_inr * INR_TO_USD_RATE, 2)

def usd_to_inr(amount_usd):
    """Convert USD to INR"""
    return round(amount_usd / INR_TO_USD_RATE, 2)

def create_order(amount_usd, user_id):
    """Create Razorpay order - MongoDB version with MINIMUM ₹10"""
    try:
        # Convert USD to INR
        amount_inr = usd_to_inr(amount_usd)
        
        # ✅ ENFORCE MINIMUM ₹10 INR
        if amount_inr < 10:
            logger.warning(f"⚠️ Amount ₹{amount_inr} is below minimum ₹10")
            return None
        
        # Convert to paise (smallest unit)
        amount_paise = int(amount_inr * 100)
        
        # ✅ Razorpay minimum is ₹1 (100 paise), but we enforce ₹10
        if amount_paise < 1000:  # 1000 paise = ₹10
            logger.error(f"❌ Amount {amount_paise} paise is below ₹10")
            return None
        
        order_data = {
            'amount': amount_paise,
            'currency': 'INR',
            'payment_capture': 1,
            'notes': {
                'user_id': str(user_id),
                'amount_usd': str(amount_usd)
            }
        }
        
        logger.info(f"💳 Creating Razorpay order: ₹{amount_inr} ({amount_paise} paise) for ${amount_usd}")
        
        order = razorpay_client.order.create(data=order_data)
        
        # Save transaction to MongoDB
        transaction_id = Transaction.create(
            user_id=user_id,
            amount=amount_usd,
            payment_method='razorpay',
            amount_inr=amount_inr,
            order_id=order['id']
        )
        
        logger.info(f"✅ Razorpay order created: {order['id']}, Transaction: {transaction_id}")
        
        return {
            'order_id': order['id'],
            'amount': amount_paise,
            'amount_inr': amount_inr,
            'amount_usd': amount_usd,
            'currency': 'INR',
            'key_id': config.RAZORPAY_KEY_ID
        }
        
    except Exception as e:
        logger.error(f"❌ Error creating order: {e}")
        import traceback
        traceback.print_exc()
        return None

def process_payment_success(order_id, payment_id):
    """Process successful payment - MongoDB version"""
    try:
        # Find transaction
        transaction = Transaction.get_by_order_id(order_id)
        
        if not transaction:
            logger.error(f"Transaction not found for order: {order_id}")
            return False
        
        if transaction['status'] == 'completed':
            logger.info(f"Transaction already completed: {order_id}")
            return True
        
        # Update user balance
        success = User.update_balance(transaction['user_id'], transaction['amount'], operation='add')
        
        if not success:
            logger.error(f"User not found: {transaction['user_id']}")
            return False
        
        # Update transaction
        Transaction.update_status(transaction['_id'], 'completed', charge_id=payment_id)
        
        logger.info(f"✅ Payment successful: ${transaction['amount']} for user {transaction['user_id']}")
        return True
        
    except Exception as e:
        logger.error(f"Error processing payment: {e}")
        return False

def process_payment_failed(order_id):
    """Process failed payment - MongoDB version"""
    try:
        transaction = Transaction.get_by_order_id(order_id)
        
        if transaction:
            Transaction.update_status(transaction['_id'], 'failed')
            logger.info(f"Payment failed for order: {order_id}")
        
        return True
        
    except Exception as e:
        logger.error(f"Error processing failed payment: {e}")
        return False

def get_payment_status(payment_id):
    """Get payment status from Razorpay"""
    try:
        payment = razorpay_client.payment.fetch(payment_id)
        return payment['status']
    except Exception as e:
        logger.error(f"Error fetching payment status: {e}")
        return None