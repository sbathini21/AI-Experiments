"use client";
import ListenPlayer from "./ListenPlayer";
import { CLAIM_LABEL, FLAGS, fmtDate, fmtDateTime, Item, Report } from "@/lib/api";

function Cites({ refs }: { refs: number[] }) {
  if (!refs?.length) return null;
  return (
    <>
      {refs.map((r) => (
        <a key={r} href={`#src-${r}`} className="cite" aria-label={`Source ${r}`}>[{r}]</a>
      ))}
    </>
  );
}

export function ItemView({ it }: { it: Item }) {
  return (
    <div>
      <div>{it.text}<Cites refs={it.source_refs} /></div>
      <div className="tags">
        {it.is_new && <span className="tag new">New</span>}
        {it.materiality && it.claim_type !== "ai_interpretation" && <span className={`tag ${it.materiality}`}>{it.materiality} materiality</span>}
        <span className={`tag claim-${it.claim_type}`}>{CLAIM_LABEL[it.claim_type] || it.claim_type}</span>
        {it.verification && !["ai_interpretation", "company_claim"].includes(it.claim_type) && it.verification !== it.claim_type && <span className="tag">{it.verification.replace(/_/g, " ")}</span>}
        {it.date && <span className="tag">{fmtDate(it.date)}</span>}
      </div>
    </div>
  );
}

const SECTION_ORDER = ["major_developments", "financial_developments", "management_updates", "industry_developments", "market_activity", "progress", "risks", "management_execution", "watch_next"];

export default function ReportView({ r }: { r: Report }) {
  const c = r.company;
  const exchanges = r.market.exchanges.join(" / ");
  return (
    <div className="stack">
      <header className="company-head">
        <h1>{c.legal_name}</h1>
        <div className="meta">
          <span>{FLAGS[c.country] || ""} {c.country}</span>
          <span>{exchanges}{c.listings[0] ? ` · ${c.listings.map((l) => l.ticker).join(", ")}` : ""}</span>
          {c.industry && <span title={c.industry_provenance ? `Industry source: ${c.industry_provenance}` : undefined}>{c.industry}{c.industry_provenance?.includes("Wikidata") ? "*" : ""}</span>}
          <span>Updated {fmtDateTime(r.generated_at)}</span>
          {r.data_freshness && <span>Newest source {fmtDate(r.data_freshness)}</span>}
        </div>
      </header>

      <section className="card stack">
        <p className="section-title">Daily company intelligence · last {r.window_days} day{r.window_days === 1 ? "" : "s"} · {r.mode.replace("_", "-")} mode</p>
        <p className="headline">{r.headline}</p>
        {r.coverage_insufficient && <div className="notice">Source coverage was insufficient. The absence of items here is not evidence that nothing changed.</div>}
        {r.change_vs_previous.previous_report_at && (
          <p className="small muted">
            {r.change_vs_previous.new_events} new since the previous briefing ({fmtDateTime(r.change_vs_previous.previous_report_at)}).
          </p>
        )}
        <ListenPlayer script={r.voice_script} reportId={r.report_id} />
        {r.summary.length > 0 && (
          <ol className="points">
            {r.summary.map((it, i) => <li key={i}><ItemView it={it} /></li>)}
          </ol>
        )}
      </section>

      <div className="grid-2">
        {SECTION_ORDER.filter((k) => k !== "management_execution" && r.sections[k]?.length).map((k) => (
          <section className="card" key={k}>
            <p className="section-title">{r.section_titles[k]}</p>
            {r.section_notes?.[k] && <p className="small muted" style={{ marginTop: 0 }}>{r.section_notes[k]}</p>}
            <ul className="points plain">
              {r.sections[k].map((it, i) => <li key={i}><ItemView it={it} /></li>)}
            </ul>
          </section>
        ))}
      </div>
      {!r.sections.risks?.length && !r.coverage_insufficient && (
        <p className="small muted">No evidence-backed risks or setbacks were found in the collected sources for this window. That is a statement about the sources, not a guarantee.</p>
      )}

      <section className="card">
        <p className="section-title">Management promise tracker</p>
        {r.management_guidance.length === 0 ? (
          <p className="muted small">No explicit forward-looking management targets were found in the collected company sources yet.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Management statement</th><th>Given</th><th>Target</th><th>Deadline</th><th>Status</th></tr></thead>
              <tbody>
                {r.management_guidance.map((g) => (
                  <tr key={g.guidance_id}>
                    <td>
                      “{g.statement}”
                      {g.source && <div className="small"><a href={g.source.url} target="_blank" rel="noreferrer noopener">Source</a></div>}
                      <div className="small muted">{g.latest_evidence}</div>
                    </td>
                    <td>{fmtDate(g.given_on)}</td>
                    <td>{g.target || "—"}</td>
                    <td>{g.deadline || "—"}</td>
                    <td className={`status status-${g.status}`}>{g.status.replace(/_/g, " ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="small muted">Status changes only when a later stored event supports it; otherwise it stays “no recent evidence”.</p>
      </section>

      <section className="card">
        <p className="section-title">Sources</p>
        <ol className="small" style={{ paddingLeft: 22, margin: 0 }}>
          {r.sources.map((s) => (
            <li key={s.ref} id={`src-${s.ref}`} style={{ marginBottom: 8 }}>
              <a href={s.url} target="_blank" rel="noreferrer noopener">{s.title}</a>
              <div className="muted">{s.publisher} · {s.tier_label} (tier {s.tier}) · {fmtDate(s.published_at)}</div>
            </li>
          ))}
        </ol>
        {r.sources.length === 0 && <p className="muted small">No sources cited.</p>}
        <details className="small" style={{ marginTop: 12 }}>
          <summary className="muted">Source coverage for this report ({r.coverage.connectors.filter((x) => x.status === "ok").length}/{r.coverage.connectors.length} connectors returned data)</summary>
          <table style={{ marginTop: 8 }}>
            <tbody>
              {r.coverage.connectors.map((x) => (
                <tr key={x.connector}><td>{x.connector}</td><td>{x.status}</td><td>{x.found}</td><td className="muted">{x.note}</td></tr>
              ))}
            </tbody>
          </table>
          <p className="muted">Synthesised by: {r.synthesizer === "rules" ? "deterministic rules (no LLM)" : r.synthesizer}</p>
        </details>
      </section>
      <p className="small muted">{r.disclaimer}{c.industry_provenance?.includes("Wikidata") ? " *Industry classification from Wikidata (community-maintained)." : ""}</p>
    </div>
  );
}
