"use client";
import { useState } from "react";
import { api, EventOut, fmtDate } from "@/lib/api";

type AskOut = { answer: string | null; could_not_verify?: boolean; message?: string; events: EventOut[] };
const SUGGESTIONS = ["What did management previously promise?", "What changed from last month?", "Explain the most important point in simple language", "Show me the source for the fundraising"];

export default function AskBox({ companyId }: { companyId: string }) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [out, setOut] = useState<AskOut | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function ask(question: string) {
    if (!question.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      setOut(await api<AskOut>(`/api/company/${companyId}/ask`, { method: "POST", json: { question } }));
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card stack">
      <p className="section-title">Ask a follow-up</p>
      <form className="row" onSubmit={(e) => { e.preventDefault(); ask(q); }}>
        <input className="btn" style={{ flex: 1, minWidth: 200, borderRadius: 10, textAlign: "left" }} value={q} onChange={(e) => setQ(e.target.value)}
          placeholder="e.g. What are competitors doing?" maxLength={500} aria-label="Follow-up question" />
        <button className="btn btn-primary" disabled={busy || !q.trim()}>{busy ? "…" : "Ask"}</button>
      </form>
      <div className="chips" style={{ justifyContent: "flex-start", marginTop: 8 }}>
        {SUGGESTIONS.map((s) => <button key={s} className="chip" onClick={() => { setQ(s); ask(s); }}>{s}</button>)}
      </div>
      {err && <div className="notice">{err}</div>}
      {out && (
        <div className="stack">
          {out.answer && <p style={{ margin: 0 }}>{out.answer}</p>}
          {out.message && <p className="small muted" style={{ margin: 0 }}>{out.message}</p>}
          {out.events.length > 0 && (
            <ul className="small" style={{ paddingLeft: 18, margin: 0 }}>
              {out.events.map((e) => (
                <li key={e.id}>
                  {fmtDate(e.date)} — {e.title}{" "}
                  {e.sources[0] && <a href={e.sources[0].url} target="_blank" rel="noreferrer noopener">[{e.sources[0].publisher}]</a>}
                </li>
              ))}
            </ul>
          )}
          <p className="small muted" style={{ margin: 0 }}>Answers use only the stored, cited event database — no fresh web search.</p>
        </div>
      )}
    </section>
  );
}
