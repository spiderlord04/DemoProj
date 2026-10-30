import React, { useState, useEffect, useRef } from "react";
import "../App.css";

const API = (import.meta.env.VITE_API_BASE_URL || "http://localhost:5000").replace(/\/$/, "");

const SUGGESTED_TOPICS = [
  "Photosynthesis & Plant Energy",
  "How Black Holes Warp Spacetime",
  "The Architecture of Deep Neural Networks",
  "The Hydrological Water Cycle",
  "Quantum Computing Principles"
];

const GENERATION_STEPS = [
  "Analyzing educational prompt and inputs",
  "Crafting structured lecture script with Gemini",
  "Synthesizing voiceover audio narration",
  "Fetching topical images and applying motion",
  "Aligning subtitle timestamps and speech segments",
  "Composing subtitles and rendering final MP4 video"
];

export default function Home() {
  const [topic, setTopic] = useState("");
  const [file, setFile] = useState(null);
  const [videoUrl, setVideoUrl] = useState("");
  const [transcript, setTranscript] = useState("");
  const [loading, setLoading] = useState(false);
  const [activeStep, setActiveStep] = useState(0);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const [backendStatus, setBackendStatus] = useState({ checked: false, online: false, device: "unknown" });
  const [isDragOver, setIsDragOver] = useState(false);

  const fileInputRef = useRef(null);

  // Check backend health on load
  useEffect(() => {
    async function checkHealth() {
      try {
        const res = await fetch(`${API}/api/health`, { method: "GET" });
        if (res.ok) {
          const data = await res.json();
          setBackendStatus({ checked: true, online: true, device: data.device || "cpu" });
        } else {
          setBackendStatus({ checked: true, online: false, device: "offline" });
        }
      } catch {
        setBackendStatus({ checked: true, online: false, device: "offline" });
      }
    }
    checkHealth();
    const interval = setInterval(checkHealth, 20000);
    return () => clearInterval(interval);
  }, []);

  // Animate progress steps during video generation
  useEffect(() => {
    let timer;
    if (loading) {
      setActiveStep(0);
      timer = setInterval(() => {
        setActiveStep((prev) => (prev < GENERATION_STEPS.length - 1 ? prev + 1 : prev));
      }, 4500);
    }
    return () => clearInterval(timer);
  }, [loading]);

  function handleFileChange(selectedFile) {
    if (!selectedFile) return;
    const ext = selectedFile.name.substring(selectedFile.name.lastIndexOf(".")).toLowerCase();
    const valid = [".txt", ".mp3", ".wav", ".m4a"];
    if (!valid.includes(ext)) {
      setError(`Unsupported file format. Please upload: ${valid.join(", ")}`);
      return;
    }
    setError("");
    setFile(selectedFile);
  }

  function handleDrop(e) {
    e.preventDefault();
    setIsDragOver(false);
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      handleFileChange(e.dataTransfer.files[0]);
    }
  }

  function formatBytes(bytes) {
    if (!bytes || bytes === 0) return "0 Bytes";
    const k = 1024;
    const sizes = ["Bytes", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
  }

  async function handleGenerate(e) {
    e.preventDefault();
    setError("");
    setVideoUrl("");
    setTranscript("");

    if (!file && !topic.trim()) {
      setError("Please enter a topic or upload a lecture file (.txt, .mp3, .wav, .m4a).");
      return;
    }

    const formData = new FormData();
    if (topic.trim()) formData.append("textInput", topic.trim());
    if (file) formData.append("file", file);

    setLoading(true);

    try {
      const response = await fetch(`${API}/api/upload`, {
        method: "POST",
        body: formData,
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.error || "Video generation failed on backend.");
      }

      setVideoUrl(data.generated_video);
      setTranscript(data.transcript || "");
    } catch (err) {
      setError(err.message || "Failed to connect to backend server.");
    } finally {
      setLoading(false);
    }
  }

  function copyTranscript() {
    if (!transcript) return;
    navigator.clipboard.writeText(transcript);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div className="app-container">
      {/* Top Bar with Live Health Status */}
      <header className="top-bar">
        <div className="logo-group">
          <div className="logo-icon">▶</div>
          <div className="logo-text">AutoAnimate</div>
        </div>
        <div className={`status-badge ${backendStatus.online ? "status-online" : "status-offline"}`}>
          <span className="status-dot"></span>
          <span>
            {backendStatus.checked
              ? backendStatus.online
                ? `Backend: Online (${backendStatus.device.toUpperCase()})`
                : "Backend: Offline"
              : "Checking Backend..."}
          </span>
        </div>
      </header>

      {/* Hero Header */}
      <section className="hero">
        <span className="eyebrow">Generative AI • Educational Studio</span>
        <h1>AI Video Generation</h1>
        <p className="lead">
          Turn any lecture topic, text notes, or spoken audio into a fully narrated educational video with cinematic stock footage, AI visuals, and synchronized subtitles.
        </p>
      </section>

      {/* Generator Form Panel */}
      <section className="panel">
        <div className="panel-header">
          <h2>Create Educational Video</h2>
          <p>Specify a lecture topic or upload source material to synthesize video lessons.</p>
        </div>

        <form onSubmit={handleGenerate}>
          {/* Text Topic Input */}
          <div className="field-group">
            <label htmlFor="topic-input">
              <span>Lecture Topic or Notes</span>
              <span className="label-hint">Text prompt</span>
            </label>
            <textarea
              id="topic-input"
              rows={4}
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder="e.g. Explain how mitochondria generate cellular energy via ATP synthesis..."
              disabled={loading}
            />

            {/* Quick Topic Chips */}
            <div className="quick-topics">
              <span className="chips-label">Try an idea:</span>
              {SUGGESTED_TOPICS.map((item) => (
                <button
                  key={item}
                  type="button"
                  className="topic-chip"
                  onClick={() => setTopic(item)}
                  disabled={loading}
                >
                  {item}
                </button>
              ))}
            </div>
          </div>

          {/* File Upload Dropzone */}
          <div className="field-group">
            <label>
              <span>Or Upload Source File</span>
              <span className="label-hint">Supported: .txt, .mp3, .wav, .m4a</span>
            </label>

            <div
              className={`file-dropzone ${isDragOver ? "dragover" : ""} ${file ? "has-file" : ""}`}
              onDragOver={(e) => { e.preventDefault(); setIsDragOver(true); }}
              onDragLeave={() => setIsDragOver(false)}
              onDrop={handleDrop}
              onClick={() => fileInputRef.current?.click()}
            >
              <input
                ref={fileInputRef}
                type="file"
                accept=".txt,.mp3,.wav,.m4a"
                onChange={(e) => handleFileChange(e.target.files?.[0])}
                disabled={loading}
              />
              <div className="dropzone-content">
                <div className="upload-icon">
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                    <polyline points="17 8 12 3 7 8" />
                    <line x1="12" y1="3" x2="12" y2="15" />
                  </svg>
                </div>
                <strong>Drag and drop your file here, or click to browse</strong>
                <span className="label-hint">Audio will be transcribed using Whisper; text will be structured by Gemini</span>
              </div>
            </div>

            {/* Selected File Details */}
            {file && (
              <div className="file-info-active">
                <div>
                  <div className="file-name">📄 {file.name}</div>
                  <div className="file-size">{formatBytes(file.size)}</div>
                </div>
                <button
                  type="button"
                  className="btn-remove-file"
                  onClick={() => setFile(null)}
                  disabled={loading}
                >
                  Remove file
                </button>
              </div>
            )}
          </div>

          {/* Action Button */}
          <button type="submit" className="btn-generate" disabled={loading}>
            {loading ? (
              <>
                <span className="spinner"></span>
                <span>Generating Video...</span>
              </>
            ) : (
              <>
                <span>✨ Generate Educational Video</span>
              </>
            )}
          </button>
        </form>

        {/* Live Generation Progress Card */}
        {loading && (
          <div className="progress-container">
            <div className="progress-header">
              <span>GENERATING EDUCATIONAL VIDEO</span>
              <span>Step {activeStep + 1} of {GENERATION_STEPS.length}</span>
            </div>
            <div className="progress-track">
              <div
                className="progress-bar-fill"
                style={{ width: `${((activeStep + 1) / GENERATION_STEPS.length) * 100}%` }}
              ></div>
            </div>
            <div className="stepper-list">
              {GENERATION_STEPS.map((step, idx) => (
                <div
                  key={step}
                  className={`step-item ${idx <= activeStep ? "active" : ""}`}
                >
                  <span className="step-dot"></span>
                  <span>{step}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Error Alert */}
        {error && (
          <div className="error-box">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="10" />
              <line x1="12" y1="8" x2="12" y2="12" />
              <line x1="12" y1="16" x2="12.01" y2="16" />
            </svg>
            <div>
              <div className="error-title">Generation Error</div>
              <div>{error}</div>
            </div>
          </div>
        )}
      </section>

      {/* Generated Video Player */}
      {videoUrl && (
        <section className="panel">
          <div className="panel-header">
            <h2>Generated Video Lesson</h2>
            <p>Your educational video has been compiled with AI voiceover narration and subtitles.</p>
          </div>
          <div className="video-preview-wrapper">
            <video controls autoPlay src={videoUrl}>
              Your browser does not support the video tag.
            </video>
          </div>
          <div className="video-actions">
            <a
              href={videoUrl}
              download="autoanimate_lesson.mp4"
              className="btn-action btn-primary-action"
            >
              📥 Download Video MP4
            </a>
            <a
              href={videoUrl}
              target="_blank"
              rel="noreferrer"
              className="btn-action"
            >
              ↗ Open in New Tab
            </a>
          </div>
        </section>
      )}

      {/* Narration Script & Subtitles */}
      {transcript && (
        <section className="panel">
          <div className="transcript-header">
            <div>
              <h2>Narration Script</h2>
              <p className="label-hint">AI-generated lecture narration synchronized with subtitles</p>
            </div>
            <button className="btn-copy" onClick={copyTranscript}>
              {copied ? "✓ Copied" : "📋 Copy Script"}
            </button>
          </div>
          <div className="transcript-card">
            <div className="transcript-content">{transcript}</div>
          </div>
        </section>
      )}

      {/* Footer */}
      <footer>
        <p>AutoAnimate Studio • Powered by Google Gemini, Whisper, gTTS & FFmpeg</p>
      </footer>
    </div>
  );
}
