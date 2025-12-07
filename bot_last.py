# Англо-русский обучающий бот для Telegram
# =======================================
# Основной функционал:
# 1. Добавление новых слов с переводом и определением
# 2. Тестирование знаний
# 3. Автоматические напоминания о повторении слов
import os
import re
import logging
import requests
import random
import datetime
from typing import Dict, List, Optional, Any
from dotenv import load_dotenv
from googletrans import Translator as GoogleTranslator
import deepl
from bs4 import BeautifulSoup
from telegram import InlineKeyboardMarkup, InlineKeyboardButton, Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes, JobQueue
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
    logger.warning('BOT_API_KEY не задан в .env. Отправка данных на сервер будет ограничена.')

# Запрос test_mode при запуске
test_input = input("Enable test_mode? (Y/N): ").strip().upper()
TEST_MODE = test_input == "Y"
ADMIN_CHAT_ID = os.getenv('ADMIN_CHAT_ID')
END_TEST_IMAGE = 'end_test.jpg'
BASE_API_URL = os.getenv('BASE_API_URL', 'http://localhost:5000/api/v1')

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===

def fetch_cambridge_definition(word: str) -> str:
    """
    Получает определение слова с Cambridge Dictionary.
    Очищает текст от лишних пробелов и форматирования.
    
    Args:
        word: Английское слово для поиска определения
    
    Returns:
        Очищенное определение или пустая строка, если определение не найдено
    """
    try:
        # Подготавливаем слово для URL (только буквы и дефисы)
        clean_word = re.sub(r'[^a-z\-]', '', word.strip().lower().replace(' ', '-'))
        if not clean_word:
            return ""
            
        url = f"https://dictionary.cambridge.org/dictionary/english/{clean_word}"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        
        # Обрабатываем случай, когда слово не найдено
        if response.status_code == 404:
            logger.info(f"Слово '{word}' не найдено в Cambridge Dictionary")
            return ""
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')
        def_tag = soup.find('div', class_='def ddef_d db')
        if not def_tag:
            logger.info(f"Не найден тег определения для слова '{word}'")
            return ""

        raw = def_tag.get_text()
        clean = re.sub(r'\s+', ' ', raw).strip().rstrip(':.')
        return clean
    except Exception as e:
        logger.warning("Ошибка при получении определения для '%s' из Cambridge Dictionary: %s", word, e)
        return ""

async def get_translations(word: str, src: str, dest: str) -> Dict[str, str]:
    """
    Получает переводы слова через Google Translate и DeepL (если доступен).
    
    Args:
        word: Исходное слово для перевода
        src: Язык исходного слова ('ru' или 'en')
        dest: Язык перевода ('ru' или 'en')
    
    Returns:
        Словарь с переводами от разных сервисов
    """
    translations = {}

    # Google Translate
    try:
        google_res = await google_translator.translate(word, src=src, dest=dest)
        translations['Google'] = google_res.text.strip()
    except Exception as e:
        logger.warning("Ошибка Google Translate для слова '%s': %s", word, e)

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
            logger.warning("Ошибка DeepL для слова '%s': %s", word, e)

    return translations

def send_word_to_database(payload: Dict, chat_id: int) -> bool:
    """
    Отправляет данные слова на сервер.
    
    Args:
        payload: Данные слова для сохранения
        chat_id: ID чата пользователя
    
    Returns:
        True при успешной отправке, False в случае ошибки
    """
    url = f'{BASE_API_URL}/words'
    headers = {
        'X-API-Key': BOT_API_KEY,
        'Content-Type': 'application/json'
    }
    server_payload = {
        'user_id': chat_id,
        'theme': 'General',
        'word': payload['word_en'],
        'translation': payload['word_ru'],
        'definition': payload['definition'],
        'definition_lang': payload['definition_lang']
    }
    
    logger.info(f"Отправка на сервер URL: {url}")
    
    try:
        response = requests.post(url, json=server_payload, headers=headers, timeout=15)
        logger.info(f"Статус ответа: {response.status_code}")
        
        if response.status_code == 401:
            logger.error("Ошибка 401: Неверный или отсутствующий API ключ")
            logger.error("Проверьте, что BOT_API_KEY в .env совпадает с ключом на сервере")
        elif not response.ok:
            logger.error(f"Ошибка сервера {response.status_code}: {response.text}")
            
        response.raise_for_status()
        logger.info("Слово успешно отправлено на сервер")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка при отправке на сервер: {e}")
        return False

async def get_user_words(user_id: int) -> List[Dict[str, Any]]:
    """
    Получает слова пользователя из базы данных.
    
    Args:
        user_id: ID пользователя в Telegram
    
    Returns:
        Список слов с их данными
    """
    url = f"{BASE_API_URL}/words?user_id={user_id}&theme=General"
    headers = {'X-API-Key': BOT_API_KEY}
    
    try:
        logger.info(f"Запрос слов для пользователя {user_id} с URL: {url}")
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        words = response.json()
        
        if isinstance(words, dict) and 'words' in words:
            words_list = words['words']
        else:
            words_list = words
            
        logger.info(f"Получено {len(words_list)} слов для пользователя {user_id}")
        return words_list
    except Exception as e:
        logger.error(f"Ошибка получения слов из БД для пользователя {user_id}: {e}")
        return []

def generate_options(words: List[Dict], correct_value: str, field: str, count: int = 3) -> List[str]:
    """
    Генерирует варианты ответов для теста, выбирая уникальные значения из списка слов.
    
    Args:
        words: Список слов для выбора вариантов
        correct_value: Правильный ответ
        field: Поле для выбора значений ('word' или 'definition')
        count: Количество неправильных вариантов
    
    Returns:
        Список вариантов ответов (всегда 4 элемента)
    """
    if not words or not correct_value:
        return []
    
    # Собираем все значения указанного поля, кроме правильного ответа
    all_values = [
        str(w[field]).strip() 
        for w in words 
        if field in w and w[field] and str(w[field]).strip() != correct_value and len(str(w[field]).strip()) > 1
    ]
    
    # Удаляем дубликаты
    all_values = list(set(all_values))
    
    # Если недостаточно вариантов, создаем заполнители
    if len(all_values) < count:
        all_values.extend([f"Вариант {i+1}" for i in range(count - len(all_values))])
    
    # Перемешиваем и берем нужное количество
    random.shuffle(all_values)
    options = [correct_value] + all_values[:count]
    
    # Перемешиваем варианты и гарантируем 4 варианта
    random.shuffle(options)
    return options[:4]

async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    """
    Отправляет пользователю напоминание о повторении слов.
    
    Args:
        context: Контекст бота с данными о задаче
    """
    job = context.job
    chat_id = job.chat_id if job else (ADMIN_CHAT_ID if ADMIN_CHAT_ID else None)
    
    if not chat_id:
        logger.error("Не удалось определить chat_id для напоминания")
        return
    
    try:
        chat_id = int(chat_id)
    except (ValueError, TypeError):
        logger.error(f"Некорректный chat_id для напоминания: {chat_id}")
        return
    
    # Проверяем, есть ли у пользователя слова для повторения
    try:
        words = await get_user_words(chat_id)
        if not words:
            logger.info(f"У пользователя {chat_id} нет слов для повторения, напоминание не отправлено")
            return
    except Exception as e:
        logger.error(f"Ошибка проверки слов для пользователя {chat_id}: {e}")
        return
    
    keyboard = [
        [InlineKeyboardButton("✅ Проверить знания", callback_data="mode::quiz")]
    ]
    
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text="🌅 Не желаете повторить слова сегодня?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        logger.info(f"Напоминание отправлено пользователю {chat_id}")
    except Exception as e:
        logger.error(f"Ошибка отправки напоминания пользователю {chat_id}: {e}")

# === ФУНКЦИИ РЕЖИМА ТЕСТИРОВАНИЯ ===

async def start_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Запускает режим тестирования знаний пользователя.
    
    Args:
        update: Объект обновления от Telegram
        context: Контекст бота
    """
    chat_id = update.effective_chat.id
    
    # Получаем слова пользователя
    words = await get_user_words(chat_id)
    
    if not words:
        await context.bot.send_message(
            chat_id=chat_id,
            text="У вас пока нет сохраненных слов. Сначала добавьте несколько слов через меню 'Добавить слово'!"
        )
        return
    
    # Перемешиваем слова для случайного выбора
    random.shuffle(words)
    
    # Определяем сколько слов запросить
    total_words = len(words)
    quiz_words = words[:min(40, total_words)]  # Берем максимум 40 слов
    logger.info(f"Сформирован набор из {len(quiz_words)} слов для теста пользователя {chat_id}")
    
    # Формируем вопросы
    questions = []
    
    # Первые 5 слов для перевода (русский -> английский)
    translation_words = [w for w in quiz_words if w.get('word') and w.get('translation')][:5]
    for word in translation_words:
        options = generate_options(quiz_words, word['word'], 'word')
        if len(options) >= 4:  # Убедимся, что есть достаточно вариантов
            questions.append({
                'type': 'translation',
                'word': word['word'],
                'translation': word['translation'],
                'correct': word['word'],
                'options': options
            })
    
    # Следующие 5 слов для определений (английский -> определение)
    definition_words = [
        w for w in quiz_words 
        if w.get('word') and w.get('definition') and w['definition'].strip()
    ][len(translation_words):len(translation_words)+5]
    
    for word in definition_words:
        # Генерируем варианты определений
        options = generate_options(quiz_words, word['definition'], 'definition')
        if len(options) >= 4:  # Убедимся, что есть достаточно вариантов
            questions.append({
                'type': 'definition',
                'word': word['word'],
                'definition': word['definition'],
                'correct': word['definition'],
                'options': options
            })
    
    # Если вопросов меньше 10, используем доступные
    if len(questions) < 10 and len(quiz_words) > len(translation_words) + len(definition_words):
        remaining_words = quiz_words[len(translation_words) + len(definition_words):]
        for i, word in enumerate(remaining_words):
            if len(questions) >= 10:
                break
            
            if i % 2 == 0 and word.get('word') and word.get('translation'):
                options = generate_options(quiz_words, word['word'], 'word')
                if len(options) >= 4:
                    questions.append({
                        'type': 'translation',
                        'word': word['word'],
                        'translation': word['translation'],
                        'correct': word['word'],
                        'options': options[:4]
                    })
            elif word.get('word') and word.get('definition') and word['definition'].strip():
                options = generate_options(quiz_words, word['definition'], 'definition')
                if len(options) >= 4:
                    questions.append({
                        'type': 'definition',
                        'word': word['word'],
                        'definition': word['definition'],
                        'correct': word['definition'],
                        'options': options[:4]
                    })
    
    # Если все еще нет вопросов
    if not questions:
        await context.bot.send_message(
            chat_id=chat_id,
            text="Недостаточно данных для создания теста. Пожалуйста, добавьте больше слов с переводами и определениями."
        )
        logger.warning(f"Не удалось создать вопросы для теста пользователя {chat_id}")
        return
    
    # Перемешиваем вопросы
    random.shuffle(questions)
    
    # Сохраняем состояние теста
    context.user_data['quiz_questions'] = questions
    context.user_data['current_question'] = 0
    context.user_data['quiz_score'] = 0
    context.user_data['mode'] = 'quiz_active'
    
    logger.info(f"Тест начат для пользователя {chat_id} с {len(questions)} вопросами")
    # Отправляем первый вопрос
    await send_question(context, chat_id)

async def send_question(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """
    Отправляет текущий вопрос пользователю.
    
    Args:
        context: Контекст бота
        chat_id: ID чата пользователя
    """
    questions = context.user_data.get('quiz_questions', [])
    current_idx = context.user_data.get('current_question', 0)
    
    if current_idx >= len(questions):
        await finish_quiz(context, chat_id)
        return
    
    question = questions[current_idx]
    
    # Формируем текст вопроса и варианты
    if question['type'] == 'translation':
        text = f"🔤 Как переводится слово:\n\n**{question['translation']}**"
    else:  # definition
        text = f"📖 Что означает слово:\n\n**{question['word']}**"
    
    # Создаем кнопки
    keyboard = []
    for idx, option in enumerate(question['options']):
        display_text = (option[:100] + '...') if len(option) > 100 else option
        callback_data = f"quiz_answer::{current_idx}:{idx}"
        keyboard.append([InlineKeyboardButton(display_text, callback_data=callback_data)])
    
    # Отправляем сообщение
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    except Exception as e:
        # Если текст слишком длинный для Markdown или содержит спецсимволы
        await context.bot.send_message(
            chat_id=chat_id,
            text=text.replace('**', '').replace('*', ''),
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        logger.warning(f"Ошибка при отправке сообщения с Markdown: {e}")

async def handle_quiz_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обрабатывает ответ пользователя на вопрос теста.
    
    Args:
        update: Объект обновления от Telegram
        context: Контекст бота
    """
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    
    # Извлекаем номер вопроса и индекс выбранного варианта
    parts = query.data.split("::")[1].split(":")
    question_idx = int(parts[0])
    option_idx = int(parts[1])
    
    questions = context.user_data.get('quiz_questions', [])
    
    if question_idx >= len(questions):
        await finish_quiz(context, chat_id)
        return
    
    question = questions[question_idx]
    selected_option = question['options'][option_idx]
    is_correct = (selected_option.strip() == question['correct'].strip())
    
    if is_correct:
        context.user_data['quiz_score'] = context.user_data.get('quiz_score', 0) + 1
        feedback = "✅ **Верно!** Отлично!"
    else:
        feedback = f"❌ **Неверно.**\nПравильный ответ: **{question['correct']}**"
    
    # Отправляем обратную связь
    await context.bot.send_message(
        chat_id=chat_id,
        text=feedback,
        parse_mode='Markdown'
    )
    
    # Переходим к следующему вопросу
    context.user_data['current_question'] = question_idx + 1
    
    # Отправляем следующий вопрос или завершаем тест
    if context.user_data['current_question'] < len(questions):
        await send_question(context, chat_id)
    else:
        await finish_quiz(context, chat_id)

async def finish_quiz(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """
    Завершает тест и показывает результаты.
    
    Args:
        context: Контекст бота
        chat_id: ID чата пользователя
    """
    score = context.user_data.get('quiz_score', 0)
    total = len(context.user_data.get('quiz_questions', []))
    
    # Формируем сообщение с результатами
    message = "🎉 **Тест завершен!**\n\n"
    message += f"✅ Правильных ответов: {score} из {total}\n"
    message += "Отличная работа! Ты на шаг ближе к цели!\n"
    message += "💪Регулярная практика приведет к успеху! Увидимся завтра! 🌟"
    
    # Отправляем изображение завершения, если оно существует
    if os.path.exists(END_TEST_IMAGE):
        try:
            with open(END_TEST_IMAGE, 'rb') as photo:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption=message,
                    parse_mode='Markdown'
                )
        except Exception as e:
            logger.error(f"Ошибка отправки изображения завершения: {e}")
            await context.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode='Markdown'
            )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode='Markdown'
        )
    
    # Очищаем данные теста
    for key in ['quiz_questions', 'current_question', 'quiz_score']:
        context.user_data.pop(key, None)
    context.user_data['mode'] = 'idle'
    logger.info(f"Тест завершен для пользователя {chat_id}. Результат: {score}/{total}")

# === ОСНОВНЫЕ ОБРАБОТЧИКИ ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Приветственное сообщение с выбором режима работы.
    
    Args:
        update: Объект обновления от Telegram
        context: Контекст бота
    """
    context.user_data.clear()
    chat_id = update.effective_chat.id

    welcome_path = 'welcome.jpg'
    caption = 'Я помогу вам учить английский! Выберите действие.'
    if os.path.exists(welcome_path):
        try:
            with open(welcome_path, 'rb') as f:
                await context.bot.send_photo(chat_id=chat_id, photo=f, caption=caption)
        except Exception as e:
            logger.error(f"Ошибка отправки приветственного изображения: {e}")
            await context.bot.send_message(chat_id=chat_id, text=caption)
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


async def add_word(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Запрашивает выбор языка добавляемого слова.
    
    Args:
        update: Объект обновления от Telegram
        context: Контекст бота
    """
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
    """
    Обрабатывает следующее слово из очереди для добавления.
    
    Args:
        context: Контекст бота
        chat_id: ID чата пользователя
    """
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
    
    Args:
        chat_id: ID чата пользователя
        context: Контекст бота
        word_en: Английское слово
        word_ru: Русский перевод
    """
    definition_en = fetch_cambridge_definition(word_en)

    context.user_data['pending_word_en'] = word_en
    context.user_data['pending_word_ru'] = word_ru
    context.user_data['cambridge_definition_en'] = definition_en

    def truncate(text: str, max_len=40) -> str:
        """Обрезает текст для отображения в кнопках."""
        return (text[:max_len] + '…') if len(text) > max_len else text

    options = []
    if definition_en:
        en_label = truncate(definition_en)
        options.append((en_label, "orig"))
        try:
            # Переводим определение на русский
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


async def request_rewrite_words(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, early_rewrite: bool = False):
    """
    Запрашивает у пользователя исправленные слова.
    
    Args:
        update: Объект обновления от Telegram
        context: Контекст бота
        chat_id: ID чата пользователя
        early_rewrite: Флаг для переписывания на этапе выбора перевода
    """
    context.user_data['mode'] = 'await_rewrite_words'
    context.user_data['early_rewrite'] = early_rewrite
    
    await context.bot.send_message(
        chat_id=chat_id, 
        text="✏️ Введите исправленное слово или несколько слов через запятую (как при первом вводе):"
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обрабатывает все текстовые сообщения пользователя.
    
    Args:
        update: Объект обновления от Telegram
        context: Контекст бота
    """
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

    # Если режим не распознан, предлагаем начать с начала
    if not mode or mode == 'idle':
        await start(update, context)
        return
    
    # Для всех остальных случаев
    await context.bot.send_message(
        chat_id=chat_id,
        text="Я не понимаю эту команду. Пожалуйста, используйте меню или команду /start"
    )


async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обрабатывает все нажатия кнопок.
    
    Args:
        update: Объект обновления от Telegram
        context: Контекст бота
    """
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat_id

    # Обработка режима викторины
    if data == "mode::quiz":
        await start_quiz(update, context)
        return

    # Обработка завершающего меню
    if data == "post_add":
        context.user_data.clear()
        await add_word(update, context)
        return
    if data == "post_quiz":
        await start_quiz(update, context)
        return
    if data == "post_finish":
        context.user_data.clear()
        await context.bot.send_message(chat_id=chat_id, text="👋 До свидания! Чтобы начать снова, нажмите /start.")
        return

    # Выбор режима
    if data == "mode::add":
        await add_word(update, context)
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
    
    # Неизвестная команда
    logger.warning(f"Получен неизвестный callback_data: {data}")
    await context.bot.send_message(chat_id=chat_id, text="Неизвестная команда. Попробуйте начать сначала с помощью /start")

# === ОСНОВНАЯ ФУНКЦИЯ ЗАПУСКА ===

def main():
    """Основная функция запуска бота."""
    application = Application.builder().token(TOKEN).build()

    # Регистрируем обработчики
    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    # Сначала регистрируем СПЕЦИФИЧЕСКИЕ обработчики callback-запросов
    application.add_handler(CallbackQueryHandler(handle_quiz_answer, pattern=r'^quiz_answer::'))
    
    # Затем регистрируем ОБЩИЙ обработчик callback-запросов
    application.add_handler(CallbackQueryHandler(callback_query_handler))
    
    # Настраиваем JobQueue для напоминаний
    job_queue = application.job_queue
    
    # Тестовый режим: отправляем напоминание через 5 секунд после запуска
    if TEST_MODE and ADMIN_CHAT_ID:
        try:
            admin_chat_id = int(ADMIN_CHAT_ID)
            job_queue.run_once(
                send_reminder, 
                5, 
                chat_id=admin_chat_id,
                name="test_reminder"
            )
            logger.info("Тестовое напоминание будет отправлено через 5 секунд")
        except (ValueError, TypeError) as e:
            logger.error(f"Ошибка настройки тестового режима: некорректный ADMIN_CHAT_ID ({ADMIN_CHAT_ID}): {e}")
    
    # Регулярное напоминание в 20:00 по UTC
    job_queue.run_daily(
        send_reminder,
        time=datetime.time(hour=20, minute=0, second=0, tzinfo=datetime.timezone.utc),
        name="daily_reminder"
    )
    logger.info("Ежедневное напоминание настроено на 20:00 UTC")
    
    logger.info("Бот запущен...")
    try:
        application.run_polling()
    except Exception as e:
        logger.critical(f"Критическая ошибка при запуске бота: {e}")
        raise

if __name__ == '__main__':
    main()