"""
Webhook handler for NOWPayments IPN callbacks - FIXED
"""

from flask import Flask, request, jsonify
import logging
from payment_nowpayments import process_ipn_callback

app = Flask(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@app.route('/nowpayments/webhook', methods=['POST'])
def nowpayments_webhook():
    """Handle NOWPayments IPN callbacks"""
    try:
        # Log full request
        logger.info("=" * 70)
        logger.info("📥 NOWPAYMENTS WEBHOOK RECEIVED")
        logger.info("=" * 70)
        
        # Get signature from header
        signature = request.headers.get('x-nowpayments-sig', '')
        
        if not signature:
            logger.error("❌ No signature header found!")
            logger.error(f"Headers: {dict(request.headers)}")
            return jsonify({'status': 'error', 'message': 'Missing signature'}), 400
        
        # Get IPN data
        ipn_data = request.get_json()
        
        if not ipn_data:
            logger.error("❌ No JSON data in request!")
            return jsonify({'status': 'error', 'message': 'Invalid JSON'}), 400
        
        logger.info(f"📦 IPN Data: {ipn_data}")
        logger.info(f"🔐 Signature: {signature[:20]}...")
        
        # Process callback
        success = process_ipn_callback(ipn_data, signature)
        
        if success:
            logger.info("✅ IPN processed successfully")
            return jsonify({'status': 'ok'}), 200
        else:
            logger.error("❌ IPN processing failed")
            return jsonify({'status': 'error', 'message': 'Processing failed'}), 400
            
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({'status': 'healthy'}), 200

@app.route('/test', methods=['GET'])
def test():
    """Test endpoint to verify webhook is running"""
    return jsonify({
        'status': 'running',
        'endpoint': '/nowpayments/webhook',
        'message': 'Webhook handler is operational'
    }), 200

if __name__ == '__main__':
    logger.info("=" * 70)
    logger.info("🚀 STARTING NOWPAYMENTS WEBHOOK HANDLER")
    logger.info("=" * 70)
    logger.info("📡 Listening on: http://0.0.0.0:5000")
    logger.info("🔗 Webhook URL: /nowpayments/webhook")
    logger.info("❤️  Health check: /health")
    logger.info("🧪 Test endpoint: /test")
    logger.info("=" * 70)
    
    # Run on port 5000
    app.run(host='0.0.0.0', port=5000, debug=False)