# Company Intel — AI company-intelligence MVP

Type or say a listed company (“Give me today’s update on Kinetic Engineering”). You get the 5–10 things that
materially changed, why they matter, where the company is progressing or falling behind, what management
previously promised and whether it’s on track, and what to watch next. Every point is cited and labelled
as fact, company claim, media report, or interpretation. A ~60-second voice briefing is included.

* **Markets:** India (NSE/BSE), US (NYSE/Nasdaq/OTC), UK (LSE). Adding a market means adding reference rows, not writing new code paths.
* **Works without any AI key** (deterministic rules mode). Add an Anthropic/OpenAI/Google key for LLM-verified extraction and prose.
* **Anti-hallucination by construction:** LLM claims need a verbatim evidence quote found in the source; report items need valid citations, and every number must appear in a cited source. Failing items are dropped.

Docs: [architecture, schema, sources, API, costs, regulation](docs/ARCHITECTURE.md) · [deployment](docs/DEPLOYMENT.md)

Quick start (local): see the end of [DEPLOYMENT.md](docs/DEPLOYMENT.md#local-development).
