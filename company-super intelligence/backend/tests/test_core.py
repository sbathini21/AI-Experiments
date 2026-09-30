"""Offline unit tests for the parts that protect correctness: parsing, resolution, dedup,
verification, and the anti-hallucination validators."""
import os
from datetime import datetime, timezone

os.environ["DATABASE_URL"] = "sqlite:///./data/test.db"
os.environ["RUN_JOBS_INLINE"] = "false"

import pytest  # noqa: E402

from app.pipeline import synthesize  # noqa: E402
from app.pipeline.extract import score_materiality, verification_for  # noqa: E402
from app.pipeline.ingest import content_hash, match_confidence  # noqa: E402
from app.pipeline.resolve import _name_score, normalize_name, parse_query  # noqa: E402
from app.pipeline.taxonomy import classify_title  # noqa: E402
from app.sources.base import CompanyContext, RawDoc  # noqa: E402
from app.sources.company_ir import headline_from_filing_text, parse_date  # noqa: E402


@pytest.mark.parametrize("q,company,window", [
    ("Give me today's update on Kinetic Engineering.", "Kinetic Engineering", 1),
    ("What changed at Kinetic Engineering this week?", "Kinetic Engineering", 7),
    ("What are the latest developments at NVIDIA?", "NVIDIA", 7),
    ("Why is Apple in the news today?", "Apple", 1),
    ("What should I know about Tata Motors?", "Tata Motors", 7),
    ("What changed in Reliance this month", "Reliance", 30),
    ("Kinetic Engineering Ltd", "Kinetic Engineering Ltd", 7),
])
def test_parse_query(q, company, window):
    p = parse_query(q)
    assert p["company"] == company and p["window_days"] == window


def test_name_scoring_prefers_exact_and_flags_near_ties():
    nq = normalize_name("Tata Motors")
    assert _name_score(nq, "Tata Motors Limited") == 100
    # A longer name containing all query words is close enough to force a user choice
    assert _name_score(nq, "Tata Motors Passenger Vehicles Limited") >= 87
    assert _name_score(normalize_name("Kinetic Engineering"), "Pitti Engineering Limited") < 87


def ctx(**kw):
    base = dict(company_id="c1", legal_name="Kinetic Engineering Limited", short_name="Kinetic Engineering", aliases=[],
                country_code="IN", tickers=[("BSE", "KNEL")], identifiers={}, industry="Automotive",
                industry_keywords=["automotive"], website_domains=["kineticindia.com"])
    base.update(kw)
    return CompanyContext(**base)


def test_company_matching_rejects_other_companies():
    on = RawDoc("gdelt", "article", "https://x.com/a", "Kinetic Engineering shares rise 5%", None)
    off = RawDoc("gdelt", "article", "https://x.com/b", "Kinetic Group announces new fund", None)
    assert match_confidence(ctx(), on) >= 0.9
    assert match_confidence(ctx(), off) < 0.5


def test_content_hash_collapses_syndicated_copies():
    t = datetime(2026, 9, 28, tzinfo=timezone.utc)
    a = RawDoc("gdelt", "article", "https://a.com/story?utm_source=x", "Kinetic Engineering bags large export order from Europe", t)
    b = RawDoc("brave_search", "article", "https://b.com/other-url", "Kinetic Engineering bags large export order from Europe", t)
    assert content_hash(a) == content_hash(b)


def test_verification_hierarchy():
    assert verification_for([1, 3], {"sec.gov", "reuters.com"}) == "verified_primary"
    assert verification_for([2], {"kineticindia.com"}) == "company_claim"
    assert verification_for([3, 4], {"reuters.com", "etauto.com"}) == "corroborated"
    assert verification_for([3], {"reuters.com"}) == "single_source"
    assert verification_for([5, 6], {"linkedin.com", "reddit.com"}) == "unverified"


def test_materiality_ranks_acquisition_above_board_meeting():
    hi = score_materiality("acquisition", 1, 2, "acquires rival for ₹500 crore")
    lo = score_materiality("board_meeting", 2, 1, "intimation of board meeting")
    assert hi[1] == "high" and lo[1] in ("low", "medium") and hi[0] > lo[0]


def test_classifier():
    assert classify_title("Kinetic Engineering bags ₹120 crore order from OEM")[0] == "new_order"
    assert classify_title("Company announces buyback of shares")[0] == "buyback"
    assert classify_title("SEBI imposes penalty on promoter")[0] in ("regulatory_action", "promoter_transaction")


def test_ir_date_and_headline_parsing():
    assert parse_date("press-release-29.05.2026.pdf")[0].date().isoformat() == "2026-05-29"
    assert parse_date("August 03, 2026")[0].date().isoformat() == "2026-08-03"
    assert parse_date("/wp-content/uploads/2026/07/file.pdf")[1] == "month"
    letter = ("Registered Address: Pune Date: 11 March, 2026 To The Manager BSE Limited Scrip Code: BSE-500240 Subject: Press Release "
              "Dear Sir/Madam, Pursuant to Regulation 30 please find enclosed Press Release titled “Company Secures ₹40 Crore "
              "Promoter Infusion” Kindly take the above on record.")
    assert headline_from_filing_text(letter) == "Company Secures ₹40 Crore Promoter Infusion"


def test_number_validator_blocks_invented_figures():
    corpus = "Kinetic Engineering secures ₹40 crore promoter infusion on 11 March 2026"
    assert synthesize._numbers_supported("The company announced a ₹40 crore infusion (11 Mar 2026).", corpus)
    assert not synthesize._numbers_supported("The company announced a ₹400 crore infusion.", corpus)


class FakeLLM:
    name = "fake:model"

    def __init__(self, out):
        self.out = out

    def json(self, system, user, schema, max_tokens=8000):
        return self.out


def test_llm_synthesis_drops_uncited_and_unsupported_items(monkeypatch):
    class D:  # minimal stand-ins for ORM rows
        def __init__(self, title):
            self.id, self.title, self.snippet, self.body_text = "d1", title, None, None
            self.published_at = datetime(2026, 3, 11, tzinfo=timezone.utc)

    class E:
        id, event_type, category, title, description = "e1", "fundraising", "capital", "₹40 crore promoter infusion", None
        verification, claim_type, materiality, sentiment, materiality_score = "company_claim", "company_claim", "high", "positive", 0.7
        event_date = datetime(2026, 3, 11).date()

    fake = FakeLLM({
        "headline": "One development",
        "summary": [
            {"text": "The company stated it secured a ₹40 crore promoter infusion (11 Mar 2026).", "claim_type": "company_claim", "source_refs": [1], "event_ids": ["e1"]},
            {"text": "The company won a ₹900 crore order.", "claim_type": "fact", "source_refs": [1], "event_ids": ["e1"]},  # invented number
            {"text": "Revenue doubled.", "claim_type": "fact", "source_refs": [], "event_ids": []},  # no citation
            {"text": "Cites a source that doesn't exist.", "claim_type": "fact", "source_refs": [99], "event_ids": []},
        ],
        **{k: [] for k in synthesize.SECTION_TITLES},
    })
    monkeypatch.setattr(synthesize, "get_llm", lambda tier: fake)

    class C:
        legal_name, country_code, industry_text = "Kinetic Engineering Limited", "IN", "Automotive"

    out = synthesize.llm_sections(C(), [E()], {"e1": [1]}, [{"ref": 1, "publisher": "kineticindia.com", "tier_label": "Company", "title": "x", "published_at": None}],
                                  {1: D("Kinetic Engineering Secures ₹40 Crore Promoter Infusion")}, {"e1"}, [], "quick", 30, None)
    assert [i["text"][:40] for i in out["summary"]] == ["The company stated it secured a ₹40 cror"]
    assert len(out["dropped"]) == 3
