"use client";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import AskBox from "@/components/AskBox";
import ReportView from "@/components/ReportView";
import Timeline from "@/components/Timeline";
import { api, ApiError, Job, MODES, Mode, Report } from "@/lib/api";

const WINDOW_OPTS = [[1, "24 hours"], [7, "7 days"], [30, "30 days"], [90, "90 days"], [365, "1 year"]] as const;

export default function Page() {
  return (
    <Suspense fallback={<p className="muted">Loading…</p>}>
      <CompanyPage />
    </Suspense>
  );
}

function CompanyPage() {
  const { id } = useParams<{ id: string }>();
  const sp = useSearchParams();
  const router = useRouter();
  const mode = (sp.get("mode") as Mode) || "quick";
  const windowDays = Number(sp.get("window") || 7);
  const jobParam = sp.get("job");

  const [report, setReport] = useState<Report | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [watchMsg, setWatchMsg] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>();

  const analyzeRef = useRef<(force?: boolean) => void>(() => {});
  const poll = useCallback((jobId: string) => {
    api<Job>(`/api/jobs/${jobId}`)
      .then((j) => {
        setJob(j);
        if (j.status === "done" && j.report) setReport(j.report);
        else if (j.status === "done") analyzeRef.current(false); // job finished but its report is gone: start fresh
        else if (j.status === "failed") setErr(`Research failed: ${j.error || "unknown error"}`);
        else timer.current = setTimeout(() => poll(jobId), 2000);
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 429) timer.current = setTimeout(() => poll(jobId), 5000);
        else setErr(e.message);
      });
  }, []);

  const analyze = useCallback(async (force = false) => {
    setErr(null);
    setJob(null);
    try {
      const out = await api<{ status: string; job_id?: string; report?: Report }>("/api/company/analyze", {
        method: "POST", json: { company_id: id, mode, window_days: windowDays, force },
      });
      if (out.report) setReport(out.report);
      else if (out.job_id) poll(out.job_id);
    } catch (e: any) {
      setErr(e.message);
    }
  }, [id, mode, windowDays, poll]);
  analyzeRef.current = analyze;

  useEffect(() => {
    clearTimeout(timer.current);
    setReport(null);
    if (jobParam) poll(jobParam);
    else analyze(false);
    return () => clearTimeout(timer.current);
  }, [jobParam, analyze, poll]);

  function setParam(k: string, v: string) {
    const p = new URLSearchParams(sp.toString());
    p.set(k, v);
    p.delete("job");
    router.replace(`/company/${id}?${p.toString()}`);
  }

  async function watch() {
    try {
      await api("/api/watchlist", { method: "POST", json: { company_id: id } });
      setWatchMsg("Added to My companies");
    } catch (e) {
      setWatchMsg(e instanceof ApiError && e.status === 401 ? "Sign in to save companies" : "Couldn't add");
    }
  }

  return (
    <main className="stack">
      <div className="row spread">
        <div className="row">
          <select className="btn" value={mode} onChange={(e) => setParam("mode", e.target.value)} aria-label="Mode">
            {MODES.map((m) => <option key={m.key} value={m.key}>{m.label} mode</option>)}
          </select>
          <select className="btn" value={windowDays} onChange={(e) => setParam("window", e.target.value)} aria-label="Window">
            {WINDOW_OPTS.map(([d, l]) => <option key={d} value={d}>Last {l}</option>)}
          </select>
        </div>
        <div className="row">
          <button className="btn" onClick={watch}>☆ Watch</button>
          <button className="btn" onClick={() => analyze(true)} disabled={!!job && job.status !== "done" && job.status !== "failed"}>↻ Refresh research</button>
        </div>
      </div>
      {watchMsg && <div className="notice info">{watchMsg}</div>}
      {err && <div className="notice" role="alert">{err}</div>}

      {!report && !err && (
        <section className="card stack" aria-live="polite">
          <p className="section-title">Researching…</p>
          <p className="muted small" style={{ margin: 0 }}>Resolving the company, checking regulatory, company, media and industry sources, extracting and verifying events. Usually 20–90 seconds.</p>
          <div className="progress-log">{(job?.progress || ["queued"]).join("\n")}</div>
        </section>
      )}

      {report && <ReportView r={report} />}
      {report && <Timeline companyId={id} refreshKey={report.report_id} />}
      {report && <AskBox companyId={id} />}
    </main>
  );
}
