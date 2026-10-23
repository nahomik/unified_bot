import os
import json
import logging
import sys
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ConversationHandler, ContextTypes
import gspread

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [BackChecker] %(message)s")
logger = logging.getLogger("BackChecker")

TOKEN = os.getenv("BACK_CHECKER_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")

def parse_creds(google_creds_json):
    if not google_creds_json:
        return None, "Empty credentials"
    google_creds_json = google_creds_json.strip()
    if google_creds_json.startswith("'") and google_creds_json.endswith("'"):
        google_creds_json = google_creds_json[1:-1].strip()
    try:
        data = json.loads(google_creds_json)
        if isinstance(data, str):
            data = json.loads(data)
        if not isinstance(data, dict):
            return None, "Credentials JSON is not a dictionary"
        if "private_key" in data and isinstance(data["private_key"], str):
            data["private_key"] = data["private_key"].replace("\\n", "\n")
        return data, None
    except Exception as e:
        return None, f"JSON parse error: {e}"

def get_sheet():
    google_creds_json = (
        os.getenv("BACK_CHECKER_GOOGLE_CREDS")
        or os.getenv("GOOGLE_CREDENTIALS")
        or os.getenv("GOOGLE_CREDS_JSON")
    )
    if not google_creds_json:
        # Fallback to local service_account.json
        file_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "service_account.json")
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    google_creds_json = f.read()
            except Exception as e:
                logger.error(f"Error reading service_account.json: {e}")

    creds_dict, err = parse_creds(google_creds_json)
    if not creds_dict:
        logger.warning(f"Google credentials error: {err}")
        return None, err

    try:
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/spreadsheets",
        ]
        from google.oauth2.service_account import Credentials
        creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        client = gspread.authorize(creds)

        sheet_id = os.getenv("BACK_CHECKER_SHEET_ID") or "1HUyB6Q5DVz0GbjS7pc-LNPi_aQkqS_lKczerF8R5-QE"
        try:
            return client.open_by_key(sheet_id).sheet1, None
        except Exception as e_key:
            logger.warning(f"Could not open by key {sheet_id}: {e_key}, trying title...")
            return client.open("Back Checker Data").sheet1, None
    except Exception as e:
        logger.error(f"Error connecting to Google Sheet: {type(e).__name__} - {e}")
        return None, f"{type(e).__name__}: {e}"

# States
(LANGUAGE, NAME, SUPPLY_UNIT, BACK_METHOD, SURVEYS, WORKER_SURVEY, WORKER_SURVEY_COUNT, COMMENT, CONFIRM) = range(9)

LANG = {
    'English': {
        'name': "Back Checker\n\nPlease enter your full name:",
        'unit': "Enter the Supply Unit name:",
        'method': "Follow-up method:",
        'methods_list': ['Phone', 'In-person'],
        'surveys': "How many surveys did you do?",
        'worker_survey': "Have you collected Worker survey?",
        'yes_no_list': ['Yes', 'No'],
        'worker_survey_count': "How many worker surveys did you collect?",
        'comment': "Any comments? (Type 'None' if no comments):",
        'confirm': "Please review your Back Checker data:\n{data}\n\nIs this correct?",
        'saved': "Data saved successfully!",
        'btn_back': "🔙 Step Back",
        'btn_main': "🏠 Main Menu",
        'btn_submit': "✅ Submit",
        'btn_edit': "✏️ Edit"
    },
    'Afaan Oromoo': {
        'name': "Back Checker\n\nMaaloo maqaa kee guutuu galchi:",
        'unit': "Maqaa 'Supply Unit' galchi:",
        'method': "Hordoffiin bilbilaan yookiin qaamaan argamuun ta'uu:",
        'methods_list': ['Bilbilaan (Phone)', 'Qaamaan (In-person)'],
        'surveys': "Qo'annoo meeqa geessitan?",
        'worker_survey': "Qo'annoo Hojjetaa (Worker survey) sassaabdeettaa?",
        'yes_no_list': ['Eeyyee (Yes)', 'Lakki (No)'],
        'worker_survey_count': "Qo'annoo hojjetaa meeqa sassaabde?",
        'comment': "Yaada qabdaa? (Yoo hin qabne 'Homaa' jedhi):",
        'confirm': "Maaloo ragaa kee mirkaneeffadhu:\n{data}\n\nKun sirriidhaa?",
        'saved': "Ragaan kee sirriitti galmaa'eera!",
        'btn_back': "🔙 Duubatti",
        'btn_main': "🏠 Baafata Guddaa",
        'btn_submit': "✅ Galchi",
        'btn_edit': "✏️ Sirreessi"
    },
    'Amharic': {
        'name': "Back Checker\n\nእባክዎ ሙሉ ስምዎን ያስገቡ:",
        'unit': "የአቅርቦት ክፍል (Supply Unit) ስም ያስገቡ:",
        'method': "ክትትሉ የተደረገው በስልክ ነው ወይስ በአካል በመገኘት?",
        'methods_list': ['በስልክ (Phone)', 'በአካል (In-person)'],
        'surveys': "ስንት ጥናቶችን አካሄዱ?",
        'worker_survey': "የሰራተኛ ጥናት (Worker survey) ሰብስበዋል?",
        'yes_no_list': ['አዎ (Yes)', 'አይ (No)'],
        'worker_survey_count': "ስንት የሰራተኛ ጥናቶችን ሰበሰቡ?",
        'comment': "ማንኛውም አስተያየት አለዎት? (ከሌለ 'ምንም' ብለው ይፃፉ):",
        'confirm': "እባክዎ መረጃዎን ያረጋግጡ:\n{data}\n\nይህ ትክክል ነው?",
        'saved': "መረጃው በተሳካ ሁኔታ ተቀምጧል!",
        'btn_back': "🔙 ወደ ኋላ",
        'btn_main': "🏠 ዋና ማውጫ",
        'btn_submit': "✅ አስገባ",
        'btn_edit': "✏️ አርም"
    }
}

def get_t(context, key, **kwargs):
    lang = context.user_data.get('language', 'English')
    text = LANG.get(lang, LANG['English'])[key]
    return text.format(**kwargs) if kwargs else text

def build_kb(options, context=None, nav=True):
    kb = [[opt] for opt in options]
    if nav and context:
        kb.append([get_t(context, 'btn_back'), get_t(context, 'btn_main')])
    return ReplyKeyboardMarkup(kb, resize_keyboard=True, one_time_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    kb = ReplyKeyboardMarkup([['English', 'Amharic (አማርኛ)', 'Afaan Oromoo']], resize_keyboard=True)
    await update.message.reply_text("Welcome / እንኳን ደህና መጡ / Baga nagaan dhuftan!\n\nSelect Language:", reply_markup=kb)
    return LANGUAGE

async def set_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if 'Amharic' in text or 'አማርኛ' in text: lang = 'Amharic'
    elif 'Afaan' in text: lang = 'Afaan Oromoo'
    else: lang = 'English'
    
    context.user_data['language'] = lang
    await update.message.reply_text(get_t(context, 'name'), reply_markup=build_kb([], context, nav=False))
    return NAME

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text in [get_t(context, 'btn_main'), get_t(context, 'btn_back')]: return await start(update, context)
    context.user_data['name'] = text
    await update.message.reply_text(get_t(context, 'unit'), reply_markup=build_kb([], context))
    return SUPPLY_UNIT

async def get_unit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == get_t(context, 'btn_main'): return await start(update, context)
    if text == get_t(context, 'btn_back'):
        await update.message.reply_text(get_t(context, 'name'), reply_markup=build_kb([], context, nav=False))
        return NAME
    context.user_data['unit'] = text
    await update.message.reply_text(get_t(context, 'method'), reply_markup=build_kb(get_t(context, 'methods_list'), context))
    return BACK_METHOD

async def get_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == get_t(context, 'btn_main'): return await start(update, context)
    if text == get_t(context, 'btn_back'):
        await update.message.reply_text(get_t(context, 'unit'), reply_markup=build_kb([], context))
        return SUPPLY_UNIT
    context.user_data['method'] = text
    await update.message.reply_text(get_t(context, 'surveys'), reply_markup=build_kb([], context))
    return SURVEYS

async def get_surveys(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == get_t(context, 'btn_main'): return await start(update, context)
    if text == get_t(context, 'btn_back'):
        await update.message.reply_text(get_t(context, 'method'), reply_markup=build_kb(get_t(context, 'methods_list'), context))
        return BACK_METHOD
    context.user_data['surveys'] = text
    await update.message.reply_text(get_t(context, 'worker_survey'), reply_markup=build_kb(get_t(context, 'yes_no_list'), context))
    return WORKER_SURVEY

async def get_worker_survey(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == get_t(context, 'btn_main'): return await start(update, context)
    if text == get_t(context, 'btn_back'):
        await update.message.reply_text(get_t(context, 'surveys'), reply_markup=build_kb([], context))
        return SURVEYS
    
    context.user_data['worker_survey'] = text
    
    if text in ['No', 'Lakki (No)', 'አይ (No)']:
        context.user_data['worker_survey_count'] = 'N/A'
        await update.message.reply_text(get_t(context, 'comment'), reply_markup=build_kb([], context))
        return COMMENT
    else:
        await update.message.reply_text(get_t(context, 'worker_survey_count'), reply_markup=build_kb([], context))
        return WORKER_SURVEY_COUNT

async def get_worker_survey_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == get_t(context, 'btn_main'): return await start(update, context)
    if text == get_t(context, 'btn_back'):
        await update.message.reply_text(get_t(context, 'worker_survey'), reply_markup=build_kb(get_t(context, 'yes_no_list'), context))
        return WORKER_SURVEY
    
    context.user_data['worker_survey_count'] = text
    await update.message.reply_text(get_t(context, 'comment'), reply_markup=build_kb([], context))
    return COMMENT

async def get_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == get_t(context, 'btn_main'): return await start(update, context)
    if text == get_t(context, 'btn_back'):
        if context.user_data.get('worker_survey') in ['No', 'Lakki (No)', 'አይ (No)']:
            await update.message.reply_text(get_t(context, 'worker_survey'), reply_markup=build_kb(get_t(context, 'yes_no_list'), context))
            return WORKER_SURVEY
        else:
            await update.message.reply_text(get_t(context, 'worker_survey_count'), reply_markup=build_kb([], context))
            return WORKER_SURVEY_COUNT
            
    context.user_data['comment'] = text
    
    d = context.user_data
    summary = (f"Name: {d.get('name')}\nUnit: {d.get('unit')}\nMethod: {d.get('method')}\n"
               f"Surveys: {d.get('surveys')}\nWorker Survey: {d.get('worker_survey')} ({d.get('worker_survey_count')} collected)\n"
               f"Comment: {d.get('comment')}")
               
    await update.message.reply_text(get_t(context, 'confirm', data=summary), reply_markup=build_kb([get_t(context, 'btn_submit'), get_t(context, 'btn_edit')], context, nav=False))
    return CONFIRM

async def save_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == get_t(context, 'btn_edit'): 
        await update.message.reply_text(get_t(context, 'comment'), reply_markup=build_kb([], context))
        return COMMENT
    
    d = context.user_data
    row = [
        d.get('name'), d.get('unit'), d.get('method'), d.get('surveys'), 
        d.get('worker_survey'), d.get('worker_survey_count'), d.get('comment')
    ]
    
    sheet, err = get_sheet()
    if sheet:
        try:
            sheet.append_row(row)
            await update.message.reply_text(get_t(context, 'saved'), reply_markup=ReplyKeyboardMarkup([['/start']], resize_keyboard=True))
        except Exception as e:
            logger.error(f"Failed to append row: {e}")
            await update.message.reply_text(f"Warning: Could not save to Google Sheet ({type(e).__name__}: {e}).", reply_markup=ReplyKeyboardMarkup([['/start']], resize_keyboard=True))
    else:
        logger.error(f"Failed to append row: Sheet not accessible: {err}")
        await update.message.reply_text(f"Warning: Could not save to Google Sheet ({err}).", reply_markup=ReplyKeyboardMarkup([['/start']], resize_keyboard=True))
    return ConversationHandler.END

def run_bot():
    token = os.getenv("BACK_CHECKER_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
    if not token:
        logger.warning("No token found for Back Checker Bot (set BACK_CHECKER_BOT_TOKEN or TELEGRAM_TOKEN). Back Checker Bot will not start.")
        return

    logger.info("Starting Back Checker Bot...")
    app = Application.builder().token(token).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            LANGUAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_lang)],
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            SUPPLY_UNIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_unit)],
            BACK_METHOD: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_method)],
            SURVEYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_surveys)],
            WORKER_SURVEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_worker_survey)],
            WORKER_SURVEY_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_worker_survey_count)],
            COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_comment)],
            CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_data)]
        },
        fallbacks=[CommandHandler('start', start)]
    )
    app.add_handler(conv)
    app.run_polling()

if __name__ == '__main__':
    run_bot()
