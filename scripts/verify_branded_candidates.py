"""Pass-3 verification of branded_content_candidates — inline LLM
(Claude) judgments, applied as a curated mapping.

Rather than calling the API, the canonical-brand map (CANON) and the
false-positive set (FALSE) below ARE the model's verdicts on the 436
distinct normalized captures: merge sponsor variants to one canonical
name (Deutsche Telekom's 5 spellings → one), and reject non-sponsors
(player/commentator names from "présenté par [person]", football orgs
presenting their own awards, generic phrase fragments).

Sets per row: brand_canonical, is_branded, reviewed=true, llm_confidence.
Flag-only rows (no captured brand) are auto-confirmed is_branded=true
from YouTube's flag with llm_confidence='flag'. The long tail of
one-off ambiguous captures is left unreviewed for a later pass.

Re-run to re-apply after editing CANON/FALSE.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()
from src.database import Database
db = Database(os.environ['SUPABASE_URL'], os.environ.get('SUPABASE_SERVICE_KEY') or os.environ['SUPABASE_KEY'])

# CANON: brand_norm(lowercased) -> canonical brand. Multiple keys can map
# to one brand (merge variants). These are confirmed real sponsors.
CANON = {
 "mpbcom":"MPB","mpb":"MPB","canonbr":"Canon",
 "deutschetelekomag":"Deutsche Telekom","deutschetelekomag​":"Deutsche Telekom",
 "deutschen telekom":"Deutsche Telekom","telekom":"Deutsche Telekom","der telekom":"Deutsche Telekom",
 "lichtblick":"LichtBlick","nordvpn":"NordVPN","hisenseinternational":"Hisense",
 "officialunibet":"Unibet","442oons":"442oons","michelob ultra":"Michelob Ultra",
 "michelob ultra - trinity rodman":"Michelob Ultra","hi88":"Hi88",
 "airbnb":"Airbnb","airbnb for the world cup":"Airbnb","astonish":"Astonish",
 "sparda-bank hamburg":"Sparda-Bank Hamburg","sparda-bank-hamburg":"Sparda-Bank Hamburg",
 "spardabank hamburg":"Sparda-Bank Hamburg","sparda-bank hamburg für euch":"Sparda-Bank Hamburg",
 "cocacola":"Coca-Cola","coca-cola":"Coca-Cola","coca cola":"Coca-Cola","corona":"Corona",
 "noco":"NOCO","congstar":"congstar","linglong tire":"Linglong Tire",
 "linglong tire - live":"Linglong Tire","linglong tire - live auf unserem kanal":"Linglong Tire",
 "treppenbau voß":"Treppenbau Voß","allstate":"Allstate",
 "fútbol mahou mvp award":"Mahou","fútbol mahou' mvp award":"Mahou",
 "weareboxt":"BOXT","boxt":"BOXT","mg":"MG","amtautoleeds5895":"AMT Auto","amt auto":"AMT Auto",
 "amt auto - @amtautoleeds5895":"AMT Auto","amtautouk":"AMT Auto","sensodyne":"Sensodyne",
 "scotiabank":"Scotiabank","pepsi":"Pepsi","pepsimax":"Pepsi","ideagen plc":"Ideagen",
 "commbank":"CommBank","ee":"EE","cibc":"CIBC","volkswagen_usa":"Volkswagen",
 "volkswagennl":"Volkswagen","volkswagen":"Volkswagen","vw":"Volkswagen",
 "volkswagen is now available to watch wi":"Volkswagen","united way":"United Way","waymo":"Waymo",
 "betsson":"Betsson","betsson sport":"Betsson","betssonsport":"Betsson","indeed":"Indeed",
 "hauptsponsor indeed":"Indeed","audi":"Audi","audi folge 3":"Audi","m&s food":"M&S Food",
 "rexona":"Rexona","jp financial":"JP Financial","psk":"PSK","easy live - www":"Easy Live",
 "holy":"HOLY","\U0001d5db\U0001d5fc\U0001d5f9\U0001d606":"HOLY","blue kc":"Blue KC",
 "qatarairways":"Qatar Airways","qatar airways":"Qatar Airways",
 "qatar airways after scoring eight goals":"Qatar Airways","beyond stats":"Beyond Stats",
 "tim":"TIM","banorte":"Banorte","clivet":"Clivet","barmeniagothaer":"BarmeniaGothaer",
 "朝日新聞］":"Asahi Shimbun","msc crociere":"MSC Crociere","msc":"MSC Crociere",
 "msccrociereufficiale":"MSC Crociere","eandgroup":"e&","visitsaudi":"Visit Saudi",
 "‌visitsaudi":"Visit Saudi","mercedes-benz bank":"Mercedes-Benz Bank","mercedes":"Mercedes-Benz",
 "rockstar":"Rockstar","multicare":"MultiCare","rebel":"Rebel","motrin":"Motrin",
 "seat unique":"Seat Unique","wish - shopping made fun":"Wish","dazn":"DAZN",
 "metro by t-mobile":"Metro by T-Mobile","övb":"ÖVB","övb & green legend":"ÖVB",
 "doppio":"Doppio","craftd":"CRAFTD","luna grill":"Luna Grill","helzberg":"Helzberg",
 "turbogrün":"turbogrün","balocco":"Balocco","lotto bayern":"Lotto Bayern",
 "lotto rheinland-pfalz":"Lotto Rheinland-Pfalz","ea sports":"EA Sports","etoro":"eToro",
 "enelgroup":"Enel","krombacher":"Krombacher","wohninvest":"wohninvest","rowe":"ROWE",
 "tylenol":"Tylenol","cupra":"CUPRA","cupra - official automotive and mobility":"CUPRA",
 "depot":"DEPOT","standard chartered":"Standard Chartered","hill dickinson":"Hill Dickinson",
 "hill dickinson — also made a special app":"Hill Dickinson","nike":"Nike",
 "cpkc":"CPKC","jeep":"Jeep","jeep ended in seoul":"Jeep","bank of america":"Bank of America",
 "bankofamerica":"Bank of America","redbull":"Red Bull","dws":"DWS","cosmosdirekt":"CosmosDirekt",
 "sportsbreaks":"SportsBreaks","telus":"TELUS","amiri":"AMIRI","at&t":"AT&T",
 "lyca mobile":"Lyca Mobile","platinum cats & dogs":"PLATINUM","weloveholidays":"weloveholidays",
 "marriott bonvoy":"Marriott Bonvoy","marriottbonvoy":"Marriott Bonvoy","dove":"Dove",
 "frecciarossa":"Frecciarossa","aok rheinland / hamburg":"AOK","sunexpress":"SunExpress",
 "ifs":"IFS","royalcaribbean":"Royal Caribbean","‪@lowes‬":"Lowe's","lowe’s":"Lowe's",
 "axa":"AXA","axauk":"AXA","microsoft copilot":"Microsoft Copilot","frankfurter sparkasse":"Frankfurter Sparkasse",
 "henkel":"Henkel","snapdragon":"Snapdragon","carrick packaging":"Carrick Packaging",
 "aqua römer quelle":"Aqua Römer","saturn petcare":"Saturn Petcare","beko":"Beko",
 "danone":"Danone","gsm sella":"GSM Sella","riyadhair":"Riyadh Air","riyadh air":"Riyadh Air",
 "bitburger":"Bitburger","ballball":"BallBall","american express":"American Express",
 "detertech":"DeterTech","sobha realty":"Sobha Realty","google gemini":"Google Gemini",
 "mcdonald's":"McDonald's","mcdonalds and the sunday mail":"McDonald's","mcdonald's and the sunday mail":"McDonald's",
 "radio leverkusen":"Radio Leverkusen","hummel":"hummel","bet365 scores":"Bet365",
 "estrella damm – official beer of fc barc":"Estrella Damm","mighty drinks":"Mighty Drinks",
 "ergo finalevent":"ERGO","hotelsdotcom":"Hotels.com","hotels":"Hotels.com","spotify":"Spotify",
 "devk":"DEVK","lenovo":"Lenovo","hansemerkur":"HanseMerkur","specsavers":"Specsavers",
 "subway":"Subway","škoda":"Škoda","carmax":"CarMax","flatex":"flatex",
 "rheinenergie":"RheinEnergie","vodafone":"Vodafone","metlifemx":"MetLife","metlife":"MetLife",
 "castore":"Castore","adidas":"adidas","puma":"PUMA","aral":"Aral","scope markets":"Scope Markets",
 "wayfair":"Wayfair","club sponsor bmw":"BMW","chevrolet":"Chevrolet","chevy":"Chevrolet",
 "hublot":"Hublot","airwallex":"Airwallex","aramco":"Aramco","rewe":"REWE","fc-hauptpartner rewe":"REWE",
 "eni":"Eni","midea":"Midea","exalt":"Exalt","alaska airlines":"Alaska Airlines",
 "lacroix water":"LaCroix","ooredoo":"Ooredoo","popp feinkost":"Popp Feinkost","pluto tv":"Pluto TV",
 "sbotop":"SBOTOP","doordash":"DoorDash","adobe":"Adobe","visit dubai":"Visit Dubai",
 "club sponsor visit dubai":"Visit Dubai","visit missouri":"Visit Missouri","hyundai":"Hyundai",
 "hyundai in new york":"Hyundai","qantas":"Qantas","ava trade":"AvaTrade",
 "unserem partner admiralbet":"AdmiralBet","modelousa":"Modelo","viva aerobus":"Viva Aerobus",
 "flamingoland":"Flamingo Land","pegasus airlines":"Pegasus Airlines","pegasusairlines":"Pegasus Airlines",
 "ge":"GE","lyle & scott":"Lyle & Scott","cynergy bank":"Cynergy Bank","fosun international":"Fosun International",
 "atlassian":"Atlassian","flyeralarm":"FlyerAlarm","deichmann":"Deichmann","thorn":"Thorn",
 "binding":"Binding","sonos":"Sonos","transfermate":"TransferMate","swb":"swb","utilita":"Utilita",
 "joie":"Joie","netcologne fc-tv":"NetCologne","playstation":"PlayStation","efootball™":"eFootball",
 "interwetten":"Interwetten","tcl":"TCL","allianz":"Allianz","dafabet":"Dafabet",
 "nexentireinternational":"Nexen Tire","adesso":"adesso","haier":"Haier","cazoo":"Cazoo",
 "leovegasnews":"LeoVegas","jdfootball":"JD Sports","haake beck":"Haake Beck",
 "cryptocomofficial":"Crypto.com","visa":"Visa","visa is here":"Visa","socios":"Socios","hailo":"Hailo",
 "mewa durchgeführtes":"MEWA","rituals":"Rituals","expressvpn":"ExpressVPN","betvictor":"BetVictor",
 "bwin":"bwin","telefónica":"Telefónica","g500mexico8":"G500","kreissparkasse köln":"Kreissparkasse Köln",
 "sport bohny":"Sport Bohny","sonepar":"Sonepar","grand central":"Grand Central","uhlsport":"uhlsport",
 "philips ambilight tv - main partner of f":"Philips","prometeon e il gruppo gazzetta di parma":"Prometeon",
 "lega serie a":"Lega Serie A","clivet":"Clivet","gateexchange":"GATE Exchange","caffè_toraldo":"Caffè Toraldo",
 "betssonsport":"Betsson","seat unique":"Seat Unique","united store":"United Store","gruppo center":"Gruppo Center",
 "emirates":"Emirates","cpkc":"CPKC","standard chartered":"Standard Chartered","sunexpress":"SunExpress",
 "scalpers":"Scalpers","christopher ward":"Christopher Ward","bitburger - das finale":"Bitburger",
 "bitburger - folge 1":"Bitburger","bitburger - folge 2":"Bitburger","schauinsland-reisen":"schauinsland-reisen",
 "lotto bayern":"Lotto Bayern","cemento argos y que transmite cable onda":"Argos","amazon méxico":"Amazon",
 "midea":"Midea","collonil":"Collonil","vfl championspartner collonil":"Collonil","binding":"Binding",
 "uefa":"UEFA","fifa":"FIFA",
}

# FALSE: not commercial sponsors — people (players/commentators/presenters),
# generic phrase fragments, or self-references. is_branded=False.
FALSE = {
 "fabien simon","vincent simonneaux","achraf hakimi","sam hart","cel spellman and natalie pike",
 "tim gagnon","sid lowe y daniel fernández","gilbert brisbois et jean-luc filser",
 "eva longoria and marco schreyl","iker casillas","jonathan tah","maximilian arnold",
 "jude bellingham","emilio nsue","jhon durán","luca campolunghi","oli sorg und oli baumann",
 "sébastien tarrago et guillaume dufy","thibault le rol","cyril collot et produit par le chat qui",
 "emmanuel moine","emmanuel moine et romain balland","emmanuel moine et vincent magniez à revi",
 "his captain","su presidente","seu técnico","one man’s brilliance","de nombreux talents",
 "des personnalités","un numeroso grupo de seguidores","hauptamtlichen mitarbeitern","one community",
 "glasgow’s physical education","pure masandawana energy","electricity","superfans","dr",
 "marina lorenzo pour évoquerle reste de l","marina lorenzo pour évoquer le début de",
 "olise","jordan football association","sport club corinthians paulista","bayer 04 leverkusen",
 "mozambique","seu técnico","his captain","gabbia","iker casillas",
}

rows=[];off=0
while True:
    r=(db.client.table('branded_content_candidates').select('video_id,brand_norm,has_paid_flag').range(off,off+999).execute().data) or []
    rows+=r
    if len(r)<1000: break
    off+=1000

# Self-promotion code prefixes — a club promoting its OWN product
# (e.g. Liverpool's GOFREE codes for their LFC streaming service) is
# NOT a third-party paid promotion. Reject by brand_norm prefix.
SELF_PROMO_PREFIX = ("gofree",)

upd=[]
n_canon=n_false=n_flag=n_auto=0
for r in rows:
    bnorm=r.get('brand_norm')
    bn=(bnorm or '').lower()
    flag=bool(r.get('has_paid_flag'))
    rec={"video_id":r["video_id"]}
    if (not flag) and bn.startswith(SELF_PROMO_PREFIX):
        rec.update({"is_branded":False,"reviewed":True,
                    "brand_canonical":None,"llm_confidence":"self_promo"}); n_false+=1
    elif flag:
        # YouTube discloses paid promotion → ALWAYS branded, whatever
        # the text capture was. Use the CANON brand when we have one;
        # otherwise leave brand_canonical untouched (extract_flag_brands
        # fills it later from @mentions). This must come first so a
        # flagged video with a non-CANON brand_norm isn't left unreviewed.
        rec.update({"is_branded":True,"reviewed":True})
        if bn in CANON:
            rec.update({"brand_canonical":CANON[bn],"llm_confidence":"high"}); n_canon+=1
        else:
            rec["llm_confidence"]="flag"; n_flag+=1
    elif bn in CANON:
        rec.update({"brand_canonical":CANON[bn],"is_branded":True,"reviewed":True,"llm_confidence":"high"}); n_canon+=1
    elif bn in FALSE:
        rec.update({"is_branded":False,"reviewed":True,"llm_confidence":"high"}); n_false+=1
    elif (bnorm and not flag
          and len(bnorm) <= 28 and len(bnorm.split()) <= 4):
        # Auto-confirm NEW text-detected sponsors not yet in the curated
        # CANON map, using the normalized name as a best-effort brand —
        # so fresh sponsors surface on a weekly re-run without waiting
        # for a manual map update. The short/few-word guard filters the
        # phrase-fragment junk. Promote common ones into CANON later.
        disp = bnorm if any(c.isupper() for c in bnorm) else bnorm.title()
        rec.update({"brand_canonical":disp,"is_branded":True,
                    "reviewed":True,"llm_confidence":"auto"}); n_auto+=1
    else:
        continue
    upd.append(rec)

for i in range(0,len(upd),200):
    db.client.table('branded_content_candidates').upsert(upd[i:i+200],on_conflict="video_id").execute()

print(f"Applied verdicts to {len(upd)} rows:")
print(f"  canonical brand confirmed: {n_canon}")
print(f"  auto-confirmed (new sponsor, normalized name): {n_auto}")
print(f"  false positive (not a sponsor): {n_false}")
print(f"  flag-only auto-confirmed (brand unknown): {n_flag}")
print(f"  left unreviewed (long tail): {len(rows)-len(upd)}")
