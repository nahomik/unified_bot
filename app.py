import os
import json
import logging
import time
from datetime import datetime, timezone, timedelta

# Ethiopia is UTC+3 year-round (no daylight saving), so we compute this
# explicitly rather than relying on datetime.now(), which would use whatever
# timezone the hosting server happens to run in (typically UTC on Render).
ETHIOPIA_TZ = timezone(timedelta(hours=3))

def now_ethiopia():
    return datetime.now(timezone.utc).astimezone(ETHIOPIA_TZ)

from flask import Flask, request, Response, jsonify, send_from_directory
import requests
import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ---------- Config from environment variables ----------
BOT_TOKEN = os.environ.get("POULTRY_BOT_TOKEN") or os.environ.get("BOT_TOKEN", "")
SHEET_ID = os.environ.get("POULTRY_SHEET_ID") or os.environ.get("SHEET_ID", "")
GOOGLE_CREDS_JSON = (
    os.environ.get("POULTRY_GOOGLE_CREDS")
    or os.environ.get("GOOGLE_CREDS_JSON")
    or os.environ.get("GOOGLE_CREDENTIALS", "")
)

# Render sets this automatically for every web service. Falls back to BASE_URL
# if set manually, useful for local testing or other hosts.
BASE_URL = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("BASE_URL", "")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

# ---------- Google Sheets setup ----------
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

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

def get_client():
    creds_json = GOOGLE_CREDS_JSON
    if not creds_json:
        file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "service_account.json")
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                creds_json = f.read()

    creds_dict, err = parse_creds(creds_json)
    if not creds_dict:
        raise ValueError(f"Google credentials error: {err}")
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(creds)

def get_sheet():
    return get_client().open_by_key(SHEET_ID).sheet1

def telegram_file_link(file_id):
    """Returns a link to our own bot's /file/<file_id> route, which fetches the
    file from Telegram on demand. Avoids needing Google Drive storage (service
    accounts have none on personal Gmail) and avoids exposing the bot token,
    since the token is only used server-side, not in the link itself."""
    return f"{BASE_URL}/file/{file_id}"

def download_telegram_file(file_id):
    resp = requests.get(f"{TELEGRAM_API}/getFile", params={"file_id": file_id})
    resp.raise_for_status()
    file_path = resp.json()["result"]["file_path"]
    file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    file_resp = requests.get(file_url)
    file_resp.raise_for_status()
    return file_resp.content

# ---------- Authorization ----------
# Who's allowed to use the bot is controlled from a second tab in the same
# Google Sheet, named "Authorized_Users", with columns: Telegram_ID | Name | Status
# Status must be exactly "active" (any other value, or a missing row, blocks access).
# This lets the farm owner add or remove people any time by editing the sheet -
# no code change or redeploy needed. Cached briefly to avoid hitting the Sheets
# API on every single message.

_auth_cache = None
_auth_cache_time = 0
AUTH_CACHE_SECONDS = 120

def load_authorized_users(force=False):
    global _auth_cache, _auth_cache_time
    now = time.time()
    if not force and _auth_cache is not None and (now - _auth_cache_time) < AUTH_CACHE_SECONDS:
        return _auth_cache
    try:
        sheet = get_client().open_by_key(SHEET_ID)
        ws = sheet.worksheet("Authorized_Users")
        records = ws.get_all_records()
        allowed = {}
        for r in records:
            r = {str(k).strip(): v for k, v in r.items()}
            tid = str(r.get("Telegram_ID", "")).strip()
            status = str(r.get("Status", "")).strip().lower()
            if tid:
                allowed[tid] = status == "active"
        _auth_cache = allowed
        _auth_cache_time = now
        return allowed
    except Exception:
        logger.exception("Failed to load Authorized_Users sheet")
        # Fail closed: if we can't confirm someone's authorized, don't guess - deny.
        # Keep serving the last known-good list if we have one, rather than locking everyone out
        # over a brief network hiccup.
        return _auth_cache if _auth_cache is not None else {}

ADMIN_IDS = {"624180317", "764757354"}

def is_authorized(user_id):
    uid = str(user_id).strip()
    if uid in ADMIN_IDS:
        return True
    allowed = load_authorized_users()
    if allowed.get(uid, False):
        return True
    allowed = load_authorized_users(force=True)
    return allowed.get(uid, False)

# ---------- Amharic text (farmer-facing) ----------
# NOTE: translations marked "written by Claude" below have not been checked by a
# fluent speaker yet - please review and send corrections if anything sounds off.

SITE_A = "ዲላ ቅ.ሚካኤል ቤተክርስቲያን"
SITE_B = "ጌቡኮ ሀገረስብከት"
SITE_C = "አንዲዳ"

REPORT_EGG = "የእንቁላል መረጃ"
REPORT_FODDER = "የመኖ መረጃ"
REPORT_BOTH = "ሁለቱንም"
REPORT_ISSUE = "የዶሮ ጤና/አካባቢ ችግር"  # chicken health / environment issue

TOP_ISSUE = REPORT_ISSUE
TOP_EGGFEED = "የእንቁላል/መኖ መረጃ"
# NOTE: written by Claude, not verified by a fluent speaker yet
TOP_BOTH = "ሁለቱንም"

FEED_FODDER = "መደበኛ መኖ"
FEED_OTHER = "አልፋ አልፋ/ቅጠላ ቅጠል"
FEED_BOTH = "ሁለቱንም"

BTN_YES = "አዎ"
BTN_NO = "የለም"
# NOTE: written by Claude, not verified by a fluent speaker yet
BTN_BACK = "🔙 ተመለስ"

Q_WELCOME = "እንኳን ደህና መጡ! ለየትኛው ቦታ ነው መረጃ የሚሞሉት?"
BTN_START = "መረጃ መሙላት ጀምር"
BTN_EDIT = "የገባውን መረጃ አርም"
IDLE_PROMPT = "መረጃ መሙላት ጀምር"
IDLE_CHOOSE_BUTTON = "እባክዎን ከታች ያለውን ቁልፍ ይጫኑ"

# NOTE: written by Claude, not verified by a fluent speaker yet
Q_TOP_CHOICE = "የትኛውን መረጃ መሙላት ይፈልጋሉ?"

Q_REPORT_TYPE = "የሚሞሉት መረጃ ስለምንድነው?"
Q_FEED_TYPE = "ምን አይነት ምግብ ነው የሰጧቸው?"
Q_FODDER_KG = "ስንት ኪሎግራም መደበኛ መኖ?"
Q_OTHER_KG = "ስንት ኪሎግራም አልፋ አልፋ/ቅጠላ ቅጠል?"
Q_EGGS = "ዛሬ ዶሮዎቹ ስንት እንቁላል ጣሉ?"
Q_BROKEN = "ስንቱ እንቁላል ተሰበረ?"
Q_SOLD = "ምን ያህል እንቁላል ተሸጠ?"
# NOTE: written by Claude, not verified by a fluent speaker yet
Q_REVENUE = "አንድ እንቁላል በስንት ብር ተሸጠ?"
Q_NOTES = "ሌላ የተመለከቱት ነገር ካለ ይጻፉ። ካልሆነ 'የለም' ብለው ይጻፉ።"

# NOTE: written by Claude, not verified by a fluent speaker yet
Q_ASK_VOICE_WANT = "ስለ ችግሩ በድምጽ መልእክት መንገር ይፈልጋሉ?"
Q_ASK_IMAGE_WANT = "ምስል መላክ ይፈልጋሉ?"
Q_ASK_TEXT_WANT = "በጽሁፍ መግለጽ ይፈልጋሉ?"
Q_ISSUE_VOICE = "እባክዎን የድምጽ መልእክት ይላኩ።"
Q_ISSUE_IMAGE = "እባክዎን ምስል ይላኩ።"
Q_ASK_IMAGE_MORE = "ተጨማሪ ምስል መላክ ይፈልጋሉ?"
Q_ISSUE_TEXT = "እባክዎን ችግሩን በዝርዝር ይጻፉ።"
ERR_ISSUE_VOICE = "እባክዎን የድምጽ መልእክት ይላኩ (ማይክራፎን ምልክቱን ተጭነው ይናገሩ)።"
ERR_ISSUE_IMAGE = "እባክዎን ምስል ይላኩ።"

# NOTE: written by Claude, not verified by a fluent speaker yet
ADMIN_CONTACT = "@Selamayale"

def unauthorized_message(user_id):
    return (
        "ይቅርታ፣ ይህን ቦት የመጠቀም ፍቃድ የለዎትም። "
        f"የቴሌግራም መለያ ቁጥርዎ: {user_id}\n"
        f"እባክዎ ይህን ቁጥር ለ{ADMIN_CONTACT} በመላክ እንዲፈቅድልዎ ይጠይቁ።"
    )

def myid_message(user_id):
    return f"የቴሌግራም መለያ ቁጥርዎ: {user_id}"

ERR_SITE = f"እባክዎን አንዱን አማራጭ ይምረጡ፡ {SITE_A}፣ {SITE_B} ወይም {SITE_C}"
ERR_CHOOSE = "እባክዎን አንዱን አማራጭ ይምረጡ"
ERR_NUMBER = "እባክዎን ቁጥር ያስገቡ"
ERR_WHOLE_NUMBER = "እባክዎን ሙሉ ቁጥር ያስገቡ"

# NOTE: written by Claude, not verified by a fluent speaker yet
FIELD_MENU_PROMPT = "የትኛውን መረጃ ማስተካከል ይፈልጋሉ?"
BTN_DONE_EDITING = "ጨርሻለሁ"
EDIT_SAVED = "ተስተካክሏል!"
ERR_EDIT_FAILED = "ይቅርታ፣ ማስተካከል አልተቻለም። እባክዎ እንደገና ይሞክሩ።"

# Fields the farmer can pick to correct individually. col = the sheet column letter.
EDIT_FIELDS = [
    {"label": "ቦታ", "col": "D", "kind": "site", "prompt": "አዲሱን ቦታ ይምረጡ"},
    {"label": f"{FEED_FODDER} ኪግ", "col": "F", "kind": "float", "prompt": Q_FODDER_KG},
    {"label": f"{FEED_OTHER} ኪግ", "col": "G", "kind": "float", "prompt": Q_OTHER_KG},
    {"label": "የእንቁላል ብዛት", "col": "H", "kind": "int", "prompt": Q_EGGS},
    {"label": "የተሰበረ እንቁላል", "col": "I", "kind": "int", "prompt": Q_BROKEN},
    {"label": "የተሸጠ እንቁላል", "col": "J", "kind": "int", "prompt": Q_SOLD},
    {"label": "የ1 እንቁላል ዋጋ (ብር)", "col": "K", "kind": "float", "prompt": Q_REVENUE},
    {"label": "ማስታወሻ", "col": "L", "kind": "text", "prompt": Q_NOTES},
]

def err_broken_exceeds(eggs):
    return f"ያስገቡት ቁጥር ዛሬ ከተሰበሰበው {eggs} እንቁላል በላይ ነው። እባክዎን እንደገና ያስገቡ።"

# Column order as written by save_and_confirm - kept in sync with the row list there.
ROW_COLUMNS = [
    "date", "time", "reporter_name", "site", "report_type",
    "fodder_kg", "other_kg", "eggs", "broken", "sold", "revenue", "notes",
    "issue_text", "issue_voice_link", "images",
]

def row_to_dict(row_values):
    padded = list(row_values) + [""] * (len(ROW_COLUMNS) - len(row_values))
    return dict(zip(ROW_COLUMNS, padded))

# NOTE: written by Claude, not verified by a fluent speaker yet
def build_row_summary(d):
    """Builds the same style of confirmation summary used right after saving,
    but from a row dict read back from the sheet - used after an edit, so the
    farmer sees the full, current state of the entry rather than just the one
    field they changed."""
    rt = d.get("report_type", "")
    lines = ["ተቀብለናል ✅", f"ቦታ: {d.get('site', '')}", f"ሪፖርት: {rt}"]
    if rt in (REPORT_FODDER, REPORT_BOTH):
        lines.append(f"መኖ: {FEED_FODDER} {d.get('fodder_kg', '')}ኪግ, {FEED_OTHER} {d.get('other_kg', '')}ኪግ")
    if rt in (REPORT_EGG, REPORT_BOTH):
        lines.append(f"እንቁላል: {d.get('eggs', '')}, የተሰበረ: {d.get('broken', '')}, የተሸጠ: {d.get('sold', '')}")
        try:
            sold_n = float(d.get("sold", 0) or 0)
            price_n = float(d.get("revenue", 0) or 0)
            lines.append(f"ገቢ: {sold_n * price_n:g} ብር (በ1 እንቁላል {price_n:g} ብር)")
        except (TypeError, ValueError):
            lines.append(f"ገቢ (በ1 እንቁላል): {d.get('revenue', '')} ብር")
    if d.get("notes"):
        lines.append(f"ማስታወሻ: {d['notes']}")
    has_issue_data = bool(d.get("issue_voice_link") or d.get("issue_text") or (d.get("images") or "").strip())
    if rt == REPORT_ISSUE or has_issue_data:
        if d.get("issue_voice_link"):
            lines.append("ድምጽ: ✅")
        images = [x for x in d.get("images", "").split("\n") if x.strip()]
        if images:
            lines.append(f"ምስል: ✅ ({len(images)})")
        if d.get("issue_text"):
            lines.append(f"ጽሁፍ: {d['issue_text']}")
    lines.append("በሉ ላይ ተመዝግቧል። እናመሰግናለን!")
    return lines

# ---------- In-memory conversation state ----------
# NOTE: this resets if the bot restarts. Fine for a short daily conversation.
user_state = {}

# Tracks each chat's most recently saved row, so "edit last entry" knows what to overwrite.
# NOTE: also resets if the bot restarts.
last_entry = {}

def reset_state(chat_id, reporter_name=""):
    user_state[chat_id] = {
        "step": "IDLE",
        "history": [],  # stack of previous steps, for the 🔙 Back button
        "reporter_name": reporter_name,
        "site": None,
        "report_type": None,
        "also_issue": False,  # set when the farmer picked "ሁለቱንም" at the top-level choice
        "fodder_kg": 0,
        "other_kg": 0,
        "eggs": 0,
        "broken": 0,
        "sold": 0,
        "revenue": 0,
        "notes": "",
        "issue_voice_link": "",
        "issue_image_links": [],
        "issue_text": "",
    }

# ---------- Telegram helpers ----------
def send_message(chat_id, text, reply_markup=None, parse_mode=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    if parse_mode:
        payload["parse_mode"] = parse_mode
    requests.post(f"{TELEGRAM_API}/sendMessage", json=payload)

def site_keyboard():
    return {
        "keyboard": [[{"text": SITE_A}], [{"text": SITE_B}], [{"text": SITE_C}]],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }

def idle_keyboard(chat_id):
    rows = [[{"text": BTN_START}]]
    if chat_id in last_entry and last_entry[chat_id].get("report_type") != REPORT_ISSUE:
        rows.append([{"text": BTN_EDIT}])
    return {"keyboard": rows, "resize_keyboard": True, "one_time_keyboard": True}

def top_choice_keyboard():
    return {
        "keyboard": [[{"text": TOP_EGGFEED}], [{"text": TOP_ISSUE}], [{"text": TOP_BOTH}]],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }

def report_type_keyboard():
    return {
        "keyboard": [
            [{"text": REPORT_EGG}, {"text": REPORT_FODDER}],
            [{"text": REPORT_BOTH}],
        ],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }

def feed_type_keyboard():
    return {
        "keyboard": [
            [{"text": FEED_FODDER}, {"text": FEED_OTHER}],
            [{"text": FEED_BOTH}],
        ],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }

def yes_no_keyboard():
    return {
        "keyboard": [[{"text": BTN_YES}, {"text": BTN_NO}]],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }

def edit_field_keyboard():
    labels = [f["label"] for f in EDIT_FIELDS]
    rows = [[{"text": labels[i]}, {"text": labels[i + 1]}] if i + 1 < len(labels) else [{"text": labels[i]}]
            for i in range(0, len(labels), 2)]
    rows.append([{"text": BTN_DONE_EDITING}])
    return {"keyboard": rows, "resize_keyboard": True, "one_time_keyboard": True}

def remove_keyboard():
    return {"remove_keyboard": True}

def with_back(keyboard):
    """Appends a '🔙 Back' row to an existing reply keyboard, so any menu-style
    step also lets the farmer step back to the previous question."""
    return {**keyboard, "keyboard": keyboard["keyboard"] + [[{"text": BTN_BACK}]]}

def back_only_keyboard():
    """For free-text entry steps (kg amounts, egg counts, notes, etc). Telegram
    still lets the user type freely even with a custom keyboard showing, so this
    just adds a tappable way back without getting in the way of typing."""
    return {"keyboard": [[{"text": BTN_BACK}]], "resize_keyboard": True, "one_time_keyboard": True}

# Renders the prompt + keyboard for a given step, with no side effects on the
# farmer's answers. Used both when moving forward (advance) and when the
# farmer presses 🔙 Back to return to a step they've already been on.
STEP_RENDERERS = {
    "ASK_SITE": lambda chat_id, state: send_message(chat_id, Q_WELCOME, with_back(site_keyboard())),
    "ASK_TOP_CHOICE": lambda chat_id, state: send_message(chat_id, Q_TOP_CHOICE, with_back(top_choice_keyboard())),
    "ASK_REPORT_TYPE": lambda chat_id, state: send_message(chat_id, Q_REPORT_TYPE, with_back(report_type_keyboard())),
    "ASK_FEED_TYPE": lambda chat_id, state: send_message(chat_id, Q_FEED_TYPE, with_back(feed_type_keyboard())),
    "ASK_FODDER_KG_ONLY": lambda chat_id, state: send_message(chat_id, Q_FODDER_KG, back_only_keyboard()),
    "ASK_OTHER_KG_ONLY": lambda chat_id, state: send_message(chat_id, Q_OTHER_KG, back_only_keyboard()),
    "ASK_FODDER_KG_THEN_OTHER": lambda chat_id, state: send_message(chat_id, Q_FODDER_KG, back_only_keyboard()),
    "ASK_OTHER_KG_THEN_DONE": lambda chat_id, state: send_message(chat_id, Q_OTHER_KG, back_only_keyboard()),
    "ASK_EGGS": lambda chat_id, state: send_message(chat_id, Q_EGGS, back_only_keyboard()),
    "ASK_BROKEN": lambda chat_id, state: send_message(chat_id, Q_BROKEN, back_only_keyboard()),
    "ASK_SOLD": lambda chat_id, state: send_message(chat_id, Q_SOLD, back_only_keyboard()),
    "ASK_REVENUE": lambda chat_id, state: send_message(chat_id, Q_REVENUE, back_only_keyboard()),
    "ASK_NOTES": lambda chat_id, state: send_message(chat_id, Q_NOTES, back_only_keyboard()),
    "ASK_ISSUE_WANT_VOICE": lambda chat_id, state: send_message(chat_id, Q_ASK_VOICE_WANT, with_back(yes_no_keyboard())),
    "ASK_ISSUE_VOICE_UPLOAD": lambda chat_id, state: send_message(chat_id, Q_ISSUE_VOICE, back_only_keyboard()),
    "ASK_ISSUE_WANT_IMAGE": lambda chat_id, state: send_message(chat_id, Q_ASK_IMAGE_WANT, with_back(yes_no_keyboard())),
    "ASK_ISSUE_IMAGE_UPLOAD": lambda chat_id, state: send_message(chat_id, Q_ISSUE_IMAGE, back_only_keyboard()),
    "ASK_ISSUE_IMAGE_MORE": lambda chat_id, state: send_message(chat_id, Q_ASK_IMAGE_MORE, with_back(yes_no_keyboard())),
    "ASK_ISSUE_WANT_TEXT": lambda chat_id, state: send_message(chat_id, Q_ASK_TEXT_WANT, with_back(yes_no_keyboard())),
    "ASK_ISSUE_TEXT_INPUT": lambda chat_id, state: send_message(chat_id, Q_ISSUE_TEXT, back_only_keyboard()),
}

def advance(chat_id, state, new_step):
    """Move forward to new_step, remembering the step we're leaving so 🔙 Back
    can return to it later."""
    state["history"].append(state["step"])
    state["step"] = new_step
    STEP_RENDERERS[new_step](chat_id, state)

def go_back(chat_id, state):
    """Handles the 🔙 Back button: pops the last step off history and
    re-renders it. If there's nothing to go back to (or the previous step was
    the very start of the flow), returns to the main menu instead."""
    reporter = state["reporter_name"]
    if not state["history"]:
        reset_state(chat_id, reporter)
        send_message(chat_id, IDLE_PROMPT, idle_keyboard(chat_id))
        return
    prev_step = state["history"].pop()
    if prev_step == "IDLE" or prev_step not in STEP_RENDERERS:
        reset_state(chat_id, reporter)
        send_message(chat_id, IDLE_PROMPT, idle_keyboard(chat_id))
        return
    state["step"] = prev_step
    STEP_RENDERERS[prev_step](chat_id, state)

def parse_float(text):
    try:
        return float(text)
    except ValueError:
        return None

def parse_int(text):
    try:
        return int(text)
    except ValueError:
        return None

# ---------- Flow helpers ----------
def wants_fodder(state):
    return state["report_type"] in (REPORT_FODDER, REPORT_BOTH)

def wants_eggs(state):
    return state["report_type"] in (REPORT_EGG, REPORT_BOTH)

def wants_issue(state):
    return state["report_type"] == REPORT_ISSUE

def start_feed_section(chat_id, state):
    advance(chat_id, state, "ASK_FEED_TYPE")

def start_egg_section(chat_id, state):
    advance(chat_id, state, "ASK_EGGS")

def start_notes_section(chat_id, state):
    advance(chat_id, state, "ASK_NOTES")

def start_issue_section(chat_id, state):
    advance(chat_id, state, "ASK_ISSUE_WANT_VOICE")

def after_feed_section(chat_id, state):
    if wants_eggs(state):
        start_egg_section(chat_id, state)
    else:
        start_notes_section(chat_id, state)

def finish_and_return_to_idle(chat_id, state):
    save_and_confirm(chat_id, state)
    reporter = state["reporter_name"]
    reset_state(chat_id, reporter)
    send_message(chat_id, IDLE_PROMPT, idle_keyboard(chat_id))

# ---------- Conversation logic ----------
def handle_message(chat_id, message, reporter_name=""):
    text = (message.get("text") or "").strip()

    if text == "/start" or chat_id not in user_state:
        reset_state(chat_id, reporter_name)
        send_message(chat_id, IDLE_PROMPT, idle_keyboard(chat_id))
        return

    state = user_state[chat_id]
    step = state["step"]

    if text == BTN_BACK and step != "IDLE" and step != "EDIT_AWAIT_VALUE":
        go_back(chat_id, state)
        return

    if step == "IDLE":
        if text == BTN_START:
            advance(chat_id, state, "ASK_SITE")
        elif text == BTN_EDIT and chat_id in last_entry:
            state["step"] = "EDIT_CHOOSE_FIELD"
            send_message(chat_id, FIELD_MENU_PROMPT, edit_field_keyboard())
        else:
            send_message(chat_id, IDLE_CHOOSE_BUTTON, idle_keyboard(chat_id))

    elif step == "ASK_SITE":
        if text not in [SITE_A, SITE_B, SITE_C]:
            send_message(chat_id, ERR_SITE, site_keyboard())
            return
        state["site"] = text
        advance(chat_id, state, "ASK_TOP_CHOICE")

    elif step == "ASK_TOP_CHOICE":
        if text not in [TOP_EGGFEED, TOP_ISSUE, TOP_BOTH]:
            send_message(chat_id, ERR_CHOOSE, top_choice_keyboard())
            return
        if text == TOP_ISSUE:
            state["report_type"] = REPORT_ISSUE
            start_issue_section(chat_id, state)
        else:
            if text == TOP_BOTH:
                state["also_issue"] = True
            advance(chat_id, state, "ASK_REPORT_TYPE")

    elif step == "ASK_REPORT_TYPE":
        if text not in [REPORT_EGG, REPORT_FODDER, REPORT_BOTH]:
            send_message(chat_id, ERR_CHOOSE, report_type_keyboard())
            return
        state["report_type"] = text
        if wants_fodder(state):
            start_feed_section(chat_id, state)
        else:
            start_egg_section(chat_id, state)

    elif step == "ASK_FEED_TYPE":
        if text not in [FEED_FODDER, FEED_OTHER, FEED_BOTH]:
            send_message(chat_id, ERR_CHOOSE, feed_type_keyboard())
            return
        if text == FEED_BOTH:
            advance(chat_id, state, "ASK_FODDER_KG_THEN_OTHER")
        elif text == FEED_FODDER:
            advance(chat_id, state, "ASK_FODDER_KG_ONLY")
        else:
            advance(chat_id, state, "ASK_OTHER_KG_ONLY")

    elif step == "ASK_FODDER_KG_ONLY":
        kg = parse_float(text)
        if kg is None:
            send_message(chat_id, ERR_NUMBER)
            return
        state["fodder_kg"] = kg
        after_feed_section(chat_id, state)

    elif step == "ASK_OTHER_KG_ONLY":
        kg = parse_float(text)
        if kg is None:
            send_message(chat_id, ERR_NUMBER)
            return
        state["other_kg"] = kg
        after_feed_section(chat_id, state)

    elif step == "ASK_FODDER_KG_THEN_OTHER":
        kg = parse_float(text)
        if kg is None:
            send_message(chat_id, ERR_NUMBER)
            return
        state["fodder_kg"] = kg
        advance(chat_id, state, "ASK_OTHER_KG_THEN_DONE")

    elif step == "ASK_OTHER_KG_THEN_DONE":
        kg = parse_float(text)
        if kg is None:
            send_message(chat_id, ERR_NUMBER)
            return
        state["other_kg"] = kg
        after_feed_section(chat_id, state)

    elif step == "ASK_EGGS":
        eggs = parse_int(text)
        if eggs is None:
            send_message(chat_id, ERR_WHOLE_NUMBER)
            return
        state["eggs"] = eggs
        advance(chat_id, state, "ASK_BROKEN")

    elif step == "ASK_BROKEN":
        broken = parse_int(text)
        if broken is None:
            send_message(chat_id, ERR_WHOLE_NUMBER)
            return
        if broken > state["eggs"]:
            send_message(chat_id, err_broken_exceeds(state["eggs"]))
            return
        state["broken"] = broken
        advance(chat_id, state, "ASK_SOLD")

    elif step == "ASK_SOLD":
        sold = parse_int(text)
        if sold is None:
            send_message(chat_id, ERR_WHOLE_NUMBER)
            return
        state["sold"] = sold
        advance(chat_id, state, "ASK_REVENUE")

    elif step == "ASK_REVENUE":
        price_per_egg = parse_float(text)
        if price_per_egg is None:
            send_message(chat_id, ERR_NUMBER)
            return
        # Stored as the raw per-egg price, matching all existing rows in the
        # sheet - the total (sold x price) is computed only where it's
        # displayed (confirmation messages, dashboard), not written back here.
        state["revenue"] = price_per_egg
        start_notes_section(chat_id, state)

    elif step == "ASK_NOTES":
        state["notes"] = "" if text in ["የለም", "የለም።"] else text
        if state.get("also_issue"):
            start_issue_section(chat_id, state)
        else:
            finish_and_return_to_idle(chat_id, state)

    elif step == "ASK_ISSUE_WANT_VOICE":
        if text not in [BTN_YES, BTN_NO]:
            send_message(chat_id, ERR_CHOOSE, yes_no_keyboard())
            return
        if text == BTN_YES:
            advance(chat_id, state, "ASK_ISSUE_VOICE_UPLOAD")
        else:
            advance(chat_id, state, "ASK_ISSUE_WANT_IMAGE")

    elif step == "ASK_ISSUE_VOICE_UPLOAD":
        voice = message.get("voice")
        if not voice:
            send_message(chat_id, ERR_ISSUE_VOICE)
            return
        state["issue_voice_link"] = telegram_file_link(voice["file_id"])
        advance(chat_id, state, "ASK_ISSUE_WANT_IMAGE")

    elif step == "ASK_ISSUE_WANT_IMAGE":
        if text not in [BTN_YES, BTN_NO]:
            send_message(chat_id, ERR_CHOOSE, yes_no_keyboard())
            return
        if text == BTN_YES:
            advance(chat_id, state, "ASK_ISSUE_IMAGE_UPLOAD")
        else:
            advance(chat_id, state, "ASK_ISSUE_WANT_TEXT")

    elif step == "ASK_ISSUE_IMAGE_UPLOAD":
        photos = message.get("photo")
        if not photos:
            send_message(chat_id, ERR_ISSUE_IMAGE)
            return
        largest = photos[-1]  # Telegram sends multiple sizes; last is highest resolution
        state["issue_image_links"].append(telegram_file_link(largest["file_id"]))
        advance(chat_id, state, "ASK_ISSUE_IMAGE_MORE")

    elif step == "ASK_ISSUE_IMAGE_MORE":
        if text not in [BTN_YES, BTN_NO]:
            send_message(chat_id, ERR_CHOOSE, yes_no_keyboard())
            return
        if text == BTN_YES:
            advance(chat_id, state, "ASK_ISSUE_IMAGE_UPLOAD")
        else:
            advance(chat_id, state, "ASK_ISSUE_WANT_TEXT")

    elif step == "ASK_ISSUE_WANT_TEXT":
        if text not in [BTN_YES, BTN_NO]:
            send_message(chat_id, ERR_CHOOSE, yes_no_keyboard())
            return
        if text == BTN_YES:
            advance(chat_id, state, "ASK_ISSUE_TEXT_INPUT")
        else:
            finish_and_return_to_idle(chat_id, state)

    elif step == "ASK_ISSUE_TEXT_INPUT":
        if not text:
            send_message(chat_id, Q_ISSUE_TEXT)
            return
        state["issue_text"] = text
        finish_and_return_to_idle(chat_id, state)

    elif step == "EDIT_CHOOSE_FIELD":
        if text == BTN_DONE_EDITING:
            reporter = state["reporter_name"]
            reset_state(chat_id, reporter)
            send_message(chat_id, IDLE_PROMPT, idle_keyboard(chat_id))
            return
        field = next((f for f in EDIT_FIELDS if f["label"] == text), None)
        if not field:
            send_message(chat_id, ERR_CHOOSE, edit_field_keyboard())
            return
        state["editing_field"] = field
        state["step"] = "EDIT_AWAIT_VALUE"
        kb = with_back(site_keyboard()) if field["kind"] == "site" else remove_keyboard()
        send_message(chat_id, field["prompt"], kb)

    elif step == "EDIT_AWAIT_VALUE":
        field = state["editing_field"]
        if text == BTN_BACK and field["kind"] == "site":
            state["step"] = "EDIT_CHOOSE_FIELD"
            send_message(chat_id, FIELD_MENU_PROMPT, edit_field_keyboard())
            return
        if field["kind"] == "site":
            if text not in [SITE_A, SITE_B, SITE_C]:
                send_message(chat_id, ERR_SITE, site_keyboard())
                return
            value = text
        elif field["kind"] == "int":
            value = parse_int(text)
            if value is None:
                send_message(chat_id, ERR_WHOLE_NUMBER)
                return
        elif field["kind"] == "float":
            value = parse_float(text)
            if value is None:
                send_message(chat_id, ERR_NUMBER)
                return
        else:  # text
            value = "" if text in ["የለም", "የለም።"] else text

        row_num = last_entry.get(chat_id, {}).get("row")
        state["step"] = "EDIT_CHOOSE_FIELD"
        try:
            if row_num is None:
                raise ValueError("No last entry row to edit")
            sheet = get_sheet()
            sheet.update_acell(f"{field['col']}{row_num}", value)
            row_values = sheet.row_values(row_num)
            d = row_to_dict(row_values)
            lines = [f"{EDIT_SAVED} {field['label']}: {value}"]
            lines += build_row_summary(d)
            lines.append("ሌላ የሚያስተካክሉት ነገር ካለ ጨርሻለው የሚለውን ይጫኑ")
            send_message(chat_id, "\n".join(lines), edit_field_keyboard())
        except Exception:
            logger.exception("Failed to update field")
            send_message(chat_id, ERR_EDIT_FAILED, edit_field_keyboard())

    else:
        reset_state(chat_id)
        send_message(chat_id, IDLE_PROMPT, idle_keyboard(chat_id))


def save_and_confirm(chat_id, state):
    now = now_ethiopia()
    today = now.strftime("%Y-%m-%d")
    time_now = now.strftime("%H:%M:%S")
    images_joined = "\n".join(state["issue_image_links"])

    row = [
        today,
        time_now,
        state["reporter_name"],
        state["site"],
        state["report_type"],
        state["fodder_kg"],
        state["other_kg"],
        state["eggs"],
        state["broken"],
        state["sold"],
        state["revenue"],
        state["notes"],
        state["issue_text"],
        state["issue_voice_link"],
        images_joined,
    ]

    # NOTE: written by Claude, not verified by a fluent speaker yet
    lines = [f"ተቀብለናል ✅", f"ቦታ: {state['site']}", f"ሪፖርት: {state['report_type']}"]
    if wants_fodder(state):
        lines.append(f"መኖ: {FEED_FODDER} {state['fodder_kg']}ኪግ, {FEED_OTHER} {state['other_kg']}ኪግ")
    if wants_eggs(state):
        lines.append(f"እንቁላል: {state['eggs']}, የተሰበረ: {state['broken']}, የተሸጠ: {state['sold']}")
        total_revenue = state["sold"] * state["revenue"]
        lines.append(f"ገቢ: {total_revenue:g} ብር (በ1 እንቁላል {state['revenue']:g} ብር)")
    if state["notes"]:
        lines.append(f"ማስታወሻ: {state['notes']}")
    if wants_issue(state) or state.get("also_issue"):
        if state["issue_voice_link"]:
            lines.append("ድምጽ: ✅")
        if state["issue_image_links"]:
            lines.append(f"ምስል: ✅ ({len(state['issue_image_links'])})")
        if state["issue_text"]:
            lines.append(f"ጽሁፍ: {state['issue_text']}")

    try:
        sheet = get_sheet()
        sheet.append_row(row)
        row_num = len(sheet.get_all_values())
        last_entry[chat_id] = {"row": row_num, "report_type": state["report_type"]}
        lines.append("በሉ ላይ ተመዝግቧል። እናመሰግናለን!")
    except Exception:
        logger.exception("Failed to write to Google Sheets")
        # NOTE: written by Claude, not verified by a fluent speaker yet
        lines.append("መረጃዎን ሰብስበናል ነገር ግን በስህተት ምክንያት ወደ ሉህ ማስቀመጥ አልቻልንም። እባክዎ ለኃላፊዎ ይንገሩ።")

    send_message(chat_id, "\n".join(lines), remove_keyboard())


# ---------- Webhook endpoint ----------
@app.route("/webhook", methods=["POST"])
def webhook():
    update = request.get_json(force=True)
    logger.info("Update: %s", update)

    message = update.get("message")
    if message:
        # Accept text, voice, or photo messages - the state machine decides what's relevant.
        if "text" in message or "voice" in message or "photo" in message:
            chat_id = message["chat"]["id"]
            sender = message.get("from", {})
            user_id = sender.get("id")
            reporter_name = sender.get("first_name", "")
            if sender.get("last_name"):
                reporter_name += " " + sender["last_name"]
            if not reporter_name:
                reporter_name = sender.get("username", "Unknown")

            text = (message.get("text") or "").strip()

            if text == "/myid":
                send_message(chat_id, myid_message(user_id))
            elif not is_authorized(user_id):
                send_message(chat_id, unauthorized_message(user_id), parse_mode="HTML")
            else:
                handle_message(chat_id, message, reporter_name)

    return "ok"


@app.route("/file/<file_id>", methods=["GET"])
def serve_file(file_id):
    """Fetches a file from Telegram on demand and streams it back. This is what
    the links saved in the sheet point to, so clicking them plays/shows the
    voice note or photo without needing any external storage."""
    try:
        file_bytes = download_telegram_file(file_id)
    except Exception:
        logger.exception("Failed to fetch file %s from Telegram", file_id)
        return "File not found or no longer available.", 404

    # Detect file type from magic bytes so browsers/players handle it correctly.
    if file_bytes[:3] == b"\xff\xd8\xff":
        mimetype = "image/jpeg"
    elif file_bytes[:4] == b"OggS":
        mimetype = "audio/ogg"
    else:
        mimetype = "application/octet-stream"

    return Response(file_bytes, mimetype=mimetype)


def build_dashboard_data():
    """Aggregates Sheet1 into per-site-per-date series for the dashboard.
    Raises on failure so the API route can report a clean error."""
    sheet = get_sheet()
    all_values = sheet.get_all_values()
    data_rows = all_values[1:] if len(all_values) > 1 else []
    records = [row_to_dict(r) for r in data_rows if any(cell.strip() for cell in r)]

    def to_num(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return 0.0

    ALL_SITES = [SITE_A, SITE_B, SITE_C]

    by_date = {}
    by_site_date = {}  # site -> date -> totals, for per-site drill-down

    for r in records:
        date = (r.get("date") or "").strip()
        if not date:
            continue
        eggs = to_num(r.get("eggs"))
        broken = to_num(r.get("broken"))
        sold = to_num(r.get("sold"))
        # The sheet's revenue column stores the price per single egg (that's
        # what farmers have always entered), not a pre-computed total - so the
        # actual revenue for this row is sold x price, computed here rather
        # than trusting the raw column value as a total.
        price_per_egg = to_num(r.get("revenue"))
        revenue = sold * price_per_egg
        fodder = to_num(r.get("fodder_kg"))
        other = to_num(r.get("other_kg"))
        site = (r.get("site") or "ያልታወቀ").strip()

        d = by_date.setdefault(date, {"eggs": 0.0, "broken": 0.0, "sold": 0.0, "revenue": 0.0, "fodder_kg": 0.0, "other_kg": 0.0})
        for k, v in [("eggs", eggs), ("broken", broken), ("sold", sold), ("revenue", revenue), ("fodder_kg", fodder), ("other_kg", other)]:
            d[k] += v

        sd = by_site_date.setdefault(site, {}).setdefault(date, {"eggs": 0.0, "broken": 0.0, "sold": 0.0, "revenue": 0.0, "fodder_kg": 0.0, "other_kg": 0.0})
        for k, v in [("eggs", eggs), ("broken", broken), ("sold", sold), ("revenue", revenue), ("fodder_kg", fodder), ("other_kg", other)]:
            sd[k] += v

    dates_sorted = sorted(by_date.keys())
    # Always show all three known sites (even with zero data), plus any
    # unexpected site values found in the sheet, so nothing silently vanishes.
    extra_sites = sorted(s for s in by_site_date.keys() if s not in ALL_SITES)
    sites_sorted = ALL_SITES + extra_sites

    def series_for(source_by_date):
        return {
            "eggs": [source_by_date.get(d, {}).get("eggs", 0.0) for d in dates_sorted],
            "broken": [source_by_date.get(d, {}).get("broken", 0.0) for d in dates_sorted],
            "sold": [source_by_date.get(d, {}).get("sold", 0.0) for d in dates_sorted],
            "revenue": [source_by_date.get(d, {}).get("revenue", 0.0) for d in dates_sorted],
            "fodder_kg": [source_by_date.get(d, {}).get("fodder_kg", 0.0) for d in dates_sorted],
            "other_kg": [source_by_date.get(d, {}).get("other_kg", 0.0) for d in dates_sorted],
        }

    # Raw per-site (and combined "__ALL__") series aligned to the same date
    # axis - the front end slices/sums these live for any site + date-range
    # combination, so no page reload is needed to change either filter.
    site_series = {"__ALL__": series_for(by_date)}
    for site in sites_sorted:
        site_series[site] = series_for(by_site_date.get(site, {}))

    chart_data = {
        "today": now_ethiopia().strftime("%Y-%m-%d"),
        "dates": dates_sorted,
        "sites": sites_sorted,
        "series": site_series,
    }
    return chart_data


@app.route("/api/dashboard-data", methods=["GET"])
def api_dashboard_data():
    """JSON data feed for dashboard.html. Kept as a plain API endpoint (rather
    than baking data into the HTML) so the frontend is a normal static file
    you can open, edit, and debug on its own - not a Python string."""
    try:
        return jsonify(build_dashboard_data())
    except Exception:
        logger.exception("Failed to load dashboard data")
        return jsonify({"error": "የዳሽቦርድ መረጃ መጫን አልተቻለም። እባክዎ ትንሽ ቆይተው ደግመው ይሞክሩ።"}), 500


@app.route("/dashboard", methods=["GET"])
def dashboard():
    """Serves the standalone dashboard HTML file, which fetches its data
    from /api/dashboard-data at load time."""
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")



@app.route("/", methods=["GET"])
def home():
    has_poultry = bool(BOT_TOKEN and SHEET_ID and GOOGLE_CREDS_JSON)
    has_back = bool(os.environ.get("BACK_CHECKER_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN"))
    has_field = bool(os.environ.get("FIELD_CHECKER_BOT_TOKEN") or os.environ.get("FIELD_TELEGRAM_TOKEN"))

    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Unified Bot Service</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; max-width: 620px; margin: 40px auto; padding: 20px; line-height: 1.6; color: #1f2937; background: #fdfdfd; }}
        .header {{ border-bottom: 2px solid #e5e7eb; padding-bottom: 16px; margin-bottom: 24px; }}
        .card {{ background: #ffffff; border: 1px solid #e5e7eb; border-radius: 10px; padding: 18px 22px; margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }}
        .status {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 8px; vertical-align: middle; }}
        .status-on {{ background: #10b981; box-shadow: 0 0 6px #10b981; }}
        .status-off {{ background: #f59e0b; }}
        h1 {{ font-size: 24px; margin: 0 0 8px 0; color: #111827; }}
        h3 {{ margin: 0 0 10px 0; font-size: 17px; }}
        p {{ margin: 6px 0; font-size: 14px; color: #4b5563; }}
        a.btn {{ display: inline-block; margin-top: 8px; background: #2563eb; color: #fff; padding: 6px 14px; border-radius: 6px; text-decoration: none; font-size: 13px; font-weight: 500; }}
        a.btn:hover {{ background: #1d4ed8; }}
        .footer {{ font-size: 12px; color: #9ca3af; margin-top: 32px; text-align: center; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>🚀 Unified Bot Service</h1>
        <p>All three services running inside a single container (100% within Render's 750 free hours).</p>
    </div>

    <div class="card">
        <h3><span class="status {'status-on' if has_poultry else 'status-off'}"></span>🐔 Poultry Farm Tracker Bot</h3>
        <p>Service Type: <strong>Web Service &amp; Webhook</strong> (<code>/webhook</code>)</p>
        <p>Dashboard: Available at <code>/dashboard</code></p>
        <a class="btn" href="/dashboard">Open Farm Dashboard</a>
    </div>

    <div class="card">
        <h3><span class="status {'status-on' if has_back else 'status-off'}"></span>🔍 Back Checker Bot</h3>
        <p>Service Type: <strong>Telegram Long Polling Worker</strong></p>
        <p>Status: {'Configured &amp; Active' if has_back else 'Waiting for token configuration'}</p>
    </div>

    <div class="card">
        <h3><span class="status {'status-on' if has_field else 'status-off'}"></span>📋 Field Checker Bot</h3>
        <p>Service Type: <strong>Telegram Long Polling Worker</strong></p>
        <p>Status: {'Configured &amp; Active' if has_field else 'Waiting for token configuration'}</p>
    </div>

    <div class="footer">
        Health Status: <a href="/healthz">/healthz</a> • Ping Keep-Alive Target
    </div>
</body>
</html>"""
    return html

@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify({
        "status": "ok",
        "service": "unified-bot-service",
        "bots": {
            "poultry": bool(BOT_TOKEN and SHEET_ID and GOOGLE_CREDS_JSON),
            "back_checker": bool(os.environ.get("BACK_CHECKER_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN")),
            "field_checker": bool(os.environ.get("FIELD_CHECKER_BOT_TOKEN") or os.environ.get("FIELD_TELEGRAM_TOKEN"))
        }
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)