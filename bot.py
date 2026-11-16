import asyncio
import os
import re
import uuid
import requests
from bs4 import BeautifulSoup
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
import yt_dlp

BOT_TOKEN = os.getenv("BOT_TOKEN", "ТВОЙ_ТОКЕН_ОТ_BOTFATHER")

# Вставь сюда file_id стикера, который выдал @idstickerbot
LOADING_STICKER_ID = "CAACAgIAAxkBAAESNx5q-0Qmr98xPFs5eFgU2hbd2bfT_AACPJwAApKsKUtieQABGFB4MGI9BA"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

os.makedirs("downloads", exist_ok=True)

SUPPORTED_DOMAINS = (
    "tiktok.com",
    "instagram.com",
    "instagr.am",
    "pinterest.com",
    "pinterest.ru",
    "pin.it",
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def extract_url(text: str) -> str | None:
    for word in text.split():
        if any(domain in word.lower() for domain in SUPPORTED_DOMAINS):
            return word.strip()
    return None


def clean_tiktok_url(url: str) -> str:
    """Убирает лишние трекинг-параметры (?_r=1...) из длинных ссылок TikTok."""
    if "/photo/" in url or "/video/" in url:
        return url.split("?")[0]
    return url


def download_video_ytdlp(url: str) -> str:
    """Скачивает готовый mp4 со звуком."""
    file_id = str(uuid.uuid4())
    output_path = f"downloads/{file_id}.mp4"

    ydl_opts = {
        "outtmpl": output_path,
        "format": "best[ext=mp4]/best",
        "quiet": True,
        "no_warnings": True,
        "http_headers": HEADERS,
    }

    if os.path.exists("cookies.txt"):
        ydl_opts["cookiefile"] = "cookies.txt"

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    return output_path


def download_pinterest_image(url: str) -> str:
    """Вытаскивает картинку пина в оригинальном качестве."""
    resp = requests.get(url, headers=HEADERS, allow_redirects=True, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    meta = soup.find("meta", property="og:image")
    if not meta or not meta.get("content"):
        raise ValueError("Не удалось найти картинку пина на странице.")

    img_url = meta["content"]
    orig_url = re.sub(r'i\.pinimg\.com/[^/]+/', 'i.pinimg.com/originals/', img_url)
    img_resp = requests.get(orig_url, headers=HEADERS, timeout=20)

    if img_resp.status_code == 200:
        final_url = orig_url
        img_data = img_resp.content
    else:
        final_url = img_url
        img_data = requests.get(final_url, headers=HEADERS, timeout=20).content

    ext = final_url.split(".")[-1].split("?")[0]
    if ext not in ("jpg", "jpeg", "png", "webp"):
        ext = "jpg"

    file_path = f"downloads/{uuid.uuid4()}.{ext}"
    with open(file_path, "wb") as f:
        f.write(img_data)

    return file_path


async def delete_loading_ui(status_msg: types.Message, sticker_msg: types.Message | None):
    """Удаляет и сообщение 'Загружаю...', и стикер."""
    try:
        await status_msg.delete()
    except Exception:
        pass

    if sticker_msg:
        try:
            await sticker_msg.delete()
        except Exception:
            pass


@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Скинь мне ссылку из:\n"
        "• **TikTok** — видео без водяного знака\n"
        "• **Pinterest** — видео-пины и картинки в оригинале\n"
        "• **Instagram** — Reels и видеопосты",
        parse_mode="Markdown"
    )


@dp.message(F.text)
async def handle_link(message: types.Message):
    url = extract_url(message.text)
    if not url:
        return

    status_msg = await message.answer("⏳ Загружаю...")
    sticker_msg = None
    if LOADING_STICKER_ID and LOADING_STICKER_ID != "ВСТАВЬ_СЮДА_FILE_ID_СТИКЕРА":
        try:
            sticker_msg = await message.answer_sticker(sticker=LOADING_STICKER_ID)
        except Exception:
            sticker_msg = None

    files_to_clean = []

    try:
        lower_url = url.lower()

        if "tiktok.com" in lower_url:
            if "/photo/" in lower_url:
                if sticker_msg:
                    try:
                        await sticker_msg.delete()
                    except Exception:
                        pass
                await status_msg.edit_text("❌ TikTok защитил фото-посты от скачивания с облачных серверов. Закидывай видео — их качает на ура!")
                return

            video_path = await asyncio.to_thread(download_video_ytdlp, clean_tiktok_url(url))
            files_to_clean.append(video_path)
            await send_checked_video(message, status_msg, sticker_msg, video_path)

        elif any(d in lower_url for d in ("pinterest.com", "pinterest.ru", "pin.it")):
            try:
                video_path = await asyncio.to_thread(download_video_ytdlp, url)
                files_to_clean.append(video_path)
                await send_checked_video(message, status_msg, sticker_msg, video_path)
            except Exception:
                img_path = await asyncio.to_thread(download_pinterest_image, url)
                files_to_clean.append(img_path)
                await message.answer_photo(photo=types.FSInputFile(img_path))
                await delete_loading_ui(status_msg, sticker_msg)

        else:
            video_path = await asyncio.to_thread(download_video_ytdlp, url)
            files_to_clean.append(video_path)
            await send_checked_video(message, status_msg, sticker_msg, video_path)

    except Exception as e:
        if sticker_msg:
            try:
                await sticker_msg.delete()
            except Exception:
                pass

        error_text = str(e).lower()
        if "login required" in error_text or "cookies" in error_text:
            await status_msg.edit_text("❌ Instagram требует авторизацию для этого поста.")
        else:
            await status_msg.edit_text(f"❌ Не удалось скачать материал:\n`{str(e)[:200]}`", parse_mode="Markdown")

    finally:
        for file_path in files_to_clean:
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except OSError:
                    pass


async def send_checked_video(
    message: types.Message,
    status_msg: types.Message,
    sticker_msg: types.Message | None,
    video_path: str,
):
    file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
    if file_size_mb > 49.5:
        if sticker_msg:
            try:
                await sticker_msg.delete()
            except Exception:
                pass
        await status_msg.edit_text(f"❌ Видео весит {file_size_mb:.1f} МБ (лимит Telegram — 50 МБ).")
        return

    await message.answer_video(video=types.FSInputFile(video_path))
    await delete_loading_ui(status_msg, sticker_msg)


async def health_check(request):
    return web.Response(text="Bot is alive!")


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()


async def main():
    await start_web_server()
    print("Бот и веб-сервер запущены...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
