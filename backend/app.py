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

# Gemini AI Client
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

def build_educational_slide(title, sentence_text, step_idx, total_steps, output_path):
    """Generate high-resolution 1280x720 educational slides with typography and cards."""
    width, height = 1280, 720
    img = Image.new("RGB", (width, height), color=(14, 17, 26))
    draw = ImageDraw.Draw(img)

    # Vibrant vertical background gradient
    for y in range(height):
        r = int(14 + (24 - 14) * (y / height))
        g = int(17 + (32 - 17) * (y / height))
        b = int(26 + (50 - 26) * (y / height))
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    # Decorative top glow accent
    draw.rectangle([0, 0, width, 6], fill=(255, 139, 43))

    # Outer decorative card
    card_margin_x, card_margin_y = 70, 70
    card_box = [card_margin_x, card_margin_y, width - card_margin_x, height - card_margin_y]
    draw.rounded_rectangle(card_box, radius=24, fill=(21, 26, 40), outline=(45, 55, 80), width=2)

    # Header badge: "AUTOANIMATE • EDUCATIONAL LECTURE"
    badge_text = "AUTOANIMATE • AI EDUCATIONAL VISUAL"
    draw.rounded_rectangle([card_margin_x + 36, card_margin_y + 36, card_margin_x + 340, card_margin_y + 70], radius=8, fill=(255, 139, 43))
    draw.text((card_margin_x + 50, card_margin_y + 44), badge_text, fill=(18, 21, 32))

    # Slide sequence counter: "CONCEPT 01 OF 06"
    step_text = f"SECTION {step_idx:02d} / {total_steps:02d}"
    draw.text((width - card_margin_x - 170, card_margin_y + 46), step_text, fill=(160, 175, 205))

    # Topic title
    safe_title = (title[:55] + "...") if len(title) > 55 else title
    draw.text((card_margin_x + 40, card_margin_y + 105), safe_title.upper(), fill=(255, 255, 255))

    # Divider line
    draw.line([(card_margin_x + 40, card_margin_y + 140), (width - card_margin_x - 40, card_margin_y + 140)], fill=(45, 55, 80), width=2)

    # Main text formatting with word wrapping
    words = sentence_text.split()
    lines = []
    current_line = []
    for word in words:
        current_line.append(word)
        if len(" ".join(current_line)) > 42:
            lines.append(" ".join(current_line[:-1]))
            current_line = [word]
    if current_line:
        lines.append(" ".join(current_line))

    # Draw body lines
    start_y = card_margin_y + 190
    line_height = 48
    for i, line in enumerate(lines[:6]):
        draw.text((card_margin_x + 50, start_y + (i * line_height)), line, fill=(230, 235, 248))

    # Bottom progress bar indicator
    progress_w = int((width - (card_margin_x * 2) - 80) * (step_idx / max(total_steps, 1)))
    bar_y = height - card_margin_y - 45
    draw.rounded_rectangle([card_margin_x + 40, bar_y, width - card_margin_x - 40, bar_y + 8], radius=4, fill=(35, 42, 60))
    if progress_w > 0:
        draw.rounded_rectangle([card_margin_x + 40, bar_y, card_margin_x + 40 + progress_w, bar_y + 8], radius=4, fill=(255, 139, 43))

    img.save(output_path, "PNG")

def create_educational_script(raw_input):
    """Generate structured educational lecture script via Gemini with fallback."""
    global gemini_model
    if gemini_model is not None:
        try:
            prompt = (
                "You are an inspiring educator and professor. Write a structured, engaging, and clear "
                "spoken educational mini-lecture of 5 to 7 sentences about the following subject. "
                "The text will be spoken directly by an audio voiceover. "
                "Return ONLY the spoken sentences. Do not include markdown headers, bold formatting, bullet points, asterisks, or speaker tags.\n\n"
                f"Subject: {raw_input}"
            )
            response = gemini_model.generate_content(prompt)
            script = response.text.strip()
            # Clean up any leftover markdown
            script = re.sub(r"[\*#_>`]", "", script).strip()
            if script:
                return script
        except Exception as e:
            app.logger.warning(f"Gemini API generation failed: {e}")

    # Fallback structured educational script
    topic_cleaned = raw_input.strip().rstrip(".").title()
    return (
        f"Welcome to this educational lesson exploring {topic_cleaned}. "
        "Understanding this topic requires looking closely at its primary principles and practical behavior. "
        "Each core component interacts systematically to produce measurable and predictable outcomes. "
        "In modern applications, this concept helps researchers and engineers solve complex challenges. "
        "Observing these dynamics gives us a deeper appreciation for the underlying fundamentals. "
        "Thank you for exploring this educational overview of the subject."
    )

def assemble_video(work_dir, segments, audio_path, srt_path, output_filename, topic_title):
    """Generate educational frames and invoke FFmpeg to render final MP4."""
    sd = get_stable_diffusion()
    concat_file = work_dir / "concat.txt"
    total_segments = len(segments)

    with concat_file.open("w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            frame_path = (work_dir / f"frame_{i:03d}.png").resolve()
            duration = max(float(seg["end"]) - float(seg["start"]), 1.2)

            # Use Stable Diffusion if enabled, otherwise render HD educational slides
            if sd is not None:
                try:
                    prompt = f"Educational illustration of {seg['text']}, clean documentary style, cinematic, 16:9"
                    image = sd(prompt, num_inference_steps=20, width=768, height=432).images[0]
                    image.resize((1280, 720)).save(frame_path)
                except Exception as e:
                    app.logger.warning(f"SD image generation failed, falling back to slide: {e}")
                    build_educational_slide(topic_title, seg["text"], i, total_segments, frame_path)
            else:
                build_educational_slide(topic_title, seg["text"], i, total_segments, frame_path)

            f.write(f"file '{frame_path.as_posix()}'\n")
            f.write(f"duration {duration:.3f}\n")

        # Repeat last frame to prevent FFmpeg dropping the end frame
        last_frame = (work_dir / f"frame_{total_segments:03d}.png").resolve()
        f.write(f"file '{last_frame.as_posix()}'\n")

    output_path = (OUTPUTS / output_filename).resolve()

    # Attempt rendering with hardcoded subtitles
    srt_relative = "subtitles.srt"
    cmd_with_subs = [
        FFMPEG_BIN, "-y",
        "-f", "concat", "-safe", "0", "-i", "concat.txt",
        "-i", str(audio_path.resolve()),
        "-vf", f"scale=1280:720,subtitles={srt_relative}",
        "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p", "-shortest", str(output_path)
    ]

    try:
        subprocess.run(cmd_with_subs, cwd=str(work_dir), check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        app.logger.warning(f"FFmpeg with subtitles filter failed, falling back to clean video: {e.stderr.decode(errors='replace')}")
        cmd_clean = [
            FFMPEG_BIN, "-y",
            "-f", "concat", "-safe", "0", "-i", "concat.txt",
            "-i", str(audio_path.resolve()),
            "-vf", "scale=1280:720",
            "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-b:a", "192k",
            "-pix_fmt", "yuv420p", "-shortest", str(output_path)
        ]
        subprocess.run(cmd_clean, cwd=str(work_dir), check=True)

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
