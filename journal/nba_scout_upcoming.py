import os, sys, time, glob, re, random, requests, datetime
import yt_dlp

TEAM_HANDLES = {
    "hawks": "@LockedOnHawks", "celtics": "@LockedOnCeltics",
    "nets": "@LockedOnNets", "hornets": "@LockedOnHornets",
    "bulls": "@LockedOnBulls", "cavaliers": "@LockedOnCavs",
    "mavericks": "@LockedOnMavs", "nuggets": "@DNVR_Sports",
    "pistons": "@LockedOnPistons", "warriors": "@LockedOnWarriors",
    "rockets": "@LockedOnRockets", "pacers": "@LockedOnPacers",
    "clippers": "@LockedOnClippers", "lakers": "@LockedOnLakers",
    "grizzlies": "@LockedOnGrizzlies", "heat": "@LockedOnHeat",
    "bucks": "@LockedOnBucks", "timberwolves": "@LockedOnWolves",
    "pelicans": "@LockedOnPelicans", "knicks": "@LockedOnKnicks",
    "thunder": "@LockedOnThunder", "magic": "@LockedOnMagic",
    "76ers": "@LockedOn76ers", "suns": "@LockedOnSuns",
    "blazers": "@LockedOnBlazers", "kings": "@LockedOnKings",
    "spurs": "@LockedOnSpurs", "raptors": "@LockedOnRaptors",
    "jazz": "@LockedOnJazz", "wizards": "@LockedOnWizards"
}

# NBA team name -> our key mapping for schedule API
NBA_ABBR_TO_KEY = {
    "ATL":"hawks","BOS":"celtics","BKN":"nets","CHA":"hornets","CHI":"bulls","CLE":"cavaliers",
    "DAL":"mavericks","DEN":"nuggets","DET":"pistons","GSW":"warriors","HOU":"rockets","IND":"pacers",
    "LAC":"clippers","LAL":"lakers","MEM":"grizzlies","MIA":"heat","MIL":"bucks","MIN":"timberwolves",
    "NOP":"pelicans","NYK":"knicks","OKC":"thunder","ORL":"magic","PHI":"76ers","PHX":"suns",
    "POR":"blazers","SAC":"kings","SAS":"spurs","TOR":"raptors","UTA":"jazz","WAS":"wizards"
}

def get_tomorrows_teams():
    tomorrow = (datetime.datetime.now() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"📅 Checking NBA games for {tomorrow}...")
    try:
        # Use balldontlie free API - no key needed for this endpoint
        url = f"https://api.balldontlie.io/nba/v1/games?dates[]={tomorrow}"
        # Try NBA official scoreboard (more reliable, no key)
        nba_url = f"https://cdn.nba.com/static/json/liveData/scoreboard/{tomorrow}/scoreboard.json"
        r = requests.get(nba_url, timeout=10, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code == 200:
            data = r.json()
            games = data.get("scoreboard", {}).get("games", [])
            teams = set()
            for g in games:
                teams.add(g.get("homeTeam", {}).get("teamTricode"))
                teams.add(g.get("awayTeam", {}).get("teamTricode"))
            teams = {t for t in teams if t}
            print(f"Found {len(games)} games, {len(teams)} teams: {teams}")
            return [NBA_ABBR_TO_KEY.get(t) for t in teams if t in NBA_ABBR_TO_KEY]
    except Exception as e:
        print(f"Schedule API failed: {e}, defaulting to all teams")
    # Fallback: return all if API fails
    return list(TEAM_HANDLES.keys())

def clean_vtt(p):
    try:
        with open(p,'r',encoding='utf-8',errors='ignore') as f: t=f.read()
    except: return ""
    lines=[]
    for l in t.splitlines():
        if '-->' in l or 'WEBVTT' in l or l.startswith('Kind:') or not l.strip(): continue
        l=re.sub(r'<[^>]+>','',l)
        if re.match(r'^\d+$', l.strip()): continue
        if l.strip() and (not lines or lines[-1]!=l.strip()): lines.append(l.strip())
    return " ".join(lines)

def get_transcript(vid, tmp):
    url=f"https://www.youtube.com/watch?v={vid}"
    ydl_opts={'skip_download':True,'writesubtitles':True,'writeautomaticsub':True,'sublangs':['en'],'outtmpl':os.path.join(tmp,f'{vid}'),'quiet':True,'no_warnings':True,'extractor_args':{'youtube':{'player_client':['android']}},'sleep_interval':3}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl: ydl.extract_info(url, download=True)
    except Exception as e:
        if "429" in str(e):
            print("    ⚠️  429, waiting 60s..."); time.sleep(60); return None
        return None
    vtts=glob.glob(os.path.join(tmp,f'{vid}*.vtt'))
    if not vtts: return None
    txt=clean_vtt(vtts[0])
    for f in vtts:
        try: os.remove(f)
        except: pass
    return txt

def get_latest_video_id(handle):
    ydl_opts={'quiet':True,'skip_download':True,'extract_flat':True,'no_warnings':True,'extractor_args':{'youtube':{'player_client':['android']}}}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info=ydl.extract_info(f"ytsearch1:{handle} postgame",download=False)
        if info and 'entries' in info and info['entries']: return info['entries'][0]['id']
    return None

def process_team(k, out):
    h=TEAM_HANDLES[k]
    print(f"--> {k.upper()} {h}")
    try:
        vid=get_latest_video_id(h)
        if not vid: return f"NO {k.upper()}: No video"
        txt=get_transcript(vid,out)
        if not txt: return f"NO {k.upper()}: No captions / throttled"
        path=os.path.join(out,f"{k}_latest.txt")
        with open(path,"w",encoding="utf-8") as f: f.write(f"TEAM: {k}\nURL: https://youtu.be/{vid}\n\n{txt}")
        return f"YES {k.upper()}: Saved {len(txt)} chars"
    except Exception as e:
        return f"NO {k.upper()}: {e}"

if __name__=="__main__":
    od=os.path.join(os.path.dirname(os.path.abspath(__file__)),"transcripts_output")
    os.makedirs(od,exist_ok=True)
    teams = get_tomorrows_teams()
    # Clean list
    teams = [t for t in teams if t in TEAM_HANDLES]
    if not teams:
        print("No games tomorrow, using last 10 teams as test")
        teams = list(TEAM_HANDLES.keys())[:10]
    print(f"🎯 Will scrape {len(teams)} teams: {teams}")
    for k in teams:
        print(process_team(k,od))
        time.sleep(random.randint(15,25))
