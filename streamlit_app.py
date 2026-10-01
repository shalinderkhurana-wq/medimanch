from pathlib import Path
import os, re, json, sqlite3, hashlib, random
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
import xml.etree.ElementTree as ET
import requests
import streamlit as st

APP_VERSION = "V11.1"
DB_PATH = os.environ.get("MEDIMANCH_DB", str(Path(__file__).with_name("medimanch_radar_v11.db")))
HEADERS = {"User-Agent": "Medimanch-Professional-Radar/11.0 (research discovery)"}
SOURCE_STATUS, SOURCE_ERRORS = {}, {}

# ============================================================
# MEDIMANCH ONLY: the radar does not discover outside these 4 pillars.
# ============================================================
ECOSYSTEM = {
    "Energy": {
        "concepts": {
            "Movement & Performance": ["walking","backward walking","walking backwards","stairs","squat","exercise","fitness","mobility","flexibility","balance","coordination","proprioception","muscle fatigue","recovery after exercise"],
            "Breathing Physiology": ["breathing","breathwork","nasal breathing","slow breathing","breath hold","pranayama","respiration"],
            "Body-Performance Signals": ["fatigue","energy","stamina","focus","alertness","performance","heart rate","pulse"]
        }
    },
    "Gut": {
        "concepts": {
            "Gut Mechanics": ["bloating","pet phoolna","pet fulna","distension","stomach movement","gut movement","motility","bowel movement","constipation","gas","digestion","indigestion"],
            "Gut-Brain / Appetite": ["hunger","bhukh","fullness","pet bhara","appetite","gut brain","vagus","interoception","food craving"],
            "Food Transformation": ["fermentation","fermented","sprouting","sprouted","soaking","soaked","cooking","idli","dosa","kanji","dahi","curd","roti","rice","dal","meal timing"]
        }
    },
    "Hydration": {
        "concepts": {
            "Fluid Balance": ["hydration","pani","water","dehydration","thirst","fluid","electrolyte","ORS","sweat","urine","mineral water"],
            "Heat & Hydration": ["heat","garmi","heatwave","summer","cooling","body temperature","thermoregulation","heat exposure","cold exposure"],
            "Hydration Behaviour": ["water before meal","water after meal","water on empty stomach","pani kab pina","morning water","hydration routine"]
        }
    },
    "Recovery": {
        "concepts": {
            "Sleep & Circadian": ["sleep","neend","insomnia","bedtime","circadian","body clock","morning light","sunlight","sleep routine","night routine"],
            "Stress & Autonomic Recovery": ["stress","tension","relaxation","calm","autonomic","heart rate variability","hrv","breathing for stress","overthinking"],
            "Thermoregulation & Recovery": ["body temperature","thermoregulation","cooling","heat loss","cold exposure","ice bath","hot bath","warm bath","feet cold","temperature before sleep"],
            "Pain / Sensorimotor Recovery": ["neck pain","back pain","spondylitis","posture","shoulder pain","mobility","balance","sensory conflict","vestibular"]
        }
    }
}

INDIA_TERMS = ["india","indian","hindi","hinglish","desi","ayurveda","ayurvedic","naturopathy","yoga","pranayama","kadha","churan","dahi","lassi","roti","dal","rice","chai","poha","paratha","haldi","ajwain","saunf","jeera","methi","tulsi","neem","garmi","monsoon"]
QUESTION_TERMS = ["why","why does","how","what happens","does it work","is it safe","benefit","harm","side effect","proof","science","kya","kyun","kaise","kab","kya hota","kya fayda","kya nuksan","sahi hai","theek hai","kyun hota","kaise kare"]
VISUAL_TERMS = ["challenge","experiment","test","before after","compare","comparison","measure","reaction","trying","people try","routine","walk","eat","drink","sleep","breathe","hold","soak","sprout","cook","apply","temperature","balance","timer","scale","pulse","heart rate"]
NOISE_TERMS = ["celebrity","movie","song","trailer","gaming","politics","election","crypto","stock","giveaway","serial","actor","actress"]

# Discovery queries are generated ONLY from the ecosystem, then mutated.
QUERY_PATTERNS = [
    "{term} India", "{term} Hindi", "{term} kya", "{term} kyun", "{term} kaise", "{term} what happens",
    "{term} experiment", "{term} challenge", "{term} test", "{term} before after", "{term} science",
    "{term} people trying", "{term} viral India", "{term} routine India"
]

MECHANISMS = {
    "Movement & Performance":"movement, balance, proprioception and motor control",
    "Breathing Physiology":"respiratory control, gas exchange and autonomic regulation",
    "Body-Performance Signals":"measurable body-performance signals and recovery",
    "Gut Mechanics":"motility, distension, movement and sensory feedback in the digestive tract",
    "Gut-Brain / Appetite":"gut signals, appetite, fullness and brain-body communication",
    "Food Transformation":"physical and chemical changes created by preparation, fermentation or timing",
    "Fluid Balance":"fluid intake/loss, thirst, electrolytes and osmoregulation",
    "Heat & Hydration":"heat loss, sweating, temperature regulation and fluid balance",
    "Hydration Behaviour":"timing and context of fluid intake",
    "Sleep & Circadian":"sleep timing, light, temperature and circadian regulation",
    "Stress & Autonomic Recovery":"autonomic regulation, stress response and recovery behaviour",
    "Thermoregulation & Recovery":"heat transfer, skin blood flow and core temperature",
    "Pain / Sensorimotor Recovery":"sensory input, movement, posture and pain-related behaviour"
}

# ============================================================
# Utilities / DB
# ============================================================
def now(): return datetime.now(timezone.utc)
def iso(dt): return dt.astimezone(timezone.utc).isoformat()
def clean(s): return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()
def make_uid(source, url, title): return hashlib.sha1((source+"|"+url+"|"+title.lower().strip()).encode()).hexdigest()[:32]

def parse_date(v):
    if not v: return None
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(v).astimezone(timezone.utc)
    except Exception:
        try: return datetime.fromisoformat(v.replace("Z","+00:00")).astimezone(timezone.utc)
        except Exception: return None

def db():
    con=sqlite3.connect(DB_PATH); con.row_factory=sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS signals(
        uid TEXT PRIMARY KEY, source TEXT, title TEXT, url TEXT, published TEXT,
        first_seen TEXT, last_seen TEXT, seen_count INTEGER DEFAULT 1,
        score REAL, pillar TEXT, concept TEXT, status TEXT, raw_json TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS scans(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, counts TEXT)""")
    con.commit(); return con

def text_of(x): return clean((x.get("title","")+" "+x.get("description","")).lower())

def age_hours(v):
    d=parse_date(v)
    if not d: return 9999
    return max(0,(now()-d).total_seconds()/3600)

# ============================================================
# Source collectors
# ============================================================
def rss(url, source):
    try:
        r=requests.get(url,headers=HEADERS,timeout=18); r.raise_for_status(); root=ET.fromstring(r.content); out=[]
        for item in root.findall(".//item"):
            title=clean(item.findtext("title")); link=clean(item.findtext("link")); desc=clean(item.findtext("description")); pub=clean(item.findtext("pubDate"))
            if title and link: out.append({"title":title,"url":link,"description":desc,"published":pub})
        SOURCE_STATUS[source]=True; SOURCE_ERRORS.pop(source,None); return out
    except Exception as e:
        SOURCE_STATUS[source]=False; SOURCE_ERRORS[source]=str(e); return []

def trends_india():
    return rss("https://trends.google.com/trending/rss?geo=IN&hl=en-IN","Google Trends India")

def news(query):
    return rss("https://news.google.com/rss/search?q="+quote(query)+"&hl=en-IN&gl=IN&ceid=IN:en","Google News India")

def suggest(query):
    try:
        r=requests.get("https://suggestqueries.google.com/complete/search",params={"client":"firefox","hl":"en-IN","q":query},headers=HEADERS,timeout=12); r.raise_for_status(); data=r.json()
        out=[]
        for q in (data[1] if isinstance(data,list) and len(data)>1 else []):
            out.append({"title":q,"url":"https://www.google.com/search?q="+quote(q),"description":"Google query suggestion","published":iso(now())})
        SOURCE_STATUS["Google Query Suggestions"]=True; return out
    except Exception as e:
        SOURCE_STATUS["Google Query Suggestions"]=False; SOURCE_ERRORS["Google Query Suggestions"]=str(e); return []

def youtube(query,key,mode="fresh"):
    if not key:
        SOURCE_STATUS["YouTube"]=False; SOURCE_ERRORS["YouTube"]="YOUTUBE_API_KEY missing"; return []
    hours={"fresh":24,"rising":72,"relevance":168}.get(mode,72)
    order={"fresh":"date","rising":"viewCount","relevance":"relevance"}.get(mode,"relevance")
    after=(now()-timedelta(hours=hours)).isoformat().replace("+00:00","Z")
    p={"part":"snippet","q":query,"type":"video","maxResults":15,"order":order,"publishedAfter":after,"regionCode":"IN","relevanceLanguage":"hi","key":key}
    try:
        r=requests.get("https://www.googleapis.com/youtube/v3/search",params=p,timeout=18); r.raise_for_status(); data=r.json(); out=[]
        for it in data.get("items",[]):
            vid=it.get("id",{}).get("videoId"); sn=it.get("snippet",{})
            if vid: out.append({"title":sn.get("title",""),"url":"https://www.youtube.com/watch?v="+vid,"video_id":vid,"description":sn.get("description",""),"published":sn.get("publishedAt","")})
        SOURCE_STATUS["YouTube"]=True; SOURCE_ERRORS.pop("YouTube",None); return out
    except Exception as e:
        SOURCE_STATUS["YouTube"]=False; SOURCE_ERRORS["YouTube"]=str(e); return []

def yt_comments(vid,key):
    if not key: return []
    try:
        p={"part":"snippet","videoId":vid,"maxResults":50,"order":"relevance","textFormat":"plainText","key":key}
        r=requests.get("https://www.googleapis.com/youtube/v3/commentThreads",params=p,timeout=15); r.raise_for_status(); out=[]
        for it in r.json().get("items",[]):
            s=it.get("snippet",{}).get("topLevelComment",{}).get("snippet",{}); t=clean(s.get("textDisplay",""))
            if t: out.append({"title":t,"url":"https://www.youtube.com/watch?v="+vid,"description":"Viewer comment","published":s.get("publishedAt","")})
        SOURCE_STATUS["YouTube Comments"]=True; return out
    except Exception as e:
        SOURCE_STATUS["YouTube Comments"]=False; SOURCE_ERRORS["YouTube Comments"]=str(e); return []

def reddit(query):
    try:
        r=requests.get("https://www.reddit.com/search.json",params={"q":query,"sort":"new","t":"week","limit":25},headers={"User-Agent":"MedimanchProfessionalRadar/11.0"},timeout=15); r.raise_for_status(); out=[]
        for c in r.json().get("data",{}).get("children",[]):
            d=c.get("data",{}); title=clean(d.get("title")); url=d.get("url") or ("https://www.reddit.com"+d.get("permalink","") if d.get("permalink") else "")
            if title and url: out.append({"title":title,"url":url,"description":clean(d.get("selftext",""))[:1200],"published":datetime.fromtimestamp(d.get("created_utc",0),tz=timezone.utc).isoformat() if d.get("created_utc") else iso(now())})
        SOURCE_STATUS["Public Discussions"]=True; return out
    except Exception as e:
        SOURCE_STATUS["Public Discussions"]=False; SOURCE_ERRORS["Public Discussions"]=str(e); return []

def pubmed(query,days=30):
    try:
        p={"db":"pubmed","term":f"({query}) AND humans[filter]","retmode":"json","retmax":12,"reldate":days,"datetype":"pdat","sort":"pub_date"}
        r=requests.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",params=p,timeout=18); r.raise_for_status(); ids=r.json().get("esearchresult",{}).get("idlist",[])
        if not ids: SOURCE_STATUS["PubMed"]=True; return []
        r=requests.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",params={"db":"pubmed","id":",".join(ids),"retmode":"json"},timeout=18); r.raise_for_status(); data=r.json().get("result",{}); out=[]
        for pid in ids:
            d=data.get(pid,{})
            out.append({"title":d.get("title",""),"url":f"https://pubmed.ncbi.nlm.nih.gov/{pid}/","description":f"{d.get('fulljournalname','')} | {d.get('sortfirstauthor','')}","published":d.get("pubdate","")})
        SOURCE_STATUS["PubMed"]=True; return out
    except Exception as e:
        SOURCE_STATUS["PubMed"]=False; SOURCE_ERRORS["PubMed"]=str(e); return []

def europe(query,days=45):
    try:
        start=(now()-timedelta(days=days)).date().isoformat(); end=now().date().isoformat()
        p={"query":f"({query}) AND FIRST_PDATE:[{start} TO {end}]","format":"json","pageSize":12,"sort":"FIRST_PDATE_D DESC"}
        r=requests.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search",params=p,timeout=18); r.raise_for_status(); out=[]
        for d in r.json().get("resultList",{}).get("result",[]):
            pid=d.get("pmid"); out.append({"title":d.get("title",""),"url":f"https://pubmed.ncbi.nlm.nih.gov/{pid}/" if pid else f"https://europepmc.org/article/MED/{d.get('id','')}","description":f"{d.get('journalTitle','')} | {d.get('authorString','')}","published":d.get("firstPublicationDate","")})
        SOURCE_STATUS["Europe PMC"]=True; return out
    except Exception as e:
        SOURCE_STATUS["Europe PMC"]=False; SOURCE_ERRORS["Europe PMC"]=str(e); return []

# ============================================================
# Professional Medimanch filter / scoring
# ============================================================
def match_ecosystem(text):
    best=[]
    for pillar,p in ECOSYSTEM.items():
        for concept,terms in p["concepts"].items():
            hits=[t for t in terms if t in text]
            if hits: best.append((len(hits),pillar,concept,hits))
    if not best: return None
    best.sort(reverse=True)
    h,pillar,concept,hits=best[0]
    return {"pillar":pillar,"concept":concept,"hits":hits,"mechanism":MECHANISMS.get(concept,"")}

def india_score(text, source):
    h=sum(1 for t in INDIA_TERMS if t in text)
    base=12 if source in ["Google Trends India","Google News India"] else 0
    if h>=2: return min(30,base+18)
    if h==1: return base+10
    return base

def question_score(text):
    s=0
    if "?" in text: s+=25
    s+=min(30,sum(1 for q in QUESTION_TERMS if q in text)*4)
    return min(55,s)

def visual_score(text): return min(20,sum(1 for t in VISUAL_TERMS if t in text)*3)

def freshness_score(pub):
    h=age_hours(pub)
    return 35 if h<=6 else 30 if h<=12 else 26 if h<=24 else 20 if h<=48 else 12 if h<=96 else 5

def build_signal(raw,source,lane):
    x={"source":source,"lane":lane,"title":clean(raw.get("title","")),"url":raw.get("url","") or "","description":clean(raw.get("description","")),"published":raw.get("published","")}
    if raw.get("video_id"): x["video_id"]=raw["video_id"]
    t=text_of(x); eco=match_ecosystem(t)
    # STRICT: if it doesn't map to Medimanch, it is not a V11 result.
    if not eco: return None
    x["pillar"]=eco["pillar"]; x["concept"]=eco["concept"]; x["mechanism"]=eco["mechanism"]; x["matched_terms"]=eco["hits"]
    x["india_score"]=india_score(t,source)
    x["question_score"]=question_score(t)
    x["visual_score"]=visual_score(t)
    x["freshness_score"]=freshness_score(x["published"])
    x["source_bonus"]={"Google Trends India":15,"YouTube":14,"Google News India":10,"Google Query Suggestions":12,"YouTube Comments":13,"Public Discussions":13,"PubMed":7,"Europe PMC":6}.get(source,5)
    x["signal_kind"]="QUESTION" if x["question_score"]>=25 else "VISUAL BEHAVIOUR" if x["visual_score"]>=8 else "CLAIM / DISCUSSION" if any(k in t for k in ["benefit","proof","science","works","myth"]) else "TOPIC SIGNAL"
    # selection score is deliberately practical, not a generic virality score
    x["selection_score"] = min(100, round(0.30*x["freshness_score"] + 0.25*x["india_score"] + 0.18*x["visual_score"] + 0.12*x["question_score"] + 0.10*x["source_bonus"] + 0.05*min(20, len(x["matched_terms"])*5)))
    x["uid"]=make_uid(source,x["url"],x["title"])
    x["shoot"]=shoot_plan(x)
    return x

def shoot_plan(x):
    p=x["pillar"]; c=x["concept"]
    base=[f"SHOW: {x['matched_terms'][0]} as the real-world behaviour/object", "BASELINE: define one observable starting state", "CHANGE ONE VARIABLE: keep the test simple and repeatable", "DOCTOR EXPLAINS: connect the visible change to the mechanism; separate evidence from claim"]
    if p=="Gut": base[2]="COMPARE: meal size/timing/preparation or one safe behavioural variable; avoid disease-treatment claims"
    if p=="Hydration": base[2]="COMPARE: timing/context of fluid intake or heat-loss conditions; do not infer disease outcomes"
    if p=="Recovery": base[2]="COMPARE: timing/routine/temperature/light or one safe recovery variable"
    if p=="Energy": base[2]="COMPARE: movement/breathing/coordination condition while keeping other variables stable"
    return base

# ============================================================
# Memory + scan
# ============================================================
def persist(items):
    con=db(); ts=iso(now()); changed=[]
    for x in items:
        row=con.execute("SELECT * FROM signals WHERE uid=?",(x["uid"],)).fetchone()
        if not row:
            status="NEW"; seen=1; prev=None
            con.execute("INSERT INTO signals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(x["uid"],x["source"],x["title"],x["url"],x["published"],ts,ts,1,x["selection_score"],x["pillar"],x["concept"],status,json.dumps(x)))
        else:
            delta=x["selection_score"]-float(row["score"] or 0); status="ACCELERATING" if delta>=12 else "RISING" if delta>=5 else "STABLE"; prev=row["score"]; seen=int(row["seen_count"])+1
            con.execute("UPDATE signals SET last_seen=?,seen_count=?,score=?,pillar=?,concept=?,status=?,raw_json=? WHERE uid=?",(ts,seen,x["selection_score"],x["pillar"],x["concept"],status,json.dumps(x),x["uid"]))
            x["previous_score"]=prev
            if status!="STABLE": changed.append(x)
        x["status"]=status
    con.execute("INSERT INTO scans(ts,counts) VALUES(?,?)",(ts,json.dumps({"items":len(items)}))); con.commit(); con.close(); return changed

def query_space(scan_no):
    terms=[]
    for pillar,p in ECOSYSTEM.items():
        for concept,vals in p["concepts"].items():
            for term in vals[:5]: terms.append((pillar,concept,term))
    rng=random.Random(scan_no*9176); rng.shuffle(terms)
    out=[]
    for pillar,concept,term in terms[:18]:
        pattern=QUERY_PATTERNS[(scan_no+len(out))%len(QUERY_PATTERNS)]
        out.append(pattern.format(term=term))
    return list(dict.fromkeys(out))

def scan(key,scan_no):
    counts={}; raw=[]
    tr=trends_india(); counts["Google Trends India"]=len(tr)
    # Trends are hard-filtered through ecosystem after retrieval.
    for r in tr[:80]:
        x=build_signal(r,"Google Trends India","PUBLIC_SIGNAL")
        if x: raw.append(x)
    qs=query_space(scan_no)
    # Query language + questions, but ONLY ecosystem-derived queries.
    for q in qs[:10]:
        for r in suggest(q)[:8]:
            x=build_signal(r,"Google Query Suggestions","SEARCH_LANGUAGE")
            if x: raw.append(x)
    counts["Google Query Suggestions"]=sum(1 for x in raw if x["source"]=="Google Query Suggestions")
    for i,q in enumerate(qs[:10]):
        for r in news(q)[:6]:
            x=build_signal(r,"Google News India","PUBLIC_SIGNAL")
            if x: raw.append(x)
    counts["Google News India"]=sum(1 for x in raw if x["source"]=="Google News India")
    if key:
        modes=["fresh","rising","relevance"]
        for i,q in enumerate(qs[:7]):
            for r in youtube(q,key,modes[(scan_no+i)%3])[:10]:
                x=build_signal(r,"YouTube","PUBLIC_SIGNAL")
                if x: raw.append(x)
    counts["YouTube"]=sum(1 for x in raw if x["source"]=="YouTube")
    # Conversation signals: ecosystem-scoped Reddit + comments from discovered videos.
    for q in qs[:4]:
        for r in reddit(q+" India")[:10]:
            x=build_signal(r,"Public Discussions","PEOPLE_SIGNAL")
            if x: raw.append(x)
    counts["Public Discussions"]=sum(1 for x in raw if x["source"]=="Public Discussions")
    videos=[x for x in raw if x.get("video_id")][:4]
    if key:
        for v in videos:
            for r in yt_comments(v["video_id"],key)[:25]:
                x=build_signal(r,"YouTube Comments","PEOPLE_SIGNAL")
                if x: raw.append(x)
    counts["YouTube Comments"]=sum(1 for x in raw if x["source"]=="YouTube Comments")
    # Research lane: pillar-specific, latest human research. It is never mixed into video opportunity ranking.
    research=[]
    research_queries=[
        ("Energy","movement proprioception balance exercise performance"),("Energy","breathing autonomic respiration human"),
        ("Gut","bloating gut motility digestion human"),("Gut","fermentation food preparation gut microbiome human"),
        ("Hydration","hydration electrolytes heat fluid balance human"),("Hydration","thermoregulation hydration exercise human"),
        ("Recovery","sleep circadian light temperature human"),("Recovery","stress autonomic recovery breathing human")]
    for pillar,q in research_queries:
        for r in pubmed(q,21)[:6]:
            x=build_signal(r,"PubMed","RESEARCH")
            if x: x["pillar"]=pillar; research.append(x)
        for r in europe(q,35)[:5]:
            x=build_signal(r,"Europe PMC","RESEARCH")
            if x: x["pillar"]=pillar; research.append(x)
    counts["Research"]=len(research)
    # Deduplicate by URL/title fingerprint, preserving highest selection score.
    uniq={}
    for x in raw:
        if not x["title"]: continue
        k=x["url"] or x["title"].lower()
        if k not in uniq or x["selection_score"]>uniq[k]["selection_score"]: uniq[k]=x
    items=list(uniq.values())
    changed=persist(items)
    return items,research,changed,counts

# ============================================================
# UI: fast selection
# ============================================================
st.set_page_config(page_title="MEDIMANCH PROFESSIONAL RADAR V11",layout="wide")
key=st.secrets.get("YOUTUBE_API_KEY",os.getenv("YOUTUBE_API_KEY",""))
con=db(); scan_count=con.execute("SELECT COUNT(*) FROM scans").fetchone()[0]; con.close()

st.title("🔥 MEDIMANCH PROFESSIONAL RADAR V11")
st.caption("MEDIMANCH ECOSYSTEM ONLY · DISCOVER → FILTER → SELECT → SHOOT")

with st.sidebar:
    st.header("⚡ FAST SELECTION")
    pillar_filter=st.selectbox("Pillar",["ALL","Energy","Gut","Hydration","Recovery"])
    source_filter=st.selectbox("Source",["ALL","Google Trends India","Google Query Suggestions","YouTube","Google News India","Public Discussions","YouTube Comments"])
    signal_filter=st.selectbox("Signal",["ALL","QUESTION","VISUAL BEHAVIOUR","CLAIM / DISCUSSION","TOPIC SIGNAL"])
    min_score=st.slider("Minimum selection score",0,100,55,5)
    max_age=st.selectbox("Freshness",["ALL","6 HOURS","24 HOURS","3 DAYS","7 DAYS"])
    st.divider()
    st.markdown("### 🔗 SOURCE PAGES")
    st.markdown("[Google Trends India](https://trends.google.com/trending?geo=IN)")
    st.markdown("[Google News India](https://news.google.com/?hl=en-IN&gl=IN&ceid=IN:en)")
    st.markdown("[YouTube India](https://www.youtube.com/?gl=IN)")
    st.markdown("[PubMed](https://pubmed.ncbi.nlm.nih.gov/)")
    st.markdown("[Europe PMC](https://europepmc.org/)")
    st.divider()
    st.subheader("📡 LIVE SOURCES")
    for n in ["Google Trends India","Google Query Suggestions","Google News India","YouTube","Public Discussions","YouTube Comments","PubMed","Europe PMC"]:
        if n not in SOURCE_STATUS: st.write(f"⚪ {n}")
        elif SOURCE_STATUS[n]: st.write(f"🟢 {n}")
        else: st.write(f"🔴 {n}"); st.caption(SOURCE_ERRORS.get(n,"error")[:180])

if "items" not in st.session_state: st.session_state.items=[]
if "research" not in st.session_state: st.session_state.research=[]
if st.button("🔄 SCRAPE FRESH MEDIMANCH SIGNALS",type="primary",use_container_width=True):
    with st.spinner("Scraping current Indian signals, query language, public conversations and research — then hard-filtering to Medimanch…"):
        items,research,changed,counts=scan(key,scan_count+1)
        st.session_state.items=items; st.session_state.research=research; st.session_state.changed=changed; st.session_state.counts=counts
    st.success(f"Scrape complete · {len(items)} ecosystem signals · {len(changed)} rising/accelerating")

items=st.session_state.items
research=st.session_state.research

def filtered(arr):
    out=[]
    age_map={"6 HOURS":6,"24 HOURS":24,"3 DAYS":72,"7 DAYS":168}
    for x in arr:
        if pillar_filter!="ALL" and x.get("pillar")!=pillar_filter: continue
        if source_filter!="ALL" and x.get("source")!=source_filter: continue
        if signal_filter!="ALL" and x.get("signal_kind")!=signal_filter: continue
        if x.get("selection_score",0)<min_score: continue
        if max_age!="ALL" and age_hours(x.get("published"))>age_map[max_age]: continue
        out.append(x)
    return sorted(out,key=lambda z:(z.get("selection_score",0),z.get("freshness_score",0),z.get("question_score",0)),reverse=True)

# Simple, version-safe metric row. Avoid tuple-based rendering so older Streamlit
# runtimes cannot mis-handle the metric configuration.
energy_n = sum(1 for x in items if isinstance(x, dict) and x.get("pillar") == "Energy")
gut_n = sum(1 for x in items if isinstance(x, dict) and x.get("pillar") == "Gut")
hydration_n = sum(1 for x in items if isinstance(x, dict) and x.get("pillar") == "Hydration")
recovery_n = sum(1 for x in items if isinstance(x, dict) and x.get("pillar") == "Recovery")
youtube_state = "ON" if bool(key) else "OFF"
cols = st.columns(6)
cols[0].metric("Signals", int(len(items)))
cols[1].metric("Energy", int(energy_n))
cols[2].metric("Gut", int(gut_n))
cols[3].metric("Hydration", int(hydration_n))
cols[4].metric("Recovery", int(recovery_n))
cols[5].metric("YouTube", youtube_state)

tabs=st.tabs(["⚡ SELECT NOW","❓ PEOPLE QUESTIONS","🎥 SHOOTABLE","🔥 FRESH","📈 RISING","🧬 BY PILLAR","▶️ YOUTUBE","📰 NEWS","🔬 RESEARCH","🗃️ MEMORY","⚙️ STATUS"])

def card(x):
    age=age_hours(x.get("published")); age_txt=f"{age:.1f}h ago" if age<48 else f"{age/24:.1f}d ago"
    st.markdown(f"### {x.get('status','SIGNAL')} · {x['title']}")
    st.write(f"**{x['selection_score']}/100 SELECT** · {x['pillar']} → {x['concept']} · **{x['source']}** · {age_txt}")
    st.caption(f"Signal: {x['signal_kind']} · Freshness {x['freshness_score']} · India {x['india_score']} · Visual {x['visual_score']} · Question {x['question_score']}")
    st.write(f"**Why selected:** {', '.join(x['matched_terms'][:6])}")
    st.write(f"**Mechanism lane:** {x['mechanism']}")
    st.markdown("**🎥 SHOOT DESIGN**")
    for c in x["shoot"]: st.write("• "+c)
    if x.get("description"): st.caption(x["description"][:350])
    if x.get("url"): st.markdown(f"[Open source ↗]({x['url']})")
    st.divider()

with tabs[0]:
    st.caption("Only ecosystem-matched signals survive. This is the fast selection queue — not a general health feed.")
    for x in filtered(items)[:25]: card(x)
    if not filtered(items): st.info("No signal meets the current filters. Lower the score/freshness filter or run a fresh scrape.")
with tabs[1]:
    arr=[x for x in filtered(items) if x["question_score"]>=20]
    for x in arr[:25]: card(x)
with tabs[2]:
    arr=[x for x in filtered(items) if x["visual_score"]>=8]
    for x in arr[:25]: card(x)
with tabs[3]:
    arr=[x for x in filtered(items) if x["freshness_score"]>=26]
    for x in arr[:25]: card(x)
with tabs[4]:
    arr=[x for x in filtered(items) if x.get("status") in ["RISING","ACCELERATING"]]
    for x in arr[:25]: card(x)
with tabs[5]:
    for p in ["Energy","Gut","Hydration","Recovery"]:
        st.markdown(f"## {p}")
        arr=filtered([x for x in items if x["pillar"]==p])[:10]
        for x in arr: card(x)
with tabs[6]:
    arr=filtered([x for x in items if x["source"]=="YouTube"])
    for x in arr[:30]: card(x)
with tabs[7]:
    arr=filtered([x for x in items if x["source"]=="Google News India"])
    for x in arr[:30]: card(x)
with tabs[8]:
    st.caption("Research is kept separate. It can explain a selected signal; it cannot become a generic shoot recommendation by itself.")
    for x in sorted(research,key=lambda z:z.get("published",""),reverse=True)[:40]: card(x)
with tabs[9]:
    con=db(); rows=con.execute("SELECT source,title,last_seen,seen_count,score,pillar,concept,status FROM signals ORDER BY last_seen DESC LIMIT 250").fetchall(); con.close(); st.dataframe([dict(r) for r in rows],use_container_width=True)
with tabs[10]:
    st.write("Last scrape counts",st.session_state.get("counts",{}))
    for n,e in SOURCE_ERRORS.items(): st.error(f"{n}: {e[:500]}")
    st.info("V11 intentionally rejects anything that cannot be mapped to Energy, Gut, Hydration or Recovery. It does not show UNMAPPED/general-health results.")
