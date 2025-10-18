from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from dotenv import load_dotenv
from googletrans import Translator  # 4.0.2
translator = Translator()
import os
import logging

load_dotenv('.env')

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

TOKEN = os.getenv('TOKEN')



async def google_translate(word: str, src: str, dest: str) -> str:
    try: 
        result = await translator.translate(word, src=src, dest=dest)
        return result.text
    except Exception as e:
        logging.warning("Google translate failed: %s", e)



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
    variants = ['Добавить слово','Проверить свои знания']
    variants.append('Свой вариант')

    # Build inline keyboard
    keyboard = [[InlineKeyboardButton(v, callback_data=f'pick::{v}')] for v in variants]
    await context.bot.send_message(chat_id=chat_id, text='Выберите действие:',
                                       reply_markup=InlineKeyboardMarkup(keyboard))
    # keep mode so callback knows where we are
    context.user_data['mode'] = 'choose_mode'
    return



async def callback_query_handler(update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat_id

    if data.startswith('pick::') and context.user_data.get('mode') == 'choose_translation':
        chosen = data.split('::', 1)[1]

    if data.startswith('pick::') and context.user_data.get('mode') == 'choose_mode':
        chosen = data.split('::', 1)[1]
        if chosen == 'Добавить слово':
            await add_word()



async def handle_text(update, context: ContextTypes.DEFAULT_TYPE):
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
        google_tr = await safe_translate(word, src=src, dest=dest)
        # Try to get yandex only if key provided inside safe_translate
        yandex_tr = ''  # safe_translate returns one string — for brevity we treat it as alt

        variants = [v for v in (google_tr, yandex_tr) if v]
        variants.append('Свой вариант')

        # Build inline keyboard
        keyboard = [[InlineKeyboardButton(v, callback_data=f'pick::{v}')] for v in variants]
        await context.bot.send_message(chat_id=chat_id, text=f'Выберите перевод для «{word}»:',
                                       reply_markup=InlineKeyboardMarkup(keyboard))
        # keep mode so callback knows where we are
        context.user_data['mode'] = 'choose_translation'
        return


async def add_word(update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    chat_id = update.effective_chat.id

    variants = ['Русское слово','Английское слово']

    # Build inline keyboard
    keyboard = [[InlineKeyboardButton(v, callback_data=f'pick::{v}')] for v in variants]
    await context.bot.send_message(chat_id=chat_id, text='Выберите язык ереводимого слова:',
                                       reply_markup=InlineKeyboardMarkup(keyboard))

    context.user_data['mode'] = 'choose_lang'
    return



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