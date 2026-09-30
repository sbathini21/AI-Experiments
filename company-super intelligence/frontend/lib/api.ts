export type Mode = "quick" | "trader" | "long_term" | "research" | "institutional";

export const MODES: { key: Mode; label: string; hint: string }[] = [
  { key: "quick", label: "Quick", hint: "5–10 concise points" },
  { key: "trader", label: "Trader", hint: "Catalysts, corporate actions, market activity" },
  { key: "long_term", label: "Long-term", hint: "Growth, capex, execution, capital allocation" },
  { key: "research", label: "Research", hint: "Everything, including low-materiality items" },
  { key: "institutional", label: "Institutional", hint: "Guidance, ownership, risk, governance" },
];

export type Listing = { exchange: string; ticker: string; isin: string | null };
export type Candidate = { candidate_id: string; company_id: string; name: string; country: string; listings: Listing[]; industry: string | null; score: number };
export type Resolution = { status: "resolved" | "ambiguous" | "not_found"; query: { company: string; window_days: number }; selected: Candidate | null; candidates: Candidate[] };

export type Item = {
  text: string; claim_type: string; date: string | null; source_refs: number[]; event_ids: string[];
  materiality?: string; sentiment?: string | null; verification?: string | null; is_new?: boolean; event_type?: string;
};
export type Source = { ref: number; title: string; url: string; publisher: string; tier: number; tier_label: string; published_at: string | null; doc_type: string };
export type Guidance = { guidance_id: string; statement: string | null; given_on: string | null; metric: string; target: string | null; deadline: string | null; status: string; latest_evidence: string; source: { title: string; url: string } | null };
export type Report = {
  report_id: string;
  company: { id: string; legal_name: string; country: string; industry: string | null; industry_provenance?: string | null; lei: string | null; website: string | null; description?: string | null; listings: Listing[] };
  market: { country: string; exchanges: string[] };
  mode: Mode; window_days: number; generated_at: string; data_freshness: string | null;
  headline: string; no_material_change: boolean; coverage_insufficient?: boolean;
  summary: Item[]; sections: Record<string, Item[]>; section_titles: Record<string, string>; section_notes?: Record<string, string>;
  management_guidance: Guidance[];
  change_vs_previous: { previous_report_at: string | null; new_events: number; events_in_window: number };
  coverage: { connectors: { connector: string; status: string; note: string | null; found: number }[]; gaps: string[] };
  sources: Source[]; synthesizer: string; disclaimer: string; voice_script: string;
};
export type EventOut = {
  id: string; date: string | null; type: string; category: string; title: string; description: string | null; materiality: string;
  verification: string; claim_type: string; sentiment: string | null; sources: { title: string; url: string; publisher: string; tier: number; tier_label: string }[];
};
export type Job = { id: string; status: "queued" | "running" | "done" | "failed"; progress: string[]; error: string | null; report?: Report };

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export async function api<T>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const { json, ...rest } = init || {};
  const res = await fetch(path, {
    ...rest,
    credentials: "include",
    headers: { ...(json !== undefined ? { "content-type": "application/json" } : {}), ...(rest.headers || {}) },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { const b = await res.json(); msg = typeof b.detail === "string" ? b.detail : JSON.stringify(b.detail ?? b); } catch {}
    throw new ApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}

export const fmtDate = (d: string | null | undefined) =>
  d ? new Date(d.length === 10 ? d + "T00:00:00" : d).toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" }) : "—";

export const fmtDateTime = (d: string | null | undefined) =>
  d ? new Date(d).toLocaleString(undefined, { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZoneName: "short" }) : "—";

export const FLAGS: Record<string, string> = { IN: "🇮🇳", US: "🇺🇸", GB: "🇬🇧" };

export const CLAIM_LABEL: Record<string, string> = {
  fact: "Fact · filing", company_claim: "Company disclosure", media_report: "Media report",
  company_social: "Company social", unverified: "Unverified", ai_interpretation: "Interpretation",
};
