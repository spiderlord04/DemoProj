# AutoAnimate Quick Setup

This starter contains the Flask AI backend and React page files.

## Frontend
The `frontend` folder contains the page files. To create the Vite setup in that folder, open a terminal in `frontend` and run:

```bash
npm create vite@latest . -- --template react
```

When asked how to proceed because the folder is not empty, select **Ignore files and continue**. Do not choose Remove existing files.

Then run:
```bash
npm install
```

Replace `src/App.jsx` with:
```jsx
import Home from "./pages/Home";
export default function App() {
  return <Home />;
}
```

Create `frontend/.env`:
```env
VITE_API_BASE_URL=http://127.0.0.1:5000
```

Start the frontend:
```bash
npm run dev
```

## Backend
Use Python 3.10 or 3.11. Install FFmpeg separately, then in `backend`:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and add your Gemini API key. MongoDB is optional.

Start:
```bash
python app.py
```

The AI image generation and transcription models can take a long time to download and run. A GPU is recommended.

## Git push
From the project root:
```bash
git status
git add .
git commit -m "Set up AutoAnimate AI video generation"
git push
```

Never commit `.env`, API keys, MongoDB credentials, ngrok tokens, generated videos, or uploaded files.
