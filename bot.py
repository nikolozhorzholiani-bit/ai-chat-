"""
University Material Bot
- Admin: ფაილებს ტვირთავს (მხოლოდ ADMIN_IDS)
- სტუდენტი: კითხვას წერს → AI პასუხი + ფაილი
"""

import os, json, logging, asyncio, re
from pathlib import Path
from anthropic import Anthropic
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from extractor import extract_text

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BOT_TOKEN   = os.getenv("BOT_TOKEN", "")
ANTHRO_KEY  = os.getenv("ANTHROPIC_API_KEY", "")
ADMIN_IDS   = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
ALLOWED_IDS = [int(x) for x in os.getenv("ALLOWED_IDS", "").split(",") if x.strip().isdigit()]

DATA_DIR   = Path("data")
FILES_DIR  = DATA_DIR / "files"
INDEX_FILE = DATA_DIR / "index.json"
DATA_DIR.mkdir(exist_ok=True)
FILES_DIR.mkdir(exist_ok=True)

ai = Anthropic(api_key=ANTHRO_KEY)


# ── Index ─────────────────────────────────────────────────────

def load_index() -> list[dict]:
    if INDEX_FILE.exists():
        return json.loads(INDEX_FILE.read_text(encoding="utf-8")).get("docs", [])
    return []

def save_index(docs: list[dict]):
    INDEX_FILE.write_text(
        json.dumps({"docs": docs}, ensure_ascii=False, indent=2), encoding="utf-8"
    )

INDEX: list[dict] = load_index()


# ── Search ────────────────────────────────────────────────────

def keyword_score(query: str, doc: dict) -> int:
    q_words = set(re.findall(r"\w+", query.lower()))
    haystack = (doc["filename"] + " " + doc.get("subject", "") + " " + doc["text"][:3000]).lower()
    return sum(1 for w in q_words if len(w) > 2 and w in haystack)

def find_relevant(query: str, top_n: int = 3) -> list[dict]:
    scored = sorted(INDEX, key=lambda d: -keyword_score(query, d))
    hits   = [d for d in scored if keyword_score(query, d) > 0]
    return hits[:top_n] if hits else scored[:top_n]


# ── Claude helpers ────────────────────────────────────────────

async def pick_best(query: str, candidates: list[dict]) -> list[dict]:
    if len(candidates) == 1:
        return candidates

    doc_list = "\n".join(
        f"{i+1}. {d['filename']} (სუბიექტი: {d.get('subject','—')}) — "
        f"{d['text'][:300].replace(chr(10),' ')}"
        for i, d in enumerate(candidates)
    )
    prompt = (
        f"სტუდენტმა ითხოვა: \"{query}\"\n\n"
        f"ხელმისაწვდომი ფაილები:\n{doc_list}\n\n"
        f"მიუთითე მხოლოდ ნომერი(ები) რომლებიც ყველაზე შეესაბამება მოთხოვნას. "
        f"პასუხი: მხოლოდ მძიმით გამოყოფილი ნომრები, სხვა არაფერი."
    )
    try:
        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(None, lambda: ai.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=20,
            messages=[{"role": "user", "content": prompt}]
        ))
        nums = [int(x.strip()) for x in resp.content[0].text.split(",") if x.strip().isdigit()]
        picked = [candidates[n-1] for n in nums if 1 <= n <= len(candidates)]
        return picked if picked else candidates[:2]
    except Exception:
        return candidates[:2]


async def ai_answer(query: str, doc: dict) -> str:
    text = doc["text"][:6000]
    prompt = (
        f"სტუდენტის კითხვა: {query}\n\n"
        f"დოკუმენტი ({doc['filename']}):\n{text}\n\n"
        "გასცი დეტალური, ამომწურავი პასუხი ქართულად ამ დოკუმენტის მიხედვით. "
        "განმარტე ცნებები, მოიყვანე მაგალითები სადაც შესაძლებელია, და სტრუქტურა გამოიყენე (პუნქტები, სიები). "
        "თუ პასუხი დოკუმენტში ზუსტად არ არის, ისე თქვი და რაც გაქვს იმის მიხედვით უპასუხე."
    )
    try:
        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(None, lambda: ai.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        ))
        return resp.content[0].text.strip()
    except Exception as e:
        log.error("ai_answer error: %s", e)
        return ""


# ── Helpers ───────────────────────────────────────────────────

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def is_allowed(uid: int) -> bool:
    if not ALLOWED_IDS:
        return True
    return uid in ADMIN_IDS or uid in ALLOWED_IDS


# ── Handlers ──────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    if not is_allowed(uid):
        await update.message.reply_text("⛔ წვდომა შეზღუდულია.")
        return
    role = "👑 Admin" if is_admin(uid) else "👨‍🎓 სტუდენტი"
    n    = len(INDEX)
    text = (
        f"👋 გამარჯობა! <b>University Materials Bot</b>\n"
        f"როლი: {role} · ინდექსში: <b>{n} ფაილი</b>\n\n"
        "დამიწერე კითხვა და AI პასუხს მოგცემს + ფაილს გამოგიგზავნის.\n\n"
        "/list — ხელმისაწვდომი მასალები\n"
    )
    if is_admin(uid):
        text += "/upload — ფაილის ატვირთვა\n/delete — ფაილის წაშლა"
    await update.message.reply_text(text, parse_mode="HTML")


async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ წვდომა შეზღუდულია.")
        return
    if not INDEX:
        await update.message.reply_text("📭 მასალები ჯერ არ არის ატვირთული.")
        return
    lines = []
    for i, d in enumerate(INDEX, 1):
        subj = f" · <i>{d['subject']}</i>" if d.get("subject") else ""
        lines.append(f"{i}. 📄 {d['filename']}{subj}")
    await update.message.reply_text(
        "<b>📚 ხელმისაწვდომი მასალები:</b>\n\n" + "\n".join(lines),
        parse_mode="HTML"
    )



async def cmd_upload(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ მხოლოდ Admin-ისთვის.")
        return
    await update.message.reply_text(
        "📤 გამომიგზავნე ფაილი (PDF, DOCX, PPTX, XLSX, TXT)\n"
        "<i>სურვილისამებრ caption-ად მიუთითე სუბიექტი, მაგ: მათემატიკა I</i>",
        parse_mode="HTML"
    )


async def cmd_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ მხოლოდ Admin-ისთვის.")
        return
    if not INDEX:
        await update.message.reply_text("ინდექსი ცარიელია.")
        return
    btns = [
        [InlineKeyboardButton(f"🗑 {d['filename']}", callback_data=f"del:{d['id']}")]
        for d in INDEX
    ]
    await update.message.reply_text("რომელი წავშალო?", reply_markup=InlineKeyboardMarkup(btns))


async def handle_delete_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    doc_id = query.data.split(":", 1)[1]
    doc = next((d for d in INDEX if d["id"] == doc_id), None)
    if not doc:
        await query.edit_message_text("⚠️ ვერ მოიძებნა.")
        return
    INDEX.remove(doc)
    save_index(INDEX)
    fp = FILES_DIR / doc["filename"]
    if fp.exists():
        fp.unlink()
    await query.edit_message_text(f"✅ წაიშალა: {doc['filename']}")


async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ ფაილების ატვირთვა მხოლოდ Admin-ს შეუძლია.")
        return

    doc      = update.message.document
    filename = doc.file_name or "file"
    subject  = (update.message.caption or "").strip()

    msg = await update.message.reply_text(f"⏳ ვამუშავებ: <b>{filename}</b>…", parse_mode="HTML")

    tg_file = await ctx.bot.get_file(doc.file_id)
    data    = bytes(await tg_file.download_as_bytearray())

    file_path = FILES_DIR / filename
    file_path.write_bytes(data)

    text = extract_text(filename, data)
    if len(text) < 20:
        await msg.edit_text(
            f"✅ <b>{filename}</b> შენახულია.\n"
            f"⚠️ ტექსტი ვერ ამოვიღე — ძიება შეიძლება ნაკლებად ზუსტი იყოს.",
            parse_mode="HTML"
        )
        text = filename

    doc_id = filename.replace(" ", "_").lower()
    INDEX[:] = [d for d in INDEX if d["id"] != doc_id]
    INDEX.append({
        "id":       doc_id,
        "filename": filename,
        "subject":  subject,
        "text":     text,
        "size":     len(text),
    })
    save_index(INDEX)

    await msg.edit_text(
        f"✅ <b>{filename}</b> დაემატა.\n"
        f"📁 სუბიექტი: {subject or '—'} · {len(text):,} სიმბ.",
        parse_mode="HTML"
    )


async def handle_question(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ წვდომა შეზღუდულია.")
        return
    query = (update.message.text or "").strip()
    if not query:
        return

    if not INDEX:
        await update.message.reply_text("📭 მასალები ჯერ არ არის ატვირთული. მოგვიანებით სცადე.")
        return

    msg = await update.message.reply_text("🔍 ვეძებ…")

    candidates = find_relevant(query, top_n=3)
    best       = await pick_best(query, candidates)

    await msg.delete()

    if not best:
        await update.message.reply_text("😔 ამ თემაზე მასალა ვერ ვიპოვე.\n/list — ნახე რა გვაქვს.")
        return

    # AI პასუხი პირველი (საუკეთესო) დოკუმენტის მიხედვით
    answer = await ai_answer(query, best[0])
    if answer:
        await update.message.reply_text(f"🤖 <b>პასუხი:</b>\n\n{answer}", parse_mode="HTML")

    # ფაილ(ებ)ის გაგზავნა
    for doc in best:
        fp = FILES_DIR / doc["filename"]
        if not fp.exists():
            await update.message.reply_text(f"⚠️ ფაილი ვერ მოიძებნა: {doc['filename']}")
            continue
        subj    = f" · {doc['subject']}" if doc.get("subject") else ""
        caption = f"📄 <b>{doc['filename']}</b>{subj}"
        with open(fp, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=doc["filename"],
                caption=caption,
                parse_mode="HTML"
            )


# ── Main ──────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN არ არის .env-ში")
    if not ANTHRO_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY არ არის .env-ში")
    if not ADMIN_IDS:
        log.warning("⚠️  ADMIN_IDS ცარიელია — ფაილების ატვირთვა გათიშულია")

    log.info("📚 University Bot — %d docs in index, admins: %s", len(INDEX), ADMIN_IDS)

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("list",     cmd_list))
    app.add_handler(CommandHandler("upload",   cmd_upload))
    app.add_handler(CommandHandler("delete",   cmd_delete))
    app.add_handler(CallbackQueryHandler(handle_delete_cb, pattern="^del:"))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_question))

    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
