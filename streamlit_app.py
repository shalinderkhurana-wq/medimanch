import streamlit as st
from datetime import datetime, timedelta, timezone
import time
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
import json
import re

from medimanch_keywords import (
    RADAR_DICTIONARY,
    YOUTUBE_SEARCH_QUERIES,
    RESEARCH_QUERY_PACKS,
    JOURNAL_FOCUS,
    VISUAL_SIGNAL_QUERIES,
)

st.set_page_config(
    page_title="Medimanch Live Radar V5",
    page_icon="🔥",
    layout="wide",
)

# -----------------------------
# SESSION MEMORY
# -----------------------------
if "scan_history" not in st.session_state:
    st.session_state.scan_history = []

if "current_scan" not in st.session_state:
    st.session_state.current_scan = None

if "scan_number" not in st.session_state:
    st.session_state.scan_number = 0


def now_utc():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def clean_text(x):
    return re.sub(r"\s+", " ", str(x or "")).strip()


def normalize(text):
    text = clean_text(text).lower()
    return re.sub(r"[^a-z0-9\u0900-\u097f]+", " ", text)


def matched_terms(text):
    n = normalize(text)
    hits = []
    for group, words in RADAR_DICTIONARY.items():
        for word in words:
            if normalize(word) and normalize(word) in n:
                hits.append((group, word))
    # preserve order / uniqueness
    seen = set()
    out = []
    for x in hits:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def score_visual(text, source=""):
    n = normalize(text)
    visual_words = RADAR_DICTIONARY.get("visual", [])
    action_words = RADAR_DICTIONARY.get("actions", [])
    selftest_words = RADAR_DICTIONARY.get("self_test", [])
    challenge_words = RADAR_DICTIONARY.get("challenge", [])
    score = 0
    reasons = []
    if any(normalize(w) in n for w in visual_words):
        score += 2
        reasons.append("visible action/object")
    if any(normalize(w) in n for w in action_words):
        score += 2
        reasons.append("action can be demonstrated")
    if any(normalize(w) in n for w in selftest_words):
        score += 2
        reasons.append("self-test potential")
    if any(normalize(w) in n for w in challenge_words):
        score += 2
        reasons.append("challenge potential")
    if any(k in n for k in ["before", "after", "compare", "test", "challenge", "try this", "do this"]):
        score += 1
        reasons.append("comparison/test language")
    if source == "YouTube":
        score += 1
    return min(10, score), reasons


def freshness_score(published_dt):
    if not published_dt:
        return 2
    age = max(0, (now_utc() - published_dt).total_seconds() / 3600)
    if age <= 6:
        return 10
    if age <= 24:
        return 8
    if age <= 72:
        return 6
    if age <= 168:
        return 4
    return 2


def source_strength(source):
    return {
        "YouTube": 5,
        "Google Trends": 5,
        "Google News": 4,
        "PubMed": 5,
        "Europe PMC": 5,
        "Journal Focus": 6,
    }.get(source, 2)


def opportunity_score(item):
    visual, _ = score_visual(item.get("title", "") + " " + item.get("description", ""), item.get("source", ""))
    fresh = freshness_score(item.get("published_dt"))
    strength = source_strength(item.get("source"))
    matches = len(item.get("matches", []))
    behavior = sum(1 for g, _ in item.get("matches", []) if g in ["behaviour", "actions", "self_test", "challenge"])
    effect = sum(1 for g, _ in item.get("matches", []) if g in ["effects", "myth_claim", "naturopathy", "diet_food", "ayurveda"])
    score = visual * 4 + fresh * 2 + strength * 2 + min(10, matches) + behavior * 2 + effect
    return min(100, score), visual


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    # RFC / common ISO forms
    try:
        if s.endswith("Z"):
            return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
        return datetime.fromisoformat(s).astimezone(timezone.utc)
    except Exception:
        pass
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M %z"):
        try:
            return datetime.strptime(s, fmt).astimezone(timezone.utc)
        except Exception:
            pass
    return None


def fetch_url(url, timeout=15, headers=None):
    req = urllib.request.Request(
        url,
        headers=headers or {
            "User-Agent": "MedimanchLiveRadar/5.0 (+https://streamlit.io)"
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def google_trends_rss():
    # cache-busting parameter helps avoid an unchanged browser/proxy response
    url = "https://trends.google.com/trending/rss?geo=IN&_fresh=" + str(int(time.time()))
    try:
        data = fetch_url(url)
        root = ET.fromstring(data)
        items = []
        for node in root.findall(".//item"):
            title = clean_text(node.findtext("title"))
            traffic = clean_text(node.findtext("approx_traffic"))
            pub = parse_date(node.findtext("pubDate"))
            link = clean_text(node.findtext("link"))
            text = title + " " + traffic
            matches = matched_terms(text)
            if matches:
                items.append({
                    "id": "gt:" + normalize(title),
                    "title": title,
                    "description": "Google Trends India rising query. Approx traffic: " + traffic,
                    "source": "Google Trends",
                    "url": link,
                    "published_dt": pub or now_utc(),
                    "matches": matches,
                    "kind": "search_signal",
                })
        return items, None
    except Exception as e:
        return [], "Google Trends: " + str(e)


def google_news_rss():
    queries = VISUAL_SIGNAL_QUERIES[:12]
    all_items = []
    errors = []
    for q in queries:
        q_encoded = urllib.parse.quote(q)
        url = (
            "https://news.google.com/rss/search?q="
            + q_encoded
            + "&hl=en-IN&gl=IN&ceid=IN:en&_fresh="
            + str(int(time.time()))
        )
        try:
            data = fetch_url(url)
            root = ET.fromstring(data)
            for node in root.findall(".//item"):
                title = clean_text(node.findtext("title"))
                link = clean_text(node.findtext("link"))
                pub = parse_date(node.findtext("pubDate"))
                desc = clean_text(node.findtext("description"))
                text = title + " " + re.sub("<[^>]+>", " ", desc)
                matches = matched_terms(text)
                if matches:
                    all_items.append({
                        "id": "news:" + normalize(title),
                        "title": title,
                        "description": re.sub("<[^>]+>", " ", desc),
                        "source": "Google News",
                        "url": link,
                        "published_dt": pub or now_utc(),
                        "matches": matches,
                        "kind": "web_signal",
                    })
        except Exception as e:
            errors.append(str(e))
    # dedupe
    unique = {}
    for x in all_items:
        unique[x["id"]] = x
    return list(unique.values()), ("Google News: " + errors[0] if errors else None)


def youtube_scan(api_key):
    if not api_key:
        return [], "YouTube: API key missing. Add YOUTUBE_API_KEY in Streamlit Secrets."
    items = []
    errors = []
    published_after = iso(now_utc() - timedelta(days=7))
    queries = YOUTUBE_SEARCH_QUERIES + VISUAL_SIGNAL_QUERIES[:8]

    for q in queries:
        params = {
            "part": "snippet",
            "q": q,
            "type": "video",
            "order": "date",
            "publishedAfter": published_after,
            "regionCode": "IN",
            "relevanceLanguage": "hi",
            "maxResults": "8",
            "key": api_key,
        }
        url = "https://www.googleapis.com/youtube/v3/search?" + urllib.parse.urlencode(params)
        try:
            data = json.loads(fetch_url(url))
            for r in data.get("items", []):
                vid = r.get("id", {}).get("videoId")
                sn = r.get("snippet", {})
                if not vid:
                    continue
                title = clean_text(sn.get("title"))
                desc = clean_text(sn.get("description"))
                matches = matched_terms(title + " " + desc)
                if not matches:
                    continue
                pub = parse_date(sn.get("publishedAt")) or now_utc()
                items.append({
                    "id": "yt:" + vid,
                    "video_id": vid,
                    "title": title,
                    "description": desc[:800],
                    "source": "YouTube",
                    "url": "https://www.youtube.com/watch?v=" + vid,
                    "published_dt": pub,
                    "matches": matches,
                    "channel": clean_text(sn.get("channelTitle")),
                    "thumbnail": sn.get("thumbnails", {}).get("medium", {}).get("url", ""),
                    "kind": "visual_signal",
                })
        except Exception as e:
            errors.append(str(e))

    # Get stats in batches of 50.
    unique = {}
    for x in items:
        unique[x["id"]] = x
    items = list(unique.values())

    for start in range(0, len(items), 50):
        batch = items[start:start + 50]
        ids = ",".join(x["video_id"] for x in batch)
        params = {"part": "statistics,contentDetails", "id": ids, "key": api_key}
        url = "https://www.googleapis.com/youtube/v3/videos?" + urllib.parse.urlencode(params)
        try:
            data = json.loads(fetch_url(url))
            statmap = {x["id"]: x for x in data.get("items", [])}
            for x in batch:
                s = statmap.get(x["video_id"], {}).get("statistics", {})
                views = int(s.get("viewCount", 0) or 0)
                likes = int(s.get("likeCount", 0) or 0)
                comments = int(s.get("commentCount", 0) or 0)
                age_hours = max(1, (now_utc() - x["published_dt"]).total_seconds() / 3600)
                views_per_day = views / max(age_hours / 24, 0.25)
                x["views"] = views
                x["likes"] = likes
                x["comments"] = comments
                x["views_per_day"] = int(views_per_day)
        except Exception as e:
            errors.append(str(e))

    return items, ("YouTube: " + errors[0] if errors else None)


def pubmed_scan():
    items = []
    errors = []
    since = (now_utc() - timedelta(days=30)).strftime("%Y/%m/%d")
    until = now_utc().strftime("%Y/%m/%d")
    for label, query in RESEARCH_QUERY_PACKS.items():
        q = f"({query}) AND ({since}[Date - Publication] : {until}[Date - Publication])"
        params = {
            "db": "pubmed",
            "term": q,
            "retmode": "json",
            "retmax": "8",
            "sort": "pub date",
            "tool": "medimanch_radar",
            "email": "radar@example.com",
        }
        url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?" + urllib.parse.urlencode(params)
        try:
            data = json.loads(fetch_url(url))
            ids = data.get("esearchresult", {}).get("idlist", [])
            if not ids:
                continue
            params2 = {
                "db": "pubmed",
                "id": ",".join(ids),
                "retmode": "xml",
            }
            url2 = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?" + urllib.parse.urlencode(params2)
            xml_data = fetch_url(url2)
            root = ET.fromstring(xml_data)
            for art in root.findall(".//PubmedArticle"):
                title = clean_text("".join(art.find(".//ArticleTitle").itertext())) if art.find(".//ArticleTitle") is not None else ""
                abstract_parts = []
                for a in art.findall(".//AbstractText"):
                    abstract_parts.append(clean_text("".join(a.itertext())))
                abstract = " ".join(abstract_parts)
                pmid = clean_text(art.findtext(".//PMID"))
                journal = clean_text(art.findtext(".//Journal/Title"))
                pubdate = art.find(".//PubDate")
                year = pubdate.findtext("Year") if pubdate is not None else ""
                month = pubdate.findtext("Month") if pubdate is not None else ""
                day = pubdate.findtext("Day") if pubdate is not None else ""
                dt = None
                if year and month and day:
                    try:
                        dt = datetime.strptime(f"{year} {month} {day}", "%Y %b %d").replace(tzinfo=timezone.utc)
                    except Exception:
                        pass
                if dt is None and year:
                    try:
                        dt = datetime(int(year), 1, 1, tzinfo=timezone.utc)
                    except Exception:
                        dt = now_utc()
                matches = matched_terms(title + " " + abstract + " " + label)
                if not matches:
                    continue
                items.append({
                    "id": "pmid:" + pmid,
                    "title": title,
                    "description": abstract[:1000],
                    "source": "PubMed",
                    "journal": journal,
                    "url": "https://pubmed.ncbi.nlm.nih.gov/" + pmid + "/",
                    "published_dt": dt or now_utc(),
                    "matches": matches,
                    "kind": "research",
                    "research_pack": label,
                })
        except Exception as e:
            errors.append(str(e))
    unique = {}
    for x in items:
        unique[x["id"]] = x
    return list(unique.values()), ("PubMed: " + errors[0] if errors else None)


def europe_pmc_scan():
    items = []
    errors = []
    since = (now_utc() - timedelta(days=30)).strftime("%Y-%m-%d")
    for label, query in RESEARCH_QUERY_PACKS.items():
        params = {
            "query": f"({query}) AND FIRST_PDATE:[{since} TO *]",
            "format": "json",
            "pageSize": "8",
            "sort": "FIRST_PDATE_D desc",
        }
        url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + urllib.parse.urlencode(params)
        try:
            data = json.loads(fetch_url(url))
            for r in data.get("resultList", {}).get("result", []):
                title = clean_text(r.get("title"))
                abstract = clean_text(r.get("abstractText"))
                pmid = clean_text(r.get("pmid"))
                doi = clean_text(r.get("doi"))
                date_s = r.get("firstPublicationDate")
                dt = parse_date(date_s + "T00:00:00+00:00") if date_s else None
                matches = matched_terms(title + " " + abstract + " " + label)
                if not matches:
                    continue
                url_out = (
                    "https://pubmed.ncbi.nlm.nih.gov/" + pmid + "/"
                    if pmid else
                    ("https://doi.org/" + doi if doi else "https://europepmc.org/")
                )
                items.append({
                    "id": "epmc:" + (pmid or doi or normalize(title)),
                    "title": title,
                    "description": abstract[:1000],
                    "source": "Europe PMC",
                    "journal": clean_text(r.get("journalTitle")),
                    "url": url_out,
                    "published_dt": dt or now_utc(),
                    "matches": matches,
                    "kind": "research",
                    "research_pack": label,
                })
        except Exception as e:
            errors.append(str(e))
    unique = {}
    for x in items:
        unique[x["id"]] = x
    return list(unique.values()), ("Europe PMC: " + errors[0] if errors else None)


def journal_focus_scan():
    # Authoritative-journal layer without needing dozens of separate RSS endpoints.
    # PubMed searches the selected journal names and returns current records.
    items = []
    errors = []
    journal_query = " OR ".join(f'"{j}"[Journal]' for j in JOURNAL_FOCUS)
    q = f"({journal_query}) AND 2026[dp]"
    params = {
        "db": "pubmed",
        "term": q,
        "retmode": "json",
        "retmax": "40",
        "sort": "pub date",
    }
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?" + urllib.parse.urlencode(params)
    try:
        data = json.loads(fetch_url(url))
        ids = data.get("esearchresult", {}).get("idlist", [])
        if ids:
            params2 = {"db": "pubmed", "id": ",".join(ids), "retmode": "xml"}
            xml_data = fetch_url("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?" + urllib.parse.urlencode(params2))
            root = ET.fromstring(xml_data)
            for art in root.findall(".//PubmedArticle"):
                title_node = art.find(".//ArticleTitle")
                title = clean_text("".join(title_node.itertext())) if title_node is not None else ""
                pmid = clean_text(art.findtext(".//PMID"))
                journal = clean_text(art.findtext(".//Journal/Title"))
                abstract = " ".join(clean_text("".join(a.itertext())) for a in art.findall(".//AbstractText"))
                matches = matched_terms(title + " " + abstract)
                # Keep even when no dictionary match: this is the authoritative research watchlist.
                items.append({
                    "id": "journal:" + pmid,
                    "title": title,
                    "description": abstract[:1000],
                    "source": "Journal Focus",
                    "journal": journal,
                    "url": "https://pubmed.ncbi.nlm.nih.gov/" + pmid + "/",
                    "published_dt": now_utc(),
                    "matches": matches,
                    "kind": "authoritative_journal",
                })
    except Exception as e:
        errors.append(str(e))
    return items, ("Journal Focus: " + errors[0] if errors else None)


def classify_signal(item, previous_ids, previous_scores):
    sid = item["id"]
    if sid not in previous_ids:
        return "🆕 NEW"
    old = previous_scores.get(sid, 0)
    new = item.get("score", 0)
    if new >= old + 8:
        return "🔥 ACCELERATING"
    if new > old:
        return "📈 RISING"
    return "🔁 REPEATED"


def dedupe_items(items):
    out = {}
    for item in items:
        key = item["id"]
        if key not in out:
            out[key] = item
        else:
            # merge matches
            existing = out[key]
            existing["matches"] = list(dict.fromkeys(existing.get("matches", []) + item.get("matches", [])))
            if len(item.get("description", "")) > len(existing.get("description", "")):
                existing["description"] = item["description"]
    return list(out.values())


def build_scan(api_key):
    scan_time = now_utc()
    all_items = []
    errors = []

    scanners = [
        ("Google Trends", google_trends_rss),
        ("Google News", google_news_rss),
        ("PubMed", pubmed_scan),
        ("Europe PMC", europe_pmc_scan),
        ("Journal Focus", journal_focus_scan),
    ]

    for _, fn in scanners:
        items, err = fn()
        all_items.extend(items)
        if err:
            errors.append(err)

    yt_items, yt_err = youtube_scan(api_key)
    all_items.extend(yt_items)
    if yt_err:
        errors.append(yt_err)

    all_items = dedupe_items(all_items)

    previous = st.session_state.current_scan or {}
    previous_items = previous.get("items", [])
    previous_ids = {x.get("id") for x in previous_items}
    previous_scores = {x.get("id"): x.get("score", 0) for x in previous_items}

    for item in all_items:
        score, visual = opportunity_score(item)
        item["score"] = score
        item["visual"] = visual
        item["status"] = classify_signal(item, previous_ids, previous_scores)

        # Human-readable explanation
        groups = list(dict.fromkeys(g for g, _ in item.get("matches", [])))
        item["why_video"] = []
        if visual >= 6:
            item["why_video"].append("strong visual/test potential")
        if "behaviour" in groups or "actions" in groups:
            item["why_video"].append("people can perform/show the behaviour")
        if "self_test" in groups or "challenge" in groups:
            item["why_video"].append("self-test/public challenge potential")
        if item["source"] in ["PubMed", "Europe PMC", "Journal Focus"]:
            item["why_video"].append("research layer available")
        if not item["why_video"]:
            item["why_video"].append("needs stronger visual translation")

    # Cross-source convergence by normalized matched terms
    term_sources = {}
    for item in all_items:
        for _, term in item.get("matches", []):
            key = normalize(term)
            term_sources.setdefault(key, set()).add(item["source"])

    for item in all_items:
        terms = []
        for _, term in item.get("matches", []):
            key = normalize(term)
            sources = term_sources.get(key, set())
            if len(sources) >= 3:
                terms.append((term, len(sources)))
        item["convergence"] = max([x[1] for x in terms], default=1)
        item["convergence_terms"] = [x[0] for x in terms[:5]]
        item["shoot_worthy"] = (
            item["score"] >= 42
            and item["visual"] >= 5
            and (item["convergence"] >= 2 or item["source"] in ["YouTube", "Google Trends"])
        )

    all_items.sort(key=lambda x: (x.get("shoot_worthy", False), x.get("score", 0), x.get("convergence", 0)), reverse=True)

    scan = {
        "scan_number": st.session_state.scan_number + 1,
        "time": scan_time,
        "items": all_items,
        "errors": errors,
    }
    return scan


def render_item(item, rank=None):
    title = item.get("title", "Untitled")
    status = item.get("status", "")
    score = item.get("score", 0)
    visual = item.get("visual", 0)
    conv = item.get("convergence", 1)

    with st.container(border=True):
        c1, c2 = st.columns([5, 1])
        with c1:
            if rank:
                st.markdown(f"### {rank}. {status} {title}")
            else:
                st.markdown(f"### {status} {title}")
            st.caption(f"{item.get('source')}  •  Score {score}/100  •  Visual {visual}/10  •  Convergence {conv}")
        with c2:
            if item.get("thumbnail"):
                st.image(item["thumbnail"], use_container_width=True)

        if item.get("channel"):
            st.write("**Channel:**", item["channel"])
        if item.get("journal"):
            st.write("**Journal:**", item["journal"])

        st.write("**What is the signal?**", item.get("description", "")[:900])

        if item.get("matches"):
            st.write(
                "**Matched Medimanch signals:**",
                ", ".join(f"{g}:{t}" for g, t in item["matches"][:12])
            )

        st.write("**Why this could work on video:**", "; ".join(item.get("why_video", [])))

        if item.get("convergence_terms"):
            st.write("**Cross-source convergence:**", ", ".join(item["convergence_terms"]))

        if item.get("shoot_worthy"):
            st.success("🔥 SHOOT-WORTHY: investigate this before making the final video.")
        else:
            st.info("Watch / research / develop further before shooting.")

        st.markdown(f"[Open source]({item.get('url')})")


# -----------------------------
# UI
# -----------------------------
st.title("🔥 MEDIMANCH LIVE SIGNAL & VISUAL RESEARCH RADAR V5")
st.write("Fresh internet signals × visual behaviours × authoritative research × change detection")

with st.sidebar:
    st.subheader("RADAR CONTROLS")
    api_key = st.secrets.get("YOUTUBE_API_KEY", "")
    if api_key:
        st.success("YouTube API: CONNECTED")
    else:
        st.warning("YouTube API: NOT CONNECTED")

    st.caption("V5 does not cache live scans. Each button press creates a fresh scan.")
    if st.button("🧹 RESET SCAN MEMORY", use_container_width=True):
        st.session_state.scan_history = []
        st.session_state.current_scan = None
        st.session_state.scan_number = 0
        st.rerun()

    st.divider()
    st.subheader("Feed layers")
    st.write("🎥 YouTube visual signals")
    st.write("📈 Google Trends India")
    st.write("📰 Google News")
    st.write("🔬 PubMed")
    st.write("🧬 Europe PMC")
    st.write("📚 Journal Focus")

    st.divider()
    st.caption("Important: YouTube search requires an API key. Other public feeds can work without it.")

if st.button("🔄 RUN FRESH LIVE SCAN", type="primary", use_container_width=True):
    # Force a genuinely fresh execution path.
    st.cache_data.clear()
    with st.spinner("Fetching fresh signals and comparing with the previous scan..."):
        scan = build_scan(api_key)
    st.session_state.scan_number = scan["scan_number"]
    if st.session_state.current_scan:
        st.session_state.scan_history.append(st.session_state.current_scan)
        st.session_state.scan_history = st.session_state.scan_history[-10:]
    st.session_state.current_scan = scan
    st.rerun()

scan = st.session_state.current_scan

if not scan:
    st.info("Click **RUN FRESH LIVE SCAN** to collect live signals.")
    st.stop()

items = scan["items"]
shoot = [x for x in items if x.get("shoot_worthy")]
new_items = [x for x in items if x.get("status") == "🆕 NEW"]
rising = [x for x in items if x.get("status") == "📈 RISING"]
accelerating = [x for x in items if x.get("status") == "🔥 ACCELERATING"]
research = [x for x in items if x.get("source") in ["PubMed", "Europe PMC", "Journal Focus"]]
visual = [x for x in items if x.get("source") == "YouTube" and x.get("visual", 0) >= 5]

cols = st.columns(6)
cols[0].metric("SCAN", f"#{scan['scan_number']}")
cols[1].metric("NEW", len(new_items))
cols[2].metric("RISING", len(rising))
cols[3].metric("ACCELERATING", len(accelerating))
cols[4].metric("RESEARCH", len(research))
cols[5].metric("SHOOT NOW", len(shoot))

st.caption("Scan time: " + scan["time"].astimezone().strftime("%d %b %Y, %I:%M:%S %p"))

tabs = st.tabs([
    "🔥 SHOOT OPPORTUNITIES",
    "🆕 WHAT CHANGED",
    "🎥 VISUAL RADAR",
    "🔬 RESEARCH RADAR",
    "📚 JOURNAL FOCUS",
    "⚙️ SCAN STATUS",
])

with tabs[0]:
    if not shoot:
        st.warning("No signal has crossed the current shoot threshold yet. That is intentional; the radar is filtering rather than inventing opportunities.")
    for i, item in enumerate(shoot[:10], 1):
        render_item(item, i)

with tabs[1]:
    changed = new_items + accelerating + rising
    if not changed:
        st.info("No new/rising/accelerating signals relative to the previous scan.")
    for i, item in enumerate(changed[:25], 1):
        render_item(item, i)

with tabs[2]:
    vis = sorted(visual, key=lambda x: (x.get("score", 0), x.get("visual", 0)), reverse=True)
    if not vis:
        st.info("No strong visual YouTube matches in this scan.")
    for i, item in enumerate(vis[:20], 1):
        render_item(item, i)

with tabs[3]:
    res = sorted(research, key=lambda x: x.get("score", 0), reverse=True)
    for i, item in enumerate(res[:30], 1):
        render_item(item, i)

with tabs[4]:
    journals = [x for x in items if x.get("source") == "Journal Focus"]
    if not journals:
        st.info("No journal records returned.")
    for i, item in enumerate(journals[:30], 1):
        render_item(item, i)

with tabs[5]:
    st.write("**Current scan records:**", len(items))
    st.write("**Previous scans kept in session:**", len(st.session_state.scan_history))
    if scan["errors"]:
        st.warning("Some feeds reported errors:")
        for e in scan["errors"]:
            st.write("•", e)
    else:
        st.success("All configured feed layers returned without reported errors.")

    st.divider()
    st.write("### What V5 is doing")
    st.write("1. Clears Streamlit cache before a fresh scan.")
    st.write("2. Adds a fresh timestamp to RSS requests.")
    st.write("3. Fetches current YouTube, Trends, News and research records.")
    st.write("4. Compares the new scan with the immediately previous scan.")
    st.write("5. Labels records NEW / RISING / ACCELERATING / REPEATED.")
    st.write("6. Scores visual opportunity and cross-source convergence.")
    st.write("7. Puts only stronger candidates into SHOOT OPPORTUNITIES.")
