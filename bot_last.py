from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)
from dotenv import load_dotenv
from googletrans import Translator as GoogleTranslator
import deepl
import os
import logging
import requests
from bs4 import BeautifulSoup
import re

# === ИНИЦИАЛИЗАЦИЯ ===
load_dotenv('.env')

google_translator = GoogleTranslator()
DEEPL_API_KEY = os.getenv('DeepL_API_Key')
deepl_translator = deepl.Translator(DEEPL_API_KEY) if DEEPL_API_KEY else None

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv('TOKEN')
if not TOKEN:
    raise RuntimeError('TOKEN не задан в .env')

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===

def fetch_cambridge_definition(word: str) -> str:
    """Получает определение с Cambridge Dictionary и нормализует пробелы."""
    try:
        clean_word = word.strip().lower().replace(' ', '-')
        url = f"https://dictionary.cambridge.org/dictionary/english/{clean_word}"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 404:
            return ""
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')
        def_tag = soup.find('div', class_='def ddef_d db')
        if not def_tag:
            return ""

        raw = def_tag.get_text()
        clean = re.sub(r'\s+', ' ', raw).strip().rstrip(':.')
        return clean
    except Exception as e:
        logger.warning("Cambridge error for '%s': %s", word, e)
        return ""

async def translate_definition_to_russian(definition: str) -> str:
    """Переводит определение на русский через Google Translate."""
    if not definition:
        return ""
    try:
        result = await google_translator.translate(definition, src='en', dest='ru')
        return result.text.strip()
    except Exception as e:
        logger.warning("Не удалось перевести определение: %s", e)
        return ""

async def get_translations(word: str, src: str, dest: str):
    """Получает переводы через Google и DeepL (если доступен)."""
    translations = {}

    # Google Translate
    try:
        google_res = await google_translator.translate(word, src=src, dest=dest)
        translations['Google'] = google_res.text.strip()
    except Exception as e:
        logger.warning("Google Translate error: %s", e)

    # DeepL
    if deepl_translator:
        try:
            target_lang = 'RU' if dest == 'ru' else 'EN-US'
            source_lang = 'RU' if src == 'ru' else 'EN'
            deepl_res = deepl_translator.translate_text(
                word, source_lang=source_lang, target_lang=target_lang
            )
            translations['DeepL'] = deepl_res.text.strip()
        except Exception as e:
            logger.warning("DeepL error: %s", e)

    return translations

# === ОСНОВНЫЕ ОБРАБОТЧИКИ ===

async def start(update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    chat_id = update.effective_chat.id

    welcome_path = 'welcome.jpg'
    caption = 'Я помогу вам учить английский! Выберите действие.'
    if os.path.exists(welcome_path):
        with open(welcome_path, 'rb') as f:
            await context.bot.send_photo(chat_id=chat_id, photo=f, caption=caption)
    else:
        await context.bot.send_message(chat_id=chat_id, text=caption)

    keyboard = [
        [InlineKeyboardButton("Добавить слово", callback_data="mode::add")],
        [InlineKeyboardButton("Проверить знания", callback_data="mode::quiz")]
    ]
    await context.bot.send_message(
        chat_id=chat_id,
        text='Выберите действие:',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data['mode'] = 'choose_mode'


async def add_word(update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    keyboard = [
        [InlineKeyboardButton("Русское слово", callback_data="lang::ru")],
        [InlineKeyboardButton("Английское слово", callback_data="lang::en")]
    ]
    await context.bot.send_message(
        chat_id=chat_id,
        text='Выберите язык добавляемого слова:',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data['mode'] = 'choose_lang'


async def process_next_word(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    words_queue = context.user_data.get('words_queue', [])
    if not words_queue:
        await context.bot.send_message(chat_id=chat_id, text="✅ Все слова обработаны!")
        context.user_data.clear()
        return

    word = words_queue[0]
    context.user_data['current_word'] = word
    src = context.user_data['src']
    dest = context.user_data['dest']

    translations = await get_translations(word, src, dest)
    if not translations:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"Не удалось перевести слово: {word}. Пропускаем."
        )
        context.user_data['words_queue'] = words_queue[1:]
        await process_next_word(context, chat_id)
        return

    unique_variants = list(dict.fromkeys(translations.values()))
    keyboard = []
    for tr in unique_variants:
        keyboard.append([InlineKeyboardButton(tr, callback_data=f"select_trans::{tr}")])
    keyboard.append([InlineKeyboardButton("✏️ Свой вариант", callback_data="select_trans::custom")])

    await context.bot.send_message(
        chat_id=chat_id,
        text=f"Выберите перевод для «{word}»:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data['mode'] = 'selecting_translation'


async def proceed_with_word(chat_id, context, word_en: str, word_ru: str):
    """Предлагает выбрать вариант определения для слова."""
    definition_en = fetch_cambridge_definition(word_en)

    context.user_data['pending_word_en'] = word_en
    context.user_data['pending_word_ru'] = word_ru
    context.user_data['cambridge_definition_en'] = definition_en

    # Усекаем определения до 30–40 символов для кнопок
    def truncate(text: str, max_len=40) -> str:
        return (text[:max_len] + '…') if len(text) > max_len else text

    options = []
    if definition_en:
        en_label = truncate(definition_en)
        options.append((en_label, "orig"))
        try:
            # Синхронный вызов — без await!
            result = google_translator.translate(definition_en, src='en', dest='ru')
            definition_ru = result.text.strip()
            context.user_data['cambridge_definition_ru'] = definition_ru
            ru_label = truncate(definition_ru)
            options.append((ru_label, "trans"))
        except Exception as e:
            logger.warning("Не удалось перевести определение: %s", e)
            context.user_data['cambridge_definition_ru'] = ""
    else:
        context.user_data['cambridge_definition_ru'] = ""

    options.append(("✏️ Своё определение", "custom"))

    keyboard = [
        [InlineKeyboardButton(label, callback_data=f"def_choice::{code}")]
        for label, code in options
    ]
    keyboard.append([InlineKeyboardButton("🔁 Переписать слово", callback_data="action::rewrite")])
    keyboard.append([InlineKeyboardButton("⏭ Пропустить", callback_data="action::skip")])

    msg = f"Слово: **{word_en}**\nПеревод: **{word_ru}**\n\nВыберите определение:"
    if not definition_en:
        msg += "\n\n⚠️ Определение в Cambridge не найдено."

    await context.bot.send_message(
        chat_id=chat_id,
        text=msg,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )
    context.user_data['mode'] = 'choosing_definition'


async def handle_text(update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    mode = context.user_data.get('mode')

    if mode == 'await_rewrite':
        words_queue = context.user_data.get('words_queue', [])
        if words_queue:
            words_queue[0] = text
            context.user_data['words_queue'] = words_queue
        await context.bot.send_message(chat_id=chat_id, text="Слово обновлено! Обрабатываю...")
        await process_next_word(context, chat_id)
        return

    if mode == 'waiting_words':
        words = [w.strip() for w in text.replace('\n', ',').split(',') if w.strip()]
        if not words:
            await context.bot.send_message(chat_id=chat_id, text="Не удалось извлечь слова. Попробуйте снова.")
            return
        context.user_data['words_queue'] = words
        await process_next_word(context, chat_id)
        return

    if mode == 'await_custom_translation':
        word = context.user_data['current_word']
        translation = text
        src = context.user_data['src']
        dest = context.user_data['dest']
        word_en = translation if dest == 'en' else word
        word_ru = word if src == 'ru' else translation
        await proceed_with_word(chat_id, context, word_en, word_ru)
        return

    if mode == 'await_custom_definition':
        word_en = context.user_data['pending_word_en']
        word_ru = context.user_data['pending_word_ru']
        custom_def = text
        payload = {
            'word_en': word_en,
            'word_ru': word_ru,
            'definition': custom_def,
            'definition_lang': 'custom'
        }
        context.user_data['pending_payload'] = payload
        logger.info("Сохранено с пользовательским определением: %s", payload)
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"✅ Слово «{word_en}» сохранено с вашим определением!"
        )
        words_queue = context.user_data.get('words_queue', [])
        context.user_data['words_queue'] = words_queue[1:] if words_queue else []
        await process_next_word(context, chat_id)
        return


async def callback_query_handler(update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat_id

    if data == "mode::add":
        await add_word(update, context)
        return
    if data == "mode::quiz":
        await context.bot.send_message(chat_id=chat_id, text="Режим проверки знаний скоро появится!")
        return

    if data in ("lang::ru", "lang::en"):
        lang = data.split("::")[1]
        context.user_data['src'] = lang
        context.user_data['dest'] = 'en' if lang == 'ru' else 'ru'
        context.user_data['mode'] = 'waiting_words'
        await context.bot.send_message(chat_id=chat_id, text="Введите одно или несколько слов через запятую:")
        return

    if data.startswith("select_trans::"):
        selected = data.split("::", 1)[1]
        word = context.user_data['current_word']
        src = context.user_data['src']
        dest = context.user_data['dest']

        if selected == "custom":
            context.user_data['mode'] = 'await_custom_translation'
            await context.bot.send_message(chat_id=chat_id, text="Введите свой перевод:")
            return
        else:
            translation = selected
            word_en = translation if dest == 'en' else word
            word_ru = word if src == 'ru' else translation
            await proceed_with_word(chat_id, context, word_en, word_ru)
            return

    if data.startswith("def_choice::"):
        choice = data.split("::", 1)[1]
        word_en = context.user_data['pending_word_en']
        word_ru = context.user_data['pending_word_ru']

        if choice == "orig":
            definition = context.user_data.get('cambridge_definition_en', '')
            def_lang = 'en'
        elif choice == "trans":
            definition = context.user_data.get('cambridge_definition_ru', '')
            def_lang = 'ru'
        elif choice == "custom":
            # Этот случай не должен срабатывать здесь — он обрабатывается отдельно
            return
        else:
            return

        payload = {
            'word_en': word_en,
            'word_ru': word_ru,
            'definition': definition,
            'definition_lang': def_lang
        }
        context.user_data['pending_payload'] = payload
        logger.info("Сохранено: %s", payload)
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"✅ Слово «{word_en}» сохранено!"
        )
        words_queue = context.user_data.get('words_queue', [])
        context.user_data['words_queue'] = words_queue[1:] if words_queue else []
        await process_next_word(context, chat_id)
        return

    if data.startswith("action::"):
        action = data.split("::")[1]
        if action == "rewrite":
            context.user_data['mode'] = 'await_rewrite'
            await context.bot.send_message(chat_id=chat_id, text="Введите исправленное слово:")
            return
        elif action == "skip":
            words_queue = context.user_data.get('words_queue', [])
            context.user_data['words_queue'] = words_queue[1:] if words_queue else []
            await context.bot.send_message(chat_id=chat_id, text="⏭ Слово пропущено.")
            await process_next_word(context, chat_id)
            return


def main():
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(CallbackQueryHandler(callback_query_handler))

    logger.info("Бот запущен...")
    application.run_polling()


if __name__ == '__main__':
    main()