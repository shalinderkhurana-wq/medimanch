from pathlib import Path
import os, re, json, sqlite3, hashlib, random
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
import xml.etree.ElementTree as ET
import requests
import streamlit as st

APP_VERSION = "V10.0"
DB_PATH = os.environ.get("MEDIMANCH_DB", str(Path(__file__).with_name("radar_memory_v9.db")))
HEADERS = {"User-Agent": "Medimanch-Discovery-Radar/9.0 (content-research)"}
SOURCE_STATUS, SOURCE_ERRORS = {}, {}

# -----------------------------
# DISCOVERY SEARCH SPACE
# -----------------------------
DISCOVERY_SEEDS = [
    "people are trying health trend", "new health challenge", "viral body experiment",
    "what happens when body experiment", "unusual wellness habit", "strange health habit",
    "new food trend India", "Indian food trend health", "traditional remedy India viral",
    "home remedy people trying India", "before sleeping routine trend", "after eating habit trend",
    "morning routine body trend", "walking challenge India", "breathing challenge India",
    "fitness challenge India", "body test challenge", "self experiment health",
    "unusual sleep hack", "unusual hydration habit", "fermented food trend India",
    "soaked food trend India", "sprouted food trend India", "herbal drink trend India",
    "Ayurveda trend India", "naturopathy trend India", "wellness controversy India",
    "health myth viral India", "body question viral India", "new wellness product trend India"
]

# Search expansion is deliberately broad. These are not the Medimanch ecosystem.
BEHAVIOUR_EXPANDERS = [
    "people trying", "challenge", "before after", "experiment", "routine", "hack",
    "what happens when", "does it work", "viral", "trend", "test", "reaction", "India"
]

# Medimanch ecosystem is SECOND-STAGE enrichment.
CONCEPTS = {
    "Interoception": ["interoception", "body signal", "internal sensation", "heartbeat awareness", "gut feeling"],
    "Gut Mechanics": ["bloating", "distension", "stomach movement", "gut movement", "digestion", "bowel", "motility"],
    "Gut-Brain": ["gut brain", "brain gut", "appetite", "hunger", "fullness", "vagus", "interoception"],
    "Thermoregulation": ["body temperature", "thermoregulation", "cooling", "heat loss", "cold exposure", "heat exposure", "shivering"],
    "Sleep & Circadian": ["sleep", "bedtime", "circadian", "body clock", "morning light", "sunlight", "sleep routine"],
    "Movement & Balance": ["balance", "walking", "backward walking", "proprioception", "posture", "mobility", "coordination"],
    "Breathing Physiology": ["breathing", "breathwork", "nasal breathing", "slow breathing", "respiration", "breath hold"],
    "Hydration & Fluid Balance": ["hydration", "water", "electrolyte", "fluid", "dehydration", "mineral water", "sweat"],
    "Food Transformation": ["fermentation", "fermented", "sprouting", "soaking", "cooking", "food preparation", "idli", "kanji"],
    "Traditional & Herbal Practices": ["ayurveda", "herbal", "home remedy", "desi remedy", "kadha", "churan", "mud pack", "naturopathy"],
    "Sensorimotor Control": ["neck", "posture", "balance", "vision", "vestibular", "sensory conflict", "motor control"],
    "Stress & Recovery": ["stress", "relaxation", "recovery", "fatigue", "focus", "breathing", "sleep"],
}
PILLARS = {
    "Energy": ["energy", "fatigue", "exercise", "movement", "performance", "metabolism", "focus", "fitness"],
    "Gut": ["gut", "digestion", "bloating", "stomach", "bowel", "food", "meal", "appetite", "fermentation", "hunger"],
    "Hydration": ["water", "hydration", "electrolyte", "fluid", "mineral", "sweat", "heat"],
    "Recovery": ["sleep", "circadian", "stress", "breathing", "relaxation", "temperature", "pain", "posture", "recovery", "body clock"],
}

INDIA_TERMS = [
    "india", "indian", "hindi", "desi", "ayurveda", "ayurvedic", "naturopathy", "kadha", "churan",
    "roti", "rice", "dal", "chai", "curd", "dahi", "lassi", "idli", "dosa", "poha", "paratha",
    "masala", "haldi", "turmeric", "jeera", "ajwain", "saunf", "methi", "neem", "tulsi", "giloy",
    "yoga", "pranayama", "monsoon", "summer", "heatwave", "indian household"
]
NOISE_TERMS = ["celebrity", "movie", "song", "gaming", "politics", "election", "crypto", "stock", "giveaway", "trailer", "serial"]
VISUAL_TERMS = ["challenge", "experiment", "test", "before after", "routine", "trying", "reaction", "people", "walk", "eat", "drink", "sleep", "breathe", "hold", "soak", "sprout", "cook", "apply", "measure", "compare"]
CLAIM_TERMS = ["does", "works", "benefit", "harm", "why", "what happens", "science", "proof", "myth", "true", "false", "research", "study"]

# -----------------------------
# DATABASE / MEMORY
# -----------------------------
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS signals(
        uid TEXT PRIMARY KEY, source TEXT, title TEXT, url TEXT, published TEXT,
        first_seen TEXT, last_seen TEXT, seen_count INTEGER DEFAULT 1,
        last_opportunity REAL DEFAULT 0, last_ecosystem REAL DEFAULT 0,
        status TEXT, raw_json TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS scans(
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, source_counts TEXT,
        new_count INTEGER, changed_count INTEGER)""")
    con.commit()
    return con

def now(): return datetime.now(timezone.utc)
def iso(dt): return dt.astimezone(timezone.utc).isoformat()
def make_uid(source, url, title): return hashlib.sha1((source + "|" + url + "|" + title.lower().strip()).encode()).hexdigest()[:28]
def clean(s): return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()

def parse_date(value):
    if not value: return None
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(value).astimezone(timezone.utc)
    except Exception:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception:
            return None

# -----------------------------
# SOURCE LAYERS
# -----------------------------
def rss(url, source):
    try:
        r = requests.get(url, headers=HEADERS, timeout=18)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        out = []
        for item in root.findall(".//item"):
            title = clean(item.findtext("title")); link = clean(item.findtext("link")); desc = clean(item.findtext("description")); pub = clean(item.findtext("pubDate"))
            if title and link: out.append({"title": title, "url": link, "description": desc, "published": pub})
        SOURCE_STATUS[source] = True; SOURCE_ERRORS.pop(source, None)
        return out
    except Exception as e:
        SOURCE_STATUS[source] = False; SOURCE_ERRORS[source] = str(e)
        return []

def google_trends():
    return rss("https://trends.google.com/trending/rss?geo=IN&hl=en-IN", "Google Trends India")

def google_news(query):
    return rss("https://news.google.com/rss/search?q=" + quote(query) + "&hl=en-IN&gl=IN&ceid=IN:en", "Google News India")

def google_suggest(query):
    """Autocomplete is a query-language signal, not a search-volume measurement."""
    try:
        r=requests.get("https://suggestqueries.google.com/complete/search",params={"client":"firefox","hl":"en-IN","q":query},headers=HEADERS,timeout=12)
        r.raise_for_status(); data=r.json(); out=[]
        for q in (data[1] if isinstance(data,list) and len(data)>1 else []):
            if q: out.append({"title":q,"url":"https://www.google.com/search?q="+quote(q),"description":"Google autocomplete query signal","published":iso(now())})
        SOURCE_STATUS["People Searching"] = True; SOURCE_ERRORS.pop("People Searching",None); return out
    except Exception as e:
        SOURCE_STATUS["People Searching"] = False; SOURCE_ERRORS["People Searching"] = str(e); return []

def reddit_search(query):
    """Public Reddit search is used as a conversation/discussion signal, not as a popularity claim."""
    try:
        r=requests.get("https://www.reddit.com/search.json",params={"q":query,"sort":"new","t":"week","limit":25},headers={"User-Agent":"MedimanchRadar/10.0 discussion-research"},timeout=15)
        r.raise_for_status(); data=r.json(); out=[]
        for child in data.get("data",{}).get("children",[]):
            d=child.get("data",{}); title=clean(d.get("title")); url=d.get("url") or ("https://www.reddit.com"+d.get("permalink","") if d.get("permalink") else "")
            if title and url: out.append({"title":title,"url":url,"description":clean(d.get("selftext",""))[:900],"published":datetime.fromtimestamp(d.get("created_utc",0),tz=timezone.utc).isoformat() if d.get("created_utc") else iso(now())})
        SOURCE_STATUS["People Talking (Reddit)"] = True; SOURCE_ERRORS.pop("People Talking (Reddit)",None); return out
    except Exception as e:
        SOURCE_STATUS["People Talking (Reddit)"] = False; SOURCE_ERRORS["People Talking (Reddit)"] = str(e); return []

def youtube_comments(video_id, api_key, max_results=30):
    if not api_key or not video_id: return []
    try:
        params={"part":"snippet","videoId":video_id,"maxResults":max_results,"order":"relevance","textFormat":"plainText","key":api_key}
        r=requests.get("https://www.googleapis.com/youtube/v3/commentThreads",params=params,timeout=15); r.raise_for_status(); data=r.json(); out=[]
        for item in data.get("items",[]):
            sn=item.get("snippet",{}).get("topLevelComment",{}).get("snippet",{}); txt=clean(sn.get("textDisplay", ""))
            if txt: out.append({"title":txt,"url":"https://www.youtube.com/watch?v="+video_id,"description":"YouTube viewer comment","published":sn.get("publishedAt","")})
        SOURCE_STATUS["People Talking (YouTube comments)"] = True; SOURCE_ERRORS.pop("People Talking (YouTube comments)",None); return out
    except Exception as e:
        SOURCE_STATUS["People Talking (YouTube comments)"] = False; SOURCE_ERRORS["People Talking (YouTube comments)"] = str(e); return []

def youtube(query, api_key, hours=48, order="date"):
    if not api_key:
        SOURCE_STATUS["YouTube"] = False; SOURCE_ERRORS["YouTube"] = "YOUTUBE_API_KEY missing in Streamlit Secrets."; return []
    after = (now() - timedelta(hours=hours)).isoformat().replace("+00:00", "Z")
    params = {"part": "snippet", "q": query, "type": "video", "maxResults": 10,
              "order": order, "publishedAfter": after, "regionCode": "IN", "relevanceLanguage": "hi", "key": api_key}
    try:
        r = requests.get("https://www.googleapis.com/youtube/v3/search", params=params, timeout=18)
        r.raise_for_status(); data = r.json(); out = []
        for item in data.get("items", []):
            vid = item.get("id", {}).get("videoId"); sn = item.get("snippet", {})
            if vid:
                out.append({"title": sn.get("title", ""), "url": "https://www.youtube.com/watch?v=" + vid, "video_id": vid,
                            "description": sn.get("description", ""), "published": sn.get("publishedAt", "")})
        SOURCE_STATUS["YouTube"] = True; SOURCE_ERRORS.pop("YouTube", None)
        return out
    except Exception as e:
        SOURCE_STATUS["YouTube"] = False; SOURCE_ERRORS["YouTube"] = str(e); return []

def pubmed(query, days=30):
    try:
        params = {"db": "pubmed", "term": f"({query}) AND humans[filter]", "retmode": "json", "retmax": 8,
                  "reldate": days, "datetype": "pdat", "sort": "pub_date"}
        r = requests.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi", params=params, timeout=18)
        r.raise_for_status(); ids = r.json().get("esearchresult", {}).get("idlist", [])
        if not ids:
            SOURCE_STATUS["PubMed"] = True; return []
        r = requests.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi", params={"db":"pubmed","id":",".join(ids),"retmode":"json"}, timeout=18)
        r.raise_for_status(); data = r.json().get("result", {}); out=[]
        for pid in ids:
            row=data.get(pid,{})
            out.append({"title":row.get("title",""),"url":f"https://pubmed.ncbi.nlm.nih.gov/{pid}/","description":f"{row.get('sortfirstauthor','')} | {row.get('fulljournalname','')}","published":row.get("pubdate","")})
        SOURCE_STATUS["PubMed"] = True; SOURCE_ERRORS.pop("PubMed", None); return out
    except Exception as e:
        SOURCE_STATUS["PubMed"] = False; SOURCE_ERRORS["PubMed"] = str(e); return []

def europe_pmc(query, days=45):
    try:
        start=(now()-timedelta(days=days)).date().isoformat(); end=now().date().isoformat()
        params={"query":f"({query}) AND FIRST_PDATE:[{start} TO {end}]","format":"json","pageSize":8,"sort":"FIRST_PDATE_D DESC"}
        r=requests.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search",params=params,timeout=18); r.raise_for_status(); data=r.json(); out=[]
        for row in data.get("resultList",{}).get("result",[]):
            pmid=row.get("pmid"); url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else f"https://europepmc.org/article/MED/{row.get('id','')}"
            out.append({"title":row.get("title",""),"url":url,"description":f"{row.get('authorString','')} | {row.get('journalTitle','')}","published":row.get("firstPublicationDate","")})
        SOURCE_STATUS["Europe PMC"] = True; SOURCE_ERRORS.pop("Europe PMC", None); return out
    except Exception as e:
        SOURCE_STATUS["Europe PMC"] = False; SOURCE_ERRORS["Europe PMC"] = str(e); return []

def crossref(query, days=45):
    try:
        start=(now()-timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        params={"query":query,"filter":f"from-pub-date:{start[:10]},until-pub-date:{now().strftime('%Y-%m-%d')}","rows":8,"sort":"published","order":"desc"}
        r=requests.get("https://api.crossref.org/works",params=params,headers=HEADERS,timeout=18); r.raise_for_status(); out=[]
        for row in r.json().get("message",{}).get("items",[]):
            title=(row.get("title") or [""])[0]; url=row.get("URL","")
            if title and url: out.append({"title":title,"url":url,"description":(row.get("container-title") or [""])[0],"published":str((row.get("published-print") or row.get("published-online") or {}).get("date-parts",[[""]])[0][0])})
        SOURCE_STATUS["Crossref"] = True; SOURCE_ERRORS.pop("Crossref", None); return out
    except Exception as e:
        SOURCE_STATUS["Crossref"] = False; SOURCE_ERRORS["Crossref"] = str(e); return []

# -----------------------------
# DISCOVERY + ECOSYSTEM INTELLIGENCE
# -----------------------------
def text_of(x): return (x.get("title","") + " " + x.get("description","")).lower()
def count_hits(text, terms): return sum(1 for t in terms if t in text)

def india_relevance(text):
    hits=count_hits(text, INDIA_TERMS)
    if hits >= 2: return "HIGH", min(25, 12 + hits*3)
    if hits == 1: return "MEDIUM", 8
    # A neutral Indian-market signal can still be relevant if source itself is India scoped.
    return "LOW", 0

def ecosystem_match(text):
    candidates=[]
    for concept, terms in CONCEPTS.items():
        hits=count_hits(text, terms)
        if hits: candidates.append((hits, concept))
    if not candidates:
        return {"state":"UNMAPPED","concept":None,"pillar":None,"universe":None,"mechanism":None,"score":0,"why":"No sufficiently direct Medimanch concept signal found from the available text."}
    candidates.sort(reverse=True); hits, concept=candidates[0]
    pillar_scores={p:count_hits(text, terms) for p,terms in PILLARS.items()}
    pillar=max(pillar_scores,key=pillar_scores.get) if max(pillar_scores.values()) else None
    score=min(100, 45 + hits*12 + (15 if pillar else 0))
    state="MATCHED" if score>=65 else "POSSIBLE"
    why=f"Matched {hits} concept signal(s) for {concept}" + (f" and {pillar} pillar language." if pillar else ".")
    return {"state":state,"concept":concept,"pillar":pillar,"universe":concept,"mechanism":mechanism_for(concept),"score":score,"why":why}

def mechanism_for(concept):
    return {
        "Interoception":"brain-body sensing of internal signals",
        "Gut Mechanics":"movement, distension and sensory feedback in the digestive tract",
        "Gut-Brain":"communication between gut signals, appetite and brain control",
        "Thermoregulation":"heat production, heat loss, skin blood flow and core temperature",
        "Sleep & Circadian":"light, timing, body clock and sleep regulation",
        "Movement & Balance":"vision, vestibular input, proprioception and motor control",
        "Breathing Physiology":"respiratory control, gas exchange and autonomic regulation",
        "Hydration & Fluid Balance":"fluid intake, losses, electrolytes and osmoregulation",
        "Food Transformation":"physical/chemical changes caused by preparation or fermentation",
        "Traditional & Herbal Practices":"traditional preparation linked to ingredients and observable processes",
        "Sensorimotor Control":"sensory information guiding posture and movement",
        "Stress & Recovery":"autonomic, behavioural and recovery processes",
    }.get(concept,"")

def discovery_score(x):
    text=text_of(x); score=0
    d=parse_date(x.get("published"))
    if d:
        age=(now()-d).total_seconds()/3600
        score += 30 if age<=6 else 24 if age<=24 else 16 if age<=72 else 8
    visual=count_hits(text,VISUAL_TERMS); score += min(20, visual*3)
    claim=count_hits(text,CLAIM_TERMS); score += min(12, claim*2)
    india, ip=india_relevance(text); score += ip
    score += 10 if x.get("source") in ["YouTube","Google Trends India"] else 5 if x.get("source")=="Google News India" else 0
    score -= min(30,count_hits(text,NOISE_TERMS)*6)
    # Do NOT award points for ecosystem match here. Discovery must remain independent.
    return max(0,min(100,score))

def shoot_design(x):
    text=text_of(x); concept=x["eco"]["concept"]
    if concept in ["Movement & Balance","Sensorimotor Control"]:
        return ["SHOW: normal vs unusual movement side-by-side", "BASELINE: simple repeatable balance/movement observation", "CHANGE ONE VARIABLE: vision / direction / posture", "EXPLAIN: doctor maps the visible change to brain-body control"]
    if concept in ["Thermoregulation"]:
        return ["SHOW: skin/environment temperature context", "BASELINE: normal body state before exposure", "CHANGE ONE VARIABLE: temperature or timing", "EXPLAIN: heat loss, blood flow and core-temperature mechanism; avoid treatment claims"]
    if concept in ["Sleep & Circadian"]:
        return ["SHOW: the behaviour/routine exactly as people perform it", "BUILD: 24-hour body-clock timeline", "COMPARE: timing A vs timing B where safe and meaningful", "EXPLAIN: light, temperature or timing mechanism"]
    if concept in ["Gut Mechanics","Gut-Brain","Food Transformation"]:
        return ["SHOW: food/body transformation over time", "BASELINE: what changes before vs after the behaviour", "COMPARE: one variable such as meal size, preparation or timing", "EXPLAIN: digestion, movement, sensation and limits of the evidence"]
    if concept in ["Hydration & Fluid Balance"]:
        return ["SHOW: the behaviour and its context", "BASELINE: thirst/fluid-loss context rather than a disease claim", "COMPARE: water-only vs contextually different hydration where scientifically justified", "EXPLAIN: fluid balance and electrolytes"]
    if concept in ["Breathing Physiology","Stress & Recovery"]:
        return ["SHOW: breathing pattern or routine clearly", "BASELINE: resting pattern", "CHANGE ONE VARIABLE: rate, route or timing", "EXPLAIN: respiratory/autonomic mechanism and what cannot be inferred"]
    if concept == "Traditional & Herbal Practices":
        return ["SHOW: exact preparation and ingredients", "SEPARATE: traditional claim from measurable mechanism", "TEST: identify one observable variable rather than promising an outcome", "EXPLAIN: what research supports, what remains uncertain"]
    return ["SHOW: exactly what people are doing", "BASELINE: define the observable starting state", "TEST: change one variable and record the visible/measurable response", "EXPLAIN: search for a physiological mechanism before making any health claim"]

QUESTION_STARTERS = ["why", "how", "what happens", "does", "is it", "can", "should", "when", "which", "kya", "kyun", "kaise", "kab", "kya hota", "kya fayda", "kya nuksan", "sahi hai", "theek hai"]

def is_question_signal(text):
    t=clean(text).lower()
    return "?" in t or any(t.startswith(q+" ") or (" "+q+" ") in t for q in QUESTION_STARTERS)

def question_strength(text):
    t=clean(text).lower(); score=0
    if "?" in t: score += 25
    if any(q in t for q in QUESTION_STARTERS): score += 20
    if any(k in t for k in ["problem","symptom","why","how","benefit","harm","safe","work","result","pain","pet","weight","sleep","period","hair","skin","gas","acidity"]): score += 15
    if len(t.split()) >= 5: score += 10
    return min(50,score)

def people_signal_score(x):
    t=text_of(x); q=question_strength(t); score=q
    if x.get("source"," ").startswith("People"): score += 25
    if x.get("source")=="Google Trends India": score += 20
    if x.get("source")=="YouTube": score += 10
    india,_=india_relevance(t); score += 15 if india=="HIGH" else 8 if india=="MEDIUM" else 0
    return min(100,score)

def classify(raw, source, lane):
    x={"source":source,"lane":lane,"title":raw.get("title","") or "","url":raw.get("url","") or "","description":raw.get("description","") or "","published":raw.get("published","") or ""}
    if raw.get("video_id"): x["video_id"]=raw.get("video_id")
    eco=ecosystem_match(text_of(x)); x["eco"]=eco
    x["india_label"],x["india_points"]=india_relevance(text_of(x))
    x["opportunity_score"]=discovery_score(x)
    x["shoot_clues"]=shoot_design(x)
    x["type"]="RESEARCH" if lane=="RESEARCH" else "VIDEO OPPORTUNITY"
    x["question_signal"]=is_question_signal(text_of(x))
    x["question_strength"]=question_strength(text_of(x))
    x["people_signal_score"]=people_signal_score(x)
    x["uid"]=make_uid(source,x["url"],x["title"])
    return x

# -----------------------------
# PERSISTENT CHANGE DETECTION
# -----------------------------
def persist(items):
    con=db(); ts=iso(now()); new=[]; changed=[]
    for x in items:
        row=con.execute("SELECT * FROM signals WHERE uid=?",(x["uid"],)).fetchone()
        if not row:
            status="NEW"
            con.execute("INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",(x["uid"],x["source"],x["title"],x["url"],x.get("published"),ts,ts,1,x["opportunity_score"],x["eco"]["score"],status,json.dumps(x)))
            new.append(x)
        else:
            delta=x["opportunity_score"]-row["last_opportunity"]
            status="ACCELERATING" if delta>=15 else "RISING" if delta>=5 else "STABLE"
            con.execute("UPDATE signals SET last_seen=?,seen_count=seen_count+1,last_opportunity=?,last_ecosystem=?,status=?,raw_json=? WHERE uid=?",(ts,x["opportunity_score"],x["eco"]["score"],status,json.dumps(x),x["uid"]))
            x["previous_score"]=row["last_opportunity"]; x["status"]=status
            if status!="STABLE": changed.append(x)
            continue
        x["status"]=status
    con.execute("INSERT INTO scans(ts,source_counts,new_count,changed_count) VALUES(?,?,?,?,?)" if False else "INSERT INTO scans(ts,source_counts,new_count,changed_count) VALUES(?,?,?,?)",(ts,"{}",len(new),len(changed)))
    con.commit(); con.close(); return new,changed

# -----------------------------
# SCAN ORCHESTRATION
# -----------------------------
def mutated_queries(scan_number, trend_titles):
    rng=random.Random(scan_number*104729)
    base=[]
    for t in trend_titles[:12]:
        t=clean(t)
        if t: base += [t, t+" experiment", t+" why", t+" India"]
    shuffled=list(DISCOVERY_SEEDS); rng.shuffle(shuffled)
    base += shuffled[:14]
    # mutation: append a behaviour lens; each scan chooses a different lens mix.
    lens=list(BEHAVIOUR_EXPANDERS); rng.shuffle(lens)
    out=[]
    for q in base:
        if q not in out: out.append(q)
        if len(out)>=18: break
    # add 4 mutated combinations not previously fixed.
    for q in shuffled[:8]:
        m=q+" "+lens[rng.randrange(len(lens))]
        if m not in out: out.append(m)
        if len(out)>=22: break
    return out

def run_scan(api_key, scan_number):
    counts={}; all_items=[]
    trends=google_trends(); counts["Google Trends India"]=len(trends)
    trend_titles=[x.get("title","") for x in trends]
    all_items += [classify(x,"Google Trends India","DISCOVERY") for x in trends[:40]]
    queries=mutated_queries(scan_number,trend_titles)

    # Discovery lanes. These do not depend on the ecosystem dictionary.
    news=[]; yt=[]
    for q in queries[:12]: news += [classify(x,"Google News India","DISCOVERY") for x in google_news(q)[:5]]
    counts["Google News India"]=len(news); all_items += news
    if api_key:
        # Rotate modes to avoid repeatedly asking the same YouTube question.
        modes=[("date",24),("date",72),("relevance",72),("viewCount",72)]
        for i,q in enumerate(queries[:6]):
            order,hours=modes[(scan_number+i)%len(modes)]
            yt += [classify(x,"YouTube","DISCOVERY") for x in youtube(q,api_key,hours,order)[:8]]
    counts["YouTube"]=len(yt); all_items += yt

    # PEOPLE SEARCHING: autocomplete + fresh India Trends are question-language signals.
    search_signals=[]
    search_bases=[
        "health kya", "pet kyun", "pet kaise", "weight kaise", "sleep kyun", "gas kyun", "acidity kyun",
        "back pain kyun", "joint pain kyun", "hair fall kyun", "skin problem kaise", "period pain kyun",
        "sugar kaise control", "bp kaise", "fatty liver kya", "gut health kaise", "detox kya", "ayurveda kya",
        "water kab pina chahiye", "khana khane ke baad kya hota", "subah kya karna chahiye"
    ]
    # Every current Indian trend creates its own question family.
    for tt in trend_titles[:10]:
        tt=clean(tt)
        if tt:
            search_bases += [tt+" kya", tt+" kyun", tt+" kaise", tt+" does it work", tt+" benefits", tt+" side effects"]
    rng=random.Random(scan_number*7919); rng.shuffle(search_bases)
    for q in search_bases[:10]:
        search_signals += [classify(x,"People Searching","PEOPLE_SEARCH") for x in google_suggest(q)[:8]]
    # Include actual current Trends items as a separate search-intent lane.
    search_signals += [classify(x,"Google Trends India","PEOPLE_SEARCH") for x in trends[:40]]
    counts["People Searching"]=len(search_signals); all_items += search_signals

    # PEOPLE TALKING: YouTube viewer comments on discovered videos + public Reddit discussions.
    conversations=[]
    if api_key:
        yt_for_comments=[x for x in yt if x.get("video_id")][:5]
        for vx in yt_for_comments:
            conversations += [classify(c,"People Talking (YouTube comments)","PEOPLE_TALKING") for c in youtube_comments(vx.get("video_id"),api_key,25)]
    reddit_queries=["India health wellness", "India ayurveda", "India digestion bloating", "India sleep", "India weight loss", "India fitness health", "Indian home remedy"]
    for q in reddit_queries[:5]:
        conversations += [classify(x,"People Talking (Reddit)","PEOPLE_TALKING") for x in reddit_search(q)[:12]]
    counts["People Talking"]=len(conversations); all_items += conversations

    # Research lane is separate and is NEVER promoted directly to video discovery.
    research_queries=["sleep thermoregulation","gut digestion bloating","breathing autonomic","hydration electrolytes","movement balance proprioception","food fermentation","circadian light","interoception"]
    pm=[]; ep=[]; cr=[]
    for q in research_queries[:6]:
        pm += [classify(x,"PubMed","RESEARCH") for x in pubmed(q,30)[:5]]
    for q in research_queries[:5]:
        ep += [classify(x,"Europe PMC","RESEARCH") for x in europe_pmc(q,45)[:5]]
    for q in research_queries[:4]:
        cr += [classify(x,"Crossref","RESEARCH") for x in crossref(q,45)[:5]]
    counts["PubMed"]=len(pm); counts["Europe PMC"]=len(ep); counts["Crossref"]=len(cr)
    all_items += pm+ep+cr

    uniq={}
    for x in all_items:
        if not x["title"]: continue
        if x["uid"] not in uniq or x["opportunity_score"]>uniq[x["uid"]]["opportunity_score"]: uniq[x["uid"]]=x
    items=list(uniq.values())
    new,changed=persist(items)
    # Only discovery lanes can populate video-opportunity tabs.
    discovery=[x for x in items if x["lane"]!="RESEARCH"]
    research=[x for x in items if x["lane"]=="RESEARCH"]
    return discovery,research,new,changed,counts

# -----------------------------
# UI
# -----------------------------
st.set_page_config(page_title="MEDIMANCH INTELLIGENCE RADAR V9",layout="wide")
with st.sidebar:
    st.header("🌐 SOURCE LANES")
    st.caption("Discovery and research are intentionally separate.")
    st.markdown("[🔎 Google Trends India](https://trends.google.com/trending?geo=IN)")
    st.markdown("[📰 Google News India](https://news.google.com/?hl=en-IN&gl=IN&ceid=IN:en)")
    st.markdown("[▶️ YouTube](https://www.youtube.com/)")
    st.markdown("[🔍 People Searching](https://trends.google.com/trending?geo=IN)")
    st.markdown("[💬 People Talking](https://www.reddit.com/search/?q=health%20india)")
    st.markdown("[📚 PubMed](https://pubmed.ncbi.nlm.nih.gov/)")
    st.markdown("[🧬 Europe PMC](https://europepmc.org/)")
    st.markdown("[📖 Crossref](https://search.crossref.org/)")
    st.divider()
    st.subheader("🔍 MANUAL DISCOVERY")
    manual_q=st.text_input("Search a signal",placeholder="e.g. unusual Indian sleep habit")
    if st.button("Search Discovery Sources",use_container_width=True) and manual_q.strip():
        m=[]
        for item in google_news(manual_q.strip())[:15]: m.append(classify(item,"Google News India","DISCOVERY"))
        key=st.secrets.get("YOUTUBE_API_KEY",os.getenv("YOUTUBE_API_KEY",""))
        if key:
            m += [classify(item,"YouTube","DISCOVERY") for item in youtube(manual_q.strip(),key,72,"relevance")[:10]]
        st.session_state.manual=m
    if st.button("Clear manual results",use_container_width=True): st.session_state.manual=[]
    st.divider()
    st.subheader("📡 LIVE STATUS")
    for name in ["Google Trends India","Google News India","YouTube","People Searching","People Talking (YouTube comments)","People Talking (Reddit)","PubMed","Europe PMC","Crossref"]:
        if name not in SOURCE_STATUS: st.write(f"⚪ {name}: not scanned")
        elif SOURCE_STATUS[name]: st.write(f"🟢 {name}: connected")
        else:
            st.write(f"🔴 {name}: error")
            st.caption(SOURCE_ERRORS.get(name,"Unknown error")[:220])

st.title("🔥 MEDIMANCH INTELLIGENCE RADAR V9 — DISCOVERY + ECOSYSTEM")
st.caption("DISCOVER FIRST → SCORE VIDEO OPPORTUNITY → THEN MATCH MEDIMANCH ECOSYSTEM → DESIGN THE SHOOT")

api_key=st.secrets.get("YOUTUBE_API_KEY",os.getenv("YOUTUBE_API_KEY",""))
con=db(); scan_count=con.execute("SELECT COUNT(*) FROM scans").fetchone()[0]; con.close()
cols=st.columns(5)
cols[0].metric("Scans",scan_count); cols[1].metric("YouTube", "CONNECTED" if api_key else "NOT CONNECTED"); cols[2].metric("Memory","ON"); cols[3].metric("Discovery","INTERNET FIRST"); cols[4].metric("Version",APP_VERSION)

if "manual" not in st.session_state: st.session_state.manual=[]
if st.session_state.manual:
    st.subheader("🔎 MANUAL DISCOVERY RESULTS")
    for x in st.session_state.manual[:20]:
        st.markdown(f"**{x['title']}** — {x['source']} — {x['eco']['state']} | [Open]({x['url']})")
    st.divider()

if st.button("🔄 RUN FRESH DISCOVERY SCAN",type="primary",use_container_width=True):
    with st.spinner("Finding fresh signals across independent source lanes and mapping them only after discovery…"):
        discovery,research,new,changed,counts=run_scan(api_key,scan_count+1)
        st.session_state.discovery=discovery; st.session_state.research=research; st.session_state.new=new; st.session_state.changed=changed; st.session_state.counts=counts
    st.success(f"Fresh scan complete: {len(new)} NEW, {len(changed)} CHANGED/RISING")

discovery=st.session_state.get("discovery",[]); research=st.session_state.get("research",[]); new=st.session_state.get("new",[]); changed=st.session_state.get("changed",[])

tabs=st.tabs(["🔥 VIDEO OPPORTUNITY","🔍 PEOPLE SEARCHING","💬 PEOPLE TALKING","❓ QUESTIONS","🆕 NEW","⚡ MOMENTUM","🎥 SHOOT DESIGN","🧬 ECOSYSTEM MATCH","🧭 UNKNOWN","▶️ YOUTUBE","📰 NEWS","🔬 RESEARCH","🗃️ MEMORY","⚙️ STATUS"])

def opportunity_card(x, detailed=False):
    eco=x["eco"]
    st.markdown(f"### {x.get('status','SIGNAL')} · {x['title']}")
    st.write(f"**Source:** {x['source']}  |  **Video Opportunity:** {x['opportunity_score']}/100  |  **People Signal:** {x.get('people_signal_score',0)}/100  |  **India relevance:** {x['india_label']}")
    if x.get("question_signal"):
        st.markdown(f"**❓ Question signal:** YES  |  **Question strength:** {x.get('question_strength',0)}/50")
    st.write("**Why this is interesting:** discovery is independent of the Medimanch ecosystem; ecosystem matching is enrichment only.")
    if x.get("description"): st.caption(x["description"][:500])
    st.markdown(f"**🇮🇳 India fit:** {x['india_label']}")
    st.markdown(f"**🧬 Medimanch ecosystem:** {eco['state']}  |  **Match:** {eco['score']}/100")
    if eco["state"]=="MATCHED": st.write(f"**Pillar:** {eco['pillar']}  |  **Concept:** {eco['concept']}  |  **Mechanism:** {eco['mechanism']}")
    elif eco["state"]=="POSSIBLE": st.write(f"**Possible route:** {eco['concept']}  |  {eco['why']}")
    else: st.write(f"**Why unmapped:** {eco['why']} This is not rejected; it may represent a new concept opportunity.")
    st.markdown("**🎥 SHOOT DESIGN CLUES**")
    for clue in x["shoot_clues"]: st.write("• "+clue)
    if x.get("url"): st.markdown(f"[Open original source ↗]({x['url']})")
    st.divider()

with tabs[0]:
    arr=sorted([x for x in discovery if x["opportunity_score"]>=45 and x.get("lane") not in ["PEOPLE_SEARCH","PEOPLE_TALKING"]],key=lambda z:z["opportunity_score"],reverse=True)[:30]
    st.caption("Core video-discovery signals: fresh public activity, visual behaviour, questions and India relevance. Ecosystem matching does not inflate this score.")
    if not arr: st.info("No high-confidence video opportunities in this scan. Try another fresh scan or use Manual Discovery.")
    for x in arr: opportunity_card(x)
with tabs[1]:
    arr=sorted([x for x in discovery if x.get("lane")=="PEOPLE_SEARCH"],key=lambda z:(z.get("people_signal_score",0),z.get("question_strength",0)),reverse=True)[:50]
    st.caption("🔍 What people are typing/searching: Google Trends India + Google autocomplete. Autocomplete reveals query language and related questions; it is NOT a search-volume measurement.")
    for x in arr: opportunity_card(x)
with tabs[2]:
    arr=sorted([x for x in discovery if x.get("lane")=="PEOPLE_TALKING"],key=lambda z:(z.get("people_signal_score",0),z.get("question_strength",0)),reverse=True)[:50]
    st.caption("💬 What people are discussing: public YouTube viewer comments and Reddit discussions. These are conversation signals, not population estimates.")
    for x in arr: opportunity_card(x)
with tabs[3]:
    arr=sorted([x for x in discovery if x.get("question_signal")],key=lambda z:(z.get("question_strength",0)+z.get("people_signal_score",0),z.get("opportunity_score",0)),reverse=True)[:50]
    st.caption("❓ The strongest actual questions extracted from search-language and public conversations. This is the feed to mine for 'people are concerned about this right now'.")
    for x in arr: opportunity_card(x)
with tabs[4]:
    for x in sorted([x for x in new if x["lane"]!="RESEARCH"],key=lambda z:z["opportunity_score"],reverse=True)[:40]: opportunity_card(x)
with tabs[5]:
    for x in sorted([x for x in changed if x["lane"]!="RESEARCH"],key=lambda z:z["opportunity_score"],reverse=True)[:40]: opportunity_card(x)
with tabs[6]:
    arr=sorted([x for x in discovery if x.get("lane") not in ["PEOPLE_SEARCH","PEOPLE_TALKING"]],key=lambda z:z["opportunity_score"],reverse=True)[:30]
    st.subheader("🎥 What could actually be shot?")
    for x in arr: opportunity_card(x)
with tabs[7]:
    st.caption("Second-stage matching only. It does not control discovery.")
    for state in ["MATCHED","POSSIBLE","UNMAPPED"]:
        st.markdown(f"### {state}")
        arr=sorted([x for x in discovery if x["eco"]["state"]==state],key=lambda z:(z["eco"]["score"],z["opportunity_score"]),reverse=True)[:15]
        for x in arr: opportunity_card(x)
with tabs[8]:
    arr=[x for x in discovery if x["eco"]["state"]=="UNMAPPED"]
    st.caption("Unknown does NOT mean useless. These are discovery signals for which the current Medimanch map has no direct route.")
    for x in sorted(arr,key=lambda z:z["opportunity_score"],reverse=True)[:30]: opportunity_card(x)
with tabs[9]:
    arr=[x for x in discovery if x["source"]=="YouTube"]
    st.write(f"YouTube discovery results: {len(arr)}")
    for x in sorted(arr,key=lambda z:z["opportunity_score"],reverse=True)[:30]: opportunity_card(x)
with tabs[10]:
    arr=[x for x in discovery if x["source"]=="Google News India"]
    st.write(f"Google News discovery results: {len(arr)}")
    for x in sorted(arr,key=lambda z:z["opportunity_score"],reverse=True)[:30]: opportunity_card(x)
with tabs[11]:
    st.caption("Research is evidence discovery. It is NOT automatically a shoot recommendation.")
    for x in sorted(research,key=lambda z:z["published"],reverse=True)[:40]: opportunity_card(x)
with tabs[12]:
    con=db(); rows=con.execute("SELECT source,title,last_seen,seen_count,last_opportunity,last_ecosystem,status FROM signals ORDER BY last_seen DESC LIMIT 200").fetchall(); con.close(); st.dataframe([dict(r) for r in rows],use_container_width=True)
with tabs[13]:
    st.write("Last source counts:",st.session_state.get("counts",{}))
    st.write("The radar now has separate discovery, people-search, people-conversation and research lanes. Ecosystem state is MATCHED / POSSIBLE / UNMAPPED; NULL is no longer used.")
    for name,error in SOURCE_ERRORS.items(): st.error(f"{name}: {error[:500]}")
    st.caption("People Searching = query-language signals, not exact search-volume claims. People Talking = public discussion signals, not a representative survey of India.")
