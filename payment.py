from coinbase_commerce.client import Client
from coinbase_commerce.error import CoinbaseError
import config
from database import get_db, Transaction, User
from datetime import datetime

client = Client(api_key=config.COINBASE_API_KEY)

def create_charge(amount_usd, user_id):
    """Create a Coinbase Commerce charge"""
    try:
        charge_info = {
            'name': 'Account Balance Deposit',
            'description': f'Deposit for user ID: {user_id}',
            'local_price': {
                'amount': str(amount_usd),
                'currency': 'USD'
            },
            'pricing_type': 'fixed_price',
            'metadata': {
                'user_id': str(user_id)
            }
        }
        charge = client.charge.create(**charge_info)
        
        # Save transaction to database
        db = get_db()
        transaction = Transaction(
            user_id=user_id,
            amount=amount_usd,
            type='deposit',
            payment_method='crypto',
            status='pending',
            charge_id=charge['id']
        )
        db.add(transaction)
        db.commit()
        db.close()
        
        return {
            'id': charge['id'],
            'hosted_url': charge['hosted_url'],
            'expires_at': charge['expires_at']
        }
    except CoinbaseError as e:
        print(f"Error creating charge: {e}")
        return None

def check_payment_status(charge_id):
    """Check payment status"""
    try:
        charge = client.charge.retrieve(charge_id)
        timeline = charge.get('timeline', [])
        if timeline:
            return timeline[-1]['status']
        return 'pending'
    except CoinbaseError as e:
        print(f"Error checking payment: {e}")
        return None

def process_completed_payment(charge_id):
    """Process completed payment"""
    db = get_db()
    try:
        transaction = db.query(Transaction).filter_by(charge_id=charge_id).first()
        if transaction and transaction.status == 'pending':
            # Update user balance
            user = db.query(User).filter_by(telegram_id=transaction.user_id).first()
            if user:
                user.balance += transaction.amount
                transaction.status = 'completed'
                transaction.updated_at = datetime.utcnow()
                db.commit()
                return True
    except Exception as e:
        print(f"Error processing payment: {e}")
        db.rollback()
    finally:
        db.close()
    return False