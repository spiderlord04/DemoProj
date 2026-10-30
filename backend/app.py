import os
import uuid
import shutil
import subprocess
import re
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS
from werkzeug.utils import secure_filename
from PIL import Image, ImageDraw, ImageFont

# Load environment configuration
load_dotenv()

app = Flask(__name__)
frontend_origin = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
CORS(app, resources={r"/*": {"origins": "*"}})

BASE = Path(__file__).resolve().parent
UPLOADS = BASE / "uploads"
OUTPUTS = BASE / "generated_videos"
UPLOADS.mkdir(parents=True, exist_ok=True)
OUTPUTS.mkdir(parents=True, exist_ok=True)

# Detect device
device = "cpu"
try:
    import torch
    if torch.cuda.is_available():
        device = "cuda"
except Exception:
    pass

# Locate FFmpeg binary
def get_ffmpeg_executable():
    # 1. Bundled imageio-ffmpeg
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            return exe
    except Exception:
        pass
    # 2. Virtual environment Scripts
    venv_exe = BASE / ".venv" / "Scripts" / "ffmpeg.exe"
    if venv_exe.exists():
        return str(venv_exe)
    # 3. System PATH
    sys_exe = shutil.which("ffmpeg")
    if sys_exe:
        return sys_exe
    return "ffmpeg"

FFMPEG_BIN = get_ffmpeg_executable()
if os.path.exists(FFMPEG_BIN):
    ffmpeg_dir = str(Path(FFMPEG_BIN).parent)
    if ffmpeg_dir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")

import requests

# Stock Video Configuration
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "").strip()

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "for", "with", "is", "are", "was",
    "were", "be", "been", "being", "as", "at", "by", "from", "that", "this", "these", "those", "it",
    "its", "they", "them", "their", "there", "here", "which", "who", "whom", "whose", "what", "when",
    "where", "why", "how", "also", "such", "can", "could", "will", "would", "shall", "should", "may",
    "might", "must", "do", "does", "did", "done", "has", "have", "had", "not", "no", "nor", "so",
    "than", "then", "too", "very", "just", "more", "most", "other", "others", "some", "any", "each",
    "both", "all", "one", "two", "first", "second", "third", "using", "use", "used", "uses", "called",
    "known", "include", "includes", "including", "about", "into", "over", "out", "up", "down",
    "between", "during", "through", "after", "before", "while", "because", "however", "therefore",
    "thus", "many", "much", "often", "still", "even", "explain", "describe", "tell", "give", "write",
    "explanation", "explains", "explaining", "clear", "clearly", "means", "matter", "matters",
    "important", "importance", "overview", "concept", "concepts", "idea", "ideas", "example",
    "examples", "topic", "subject", "lesson", "video", "understand", "understanding", "discuss",
    "discussion", "consider", "today", "start",
}

def _query_words(text):
    """Meaningful lowercase words used to judge whether an image title matches a query."""
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 1 and w not in _STOPWORDS}

def _extract_keywords(text, limit=2):
    """Pick the most distinctive words from a sentence (longer words first), ignoring stopwords."""
    seen, candidates = set(), []
    for w in re.findall(r"[A-Za-z][A-Za-z\-]{2,}", text):
        lw = w.lower()
        if lw in _STOPWORDS or lw in seen:
            continue
        seen.add(lw)
        candidates.append(w)
    candidates.sort(key=len, reverse=True)
    return candidates[:limit]

def _image_queries(topic, text):
    """Build search queries anchored on the topic so images stay relevant."""
    topic = (topic or "").strip()
    keywords = _extract_keywords(text, limit=2)
    queries, seen = [], set()

    def add(q):
        q = re.sub(r"\s+", " ", q).strip()
        if q and q.lower() not in seen:
            seen.add(q.lower())
            queries.append(q)

    if topic:
        for kw in keywords:
            if kw.lower() not in topic.lower():
                add(f"{topic} {kw}")
        add(topic)
    if keywords:
        add(" ".join(keywords))
    return queries

def fetch_stock_video(query, output_path, duration_target):
    """Search Pexels for a relevant video clip and download it."""
    if not PEXELS_API_KEY:
        return False
    
    try:
        headers = {"Authorization": PEXELS_API_KEY}
        url = f"https://api.pexels.com/videos/search?query={query}&per_page=1&orientation=landscape"
        res = requests.get(url, headers=headers, timeout=10)
        data = res.json()
        
        if data.get("videos"):
            video = data["videos"][0]
            # Get the HD file link
            files = video.get("video_files", [])
            # Prefer mp4 and width around 1280 or 1920
            best_file = None
            for f in files:
                if f.get("file_type") == "video/mp4":
                    if not best_file or abs(f.get("width", 0) - 1280) < abs(best_file.get("width", 0) - 1280):
                        best_file = f
            
            if best_file:
                v_res = requests.get(best_file["link"], stream=True, timeout=30)
                with open(output_path, 'wb') as f:
                    for chunk in v_res.iter_content(chunk_size=8192):
                        f.write(chunk)
                return True
    except Exception as e:
        app.logger.warning(f"Stock video fetch failed for '{query}': {e}")
    return False

def _download_image(url, output_path):
    try:
        resp = requests.get(url, timeout=20,
                            headers={"User-Agent": "AutoAnimate/1.0 (educational video generator)"})
        if resp.status_code == 200 and len(resp.content) > 5000:
            with open(output_path, "wb") as f:
                f.write(resp.content)
            return True
    except Exception:
        pass
    return False

def _wikimedia_image(query):
    """Keyless, license-friendly image search on Wikimedia Commons, best title match first."""
    try:
        resp = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query", "format": "json", "generator": "search",
                "gsrsearch": f"filetype:bitmap {query}", "gsrlimit": 10, "gsrnamespace": 6,
                "prop": "imageinfo", "iiprop": "url", "iiurlwidth": 1280,
            },
            headers={"User-Agent": "AutoAnimate/1.0 (educational video generator)"},
            timeout=20,
        )
        pages = resp.json().get("query", {}).get("pages", {})
        query_words = _query_words(query)
        scored = []
        for page in pages.values():
            info = page.get("imageinfo") or []
            if not info or not info[0].get("thumburl"):
                continue
            # Only keep images whose file title actually mentions the search terms.
            score = len(query_words & _query_words(page.get("title", "")))
            scored.append((score, info[0]["thumburl"]))
        scored.sort(key=lambda item: -item[0])
        for score, url in scored:
            if score > 0:
                yield url
    except Exception as e:
        app.logger.warning(f"Wikimedia image search failed for '{query}': {e}")

def _duckduckgo_images(query):
    """Keyless image search via DuckDuckGo (secondary source), best title match first."""
    try:
        from duckduckgo_search import DDGS
        query_words = _query_words(query)
        scored = []
        for r in DDGS().images(query, max_results=10, safesearch="moderate"):
            if not r.get("image"):
                continue
            score = len(query_words & _query_words(r.get("title", "")))
            scored.append((score, r["image"]))
        scored.sort(key=lambda item: -item[0])
        for score, url in scored:
            if score > 0:
                yield url
    except Exception as e:
        app.logger.warning(f"DuckDuckGo image search failed for '{query}': {e}")

# Cached image search results so each segment does not repeat the same network requests
_image_pool = {}

def _search_images(query):
    """Return a scored list of candidate image URLs for a query, cached across segments."""
    key = query.lower().strip()
    if key in _image_pool:
        return _image_pool[key]
    urls = []
    for source in (_wikimedia_image, _duckduckgo_images):
        urls.extend(source(query))
        if len(urls) >= 6:
            break
    _image_pool[key] = urls
    return urls

def fetch_topic_image(queries, output_path):
    """Fetch a relevant image without an API key, rotating through cached results for variety."""
    if isinstance(queries, str):
        queries = [queries]
    for query in queries:
        candidates = _search_images(query)
        for _ in range(len(candidates)):
            url = candidates.pop(0)
            candidates.append(url)  # rotate so the next segment gets a different image
            if _download_image(url, output_path):
                return True
    return False

def render_image_clip(image_path, clip_path, duration, fps, zoom_in=True):
    """Turn a still image into a video clip with a clearly visible Ken Burns motion effect."""
    total_frames = max(int(round(duration * fps)), 1)
    # Zoom across the clip and add a gentle drift so the motion reads as real video.
    if zoom_in:
        zoom_expr = "min(1.0+0.0028*on,1.4)"
    else:
        zoom_expr = "max(1.4-0.0028*on,1.0)"
    x_expr = "iw/2-(iw/zoom/2)+sin(on/45)*45"
    y_expr = "ih/2-(ih/zoom/2)+cos(on/55)*30"
    vf = (
        "scale=2560:1440:force_original_aspect_ratio=increase,crop=2560:1440,"
        f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':d={total_frames}:s=1280x720:fps={fps},"
        "format=yuv420p"
    )
    cmd = [
        FFMPEG_BIN, "-y",
        "-loop", "1",
        "-i", str(Path(image_path).resolve()),
        "-frames:v", str(total_frames),
        "-vf", vf,
        "-r", str(fps),
        "-c:v", "libx264", "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-an",
        str(Path(clip_path).resolve())
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=180)

def get_key_point(text):
    """Produce a short on-screen key point (at most ~6 words) for a sentence."""
    global gemini_model
    if gemini_model:
        try:
            prompt = (
                "Summarize the following sentence as a short on-screen key point of at most 6 words. "
                "Return ONLY the key point, no quotes and no trailing punctuation.\n\n"
                f"Sentence: {text}"
            )
            kp = gemini_model.generate_content(prompt).text.strip().strip('"').strip()
            if kp:
                return kp
        except Exception:
            pass
    words = re.sub(r"[^\w\s]", "", text).split()
    return " ".join(words[:6]).capitalize() if words else text[:40]

def make_caption_overlay(key_point, step_idx, out_path):
    """Create a transparent 1280x720 banner PNG with the key point (kept static over the moving clip)."""
    target_w, target_h = 1280, 720
    overlay = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font_kp = _load_font_bold(30)
    font_badge = _load_font_bold(18)

    lines = _wrap_text(draw, key_point.strip(), font_kp, target_w - 170)[:2]
    pad, line_h = 18, 40
    banner_h = len(lines) * line_h + pad * 2
    draw.rounded_rectangle([40, 40, target_w - 40, 40 + banner_h], radius=14, fill=(10, 14, 24, 205))
    draw.rectangle([40, 40, 47, 40 + banner_h], fill=(255, 139, 43, 255))
    y = 40 + pad
    for line in lines:
        draw.text((70, y), line, font=font_kp, fill=(255, 255, 255, 255))
        y += line_h

    badge = f"{step_idx:02d}"
    bb = draw.textbbox((0, 0), badge, font=font_badge)
    bw, bh = bb[2] - bb[0] + 20, bb[3] - bb[1] + 12
    draw.rounded_rectangle([target_w - 40 - bw, 44, target_w - 40, 44 + bh], radius=8, fill=(255, 139, 43, 235))
    draw.text((target_w - 40 - bw + 10, 49), badge, font=font_badge, fill=(15, 15, 20, 255))

    overlay.save(out_path, "PNG")

def apply_caption_overlay(clip_path, caption_png, out_path, duration):
    """Fade the static caption banner in over the moving (zoomed) clip."""
    cmd = [
        FFMPEG_BIN, "-y",
        "-i", str(Path(clip_path).resolve()),
        "-loop", "1", "-t", f"{duration:.3f}", "-i", str(Path(caption_png).resolve()),
        "-filter_complex",
        "[1:v]format=rgba,fade=in:st=0:d=0.5:alpha=1[cap];"
        "[0:v][cap]overlay=0:0:eof_action=repeat,format=yuv420p[v]",
        "-map", "[v]",
        "-c:v", "libx264", "-preset", "veryfast",
        "-pix_fmt", "yuv420p", "-an",
        str(Path(out_path).resolve())
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=180)

gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
gemini_model = None
gemini_model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

if gemini_key:
    try:
        import google.generativeai as genai
        genai.configure(api_key=gemini_key)
        gemini_model = genai.GenerativeModel(gemini_model_name)
    except Exception as e:
        app.logger.warning(f"Failed to initialize Gemini with key: {e}")

# MongoDB Client
mongo = None
videos_collection = None
mongo_uri = os.getenv("MONGO_URI", "").strip()
if mongo_uri:
    try:
        from pymongo import MongoClient
        mongo = MongoClient(mongo_uri, serverSelectionTimeoutMS=2500)
        mongo.admin.command("ping")
        videos_collection = mongo["autoanimateDB"]["videos"]
        app.logger.info("Connected to MongoDB successfully.")
    except Exception as e:
        app.logger.warning(f"MongoDB connection skipped or failed: {e}")
        mongo = None
        videos_collection = None

# Lazy Whisper loader
_whisper_model = None
def get_whisper():
    global _whisper_model
    if _whisper_model is None:
        try:
            import whisper
            model_name = os.getenv("WHISPER_MODEL", "base")
            _whisper_model = whisper.load_model(model_name, device=device)
        except Exception as e:
            app.logger.warning(f"Whisper load skipped/failed: {e}")
            return None
    return _whisper_model

# Lazy Stable Diffusion loader
_sd_pipe = None
def get_stable_diffusion():
    global _sd_pipe
    if os.getenv("USE_STABLE_DIFFUSION", "false").lower() != "true":
        return None
    if _sd_pipe is None:
        try:
            from diffusers import StableDiffusionPipeline
            sd_model = os.getenv("SD_MODEL", "SG161222/Realistic_Vision_V5.1_noVAE")
            pipe = StableDiffusionPipeline.from_pretrained(
                sd_model,
                torch_dtype=torch.float16 if device == "cuda" else torch.float32,
                safety_checker=None
            ).to(device)
            if device == "cuda":
                pipe.enable_attention_slicing()
            _sd_pipe = pipe
        except Exception as e:
            app.logger.warning(f"Stable Diffusion could not be loaded: {e}")
            return None
    return _sd_pipe

def format_timestamp(seconds):
    h, rem = divmod(float(seconds), 3600)
    m, sec = divmod(rem, 60)
    ms = int((sec - int(sec)) * 1000)
    return f"{int(h):02}:{int(m):02}:{int(sec):02},{ms:03}"

def split_into_sentences(text):
    text = text.strip()
    sentences = re.split(r'(?<=[.!?])\s+', text)
    clean = [s.strip() for s in sentences if len(s.strip()) > 3]
    return clean if clean else [text]

def get_audio_duration(audio_path):
    """Inspect duration of an audio file using FFmpeg."""
    try:
        cmd = [
            FFMPEG_BIN, "-i", str(audio_path)
        ]
        res = subprocess.run(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True, errors="replace")
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", res.stderr)
        if match:
            h, m, s = match.groups()
            return int(h) * 3600 + int(m) * 60 + float(s)
    except Exception as e:
        app.logger.warning(f"Duration inspection error: {e}")
    return 15.0

def _load_font(size):
    """Load a TrueType font, falling back through common system locations."""
    font_candidates = [
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/calibri.ttf",
        "C:/Windows/Fonts/tahoma.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
    ]
    for fp in font_candidates:
        if os.path.isfile(fp):
            return ImageFont.truetype(fp, size)
    # Last resort: Pillow default (bitmap, not resizable — use size 10 max)
    return ImageFont.load_default()

def _load_font_bold(size):
    """Load a bold TrueType font."""
    font_candidates = [
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/calibrib.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for fp in font_candidates:
        if os.path.isfile(fp):
            return ImageFont.truetype(fp, size)
    return _load_font(size)

def _wrap_text(draw, text, font, max_width):
    """Word-wrap text using actual font metrics."""
    words = text.split()
    lines = []
    current = []
    for word in words:
        test = " ".join(current + [word])
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] > max_width and current:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines

# Color palettes for different slide layouts — gives visual variety
SLIDE_PALETTES = [
    {"bg_top": (10, 15, 30), "bg_bot": (20, 30, 55), "accent": (255, 139, 43), "card": (18, 24, 42)},
    {"bg_top": (8, 20, 28),  "bg_bot": (15, 35, 50), "accent": (0, 200, 180),  "card": (14, 28, 40)},
    {"bg_top": (18, 10, 28), "bg_bot": (30, 18, 50), "accent": (180, 120, 255),"card": (25, 16, 42)},
    {"bg_top": (10, 18, 12), "bg_bot": (18, 35, 22), "accent": (100, 220, 100),"card": (15, 28, 18)},
    {"bg_top": (25, 12, 12), "bg_bot": (45, 20, 22), "accent": (255, 90, 90),  "card": (35, 18, 18)},
    {"bg_top": (12, 15, 28), "bg_bot": (22, 28, 55), "accent": (80, 160, 255), "card": (18, 22, 45)},
]

def build_educational_slide(title, sentence_text, step_idx, total_steps, output_path):
    """Generate high-resolution 1280x720 educational slides with large readable fonts and varied layouts."""
    width, height = 1280, 720
    img = Image.new("RGB", (width, height), color=(14, 17, 26))
    draw = ImageDraw.Draw(img)

    # Pick a palette based on step index for visual variety
    pal = SLIDE_PALETTES[step_idx % len(SLIDE_PALETTES)]

    # Load real fonts at proper sizes
    font_title = _load_font_bold(32)
    font_body = _load_font(28)
    font_badge = _load_font_bold(16)
    font_step = _load_font(18)
    font_big_num = _load_font_bold(120)

    # Background gradient
    for y in range(height):
        t = y / height
        r = int(pal["bg_top"][0] + (pal["bg_bot"][0] - pal["bg_top"][0]) * t)
        g = int(pal["bg_top"][1] + (pal["bg_bot"][1] - pal["bg_top"][1]) * t)
        b = int(pal["bg_top"][2] + (pal["bg_bot"][2] - pal["bg_top"][2]) * t)
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    # Add abstract visual patterns (circles and lines) for "video-like" visuals
    import random
    random.seed(step_idx) # Keep patterns consistent for the same slide
    for _ in range(15):
        x = random.randint(0, width)
        y = random.randint(0, height)
        size = random.randint(50, 300)
        alpha = random.randint(10, 30)
        draw.ellipse([x, y, x + size, y + size], outline=(*pal["accent"], alpha), width=random.randint(1, 3))
    
    for _ in range(10):
        x1 = random.randint(0, width)
        y1 = random.randint(0, height)
        x2 = x1 + random.randint(-200, 200)
        y2 = y1 + random.randint(-200, 200)
        alpha = random.randint(5, 20)
        draw.line([(x1, y1), (x2, y2)], fill=(*pal["accent"], alpha), width=1)

    # Top accent bar
    draw.rectangle([0, 0, width, 5], fill=pal["accent"])
    # Bottom accent bar
    draw.rectangle([0, height - 5, width, height], fill=pal["accent"])

    # Left side: big step number watermark
    num_text = f"{step_idx:02d}"
    draw.text((40, height // 2 - 80), num_text, fill=(*pal["accent"], ), font=font_big_num)
    # Dim overlay on the number so it looks like a watermark
    overlay = Image.new("RGBA", (200, 200), (*pal["bg_top"], 160))
    img.paste(Image.blend(
        img.crop((30, height // 2 - 90, 230, height // 2 + 110)).convert("RGBA"),
        overlay,
        alpha=0.55
    ).convert("RGB"), (30, height // 2 - 90))
    draw = ImageDraw.Draw(img)  # Refresh draw after paste
    draw.text((50, height // 2 - 75), num_text, fill=(*pal["accent"],), font=font_big_num)

    # Main content card (right ~75%)
    card_x = 240
    card_y = 50
    card_w = width - card_x - 50
    card_h = height - 100
    draw.rounded_rectangle(
        [card_x, card_y, card_x + card_w, card_y + card_h],
        radius=20, fill=pal["card"], outline=(*pal["accent"], ), width=2
    )

    # Badge
    badge_text = "AUTOANIMATE"
    badge_bbox = draw.textbbox((0, 0), badge_text, font=font_badge)
    badge_w = badge_bbox[2] - badge_bbox[0] + 24
    badge_h = badge_bbox[3] - badge_bbox[1] + 14
    draw.rounded_rectangle(
        [card_x + 30, card_y + 28, card_x + 30 + badge_w, card_y + 28 + badge_h],
        radius=6, fill=pal["accent"]
    )
    draw.text((card_x + 42, card_y + 32), badge_text, fill=(15, 15, 20), font=font_badge)

    # Step counter on the right
    step_text = f"SECTION {step_idx:02d} / {total_steps:02d}"
    st_bbox = draw.textbbox((0, 0), step_text, font=font_step)
    st_w = st_bbox[2] - st_bbox[0]
    draw.text((card_x + card_w - st_w - 30, card_y + 34), step_text, fill=(160, 175, 205), font=font_step)

    # Title
    safe_title = (title[:60] + "...") if len(title) > 60 else title
    title_y = card_y + 80
    draw.text((card_x + 30, title_y), safe_title.upper(), fill=(255, 255, 255), font=font_title)

    # Divider
    div_y = title_y + 45
    draw.line([(card_x + 30, div_y), (card_x + card_w - 30, div_y)], fill=(*pal["accent"],), width=2)

    # Body text — word-wrapped using real font metrics
    body_max_w = card_w - 60
    body_lines = _wrap_text(draw, sentence_text, font_body, body_max_w)

    body_y = div_y + 25
    line_spacing = 42
    max_lines = min(len(body_lines), 9)
    for i in range(max_lines):
        # Slight indent and bullet for each line
        if i == 0:
            draw.text((card_x + 30, body_y + i * line_spacing), "▸  " + body_lines[i], fill=(230, 235, 248), font=font_body)
        else:
            draw.text((card_x + 45, body_y + i * line_spacing), body_lines[i], fill=(200, 210, 230), font=font_body)

    # Progress bar at bottom of card
    bar_y = card_y + card_h - 30
    bar_x_start = card_x + 30
    bar_x_end = card_x + card_w - 30
    bar_total = bar_x_end - bar_x_start
    draw.rounded_rectangle([bar_x_start, bar_y, bar_x_end, bar_y + 8], radius=4, fill=(35, 42, 60))
    progress_w = int(bar_total * (step_idx / max(total_steps, 1)))
    if progress_w > 0:
        draw.rounded_rectangle([bar_x_start, bar_y, bar_x_start + progress_w, bar_y + 8], radius=4, fill=pal["accent"])

    img.save(output_path, "PNG")

def _normalize_topic(raw_input):
    """Strip filler/question words so a readable subject and search query remain."""
    topic = re.sub(r"\s+", " ", raw_input.strip().rstrip("?.!")).strip()
    # Drop common instruction prefixes like "explain about", "tell me about", "what is"
    topic = re.sub(
        r"^(please\s+)?(can you\s+)?(explain|describe|tell me about|talk about|write about|give me|what is|what are|who is|who are|how does|how do|why is|why do)\s+",
        "", topic, flags=re.IGNORECASE,
    ).strip()
    topic = re.sub(r"^(about|on|the|a|an)\s+", "", topic, flags=re.IGNORECASE).strip()
    return topic or raw_input.strip()

def _clean_wiki_text(text):
    """Tidy a Wikipedia plaintext extract for narration."""
    text = re.sub(r"\([^)]{0,40}\)", "", text)  # drop short parentheticals e.g. pronunciations
    text = re.sub(r"\[[0-9]+\]", "", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return text.strip()

def _wikipedia_explanation(topic, max_words=150):
    """Build an on-topic educational narration from Wikipedia's intro (keyless, factual)."""
    headers = {"User-Agent": "AutoAnimate/1.0 (educational video generator)"}
    try:
        search = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "query", "list": "search", "srsearch": topic, "format": "json", "srlimit": 1},
            headers=headers, timeout=20,
        ).json()
        hits = search.get("query", {}).get("search", [])
        if not hits:
            return None
        title = hits[0]["title"]
        extract = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "query", "prop": "extracts", "exintro": 1, "explaintext": 1,
                    "titles": title, "format": "json", "redirects": 1},
            headers=headers, timeout=20,
        ).json()
        pages = extract.get("query", {}).get("pages", {})
        body = _clean_wiki_text(list(pages.values())[0].get("extract", ""))
        if not body:
            return None

        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", body) if len(s.split()) > 3]
        chosen, used = [], 0
        for s in sentences:
            if used + len(s.split()) > max_words and chosen:
                break
            chosen.append(s)
            used += len(s.split())
        if not chosen:
            return None
        return " ".join(chosen)
    except Exception as e:
        app.logger.warning(f"Wikipedia lookup failed for '{topic}': {e}")
        return None

def create_educational_script(raw_input):
    """Generate an informative educational script via Gemini, Wikipedia, or a short fallback."""
    global gemini_model
    if gemini_model is not None:
        try:
            prompt = (
                "You are an expert teacher writing the narration for a one-minute educational video.\n\n"
                "Write 8 to 10 short spoken sentences, about 130 to 150 words total, that genuinely EXPLAIN the subject. "
                "Structure them as:\n"
                "1) one opening sentence that defines or frames the subject,\n"
                "2) six to eight sentences that each teach ONE concrete, specific key point about the subject "
                "(a real fact, how it works, or why it matters),\n"
                "3) one short closing sentence.\n\n"
                "Rules:\n"
                "- Be specific to the subject; teach real information about it.\n"
                "- NEVER use vague filler, and never mention API keys, settings, or this prompt.\n"
                "- Do not restate the subject name in every sentence.\n"
                "- Keep each sentence under 25 words so it fits the narration time.\n"
                "- No markdown, no bullet points, no asterisks, no headings, no speaker tags.\n"
                "- Return ONLY the spoken sentences.\n\n"
                f"Subject: {raw_input}"
            )
            response = gemini_model.generate_content(prompt)
            script = re.sub(r"[\*#_>`]", "", response.text.strip()).strip()
            if script:
                return script
        except Exception as e:
            app.logger.warning(f"Gemini API generation failed: {e}")

    # Keyless fallback: build a real explanation from Wikipedia's intro.
    subject = _normalize_topic(raw_input)
    wiki = _wikipedia_explanation(subject)
    if wiki:
        return f"Here is a clear explanation of {subject}. {wiki}"

    # Last resort: a short, neutral scaffold (never mentions API keys in the narration).
    app.logger.warning(f"No script source available for '{raw_input}'; using a minimal scaffold.")
    return (
        f"Today we examine {subject}. "
        f"We start from what {subject} means and why it matters. "
        "Then we look at how it works and the parts that support it. "
        "Finally, we consider where it is used and what to watch out for."
    )

def assemble_video(work_dir, segments, audio_path, srt_path, output_filename, topic_title):
    """Generate educational frames per segment, render each as a timed clip, then combine with audio."""
    sd = get_stable_diffusion()
    total_segments = len(segments)
    fps = 30
    clip_files = []

    for i, seg in enumerate(segments, 1):
        frame_path = work_dir / f"frame_{i:03d}.png"
        clip_path = work_dir / f"clip_{i:03d}.mp4"
        raw_video_path = work_dir / f"raw_{i:03d}.mp4"
        duration = max(float(seg["end"]) - float(seg["start"]), 1.5)

        queries = _image_queries(topic_title, seg["text"])
        zoom_in = (i % 2 == 1)

        # 1) Preferred: real stock video clip (only if a Pexels key is configured)
        if PEXELS_API_KEY and fetch_stock_video(" ".join(queries[:1]) if queries else topic_title, raw_video_path, duration):
            cmd_process = [
                FFMPEG_BIN, "-y",
                "-i", str(raw_video_path.resolve()),
                "-t", f"{duration:.3f}",
                "-vf", "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,format=yuv420p",
                "-r", str(fps),
                "-c:v", "libx264", "-preset", "veryfast",
                "-an",
                str(clip_path.resolve())
            ]
            try:
                subprocess.run(cmd_process, cwd=str(work_dir), check=True, capture_output=True, timeout=120)
                clip_files.append(clip_path)
                continue
            except Exception as e:
                app.logger.warning(f"Stock video processing failed: {e}")

        # 2) Keyless approach: fetch a real topical image and add motion
        image_ok = fetch_topic_image(queries, frame_path)
        is_photo = image_ok

        # 3) Fallback: AI image, then generated slide
        if not image_ok:
            if sd is not None:
                try:
                    prompt = f"Educational illustration of {seg['text']}, clean documentary style, cinematic, 16:9"
                    image = sd(prompt, num_inference_steps=20, width=768, height=432).images[0]
                    image.resize((1280, 720)).save(str(frame_path))
                    image_ok = True
                    is_photo = True
                except Exception as e:
                    app.logger.warning(f"SD image generation failed, falling back to slide: {e}")
            if not image_ok:
                build_educational_slide(topic_title, seg["text"], i, total_segments, str(frame_path))

        # Key point for this segment (used as a static caption on real photos)
        key_point = get_key_point(seg["text"]) if is_photo else None

        # Animate the still image into a clip with Ken Burns motion
        try:
            render_image_clip(frame_path, clip_path, duration, fps, zoom_in=zoom_in)
            rendered_clip = clip_path
            # Burn the caption as a static, fading-in layer so the zoom never distorts the text
            if key_point:
                caption_png = work_dir / f"caption_{i:03d}.png"
                captioned_clip = work_dir / f"clip_cap_{i:03d}.mp4"
                make_caption_overlay(key_point, i, str(caption_png))
                apply_caption_overlay(clip_path, caption_png, captioned_clip, duration)
                rendered_clip = captioned_clip
            clip_files.append(rendered_clip)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            app.logger.warning(f"Clip {i} render failed: {e}")

    if not clip_files:
        raise RuntimeError("No video clips were produced. FFmpeg may be misconfigured.")

    # Write a concat file referencing all clip videos
    concat_file = work_dir / "concat.txt"
    with concat_file.open("w", encoding="utf-8") as f:
        for cf in clip_files:
            f.write(f"file '{cf.resolve().as_posix()}'\n")

    # Concatenate all clips into one silent video
    silent_video = work_dir / "silent_merged.mp4"
    cmd_merge = [
        FFMPEG_BIN, "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_file.resolve()),
        "-c", "copy",
        str(silent_video.resolve())
    ]
    subprocess.run(cmd_merge, cwd=str(work_dir), check=True, capture_output=True, timeout=300)

    output_path = (OUTPUTS / output_filename).resolve()

    # Final pass: merge silent video + audio, optionally burn in subtitles
    # Use relative path for subtitles to avoid Windows drive colon issues in FFmpeg filters
    srt_rel = "subtitles.srt"

    # Try with subtitles first
    cmd_final = [
        FFMPEG_BIN, "-y",
        "-i", str(silent_video.resolve()),
        "-i", str(audio_path.resolve()),
        "-vf", f"subtitles={srt_rel}:force_style='FontSize=22,PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,Outline=2,Shadow=1,MarginV=30'",
        "-c:v", "libx264", "-preset", "veryfast",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        str(output_path)
    ]

    try:
        subprocess.run(cmd_final, cwd=str(work_dir), check=True, capture_output=True, timeout=300)
    except subprocess.CalledProcessError as e:
        app.logger.warning(f"Subtitle burn-in failed, rendering without subs: {e.stderr.decode(errors='replace')[:500]}")
        # Fallback: no subtitles, but still re-encode to ensure compatibility and correct duration
        cmd_nosub = [
            FFMPEG_BIN, "-y",
            "-i", str(silent_video.resolve()),
            "-i", str(audio_path.resolve()),
            "-c:v", "libx264", "-preset", "veryfast",
            "-c:a", "aac", "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            "-shortest",
            str(output_path)
        ]
        subprocess.run(cmd_nosub, cwd=str(work_dir), check=True, capture_output=True, timeout=300)

    return output_path

# ================= ROUTES =================

@app.get("/api/health")
def health():
    return jsonify({
        "ok": True,
        "device": device,
        "gemini_configured": bool(gemini_key),
        "gemini_model": gemini_model_name,
        "mongo_connected": bool(videos_collection is not None),
        "ffmpeg_available": os.path.exists(FFMPEG_BIN) or bool(shutil.which("ffmpeg")),
        "ffmpeg_path": FFMPEG_BIN,
        "sd_enabled": os.getenv("USE_STABLE_DIFFUSION", "false").lower() == "true"
    })

@app.post("/api/upload")
def upload():
    work = None
    try:
        session_id = str(uuid.uuid4())
        work = UPLOADS / session_id
        work.mkdir(parents=True, exist_ok=True)

        raw_text = request.form.get("textInput", "").strip()

        # Handle file upload
        if "file" in request.files and request.files["file"].filename:
            file_obj = request.files["file"]
            filename = secure_filename(file_obj.filename)
            ext = Path(filename).suffix.lower()

            if ext not in {".txt", ".mp3", ".wav", ".m4a"}:
                return jsonify(error="Allowed file types: .txt, .mp3, .wav, .m4a"), 400

            file_dest = work / f"input{ext}"
            file_obj.save(file_dest)

            if ext == ".txt":
                raw_text = file_dest.read_text(encoding="utf-8", errors="replace").strip()
            else:
                # Transcribe user's audio input using Whisper
                whisper = get_whisper()
                if whisper is not None:
                    try:
                        transcription = whisper.transcribe(str(file_dest))
                        raw_text = transcription.get("text", "").strip()
                    except Exception as e:
                        return jsonify(error=f"Audio transcription failed: {str(e)}"), 500
                else:
                    return jsonify(error="Audio uploaded, but Whisper speech-to-text model is not available."), 500

        if not raw_text:
            return jsonify(error="Please provide a topic or upload a valid text/audio file."), 400

        # Step 1: AI Script Generation
        script = create_educational_script(raw_text)

        # Step 2: Text-to-speech audio narration
        from gtts import gTTS
        audio_file = work / "voiceover.mp3"
        tts = gTTS(text=script, lang="en")
        tts.save(str(audio_file))

        # Step 3: Subtitle timing and alignment
        whisper = get_whisper()
        segments = []
        if whisper is not None:
            try:
                whisper_res = whisper.transcribe(str(audio_file))
                segments = whisper_res.get("segments", [])
            except Exception as e:
                app.logger.warning(f"Whisper alignment skipped: {e}")

        # Proportional fallback for subtitle segments if Whisper is absent or produced empty
        if not segments:
            sentences = split_into_sentences(script)
            total_duration = get_audio_duration(audio_file)
            total_chars = sum(len(s) for s in sentences) or 1
            cur_time = 0.0
            for s in sentences:
                seg_duration = (len(s) / total_chars) * total_duration
                segments.append({
                    "start": cur_time,
                    "end": cur_time + seg_duration,
                    "text": s
                })
                cur_time += seg_duration

        # Step 4: Write Subtitle File (.srt)
        srt_file = work / "subtitles.srt"
        with srt_file.open("w", encoding="utf-8") as f:
            for idx, seg in enumerate(segments, 1):
                f.write(f"{idx}\n{format_timestamp(seg['start'])} --> {format_timestamp(seg['end'])}\n{seg['text'].strip()}\n\n")

        # Step 5: Render Video
        video_filename = f"video_{session_id}.mp4"
        assemble_video(work, segments, audio_file, srt_file, video_filename, raw_text[:50])

        video_url = f"{request.host_url.rstrip('/')}/generated_videos/{video_filename}"

        # Step 6: Persist in MongoDB if available
        if videos_collection is not None:
            try:
                videos_collection.insert_one({
                    "session_id": session_id,
                    "prompt": raw_text,
                    "transcript": script,
                    "video_url": video_url,
                    "timestamp": datetime.now().isoformat()
                })
            except Exception as e:
                app.logger.warning(f"MongoDB insert skipped: {e}")

        return jsonify({
            "message": "Video generated successfully",
            "generated_video": video_url,
            "transcript": script
        })

    except Exception as e:
        app.logger.exception("Video generation encountered an error")
        return jsonify(error=str(e)), 500
    finally:
        if work and work.exists():
            shutil.rmtree(work, ignore_errors=True)

@app.get("/generated_videos/<path:filename>")
def serve_video(filename):
    file_path = OUTPUTS / filename
    if not file_path.exists():
        return jsonify(error="Video not found"), 404
    return send_from_directory(OUTPUTS, filename, mimetype="video/mp4", as_attachment=False)

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
