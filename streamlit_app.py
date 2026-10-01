import streamlit as st
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET
import re
import json
import math

st.set_page_config(page_title="Medimanch Viral Behaviour Radar", page_icon="🔥", layout="wide")

# ============================================================
# MEDIMANCH V2 — DISCOVERY -> BEHAVIOUR -> VIDEO OPPORTUNITY
# ============================================================

try:
    from medimanch_keywords import RADAR_DICTIONARY
except Exception:
    RADAR_DICTIONARY = {}

def terms(category):
    return [x.lower() for x in RADAR_DICTIONARY.get(category, [])]

def fetch_url(url, timeout=15):
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 MedimanchRadar/2.0"})
    with urlopen(req, timeout=timeout) as r:
        return r.read()

def parse_rss(xml_bytes):
    root = ET.fromstring(xml_bytes)
    out = []
    for item in root.findall(".//item"):
        def get(tag):
            n = item.find(tag)
            return (n.text or "").strip() if n is not None else ""
        out.append({
            "title": get("title"),
            "link": get("link"),
            "description": re.sub(r"<[^>]+>", " ", get("description")).strip(),
            "published": get("pubDate")
        })
    return out

def matched_terms(text):
    t = text.lower()
    matches = {}
    for category, values in RADAR_DICTIONARY.items():
        hits = [v for v in values if v.lower() in t]
        if hits:
            matches[category] = sorted(set(hits), key=lambda x: (-len(x), x))[:12]
    return matches

def extract_signals(text):
    m = matched_terms(text)
    flat = [x for xs in m.values() for x in xs]
    return m, flat

def score_signal(item):
    m = item["matches"]
    # Discovery score: intentionally rewards action + visual + claim/question structure.
    s = 0
    s += min(20, len(m.get("behaviour", [])) * 5)
    s += min(18, len(m.get("actions", [])) * 4)
    s += min(12, len(m.get("visual", [])) * 4)
    s += min(12, len(m.get("naturopathy", [])) * 4)
    s += min(10, len(m.get("diet_food", [])) * 3)
    s += min(10, len(m.get("self_test", [])) * 3)
    s += min(8, len(m.get("myth_claim", [])) * 2)
    s += min(5, len(m.get("ayurveda", [])) * 2)
    if item.get("source") == "Google Trends India":
        s += 10
    if item.get("source") == "PubMed":
        s += 3
    return min(100, s)

def classify(item):
    m = item["matches"]
    action = bool(m.get("actions") or m.get("behaviour"))
    visual = bool(m.get("visual") or m.get("self_test") or m.get("actions"))
    claim = bool(m.get("myth_claim") or m.get("effects"))
    if action and claim and visual:
        return "🔥 BEHAVIOUR + CLAIM + VISUAL"
    if action and visual:
        return "🎥 ACTIONABLE / VISUAL BEHAVIOUR"
    if m.get("naturopathy") or m.get("ayurveda"):
        return "🌿 TRADITIONAL / NATUROPATHY"
    if m.get("diet_food"):
        return "🍽️ FOOD / DIET BEHAVIOUR"
    if m.get("self_test"):
        return "🧪 SELF-TEST / CHALLENGE"
    if claim:
        return "⚠️ CLAIM / MYTH-FULL CHECK"
    return "RELATED SIGNAL"

def videoability(item):
    m = item["matches"]
    score = 0
    if m.get("actions"): score += 20
    if m.get("visual"): score += 20
    if m.get("self_test"): score += 15
    if m.get("behaviour"): score += 10
    if m.get("naturopathy") or m.get("ayurveda"): score += 10
    if m.get("diet_food"): score += 10
    if m.get("myth_claim"): score += 10
    if m.get("effects"): score += 5
    return min(100, score)

def build_video_angle(item):
    title = item["title"]
    m = item["matches"]
    parts = []
    if m.get("actions"):
        parts.append("visible action: " + ", ".join(m["actions"][:3]))
    if m.get("myth_claim"):
        parts.append("claim-check: " + ", ".join(m["myth_claim"][:3]))
    if m.get("effects"):
        parts.append("effect/question: " + ", ".join(m["effects"][:3]))
    if m.get("self_test"):
        parts.append("self-test/challenge possible")
    if m.get("naturopathy"):
        parts.append("naturopathy/home-practice context")
    if m.get("diet_food"):
        parts.append("food/preparation behaviour")
    angle = " · ".join(parts)
    return (
        "Don't simply repeat the claim. Turn the visible behaviour into a "
        "demonstration/question and verify the claimed effect. " + angle
    )

@st.cache_data(ttl=600, show_spinner=False)
def google_trends():
    try:
        rows = parse_rss(fetch_url("https://trends.google.com/trending/rss?geo=IN"))
        out = []
        for row in rows:
            text = row["title"] + " " + row["description"]
            m, flat = extract_signals(text)
            if not flat:
                continue
            item = {
                "source": "Google Trends India",
                "title": row["title"],
                "link": row["link"],
                "published": row["published"],
                "description": row["description"],
                "matches": m,
            }
            item["type"] = classify(item)
            item["videoability"] = videoability(item)
            item["score"] = min(100, score_signal(item) + round(item["videoability"] * .25))
            item["video_angle"] = build_video_angle(item)
            out.append(item)
        return out, ""
    except Exception as e:
        return [], str(e)

@st.cache_data(ttl=900, show_spinner=False)
def pubmed():
    # Research is supporting evidence, not proof that a trending behaviour works.
    all_terms = []
    for category in ["behaviour", "actions", "naturopathy", "diet_food", "ayurveda", "effects", "self_test"]:
        all_terms += terms(category)
    all_terms = sorted(set(all_terms), key=len, reverse=True)[:70]
    query = " OR ".join('"' + x + '"' for x in all_terms)
    url = (
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        "?db=pubmed&retmode=json&retmax=40&sort=date&term=" + quote_plus(query)
    )
    try:
        data = json.loads(fetch_url(url))
        ids = data.get("esearchresult", {}).get("idlist", [])
        if not ids:
            return [], ""
        su = (
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
            "?db=pubmed&retmode=json&id=" + ",".join(ids)
        )
        summary = json.loads(fetch_url(su)).get("result", {})
        out = []
        for pid in ids:
            rec = summary.get(pid, {})
            title = (rec.get("title") or "").strip()
            if not title:
                continue
            m, flat = extract_signals(title)
            item = {
                "source": "PubMed",
                "title": title,
                "link": "https://pubmed.ncbi.nlm.nih.gov/" + pid + "/",
                "published": rec.get("pubdate", ""),
                "description": rec.get("source", ""),
                "matches": m,
            }
            item["type"] = classify(item)
            item["videoability"] = videoability(item)
            item["score"] = min(100, score_signal(item) + round(item["videoability"] * .10))
            item["video_angle"] = build_video_angle(item)
            out.append(item)
        return out, ""
    except Exception as e:
        return [], str(e)

def scan():
    a, e1 = google_trends()
    b, e2 = pubmed()
    all_rows = a + b
    seen = set()
    clean = []
    for x in all_rows:
        key = re.sub(r"\W+", " ", x["title"].lower()).strip()
        if key in seen:
            continue
        seen.add(key)
        clean.append(x)
    clean.sort(key=lambda x: (x["score"], x["videoability"]), reverse=True)
    return clean, [e for e in [e1, e2] if e]

if "signals" not in st.session_state:
    st.session_state.signals = []
if "errors" not in st.session_state:
    st.session_state.errors = []

st.title("🔥 MEDIMANCH VIRAL BEHAVIOUR & VIDEO RADAR")
st.caption("Live signal → behaviour → claim → visual opportunity → research")

if st.button("🔥 RUN LIVE SCAN", type="primary"):
    with st.spinner("Scanning live feeds and extracting Medimanch-style opportunities..."):
        st.session_state.signals, st.session_state.errors = scan()

signals = st.session_state.signals

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("ACTIVE MATCHES", len(signals))
c2.metric("VISUAL", sum(x["videoability"] >= 50 for x in signals))
c3.metric("BEHAVIOUR", sum("BEHAVIOUR" in x["type"] for x in signals))
c4.metric("CLAIM / MYTH", sum("CLAIM" in x["type"] for x in signals))
c5.metric("SHOOT CANDIDATES", sum(x["score"] >= 65 for x in signals))

st.divider()

tab1, tab2, tab3 = st.tabs(["🔥 SHOOT CANDIDATES", "🌐 ALL ACTIVE SIGNALS", "🧠 ENGINE LOGIC"])

with tab1:
    candidates = [x for x in signals if x["score"] >= 65]
    if not candidates:
        st.info("No strong candidate matched this scan. That is acceptable: the bot should not manufacture topics.")
    for x in candidates[:15]:
        st.markdown("## 🔥 " + x["title"])
        st.write("**Signal type:**", x["type"])
        st.write("**Source:**", x["source"], "| **Discovery score:**", str(x["score"]) + "/100", "| **Videoability:**", str(x["videoability"]) + "/100")
        if x["matches"]:
            for cat, hits in x["matches"].items():
                st.write("**" + cat.replace("_", " ").title() + ":**", " · ".join(hits))
        st.info("🎬 Medimanch angle: " + x["video_angle"])
        if x["link"]:
            st.markdown("[Open source →](" + x["link"] + ")")
        st.divider()

with tab2:
    for x in signals[:40]:
        with st.expander(x["title"] + " — " + x["type"] + " — " + str(x["score"]) + "/100"):
            st.write("Source:", x["source"])
            st.write("Matched:", x["matches"])
            st.write("Videoability:", x["videoability"])
            st.write(x["video_angle"])
            st.markdown("[Open source →](" + x["link"] + ")")

with tab3:
    st.write("""
The current engine deliberately separates DISCOVERY from VERIFICATION.

1. Capture an active signal.
2. Match it against Medimanch behaviour/action/food/naturopathy/self-test/claim vocabulary.
3. Estimate whether the signal can become visible video.
4. Rank it as a discovery opportunity.
5. Research/evidence is supporting context — not automatic proof.
6. A later verification layer will check authenticity, evidence, safety and originality before a final SHOOT decision.
""")
    st.write("Dictionary categories currently loaded:")
    for k, v in RADAR_DICTIONARY.items():
        st.write("**" + k.upper() + "**:", len(v), "signals")

if st.session_state.errors:
    st.warning("Some feeds could not be read during this scan.")
    for e in st.session_state.errors:
        st.code(e)

st.divider()
st.success("🟢 Engine ONLINE")
st.write("Last dashboard run:", datetime.now().strftime("%d %B %Y, %I:%M:%S"))

with st.expander("⚙️ View current Medimanch signal dictionary"):
    for k, v in RADAR_DICTIONARY.items():
        st.write("### " + k.upper())
        st.write(", ".join(v))

st.caption("Discovery score is not a truth score. Viral/active signals require separate authenticity, evidence and safety verification.")
