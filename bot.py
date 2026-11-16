import asyncio
import json
import os
import re
import uuid
import requests
from bs4 import BeautifulSoup
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.utils.media_group import MediaGroupBuilder
import yt_dlp

BOT_TOKEN = os.getenv("BOT_TOKEN", "ТВОЙ_ТОКЕН_ОТ_BOTFATHER")

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


def extract_tiktok_id(url: str) -> str | None:
    """Извлекает числовой ID поста из ссылки TikTok."""
    clean = clean_tiktok_url(url)
    match = re.search(r'/(?:video|photo)/(\d+)', clean)
    if match:
        return match.group(1)

    try:
        r = requests.head(url, headers=HEADERS, allow_redirects=True, timeout=15)
        match = re.search(r'/(?:video|photo)/(\d+)', r.url)
        if match:
            return match.group(1)
    except Exception:
        pass
    return None


def download_video_ytdlp(url: str) -> str:
    """Скачивает готовый mp4 со звуком (не требует FFmpeg на сервере)."""
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


def save_media_list(image_urls: list[str], music_url: str | None, session=None) -> tuple[list[str], str | None]:
    """Скачивает список картинок и аудио на диск."""
    fetcher = session or requests
    saved_images = []
    for img_url in image_urls:
        try:
            r = fetcher.get(img_url, headers=HEADERS, timeout=20)
            if r.status_code == 200 and len(r.content) > 500:
                img_path = f"downloads/{uuid.uuid4()}.jpg"
                with open(img_path, "wb") as f:
                    f.write(r.content)
                saved_images.append(img_path)
        except Exception:
            continue

    audio_path = None
    if music_url and saved_images:
        try:
            ar = fetcher.get(music_url, headers=HEADERS, timeout=20)
            if ar.status_code == 200 and len(ar.content) > 1000:
                audio_path = f"downloads/{uuid.uuid4()}.mp3"
                with open(audio_path, "wb") as f:
                    f.write(ar.content)
        except Exception:
            audio_path = None

    return saved_images, audio_path


def get_tiktok_slideshow(url: str) -> tuple[list[str], str | None]:
    """Каскад из мобильного API и запасных методов для скачивания фото из TikTok."""
    clean_url = clean_tiktok_url(url)
    aweme_id = extract_tiktok_id(clean_url)

    # Способ 1: Официальный мобильный API TikTok по ID поста
    if aweme_id:
        try:
            mobile_headers = {
                "User-Agent": "com.zhiliaoapp.musically/2023501030 (Linux; U; Android 13; en_US; Pixel 7; Build/TQ3A.230805.001; Cronet/58.0.2991.0)",
            }
            api_url = f"https://api16-normal-c-useast1a.tiktokv.com/aweme/v1/feed/?aweme_id={aweme_id}"
            r = requests.get(api_url, headers=mobile_headers, timeout=15)
            if r.status_code == 200 and r.text.strip().startswith("{"):
                aweme_list = r.json().get("aweme_list", [])
                for item in aweme_list:
                    if str(item.get("aweme_id")) == str(aweme_id):
                        images_data = item.get("image_post_info", {}).get("images", [])
                        img_urls = []
                        for img_obj in images_data:
                            url_list = img_obj.get("display_image", {}).get("url_list", [])
                            if url_list:
                                img_urls.append(url_list[0])
                        if img_urls:
                            music_urls = item.get("music", {}).get("play_url", {}).get("url_list", [])
                            music_url = music_urls[0] if music_urls else None
                            return save_media_list(img_urls, music_url)
        except Exception:
            pass

    # Способ 2: TikWM API
    try:
        tikwm_headers = {
            **HEADERS,
            "Referer": "https://www.tikwm.com/",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        }
        r = requests.get("https://www.tikwm.com/api/", params={"url": clean_url, "hd": 1}, headers=tikwm_headers, timeout=15)
        if r.status_code == 200 and r.text.strip().startswith("{"):
            data = r.json().get("data", {})
            images = data.get("images")
            if images:
                return save_media_list(images, data.get("music"))
    except Exception:
        pass

    # Способ 3: Прямой парсинг HTML
    try:
        session = requests.Session()
        resp = session.get(clean_url, headers=HEADERS, allow_redirects=True, timeout=20)
        soup = BeautifulSoup(resp.text, "html.parser")

        script = soup.find("script", id="__UNIVERSAL_DATA_FOR_REHYDRATION__")
        if script and script.string:
            raw_data = json.loads(script.string)
            default_scope = raw_data.get("__DEFAULT_SCOPE__", {})
            video_detail = default_scope.get("webapp.video-detail", {})
            item_struct = video_detail.get("itemInfo", {}).get("itemStruct", {})

            images_list = item_struct.get("imagePost", {}).get("images", [])
            img_urls = []
            for img_obj in images_list:
                urls = img_obj.get("imageURL", {}).get("urlList", [])
                if urls:
                    img_urls.append(urls[0])

            if img_urls:
                music_url = item_struct.get("music", {}).get("playUrl")
                return save_media_list(img_urls, music_url, session=session)
    except Exception:
        pass

    return [], None


@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Скинь мне ссылку из:\n"
        "• **TikTok** — видео без водяного знака, фото и альбомы с музыкой\n"
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
    files_to_clean = []

    try:
        lower_url = url.lower()

        if "tiktok.com" in lower_url:
            images, audio = await asyncio.to_thread(get_tiktok_slideshow, url)
            if images:
                files_to_clean.extend(images)
                if audio:
                    files_to_clean.append(audio)

                for i in range(0, len(images), 10):
                    chunk = images[i:i + 10]
                    if len(chunk) == 1:
                        await message.answer_photo(photo=types.FSInputFile(chunk[0]))
                    else:
                        album = MediaGroupBuilder()
                        for img_path in chunk:
                            album.add_photo(media=types.FSInputFile(img_path))
                        await message.answer_media_group(media=album.build())

                if audio:
                    await message.answer_audio(audio=types.FSInputFile(audio), title="Звук из TikTok")

                await status_msg.delete()
                return

            if "/photo/" in lower_url:
                await status_msg.edit_text("❌ Не удалось вытащить фото из этого поста TikTok (возможно, пост приватный или защищён).")
                return

            video_path = await asyncio.to_thread(download_video_ytdlp, clean_tiktok_url(url))
            files_to_clean.append(video_path)
            await send_checked_video(message, status_msg, video_path)

        elif any(d in lower_url for d in ("pinterest.com", "pinterest.ru", "pin.it")):
            try:
                video_path = await asyncio.to_thread(download_video_ytdlp, url)
                files_to_clean.append(video_path)
                await send_checked_video(message, status_msg, video_path)
            except Exception:
                img_path = await asyncio.to_thread(download_pinterest_image, url)
                files_to_clean.append(img_path)
                await message.answer_photo(photo=types.FSInputFile(img_path))
                await status_msg.delete()

        else:
            video_path = await asyncio.to_thread(download_video_ytdlp, url)
            files_to_clean.append(video_path)
            await send_checked_video(message, status_msg, video_path)

    except Exception as e:
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


async def send_checked_video(message: types.Message, status_msg: types.Message, video_path: str):
    file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
    if file_size_mb > 49.5:
        await status_msg.edit_text(f"❌ Видео весит {file_size_mb:.1f} МБ (лимит Telegram — 50 МБ).")
        return

    await message.answer_video(video=types.FSInputFile(video_path))
    await status_msg.delete()


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
