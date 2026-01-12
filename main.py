from telethon import TelegramClient
from config import API_ID, API_HASH, BOT_TOKEN
from handlers import register_handlers
import logging

# Configure logging
logging.basicConfig(format='[%(levelname) 5s/%(asctime)s] %(name)s: %(message)s',
                    level=logging.INFO)

def main():
    client = TelegramClient('bot_session', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
    print("Bot started...")
    
    register_handlers(client)
    
    client.run_until_disconnected()

if __name__ == '__main__':
    main()
