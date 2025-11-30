# Структура бота для изучения английских слов
# ===========================================
# 1. Инициализация:
#    - Загрузка переменных окружения (.env)
#    - Настройка логгера
#    - Инициализация переводчиков (Google Translate, DeepL)
# 
# 2. Вспомогательные функции:
#    - fetch_cambridge_definition(): получение определения слова с Cambridge Dictionary
#    - translate_definition_to_russian(): перевод определения на русский
#    - get_translations(): получение переводов через Google и DeepL
#    - send_word_to_database(): отправка данных слова на локальный сервер
# 
# 3. Основные обработчики:
#    - start(): приветственное сообщение с выбором режима
#    - add_word(): выбор языка добавляемого слова
#    - handle_text(): обработка текстовых сообщений пользователя
#    - callback_query_handler(): обработка нажатий кнопок
#    - process_next_word(): обработка следующего слова из очереди
#    - handle_word_definition_selection(): выбор определения для слова
#    - request_rewrite_words(): запрос исправленных слов у пользователя
# 
# 4. Поток работы:
#    - Пользователь выбирает режим (добавить слово/проверить знания)
#    - Выбирает язык исходного слова
#    - Вводит слова через запятую
#    - Для каждого слова:
#        * Получает переводы от Google и DeepL
#        * Выбирает перевод или вводит свой
#        * Получает определение из Cambridge Dictionary
#        * Выбирает вариант определения или вводит своё
#        * Данные отправляются на локальный сервер
#    - После обработки всех слов показывается меню дальнейших действий
# 
# 5. Отправка данных на сервер:
#    - При успешном выборе перевода и определения
#    - При вводе пользовательского определения
#    - Формат данных: JSON с полями userId, theme, word, translation, definition, definition_lang
#    - Адрес: http://127.0.0.1:5000/api/v1/words
#    - Требуется заголовок X-API-Key из .env

import os
import re
import logging
import requests
from typing import Dict, Optional, Tuple
from dotenv import load_dotenv
from googletrans import Translator as GoogleTranslator
import deepl
from bs4 import BeautifulSoup
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

# === ИНИЦИАЛИЗАЦИЯ ===
load_dotenv('.env')

google_translator = GoogleTranslator()
DEEPL_API_KEY = os.getenv('DEEPL_API_KEY')
deepl_translator = deepl.Translator(DEEPL_API_KEY) if DEEPL_API_KEY else None

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv('TOKEN')
BOT_API_KEY = os.getenv('BOT_API_KEY')
if not TOKEN:
    raise RuntimeError('TOKEN не задан в .env')
if not BOT_API_KEY:
    logger.warning('BOT_API_KEY не задан в .env. Отправка данных на сервер будет недоступна.')

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

async def get_translations(word: str, src: str, dest: str) -> Dict[str, str]:
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

def send_word_to_database(payload: Dict, chat_id: int) -> bool:
    """
    Отправляет данные слова на локальный сервер.
    Возвращает True при успешной отправке, False в случае ошибки.
    """
    if not BOT_API_KEY:
        logger.error("BOT_API_KEY не задан. Проверьте .env файл.")
        return False
        
    url = 'http://127.0.0.1:5000/api/v1/words'
    headers = {
        'X-API-Key': BOT_API_KEY,
        'Content-Type': 'application/json'
    }
    server_payload = {
        'userId': chat_id,
        'theme': 'General',
        'word': payload['word_en'],
        'translation': payload['word_ru'],
        'definition': payload['definition'],
        'definition_lang': payload['definition_lang']
    }
    
    logger.info(f"Отправка на сервер URL: {url}")
    logger.info(f"Заголовки: {headers}")
    logger.info(f"Данные: {server_payload}")
    
    try:
        response = requests.post(url, json=server_payload, headers=headers, timeout=15)
        logger.info(f"Статус ответа: {response.status_code}")
        logger.info(f"Тело ответа: {response.text[:200]}")  # Первые 200 символов
        
        if response.status_code == 401:
            logger.error("Ошибка 401: Неверный или отсутствующий API ключ")
            logger.error("Проверьте, что BOT_API_KEY в .env совпадает с ключом на сервере")
        elif response.status_code >= 400:
            logger.error(f"Ошибка сервера {response.status_code}: {response.text}")
            
        response.raise_for_status()
        logger.info("Слово успешно отправлено на сервер")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка при отправке на сервер: {e}")
        logger.error(f"URL: {url}")
        logger.error(f"Заголовки: {headers}")
        logger.error(f"Данные: {server_payload}")
        return False

# === ОСНОВНЫЕ ОБРАБОТЧИКИ ===

async def start(update, context: ContextTypes.DEFAULT_TYPE):
    """Приветственное сообщение с выбором режима работы."""
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
    """Запрашивает выбор языка добавляемого слова."""
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
    """Обрабатывает следующее слово из очереди."""
    words_queue = context.user_data.get('words_queue', [])
    if not words_queue:
        keyboard = [
            [InlineKeyboardButton("Добавить слово", callback_data="post_add")],
            [InlineKeyboardButton("Проверить знания", callback_data="post_quiz")],
            [InlineKeyboardButton("Завершить программу", callback_data="post_finish")]
        ]
        await context.bot.send_message(
            chat_id=chat_id,
            text="✅ Все слова добавлены! Хотите сделать что-то ещё?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        context.user_data['mode'] = 'post_actions'
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
    keyboard.append([InlineKeyboardButton("🔁 Переписать слово", callback_data="action::rewrite_early")])

    await context.bot.send_message(
        chat_id=chat_id,
        text=f"Выберите перевод для «{word}»:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data['mode'] = 'selecting_translation'


async def handle_word_definition_selection(chat_id: int, context: ContextTypes.DEFAULT_TYPE, 
                                          word_en: str, word_ru: str):
    """
    Предлагает выбрать вариант определения для слова.
    Получает определение из Cambridge Dictionary и предлагает варианты.
    """
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
            result = await google_translator.translate(definition_en, src='en', dest='ru')
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


async def request_rewrite_words(update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, early_rewrite: bool = False):
    """
    Запрашивает у пользователя исправленные слова.
    early_rewrite=True для переписывания на этапе выбора перевода.
    """
    context.user_data['mode'] = 'await_rewrite_words'
    context.user_data['early_rewrite'] = early_rewrite
    
    await context.bot.send_message(
        chat_id=chat_id, 
        text="✏️ Введите исправленное слово или несколько слов через запятую (как при первом вводе):"
    )


async def handle_text(update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает все текстовые сообщения пользователя."""
    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    mode = context.user_data.get('mode')

    # Обработка переписывания слов
    if mode == 'await_rewrite_words':
        words = [w.strip() for w in text.replace('\n', ',').split(',') if w.strip()]
        if not words:
            await context.bot.send_message(chat_id=chat_id, text="Не удалось извлечь слова. Попробуйте снова.")
            return
        
        # Удаляем текущее слово из очереди
        words_queue = context.user_data.get('words_queue', [])
        if words_queue:
            words_queue = words_queue[1:]
        
        # Добавляем новые слова в начало очереди
        new_queue = words + words_queue
        context.user_data['words_queue'] = new_queue
        
        await context.bot.send_message(
            chat_id=chat_id, 
            text=f"✅ Слова обновлены! Теперь в очереди: {len(new_queue)} слов.\nОбрабатываю следующее слово..."
        )
        
        # Если это раннее переписывание (на этапе перевода), возвращаемся к выбору перевода
        if context.user_data.get('early_rewrite', False):
            context.user_data['early_rewrite'] = False
            await process_next_word(context, chat_id)
        else:
            # Иначе продолжаем с определением для первого нового слова
            await process_next_word(context, chat_id)
        return

    # Прием слов для добавления
    if mode == 'waiting_words':
        words = [w.strip() for w in text.replace('\n', ',').split(',') if w.strip()]
        if not words:
            await context.bot.send_message(chat_id=chat_id, text="Не удалось извлечь слова. Попробуйте снова.")
            return
        context.user_data['words_queue'] = words
        await process_next_word(context, chat_id)
        return

    # Прием пользовательского перевода
    if mode == 'await_custom_translation':
        word = context.user_data['current_word']
        translation = text
        src = context.user_data['src']
        dest = context.user_data['dest']
        word_en = translation if dest == 'en' else word
        word_ru = word if src == 'ru' else translation
        await handle_word_definition_selection(chat_id, context, word_en, word_ru)
        return

    # Прием пользовательского определения
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
        
        # Отправляем данные на сервер
        success = send_word_to_database(payload, chat_id)
        if success:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"✅ Слово «{word_en}» сохранено с вашим определением!"
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ Слово «{word_en}» сохранено локально, но не отправлено на сервер."
            )
            logger.error("Не удалось отправить слово с пользовательским определением: %s", payload)
        
        # Переходим к следующему слову
        words_queue = context.user_data.get('words_queue', [])
        context.user_data['words_queue'] = words_queue[1:] if words_queue else []
        await process_next_word(context, chat_id)
        return


async def callback_query_handler(update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает все нажатия кнопок."""
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat_id

    # Обработка завершающего меню
    if data == "post_add":
        context.user_data.clear()
        await add_word(update, context)
        return
    if data == "post_quiz":
        await context.bot.send_message(chat_id=chat_id, text="🧠 Режим проверки знаний скоро появится!")
        # Показываем то же меню снова
        keyboard = [
            [InlineKeyboardButton("Добавить слово", callback_data="post_add")],
            [InlineKeyboardButton("Проверить знания", callback_data="post_quiz")],
            [InlineKeyboardButton("Завершить программу", callback_data="post_finish")]
        ]
        await context.bot.send_message(
            chat_id=chat_id,
            text="Что дальше?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    if data == "post_finish":
        context.user_data.clear()
        await context.bot.send_message(chat_id=chat_id, text="👋 До свидания! Чтобы начать снова, нажмите /start.")
        return

    # Выбор режима
    if data == "mode::add":
        await add_word(update, context)
        return
    if data == "mode::quiz":
        await context.bot.send_message(chat_id=chat_id, text="🧠 Режим проверки знаний скоро появится!")
        return

    # Выбор языка
    if data in ("lang::ru", "lang::en"):
        lang = data.split("::")[1]
        context.user_data['src'] = lang
        context.user_data['dest'] = 'en' if lang == 'ru' else 'ru'
        context.user_data['mode'] = 'waiting_words'
        await context.bot.send_message(chat_id=chat_id, text="🔤 Введите одно или несколько слов через запятую:")
        return

    # Выбор перевода
    if data.startswith("select_trans::"):
        selected = data.split("::", 1)[1]
        word = context.user_data['current_word']
        src = context.user_data['src']
        dest = context.user_data['dest']

        if selected == "custom":
            context.user_data['mode'] = 'await_custom_translation'
            await context.bot.send_message(chat_id=chat_id, text="✏️ Введите свой перевод:")
            return
        else:
            translation = selected
            word_en = translation if dest == 'en' else word
            word_ru = word if src == 'ru' else translation
            await handle_word_definition_selection(chat_id, context, word_en, word_ru)
            return

    # Выбор определения
    if data.startswith("def_choice::"):
        choice = data.split("::", 1)[1]
        word_en = context.user_data['pending_word_en']
        word_ru = context.user_data['pending_word_ru']

        if choice == "custom":
            context.user_data['mode'] = 'await_custom_definition'
            await context.bot.send_message(
                chat_id=chat_id,
                text="✏️ Введите своё определение для слова:"
            )
            return
            
        if choice == "orig":
            definition = context.user_data.get('cambridge_definition_en', '')
            def_lang = 'en'
        elif choice == "trans":
            definition = context.user_data.get('cambridge_definition_ru', '')
            def_lang = 'ru'
        else:
            return

        payload = {
            'word_en': word_en,
            'word_ru': word_ru,
            'definition': definition,
            'definition_lang': def_lang
        }
        
        # Отправляем данные на сервер
        success = send_word_to_database(payload, chat_id)
        if success:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"✅ Слово «{word_en}» сохранено!"
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ Слово «{word_en}» сохранено локально, но не отправлено на сервер."
            )
            logger.error("Не удалось отправить слово: %s", payload)
        
        words_queue = context.user_data.get('words_queue', [])
        context.user_data['words_queue'] = words_queue[1:] if words_queue else []
        await process_next_word(context, chat_id)
        return

    # Действия с словом
    if data.startswith("action::"):
        action = data.split("::")[1]
        
        if action == "rewrite_early":
            await request_rewrite_words(update, context, chat_id, early_rewrite=True)
            return
            
        if action == "rewrite":
            await request_rewrite_words(update, context, chat_id)
            return
            
        elif action == "skip":
            words_queue = context.user_data.get('words_queue', [])
            if words_queue:
                skipped_word = words_queue[0]
                context.user_data['words_queue'] = words_queue[1:]
                await context.bot.send_message(chat_id=chat_id, text=f"⏭ Слово «{skipped_word}» пропущено.")
            await process_next_word(context, chat_id)
            return


def main():
    """Основная функция запуска бота."""
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(CallbackQueryHandler(callback_query_handler))

    logger.info("Бот запущен...")
    application.run_polling()


if __name__ == '__main__':
    main()