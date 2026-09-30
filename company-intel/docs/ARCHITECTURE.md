# Company Intel — MVP Architecture

> “What materially changed about this company, why does it matter, where is it getting ahead or falling
> behind, and what should I watch next?” — in 30–60 seconds, every point cited.

This document covers the 12 requested deliverables. Everything described as *implemented* exists in this
repo and was exercised end-to-end against **Kinetic Engineering Ltd (BSE)** on 30 Sep 2026.

---

## 1. System architecture

```
 Browser (Next.js)                         VPS (docker compose)
 ┌──────────────┐  HTTPS  ┌───────┐   ┌──────────────────────────┐   ┌────────────┐
 │ text / 🎤 STT │ ──────► │ Caddy │──►│ api  (FastAPI, 2 workers) │──►│ PostgreSQL │
 │ report UI     │ ◄────── │  TLS  │   │  resolve · analyze · ask │   │  events,   │
 │ ▶ TTS         │         └───────┘   └──────────┬───────────────┘   │  sources,  │
 └──────────────┘                                  │ jobs table        │  reports,  │
                                                   ▼                   │  job queue │
                                       ┌──────────────────────────┐   └────────────┘
                                       │ worker (python -m app.worker)                │
                                       │  research pipeline + daily watchlist refresh │
                                       └────────┬─────────────────┘
                     permitted sources ◄────────┘  LLM provider (optional, routed cheap/strong)
     SEC EDGAR · Companies House · company IR sites · GDELT · Brave Search · OpenFIGI · GLEIF · Wikidata
```

**The pipeline (implemented in `backend/app/pipeline/`):**

| Step | Module | What happens |
|---|---|---|
| 1–2 Resolve & profile | `resolve.py` | NL query → company name + time window; local securities master (SEC + NSE lists) → OpenFIGI fallback; **auto-select only if the best match is ≥90 and ≥10 points clear of the runner-up**, otherwise the user picks. Enrichment: SEC (SIC, website), GLEIF (LEI, legal name/address), Wikidata (industry/website, labelled “community-maintained”). |
| 3–8 Discover & collect | `ingest.py`, `sources/*` | Connectors run in parallel, each reporting `ok / skipped / error` + reason. Robots.txt honoured, per-host throttles, 10-min–24-h response cache. |
| 9 Extract | `extract.py`, `taxonomy.py` | Rules classifier (45 event types, SEC 8-K item mapping) always runs. Optional cheap LLM refines type/summary — **only accepted if its verbatim evidence quote is found in the document**. |
| 10 Dedupe | `ingest.content_hash`, `extract` | Document level: content hash on normalised title+day (syndicated copies collapse) or filing id. Event level: same type + title Jaccard ≥0.45 within ±3 days → one event with several sources. |
| 11–12 Credibility & cross-check | `reference.py`, `extract.verification_for` | Source tier 1–6 → `verified_primary / company_claim / corroborated / single_source / unverified`. |
| 13 Store | `models.py` | Events, event_sources, statements, guidance. |
| 14 Historical comparison | `synthesize.build_report` | Diff against the previous report’s event set → `is_new`, “N new since previous briefing”. |
| 15 Guidance | `extract.rule_guidance`, `run.track_guidance` | Forward-looking statements (verbatim) → promise tracker. Status changes **only** with a later supporting event id. |
| 16–17 Context & materiality | `extract.score_materiality`, modes | Type weight × source tier × corroboration × quantified impact → high/medium/low; investor modes reweight categories. |
| 18–19 Report & citations | `synthesize.py` | Rules synthesiser (zero cost) or strong LLM; LLM items are **dropped** unless they cite provided refs and every number appears in the cited sources. |
| 20 Voice | `synthesize.voice_script`, frontend | ~150-word script (~60 s); browser TTS by default, optional server TTS (cached MP3). |

**Design principle held throughout:** the LLM is the reasoning/synthesis layer, never the database. With no
API key at all the system still produces a correct, cited (if plainer) report.

---

## 2. Technology stack (chosen)

| Layer | Choice | Why |
|---|---|---|
| Frontend | Next.js 14 (App Router), plain CSS | SSR-ready, `/api` rewrite keeps backend + keys server-side; 90 kB first load. |
| Backend | Python 3.11 + FastAPI + SQLAlchemy 2 | Best ecosystem for parsing (pypdf), NLP, data. |
| DB | PostgreSQL 16 (SQLite for local dev/tests) | JSONB for reports/raw payloads; `SKIP LOCKED` job queue. |
| Queue | **Postgres `jobs` table** (no Redis) | One fewer service; single-flight via unique `dedupe_key`. Add Redis/RQ only past ~10 jobs/s. |
| Search | Postgres (LIKE + RapidFuzz rerank) | 13k securities resolve in ms. OpenSearch only when full-text over millions of docs is needed. |
| AI | Provider-neutral `app/llm` — Anthropic (official SDK), OpenAI, Google via REST | `LLM_CHEAP=anthropic:claude-haiku-4-5`, `LLM_STRONG=anthropic:claude-opus-5-5` (or `claude-sonnet-5-5` to halve synthesis cost). Structured JSON outputs + server-side refusal fallback. |
| Speech | Browser Web Speech API (free) → OpenAI whisper-1 / tts-1 fallback | Zero marginal cost for most users. |
| Edge | Caddy 2 | Automatic Let’s Encrypt, HTTP/3, one small config file. |
| Hosting | One VPS (Hetzner CX22/CX32 or Hostinger KVM 2) | See §9–10. |

---

## 3. Database schema

28 tables; canonical PostgreSQL DDL is generated from the models: `backend/migrations/schema.sql`
(`python -m app.tools.dump_schema`). Core shape:

```
countries ─< exchanges ─< securities >─ companies ─< documents ─< filings / articles
                                           │   │            │
                     industries ─ sectors ─┘   │            └──< event_sources >── events
                                               ├──< events ─────────────────────────┘
                                               ├──< management_statements ─< management_guidance ─< guidance_tracking
                                               ├──< financial_metrics, competitors, market_data (via securities)
                                               └──< company_daily_reports ─< citations, audio_reports
users ─< watchlists ─< watchlist_items ; users ─< alerts ; research_queries ; sources (registry) ; jobs
```

Key decisions:
* **Generic market hierarchy** Country → Exchange (ISO MIC) → Security → Company. India/US/UK are rows in
  `reference.py`, not code paths. New market = rows + optional regulatory connector.
* **Events are the asset**, not articles: `events` carries type, category, dates, materiality score,
  verification, claim type (`fact / company_claim / media_report / company_social / unverified`),
  sentiment, evidence quote, fingerprint, extractor (`rules` or model name).
* **documents.content_hash** unique per company → dedup; `credibility_tier` stored per document.
* **company_daily_reports.report** stores the exact JSON the UI rendered (auditability); `citations` rows
  link every report item to documents/events.
* `research_queries.client_key` is a salted hash — raw IPs are never stored.

---

## 4. Agent architecture

The MVP deliberately implements the “agents” as **deterministic pipeline stages with narrow LLM calls**, not
autonomous tool-using agents. That keeps cost predictable and every claim traceable.

| Future agent | MVP implementation today | LLM tier |
|---|---|---|
| Company Research / News | connectors + `collect_and_store` | none |
| Event Detection | `taxonomy.classify_title` + `llm_refine` | cheap |
| Verification | `verification_for`, evidence-quote check, number check | none |
| Management | `rule_guidance` + LLM forward-looking extraction + `track_guidance` | cheap |
| Financial / Market | SEC forms, results events (XBRL `companyfacts` is the next step) | none |
| Synthesis | `llm_sections` with validator, or rules | strong |
| Follow-up Q&A | `/ask` — retrieval over stored events, answer must cite event ids | cheap |
| Competitor / Industry | schema + same-industry peers; LLM competitor extraction is Phase 2 | cheap |

All of them write to the same event database, which is what later lets portfolio/market questions
(“which companies in my watchlist raised capex?”) become SQL over events rather than new research.

---

## 5. Data-source architecture

| Source | Tier | Status in MVP | Terms / licensing position |
|---|---|---|---|
| SEC EDGAR submissions + filing docs | 1 | **Implemented** (US) | Public domain; fair access ≤10 req/s, descriptive User-Agent required. |
| Companies House API | 1 | **Implemented** (UK, needs free key) | UK OGL v3. |
| Company IR websites | 2 | **Implemented** (all markets) | Robots.txt honoured per URL; ≤5 pages + 8 PDFs per run; stores text for extraction, displays headline + link only. In India these carry copies of Reg 30 exchange filings (SEBI LODR Reg 46). |
| NSE / BSE announcements | 1 | **Gap — placeholder connector** | Both sites block automated access (verified: BSE API returns *Access Denied*). Production needs a licensed feed (exchange data products or a licensed vendor). |
| LSE RNS | 1 | Gap | RNS content is licensed by LSEG; use a licensed feed. FCA NSM is a possible free route to evaluate. |
| GDELT DOC API | 3–4 | Implemented | Open; 1 request / 5 s. Was rate-limiting this dev IP during testing — reported honestly in the report’s coverage block. |
| Brave Search API | 3–5 | Implemented (needs key) | Licensed commercial search; used for IR pages, industry media, and **public LinkedIn search snippets** (no LinkedIn scraping — prohibited by its User Agreement). |
| Google News RSS | 3–4 | Implemented, **off by default** | Feed terms: personal, non-commercial use only. |
| OpenFIGI | — | Implemented (resolution) | Free, rate-limited; attribution. |
| GLEIF | — | Implemented (LEI, legal name) | CC0. |
| Wikidata | — | Implemented (industry/website hints) | CC0; community-edited, so labelled as such in the UI. |
| Market prices | — | Gap | Exchange price data is licensed. Candidates: a licensed EOD vendor; until then “market activity” comes from reported news only. |

**Industry media** is discovered dynamically: the industry label drives search keywords
(`INDUSTRY_KEYWORDS` in `resolve.py`), and unknown news domains default to tier 4 (published outlet, not
curated). Curated trade press is listed in `reference.SOURCE_REGISTRY` and grows as rows.

**Honesty rule in code:** if no connector returned data, the report headline says coverage was insufficient
and explicitly that this “is not evidence that nothing changed” (`coverage_insufficient: true`).

---

## 6. API design

Interactive docs at `/api/docs`. All endpoints below are implemented.

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/search` | NL entry point: resolve → analyze (or return candidates) |
| POST | `/api/company/resolve` | Resolve text to a company or candidate list |
| POST | `/api/company/analyze` | Return fresh cached report or enqueue a single-flight job |
| GET | `/api/jobs/{id}` | Job status, progress log, report when done |
| GET | `/api/company/{id}` | Profile + listings |
| GET | `/api/company/{id}/latest` | Latest report (`?mode=`) |
| GET | `/api/company/{id}/timeline?window=24h\|7d\|30d\|90d\|1y\|3y` | Stored events with sources |
| GET | `/api/company/{id}/events` | Filter by type/materiality |
| GET | `/api/company/{id}/guidance` | Promise tracker |
| GET | `/api/company/{id}/competitors` | Stored competitors + same-industry peers (labelled) |
| POST | `/api/company/{id}/ask` | Follow-up Q&A over the stored event DB |
| GET | `/api/reports/{id}` | Any stored report |
| POST | `/api/voice/transcribe`, `/api/voice/synthesize` | Server STT / cached TTS (optional) |
| GET/POST/DELETE | `/api/watchlist` | My companies |
| GET/POST | `/api/alerts` | Alert rules (delivery is Phase 2) |
| POST | `/api/auth/signup`, `/login`, `/logout`; GET `/me`, `/history` | Accounts |

Example (real output, abridged):

```http
POST /api/company/resolve  {"query": "Give me today's update on Kinetic Engineering."}
→ {"status": "resolved",
   "query": {"company": "Kinetic Engineering", "window_days": 1},
   "selected": {"company_id": "62fb…", "name": "Kinetic Engineering Ltd", "country": "IN",
                "listings": [{"exchange": "BSE", "ticker": "KNEL", "isin": null}], "score": 100}}

POST /api/company/resolve  {"query": "Tata Motors"}
→ {"status": "ambiguous", "candidates": [
     {"name": "Tata Motors Limited", "listings": [{"exchange": "NSE", "ticker": "TMCV"}], "score": 100},
     {"name": "Tata Motors Passenger Vehicles Limited", "listings": [{"exchange": "NSE", "ticker": "TMPV"}], "score": 87.5}]}

POST /api/company/analyze  {"company_id": "62fb…", "mode": "quick", "window_days": 365}
→ {"status": "queued", "job_id": "9e0a…"}         # or {"status": "ready", "cached": true, "report": {…}}

GET /api/jobs/9e0a…
→ {"status": "done", "progress": ["… company_ir: ok (12 items)", …], "report": {
     "headline": "5 material developments in the last 365 days; most significant: Kinetic Engineering Secures ₹40 Crore Promoter Infusion …",
     "summary": [{"text": "In a disclosure to the stock exchange dated 11 Mar 2026 (copy on the company website), the company announced: “Kinetic Engineering Secures ₹40 Crore Promoter Infusion to Accelerate EV and Component Growth”.",
                  "claim_type": "company_claim", "materiality": "high", "date": "2026-03-11", "source_refs": [1]}, …],
     "sections": {"major_developments": […], "progress": […], "risks": [], "watch_next": […], …},
     "management_guidance": [{"statement": "The company also aims to establish a nationwide network of 200 dealerships by FY 2026 –27 …",
                              "metric": "distribution_network", "target": "200 dealerships", "deadline": "FY 2026 –27", "status": "no_recent_evidence"}],
     "sources": [{"ref": 1, "title": "…", "url": "https://kineticindia.com/wp-content/uploads/2026/07/press-release-11-03-2026.pdf", "tier": 2}],
     "coverage": {"gaps": ["india_exchange_announcements: … licensed feed …", "gdelt: … 429 …"]},
     "voice_script": "Here are the five developments worth knowing about Kinetic Engineering …", "disclaimer": "…"}}
```

---

## 7. UI wireframe (implemented)

```
┌───────────────────────────────────────────────────────────────┐
│ ◆ Company Intel                    My companies  History  Account│
│        What company do you want to understand today?           │
│  [ Kinetic Engineering Ltd …                 ] [🎤] [Brief me]  │
│   (Quick) (Trader) (Long-term) (Research) (Institutional)      │
│   → if ambiguous:  Company | Country | Exchange | Ticker | [Select]
└───────────────────────────────────────────────────────────────┘
┌ /company/{id} ────────────────────────────────────────────────┐
│ [Quick mode ▾] [Last 7 days ▾]              [☆ Watch] [↻ Refresh]│
│ KINETIC ENGINEERING LIMITED                                     │
│ 🇮🇳 IN · BSE · KNEL · Automotive* · Updated … · Newest source …  │
│ DAILY COMPANY INTELLIGENCE                                      │
│ Headline sentence                                               │
│ [▶ Listen] [↺ Replay] Speed 1× ≈60 s [⬇ MP3]                     │
│ 1 … text … [1]   (high materiality)(Company disclosure)(date)   │
│ 2 …                                                             │
│ ┌ Major developments ┐ ┌ Where it is getting ahead ┐             │
│ ┌ Management update  ┐ ┌ Risks / falling behind    ┐ ┌ Watch next┐│
│ Management promise tracker: statement | given | target | deadline | status │
│ Sources [1]… (publisher · tier · date) ▸ coverage per connector  │
│ Company timeline  [24H 7D 30D 90D 1Y 3Y]  date|event|category|materiality|source │
│ Ask a follow-up  [ … ] (suggestion chips)                        │
└───────────────────────────────────────────────────────────────┘
```

---

## 8. 8-week plan (adjusted to where the build is now)

| Week | Scope | Status |
|---|---|---|
| 1 | Architecture, schema, project setup, auth | ✅ done |
| 2 | Company resolution, securities master (SEC + NSE), OpenFIGI/GLEIF/Wikidata | ✅ done — add BSE + LSE master CSVs |
| 3 | Connectors: SEC, Companies House, company IR, GDELT, Brave, Google RSS (off) | ✅ done — **decide & contract an Indian exchange feed** (biggest data gap) |
| 4 | Event extraction, dedup, verification, materiality | ✅ rules + LLM-verified; next: gold-set of 200 labelled events to measure precision |
| 5 | AI synthesis, citations, validators, report JSON | ✅ done — run with a real LLM key and review 30 reports by hand |
| 6 | Guidance tracker, timeline, competitor/industry context | Tracker + timeline ✅; competitor extraction + SEC XBRL financials ⏳ |
| 7 | Voice in/out, watchlist, UX | ✅ done; alert delivery (email) ⏳ |
| 8 | Security review, load test, deploy, analytics (Plausible/PostHog self-host), feedback button | Compose + Caddy + backups ✅; deploy to VPS ⏳ |

---

## 9. Infrastructure

Single VPS, five containers: `caddy` (TLS) → `web` (Next.js) and `api` (FastAPI) → `db` (Postgres);
`worker` runs research jobs and the daily watchlist refresh. Postgres isn’t published to the host.
Backups: `deploy/backup.sh` (nightly `pg_dump`, 14-day retention) + offsite copy (restic/rclone → B2).
Scale path: (1) bigger VPS, (2) move Postgres to managed (Supabase/Neon/Hetzner managed), (3) N workers,
(4) Redis rate-limit/cache when running >1 API host.

Step-by-step commands: [`DEPLOYMENT.md`](DEPLOYMENT.md).

---

## 10. Estimated monthly cost

**Fixed (MVP):** VPS $5–10 (Hetzner CX22/CX32 or Hostinger KVM 2) · domain ~$1 · offsite backups <$1 ·
TLS $0 → **≈ $7–12 / month.** Rules mode (no LLM, no paid search) keeps the *total* there.

**Variable per fresh research run** (cache miss), from prompt sizes in the code:

| Item | Tokens / calls | Cost |
|---|---|---|
| Extraction (Haiku 4.5, $1/$5 per MTok), new docs only | ~9k in / 3k out per 15 docs | ~$0.02 |
| Synthesis (Opus 5.5, $4/$20) — skipped when nothing new | ~8k in / 2.5k out | ~$0.08 (Sonnet 5.5: ~$0.04) |
| Guidance tracking + follow-ups (Haiku) | small | ~$0.005 |
| Brave Search (4 queries) | check current Brave pricing | ~$0.01–0.02 |

Assumptions: 30 % DAU, 2 briefings per active user per day, report cache hit rate rising with scale
(shared companies), ~35 % of runs have new material events (the rest reuse prior prose).

| Users | Fresh runs / day | Variable / month | Infra / month | Total |
|---|---|---|---|---|
| 100 | ~40 | $25–55 | $8–12 | **~$35–65** (Sonnet + no Brave ≈ $30) |
| 1,000 | ~270 | $180–450 | $20–40 | **~$200–500** |
| 10,000 | ~1,500 | $1.0k–2.5k | $100–250 | **~$1.1k–2.8k** |
| 100,000 | ~6,000 (capped by distinct companies) | $4k–10k | $1k–2k | **~$5k–12k** |

Levers already built: 30-min single-flight report cache, document dedup (extract once), cheap-model routing,
prose reuse when no new events, browser STT/TTS. Next levers: Message Batches API (50 % off) for the
overnight watchlist refresh, prompt caching of the synthesis system prompt, per-company scheduled research
so cost scales with *companies covered*, not users.

---

## 11. Regulatory & data-licensing considerations

*Not legal advice — engage counsel in each market before public launch, particularly before adding any
personalised features.*

**Positioning (enforced in code & copy):** public-information research and evidence-based synthesis.
No buy/sell/hold, no price targets, no suitability, no execution, no portfolio management. The synthesis
prompt forbids advice language; every report carries a disclaimer; company claims are labelled as claims.

| Activity | Where the line typically sits |
|---|---|
| Information / data / news aggregation | Generally outside advice regimes, but still subject to content licensing and fair-dealing rules. |
| Research (opinions on particular securities) | India: SEBI (Research Analysts) Regulations, 2014 (as amended, incl. 2024–25 changes such as disclosure of AI-tool use) can apply to persons issuing research reports or opinions on securities. US: impersonal, bona fide, regularly circulated publications can fall within the Investment Advisers Act “publisher’s exclusion” (*Lowe v. SEC*, 1985); personalised or promotional content erodes it. UK: “advising on investments” (RAO art. 53) requires advice on the merits for a person as investor; general, non-personal information usually isn’t — but financial promotions (FSMA s.21) are a separate test. |
| Personalised investment advice | Registration regimes (SEBI IA Regulations 2013; SEC/state investment adviser; FCA authorisation). **Out of scope.** |
| Execution / portfolio management | Broker/PMS/adviser regimes. **Out of scope.** |

Practical guard-rails for the roadmap: watchlist alerts must stay factual (“Company X disclosed Y”), not
“consider selling”; avoid ranking securities as “best to buy”; keep marketing claims about returns out.
In India, SEBI has restricted regulated entities from associating with unregistered persons who give
advice/recommendations or make return claims — relevant for any broker partnership.

**Privacy:** email + password hash only; no brokerage/bank credentials ever; salted-hash client keys instead of IPs;
data minimisation and deletion on request. Map to India’s DPDP Act 2023 (and its Rules), UK GDPR/DPA 2018,
EU GDPR (if EU users), and US state laws (e.g. CCPA/CPRA): privacy notice, lawful basis/consent for
analytics, processor agreements with LLM providers, retention limits.

**Data licensing** — see §5. Rules in code: official APIs first; robots.txt honoured; no scraping of
LinkedIn, NSE, BSE or RNS; Google News RSS disabled for commercial use; only headlines + links are shown,
never full republished articles.

---

## 12. Project structure

```
company-intel/
├── backend/
│   ├── app/
│   │   ├── main.py            FastAPI app, security headers, startup seeding
│   │   ├── config.py          all settings via env
│   │   ├── models.py          28-table data model (source of truth)
│   │   ├── reference.py       markets, exchanges, source-credibility registry
│   │   ├── security.py        JWT auth, bcrypt, rate limits, quotas
│   │   ├── worker.py          job runner + daily refresh scheduler
│   │   ├── api/routes.py      all HTTP endpoints
│   │   ├── llm/__init__.py    provider-neutral LLM layer (Anthropic SDK / OpenAI / Google)
│   │   ├── sources/           http (throttle+cache), connectors, company_ir
│   │   ├── pipeline/          resolve, ingest, extract, taxonomy, synthesize, run
│   │   └── tools/             seed (securities master), dump_schema
│   ├── migrations/schema.sql  generated PostgreSQL DDL
│   ├── scripts/e2e_analyze.sh end-to-end smoke test against a running API
│   └── tests/                 offline unit + API tests (20)
├── frontend/                  Next.js app (home, company report, watchlist, history, account)
├── deploy/                    Caddyfile, backup.sh
├── docker-compose.yml
├── .env.example
└── docs/ARCHITECTURE.md, DEPLOYMENT.md
```
