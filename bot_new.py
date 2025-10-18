from telegram import ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from googletrans import Translator  # 4.0.2
from bs4 import BeautifulSoup  # 4.14.2
from dotenv import load_dotenv

import requests
import os
import logging
import aiohttp
import deepl

load_dotenv('.env')

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)


DeepL_API_Key = os.getenv('DeepL_API_Key')
deepl_client = deepl.DeepLClient('85704ee5-f2e9-450b-a005-5fc7efa93604:fx')

TOKEN = os.getenv('TOKEN')
translator = Translator()


# --- Helper functions -----------------------------------------------------
async def safe_translate(word: str, src: str, dest: str, sorce: str) -> str:
    """
    Асинхронно переводит слово.
    Сначала пробует Google Translate (через googletrans),
    затем — Yandex (если задан YANDEX_API_KEY).
    Возвращает строку перевода (или пустую строку при ошибке).
    """
    # --- Google Translate ---
    if sorce =='ggl':
        try:
            result = await translator.translate(word, src=src, dest=dest)
            google_text = result.text
        except Exception as e:
            logging.warning("Google translate failed: %s", e)
        return google_text or ""
    # --- DeepL Translate ---
    if sorce=='dl' and deepl_client:
        try:
            if dest== 'en':
                dest='EN-US'
            result = deepl_client.translate_text(word, target_lang=f"{dest}")
            deepl_text= result.text
        except Exception as e:
            logging.warning("DeepL translate failed: %s", e)
        return deepl_text or ""


async def start(update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    # Send a welcome photo (use context.bot.send_photo with a file handle)
    welcome_path = 'welcome.jpg'
    if os.path.exists(welcome_path):
        with open(welcome_path, 'rb') as f:
            await context.bot.send_photo(chat_id=chat_id, photo=f,
                                         caption='Я помогу вам учить английский! Выберите действие.')
    else:
        await context.bot.send_message(chat_id=chat_id,
                                       text='Я помогу вам учить английский! (Изображение не найдено)')

    # Show options
    buttons = [['Добавить слово', 'Проверить свои знания']]
    await context.bot.send_message(chat_id=chat_id,
                                   text='Выберите опцию:',
                                   reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True))


# One unified text handler to keep flow simple
async def handle_text(update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text.strip()

    # Entry point: user chose to add a word
    if text == 'Добавить слово':
        context.user_data.clear()
        context.user_data['mode'] = 'choose_lang'
        buttons = [['Русское слово', 'Английское слово']]
        await context.bot.send_message(chat_id=chat_id,
                                       text='Выберите язык вводимого слова:',
                                       reply_markup=ReplyKeyboardMarkup(buttons, one_time_keyboard=True, resize_keyboard=True))
        return

    # Language choice
    if text in ('Русское слово', 'Английское слово') and context.user_data.get('mode') == 'choose_lang':
        context.user_data['lang_choice'] = text
        context.user_data['mode'] = 'waiting_words'
        await context.bot.send_message(chat_id=chat_id, text='Отправьте слово(а) через запятую:')
        return

    # If we are waiting for words input
    if context.user_data.get('mode') == 'waiting_words':
        words = [w.strip() for w in text.split(',') if w.strip()]
        if not words:
            await context.bot.send_message(chat_id=chat_id, text='Не удалось распознать слова. Попробуйте ещё раз.')
            return

        # Process first word only for simplicity (expandable)
        word = words[0]
        context.user_data['current_word'] = word
        # Determine direction
        if context.user_data.get('lang_choice') == 'Русское слово':
            src, dest = 'ru', 'en'
        else:
            src, dest = 'en', 'ru'
        context.user_data['src'] = src
        context.user_data['dest'] = dest

        # Get translations
        google_tr = await safe_translate(word, src=src, dest=dest, sorce='ggl')
        # Try to get yandex only if key provided inside safe_translate
        deepl_tr = await safe_translate(word, src=src, dest=dest, sorce='dl')  
        
        # safe_translate returns one string — for brevity we treat it as alt

        variants = [v for v in (google_tr, deepl_tr) if v]
        variants.append('Свой вариант')

        # Build inline keyboard
        keyboard = [[InlineKeyboardButton(v, callback_data=f'pick::{v}')] for v in variants]
        await context.bot.send_message(chat_id=chat_id, text=f'Выберите перевод для «{word}»:',
                                       reply_markup=InlineKeyboardMarkup(keyboard))
        # keep mode so callback knows where we are
        context.user_data['mode'] = 'choose_translation'
        return

    # If waiting for user's definition text (after prompting)
    if context.user_data.get('mode') == 'await_definition':
        user_def = text
        payload = context.user_data.get('pending_payload', {})
        payload['Definition'] = user_def or payload.get('Definition', '')

        headers = {'X-API-Key': os.getenv('API_KEY', '')}
        try:
            r = requests.post('http://localhost:5000/words', json=payload, headers=headers, timeout=10)
            r.raise_for_status()
            await context.bot.send_message(chat_id=chat_id, text='Слово успешно добавлено.')
        except Exception as e:
            logging.error('Failed to post word: %s', e)
            await context.bot.send_message(chat_id=chat_id, text='Не удалось отправить данные на сервер.')

        context.user_data.clear()
        return

    # Default fallback
    await context.bot.send_message(chat_id=chat_id, text='Не понимаю. Выберите опцию или нажмите /start.')


async def callback_query_handler(update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat_id

    if data.startswith('pick::') and context.user_data.get('mode') == 'choose_translation':
        chosen = data.split('::', 1)[1]
        word = context.user_data.get('current_word')
        src = context.user_data.get('src')
        dest = context.user_data.get('dest')

        # Build payload
        payload = {
            'UserId': chat_id,
            'RusWord': word if src == 'ru' else (chosen if dest == 'ru' else ''),
            'EngWord': chosen if dest == 'en' else (word if src == 'en' else ''),
            'Definition': ''
        }

        # Try to fetch English definition if target is English (else skip)
        definition = ''
        if dest == 'en':
            try:
                url = f"https://dictionary.cambridge.org/dictionary/english/{word}"
                headers = {'User-Agent': 'Mozilla/5.0'}
                r = requests.get(url, headers=headers, timeout=10)
                r.raise_for_status()
                soup = BeautifulSoup(r.text, 'html.parser')
                defn_tag = soup.find('div', {'class': 'def ddef_d db'})
                definition = defn_tag.get_text(strip=True) if defn_tag else ''
            except Exception as e:
                logging.warning('Cambridge parse failed: %s', e)

        payload['Definition'] = definition

        # Ask user to confirm or provide own definition
        context.user_data['pending_payload'] = payload
        context.user_data['mode'] = 'await_definition'

        text = f"Вы выбрали перевод: {chosen}.\nОпределение (если найдено): {definition or 'не найдено'}.\nДобавьте своё определение или отправьте пустое сообщение, чтобы сохранить найденное."
        await context.bot.send_message(chat_id=chat_id, text=text)
        return

    # Unhandled callback
    await context.bot.send_message(chat_id=chat_id, text='Неожиданный ответ кнопки.')


def main():
    if not TOKEN:
        raise RuntimeError('TOKEN не задан в окружении')

    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(CallbackQueryHandler(callback_query_handler))

    application.run_polling()


if __name__ == '__main__':
    main()
