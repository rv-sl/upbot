from flask import Flask
import threading
import logging

app = Flask(__name__)

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

@app.route('/')
def health_check():
    """Simple health check endpoint"""
    return {'status': 'healthy'}, 200

def run_server():
    """Run the Flask server"""
    app.run(host='0.0.0.0', port=8000)

if __name__ == '__main__':
    run_server()
