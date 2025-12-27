"""
Keep Alive + NOWPayments Webhook Handler
"""

from flask import Flask, jsonify, request
from threading import Thread
import logging

logger = logging.getLogger(__name__)

app = Flask(__name__)

@app.route('/')
def home():
    """Health check"""
    return "Bot is alive! ✅"

@app.route('/nowpayments/webhook', methods=['POST'])
def nowpayments_webhook():
    """
    Handle NOWPayments IPN callbacks
    This endpoint receives payment notifications
    """
    try:
        # Import here to avoid circular imports
        from payment_nowpayments import process_ipn_callback
        
        # Get signature from header
        signature = request.headers.get('x-nowpayments-sig', '')
        
        # Get IPN data
        ipn_data = request.json
        
        logger.info(f"📥 IPN received: {ipn_data.get('payment_id')} - Status: {ipn_data.get('payment_status')}")
        
        # Process callback
        success = process_ipn_callback(ipn_data, signature)
        
        if success:
            return jsonify({'status': 'ok'}), 200
        else:
            return jsonify({'status': 'error', 'message': 'Processing failed'}), 400
            
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({'status': 'healthy', 'service': 'telegram-bot'}), 200

def run():
    """Run Flask app"""
    app.run(host='0.0.0.0', port=8080, debug=False)

def keep_alive():
    """Start Flask server in background thread"""
    t = Thread(target=run)
    t.daemon = True
    t.start()
    logger.info("✅ Keep-alive server started on port 8080")
    logger.info("✅ Webhook endpoint: /nowpayments/webhook")