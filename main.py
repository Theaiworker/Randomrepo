import telebot
from telebot import types
import time
import logging
from collections import deque
import threading

# Bot Configuration
TOKEN = "7652831798:AAEHkC3hpqePMMIoX8D1JLh0pmMwXyK5uyY"
ADMIN_ID = 7893221479
REQUEST_COOLDOWN = 86400  # 24 hours
MAX_WAIT_TIME = 600      # 10 minutes
MAX_MESSAGE_LENGTH = 500
MAINTENANCE_INTERVAL = 300  # 5 minutes

# Logging Setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class ConcurrentDict:
    def __init__(self):
        self._lock = threading.Lock()
        self._data = {}

    def __setitem__(self, key, value):
        with self._lock:
            self._data[key] = value

    def __getitem__(self, key):
        with self._lock:
            return self._data[key]

    def __delitem__(self, key):
        with self._lock:
            del self._data[key]

    def __contains__(self, key):
        with self._lock:
            return key in self._data

    def get(self, key, default=None):
        with self._lock:
            return self._data.get(key, default)

    def items(self):
        with self._lock:
            return list(self._data.items())

    @property
    def data(self):
        with self._lock:
            return self._data.copy()

class UserState:
    def __init__(self):
        self.partner = None
        self.searching = False
        self.last_active = time.time()
        self.pending_request = None
        self.last_request_time = 0
        self.message_count = 0

# Global State
users = ConcurrentDict()
waiting_queue = deque()
active_pairs = ConcurrentDict()
bot = telebot.TeleBot(TOKEN, parse_mode='HTML')

def create_keyboard(buttons, row_width=2):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=row_width)
    markup.add(*[types.KeyboardButton(text) for text in buttons])
    return markup

def main_menu():
    buttons = [
        "🔍 Find Partner",
        "🔄 New Partner",
        "📨 Request Contact",
        "❌ Cancel Chat"
    ]
    return create_keyboard(buttons)

def contact_request_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("✅ Approve", callback_data="approve_contact"),
        types.InlineKeyboardButton("❌ Deny", callback_data="deny_contact")
    )
    return markup

def match_users():
    while len(waiting_queue) >= 2:
        user1 = waiting_queue.popleft()
        user2 = waiting_queue.popleft()

        if user1 == user2 or not validate_user(user1) or not validate_user(user2):
            continue

        try:
            users[user1].partner = user2
            users[user2].partner = user1
            users[user1].searching = False
            users[user2].searching = False
            active_pairs[user1] = user2
            active_pairs[user2] = user1

            welcome_msg = "🎉 Connected with a new partner!\n\nType a message to start chatting."
            bot.send_message(user1, welcome_msg, reply_markup=main_menu())
            bot.send_message(user2, welcome_msg, reply_markup=main_menu())

        except Exception as e:
            logger.error(f"Pairing error: {e}")
            cleanup_pair(user1, user2)

def validate_user(user_id):
    user = users.get(user_id)
    return (user and user.searching and 
            (time.time() - user.last_active <= MAX_WAIT_TIME))

def cleanup_pair(user1_id, user2_id):
    for uid in (user1_id, user2_id):
        if uid in active_pairs:
            del active_pairs[uid]
        if user := users.get(uid):
            user.partner = None
            user.searching = False
            user.pending_request = None

def cleanup_user(user_id):
    try:
        if user_id in waiting_queue:
            waiting_queue.remove(user_id)
        if user_id in active_pairs:
            cleanup_pair(user_id, active_pairs[user_id])
        if user_id in users:
            del users[user_id]
    except Exception as e:
        logger.error(f"Cleanup error for {user_id}: {e}")

@bot.message_handler(commands=['start', 'menu'])
def handle_start(message):
    user_id = message.from_user.id
    if user_id not in users:
        users[user_id] = UserState()

    welcome_text = (
        "👋 Welcome to Anonymous Chat!\n\n"
        "🔒 Chat privately and securely\n"
        "👥 Meet new people\n"
        "📨 Exchange contacts safely\n\n"
        "Use the menu below to get started!"
    )
    bot.reply_to(message, welcome_text, reply_markup=main_menu())

@bot.message_handler(commands=['stats'])
def handle_stats(message):
    if message.from_user.id != ADMIN_ID:
        return bot.reply_to(message, "⚠️ You are not authorized!")

    try:
        active_chat_count = len(active_pairs.data) // 2
        waiting_count = len(waiting_queue)
        total_users = len(users.data)

        stats = (
            "📊 *Bot Statistics*\n\n"
            f"👥 Total Users: `{total_users}`\n"
            f"💬 Active Chats: `{active_chat_count}`\n"
            f"⏳ Users Waiting: `{waiting_count}`\n\n"
            f"🕒 Updated: `{time.strftime('%Y-%m-%d %H:%M:%S')}`"
        )

        bot.reply_to(message, stats, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Stats Error: {e}")
        bot.reply_to(message, "⚠️ Error getting statistics!")

@bot.message_handler(func=lambda m: m.text == "🔍 Find Partner")
def handle_search(message):
    user_id = message.from_user.id
    user = users.get(user_id)

    if not user:
        users[user_id] = UserState()
        user = users[user_id]

    if user.partner:
        return bot.reply_to(message, "⚠️ Please end your current chat first!")

    if user_id in waiting_queue:
        return bot.reply_to(message, "⏳ Already searching for a partner...")

    user.searching = True
    user.last_active = time.time()
    waiting_queue.append(user_id)

    bot.reply_to(message, "🔍 Searching for a chat partner...\nPlease wait.", reply_markup=main_menu())
    match_users()

@bot.message_handler(func=lambda m: m.text == "📨 Request Contact")
def handle_contact_request(message):
    user_id = message.from_user.id
    user = users.get(user_id)

    if not user or not user.partner:
        return bot.reply_to(message, "⚠️ You need to be in an active chat to request contact!")

    partner_id = user.partner
    partner = users.get(partner_id)
    if not partner:
        return bot.reply_to(message, "⚠️ Partner not found!")

    user.pending_request = partner_id
    user.last_request_time = time.time()
    partner.pending_request = user_id

    try:
        bot.send_message(partner_id, 
            "📨 Your chat partner would like to exchange contacts.\nDo you accept?",
            reply_markup=contact_request_menu()
        )
        bot.reply_to(message, "📤 Contact request sent! Waiting for partner's response...")
    except Exception as e:
        logger.error(f"Contact request error: {e}")
        bot.reply_to(message, "⚠️ Failed to send contact request. Please try again.")
        user.pending_request = None
        partner.pending_request = None

@bot.callback_query_handler(func=lambda c: c.data in ["approve_contact", "deny_contact"])
def handle_contact_callback(call):
    user_id = call.from_user.id
    user = users.get(user_id)

    if not user or not user.pending_request:
        return bot.answer_callback_query(call.id, "⚠️ No pending request found!")

    requester_id = user.pending_request
    requester = users.get(requester_id)

    if not requester:
        return bot.answer_callback_query(call.id, "⚠️ Requester not found!")

    if call.data == "approve_contact":
        try:
            user_info = bot.get_chat(user_id)
            requester_info = bot.get_chat(requester_id)

            user_contact = f"@{user_info.username}" if user_info.username else f"ID: {user_id}"
            requester_contact = f"@{requester_info.username}" if requester_info.username else f"ID: {requester_id}"

            bot.send_message(requester_id, f"✅ Contact request approved!\nPartner: {user_contact}")
            bot.send_message(user_id, f"✅ You shared your contact: {requester_contact}")

        except Exception as e:
            logger.error(f"Contact share error: {e}")
            bot.answer_callback_query(call.id, "⚠️ Error sharing contacts!")
            return
    else:
        bot.send_message(requester_id, "❌ Your contact request was denied.")
        bot.answer_callback_query(call.id, "Request denied")

    user.pending_request = None
    requester.pending_request = None

@bot.message_handler(func=lambda m: m.text == "🔄 New Partner")
def handle_new_partner(message):
    user_id = message.from_user.id
    user = users.get(user_id)

    if not user or not user.partner:
        return bot.reply_to(message, "⚠️ You need to be in an active chat to find a new partner!")

    partner_id = user.partner
    try:
        bot.send_message(partner_id, "👋 Your chat partner has left to find someone new.\nTo connect with new partner type 🔍 Find Partner")
    except:
        pass

    cleanup_pair(user_id, partner_id)

    user.searching = True
    user.last_active = time.time()
    waiting_queue.append(user_id)

    bot.reply_to(message, "🔍 Looking for a new chat partner...\nPlease wait.", reply_markup=main_menu())
    match_users()

@bot.message_handler(func=lambda m: m.text == "❌ Cancel Chat")
def handle_cancel(message):
    user_id = message.from_user.id
    user = users.get(user_id)

    if not user:
        return bot.reply_to(message, "⚠️ No active session found!")

    if user.partner:
        partner_id = user.partner
        try:
            bot.send_message(partner_id, "👋 Your chat partner has left the conversation.\nTo connect with new partner type 🔍 Find Partner")
        except:
            pass
        cleanup_pair(user_id, partner_id)
        bot.reply_to(message, "You have ended the chat.\nTo connect with new partner type 🔍 Find Partner", reply_markup=main_menu())
    elif user.searching or user_id in waiting_queue:
        if user_id in waiting_queue:
            waiting_queue.remove(user_id)
        user.searching = False
        bot.reply_to(message, "You have cancelled your search.\nTo connect with new partner type 🔍 Find Partner", reply_markup=main_menu())
    else:
        bot.reply_to(message, "⚠️ You're not in an active chat!", reply_markup=main_menu())

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    user_id = message.from_user.id
    user = users.get(user_id)

    if not user or not user.partner:
        return bot.reply_to(message, 
            "⚠️ You're not in a chat!\nUse 🔍 Find Partner to start chatting.",
            reply_markup=main_menu()
        )

    try:
        photo = message.photo[-1]  # Get highest quality photo
        bot.send_photo(user.partner, photo.file_id)
        user.last_active = time.time()
        user.message_count += 1
    except Exception as e:
        logger.error(f"Photo delivery error: {e}")
        cleanup_pair(user_id, user.partner)
        bot.reply_to(message, "⚠️ Failed to send photo. Chat ended.", reply_markup=main_menu())

@bot.message_handler(content_types=['text'])
def handle_message(message):
    user_id = message.from_user.id
    user = users.get(user_id)

    if not user or not user.partner:
        return bot.reply_to(message, 
            "⚠️ You're not in a chat!",
            reply_markup=main_menu()
        )

    if len(message.text) > MAX_MESSAGE_LENGTH:
        return bot.reply_to(message, f"⚠️ Message too long! Max {MAX_MESSAGE_LENGTH} characters.")

    try:
        bot.send_message(user.partner, message.text)
        user.last_active = time.time()
        user.message_count += 1
    except Exception as e:
        logger.error(f"Message delivery error: {e}")
        cleanup_pair(user_id, user.partner)
        bot.reply_to(message, "⚠️ Failed to send message. Chat ended.", reply_markup=main_menu())

def maintenance_task():
    while True:
        try:
            current_time = time.time()
            for uid, user in list(users.items()):
                if current_time - user.last_active > MAX_WAIT_TIME:
                    cleanup_user(uid)
            time.sleep(MAINTENANCE_INTERVAL)
        except Exception as e:
            logger.error(f"Maintenance error: {e}")

if __name__ == '__main__':
    logger.info("🚀 Bot Started")
    maintenance_thread = threading.Thread(target=maintenance_task, daemon=True)
    maintenance_thread.start()

    while True:
        try:
            bot.infinity_polling(timeout=10, long_polling_timeout=10)
        except Exception as e:
            logger.error(f"Bot error: {e}")
            time.sleep(3)