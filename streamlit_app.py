import streamlit as st
from datetime import datetime, timedelta, timezone
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import json
import re
import time

from medimanch_keywords import (
    CONCEPTS,
    YOUTUBE_QUERY_MAP,
    NEWS_QUERY_MAP,
    RESEARCH_QUERY_PACKS,
    JOURNAL_FOCUS,
)

# ============================================================
# MEDIMANCH INTELLIGENCE RADAR V6
#
# Core rule:
# INTERNET BEHAVIOUR / VISUAL SIGNAL
#       -> OPPORTUNITY
#       -> RESEARCH EXPLANATION
#
# Research alone can NEVER create a Shoot Now opportunity.
# ============================================================

st.set_page_config(
    page_title="Medimanch Intelligence Radar V6",
    page_icon="🔥",
    layout="wide",
)

# ---------- Session memory ----------
DEFAULTS = {
    "current_scan": None,
    "previous_scan": None,
    "scan_number": 0,
    "scan_history": [],
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v


# ---------- Utilities ----------
def now_utc():
    return datetime.now(timezone.utc)


def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def norm(value):
    return re.sub(r"[^a-z0-9\u0900-\u097f]+", " ", clean(value).lower())


def parse_date(value):
    if not value:
        return None
    value = clean(value)
    for candidate in (value, value.replace("Z", "+00:00")):
        try:
            dt = datetime.fromisoformat(candidate)
            return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M %z"):
        try:
            return datetime.strptime(value, fmt).astimezone(timezone.utc)
        except Exception:
            pass
    return None


def fetch_bytes(url, timeout=18):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Medimanch-Intelligence-Radar/6.0",
            "Accept": "application/json, application/xml, application/rss+xml, text/xml, */*",
            "Cache-Control": "no-cache",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_json(url):
    return json.loads(fetch_bytes(url).decode("utf-8"))


def unique_by_id(items):
    output = {}
    for item in items:
        if item.get("id") not in output:
            output[item["id"]] = item
    return list(output.values())


def age_hours(dt):
    if not dt:
        return 9999
    return max(0.1, (now_utc() - dt).total_seconds() / 3600)


def freshness_score(dt):
    h = age_hours(dt)
    if h <= 6:
        return 10
    if h <= 24:
        return 9
    if h <= 48:
        return 8
    if h <= 72:
        return 7
    if h <= 168:
        return 5
    return 2


# ---------- Visual / behaviour intelligence ----------
def visual_score(item):
    text = norm(item.get("title", "") + " " + item.get("description", ""))
    concept = CONCEPTS.get(item.get("concept", ""), {})
    visual = 0
    reasons = []

    # These are actual observable actions/objects, not generic paper vocabulary.
    action_terms = concept.get("visual_actions", [])
    object_terms = concept.get("visual_objects", [])
    test_terms = concept.get("visual_tests", [])

    if any(norm(x) in text for x in action_terms):
        visual += 4
        reasons.append("physical action")
    if any(norm(x) in text for x in object_terms):
        visual += 2
        reasons.append("visible object/preparation")
    if any(norm(x) in text for x in test_terms):
        visual += 3
        reasons.append("test/comparison")
    if item.get("source") == "YouTube":
        visual += 1
        reasons.append("video signal")
    return min(10, visual), reasons


def behaviour_score(item):
    text = norm(item.get("title", "") + " " + item.get("description", ""))
    concept = CONCEPTS.get(item.get("concept", ""), {})
    terms = concept.get("behaviour_terms", [])
    hits = [x for x in terms if norm(x) in text]
    score = min(10, len(set(hits)) * 2)
    if item.get("source") in ("YouTube", "Google Trends"):
        score = min(10, score + 2)
    return score, hits[:8]


def audience_signal_score(signals):
    sources = {x["source"] for x in signals}
    score = 0
    if "YouTube" in sources:
        score += 4
    if "Google Trends" in sources:
        score += 4
    if "Google News" in sources:
        score += 2
    # Multiple independent families matter more than duplicate records.
    if len(sources) >= 2:
        score += 2
    if len(sources) >= 3:
        score += 2
    return min(10, score)


def research_quality(item):
    title = norm(item.get("title", ""))
    abstract = norm(item.get("description", ""))
    text = title + " " + abstract

    animal = any(x in text for x in [
        "mouse", "mice", "rat", "rats", "murine", "animal model",
        "zebrafish", "in vitro", "cell culture"
    ])
    disease_heavy = any(x in text for x in [
        "carcinoma", "tumor", "tumour", "metastasis", "chemotherapy",
        "colitis associated colorectal cancer", "cancer treatment"
    ])
    human = any(x in text for x in [
        "human", "humans", "adult", "adults", "participant",
        "participants", "randomized", "randomised", "trial", "clinical"
    ])
    relevant = any(x in text for x in [
        "sleep", "walking", "exercise", "diet", "food", "meal",
        "fasting", "breathing", "hydration", "water", "light",
        "temperature", "stress", "relaxation", "posture", "balance",
        "mobility", "proprioception", "fermented", "herbal", "sunlight"
    ])

    score = 4
    if human:
        score += 3
    if relevant:
        score += 2
    if animal:
        score -= 4
    if disease_heavy:
        score -= 4

    score = max(0, min(10, score))

    if animal or disease_heavy:
        role = "RESEARCH BANK"
    elif score >= 7:
        role = "EXPLANATION CANDIDATE"
    else:
        role = "BACKGROUND"

    return score, role, {
        "human": human,
        "animal": animal,
        "disease_heavy": disease_heavy,
    }


# ---------- Feed: YouTube ----------
def youtube_scan(api_key):
    if not api_key:
        return [], "YouTube API is not connected."

    records = []
    errors = []

    # Each query is mapped to a specific human behaviour concept.
    # This prevents generic keyword matching from inventing a concept.
    published_after = (now_utc() - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")

    for query, concept in YOUTUBE_QUERY_MAP:
        params = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "order": "viewCount",
            "publishedAfter": published_after,
            "regionCode": "IN",
            "relevanceLanguage": "hi",
            "maxResults": "8",
            "key": api_key,
        }
        url = "https://www.googleapis.com/youtube/v3/search?" + urllib.parse.urlencode(params)

        try:
            data = fetch_json(url)
            for result in data.get("items", []):
                video_id = result.get("id", {}).get("videoId")
                snippet = result.get("snippet", {})
                if not video_id:
                    continue

                records.append({
                    "id": "yt:" + video_id,
                    "source": "YouTube",
                    "kind": "VISUAL_BEHAVIOUR",
                    "concept": concept,
                    "title": clean(snippet.get("title")),
                    "description": clean(snippet.get("description"))[:900],
                    "channel": clean(snippet.get("channelTitle")),
                    "published_dt": parse_date(snippet.get("publishedAt")) or now_utc(),
                    "url": "https://www.youtube.com/watch?v=" + video_id,
                    "thumbnail": snippet.get("thumbnails", {}).get("medium", {}).get("url", ""),
                })
        except Exception as exc:
            errors.append(f"{query}: {exc}")

    records = unique_by_id(records)

    # One videos.list call can retrieve stats for up to 50 video IDs.
    for start in range(0, len(records), 50):
        batch = records[start:start + 50]
        ids = ",".join(x["id"].split(":", 1)[1] for x in batch)
        params = {
            "part": "statistics",
            "id": ids,
            "key": api_key,
        }
        url = "https://www.googleapis.com/youtube/v3/videos?" + urllib.parse.urlencode(params)

        try:
            data = fetch_json(url)
            stats = {x["id"]: x.get("statistics", {}) for x in data.get("items", [])}
            for item in batch:
                stats_row = stats.get(item["id"].split(":", 1)[1], {})
                item["views"] = int(stats_row.get("viewCount", 0) or 0)
                item["likes"] = int(stats_row.get("likeCount", 0) or 0)
                item["comments"] = int(stats_row.get("commentCount", 0) or 0)

                hours = age_hours(item["published_dt"])
                item["views_per_day"] = int(item["views"] / max(hours / 24, 0.25))
        except Exception as exc:
            errors.append("YouTube statistics: " + str(exc))

    return records, ("YouTube: " + errors[0] if errors else None)


# ---------- Feed: Google Trends ----------
def trends_scan():
    url = (
        "https://trends.google.com/trending/rss"
        "?geo=IN&_fresh=" + str(int(time.time()))
    )

    try:
        root = ET.fromstring(fetch_bytes(url))
        records = []

        for node in root.findall(".//item"):
            title = clean(node.findtext("title"))

            # Exact concept routing: only a known concept can enter the opportunity engine.
            matched = []
            title_norm = norm(title)
            for concept, data in CONCEPTS.items():
                if any(norm(alias) in title_norm for alias in data["aliases"]):
                    matched.append(concept)

            for concept in matched[:1]:
                records.append({
                    "id": "trend:" + norm(title),
                    "source": "Google Trends",
                    "kind": "SEARCH_SIGNAL",
                    "concept": concept,
                    "title": title,
                    "description": "Rising search signal in India.",
                    "published_dt": parse_date(node.findtext("pubDate")) or now_utc(),
                    "url": clean(node.findtext("link")),
                })

        return unique_by_id(records), None

    except Exception as exc:
        return [], "Google Trends: " + str(exc)


# ---------- Feed: Google News ----------
def news_scan():
    records = []
    errors = []

    for query, concept in NEWS_QUERY_MAP:
        params = {
            "q": query,
            "hl": "en-IN",
            "gl": "IN",
            "ceid": "IN:en",
            "_fresh": str(int(time.time())),
        }
        url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(params)

        try:
            root = ET.fromstring(fetch_bytes(url))
            for node in root.findall(".//item"):
                title = clean(node.findtext("title"))
                desc = clean(re.sub("<[^>]+>", " ", node.findtext("description") or ""))

                records.append({
                    "id": "news:" + norm(title),
                    "source": "Google News",
                    "kind": "WEB_SIGNAL",
                    "concept": concept,
                    "title": title,
                    "description": desc[:900],
                    "published_dt": parse_date(node.findtext("pubDate")) or now_utc(),
                    "url": clean(node.findtext("link")),
                })
        except Exception as exc:
            errors.append(f"{query}: {exc}")

    return unique_by_id(records), ("Google News: " + errors[0] if errors else None)


# ---------- Feed: PubMed ----------
def pubmed_scan():
    records = []
    errors = []

    since = (now_utc() - timedelta(days=45)).strftime("%Y/%m/%d")
    until = now_utc().strftime("%Y/%m/%d")

    for concept, query in RESEARCH_QUERY_PACKS.items():
        params = {
            "db": "pubmed",
            "term": f"({query}) AND ({since}[Date - Publication] : {until}[Date - Publication])",
            "retmode": "json",
            "retmax": "5",
            "sort": "pub date",
        }
        search_url = (
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?"
            + urllib.parse.urlencode(params)
        )

        try:
            data = fetch_json(search_url)
            ids = data.get("esearchresult", {}).get("idlist", [])
            if not ids:
                continue

            fetch_url = (
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?"
                + urllib.parse.urlencode({
                    "db": "pubmed",
                    "id": ",".join(ids),
                    "retmode": "xml",
                })
            )

            root = ET.fromstring(fetch_bytes(fetch_url))

            for article in root.findall(".//PubmedArticle"):
                title_node = article.find(".//ArticleTitle")
                title = clean("".join(title_node.itertext())) if title_node is not None else ""
                pmid = clean(article.findtext(".//PMID"))
                journal = clean(article.findtext(".//Journal/Title"))
                abstract = " ".join(
                    clean("".join(node.itertext()))
                    for node in article.findall(".//AbstractText")
                )

                records.append({
                    "id": "pmid:" + pmid,
                    "source": "PubMed",
                    "kind": "RESEARCH",
                    "concept": concept,
                    "title": title,
                    "description": abstract[:1400],
                    "journal": journal,
                    "published_dt": now_utc(),
                    "url": "https://pubmed.ncbi.nlm.nih.gov/" + pmid + "/",
                })

        except Exception as exc:
            errors.append(f"{concept}: {exc}")

    return unique_by_id(records), ("PubMed: " + errors[0] if errors else None)


# ---------- Feed: authoritative journals ----------
def journal_scan():
    records = []
    errors = []

    journal_query = " OR ".join(
        f'"{journal}"[Journal]' for journal in JOURNAL_FOCUS
    )

    params = {
        "db": "pubmed",
        "term": f"({journal_query}) AND 2026[dp]",
        "retmode": "json",
        "retmax": "40",
        "sort": "pub date",
    }

    search_url = (
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?"
        + urllib.parse.urlencode(params)
    )

    try:
        data = fetch_json(search_url)
        ids = data.get("esearchresult", {}).get("idlist", [])

        if ids:
            fetch_url = (
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?"
                + urllib.parse.urlencode({
                    "db": "pubmed",
                    "id": ",".join(ids),
                    "retmode": "xml",
                })
            )
            root = ET.fromstring(fetch_bytes(fetch_url))

            for article in root.findall(".//PubmedArticle"):
                title_node = article.find(".//ArticleTitle")
                title = clean("".join(title_node.itertext())) if title_node is not None else ""
                pmid = clean(article.findtext(".//PMID"))
                journal = clean(article.findtext(".//Journal/Title"))
                abstract = " ".join(
                    clean("".join(node.itertext()))
                    for node in article.findall(".//AbstractText")
                )

                # Journal records are research-bank records.
                # They are NOT directly promoted to video opportunities.
                records.append({
                    "id": "journal:" + pmid,
                    "source": "Journal Focus",
                    "kind": "RESEARCH",
                    "concept": None,
                    "title": title,
                    "description": abstract[:1400],
                    "journal": journal,
                    "published_dt": now_utc(),
                    "url": "https://pubmed.ncbi.nlm.nih.gov/" + pmid + "/",
                })

    except Exception as exc:
        errors.append(str(exc))

    return records, ("Journal Focus: " + errors[0] if errors else None)


# ---------- Opportunity engine ----------
def build_opportunities(internet_records, research_records):
    # Group only internet/behaviour signals.
    clusters = {}
    for item in internet_records:
        concept = item.get("concept")
        if concept:
            clusters.setdefault(concept, []).append(item)

    # Research is attached to the same concept.
    research_by_concept = {}
    for item in research_records:
        concept = item.get("concept")
        if concept:
            research_by_concept.setdefault(concept, []).append(item)

    opportunities = []

    for concept, signals in clusters.items():
        concept_data = CONCEPTS[concept]

        visual_values = []
        behaviour_values = []
        freshness_values = []

        for signal in signals:
            v, _ = visual_score(signal)
            b, _ = behaviour_score(signal)
            visual_values.append(v)
            behaviour_values.append(b)
            freshness_values.append(freshness_score(signal.get("published_dt")))

        visual = max(visual_values or [0])
        behaviour = max(behaviour_values or [0])
        fresh = max(freshness_values or [0])
        audience = audience_signal_score(signals)

        source_families = sorted({x["source"] for x in signals})

        # Research connection is a bonus only.
        usable_research = []
        for r in research_by_concept.get(concept, []):
            q, role, flags = research_quality(r)
            r["research_quality"] = q
            r["research_role"] = role
            r["research_flags"] = flags
            if role == "EXPLANATION CANDIDATE":
                usable_research.append(r)

        usable_research.sort(key=lambda x: x.get("research_quality", 0), reverse=True)
        research_pick = usable_research[0] if usable_research else None

        # Strict opportunity score:
        # audience + behaviour + visual are the core.
        # research can improve confidence but cannot rescue a weak behaviour.
        score = (
            audience * 3
            + behaviour * 3
            + visual * 3
            + fresh * 1
            + (2 if research_pick else 0)
        )
        score = min(100, int(round(score)))

        # Hard gates.
        if behaviour < 4:
            continue
        if visual < 4:
            continue
        if audience < 4:
            continue

        if research_pick:
            research_label = "Research connection found"
        else:
            research_label = "Research connection not yet found"

        opportunities.append({
            "concept": concept,
            "title": concept_data["display"],
            "human_question": concept_data["human_question"],
            "show": concept_data["show"],
            "video_direction": concept_data["video_direction"],
            "score": score,
            "audience": audience,
            "behaviour": behaviour,
            "visual": visual,
            "freshness": fresh,
            "sources": source_families,
            "signals": sorted(
                signals,
                key=lambda x: (
                    x.get("views_per_day", 0),
                    freshness_score(x.get("published_dt"))
                ),
                reverse=True,
            )[:8],
            "research": research_pick,
            "research_label": research_label,
        })

    opportunities.sort(
        key=lambda x: (
            x["score"],
            x["audience"],
            x["visual"],
            x["behaviour"],
        ),
        reverse=True,
    )

    return opportunities


def compare_scans(current, previous):
    previous_map = {
        item["concept"]: item
        for item in (previous or {}).get("opportunities", [])
    }

    for item in current:
        old = previous_map.get(item["concept"])
        if old is None:
            item["status"] = "🆕 NEW"
        elif item["score"] >= old["score"] + 8:
            item["status"] = "🔥 ACCELERATING"
        elif item["score"] > old["score"]:
            item["status"] = "📈 RISING"
        else:
            item["status"] = "🔁 STABLE"

    return current


# ---------- Presentation ----------
def metric_bar(label, value, maximum=10):
    filled = int(round(value / maximum * 5))
    return "●" * filled + "○" * (5 - filled)


def render_opportunity(item, rank):
    with st.container(border=True):
        top_left, top_right = st.columns([5, 1])

        with top_left:
            st.markdown(
                f"## {rank}. {item.get('status', '')} {item['title']}"
            )
            st.caption(
                f"Opportunity {item['score']}/100  •  "
                f"Audience {item['audience']}/10  •  "
                f"Behaviour {item['behaviour']}/10  •  "
                f"Visual {item['visual']}/10  •  "
                f"Freshness {item['freshness']}/10"
            )

        with top_right:
            yt = next(
                (x for x in item["signals"] if x["source"] == "YouTube"),
                None
            )
            if yt and yt.get("thumbnail"):
                st.image(yt["thumbnail"], use_container_width=True)

        st.markdown("### 👀 WHAT PEOPLE ARE DOING / WATCHING")
        st.write(item["human_question"])

        st.markdown("### 🎬 WHAT MEDIMANCH CAN ACTUALLY SHOW")
        st.write(item["show"])

        st.markdown("### 💡 POSSIBLE VIDEO DIRECTION")
        st.info(item["video_direction"])

        st.markdown("### 📡 WHY THIS IS ON THE RADAR")
        st.write(
            "Independent signal families: "
            + " • ".join(item["sources"])
        )

        if item["research"]:
            r = item["research"]
            st.markdown("### 🔬 RESEARCH CONNECTION")
            st.write(r["title"])
            st.caption(
                f"{r.get('journal','')}  •  "
                f"{r.get('research_role','')}  •  "
                f"research routing {r.get('research_quality', 0)}/10"
            )
            st.write(
                "Use this research to explain/check the behaviour — "
                "not as automatic proof that the viral behaviour works."
            )
        else:
            st.markdown("### 🔬 RESEARCH CONNECTION")
            st.write(
                "No strong matching research connection was found in this scan. "
                "This remains a behaviour/visual lead."
            )

        with st.expander("VIEW SOURCE SIGNALS"):
            for signal in item["signals"]:
                if signal["source"] == "YouTube":
                    stats = (
                        f" • {signal.get('views', 0):,} views"
                        f" • {signal.get('views_per_day', 0):,}/day"
                    )
                else:
                    stats = ""
                st.write(f"**{signal['source']}**{stats}: {signal['title']}")
                st.markdown(f"[Open source]({signal['url']})")


def render_research(item):
    q, role, flags = research_quality(item)

    with st.container(border=True):
        st.markdown(f"### 🔬 {item['title']}")
        st.caption(
            f"{item.get('journal','')} • {role} • research routing {q}/10"
        )

        if flags["animal"]:
            st.warning(
                "Animal/preclinical context. Kept in Research Bank; "
                "not promoted as a human home/public video opportunity."
            )
        elif flags["disease_heavy"]:
            st.warning(
                "Disease-treatment-heavy context. Kept in Research Bank "
                "instead of being converted into a generic home experiment."
            )
        else:
            st.info(
                "Research record. It can explain or challenge a behaviour "
                "but does not create a Shoot Now opportunity by itself."
            )

        if item.get("description"):
            st.write(item["description"][:900])

        st.markdown(f"[Open source]({item['url']})")


# ============================================================
# UI
# ============================================================

st.title("🔥 MEDIMANCH INTELLIGENCE RADAR V6")
st.write(
    "Behaviour first • Visual opportunity • Fresh signals • Research explanation"
)

with st.sidebar:
    api_key = st.secrets.get("YOUTUBE_API_KEY", "")

    if api_key:
        st.success("🟢 YouTube API: CONNECTED")
    else:
        st.error("🔴 YouTube API: NOT CONNECTED")

    st.divider()
    st.markdown("### LIVE FEED LAYERS")
    st.write("🎥 YouTube behaviour/video")
    st.write("📈 Google Trends India")
    st.write("📰 Google News")
    st.write("🔬 PubMed")
    st.write("📚 Authoritative Journal Watch")

    st.divider()
    st.markdown("### V6 RULE")
    st.caption(
        "Research papers cannot become Shoot Now merely because "
        "their abstract contains words such as test, compare, heat or metabolism."
    )

    if st.button("🧹 RESET SCAN MEMORY", use_container_width=True):
        st.session_state.current_scan = None
        st.session_state.previous_scan = None
        st.session_state.scan_number = 0
        st.session_state.scan_history = []
        st.rerun()


if st.button(
    "🔄 RUN FRESH INTELLIGENCE SCAN",
    type="primary",
    use_container_width=True,
):
    # No @st.cache_data is used for live feed functions.
    # Clear any unrelated cache and create a genuinely new scan.
    st.cache_data.clear()

    with st.spinner(
        "1/5 Internet behaviour → 2/5 visual signals → "
        "3/5 research → 4/5 convergence → 5/5 opportunity cards..."
    ):
        youtube_records, youtube_error = youtube_scan(api_key)
        trend_records, trend_error = trends_scan()
        news_records, news_error = news_scan()
        pubmed_records, pubmed_error = pubmed_scan()
        journal_records, journal_error = journal_scan()

        internet_records = unique_by_id(
            youtube_records + trend_records + news_records
        )
        research_records = unique_by_id(
            pubmed_records + journal_records
        )

        opportunities = build_opportunities(
            internet_records,
            research_records,
        )

        opportunities = compare_scans(
            opportunities,
            st.session_state.current_scan,
        )

        scan = {
            "number": st.session_state.scan_number + 1,
            "time": now_utc(),
            "opportunities": opportunities,
            "research": research_records,
            "internet_count": len(internet_records),
            "research_count": len(research_records),
            "errors": [
                x for x in [
                    youtube_error,
                    trend_error,
                    news_error,
                    pubmed_error,
                    journal_error,
                ]
                if x
            ],
        }

        if st.session_state.current_scan is not None:
            st.session_state.previous_scan = st.session_state.current_scan
            st.session_state.scan_history.append(
                st.session_state.current_scan
            )
            st.session_state.scan_history = st.session_state.scan_history[-10:]

        st.session_state.scan_number = scan["number"]
        st.session_state.current_scan = scan

    st.rerun()


scan = st.session_state.current_scan

if scan is None:
    st.info(
        "Click **RUN FRESH INTELLIGENCE SCAN**. "
        "The radar will first look for behaviour/visual signals, "
        "then attach research."
    )
    st.stop()

opportunities = scan["opportunities"]
shoot_now = [x for x in opportunities if x["score"] >= 55]
new_items = [x for x in opportunities if x.get("status") == "🆕 NEW"]
accelerating = [x for x in opportunities if x.get("status") == "🔥 ACCELERATING"]
rising = [x for x in opportunities if x.get("status") == "📈 RISING"]

m = st.columns(6)
m[0].metric("SCAN", f"#{scan['number']}")
m[1].metric("OPPORTUNITIES", len(opportunities))
m[2].metric("NEW", len(new_items))
m[3].metric("ACCELERATING", len(accelerating))
m[4].metric("RESEARCH", scan["research_count"])
m[5].metric("SHOOT NOW", len(shoot_now))

st.caption(
    "Fresh scan: "
    + scan["time"].astimezone().strftime("%d %b %Y, %I:%M:%S %p")
)

tabs = st.tabs([
    "🔥 SHOOT NOW",
    "🆕 WHAT CHANGED",
    "🎥 VISUAL BEHAVIOUR",
    "🔬 RESEARCH BANK",
    "📚 JOURNALS",
    "⚙️ STATUS",
])

with tabs[0]:
    if not shoot_now:
        st.warning(
            "No concept crossed the strict Shoot Now threshold. "
            "This is intentional: weak research matches are not being promoted."
        )
    for i, item in enumerate(shoot_now[:10], 1):
        render_opportunity(item, i)

with tabs[1]:
    changed = new_items + accelerating + rising
    if not changed:
        st.info(
            "No new/rising/accelerating behaviour concepts versus the previous scan."
        )
    for i, item in enumerate(changed[:20], 1):
        render_opportunity(item, i)

with tabs[2]:
    visual_items = sorted(
        opportunities,
        key=lambda x: (x["visual"], x["audience"], x["score"]),
        reverse=True,
    )
    for i, item in enumerate(visual_items[:20], 1):
        render_opportunity(item, i)

with tabs[3]:
    if not scan["research"]:
        st.info("No research records returned.")
    for item in sorted(
        scan["research"],
        key=lambda x: research_quality(x)[0],
        reverse=True,
    )[:40]:
        render_research(item)

with tabs[4]:
    st.write(
        "Authoritative journals are a research discovery layer. "
        "They do not automatically create video opportunities."
    )
    st.write(", ".join(JOURNAL_FOCUS))

with tabs[5]:
    st.write("**Internet signal records:**", scan["internet_count"])
    st.write("**Research records:**", scan["research_count"])
    st.write("**Opportunity concepts:**", len(opportunities))
    st.write("**Previous scans retained in this session:**", len(st.session_state.scan_history))

    if scan["errors"]:
        st.warning("Some feed layers reported an issue:")
        for error in scan["errors"]:
            st.write("•", error)
    else:
        st.success("All configured feed layers returned without reported errors.")

    st.divider()
    st.markdown("### V6 intelligence pipeline")
    st.write("1. Discover real internet behaviour / visual signals.")
    st.write("2. Route signals into known Medimanch concept universes.")
    st.write("3. Score actual audience + behaviour + visual evidence.")
    st.write("4. Search research for the same concept.")
    st.write("5. Keep animal/preclinical/disease-heavy papers in Research Bank.")
    st.write("6. Attach useful research as an explanation layer.")
    st.write("7. Promote only human-facing opportunities to Shoot Now.")
    st.write("8. Compare the new scan with the previous scan.")
