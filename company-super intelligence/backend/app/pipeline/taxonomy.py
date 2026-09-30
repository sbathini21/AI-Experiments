"""Expandable event taxonomy. Each type has a category, a base materiality weight (0..1) and
keyword patterns used by the rule-based extractor (LLM extraction uses the same type list)."""
import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class EventType:
    key: str
    category: str
    base: float  # base materiality weight
    patterns: tuple[str, ...] = field(default_factory=tuple)
    direction: str = "neutral"  # default business direction if not inferable


TAXONOMY: list[EventType] = [
    EventType("earnings", "financial", 0.75, (r"\bresults?\b", r"\bearnings\b", r"\bquarterly\b.*\b(profit|loss|revenue)", r"\bq[1-4]\b.*\b(profit|loss|revenue|results)", r"financial results", r"\bnet (profit|loss)\b")),
    EventType("guidance", "financial", 0.7, (r"\bguidance\b", r"\boutlook\b", r"\bforecast", r"\btargets?\b.*\bfy\d{2}")),
    EventType("guidance_revision", "financial", 0.85, (r"(raises?|cuts?|lowers?|revises?|withdraws?)\b.*\b(guidance|outlook|forecast)",)),
    EventType("revenue_change", "financial", 0.6, (r"\brevenue (rises|falls|jumps|drops|grows|declines)", r"\bsales (rise|fall|jump|drop|grow|decline)")),
    EventType("profit_change", "financial", 0.6, (r"\bprofit (rises|falls|jumps|drops|surges|plunges|doubles)", r"\bturns? (profitable|to profit|to loss)")),
    EventType("margin_change", "financial", 0.55, (r"\bmargins?\b.*(expand|contract|improv|declin|pressure)",)),
    EventType("cash_flow_change", "financial", 0.5, (r"\bcash flow\b",)),
    EventType("new_order", "business", 0.7, (r"\b(bags?|wins?|secures?|receives?|bagged|won|secured|received)\b.*\b(order|contract|mandate|deal)\b", r"\border (win|inflow|book)\b", r"\blarge order\b")),
    EventType("contract", "business", 0.6, (r"\bcontract\b", r"\bmaterial (definitive )?agreement\b")),
    EventType("supply_agreement", "business", 0.6, (r"\bsupply (agreement|deal|contract)\b",)),
    EventType("customer_win", "business", 0.65, (r"\bnew customer\b", r"\bselected (by|as)\b.*\bsupplier\b")),
    EventType("customer_loss", "business", 0.75, (r"\b(loses?|lost|terminat\w+)\b.*\b(customer|contract|client)\b",), "negative"),
    EventType("capacity_expansion", "capex", 0.65, (r"\bcapacity\b.*\b(expan|increas|addition|augment)", r"\bexpan\w+\b.*\bcapacity\b", r"\bdebottleneck")),
    EventType("new_plant", "capex", 0.65, (r"\bnew (plant|facility|factory|unit)\b", r"\b(inaugurat|commission)\w*\b.*\b(plant|facility|factory|unit)\b")),
    EventType("capex", "capex", 0.55, (r"\bcapex\b", r"\bcapital expenditure\b", r"\binvest\w*\b.*\b(crore|million|billion)\b.*\b(plant|facility|capacity)")),
    EventType("product_launch", "business", 0.45, (r"\bcommences deliveries\b", r"\blaunch(es|ed)?\b", r"\bunveil", r"\bintroduc\w+ (new|its)\b")),
    EventType("technology_development", "business", 0.45, (r"\bpatent\b", r"\btechnology\b.*\b(develop|breakthrough)", r"\bR&D\b")),
    EventType("certification", "business", 0.35, (r"\bcertif\w+\b", r"\baccredit", r"\bapproval\b.*\b(FDA|USFDA|DCGI|EMA|ARAI|homolog)")),
    EventType("acquisition", "corporate", 0.85, (r"\bacquir\w+\b", r"\bacquisition\b", r"\btakeover\b", r"\bbuys?\b.*\bstake\b")),
    EventType("divestiture", "corporate", 0.75, (r"\bdivest", r"\bsells?\b.*\b(unit|business|division|stake)\b", r"\bhive[- ]off\b", r"\bdemerg")),
    EventType("partnership", "business", 0.5, (r"\bjoins? hands\b", r"\bstrategic (partnership|alliance)\b", r"\bpartner(s|ship|ed)?\b", r"\bmou\b", r"\bmemorandum of understanding\b", r"\bcollaborat", r"\btie[- ]up\b")),
    EventType("joint_venture", "corporate", 0.6, (r"\bjoint venture\b", r"\bJV\b")),
    EventType("fundraising", "capital", 0.7, (r"\b(promoter|capital) infusion\b", r"\bconversion of warrants\b", r"\bfund ?rais", r"\bqip\b", r"\brights issue\b", r"\bpreferential (issue|allotment)\b", r"\bwarrants\b", r"\bIPO\b", r"\bplacement\b", r"\braises?\b.*\b(crore|million|billion)\b")),
    EventType("debt", "capital", 0.55, (r"\b(borrowing|loan|debentures?|ncds?|bonds?|credit facility|notes offering)\b",)),
    EventType("debt_reduction", "capital", 0.55, (r"\bdebt[- ]free\b", r"\b(repay|prepay|reduc)\w*\b.*\bdebt\b"), "positive"),
    EventType("credit_rating", "capital", 0.5, (r"\bcredit rating\b", r"\b(CRISIL|ICRA|CARE|India Ratings|Moody's|S&P|Fitch)\b.*\b(rating|upgrade|downgrade|reaffirm)")),
    EventType("dividend", "capital", 0.45, (r"\bdividend\b",)),
    EventType("buyback", "capital", 0.6, (r"\bbuy[- ]?back\b", r"\bshare repurchase\b")),
    EventType("corporate_action", "capital", 0.4, (r"\bstock split\b", r"\bsub-division\b", r"\bbonus (issue|shares)\b", r"\brecord date\b")),
    EventType("management_change", "governance", 0.6, (r"\b(appoint|resign|step(s|ped)? down|retire|re-?appoint)\w*\b", r"\bnew (ceo|cfo|md|chairman|director)\b", r"\bdeparture of\b")),
    EventType("board_meeting", "governance", 0.3, (r"\bboard meeting\b", r"\boutcome of board\b", r"\bAGM\b", r"\bEGM\b", r"\bannual general meeting\b", r"\bpostal ballot\b")),
    EventType("promoter_transaction", "ownership", 0.55, (r"\bpromoters?\b.*\b(stake|pledge|sell|buy|acquir|increas|decreas)", r"\bpledge\b")),
    EventType("insider_transaction", "ownership", 0.4, (r"\binsider\b", r"\bform 4\b", r"\bSAST\b", r"\bdirector\b.*\b(buys|sells|dealing)\b", r"\bPDMR\b")),
    EventType("shareholding_change", "ownership", 0.4, (r"\bshareholding pattern\b", r"\b(FII|DII|FPI|mutual fund)s?\b.*\bstake\b", r"\bblock deal\b", r"\bbulk deal\b")),
    EventType("regulatory_action", "regulatory", 0.8, (r"\b(penalty|fine|show[- ]cause|sebi order|sec charges|warning letter|import alert|sanction|probe|investigation|raid)\b",), "negative"),
    EventType("litigation", "regulatory", 0.65, (r"\b(lawsuit|litigation|sued|court|tribunal|NCLT|arbitration|class action)\b",), "negative"),
    EventType("export_expansion", "business", 0.5, (r"\bexport\w*\b.*\b(order|market|expan|growth)",)),
    EventType("geographic_expansion", "business", 0.5, (r"\b(showroom|dealership|nationwide expansion)\b", r"\b(enters?|expand\w*)\b.*\b(market|country|region|overseas)\b",)),
    EventType("hiring", "business", 0.25, (r"\bhir(e|es|ing)\b", r"\blayoffs?\b", r"\bjob cuts\b")),
    EventType("stock_movement", "market", 0.35, (r"\bshares?\b.*\b(surge|jump|rall|soar|plunge|slump|tank|hit upper circuit|hit lower circuit|52-week)", r"\bupper circuit\b", r"\blower circuit\b", r"\bstock (rises|falls|gains|drops)\b")),
    EventType("competitor_development", "industry", 0.3, ()),
    EventType("industry_development", "industry", 0.3, ()),
    EventType("other", "other", 0.2, ()),
]

BY_KEY = {t.key: t for t in TAXONOMY}
EVENT_TYPE_KEYS = [t.key for t in TAXONOMY]
_COMPILED = [(t, [re.compile(p, re.I) for p in t.patterns]) for t in TAXONOMY if t.patterns]

# SEC 8-K item codes -> event types (https://www.sec.gov/fast-answers/answersform8khtm.html)
SEC_8K_ITEMS = {
    "1.01": "contract", "1.02": "contract", "1.03": "litigation", "1.05": "regulatory_action",
    "2.01": "acquisition", "2.02": "earnings", "2.03": "debt", "2.04": "debt", "2.05": "divestiture",
    "2.06": "earnings", "3.01": "regulatory_action", "3.02": "fundraising", "3.03": "corporate_action",
    "4.01": "management_change", "4.02": "earnings", "5.01": "management_change", "5.02": "management_change",
    "5.03": "corporate_action", "5.07": "board_meeting", "7.01": "other", "8.01": "other",
}
SEC_FORMS = {
    "10-K": ("earnings", "Annual report (10-K) filed"),
    "10-Q": ("earnings", "Quarterly report (10-Q) filed"),
    "20-F": ("earnings", "Annual report (20-F) filed"),
    "6-K": ("other", "Foreign issuer report (6-K) filed"),
    "4": ("insider_transaction", "Insider transaction report (Form 4)"),
    "SC 13D": ("shareholding_change", "Beneficial ownership report (13D)"),
    "SC 13G": ("shareholding_change", "Beneficial ownership report (13G)"),
    "DEF 14A": ("board_meeting", "Proxy statement filed"),
    "S-1": ("fundraising", "Registration statement (S-1)"),
    "S-3": ("fundraising", "Shelf registration (S-3)"),
    "424B5": ("fundraising", "Prospectus supplement (424B5)"),
}

NEGATIVE_WORDS = re.compile(r"\b(loss|losses|decline|declines|falls?|drop|drops|plunge|cut|cuts|lower|weak|delay|delayed|miss|missed|resign|penalty|fine|downgrade|default|slump|shortfall|halt|suspend|lower circuit)\b", re.I)
POSITIVE_WORDS = re.compile(r"\b(wins?|won|bags?|secures?|record|growth|grows|rises?|jumps?|surges?|beats?|upgrade|expands?|expansion|launch|profit up|turnaround|debt[- ]free|upper circuit|partnership|joins hands|commences deliveries|inaugurates|strong bookings|infusion)\b", re.I)


def classify_title(text: str) -> tuple[str, float]:
    """Return (event_type, confidence) from pattern matches. Picks the highest-base matching type."""
    hits = []
    for t, pats in _COMPILED:
        n = sum(1 for p in pats if p.search(text))
        if n:
            hits.append((t.base + 0.05 * n, t))
    if not hits:
        return "other", 0.3
    hits.sort(key=lambda x: x[0], reverse=True)
    return hits[0][1].key, min(0.9, 0.55 + 0.1 * len(hits[:1]) + 0.05 * (len(hits) == 1))


def direction(text: str, event_type: str) -> str:
    pos = len(POSITIVE_WORDS.findall(text))
    neg = len(NEGATIVE_WORDS.findall(text))
    if pos and neg:
        return "mixed"
    if pos:
        return "positive"
    if neg:
        return "negative"
    return BY_KEY.get(event_type, BY_KEY["other"]).direction
