#!/usr/bin/env python3
# coding: utf-8

import os
import json
import time
import threading
import queue
import logging
from datetime import datetime
from functools import wraps
from io import BytesIO

import replicate
import requests
from telegram import (
    Bot,
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup
)
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    CallbackContext,
    CallbackQueryHandler
)

import config  # Здесь должны быть TELEGRAM_BOT_TOKEN, REPLICATE_API_TOKEN, INPUT_DIR, OUTPUT_DIR, LOG_DIR, ADMIN_IDS, FILE_PREFIX, WORKER_COUNT

# ---- Prepare folders ----
os.makedirs(config.INPUT_DIR, exist_ok=True)
os.makedirs(config.OUTPUT_DIR, exist_ok=True)
os.makedirs(config.LOG_DIR, exist_ok=True)

USERS_FILE = "users.json"
if not os.path.exists(USERS_FILE):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump({}, f)

# ---- Logging ----
logging.basicConfig(
    filename=os.path.join(config.LOG_DIR, "requests.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger(__name__)
console = logging.StreamHandler()
console.setLevel(logging.INFO)
logger.addHandler(console)

# ---- Replicate client ----
replicate_client = replicate.Client(api_token=config.REPLICATE_API_TOKEN)

# ---- Task queue ----
task_queue = queue.Queue()

# ---- Language strings (RU/UA/EN) ----
STRINGS = {
    "ask_custom_prompt": {
        "ru": "Введите ваш кастомный промт:",
        "ua": "Введіть ваш кастомний промт:",
        "en": "Enter your custom prompt:"
    },
    "start": {
        "ru": "Привет! Пришли фото, выбери эффект и я применю damage-эффект.",
        "ua": "Привіт! Пришли фото, вибери ефект і я застосую damage-ефект.",
        "en": "Hi! Send a photo, choose an effect and I'll apply a damage effect."
    },
    "choose_effect": {
        "ru": "Выбери эффект:",
        "ua": "Оберіть ефект:",
        "en": "Choose an effect:"
    },
    "processing": {
        "ru": "🔄 В очереди. Обработка может занять несколько секунд — не закрывай чат.",
        "ua": "🔄 В черзі. Обробка може зайняти кілька секунд — не закривай чат.",
        "en": "🔄 Queued. Processing may take a few seconds — don't close the chat."
    },
    "done": {
        "ru": "✅ Готово — держи результат.",
        "ua": "✅ Готово — тримай результат.",
        "en": "✅ Done — here is the result."
    },
    "error": {
        "ru": "❌ Ошибка при обработке. Попробуй ещё раз позже.",
        "ua": "❌ Помилка при обробці. Спробуйте згодом.",
        "en": "❌ Error during processing. Please try again later."
    },
    "no_admin": {
        "ru": "У тебя нет доступа к этой команде.",
        "ua": "У тебе немає доступу до цієї команди.",
        "en": "You don't have access to this command."
    },
    "ask_language": {
        "ru": "Выбери язык / Choose language / Оберіть мову",
        "ua": "Оберіть мову / Choose language / Выберите язык",
        "en": "Choose language / Выберите язык / Оберіть мову"
    },
    "send_photo": {
        "ru": "Отправьте фотографию для редактирования",
        "ua": "Надішліть фотографію для редагування",
        "en": "Send a photo for editing"
    }
}

# ---- Effect prompts mapping ----
EFFECT_PROMPTS = {
    "custom": {
        "label": {"ru": "✏️ Мой промт", "ua": "✏️ Мій промт", "en": "✏️ My prompt"},
        "prompt": None   # у кастомного эффекта нет заранее заданного промта
    },
    "broken": {
        "label": {"ru": "🔨 Разбить", "ua": "🔨 Розбити", "en": "🔨 Break"},
        "prompt": "apply realistic broken damage effect only to the main object, keep background untouched; cracks, chips, realistic high-res"
    },
    "burn": {
        "label": {"ru": "🔥 Обжечь", "ua": "🔥 Обпалити", "en": "🔥 Burn"},
        "prompt": "apply realistic burn and scorch marks to the main object, keep background untouched; charred edges, soot, melted textures, high-res"
    },
    "scratch": {
        "label": {"ru": "⚡ Поцарапать", "ua": "⚡ Подряпати", "en": "⚡ Scratch"},
        "prompt": "apply realistic scratches and scuffs to the main object, keep background untouched; scratched metal, paint damage, high-res"
    },
    "glass": {
        "label": {"ru": "💥 Разбить стекло", "ua": "💥 Розбити скло", "en": "💥 Shatter glass"},
        "prompt": "apply shattered glass and spiderweb cracks to the main object's glass parts; retain original composition and lighting"
    },
    "cut": {
        "label": {"ru": "🪓 Разрубить", "ua": "🪓 Розрізати", "en": "🪓 Chop / Split"},
        "prompt": "apply a cut/cleaved damage effect to the main object, realistic split, exposed insides, keep background"
    }
}

# ---- Utility functions ----
def load_users():
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_users(u):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(u, f, ensure_ascii=False, indent=2)

def get_user_lang(user_id):
    users = load_users()
    s = users.get(str(user_id), {}).get("lang", "ru")
    if s not in ("ru","ua","en"):
        return "ru"
    return s

def set_user_lang(user_id, lang):
    users = load_users()
    data = users.get(str(user_id), {})
    data["lang"] = lang
    users[str(user_id)] = data
    save_users(users)

def register_user(user_id):
    users = load_users()
    if str(user_id) not in users:
        users[str(user_id)] = {"lang": "ru", "first_seen": int(time.time())}
        save_users(users)

def admin_only(func):
    @wraps(func)
    def wrapped(update: Update, context: CallbackContext, *args, **kwargs):
        uid = update.effective_user.id
        if uid not in config.ADMIN_IDS:
            update.message.reply_text(STRINGS["no_admin"][get_user_lang(uid)])
            return
        return func(update, context, *args, **kwargs)
    return wrapped

# ---- Worker ----
def worker_loop():
    while True:
        task = task_queue.get()
        if task is None:
            break
        try:
            process_task(task)
        except Exception as e:
            logger.exception("Error processing task: %s", e)
            try:
                bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
                bot.send_message(chat_id=task["chat_id"], text=STRINGS["error"][get_user_lang(task["user_id"])])
            except Exception:
                pass
        finally:
            task_queue.task_done()

def process_task(task):
    """
    task dict:
      - chat_id
      - user_id
      - input_path
      - effect_key
      - timestamp
    """
    chat_id = task["chat_id"]
    user_id = task["user_id"]
    input_path = task["input_path"]
    effect_key = task["effect_key"]
    lang = get_user_lang(user_id)
    bot = Bot(token=config.TELEGRAM_BOT_TOKEN)

    logger.info("Start processing task for user %s effect=%s file=%s", user_id, effect_key, input_path)

    effect = EFFECT_PROMPTS.get(effect_key)
    if not isinstance(effect, dict) or "prompt" not in effect:
        bot.send_message(chat_id=chat_id, text=STRINGS["error"][lang])
        logger.error("Effect not found or invalid: %s", effect_key)
        return

    # For custom, load user's saved custom_prompt
    if effect_key == "custom":
        users = load_users()
        prompt = users.get(str(user_id), {}).get("custom_prompt")
        if not prompt:
            bot.send_message(chat_id=chat_id, text=STRINGS["error"][lang])
            logger.error("Custom prompt missing for user %s", user_id)
            return
    else:
        prompt = effect["prompt"]

    try:
        with open(input_path, "rb") as img_file:
            output = replicate_client.run(
                "google/nano-banana-pro",
                input={
                    "prompt": prompt,
                    "resolution": "2K",
                    "image_input": [img_file],
                    "aspect_ratio": "1:1",
                    "output_format": "png",
                    "safety_filter_level": "block_only_high"
                }
            )

        # Обработка типичных форматов ответа
        url = None
        if isinstance(output, (list, tuple)) and output:
            url = output[0]
        elif isinstance(output, dict) and "url" in output:
            url = output["url"]
        else:
            # try attribute
            url = getattr(output, "url", None)

        if url:
            r = requests.get(url)
            r.raise_for_status()
            bot.send_photo(chat_id=chat_id, photo=BytesIO(r.content), caption=STRINGS["done"][lang])
        else:
            # Если вернулись raw bytes
            if isinstance(output, (bytes, bytearray)):
                bot.send_photo(chat_id=chat_id, photo=BytesIO(output), caption=STRINGS["done"][lang])
            else:
                bot.send_message(chat_id=chat_id, text=STRINGS["error"][lang])
                logger.error("Replicate returned unexpected output for user %s: %s", user_id, type(output))

    except Exception as e:
        logger.exception("Replicate error: %s", e)
        bot.send_message(chat_id=chat_id, text=STRINGS["error"][lang])
        return

    rec = {
        "time": datetime.utcnow().isoformat() + "Z",
        "user_id": user_id,
        "chat_id": chat_id,
        "input_path": input_path,
        "effect": effect_key,
        "prompt": prompt
    }
    logger.info(json.dumps(rec, ensure_ascii=False))

# ---- Handlers ----
def start(update: Update, context: CallbackContext):
    user = update.effective_user
    register_user(user.id)
    lang = get_user_lang(user.id)

    update.message.reply_text(STRINGS["start"][lang])

    keyboard = [["Generate an image"]]
    reply_markup = ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
        one_time_keyboard=False
    )
    update.message.reply_text("Нажмите кнопку, чтобы начать:", reply_markup=reply_markup)


def generate_image_handler(update: Update, context: CallbackContext):
    show_language_keyboard(update, context)


def show_language_keyboard(update: Update, context: CallbackContext):
    keyboard = [
        [InlineKeyboardButton("🇷🇺 Русский", callback_data="lang:ru"),
         InlineKeyboardButton("🇺🇦 Українська", callback_data="lang:ua"),
         InlineKeyboardButton("🇬🇧 English", callback_data="lang:en")]
    ]
    update.message.reply_text(STRINGS["ask_language"]["ru"], reply_markup=InlineKeyboardMarkup(keyboard))


def lang_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    user = query.from_user
    data = query.data
    if data.startswith("lang:"):
        lang = data.split(":",1)[1]
        set_user_lang(user.id, lang)
        query.answer("OK")
        query.edit_message_text(text="Отправьте изображение для редактирования." if lang=="ru" else ("Надішліть зображення для редагування." if lang=="ua" else "Send an image for editing."))
        # Сообщение о том, что нужно прислать фото
        query.message.reply_text(STRINGS["send_photo"][lang])
    else:
        query.answer()


def photo_handler(update: Update, context: CallbackContext):
    user = update.effective_user
    register_user(user.id)
    lang = get_user_lang(user.id)

    kb = []
    row = []
    for key, meta in EFFECT_PROMPTS.items():
        label = meta["label"].get(lang, meta["label"]["en"])
        row.append(InlineKeyboardButton(label, callback_data=f"effect:{key}"))
        if len(row) == 2:
            kb.append(row)
            row = []
    if row:
        kb.append(row)

    photo = update.message.photo[-1]
    file_id = photo.file_id
    file = context.bot.get_file(file_id)
    filename = f"{config.FILE_PREFIX}_{user.id}_{int(time.time())}.jpg"
    path = os.path.join(config.INPUT_DIR, filename)
    file.download(path)

    users = load_users()
    data = users.get(str(user.id), {})
    data["last_input"] = path
    # Reset any awaiting_custom flag (safe)
    data.pop("awaiting_custom_prompt", None)
    users[str(user.id)] = data
    save_users(users)

    update.message.reply_text(STRINGS["choose_effect"][lang], reply_markup=InlineKeyboardMarkup(kb))


def effect_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    user = query.from_user
    data = query.data
    lang = get_user_lang(user.id)
    if not data.startswith("effect:"):
        query.answer()
        return
    effect_key = data.split(":",1)[1]

    users = load_users()
    u = users.get(str(user.id), {})
    input_path = u.get("last_input")
    if not input_path or not os.path.exists(input_path):
        query.answer()
        query.edit_message_text(
            text="❌ Входное изображение не найдено. Пришли фото ещё раз." if lang=="ru" else ("❌ Вхідне зображення не знайдено. Надішли фото ще раз." if lang=="ua" else "❌ Input image not found. Send a photo again."))
        return

    # If user selected custom -> ask for prompt text
    if effect_key == "custom":
        # mark user as awaiting a custom prompt
        u["awaiting_custom_prompt"] = True
        users[str(user.id)] = u
        save_users(users)

        query.answer()
        try:
            query.edit_message_text(STRINGS["ask_custom_prompt"][lang])
        except Exception:
            # fallback if editing fails
            query.message.reply_text(STRINGS["ask_custom_prompt"][lang])
        return

    task = {
        "chat_id": query.message.chat_id,
        "user_id": user.id,
        "input_path": input_path,
        "effect_key": effect_key,
        "timestamp": time.time()
    }
    task_queue.put(task)
    query.answer()
    query.edit_message_text(text=STRINGS["processing"][lang])


def custom_prompt_handler(update: Update, context: CallbackContext):
    """
    This handler reacts when a user has been marked as awaiting a custom prompt.
    If not awaiting, it simply returns and does nothing (so other handlers can process the message).
    """
    user = update.effective_user
    text = update.message.text.strip()
    users = load_users()
    u = users.get(str(user.id), {})

    # If user isn't awaiting a custom prompt, ignore here
    if not u.get("awaiting_custom_prompt"):
        return

    # Save custom prompt and clear awaiting flag
    u["custom_prompt"] = text
    u.pop("awaiting_custom_prompt", None)
    users[str(user.id)] = u
    save_users(users)

    lang = get_user_lang(user.id)
    last_input = u.get("last_input")
    if not last_input or not os.path.exists(last_input):
        update.message.reply_text(STRINGS["send_photo"][lang])
        return

    # Queue processing with custom prompt
    task = {
        "chat_id": update.message.chat.id,
        "user_id": user.id,
        "input_path": last_input,
        "effect_key": "custom",
        "timestamp": time.time()
    }
    task_queue.put(task)

    update.message.reply_text(STRINGS["processing"][lang])


def help_command(update: Update, context: CallbackContext):
    update.message.reply_text("/start - начать\nОтправь фото - и выбери эффект (кнопки)\nAdmins: /stats /users /balance")


def unknown(update: Update, context: CallbackContext):
    update.message.reply_text("Неизвестная команда. Отправь фото чтобы начать.")


def main():
    worker_threads = []
    for _ in range(getattr(config, "WORKER_COUNT", 2)):
        t = threading.Thread(target=worker_loop, daemon=True)
        t.start()
        worker_threads.append(t)

    updater = Updater(token=config.TELEGRAM_BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    # Команды
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help_command))

    # Callback для языка и эффектов
    dp.add_handler(CallbackQueryHandler(lang_callback, pattern=r"^lang:"))
    dp.add_handler(CallbackQueryHandler(effect_callback, pattern=r"^effect:"))

    # Reply кнопка "Generate an image"
    dp.add_handler(MessageHandler(Filters.text & Filters.regex("^Generate an image$"), generate_image_handler))

    # Custom prompt text handler MUST be added BEFORE generic command handlers,
    # but after the 'Generate an image' specific handler so it doesn't intercept that.
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, custom_prompt_handler))

    # Обработка фото
    dp.add_handler(MessageHandler(Filters.photo, photo_handler))

    # Неизвестные команды
    dp.add_handler(MessageHandler(Filters.command, unknown))

    print("Bot started")
    updater.start_polling()
    updater.idle()

    for _ in worker_threads:
        task_queue.put(None)
    for t in worker_threads:
        t.join()


if __name__ == "__main__":
    main()
