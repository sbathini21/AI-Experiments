"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, ApiError, fmtDateTime } from "@/lib/api";

type H = { history: { query: string; company_id: string; company: string; at: string; input_mode: string }[] };

export default function History() {
  const [rows, setRows] = useState<H["history"] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api<H>("/api/history").then((d) => setRows(d.history)).catch((e) => setErr(e instanceof ApiError && e.status === 401 ? "signin" : e.message)); }, []);

  if (err === "signin") return <main className="card"><p><Link href="/login">Sign in</Link> to keep your research history.</p></main>;
  if (err) return <div className="notice">{err}</div>;
  if (!rows) return <p className="muted">Loading…</p>;
  return (
    <main className="card">
      <p className="section-title">Recent research</p>
      {rows.length === 0 ? <p className="muted">No searches yet.</p> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>When</th><th>Company</th><th>Query</th></tr></thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  <td className="small">{fmtDateTime(r.at)}</td>
                  <td><Link href={`/company/${r.company_id}`}>{r.company}</Link></td>
                  <td className="small muted">{r.input_mode === "voice" ? "🎤 " : ""}{r.query}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
