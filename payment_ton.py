"""
TON Payment Integration - MongoDB Version
✅ FIXED: Added base64 comment decoding
"""

import logging
import asyncio
import time
import base64  # ✅ ADDED FOR BASE64 DECODING
from typing import Optional, Dict
from datetime import datetime, timedelta
import requests
from database import get_db, Transaction, User

logger = logging.getLogger(__name__)

TONAPI_URL = "https://tonapi.io/v2"
TONCENTER_API = "https://toncenter.com/api/v2"
TONCENTER_API_V3 = "https://toncenter.com/api/v3"
MANUAL_TON_PRICE = None

class TONPayment:
    """Handle TON payments with MongoDB"""
    
    def __init__(self, master_wallet: str, api_key: Optional[str] = None):
        self.master_wallet = master_wallet.strip()
        self.api_key = api_key
        
        logger.info(f"✅ TON Payment initialized with wallet: {self.master_wallet[:10]}...")
        if api_key:
            logger.info(f"✅ Using TonCenter API key: {api_key[:10]}...")
    
    def create_deposit_address(self, user_id: int, amount: float) -> Dict:
        """Create a deposit request - MongoDB version"""
        try:
            ton_amount = self.usd_to_ton(amount)
            
            timestamp = int(time.time())
            payment_memo = f"deposit_{user_id}_{timestamp}"
            
            # Create transaction in MongoDB
            transaction_id = Transaction.create(
                user_id=user_id,
                amount=amount,
                payment_method='ton',
                payment_id=payment_memo
            )
            
            logger.info(f"✅ Created TON transaction {transaction_id} for user {user_id}: ${amount} ({ton_amount} TON)")
            
            return {
                'success': True,
                'wallet_address': self.master_wallet,
                'amount_ton': ton_amount,
                'amount_usd': amount,
                'memo': payment_memo,
                'transaction_id': str(transaction_id),
                'expires_at': timestamp + 3600
            }
            
        except Exception as e:
            logger.error(f"❌ Error creating TON deposit: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def usd_to_ton(self, usd_amount: float) -> float:
        """Convert USD to TON"""
        try:
            if MANUAL_TON_PRICE:
                ton_amount = usd_amount / MANUAL_TON_PRICE
                return round(ton_amount, 2)
            
            response = requests.get(
                'https://api.coingecko.com/api/v3/simple/price',
                params={'ids': 'the-open-network', 'vs_currencies': 'usd'},
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                ton_price = data['the-open-network']['usd']
                ton_amount = usd_amount / ton_price
                return round(ton_amount, 2)
            else:
                return round(usd_amount / 5.50, 2)
                
        except Exception as e:
            logger.error(f"❌ Error getting TON price: {e}")
            return round(usd_amount / 5.50, 2)
    
    def ton_to_usd(self, ton_amount: float) -> float:
        """Convert TON to USD"""
        try:
            if MANUAL_TON_PRICE:
                return round(ton_amount * MANUAL_TON_PRICE, 2)
            
            response = requests.get(
                'https://api.coingecko.com/api/v3/simple/price',
                params={'ids': 'the-open-network', 'vs_currencies': 'usd'},
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                ton_price = data['the-open-network']['usd']
                return round(ton_amount * ton_price, 2)
            else:
                return round(ton_amount * 5.50, 2)
                
        except Exception as e:
            logger.error(f"❌ Error converting TON to USD: {e}")
            return round(ton_amount * 5.50, 2)
    
    async def check_payment(self, payment_memo: str, expected_amount_ton: float) -> Dict:
        """Check if payment has been received"""
        try:
            logger.info(f"🔍 Checking payment: memo={payment_memo}, expected={expected_amount_ton} TON")
            
            # Try TonCenter v2
            result = await self._check_toncenter_v2(payment_memo, expected_amount_ton)
            if result.get('paid'):
                return result
            
            # Try TonCenter v3
            result = await self._check_toncenter_v3(payment_memo, expected_amount_ton)
            if result.get('paid'):
                return result
            
            # Try TonAPI
            result = await self._check_tonapi(payment_memo, expected_amount_ton)
            if result.get('paid'):
                return result
            
            return {
                'success': True,
                'paid': False,
                'message': 'Payment not yet received'
            }
            
        except Exception as e:
            logger.error(f"❌ Error checking TON payment: {e}")
            return {
                'success': False,
                'paid': False,
                'error': str(e)
            }
    
    async def _check_toncenter_v2(self, payment_memo: str, expected_amount_ton: float) -> Dict:
        """Check payment using TonCenter API v2"""
        try:
            url = f"{TONCENTER_API}/getTransactions"
            
            params = {'address': self.master_wallet, 'limit': 100}
            headers = {}
            if self.api_key:
                headers['X-API-Key'] = self.api_key
            
            response = requests.get(url, params=params, headers=headers, timeout=15)
            
            if response.status_code != 200:
                return {'success': False, 'paid': False}
            
            data = response.json()
            
            if not data.get('ok'):
                return {'success': False, 'paid': False}
            
            transactions = data.get('result', [])
            
            for tx in transactions:
                try:
                    in_msg = tx.get('in_msg', {})
                    if not in_msg:
                        continue
                    
                    comment = self._extract_comment(in_msg)
                    
                    if comment and payment_memo in str(comment):
                        value = in_msg.get('value', 0)
                        if isinstance(value, str):
                            value = int(value)
                        amount_ton = value / 1e9
                        
                        if amount_ton >= expected_amount_ton * 0.97:
                            tx_id = tx.get('transaction_id', {})
                            tx_hash = tx_id.get('hash', '') if isinstance(tx_id, dict) else str(tx_id)
                            
                            return {
                                'success': True,
                                'paid': True,
                                'amount_ton': amount_ton,
                                'tx_hash': tx_hash,
                                'timestamp': tx.get('utime', 0),
                                'method': 'TonCenter_v2'
                            }
                
                except Exception as e:
                    continue
            
            return {'success': True, 'paid': False}
            
        except Exception as e:
            return {'success': False, 'paid': False}
    
    async def _check_toncenter_v3(self, payment_memo: str, expected_amount_ton: float) -> Dict:
        """Check payment using TonCenter API v3"""
        try:
            url = f"{TONCENTER_API_V3}/transactions"
            
            params = {'account': self.master_wallet, 'limit': 100}
            headers = {}
            if self.api_key:
                headers['X-API-Key'] = self.api_key
            
            response = requests.get(url, params=params, headers=headers, timeout=15)
            
            if response.status_code != 200:
                return {'success': False, 'paid': False}
            
            data = response.json()
            transactions = data.get('transactions', [])
            
            for tx in transactions:
                try:
                    in_msg = tx.get('in_msg', {})
                    if not in_msg:
                        continue
                    
                    comment = self._extract_comment(in_msg)
                    
                    if comment and payment_memo in str(comment):
                        value = in_msg.get('value', 0)
                        if isinstance(value, str):
                            value = int(value)
                        amount_ton = value / 1e9
                        
                        if amount_ton >= expected_amount_ton * 0.97:
                            return {
                                'success': True,
                                'paid': True,
                                'amount_ton': amount_ton,
                                'tx_hash': tx.get('hash', ''),
                                'method': 'TonCenter_v3'
                            }
                
                except Exception as e:
                    continue
            
            return {'success': True, 'paid': False}
            
        except Exception as e:
            return {'success': False, 'paid': False}
    
    async def _check_tonapi(self, payment_memo: str, expected_amount_ton: float) -> Dict:
        """Check payment using TonAPI"""
        try:
            url = f"{TONAPI_URL}/blockchain/accounts/{self.master_wallet}/transactions"
            headers = {}
            if self.api_key:
                headers['Authorization'] = f'Bearer {self.api_key}'
            
            response = requests.get(url, params={'limit': 100}, headers=headers, timeout=15)
            
            if response.status_code != 200:
                return {'success': False, 'paid': False}
            
            data = response.json()
            transactions = data.get('transactions', [])
            
            for tx in transactions:
                try:
                    in_msg = tx.get('in_msg', {})
                    if not in_msg:
                        continue
                    
                    comment = self._extract_comment(in_msg)
                    
                    if comment and payment_memo in str(comment):
                        value = int(in_msg.get('value', 0))
                        amount_ton = value / 1e9
                        
                        if amount_ton >= expected_amount_ton * 0.97:
                            return {
                                'success': True,
                                'paid': True,
                                'amount_ton': amount_ton,
                                'tx_hash': tx.get('hash', ''),
                                'method': 'TonAPI'
                            }
                
                except Exception as e:
                    continue
            
            return {'success': True, 'paid': False}
            
        except Exception as e:
            return {'success': False, 'paid': False}
    
    def _extract_comment(self, message: dict) -> str:
        """Extract comment from message - ✅ WITH BASE64 DECODING"""
        
        # Try direct text fields first
        msg_data = message.get('msg_data', {})
        if isinstance(msg_data, dict):
            text = msg_data.get('text', '')
            if text:
                decoded = self._try_decode_base64(text)
                return decoded if decoded else text
        
        direct_message = message.get('message', '')
        if direct_message:
            decoded = self._try_decode_base64(direct_message)
            return decoded if decoded else direct_message
        
        decoded = message.get('decoded', {})
        if isinstance(decoded, dict):
            comment = decoded.get('comment', '') or decoded.get('text', '')
            if comment:
                decoded_text = self._try_decode_base64(comment)
                return decoded_text if decoded_text else comment
        
        message_content = message.get('message_content', {})
        if isinstance(message_content, dict):
            text = message_content.get('text', '')
            if text:
                decoded_text = self._try_decode_base64(text)
                return decoded_text if decoded_text else text
        
        body = message.get('body', '')
        if body:
            decoded_text = self._try_decode_base64(body)
            return decoded_text if decoded_text else body
        
        return ''
    
    def _try_decode_base64(self, text: str) -> str:
        """✅ NEW: Try to decode base64 string"""
        if not text:
            return ''
        
        try:
            # Check if it looks like base64
            if all(c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=' for c in text):
                decoded_bytes = base64.b64decode(text)
                decoded_str = decoded_bytes.decode('utf-8')
                
                # Verify it's reasonable text
                if 'deposit_' in decoded_str or decoded_str.isprintable():
                    logger.info(f"🔓 Decoded base64: '{text[:20]}...' → '{decoded_str}'")
                    return decoded_str
        except Exception:
            pass
        
        return ''
    
    async def verify_and_credit_payment(self, transaction_id: str) -> bool:
        """Verify payment and credit user - MongoDB version"""
        try:
            from bson.objectid import ObjectId
            
            logger.info(f"🔍 Verifying TON payment: {transaction_id}")
            
            transaction = None
            database = get_db()
            
            try:
                if len(transaction_id) == 24:
                    obj_id = ObjectId(transaction_id)
                    transaction = database.transactions.find_one({'_id': obj_id})
            except Exception as e:
                logger.warning(f"Could not parse as ObjectId: {e}")
            
            if not transaction:
                try:
                    numeric_id = int(transaction_id)
                    transaction = database.transactions.find_one({'_id': numeric_id})
                except ValueError:
                    logger.error(f"Invalid transaction ID: {transaction_id}")
            
            if not transaction:
                logger.error(f"❌ Transaction {transaction_id} not found")
                return False
            
            if transaction['status'] == 'completed':
                logger.info(f"✅ Transaction {transaction_id} already completed")
                return True
            
            expected_amount_ton = self.usd_to_ton(transaction['amount'])
            payment_memo = transaction['payment_id']
            
            logger.info(f"🔍 Verifying transaction {transaction_id}")
            logger.info(f"   User: {transaction['user_id']}")
            logger.info(f"   Amount: ${transaction['amount']} (~{expected_amount_ton} TON)")
            logger.info(f"   Memo: {payment_memo}")
            
            result = await self.check_payment(payment_memo, expected_amount_ton)
            
            if result.get('paid'):
                success = User.update_balance(transaction['user_id'], transaction['amount'], operation='add')
                
                if success:
                    Transaction.update_status(
                        transaction['_id'],
                        'completed',
                        charge_id=result.get('tx_hash', '')
                    )
                    
                    user = User.get_by_telegram_id(transaction['user_id'])
                    
                    logger.info(f"✅✅✅ PAYMENT VERIFIED AND CREDITED! ✅✅✅")
                    logger.info(f"   Transaction ID: {transaction_id}")
                    logger.info(f"   User: {transaction['user_id']}")
                    logger.info(f"   Amount: ${transaction['amount']}")
                    logger.info(f"   New Balance: ${user['balance']:.2f}")
                    logger.info(f"   TX Hash: {result.get('tx_hash', 'N/A')}")
                    
                    return True
                else:
                    logger.error(f"❌ User {transaction['user_id']} not found")
                    return False
            else:
                logger.info(f"⏳ Payment not yet received for transaction {transaction_id}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Error verifying TON payment: {e}")
            import traceback
            traceback.print_exc()
            return False

# Global TON payment handler
ton_payment = None

def init_ton_payment(master_wallet: str, api_key: Optional[str] = None):
    """Initialize global TON payment handler"""
    global ton_payment
    ton_payment = TONPayment(master_wallet, api_key)
    logger.info(f"✅ TON Payment initialized successfully")
    return ton_payment

def get_ton_payment() -> TONPayment:
    """Get global TON payment handler"""
    if ton_payment is None:
        raise ValueError("TON Payment not initialized. Call init_ton_payment first.")
    return ton_payment