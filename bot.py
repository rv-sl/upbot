import os
import logging
import tempfile
import magic
import requests
import time
from PIL import Image
from io import BytesIO
from urllib.parse import urlparse
from telegram import Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext
from concurrent.futures import ThreadPoolExecutor

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Bot configuration from environment variables
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
MAX_FILE_SIZE = 2000 * 1024 * 1024  # 2GB (Telegram's limit)
ALLOWED_USERS = os.getenv('ALLOWED_USERS', '').split(',') if os.getenv('ALLOWED_USERS') else []
RATE_LIMIT = int(os.getenv('RATE_LIMIT', '3'))  # Max downloads per minute per user

# Global variables
user_downloads = {}
executor = ThreadPoolExecutor(max_workers=4)
mime = magic.Magic(mime=True)

def start(update: Update, context: CallbackContext) -> None:
    """Send a message when the command /start is issued."""
    update.message.reply_text(
        'Hi! Send me a direct download URL and I\'ll upload it to Telegram for you.\n'
        'Supported URLs: http/https direct links to files\n'
        'Max file size: 2GB'
    )

def help_command(update: Update, context: CallbackContext) -> None:
    """Send a message when the command /help is issued."""
    update.message.reply_text(
        'How to use this bot:\n'
        '1. Send me a direct download URL (http/https)\n'
        '2. I\'ll download the file and upload it to Telegram\n'
        '\n'
        'Commands:\n'
        '/start - Show welcome message\n'
        '/help - Show this help message\n'
        '\n'
        'Note: The bot supports files up to 2GB in size.'
    )

def is_user_allowed(user_id: int) -> bool:
    """Check if user is allowed to use the bot."""
    return not ALLOWED_USERS or str(user_id) in ALLOWED_USERS

def is_rate_limited(user_id: int) -> bool:
    """Check if user is rate limited."""
    now = time.time()
    if user_id not in user_downloads:
        user_downloads[user_id] = []
    
    # Remove old entries
    user_downloads[user_id] = [t for t in user_downloads[user_id] if now - t < 60]
    
    if len(user_downloads[user_id]) >= RATE_LIMIT:
        return True
    
    user_downloads[user_id].append(now)
    return False

def download_file(url: str, progress_callback=None) -> tuple:
    """Download a file from URL with progress tracking."""
    try:
        with requests.get(url, stream=True, timeout=30) as r:
            r.raise_for_status()
            
            # Check file size
            file_size = int(r.headers.get('content-length', 0))
            if file_size > MAX_FILE_SIZE:
                return None, "File too large (max 2GB)"
            
            # Create temp file
            ext = os.path.splitext(urlparse(url).path)[1]
            with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp_file:
                downloaded = 0
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        tmp_file.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback and file_size > 0:
                            progress = (downloaded / file_size) * 100
                            progress_callback(progress)
                
                # Get MIME type
                tmp_file.seek(0)
                mime_type = mime.from_buffer(tmp_file.read(2048))
                tmp_file.seek(0)
                
                return tmp_file.name, mime_type
    except Exception as e:
        logger.error(f"Download error: {e}")
        return None, str(e)

def generate_thumbnail(file_path: str, mime_type: str) -> BytesIO:
    """Generate thumbnail for media files."""
    try:
        if mime_type.startswith('image/'):
            with Image.open(file_path) as img:
                img.thumbnail((320, 320))
                thumb = BytesIO()
                img.save(thumb, format='JPEG')
                thumb.seek(0)
                return thumb
        elif mime_type.startswith('video/'):
            # For video, we'll just return a generic thumbnail
            thumb = BytesIO()
            with Image.new('RGB', (320, 320), color='gray') as img:
                img.save(thumb, format='JPEG')
            thumb.seek(0)
            return thumb
    except Exception as e:
        logger.error(f"Thumbnail error: {e}")
        return None

def handle_url(update: Update, context: CallbackContext) -> None:
    """Handle the URL message."""
    user_id = update.effective_user.id
    
    if not is_user_allowed(user_id):
        update.message.reply_text("Sorry, you're not authorized to use this bot.")
        return
    
    if is_rate_limited(user_id):
        update.message.reply_text("You're doing too many downloads too fast. Please wait a minute.")
        return
    
    url = update.message.text.strip()
    
    # Validate URL
    if not (url.startswith('http://') or url.startswith('https://')):
        update.message.reply_text("Please send a valid http/https URL.")
        return
    
    # Send initial message
    status_msg = update.message.reply_text("Starting download...")
    
    def progress_callback(progress):
        """Update download progress."""
        context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=status_msg.message_id,
            text=f"Downloading... {int(progress)}%"
        )
    
    # Download the file in a separate thread
    def download_and_upload():
        try:
            file_path, mime_type = download_file(url, progress_callback)
            
            if not file_path:
                context.bot.edit_message_text(
                    chat_id=update.effective_chat.id,
                    message_id=status_msg.message_id,
                    text=f"Error: {mime_type}"
                )
                return
            
            file_size = os.path.getsize(file_path)
            file_name = os.path.basename(urlparse(url).path) or "downloaded_file"
            
            # Generate thumbnail for media files
            thumb = None
            if mime_type.startswith(('image/', 'video/')):
                thumb = generate_thumbnail(file_path, mime_type)
            
            # Update status
            context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=status_msg.message_id,
                text=f"Uploading to Telegram... (File size: {file_size/1024/1024:.2f}MB)"
            )
            
            # Upload based on file type
            with open(file_path, 'rb') as file:
                if mime_type.startswith('image/'):
                    update.effective_chat.send_photo(
                        photo=file,
                        caption=f"Downloaded: {file_name}",
                        thumb=thumb,
                        timeout=300
                    )
                elif mime_type.startswith('video/'):
                    update.effective_chat.send_video(
                        video=file,
                        caption=f"Downloaded: {file_name}",
                        thumb=thumb,
                        timeout=300,
                        supports_streaming=True
                    )
                elif mime_type.startswith('audio/'):
                    update.effective_chat.send_audio(
                        audio=file,
                        caption=f"Downloaded: {file_name}",
                        timeout=300
                    )
                else:
                    update.effective_chat.send_document(
                        document=file,
                        caption=f"Downloaded: {file_name}",
                        thumb=thumb,
                        timeout=300
                    )
            
            # Clean up
            os.unlink(file_path)
            context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=status_msg.message_id
            )
            
        except Exception as e:
            logger.error(f"Error: {e}")
            context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=status_msg.message_id,
                text=f"Error uploading file: {str(e)}"
            )
            if file_path and os.path.exists(file_path):
                os.unlink(file_path)
    
    executor.submit(download_and_upload)

def error_handler(update: Update, context: CallbackContext) -> None:
    """Log errors caused by updates."""
    logger.error(f"Update {update} caused error {context.error}")

def main() -> None:
    """Start the bot."""
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN environment variable not set")
        return
    
    # Create the Updater and pass it your bot's token.
    updater = Updater(TOKEN)

    # Get the dispatcher to register handlers
    dispatcher = updater.dispatcher

    # Register command handlers
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("help", help_command))

    # Register URL handler
    dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_url))

    # Register error handler
    dispatcher.add_error_handler(error_handler)

    # Start the Bot
    updater.start_polling()

    # Run the bot until you press Ctrl-C
    updater.idle()

if __name__ == '__main__':
    main()
