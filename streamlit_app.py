from pathlib import Path
import os, re, json, sqlite3, hashlib, random
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
import xml.etree.ElementTree as ET
import requests
import streamlit as st

DB_PATH = os.environ.get('MEDIMANCH_DB', str(Path(__file__).with_name('radar_memory.db')))
HEADERS={'User-Agent':'Medimanch-Intelligence-Radar/7.0'}

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

def rss(url):
    try:
        r=requests.get(url,headers=HEADERS,timeout=15); r.raise_for_status(); root=ET.fromstring(r.content); out=[]
        for item in root.findall('.//item'):
            title=clean(item.findtext('title')); link=clean(item.findtext('link')); desc=clean(item.findtext('description')); pub=clean(item.findtext('pubDate'))
            if title and link: out.append({'title':title,'url':link,'description':desc,'published':pub})
        return out
    except Exception: return []

def google_trends(): return rss('https://trends.google.com/trending/rss?geo=IN&hl=en-IN')
def google_news(q): return rss('https://news.google.com/rss/search?q='+quote(q)+'&hl=en-IN&gl=IN&ceid=IN:en')

def youtube(q,api_key,hours=48):
    if not api_key: return []
    after=(now()-timedelta(hours=hours)).isoformat().replace('+00:00','Z')
    params={'part':'snippet','q':q,'type':'video','maxResults':10,'order':'date','publishedAfter':after,'regionCode':'IN','relevanceLanguage':'hi','key':api_key}
    try:
        r=requests.get('https://www.googleapis.com/youtube/v3/search',params=params,timeout=15); r.raise_for_status(); data=r.json()
        return [{'title':x.get('snippet',{}).get('title',''),'url':'https://www.youtube.com/watch?v='+x.get('id',{}).get('videoId',''),'description':x.get('snippet',{}).get('description',''),'published':x.get('snippet',{}).get('publishedAt','')} for x in data.get('items',[]) if x.get('id',{}).get('videoId')]
    except Exception: return []

def concept_match(text):
    t=text.lower(); best=[]
    for c,aliases in MEDIMANCH_MAP.items():
        hits=sum(1 for a in aliases if a in t)
        if hits: best.append((hits,c))
    return sorted(best,reverse=True)[0][1] if best else None

def score_signal(x):
    text=(x['title']+' '+x.get('description','')).lower(); score=0
    d=parse_date(x.get('published'))
    if d:
        age=(now()-d).total_seconds()/3600; score += 35 if age<=6 else 25 if age<=24 else 15 if age<=48 else 5
    score += min(25,sum(2 for w in VISUAL_WORDS if w in text))
    score -= min(25,sum(5 for w in NOISE if w in text))
    if x.get('concept'): score += 25
    if any(k in text for k in ['health','body','sleep','gut','food','water','breath','exercise','remedy','wellness']): score += 10
    return max(0,min(100,score))

def classify(raw,source):
    x={'source':source,'title':raw.get('title',''),'url':raw.get('url',''),'description':raw.get('description',''),'published':raw.get('published','')}
    x['concept']=concept_match(x['title']+' '+x['description']); text=(x['title']+' '+x['description']).lower()
    x['kind']='visual_behaviour' if any(w in text for w in VISUAL_WORDS) else 'discovery'; x['score']=score_signal(x); x['uid']=uid(source,x['url'],x['title']); return x

def mutate(seed,n):
    variants=[seed,seed+' new trend',seed+' challenge',seed+' experiment',seed+' people doing',seed+' before after',seed+' body',seed+' India']
    return variants[n%len(variants)]

def mutate_from_trend(title):
    base=re.sub(r'\s+',' ',re.sub(r'[^\w\s-]',' ',title.lower())).strip(); base=' '.join(base.split()[:9])
    return [base,base+' health',base+' body',base+' experiment',base+' challenge']

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
    raw=[]; counts={}; trends=google_trends(); counts['trends']=len(trends)
    for t in trends[:35]: raw.append(classify(t,'Google Trends IN'))
    queries=[]
    for t in trends[:35]:
        tx=(t.get('title','')+' '+t.get('description','')).lower()
        if any(k in tx for k in ['health','body','food','sleep','water','fitness','exercise','diet','remedy','wellness','weight','pain','breath','skin','gut']): queries += mutate_from_trend(t.get('title',''))[:2]
    rng=random.Random(scan_number*7919); seeds=SEEDS[:]; rng.shuffle(seeds); queries += [mutate(q,scan_number+i) for i,q in enumerate(seeds[:10])]
    seen=set(); queries=[q for q in queries if q and q.lower() not in seen and not seen.add(q.lower())]
    for q in queries[:12]:
        for item in google_news(q)[:8]: raw.append(classify(item,'Google News'))
    counts['news_queries']=min(12,len(queries))
    ytq=queries[:8]
    if api_key:
        for q in ytq:
            for item in youtube(q,api_key,48): raw.append(classify(item,'YouTube'))
    counts['youtube_queries']=len(ytq) if api_key else 0
    uniq={}
    for x in raw:
        if x['uid'] not in uniq or x['score']>uniq[x['uid']]['score']: uniq[x['uid']]=x
    items=sorted(uniq.values(),key=lambda z:z['score'],reverse=True); new,changed=persist(items)
    return items,new,changed,counts

st.set_page_config(page_title='MEDIMANCH INTELLIGENCE RADAR V7',layout='wide')
st.title('🔥 MEDIMANCH INTELLIGENCE RADAR V7 — DISCOVERY ENGINE')
st.caption('INTERNET FIRST → CHANGE DETECTION → QUERY MUTATION → MEDIMANCH MATCH → VISUAL OPPORTUNITY')
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
tabs=st.tabs(['🔥 NEW','⚡ ACCELERATING','🔄 RISING','🎥 VISUAL','🧭 UNKNOWN','🗃️ MEMORY','⚙️ STATUS'])
def card(x):
    st.markdown(f"### {x.get('status','SIGNAL')} — {x['title']}")
    st.write(f"**Source:** {x['source']}  |  **Score:** {x['score']}/100  |  **Concept:** {x.get('concept') or 'Not mapped'}")
    if x.get('description'): st.caption(x['description'][:350])
    if x.get('url'): st.markdown(f"[Open source]({x['url']})")
    st.divider()
with tabs[0]:
    arr=sorted(new,key=lambda x:x['score'],reverse=True)[:20]
    st.subheader('Signals never seen before'); [card(x) for x in arr]
    if not arr: st.info('No brand-new signals. Run later; the query space rotates.')
with tabs[1]:
    arr=sorted([x for x in changed if x['status']=='ACCELERATING'],key=lambda x:x['score'],reverse=True)[:20]; [card(x) for x in arr]
    if not arr: st.info('No accelerating signals detected.')
with tabs[2]:
    arr=sorted([x for x in changed if x['status']=='RISING'],key=lambda x:x['score'],reverse=True)[:20]; [card(x) for x in arr]
    if not arr: st.info('No rising signals detected.')
with tabs[3]:
    arr=[x for x in items if x['kind']=='visual_behaviour' and x['score']>=35][:25]; [card(x) for x in arr]
with tabs[4]:
    arr=[x for x in new if not x.get('concept')][:25]; [card(x) for x in arr]
    st.caption('UNKNOWN is deliberate: it stops the dictionary from becoming a prison.')
with tabs[5]:
    con=db(); rows=con.execute('SELECT source,title,last_seen,seen_count,last_score,concept FROM signals ORDER BY last_seen DESC LIMIT 100').fetchall(); con.close(); st.dataframe([dict(r) for r in rows],use_container_width=True)
with tabs[6]:
    st.write('Google Trends = high-frequency discovery. Google News = expansion. YouTube = quota-aware recent video discovery. SQLite = persistent memory.')
    st.write('Last source counts:',st.session_state.get('counts',{})); st.warning('Never paste your YouTube API key into chat. Store it only in Streamlit Secrets as YOUTUBE_API_KEY.')
st.caption('V7 rule: DISCOVER FIRST. Do not repeatedly search the same fixed Medimanch topics and call them fresh.')
