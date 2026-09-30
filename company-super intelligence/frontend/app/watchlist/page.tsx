"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, ApiError, FLAGS, fmtDateTime } from "@/lib/api";

type WL = { watchlist: { name: string; items: { company_id: string; name: string; country: string; latest: { headline: string; generated_at: string; new_events: number } | null }[] } };

export default function Watchlist() {
  const [data, setData] = useState<WL["watchlist"] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = () => api<WL>("/api/watchlist").then((d) => setData(d.watchlist)).catch((e) => setErr(e instanceof ApiError && e.status === 401 ? "signin" : e.message));
  useEffect(() => { load(); }, []);

  if (err === "signin") return <main className="card"><p><Link href="/login">Sign in</Link> to build your watchlist.</p></main>;
  if (err) return <div className="notice">{err}</div>;
  if (!data) return <p className="muted">Loading…</p>;
  return (
    <main className="stack">
      <h1 style={{ fontFamily: "var(--serif)", margin: "8px 0 0" }}>My companies</h1>
      <p className="muted small" style={{ margin: 0 }}>What changed across your watchlist. Watched companies are refreshed once a day in the background.</p>
      {data.items.length === 0 && <div className="card muted">Nothing here yet — open a company report and click ☆ Watch.</div>}
      {data.items.map((it) => (
        <section className="card" key={it.company_id}>
          <div className="row spread">
            <Link href={`/company/${it.company_id}`} style={{ fontWeight: 600 }}>{FLAGS[it.country] || ""} {it.name}</Link>
            <button className="btn small" onClick={async () => { await api(`/api/watchlist/${it.company_id}`, { method: "DELETE" }); load(); }}>Remove</button>
          </div>
          {it.latest ? (
            <>
              <p style={{ margin: "8px 0 4px" }}>{it.latest.headline}</p>
              <p className="small muted" style={{ margin: 0 }}>{it.latest.new_events ?? 0} new events · {fmtDateTime(it.latest.generated_at)}</p>
            </>
          ) : (
            <p className="small muted">No report yet.</p>
          )}
        </section>
      ))}
    </main>
  );
}
