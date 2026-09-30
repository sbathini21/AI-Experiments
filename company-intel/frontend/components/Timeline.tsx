"use client";
import { useEffect, useState } from "react";
import { api, CLAIM_LABEL, EventOut, fmtDate } from "@/lib/api";

const WINDOWS = ["24h", "7d", "30d", "90d", "1y", "3y"];

export default function Timeline({ companyId, refreshKey }: { companyId: string; refreshKey: string }) {
  const [w, setW] = useState("90d");
  const [events, setEvents] = useState<EventOut[] | null>(null);

  useEffect(() => {
    setEvents(null);
    api<{ events: EventOut[] }>(`/api/company/${companyId}/timeline?window=${w}`).then((d) => setEvents(d.events)).catch(() => setEvents([]));
  }, [companyId, w, refreshKey]);

  return (
    <section className="card">
      <div className="row spread">
        <p className="section-title">Company timeline</p>
        <div className="tabs" role="tablist">
          {WINDOWS.map((x) => (
            <button key={x} className={`tab ${w === x ? "active" : ""}`} onClick={() => setW(x)} role="tab" aria-selected={w === x}>{x.toUpperCase()}</button>
          ))}
        </div>
      </div>
      {events === null ? (
        <p className="muted small">Loading…</p>
      ) : events.length === 0 ? (
        <p className="muted small">No stored events in this window. The timeline fills in as the company is researched over time.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Date</th><th>Event</th><th>Category</th><th>Materiality</th><th>Source</th></tr></thead>
            <tbody>
              {events.map((e) => (
                <tr key={e.id}>
                  <td style={{ whiteSpace: "nowrap" }}>{fmtDate(e.date)}</td>
                  <td>{e.title}<div className="small muted">{e.type.replace(/_/g, " ")} · {CLAIM_LABEL[e.claim_type] || e.claim_type}</div></td>
                  <td>{e.category}</td>
                  <td><span className={`tag ${e.materiality}`}>{e.materiality}</span></td>
                  <td className="small">
                    {e.sources.slice(0, 2).map((s, i) => (
                      <div key={i}><a href={s.url} target="_blank" rel="noreferrer noopener">{s.publisher}</a> <span className="muted">T{s.tier}</span></div>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
