import os
import re
import asyncio
import logging
import sqlite3
import json
from datetime import datetime, date
from typing import List, Dict, Tuple
import calendar

from aiogram import Bot, Dispatcher, types, Router, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BotCommand  # <-- Импорт BotCommand
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command

from aiogram.enums import ParseMode
from dotenv import load_dotenv

from openai import OpenAI

# ----------------------------- КОНФИГУРАЦИЯ ---------------------------------
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
OPENAI_MODEL = os.getenv('OPENAI_MODEL')

# Устанавливаем таймаут, например, 30 секунд.
TIMEOUT_SECONDS = 30

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENAI_API_KEY,
    timeout=TIMEOUT_SECONDS
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(
    token=TELEGRAM_BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()
router = Router()
dp.include_router(router)

DB_PATH = 'ai_content_bot.db'


# *** БЕЗОПАСНОЕ ХРАНЕНИЕ СОСТОЯНИЯ *** user_states: Dict[int, int] = {} # {user_id: schedule_id} <-- ИСПРАВЛЕНИЕ: Добавление словаря для редактирования
user_states: Dict[int, int] = {}
# ----------------------------- ПРОМПТЫ --------------------------------------
def get_schedule_prompt(next_month_info: dict) -> str:
    month_name = next_month_info['month_name_rus']
    year = next_month_info['year']
    # Приводим даты к формату 'YYYY-MM-DD'
    dates_list = ', '.join([f"'{year}-{next_month_info['month_num']:02d}-{d}'" for d in next_month_info['even_dates']])

    return (
        f"Ты — редактор SMM для компании 'Завод ВОЛГА'. "
        f"Составь расписание постов на {month_name} {year} года. "
        f"КРАЙНЕ ВАЖНО: Расписание должно содержать посты СТРОГО на каждую из следующих дат и ни на какую другую: [{dates_list}]. "
        "НЕ ИСПОЛЬЗУЙ ДРУГИХ ДАТ И НЕ ДУБЛИРУЙ СУЩЕСТВУЮЩИЕ."
        "Список возможных тем: Поздравления с праздниками (Основными праздниками в РФ), напоминание о производстве БТП, "
        "напоминание о производстве шкафов управления, напоминание о производстве АНС, напоминание о производстве блочно-модульных ТП, "
        "напоминание о производстве КНС, Только лучшие комплектующие (Обзор комплектующих, которые применяются на производстве),"
        "Познавательные посты (Советы и т.д.), Пост с реализованными объектами, Посты с производства, Посты про компанию;"
        "Важный принцип составления расписания: в начале месяца должен быть пост о самой компании, также где-то в начале месяца пост с напоминанием о том, что мы производим БТП,"
        "где-то между началом и серединой пост с напоминанием о производстве КНС, в середине месяца пост с напоминанием о производстве шкафов управления,"
        "между серединой и концом месяца пост с напоминанием о производстве блочно-модульных тепловых пунктов, а в конце месяца пост с напоминанием о производстве автоматических насосных станций,"
        "Остальные слоты забивай другими темами, также важно учесть, что пост с советом должен быть 1 раз в месяц и если он, например, относится к БТП, то должен стоять как можно дальше от поста с БТП"
        "для того, чтобы поддерживать интерес к продукции весь месяц."
        "Можно придумывать какие-то другие тематики для постов, но важно учитывать, что аудитория возрастная (Инженеры, примерно 40 лет)."
        "Возвращай СТРОГО JSON-массив, без пояснений, без текста вне JSON. "
        "Пример вывода: [{\"date\": \"2025-10-15\", \"topic\": \"Автоматизация насосных станций\", \"type\": \"информационный\"}, ...]"
    )


PROMPT_POST = (
    "Ты — контент-редактор компании 'Завод ВОЛГА'. Стиль — деловой и понятный. "
    "Целевая аудитория — инженеры и заказчики.\n"
    "Вот примеры прошлых постов (если есть):\n{previous_posts}\n"
    "Создай текст для поста по теме: \"{topic}\" для публикации {date}. "
    "Требования: Для данного поста не нужно чётко соблюдать структуру, однако пост должен соответствовать некоторым требованиям:"
    "Если в тексте есть перечисления, то они идут в таком формате:"
    "[эмодзи Текст]"
    "– текст;"
    "– текст;"
    "..."
    "– текст."
    "В начале поста стоит заголовок и перед ним эмодзи."
    "Заголовки должны выглядеть, например, так: 🚰 Автоматические насосные станции Завода ВОЛГА, 🏭 Завод ВОЛГА – производитель инженерного оборудования полного цикла, 🧰 Стандарты качества и контроль на каждом этапе"
    "В заголовках используй подходящие эмодзи, а не все подряд"
    "Подвал должен выглядеть так:"
    "📞 +7 (927) 015-72-96 (WhatsApp / Telegram)"
    "🌐 zavod-volga.ru"
    "📍 Самара, ул. Авроры, 114Ак2, офис 402"
    "🕘 Часы работы: 9:00−18:00"
    "Разделяются абзацы невидимым символом ''"
    "Никакие хештеги не нужно использовать"
    "Не используй никакие местоимения. Не делай повествование от первого лица. Например, вместо 'Мы производим ' пиши 'Завод ВОЛГА производит'"
    "Длина текста небольшая-средняя, без лонгридов"
    "Использовать необходимо информацию, которую я тебе предоставлял до этого в этом чате и исходя из того, что ты знаешь о Заводе ВОЛГА."
    "В тексте нельзя использовать длинные тире."
)


# ----------------------------- РАБОТА С БД ----------------------------------

def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute('''
    CREATE TABLE IF NOT EXISTS previous_posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        text TEXT,
        uploaded_at TEXT
    )
    ''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS schedules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        schedule_json TEXT,
        created_at TEXT,
        status TEXT
    )
    ''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS generated_posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        schedule_id INTEGER,
        date TEXT,
        topic TEXT,
        post_text TEXT,
        created_at TEXT
    )
    ''')
    con.commit()
    con.close()


def save_previous_posts(text: str):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute('INSERT INTO previous_posts (text, uploaded_at) VALUES (?, ?)', (text, datetime.utcnow().isoformat()))
    con.commit()
    con.close()


def get_all_previous_posts() -> List[str]:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute('SELECT text FROM previous_posts ORDER BY id')
    rows = cur.fetchall()
    con.close()
    return [r[0] for r in rows]


def save_schedule(user_id: int, schedule_json: str, status: str = 'pending') -> int:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute('INSERT INTO schedules (user_id, schedule_json, created_at, status) VALUES (?, ?, ?, ?)',
                (user_id, schedule_json, datetime.utcnow().isoformat(), status))
    schedule_id = cur.lastrowid
    con.commit()
    con.close()
    return schedule_id


def update_schedule_status(schedule_id: int, status: str):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute('UPDATE schedules SET status = ? WHERE id = ?', (status, schedule_id))
    con.commit()
    con.close()


def save_generated_post(user_id: int, schedule_id: int, date: str, topic: str, post_text: str):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        'INSERT INTO generated_posts (user_id, schedule_id, date, topic, post_text, created_at) VALUES (?, ?, ?, ?, ?, ?)',
        (user_id, schedule_id, date, topic, post_text, datetime.utcnow().isoformat()))
    con.commit()
    con.close()


# ----------------------------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ----------------------
def get_next_month_info() -> dict:
    today = date.today()
    if today.month == 12:
        next_month = 1
        next_year = today.year + 1
    else:
        next_month = today.month + 1
        next_year = today.year

    # Получаем название месяца на русском
    month_name = calendar.month_name[next_month]
    # Получаем количество дней
    _, num_days = calendar.monthrange(next_year, next_month)

    # Формируем список четных чисел
    even_dates = [i for i in range(1, num_days + 1) if i % 2 == 0]

    return {
        'month_name_rus': {1: 'Январь', 2: 'Февраль', 3: 'Март', 4: 'Апрель', 5: 'Май', 6: 'Июнь',
                           7: 'Июль', 8: 'Август', 9: 'Сентябрь', 10: 'Октябрь', 11: 'Ноябрь', 12: 'Декабрь'}[
            next_month],
        'month_num': next_month,
        'year': next_year,
        'even_dates': [str(d).zfill(2) for d in even_dates]  # '02', '04', '06', etc.
    }


def try_parse_json(text: str):
    """
    Пытается извлечь и распарсить JSON-массив из строки,
    игнорируя окружающий текст и блоки кода (```json).
    """

    # 1. Сначала удаляем блоки кода (например, ```json)
    text = re.sub(r"```json|```", "", text, flags=re.IGNORECASE).strip()

    # 2. Целенаправленно заменяем неразрывные пробелы (NBSP) на обычные
    text = text.replace('\xa0', ' ')

    # 3. Находим границы JSON-массива [ ... ]
    start_index = text.find('[')
    end_index = text.rfind(']')

    if start_index == -1 or end_index == -1 or end_index <= start_index:
        raise ValueError("Не удалось найти корректные границы JSON-массива в ответе ИИ.")

    # 4. Извлекаем чистый JSON-массив (включая скобки)
    json_content = text[start_index: end_index + 1].strip()

    # 5. Парсим содержимое
    return json.loads(json_content)


def pretty_schedule_text(schedule: List[Dict]) -> str:
    lines = ['📅 <b>Расписание постов:</b>']

    # Символ неразрывного пробела (U+00A0)
    NBSP = '\xa0'

    for item in schedule:
        date = item.get('date')

        # 1. Извлекаем и заменяем все неразрывные пробелы на обычные
        # 2. Очищаем от обычных пробелов с помощью .strip()

        topic = item.get('topic', '')
        topic = topic.replace(NBSP, ' ').strip()

        type_ = item.get('type', '')
        type_ = type_.replace(NBSP, ' ').strip()

        # Если тема пустая после очистки, ставим заглушку
        topic = topic if topic else 'Нет темы'

        lines.append(f"{date} — {topic} ({type_})")
    lines.append('\nНажми ✅ Утвердить или ✏️ Изменить')
    return '\n'.join(lines)


def parse_user_edited_schedule(text: str) -> List[Dict]:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    parsed = []
    for line in lines:
        try:
            # Ищем дату в формате YYYY-MM-DD в начале строки
            match = re.match(r"(\d{4}-\d{2}-\d{2})\s*—\s*(.*)", line)
            if match:
                date_part = match.group(1)
                topic = match.group(2).strip()
            else:
                # Попытка парсинга старым способом, если не сработал новый
                if ' - ' in line:
                    date_part, topic = line.split(' - ', 1)
                else:
                    parts = line.split(' ', 1)
                    date_part = parts[0]
                    topic = parts[1] if len(parts) > 1 else 'Тема'

            date_obj = datetime.fromisoformat(date_part.strip())

            # Удаляем информацию в скобках (тип поста), если она есть
            topic = re.sub(r'\s*\([^)]+\)$', '', topic).strip()

            parsed.append({'date': date_obj.date().isoformat(), 'topic': topic.strip(), 'type': 'ручная'})

        except Exception as e:
            logger.warning(f"Не удалось распарсить строку расписания: '{line}' — {e}")
    return parsed


async def ask_openai_for_schedule() -> Tuple[bool, str]:
    # 1. Получаем контекст о следующем месяце
    next_month_info = get_next_month_info()

    # 2. Генерируем динамический промпт, передавая контекст
    prompt = get_schedule_prompt(next_month_info)

    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=600,
            temperature=0.7,
        )
        text = resp.choices[0].message.content.strip()

        logger.info("Ответ ИИ (сырый текст):\n%s", text)

        return True, text
    except Exception as e:
        logger.exception('Ошибка OpenAI при генерации расписания')
        return False, str(e)


async def ask_openai_for_post(topic: str, date: str, previous_posts: List[str]) -> Tuple[bool, str]:
    prev_concat = '\n---\n'.join(previous_posts[-20:]) if previous_posts else 'Нет прошлых постов.'
    prompt = PROMPT_POST.format(previous_posts=prev_concat, topic=topic, date=date)
    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=600,
            temperature=0.7,
        )

        text = resp.choices[0].message.content.strip()
        return True, text
    except Exception as e:
        logger.exception('Ошибка OpenAI при генерации поста')
        return False, str(e)


# ----------------------------- ОБРАБОТЧИКИ КОМАНД ---------------------------

@router.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Привет! Я — бот, который помогает автоматизировать работу с контентом для компании <b>«Завод ВОЛГА»</b>.\n\n"
        "📂 <b>/upload_posts</b> — загрузи файл с прошлыми постами (.txt), чтобы я понимал стиль компании.\n"
        "🗓️ <b>/generate_schedule</b> — создай расписание постов на выбранный период.\n\n"
        "💡 Сначала загрузите прошлые публикации, а затем запустите генерацию расписания."
    )


@router.message(Command("upload_posts"))
async def cmd_upload_posts(message: types.Message):
    await message.answer("Пришли .txt файл с предыдущими постами (можно один файл, разделённый пустыми строками).")


@router.message(F.document)
async def handle_file_upload(message: types.Message):
    file_info = await bot.get_file(message.document.file_id)
    file_path = file_info.file_path
    file = await bot.download_file(file_path)
    content = file.read().decode("utf-8")
    save_previous_posts(content)
    await message.answer("✅ Файл успешно загружен и сохранён в базе.")


@router.message(Command("generate_schedule"))
async def cmd_generate_schedule(message: types.Message):
    await message.answer("Запрашиваю расписание у ИИ... Подожди минуту.")
    ok, result = await ask_openai_for_schedule()
    if not ok:
        await message.answer(f"Ошибка при запросе расписания: {result}")
        return

    try:
        parsed = try_parse_json(result)
        if isinstance(parsed, dict):
            parsed = [parsed]
    except Exception:
        await message.answer("ИИ вернул непонятный формат. Попробуй позже.")
        return

    json_data = json.dumps(parsed, ensure_ascii=False, indent=2)
    schedule_id = save_schedule(message.from_user.id, json_data)
    pretty = pretty_schedule_text(parsed)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Утвердить", callback_data=f"approve_schedule:{schedule_id}"),
         InlineKeyboardButton(text="✏️ Изменить", callback_data=f"edit_schedule:{schedule_id}")]
    ])
    await message.answer(pretty, reply_markup=kb)


# --- Обработка кнопок “Утвердить” и “Изменить” ---
@router.callback_query(F.data.startswith("approve_schedule"))
async def approve_schedule(callback: types.CallbackQuery):
    schedule_id = int(callback.data.split(":")[1])
    update_schedule_status(schedule_id, "approved")

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT schedule_json FROM schedules WHERE id=?", (schedule_id,))
    row = cur.fetchone()
    con.close()

    if not row:
        await callback.message.answer("Ошибка: не найдено расписание.")
        return

    schedule = json.loads(row[0])
    previous_posts = get_all_previous_posts()

    await callback.message.answer("✅ Расписание утверждено. Начинаю генерацию постов...")

    for item in schedule:
        date, topic = item.get("date"), item.get("topic")
        ok, post_text = await ask_openai_for_post(topic, date, previous_posts)
        if ok:
            save_generated_post(callback.from_user.id, schedule_id, date, topic, post_text)
            await callback.message.answer(f"<b>{date}</b>\nТема: {topic}\n\n{post_text}")
        else:
            await callback.message.answer(f"Ошибка при генерации поста для {topic}: {post_text}")

    await callback.message.answer("✅ Все посты сгенерированы.")


@router.callback_query(F.data.startswith("edit_schedule"))
async def edit_schedule(callback: types.CallbackQuery):
    schedule_id = int(callback.data.split(":")[1])
    await callback.message.answer(
        f"✏️ Пришли отредактированное расписание в формате:\nYYYY-MM-DD — Тема\n(по одному посту на строку)"
    )
    # ИСПРАВЛЕНИЕ: Используем безопасный словарь user_states
    user_states[callback.from_user.id] = schedule_id


@router.message(F.text & (F.text.regexp(r"^\d{4}-\d{2}-\d{2}")))
async def handle_edited_schedule(message: types.Message):
    # ИСПРАВЛЕНИЕ: Извлекаем и удаляем ID расписания из словаря user_states
    schedule_id = user_states.pop(message.from_user.id, None)

    if not schedule_id:
        await message.answer(
            "⚠️ Не удалось определить, какое расписание редактируется. Начните заново с /generate_schedule.")
        return

    parsed = parse_user_edited_schedule(message.text)
    json_data = json.dumps(parsed, ensure_ascii=False, indent=2)
    update_schedule_status(schedule_id, "edited")

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("UPDATE schedules SET schedule_json=? WHERE id=?", (json_data, schedule_id))
    con.commit()
    con.close()

    # --- НОВЫЙ БЛОК: Показываем отредактированное расписание для утверждения ---
    pretty = pretty_schedule_text(parsed)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Утвердить", callback_data=f"approve_schedule:{schedule_id}")]
    ])

    await message.answer(
        pretty,  # Показываем отредактированное расписание
        reply_markup=kb
    )
    await message.answer(
        "✅ **Новое расписание сохранено.** Проверьте его и нажмите «Утвердить», чтобы сгенерировать посты."
    )
    # --------------------------------------------------------------------------


# ----------------------------- ЗАПУСК БОТА ----------------------------------

async def set_commands(bot: Bot):
    """Устанавливает команды для меню в Telegram."""
    commands = [
        BotCommand(command="/start", description="👋 Приветствие и информация"),
        BotCommand(command="/generate_schedule", description="🗓️ Сгенерировать расписание"),
        BotCommand(command="/upload_posts", description="📂 Загрузить прошлые посты")
    ]
    await bot.set_my_commands(commands)


async def main():
    init_db()

    # ИСПРАВЛЕНИЕ: Устанавливаем команды перед началом поллинга
    await set_commands(bot)

    logger.info("Бот запущен. Убедись, что TELEGRAM_BOT_TOKEN и OPENAI_API_KEY заданы.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())