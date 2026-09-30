"use client";
import { useEffect, useRef, useState } from "react";

/* Text-to-speech: browser speechSynthesis by default (free). If the server has a TTS provider configured,
   "Download MP3" fetches a cached studio-voice file from /api/voice/synthesize. */
export default function ListenPlayer({ script, reportId }: { script: string; reportId: string }) {
  const [state, setState] = useState<"idle" | "playing" | "paused">("idle");
  const [rate, setRate] = useState(1);
  const [serverTts, setServerTts] = useState<boolean | null>(null);
  const uttRef = useRef<SpeechSynthesisUtterance | null>(null);
  const seconds = Math.round(script.split(/\s+/).length / (2.6 * rate));

  useEffect(() => () => window.speechSynthesis?.cancel(), []);

  function play(fromStart = false) {
    const synth = window.speechSynthesis;
    if (!synth) return;
    if (state === "paused" && !fromStart) {
      synth.resume();
      setState("playing");
      return;
    }
    synth.cancel();
    const u = new SpeechSynthesisUtterance(script);
    u.rate = rate;
    const voices = synth.getVoices();
    u.voice = voices.find((v) => /en[-_](IN|GB|US)/i.test(v.lang) && /natural|premium|enhanced|google/i.test(v.name)) || voices.find((v) => v.lang.startsWith("en")) || null;
    u.onend = () => setState("idle");
    uttRef.current = u;
    synth.speak(u);
    setState("playing");
  }

  function pause() {
    window.speechSynthesis?.pause();
    setState("paused");
  }

  async function download() {
    const r = await fetch("/api/voice/synthesize", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ report_id: reportId }) });
    if (!r.ok) {
      setServerTts(false);
      return;
    }
    const url = URL.createObjectURL(await r.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = "briefing.mp3";
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="player">
      {state === "playing" ? (
        <button className="btn btn-primary" onClick={pause}>❚❚ Pause</button>
      ) : (
        <button className="btn btn-primary" onClick={() => play()}>▶ {state === "paused" ? "Resume" : "Listen"}</button>
      )}
      <button className="btn" onClick={() => play(true)} disabled={state === "idle"}>↺ Replay</button>
      <label className="small muted row" style={{ gap: 6 }}>
        Speed
        <select value={rate} onChange={(e) => setRate(Number(e.target.value))} className="btn" style={{ padding: "6px 10px" }}>
          {[0.85, 1, 1.15, 1.3, 1.5].map((r) => <option key={r} value={r}>{r}×</option>)}
        </select>
      </label>
      <span className="small muted">≈ {seconds} seconds</span>
      {serverTts !== false && <button className="btn small" onClick={download}>⬇ MP3</button>}
      {serverTts === false && <span className="small muted">MP3 download needs a server TTS key</span>}
    </div>
  );
}
