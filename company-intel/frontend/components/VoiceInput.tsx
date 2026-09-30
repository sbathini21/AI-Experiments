"use client";
import { useRef, useState } from "react";

/* Speech-to-text: the browser's Web Speech API first (free, on-device/vendor), falling back to recording
   audio and sending it to /api/voice/transcribe (server STT) when the browser lacks recognition. */
type Props = { onText: (text: string) => void; disabled?: boolean };

export default function VoiceInput({ onText, disabled }: Props) {
  const [state, setState] = useState<"idle" | "listening" | "uploading">("idle");
  const [err, setErr] = useState<string | null>(null);
  const recRef = useRef<any>(null);
  const mediaRef = useRef<MediaRecorder | null>(null);

  async function start() {
    setErr(null);
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (SR) {
      const rec = new SR();
      rec.lang = navigator.language || "en-US";
      rec.interimResults = false;
      rec.maxAlternatives = 1;
      rec.onresult = (e: any) => onText(e.results[0][0].transcript);
      rec.onerror = (e: any) => setErr(e.error === "not-allowed" ? "Microphone permission denied" : "Didn't catch that — try again");
      rec.onend = () => setState("idle");
      recRef.current = rec;
      rec.start();
      setState("listening");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mr = new MediaRecorder(stream);
      const chunks: Blob[] = [];
      mr.ondataavailable = (e) => chunks.push(e.data);
      mr.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        setState("uploading");
        const fd = new FormData();
        fd.append("audio", new Blob(chunks, { type: mr.mimeType }), "query.webm");
        const r = await fetch("/api/voice/transcribe", { method: "POST", body: fd });
        setState("idle");
        if (r.ok) onText((await r.json()).text);
        else setErr("Voice input isn't available in this browser");
      };
      mediaRef.current = mr;
      mr.start();
      setState("listening");
      setTimeout(() => mr.state === "recording" && mr.stop(), 8000);
    } catch {
      setErr("Microphone unavailable");
    }
  }

  function stop() {
    recRef.current?.stop();
    if (mediaRef.current?.state === "recording") mediaRef.current.stop();
  }

  return (
    <>
      <button
        type="button"
        className={`btn btn-icon ${state === "listening" ? "btn-rec" : ""}`}
        onClick={state === "listening" ? stop : start}
        disabled={disabled || state === "uploading"}
        aria-label={state === "listening" ? "Stop listening" : "Speak your question"}
        title={state === "listening" ? "Stop" : "Speak"}
      >
        {state === "uploading" ? "…" : "🎤"}
      </button>
      {err && <span className="small muted" role="status" style={{ position: "absolute", marginTop: 56 }}>{err}</span>}
    </>
  );
}
