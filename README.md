# AutoAnimate — AI Educational Video Generator

Turn any topic, text notes, or a spoken audio file into a finished narrated educational video: a real explanation, a voiceover, subtitles, and topical images animated with cinematic motion.

## Features

- **Real explanations without an API key** — narration is built from Wikipedia's article intro, so the video actually teaches the topic. Add a Google Gemini key to get richer, custom-written narration.
- **Keyless topical images** — photos are fetched from Wikimedia Commons (license-friendly) with a DuckDuckGo fallback, and every image is matched against the topic so it stays relevant.
- **Motion on every visual** — each image gets a Ken Burns zoom/pan so it reads as video, not a slideshow.
- **Static key-point captions** — a caption banner fades in over the moving image and stays readable instead of drifting with the zoom.
- **Voiceover + burned-in subtitles** — narrated with gTTS, timed and burned into the video.
- **Optional extras** — Stable Diffusion image generation and Pexels stock video clips if you configure them.

## Tech Stack

| Layer | Technology |
| --- | --- |
| Backend | Python, Flask, Flask-CORS |
| Video | FFmpeg (bundled through `imageio-ffmpeg`), Pillow |
| Speech | gTTS (voiceover), OpenAI Whisper (timing) |
| Content | Wikipedia API (keyless), Google Gemini (optional) |
| Images | Wikimedia Commons, DuckDuckGo (optional: Stable Diffusion, Pexels) |
| Frontend | React 18 + Vite |
| Storage | MongoDB (optional) |

## Prerequisites

- **Python 3.10 or 3.11** (recommended — some AI wheels may not be available on 3.13)
- **Node.js 18+** and npm
- **Git**
- FFmpeg is installed automatically via `imageio-ffmpeg`, so no separate install is normally needed.

## 1. Clone

```bash
git clone https://github.com/spiderlord04/DemoProj.git
cd DemoProj
```

## 2. Backend — install dependencies and run

Open a terminal in the `backend` folder.

**Windows (PowerShell)**

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python app.py
```

**macOS / Linux**

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python app.py
```

The backend starts on **http://127.0.0.1:5000**.

The first run downloads the Whisper model, which can take a while.

## 3. Frontend — install dependencies and run

Open a **second** terminal in the `frontend` folder.

```bash
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173** in your browser.

To point the frontend at a different backend, create `frontend/.env`:

```env
VITE_API_BASE_URL=http://127.0.0.1:5000
```

## Environment Variables (`backend/.env`)

| Variable | Required | Description |
| --- | --- | --- |
| `GEMINI_API_KEY` | No | Google Gemini key for richer AI-written narration. Without it, Wikipedia is used. |
| `PEXELS_API_KEY` | No | Enables real stock video clips instead of animated images. |
| `MONGO_URI` | No | MongoDB connection string for storing video records. |
| `GEMINI_MODEL` | No | Gemini model name (default `gemini-2.5-flash`). |
| `USE_STABLE_DIFFUSION` | No | `true` to generate images with Stable Diffusion (needs a GPU). |
| `SD_MODEL` | No | Stable Diffusion model id. |
| `WHISPER_MODEL` | No | Whisper size for subtitle timing (default `base`). |
| `PORT` | No | Backend port (default `5000`). |
| `FRONTEND_ORIGIN` | No | Allowed frontend origin (default `http://localhost:5173`). |

**You can run the whole app with no API keys.** Everything below is optional.

## How It Works

1. The topic or uploaded text/audio is turned into a script (Gemini, or Wikipedia when no key is set).
2. gTTS produces the voiceover; Whisper aligns subtitle timings.
3. For each sentence, topic-anchored queries fetch a relevant image, which is animated with Ken Burns motion and given a fading key-point caption.
4. Clips are concatenated, subtitles are burned in, and the final MP4 is written to `backend/generated_videos/`.

## Project Structure

```
AutoAnimate_QuickSetup/
├── backend/
│   ├── app.py              # Flask API + video generation pipeline
│   ├── requirements.txt
│   ├── .env.example
│   ├── uploads/            # uploaded inputs (created at runtime)
│   └── generated_videos/   # rendered videos (created at runtime)
└── frontend/
    ├── src/pages/Home.jsx  # main UI
    ├── package.json
    └── .env.example
```

## Troubleshooting

- **Port already in use** — change `PORT` in `backend/.env`, then update `VITE_API_BASE_URL` in `frontend/.env`.
- **`pip install` fails on Python 3.13** — use Python 3.10 or 3.11 in the virtual environment.
- **Video generation is slow** — subtitle timing (Whisper) and video encoding are CPU heavy; a GPU speeds up Stable Diffusion if enabled.
- **No images in the video** — check your internet connection; image search needs outbound access to Wikimedia Commons and DuckDuckGo.

## Notes

Never commit `.env`, API keys, MongoDB credentials, generated videos, or uploaded files.
