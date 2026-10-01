from pathlib import Path
import os, re, json, sqlite3, hashlib, random
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
import xml.etree.ElementTree as ET
import requests
import streamlit as st

DB_PATH = os.environ.get('MEDIMANCH_DB', str(Path(__file__).with_name('radar_memory.db')))
HEADERS={'User-Agent':'Medimanch-Intelligence-Radar/7.1'}
SOURCE_STATUS={}
SOURCE_ERRORS={}

SEEDS = [
 'body experiment','body test','strange health habit','people trying health','viral body challenge',
 'food trend digestion','gut health trend','fermented food trend','soaking food trend','meal timing trend',
 'sleep hack trend','sleep routine trend','bedtime experiment','morning light sleep','body temperature sleep',
 'breathing challenge','breathing technique trend','cold exposure trend','energy routine trend',
 'walking challenge','balance challenge','posture trend','mobility challenge','unusual exercise trend',
 'hydration trend','water challenge','electrolyte trend','mineral water trend',
 'traditional remedy trend','home remedy viral','ayurveda remedy trend','herbal drink trend','desi remedy trend',
 'habit challenge','dopamine detox trend','stress body experiment','focus routine trend','mind body trend'
]
MEDIMANCH_MAP={
 'backward walking':['backward walking','reverse walking','walking backwards','retro walking'],
 'balance':['balance challenge','one leg balance','balance test','vestibular','proprioception'],
 'breathing':['breathing','breathwork','breathing exercise','slow breathing','nasal breathing'],
 'cold exposure':['cold shower','ice bath','cold exposure','cold water'],
 'morning light':['morning sunlight','morning light','sunlight after waking','light exposure'],
 'sleep temperature':['body temperature sleep','cooling sleep','cold feet sleep','thermoregulation sleep'],
 'meal timing':['meal timing','early dinner','time restricted eating','eating window'],
 'fermentation':['fermented food','fermentation','kanji','kimchi','idli fermentation'],
 'soaking sprouting':['soaked food','sprouting','soaked nuts','sprouted grains'],
 'hydration':['hydration','water intake','electrolyte','mineral water'],
 'posture':['posture','neck posture','forward head','sitting posture'],
 'herbal remedies':['home remedy','herbal remedy','ayurvedic remedy','herbal drink','desi remedy'],
 'naturopathy':['naturopathy','mud pack','wet pack','hydrotherapy','natural therapy'],
 'interoception':['interoception','body signals','internal body sensation','gut feeling physiology'],
 'thermoregulation':['thermoregulation','body temperature','heat regulation','cooling body']
}
PILLARS={
"Energy":["energy","fatigue","metabolism","exercise","movement","performance","focus"],
"Gut":["gut","digestion","bloating","appetite","fermentation","microbiome","stomach","meal","food","bowel"],
"Hydration":["hydration","water","electrolyte","mineral","fluid","sweat","heat"],
"Recovery":["sleep","circadian","stress","breathing","relaxation","temperature","pain","posture","recovery"]}

CONCEPT_DETAILS={
"backward walking":("Movement & Balance","balance / proprioception","walking pattern, balance, sensory conflict"),
"balance":("Movement & Balance","balance / proprioception","one-leg test, sensory conflict, stability"),
"breathing":("Breathing Physiology","respiratory control","breathing rate, nasal/oral route, recovery"),
"cold exposure":("Thermoregulation","temperature regulation","skin temperature, shivering, blood flow"),
"morning light":("Sleep & Circadian","circadian timing","light exposure, timing, sleep routine"),
"sleep temperature":("Sleep & Circadian","thermoregulation","cooling, heat loss, sleep onset"),
"meal timing":("Digestion & Gut Mechanics","digestion / timing","meal window, hunger, post-meal changes"),
"fermentation":("Food Preparation & Bioavailability","food transformation","fermentation process, texture, gas, acidity"),
"soaking sprouting":("Food Preparation & Bioavailability","food preparation","soaking time, sprouting, texture"),
"hydration":("Hydration & Fluid Balance","fluid balance","water intake, urine colour, thirst, electrolytes"),
"posture":("Movement & Balance","sensorimotor control","head/neck position, balance, movement"),
"herbal remedies":("Traditional & Herbal Practices","traditional practice","preparation, ingredients, observable process"),
"naturopathy":("Traditional & Herbal Practices","naturopathy practice","application, temperature, compression, routine"),
"interoception":("Interoception","brain-body sensing","heartbeat/breath/body-signal awareness"),
"thermoregulation":("Thermoregulation","temperature regulation","heat loss, skin blood flow, core temperature")}

VISUAL_WORDS=['people doing','challenge','before after','experiment','test at home','demonstration','routine','trying','reaction','walk','hold','breathe','eat','drink','sleep','soak','sprout','ice','sunlight','posture']
NOISE=['celebrity','movie','song','gaming','politics','election','crypto','stock','giveaway','shorts compilation']

def db():
    con=sqlite3.connect(DB_PATH); con.row_factory=sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS signals(
      uid TEXT PRIMARY KEY, source TEXT, title TEXT, url TEXT, published TEXT, first_seen TEXT,
      last_seen TEXT, seen_count INTEGER DEFAULT 1, last_score REAL DEFAULT 0,
      kind TEXT, concept TEXT, raw_json TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS scans(
      id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, source_counts TEXT, new_count INTEGER, changed_count INTEGER)""")
    con.commit(); return con

def now(): return datetime.now(timezone.utc)
def iso(dt): return dt.astimezone(timezone.utc).isoformat()
def uid(source,url,title): return hashlib.sha1((source+'|'+url+'|'+title.lower().strip()).encode()).hexdigest()[:24]
def clean(s): return re.sub(r'\s+',' ', re.sub(r'<[^>]+>',' ',s or '')).strip()
def parse_date(s):
    if not s: return None
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(s).astimezone(timezone.utc)
    except Exception: return None

def rss(url, source_name='Feed'):
    try:
        r=requests.get(url,headers=HEADERS,timeout=15); r.raise_for_status(); root=ET.fromstring(r.content); out=[]
        SOURCE_STATUS[source_name]=True; SOURCE_ERRORS.pop(source_name,None)
        for item in root.findall('.//item'):
            title=clean(item.findtext('title')); link=clean(item.findtext('link')); desc=clean(item.findtext('description')); pub=clean(item.findtext('pubDate'))
            if title and link: out.append({'title':title,'url':link,'description':desc,'published':pub})
        return out
    except Exception as e:
        SOURCE_STATUS[source_name]=False; SOURCE_ERRORS[source_name]=str(e)
        return []

def google_trends(): return rss('https://trends.google.com/trending/rss?geo=IN&hl=en-IN','Google Trends')
def google_news(q): return rss('https://news.google.com/rss/search?q='+quote(q)+'&hl=en-IN&gl=IN&ceid=IN:en','Google News')

def youtube(q,api_key,hours=72,order="date"):
    if not api_key:
        SOURCE_STATUS["YouTube"]=False; SOURCE_ERRORS["YouTube"]="YOUTUBE_API_KEY missing in Streamlit Secrets."; return []
    after=(now()-timedelta(hours=hours)).isoformat().replace("+00:00","Z")
    params={"part":"snippet","q":q,"type":"video","maxResults":10,"order":order,"publishedAfter":after,"regionCode":"IN","relevanceLanguage":"hi","key":api_key}
    try:
        r=requests.get("https://www.googleapis.com/youtube/v3/search",params=params,timeout=18); r.raise_for_status(); data=r.json()
        out=[]
        for x in data.get("items",[]):
            vid=x.get("id",{}).get("videoId"); sn=x.get("snippet",{})
            if vid: out.append({"title":sn.get("title",""),"url":"https://www.youtube.com/watch?v="+vid,"description":sn.get("description",""),"published":sn.get("publishedAt","")})
        SOURCE_STATUS["YouTube"]=True; SOURCE_ERRORS.pop("YouTube",None); return out
    except Exception as e:
        SOURCE_STATUS["YouTube"]=False; SOURCE_ERRORS["YouTube"]=str(e); return []

def pubmed(q,days=14):
    try:
        params={"db":"pubmed","term":f"({q}) AND humans[filter]","retmode":"json","retmax":8,"reldate":days,"datetype":"pdat","sort":"pub_date"}
        r=requests.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",params=params,timeout=18); r.raise_for_status(); ids=r.json().get("esearchresult",{}).get("idlist",[])
        if not ids: SOURCE_STATUS["PubMed"]=True; return []
        r=requests.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",params={"db":"pubmed","id":",".join(ids),"retmode":"json"},timeout=18); r.raise_for_status(); data=r.json().get("result",{})
        out=[]
        for pid in ids:
            row=data.get(pid,{})
            out.append({"title":row.get("title",""),"url":"https://pubmed.ncbi.nlm.nih.gov/"+pid+"/","description":row.get("sortfirstauthor","")+" | "+row.get("fulljournalname",""),"published":row.get("pubdate","")})
        SOURCE_STATUS["PubMed"]=True; SOURCE_ERRORS.pop("PubMed",None); return out
    except Exception as e:
        SOURCE_STATUS["PubMed"]=False; SOURCE_ERRORS["PubMed"]=str(e); return []

def europe_pmc(q,days=30):
    try:
        start=(now()-timedelta(days=days)).date().isoformat(); end=now().date().isoformat()
        params={"query":f"({q}) AND FIRST_PDATE:[{start} TO {end}]","format":"json","pageSize":8,"sort":"FIRST_PDATE_D DESC"}
        r=requests.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search",params=params,timeout=18); r.raise_for_status(); data=r.json(); out=[]
        for row in data.get("resultList",{}).get("result",[]):
            pmid=row.get("pmid"); url="https://pubmed.ncbi.nlm.nih.gov/"+pmid+"/" if pmid else "https://europepmc.org/article/MED/"+str(row.get("id",""))
            out.append({"title":row.get("title",""),"url":url,"description":row.get("authorString","")+" | "+row.get("journalTitle",""),"published":row.get("firstPublicationDate","")})
        SOURCE_STATUS["Europe PMC"]=True; SOURCE_ERRORS.pop("Europe PMC",None); return out
    except Exception as e:
        SOURCE_STATUS["Europe PMC"]=False; SOURCE_ERRORS["Europe PMC"]=str(e); return []

def concept_match(text):
    t=text.lower(); best=[]
    for c,aliases in MEDIMANCH_MAP.items():
        hits=sum(1 for a in aliases if a in t)
        if hits: best.append((hits,c))
    return sorted(best,reverse=True)[0][1] if best else None

def ecosystem_route(text,concept):
    t=text.lower(); ps={p:sum(1 for w in ws if w in t) for p,ws in PILLARS.items()}; pillar=max(ps,key=ps.get) if max(ps.values()) else None
    d=CONCEPT_DETAILS.get(concept); return pillar,(d[0] if d else concept),(d[1] if d else None),(d[2] if d else None)

def clue_engine(x):
    c=x.get("concept"); t=(x["title"]+" "+x.get("description","")).lower()
    base={
    "backward walking":["walking path ko visually compare karo","balance/proprioception ko simple test se show karo","normal vs backward walking ka difference shoot karo"],
    "balance":["one-leg baseline test","vision vs proprioception conflict demonstration","before/after repeat test"],
    "breathing":["breathing rate/route ko visibly capture karo","breathing aur recovery ka measurable proxy","doctor mechanism explanation"],
    "cold exposure":["skin temperature/heat loss visual","blood-flow or shivering mechanism","timing vs intensity comparison"],
    "morning light":["morning light timing visual","body-clock timeline","morning vs late-day exposure comparison"],
    "sleep temperature":["body cooling timeline","heat-loss mechanism visual","sleep onset timing experiment"],
    "meal timing":["meal-to-body timeline","hunger/fullness observation","same food different timing comparison"],
    "fermentation":["day-by-day food transformation","texture/volume/acidity observation","traditional process + mechanism"],
    "hydration":["water vs electrolyte question","safe before/after observation","fluid-balance body model"],
    "posture":["head/neck position visual","balance change with posture","simple repeatable test"],
    "herbal remedies":["show preparation exactly","separate traditional claim from mechanism","identify measurable variable before making a claim"],
    "naturopathy":["show application process","identify temperature/compression variable","claim-to-mechanism test"],
    "interoception":["body-signal awareness test","heartbeat/breath perception","brain-body explanation"],
    "thermoregulation":["heat-loss pathway","skin blood-flow explanation","environment vs body response"]}.get(c,[])
    if not base: base=["People exactly kya kar rahe hain?","Is behaviour ka visible/measurable part kya hai?","Iska mechanism kya ho sakta hai?"]
    return base[:4]

def classify(raw,source):
    x={"source":source,"title":raw.get("title",""),"url":raw.get("url",""),"description":raw.get("description",""),"published":raw.get("published","")}
    text=x["title"]+" "+x["description"]; x["concept"]=concept_match(text); x["pillar"],x["universe"],x["mechanism"],x["visual"]=ecosystem_route(text,x["concept"]); x["clues"]=clue_engine(x)
    x["kind"]="research" if source in ["PubMed","Europe PMC"] else "visual_behaviour" if any(w in text.lower() for w in VISUAL_WORDS) else "discovery"
    x["score"]=score_signal(x); x["uid"]=uid(source,x["url"],x["title"]); return x

def score_signal(x):
    text=(x["title"]+" "+x.get("description","")).lower(); score=0; d=parse_date(x.get("published"))
    if d:
        age=(now()-d).total_seconds()/3600; score+=35 if age<=6 else 25 if age<=24 else 15 if age<=72 else 5
    score+=min(20,sum(2 for w in VISUAL_WORDS if w in text)); score-=min(25,sum(5 for w in NOISE if w in text)); score+=20 if x.get("concept") else 0; score+=10 if x.get("pillar") else 0; score+=5 if x.get("source") in ["YouTube","Google Trends IN"] else 0
    return max(0,min(100,score))

def persist(items):
    con=db(); new=[]; changed=[]; ts=iso(now())
    for x in items:
        row=con.execute('SELECT * FROM signals WHERE uid=?',(x['uid'],)).fetchone()
        if not row:
            con.execute('INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',(x['uid'],x['source'],x['title'],x['url'],x.get('published'),ts,ts,1,x['score'],x['kind'],x.get('concept'),json.dumps(x)))
            x['status']='NEW'; new.append(x)
        else:
            delta=x['score']-row['last_score']; status='ACCELERATING' if delta>=15 else 'RISING' if delta>=5 else 'STABLE'
            con.execute('UPDATE signals SET last_seen=?,seen_count=seen_count+1,last_score=?,concept=?,raw_json=? WHERE uid=?',(ts,x['score'],x.get('concept'),json.dumps(x),x['uid']))
            x['status']=status
            if status!='STABLE': changed.append(x)
    con.execute('INSERT INTO scans(ts,source_counts,new_count,changed_count) VALUES(?,?,?,?)',(ts,json.dumps({}),len(new),len(changed))); con.commit(); con.close(); return new,changed

def run_scan(api_key,scan_number):
    raw=[]; counts={}; trends=google_trends(); counts["Google Trends"]=len(trends); raw += [classify(x,"Google Trends IN") for x in trends[:30]]
    seed=list(SEEDS); random.Random(scan_number*7919).shuffle(seed)
    trend_queries=[]
    for t in trends[:20]:
        title=t.get("title",""); tx=title.lower()
        if any(k in tx for k in ["health","body","food","sleep","water","fitness","exercise","diet","remedy","wellness","pain","breath","gut"]): trend_queries += [title,title+" experiment"]
    qlist=[]
    for q in trend_queries[:12]+seed[:8]:
        if q and q not in qlist: qlist.append(q)
    news=[]
    for q in qlist[:10]: news += [classify(x,"Google News") for x in google_news(q)[:6]]
    raw+=news; counts["Google News"]=len(news)
    yt=[]
    if api_key:
        for q in qlist[:7]: yt += [classify(x,"YouTube") for x in youtube(q,api_key,72,"date")[:8]]
    raw+=yt; counts["YouTube"]=len(yt)
    rq=list(dict.fromkeys([q for q in qlist[:6]]+list(CONCEPT_DETAILS.keys())))
    pm=[]; ep=[]
    for q in rq[:8]: pm += [classify(x,"PubMed") for x in pubmed(q,30)[:5]]
    for q in rq[:6]: ep += [classify(x,"Europe PMC") for x in europe_pmc(q,45)[:5]]
    raw+=pm+ep; counts["PubMed"]=len(pm); counts["Europe PMC"]=len(ep)
    uniq={}
    for x in raw:
        if x["uid"] not in uniq or x["score"]>uniq[x["uid"]]["score"]: uniq[x["uid"]]=x
    items=sorted(uniq.values(),key=lambda z:z["score"],reverse=True); new,changed=persist(items); return items,new,changed,counts

st.set_page_config(page_title='MEDIMANCH INTELLIGENCE RADAR V7.1',layout='wide')

# --- SIDEBAR: LIVE SOURCE CONTROL ---
with st.sidebar:
    st.header('🌐 LIVE FEEDS')
    st.caption('These are the actual source routes used by the radar.')
    st.markdown('[🔎 Google Trends India](https://trends.google.com/trending?geo=IN)')
    st.markdown('[📰 Google News India](https://news.google.com/?hl=en-IN&gl=IN&ceid=IN:en)')
    st.markdown('[▶️ YouTube Trending](https://www.youtube.com/feed/trending)')
    st.markdown('[📚 PubMed](https://pubmed.ncbi.nlm.nih.gov/)')
    st.divider()
    st.subheader('🔍 MANUAL DISCOVERY')
    manual_q=st.text_input('Search a signal/topic',placeholder='e.g. cold feet sleep')
    if st.button('Search All Sources',use_container_width=True) and manual_q.strip():
        manual_results=google_news(manual_q.strip())[:20]
        st.session_state['manual_results']=manual_results
    if st.button('Clear manual results',use_container_width=True):
        st.session_state['manual_results']=[]
    st.divider()
    st.subheader('📡 SOURCE STATUS')
    for name in ['Google Trends','Google News','YouTube','PubMed','Europe PMC']:
        if name not in SOURCE_STATUS:
            st.write(f'⚪ {name}: not scanned')
        elif SOURCE_STATUS[name]:
            st.write(f'🟢 {name}: connected')
        else:
            st.write(f'🔴 {name}: error')
            if SOURCE_ERRORS.get(name): st.caption(SOURCE_ERRORS[name][:180])
    st.divider()
    st.caption('V7.1: discovery-first + source transparency + manual search')

st.title('🔥 MEDIMANCH INTELLIGENCE RADAR V7.1 — DISCOVERY ENGINE')
st.caption('INTERNET FIRST → CHANGE DETECTION → QUERY MUTATION → MEDIMANCH MATCH → VISUAL OPPORTUNITY')

manual_results=st.session_state.get('manual_results',[])
if manual_results:
    st.subheader('🔎 MANUAL SEARCH RESULTS')
    for mr in manual_results:
        st.markdown(f"**{mr.get('title','')}** — [Open source]({mr.get('url','')})")
    st.divider()
api_key=st.secrets.get('YOUTUBE_API_KEY',os.getenv('YOUTUBE_API_KEY',''))
con=db(); scan_count=con.execute('SELECT COUNT(*) FROM scans').fetchone()[0]; con.close()
a,b,c,d=st.columns(4); a.metric('Persistent scans',scan_count); b.metric('YouTube API','CONNECTED' if api_key else 'NOT CONNECTED'); c.metric('Memory','ON'); d.metric('Mode','INTERNET-FIRST')
if 'last_items' not in st.session_state: st.session_state.last_items=[]
if st.button('🔄 RUN FRESH DISCOVERY SCAN',type='primary'):
    with st.spinner('Discovering new signals, changing search paths and comparing with persistent memory…'):
        items,new,changed,counts=run_scan(api_key,scan_count+1)
        st.session_state.update(last_items=items,last_new=new,last_changed=changed,counts=counts)
    st.success(f'Fresh scan: {len(new)} NEW, {len(changed)} CHANGED/RISING')
items=st.session_state.get('last_items',[]); new=st.session_state.get('last_new',[]); changed=st.session_state.get('last_changed',[])
tabs=st.tabs(['🔥 NEW','⚡ ACCELERATING','🔄 RISING','🎥 SHOOT DESIGN','🧬 ECOSYSTEM','▶️ YOUTUBE','📰 NEWS','🔬 RESEARCH','🧭 UNKNOWN','🗃️ MEMORY','⚙️ STATUS'])
def card(x):
    st.markdown(f"### {x.get('status','SIGNAL')} — {x['title']}")
    st.write(f"**Source:** {x['source']} | **Score:** {x['score']}/100 | **Pillar:** {x.get('pillar') or '—'} | **Concept Universe:** {x.get('universe') or '—'}")
    st.write(f"**Mechanism:** {x.get('mechanism') or '—'} | **Visual design:** {x.get('visual') or '—'}")
    if x.get('description'): st.caption(x['description'][:350])
    st.markdown('**🎥 SHOOT DESIGN CLUES**')
    for c in x.get('clues',[]): st.write('• '+c)
    if x.get('url'): st.markdown(f"[Open source]({x['url']})")
    st.divider()

with tabs[0]:
    arr=sorted(new,key=lambda x:x['score'],reverse=True)[:20]; [card(x) for x in arr]
with tabs[1]:
    arr=sorted([x for x in changed if x['status']=='ACCELERATING'],key=lambda x:x['score'],reverse=True)[:20]; [card(x) for x in arr]
with tabs[2]:
    arr=sorted([x for x in changed if x['status']=='RISING'],key=lambda x:x['score'],reverse=True)[:20]; [card(x) for x in arr]
with tabs[3]:
    arr=sorted([x for x in items if x.get('concept')],key=lambda x:x['score'],reverse=True)[:25]; [card(x) for x in arr]
with tabs[4]:
    groups={}
    for x in items:
        if x.get('pillar'): groups.setdefault(x['pillar'],[]).append(x)
    for p,arr in groups.items():
        st.markdown('### '+p); [card(x) for x in sorted(arr,key=lambda z:z['score'],reverse=True)[:6]]
with tabs[5]:
    arr=[x for x in items if x['source']=='YouTube']; st.write('YouTube results:',len(arr)); [card(x) for x in sorted(arr,key=lambda z:z['score'],reverse=True)[:25]]
with tabs[6]:
    arr=[x for x in items if x['source']=='Google News']; st.write('Google News results:',len(arr)); [card(x) for x in sorted(arr,key=lambda z:z['score'],reverse=True)[:25]]
with tabs[7]:
    arr=[x for x in items if x['source'] in ['PubMed','Europe PMC']]; st.write('Research results:',len(arr)); [card(x) for x in sorted(arr,key=lambda z:z['score'],reverse=True)[:25]]
with tabs[8]:
    arr=[x for x in new if not x.get('concept')][:25]; [card(x) for x in arr]
with tabs[9]:
    con=db(); rows=con.execute('SELECT source,title,last_seen,seen_count,last_score,concept FROM signals ORDER BY last_seen DESC LIMIT 100').fetchall(); con.close(); st.dataframe([dict(r) for r in rows],use_container_width=True)
with tabs[10]:
    st.write('Last source counts:',st.session_state.get('counts',{}))
    for name in ['Google Trends','Google News','YouTube','PubMed','Europe PMC']:
        if name in SOURCE_ERRORS: st.error(name+': '+SOURCE_ERRORS[name][:400])
    st.write('Source lanes are intentionally separated. A research paper explains a signal; it does not automatically become a shoot recommendation.')

st.caption('V8 rule: DISCOVER FIRST + SOURCE LANES + ECOSYSTEM ROUTING + SHOOT DESIGN CLUES. Do not repeatedly search the same fixed Medimanch topics and call them fresh.')
