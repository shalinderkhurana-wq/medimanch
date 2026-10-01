import streamlit as st
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET
import re
import json

st.set_page_config(
    page_title="Medimanch Live Signal Radar",
    page_icon="🔥",
    layout="wide"
)

try:
    from medimanch_keywords import RADAR_DICTIONARY
except Exception:
    RADAR_DICTIONARY = {
        "behaviour": ["challenge", "try", "hack", "routine", "test", "exercise", "stretch", "walk", "breathe"],
        "actions": ["rub", "press", "tap", "hold", "soak", "boil", "mix", "drink", "apply", "wrap"],
        "objects": ["water", "ice", "salt", "lemon", "ginger", "garlic", "honey", "oil", "herb", "spice"],
        "effects": ["sleep", "energy", "digestion", "bloating", "pain", "flexibility", "balance", "memory", "focus", "weight"],
        "traditional": ["ayurveda", "ayurvedic", "naturopathy", "yoga", "pranayama", "home remedy", "gharelu nuskha"]
    }

def all_terms():
    terms = []
    for values in RADAR_DICTIONARY.values():
        terms.extend(values)
    return sorted(set(x.lower() for x in terms if x.strip()))

TERMS = all_terms()

def fetch_url(url, timeout=15):
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 Medimanch-Live-Radar/1.0"})
    with urlopen(request, timeout=timeout) as response:
        return response.read()

def parse_rss(xml_bytes):
    root = ET.fromstring(xml_bytes)
    rows = []
    for item in root.findall(".//item"):
        def text(tag):
            node = item.find(tag)
            return (node.text or "").strip() if node is not None else ""
        rows.append({
            "title": text("title"),
            "link": text("link"),
            "description": re.sub(r"<[^>]+>", " ", text("description")).strip(),
            "published": text("pubDate")
        })
    return rows

def keyword_hits(text):
    text = text.lower()
    return sorted(
        set(term for term in TERMS if term in text),
        key=lambda x: (-len(x), x)
    )[:15]

def classify(text):
    t = text.lower()
    hits = keyword_hits(t)
    behaviour = any(x in t for x in RADAR_DICTIONARY.get("behaviour", []))
    action = any(x in t for x in RADAR_DICTIONARY.get("actions", []))
    effect = any(x in t for x in RADAR_DICTIONARY.get("effects", []))
    traditional = any(x in t for x in RADAR_DICTIONARY.get("traditional", []))
    visual = behaviour or action

    if visual and effect:
        kind = "BEHAVIOUR × EFFECT"
    elif traditional and (visual or effect):
        kind = "TRADITIONAL / WELLNESS"
    elif visual:
        kind = "VISIBLE BEHAVIOUR"
    elif effect:
        kind = "HEALTH / EFFECT"
    else:
        kind = "RELATED"

    return kind, hits, visual

def opportunity_score(item):
    # This is a discovery score, NOT a scientific validity score.
    score = 30
    score += min(30, len(item["keyword_hits"]) * 5)
    score += 20 if item["visual"] else 0
    score += item.get("source_bonus", 0)
    return min(100, score)

@st.cache_data(ttl=600, show_spinner=False)
def get_google_trends():
    # Google Trends Trending Now RSS export for India.
    url = "https://trends.google.com/trending/rss?geo=IN"
    try:
        rows = parse_rss(fetch_url(url))
        results = []
        for row in rows:
            combined = row["title"] + " " + row["description"]
            kind, hits, visual = classify(combined)
            if not hits:
                continue
            item = {
                "source": "Google Trends India",
                "title": row["title"],
                "link": row["link"],
                "published": row["published"],
                "description": row["description"],
                "kind": kind,
                "keyword_hits": hits,
                "visual": visual,
                "source_bonus": 15
            }
            item["score"] = opportunity_score(item)
            results.append(item)
        return results, ""
    except Exception as exc:
        return [], str(exc)

@st.cache_data(ttl=900, show_spinner=False)
def get_pubmed():
    # Public NCBI E-utilities; no API key is required for this basic use.
    selected = TERMS[:80]
    query = " OR ".join('"' + x + '"' for x in selected)
    search_url = (
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        "?db=pubmed&retmode=json&retmax=30&sort=date&term=" + quote_plus(query)
    )
    try:
        data = json.loads(fetch_url(search_url))
        ids = data.get("esearchresult", {}).get("idlist", [])
        if not ids:
            return [], ""
        summary_url = (
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
            "?db=pubmed&retmode=json&id=" + ",".join(ids)
        )
        summary = json.loads(fetch_url(summary_url)).get("result", {})
        results = []
        for pid in ids:
            rec = summary.get(pid, {})
            title = (rec.get("title") or "").strip()
            if not title:
                continue
            kind, hits, visual = classify(title)
            item = {
                "source": "PubMed",
                "title": title,
                "link": "https://pubmed.ncbi.nlm.nih.gov/" + pid + "/",
                "published": rec.get("pubdate", ""),
                "description": rec.get("source", ""),
                "kind": kind,
                "keyword_hits": hits,
                "visual": visual,
                "source_bonus": 5
            }
            item["score"] = opportunity_score(item)
            results.append(item)
        return results, ""
    except Exception as exc:
        return [], str(exc)

def live_scan():
    trends, trends_error = get_google_trends()
    research, research_error = get_pubmed()
    combined = trends + research

    seen = set()
    clean = []
    for item in combined:
        key = re.sub(r"\W+", " ", item["title"].lower()).strip()
        if key in seen:
            continue
        seen.add(key)
        clean.append(item)

    clean.sort(key=lambda x: x["score"], reverse=True)
    errors = [x for x in [trends_error, research_error] if x]
    return clean, errors

if "signals" not in st.session_state:
    st.session_state.signals = []
if "errors" not in st.session_state:
    st.session_state.errors = []

st.title("🔥 MEDIMANCH LIVE SIGNAL & OPPORTUNITY RADAR")
st.caption("Internet behaviour × research × visual opportunity")

c1, c2, c3, c4 = st.columns(4)

if st.button("🔥 RUN LIVE SCAN", type="primary"):
    with st.spinner("Reading live feeds and matching Medimanch signals..."):
        st.session_state.signals, st.session_state.errors = live_scan()

signals = st.session_state.signals

with c1:
    st.metric("LIVE SIGNAL MATCHES", sum(x["source"] == "Google Trends India" for x in signals))
with c2:
    st.metric("RESEARCH MATCHES", sum(x["source"] == "PubMed" for x in signals))
with c3:
    st.metric("VISUAL SIGNALS", sum(x["visual"] for x in signals))
with c4:
    st.metric("SHOOT-WORTHY", sum(x["score"] >= 70 for x in signals))

st.divider()

st.subheader("🔥 ACTIVE / SHOOT-WORTHY SIGNALS")

if not signals:
    st.info("Click RUN LIVE SCAN. The bot will read the configured feeds and match them against the editable Medimanch dictionary.")
else:
    for item in signals[:30]:
        label = "🔥 SHOOT-WORTHY" if item["score"] >= 70 else "👀 WATCH"
        st.markdown("### " + label + " — " + item["title"])
        st.write(
            "**Source:** " + item["source"] +
            "  |  **Type:** " + item["kind"] +
            "  |  **Discovery score:** " + str(item["score"]) + "/100"
        )
        if item["keyword_hits"]:
            st.write("**Matched Medimanch signals:** " + " · ".join(item["keyword_hits"]))
        if item["description"]:
            st.caption(item["description"][:500])
        if item["link"]:
            st.markdown("[Open source](" + item["link"] + ")")
        st.divider()

st.subheader("📡 ENGINE STATUS")
st.success("ONLINE — live ingestion is enabled for the configured sources.")
st.write("Last dashboard run:", datetime.now().strftime("%d %B %Y, %I:%M:%S"))

if st.session_state.errors:
    st.warning("One or more feeds returned an error. Other feeds can still work.")
    for error in st.session_state.errors:
        st.code(error)

with st.expander("🧠 MEDIMANCH RADAR DICTIONARY — editable"):
    for category, values in RADAR_DICTIONARY.items():
        st.write("**" + category.upper() + "**")
        st.write(", ".join(values))

st.caption(
    "IMPORTANT: an active/viral signal is displayed because it is active. "
    "Authenticity, evidence strength and safety are separate verification gates."
)
