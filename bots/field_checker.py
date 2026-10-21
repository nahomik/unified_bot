import os
import json
import logging
import sys
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ConversationHandler, ContextTypes
import gspread

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [FieldChecker] %(message)s")
logger = logging.getLogger("FieldChecker")

TOKEN = os.getenv("FIELD_CHECKER_BOT_TOKEN") or os.getenv("FIELD_TELEGRAM_TOKEN") or os.getenv("TELEGRAM_TOKEN")

def get_sheet():
    google_creds_json = (
        os.getenv("FIELD_CHECKER_GOOGLE_CREDS")
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

    if not google_creds_json:
        logger.warning("No Google credentials found in FIELD_CHECKER_GOOGLE_CREDS, GOOGLE_CREDENTIALS, GOOGLE_CREDS_JSON, or service_account.json")
        return None
    try:
        creds_dict = json.loads(google_creds_json)
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/spreadsheets",
        ]
        try:
            from google.oauth2.service_account import Credentials
            creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
            client = gspread.authorize(creds)
        except ImportError:
            from oauth2client.service_account import ServiceAccountCredentials
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope[:2])
            client = gspread.authorize(creds)

        sheet_id = os.getenv("FIELD_CHECKER_SHEET_ID") or "1ljx6JwUSn9LPW2ooYzZkToUX_SA3zA8-2XzlEhuxOWc"
        try:
            return client.open_by_key(sheet_id).sheet1
        except Exception:
            return client.open("Field Checker Data").sheet1
    except Exception as e:
        logger.error(f"Error connecting to Field Checker Sheet: {type(e).__name__} - {e}")
        return None

# States
(LANGUAGE, NAME, TEAM, SUPPLY_UNIT, START_DAY, SURVEYS, WORKER_SURVEY, WORKER_SURVEY_COUNT, REJECTED, REASON, ACTION, QCO_COMMENT, USER_COMMENT, CONFIRM) = range(14)

LANG = {
    'English': {
        'name': "Field Checker\n\nPlease enter your full name:",
        'team': "Select your Team:",
        'teams_list': ['Gedeo', 'Guji 1', 'Guji 2', 'Gurage', 'Sidama 1', 'Sidama 2'],
        'unit': "Enter the Supply Unit name:",
        'start_day': "Enter the Start Day (e.g., Oct 1):",
        'surveys': "How many surveys did you do?",
        'worker_survey': "Have you collected Worker survey?",
        'yes_no_list': ['Yes', 'No'],
        'worker_survey_count': "How many worker surveys did you collect?",
        'rejected': "How many surveys were rejected?",
        'rejected_list': ['None'],
        'reason': "Select the reason(s) for rejection. You can click multiple. Click 'Done' when finished:",
        'reasons_list': ['Content flag', 'Out of radius', 'Survey length', 'Too many numerical flag'],
        'action': "What action was executed?",
        'qco_comment': "Comment received from QCO:",
        'user_comment': "Any comments from you? (Type 'None' if no comment):",
        'confirm': "Please review your data:\n{data}\n\nIs this correct?",
        'saved': "Data saved successfully!",
        'btn_back': "🔙 Step Back",
        'btn_main': "🏠 Main Menu",
        'btn_submit': "✅ Submit",
        'btn_edit': "✏️ Edit",
        'btn_done': "✅ Done (Finished selecting)"
    },
    'Afaan Oromoo': {
        'name': "Field Checker\n\nMaaloo maqaa kee guutuu galchi:",
        'team': "Garee kee filadhu:",
        'teams_list': ['Gedeo', 'Guji 1', 'Guji 2', 'Gurage', 'Sidama 1', 'Sidama 2'],
        'unit': "Maqaa 'Supply Unit' galchi:",
        'start_day': "Guyyaa jalqabaa galchi (fkn. Oct 1):",
        'surveys': "Qo'annoo meeqa geessitan?",
        'worker_survey': "Qo'annoo Hojjetaa (Worker survey) sassaabdeettaa?",
        'yes_no_list': ['Eeyyee (Yes)', 'Lakki (No)'],
        'worker_survey_count': "Qo'annoo hojjetaa meeqa sassaabde?",
        'rejected': "Qo'annoo meeqatu haqame (Rejected)?",
        'rejected_list': ['Homaa (None)'],
        'reason': "Sababa haqameef filadhu. Tokkoo ol filachuu ni dandeessa. Yeroo xumurtu 'Done' tuqi:",
        'reasons_list': ['Rakkoo qabiyyee (Content flag)', 'Daangaa ala (Out of radius)', "Dheerina qo'annoo (Survey length)", 'Dogoggora lakkoofsaa (Too many numerical flag)'],
        'action': "Tarkaanfii maal fudhatame (Action executed)?",
        'qco_comment': "Yaada QCO irraa kenname:",
        'user_comment': "Yaada dabalataa qabdaa? (Yoo hin qabne 'Homaa' jedhi):",
        'confirm': "Maaloo ragaa kee mirkaneeffadhu:\n{data}\n\nKun sirriidhaa?",
        'saved': "Ragaan kee sirriitti galmaa'eera!",
        'btn_back': "🔙 Duubatti",
        'btn_main': "🏠 Baafata Guddaa",
        'btn_submit': "✅ Galchi",
        'btn_edit': "✏️ Sirreessi",
        'btn_done': "✅ Xumurameera (Done)"
    },
    'Amharic': {
        'name': "የመስክ ተቆጣጣሪ (Field Checker)\n\nእባክዎ ሙሉ ስምዎን ያስገቡ:",
        'team': "ቡድንዎን ይምረጡ:",
        'teams_list': ['ጌዲዮ', 'ጉጂ 1', 'ጉጂ 2', 'ጉራጌ', 'ሲዳማ 1', 'ሲዳማ 2'],
        'unit': "የአቅርቦት ክፍል (Supply Unit) ስም ያስገቡ:",
        'start_day': "የመጀመሪያ ቀንን ያስገቡ (ለምሳሌ ጥቅምት 1):",
        'surveys': "ስንት ጥናቶችን አካሄዱ?",
        'worker_survey': "የሰራተኛ ጥናት (Worker survey) ሰብስበዋል?",
        'yes_no_list': ['አዎ (Yes)', 'አይ (No)'],
        'worker_survey_count': "ስንት የሰራተኛ ጥናቶችን ሰበሰቡ?",
        'rejected': "ስንት ጥናቶች ውድቅ ተደረጉ (Rejected)?",
        'rejected_list': ['ምንም (None)'],
        'reason': "ውድቅ የተደረገበትን ምክንያት ይምረጡ። ከአንድ በላይ መምረጥ ይችላሉ። ሲጨርሱ 'Done' ይጫኑ:",
        'reasons_list': ['የይዘት ችግር (Content flag)', 'ከክልል ውጪ (Out of radius)', 'የጥናቱ ርዝመት (Survey length)', 'የቁጥር ስህተት መብዛት (Too many numerical flag)'],
        'action': "ምን እርምጃ ተወሰደ (Action executed)?",
        'qco_comment': "ከ QCO የተሰጠ አስተያየት:",
        'user_comment': "የእርስዎ ተጨማሪ አስተያየት አለ? (ከሌለ 'ምንም' ይበሉ):",
        'confirm': "እባክዎ መረጃዎን ያረጋግጡ:\n{data}\n\nይህ ትክክል ነው?",
        'saved': "መረጃው በተሳካ ሁኔታ ተቀምጧል!",
        'btn_back': "🔙 ወደ ኋላ",
        'btn_main': "🏠 ዋና ማውጫ",
        'btn_submit': "✅ አስገባ",
        'btn_edit': "✏️ አርም",
        'btn_done': "✅ ጨርሻለሁ (Done)"
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
    await update.message.reply_text(get_t(context, 'team'), reply_markup=build_kb(get_t(context, 'teams_list'), context))
    return TEAM

async def get_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == get_t(context, 'btn_main'): return await start(update, context)
    if text == get_t(context, 'btn_back'):
        await update.message.reply_text(get_t(context, 'name'), reply_markup=build_kb([], context, nav=False))
        return NAME
    context.user_data['team'] = text
    await update.message.reply_text(get_t(context, 'unit'), reply_markup=build_kb([], context))
    return SUPPLY_UNIT

async def get_unit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == get_t(context, 'btn_main'): return await start(update, context)
    if text == get_t(context, 'btn_back'):
        await update.message.reply_text(get_t(context, 'team'), reply_markup=build_kb(get_t(context, 'teams_list'), context))
        return TEAM
    context.user_data['unit'] = text
    await update.message.reply_text(get_t(context, 'start_day'), reply_markup=build_kb([], context))
    return START_DAY

async def get_start_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == get_t(context, 'btn_main'): return await start(update, context)
    if text == get_t(context, 'btn_back'):
        await update.message.reply_text(get_t(context, 'unit'), reply_markup=build_kb([], context))
        return SUPPLY_UNIT
    context.user_data['start_day'] = text
    await update.message.reply_text(get_t(context, 'surveys'), reply_markup=build_kb([], context))
    return SURVEYS

async def get_surveys(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == get_t(context, 'btn_main'): return await start(update, context)
    if text == get_t(context, 'btn_back'):
        await update.message.reply_text(get_t(context, 'start_day'), reply_markup=build_kb([], context))
        return START_DAY
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
        await update.message.reply_text(get_t(context, 'rejected'), reply_markup=build_kb(get_t(context, 'rejected_list'), context))
        return REJECTED
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
    await update.message.reply_text(get_t(context, 'rejected'), reply_markup=build_kb(get_t(context, 'rejected_list'), context))
    return REJECTED

async def get_rejected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == get_t(context, 'btn_main'): return await start(update, context)
    if text == get_t(context, 'btn_back'):
        if context.user_data.get('worker_survey') in ['No', 'Lakki (No)', 'አይ (No)']:
            await update.message.reply_text(get_t(context, 'worker_survey'), reply_markup=build_kb(get_t(context, 'yes_no_list'), context))
            return WORKER_SURVEY
        else:
            await update.message.reply_text(get_t(context, 'worker_survey_count'), reply_markup=build_kb([], context))
            return WORKER_SURVEY_COUNT
    
    context.user_data['rejected'] = text
    context.user_data['reason_list'] = []
    
    if text in ['None', 'Homaa (None)', 'ምንም (None)', '0']:
        context.user_data['reason'] = 'N/A'
        context.user_data['action'] = 'N/A'
        context.user_data['qco_comment'] = 'N/A'
        await update.message.reply_text(get_t(context, 'user_comment'), reply_markup=build_kb([], context))
        return USER_COMMENT
    else:
        options = get_t(context, 'reasons_list') + [get_t(context, 'btn_done')]
        await update.message.reply_text(get_t(context, 'reason'), reply_markup=build_kb(options, context))
        return REASON

async def get_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == get_t(context, 'btn_main'): return await start(update, context)
    if text == get_t(context, 'btn_back'):
        await update.message.reply_text(get_t(context, 'rejected'), reply_markup=build_kb(get_t(context, 'rejected_list'), context))
        return REJECTED
        
    if text == get_t(context, 'btn_done'):
        if not context.user_data.get('reason_list'):
            context.user_data['reason'] = 'None'
        else:
            context.user_data['reason'] = ", ".join(context.user_data['reason_list'])
        await update.message.reply_text(get_t(context, 'action'), reply_markup=build_kb([], context))
        return ACTION
        
    if 'reason_list' not in context.user_data:
        context.user_data['reason_list'] = []
    
    if text not in context.user_data['reason_list']:
        context.user_data['reason_list'].append(text)
        
    current_selections = ", ".join(context.user_data['reason_list'])
    msg = f"Added: {text}\n\nCurrent selections: [{current_selections}]\n\nSelect another reason, or click '{get_t(context, 'btn_done')}' to proceed:"
    
    options = get_t(context, 'reasons_list') + [get_t(context, 'btn_done')]
    await update.message.reply_text(msg, reply_markup=build_kb(options, context))
    return REASON

async def get_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == get_t(context, 'btn_main'): return await start(update, context)
    if text == get_t(context, 'btn_back'):
        context.user_data['reason_list'] = []
        options = get_t(context, 'reasons_list') + [get_t(context, 'btn_done')]
        await update.message.reply_text(get_t(context, 'reason'), reply_markup=build_kb(options, context))
        return REASON
    context.user_data['action'] = text
    await update.message.reply_text(get_t(context, 'qco_comment'), reply_markup=build_kb([], context))
    return QCO_COMMENT

async def get_qco_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == get_t(context, 'btn_main'): return await start(update, context)
    if text == get_t(context, 'btn_back'):
        await update.message.reply_text(get_t(context, 'action'), reply_markup=build_kb([], context))
        return ACTION
    context.user_data['qco_comment'] = text
    await update.message.reply_text(get_t(context, 'user_comment'), reply_markup=build_kb([], context))
    return USER_COMMENT

async def get_user_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == get_t(context, 'btn_main'): return await start(update, context)
    if text == get_t(context, 'btn_back'):
        if context.user_data.get('rejected') in ['None', 'Homaa (None)', 'ምንም (None)', '0']:
            await update.message.reply_text(get_t(context, 'rejected'), reply_markup=build_kb(get_t(context, 'rejected_list'), context))
            return REJECTED
        else:
            await update.message.reply_text(get_t(context, 'qco_comment'), reply_markup=build_kb([], context))
            return QCO_COMMENT
            
    context.user_data['user_comment'] = text
    
    d = context.user_data
    summary = (f"Name: {d.get('name')}\nTeam: {d.get('team')}\nUnit: {d.get('unit')}\n"
               f"Start Day: {d.get('start_day')}\nSurveys: {d.get('surveys')}\n"
               f"Worker Survey: {d.get('worker_survey')} ({d.get('worker_survey_count')} collected)\n"
               f"Rejected: {d.get('rejected')}\nReason: {d.get('reason')}\n"
               f"Action: {d.get('action')}\nQCO Comment: {d.get('qco_comment')}\n"
               f"User Comment: {d.get('user_comment')}")
               
    await update.message.reply_text(get_t(context, 'confirm', data=summary), reply_markup=build_kb([get_t(context, 'btn_submit'), get_t(context, 'btn_edit')], context, nav=False))
    return CONFIRM

async def save_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == get_t(context, 'btn_edit'): 
        await update.message.reply_text(get_t(context, 'user_comment'), reply_markup=build_kb([], context))
        return USER_COMMENT
    
    d = context.user_data
    row = [
        d.get('name'), d.get('team'), d.get('unit'), d.get('start_day'), 
        d.get('surveys'), d.get('worker_survey'), d.get('worker_survey_count'),
        d.get('rejected'), d.get('reason'), 
        d.get('action'), d.get('qco_comment'), d.get('user_comment')
    ]
    
    sheet = get_sheet()
    if sheet:
        sheet.append_row(row)
        await update.message.reply_text(get_t(context, 'saved'), reply_markup=ReplyKeyboardMarkup([['/start']], resize_keyboard=True))
    else:
        logger.error("Failed to append row: Sheet not accessible")
        await update.message.reply_text("Warning: Could not save to Google Sheet. Please check credentials.", reply_markup=ReplyKeyboardMarkup([['/start']], resize_keyboard=True))
    return ConversationHandler.END

def run_bot():
    token = os.getenv("FIELD_CHECKER_BOT_TOKEN") or os.getenv("FIELD_TELEGRAM_TOKEN") or os.getenv("TELEGRAM_TOKEN")
    if not token:
        logger.warning("No token found for Field Checker Bot (set FIELD_CHECKER_BOT_TOKEN or TELEGRAM_TOKEN). Field Checker Bot will not start.")
        return

    logger.info("Starting Field Checker Bot...")
    app = Application.builder().token(token).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            LANGUAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_lang)],
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            TEAM: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_team)],
            SUPPLY_UNIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_unit)],
            START_DAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_start_day)],
            SURVEYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_surveys)],
            WORKER_SURVEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_worker_survey)],
            WORKER_SURVEY_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_worker_survey_count)],
            REJECTED: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_rejected)],
            REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_reason)],
            ACTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_action)],
            QCO_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_qco_comment)],
            USER_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_user_comment)],
            CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_data)]
        },
        fallbacks=[CommandHandler('start', start)]
    )
    app.add_handler(conv)
    app.run_polling()

if __name__ == '__main__':
    run_bot()
