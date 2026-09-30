"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import VoiceInput from "@/components/VoiceInput";
import { api, Candidate, FLAGS, MODES, Mode, Resolution } from "@/lib/api";

type SearchOut = { resolution: Resolution; status?: string; job_id?: string; report?: { report_id: string } };

const EXAMPLES = ["Kinetic Engineering", "What changed at NVIDIA this week?", "Why is Apple in the news today?", "Tata Motors", "Barclays"];

export default function Home() {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [mode, setMode] = useState<Mode>("quick");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ambiguous, setAmbiguous] = useState<Resolution | null>(null);
  const [inputMode, setInputMode] = useState<"text" | "voice">("text");

  async function submit(text: string, via: "text" | "voice" = inputMode) {
    if (!text.trim()) return;
    setBusy(true);
    setErr(null);
    setAmbiguous(null);
    try {
      const out = await api<SearchOut>("/api/search", { method: "POST", json: { query: text, mode, input_mode: via } });
      if (out.resolution.status === "resolved") {
        const cid = out.resolution.selected!.company_id;
        router.push(`/company/${cid}?mode=${mode}&window=${out.resolution.query.window_days}${out.job_id ? `&job=${out.job_id}` : ""}`);
      } else if (out.resolution.status === "ambiguous") {
        setAmbiguous(out.resolution);
      } else {
        setErr(`I couldn't find a listed company matching “${out.resolution.query.company}” in India, the US or the UK.`);
      }
    } catch (e: any) {
      setErr(e.message || "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  async function pick(c: Candidate) {
    setBusy(true);
    try {
      const window = ambiguous?.query.window_days ?? 7;
      const out = await api<{ status: string; job_id?: string }>("/api/company/analyze", {
        method: "POST",
        json: { company_id: c.company_id, mode, window_days: window, raw_query: q, input_mode: inputMode },
      });
      router.push(`/company/${c.company_id}?mode=${mode}&window=${window}${out.job_id ? `&job=${out.job_id}` : ""}`);
    } catch (e: any) {
      setErr(e.message);
      setBusy(false);
    }
  }

  return (
    <main>
      <section className="hero">
        <h1>What company do you want to understand today?</h1>
        <p>What materially changed, why it matters, where it’s progressing or falling behind — and what to watch next. Every point cited.</p>
        <form
          className="searchbar"
          onSubmit={(e) => {
            e.preventDefault();
            submit(q, "text");
          }}
        >
          <input
            value={q}
            onChange={(e) => { setQ(e.target.value); setInputMode("text"); }}
            placeholder="e.g. Kinetic Engineering Ltd, or “What changed at NVIDIA this week?”"
            aria-label="Company or question"
            maxLength={300}
            autoFocus
          />
          <VoiceInput
            disabled={busy}
            onText={(t) => {
              setQ(t);
              setInputMode("voice");
              submit(t, "voice");
            }}
          />
          <button className="btn btn-primary" disabled={busy || !q.trim()}>{busy ? "Working…" : "Brief me"}</button>
        </form>
        <div className="chips" role="radiogroup" aria-label="Report mode">
          {MODES.map((m) => (
            <button key={m.key} type="button" className={`chip ${mode === m.key ? "active" : ""}`} onClick={() => setMode(m.key)} title={m.hint} aria-pressed={mode === m.key}>
              {m.label}
            </button>
          ))}
        </div>
        <div className="chips">
          {EXAMPLES.map((x) => (
            <button key={x} className="chip" type="button" onClick={() => { setQ(x); submit(x, "text"); }}>{x}</button>
          ))}
        </div>
      </section>

      {err && <div className="notice" role="alert">{err}</div>}

      {ambiguous && (
        <section className="card">
          <p className="section-title">Which company did you mean?</p>
          <p className="small muted">Several listed companies match “{ambiguous.query.company}”. We never pick one silently.</p>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Company</th><th>Country</th><th>Exchange</th><th>Ticker</th><th></th></tr></thead>
              <tbody>
                {ambiguous.candidates.map((c) => (
                  <tr key={c.candidate_id}>
                    <td>{c.name}</td>
                    <td>{FLAGS[c.country] || ""} {c.country}</td>
                    <td>{c.listings.map((l) => l.exchange).join(", ")}</td>
                    <td>{c.listings.map((l) => l.ticker).join(", ")}</td>
                    <td><button className="btn small" onClick={() => pick(c)} disabled={busy}>Select</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </main>
  );
}
