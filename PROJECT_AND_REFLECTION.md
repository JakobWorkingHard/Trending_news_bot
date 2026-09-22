Syfte med projektet
Hela poängen med det här bygget var att slippa fastna i ett ändlöst scrollande på nätet varje morgon. Jag ville helt enkelt ha en pipeline som sköter morgonkaffe-spaningen åt mig inom tech, data science och AI:

Skrapa hem färska nyhetsartiklar från nätet.

Dumpa in rubrikerna till en LLM som får svettas och vaska fram vad folk faktiskt snackar om just nu.

Spotta ur sig en färdig Markdown-rapport som serverar alla heta trender på ett silverfat till användaren (vilket i dagsläget är jag själv :D).

För egen del handlade det mest om att lära mig hur man bygger en dataskrapa som inte imploderar så fort en webbsida hostar till. Jag ville förstå hur man handskas med RSS-flöden på riktigt, vad för knasiga spärrar man åker på, och hur man sätter ihop en hel kedja där man skyfflar data fram och tillbaka mot ett LLM-API utan att plånboken ryker eller koden kraschar.

Fördjupningsområden
Web scraping med feedparser, requests och trafilatura.

Prata med externa LLM-modeller via OpenAI-kompatibla API:er.

Automatisk rapportgenerering utan handpåläggning.

Strukturera upp allt som ett städat och körbart Python-paket med pyproject.toml och src-layout.

Klassiskt data engineering-hantverk i miniformat: hämta, rensa dubbletter, spara i SQLite, analysera och leverera.

Vad projektet gör
1. Skrapar nyheter från RSS och artiklar
Pipelinen börjar med att läsa in länkarna som jag petat in i config/sources.json. Det här sker i två steg:

RSS-steget: Först plockar jag RSS-flödet med feedparser. Men feedparsers inbyggda nätverkssniffare blir portad snabbare än en full student på krogen av sajternas brandväggar (hej 403 och 429). Därför tar jag hem rådatan med requests först. Då kan jag nämligen ljuga om vem jag är! Jag sätter min USER_AGENT till Feedly. Varför? Jo, för efter att ha googlat halvt ihjäl mig insåg jag att sajter som KDnuggets och AI News ofta vitlistar just Feedly för att deras RSS-flöden ska funka i vanliga läsare, medan vanliga botar åker ut med huvudet före. Dessutom passar jag på att städa bort konstiga blanksteg och skräp i början av XML-filerna så feedparser slipper få tuppjuck på grund av felaktiga encodings (som när VentureBeat påstår att de kör us-ascii men i själva verket kör UTF-8).

Fulltext-steget: Sen skickar jag in trafilatura för att dammsuga själva artikelsidan på brödtext och slänga menyer, sidfötter och banners i papperskorgen. Här byter jag maskering igen och utger mig för att vara en helt vanlig snubbe som surfar på Chrome i Windows, vilket gör att sajterna snällt släpper förbi mig.

2. Lagrar och städar i SQLite
När datan väl är bärgad åker den rakt ner i en lokal SQLite-databas. Jag kör hängslen och livrem mot dubbletter: först kollar koden om länken redan finns via is_url_seen(), och skulle något smita förbi sätter databasens UNIQUE-spärr stopp. Dessutom agerar databasen städtant varje gång programmet körs: artiklar som är äldre än vad jag satt i retention-inställningen raderas rakt av, så databasen inte svämmar över av stenåldersnyheter som förstör dagens trender.

3. Skickar rubriker till LLM
Projektet plockar ut de senaste artiklarna (man kan köra python main.py --hours 6 om man vill ha det absolut senaste, annars körs senaste dygnet som standard). Dessa buntas ihop med rubrik och källsajt och skickas in till modellen. Jag kör med openai-biblioteket men pekar om bas-URL:en till svenska evroc.

4. Sparar rapporten
Svaret från språkmodellen fångas upp, printas i terminalen, loggas i pipeline.log och sparas till sist ner som en prydlig Markdown-fil under data/trend_reports/.

Exempel på hur ett resultat kan se ut:

___________________________________________________________________________

# Trendanalys (senaste 24 timmarna)

_Genererad 2026-09-21T13:21:12.611087+00:00_

---

Här är en analys av dagens nyhetsflöde baserat på de rubriker och sajter du tillhandahållit. Det finns tre tydliga trender som dominerar just nu.

### 1. Framväxten av autonoma AI-agenter och AI-kodning
**Sajter som skriver om detta:** geekwire.com, github.com, infoq.com, marktechpost.com, towardsdatascience.com

**Förklaring och varför det är intressant:**
Nyheterna visar en tydlig övergång från passiva AI-verktyg (som chatbotar) till självständiga AI-agenter som kan utföra komplexa arbetsflöden. Vi ser detta i Amazon och Metas strid om "agentic shopping" (geekwire.com), Googles Agent Development Kit (infoq.com) och StepFuns nya modell designad för "Long-Horizon Agentic Work" (marktechpost.com). Inom utvecklarvärlden fokuserar man nu på team av AI-kodningsagenter som delar minne (github.com) och AI-assisterad kodgranskning (infoq.com). Det intressanta – och potentiellt farliga – är att ansvaret flyttas från människa till maskin. Towardsdatascience.com lyfter fram en kritisk fråga i detta: *"Your AI Assistant Wrote the Code. Who Checked the Defaults?"*, vilket pekar på att hastigheten i AI-automatisering kanske överstiger vår förmåga att kvalitetskontrollera den.

### 2. Säkerhetsrisker, misslyckanden och etiska konsekvenser av AI och teknik
**Sajter som skriver om detta:** thefp.com, technologyreview.com, marktechpost.com, techcrunch.com, infoq.com

**Förklaring och varför det är intressant:**
En mörkare och mer kritisk ton genomsyrar många av dagens nyheter. Technologyreview.com har en stor granskningsserie om hur USA:s gräns "virtuella mur" (surveillance tech) misslyckas med att rädda liv, trots miljardinvesteringar. På AI-sidan rapporterar marktechpost.com om hur Googles Gemini lyckades bryta säkerheten hos tre företag under tester, och thefp.com varnar för ett "Doomsday Scenario" för amerikansk AI. Techcrunch.com ställer frågan om AI-industrin verkligen är redo att sakta ner, samtidigt som de rapporterar om "world model companies" som hemlighåller mycket av sin teknik. Detta är intressant eftersom det markerar en viss motreaktion mot hypen; fokus skiftar från vad AI *kan* göra, till vad som går fel när tekniken implementeras i stor skala i samhället och i säkerhetskritiska system.

### 3. Mognad i AI-infrastruktur och öppen källkod
**Sajter som skriver om detta:** infoq.com, marktechpost.com, github.com, machinelearningmastery.com, towardsdatascience.com, nanomo.app

**Förklaring och varför det är intressant:**
Bakom kulisserna pågår en enorm infrastruktur- och optimeringsracet. Infoq.com rapporterar om stora infrastrukturella händelser: Bun skriver om 535 000 rader kod från Zig till Rust för att elimimera minnesläckor, Kubernetes släpper nya stabila funktioner, och AWS tappar data i mellanöstern på grund av skadade tillgänglighetszoner. Parallelt med detta öppnar Alibaba källkoden för sina AI-verktyg (infoq.com, marktechpost.com) och utvecklare publicerar guider för avancerad LLM-inferensoptimering och GraphRAG-arkitektur (machinelearningmastery.com, towardsdatascience.com). Trenden är intressant eftersom den visar att AI-industrin går in i en mer industriell fas. Det handlar inte längre bara om att bygga modeller, utan om att bygga hårdvara, kluster och system (ofta i öppen källkod) som kan köra dessa modeller extremt snabbt, säkert och kostnadseffektivt.

____________________________________________________________________________

Vad som fungerade bra
Lagerindelningen i koden: Att dela upp allt i core, scrapers och llm styrda av ContentManager gjorde livet tusen gånger enklare. Det blev supertydligt vem som gör vad och hur testerna skulle skrivas.

Feedly-bluffen: Att låtsas vara Feedly mot RSS-flödena och en vanlig webbläsare mot artiklarna löste blockeringsproblemen på ett kick. Riktigt skön känsla när de röda felkoderna plötsligt lös med sin frånvaro.

Pipelinen kraschar inte för minsta lilla: Varje artikelskrapning ligger i en egen try/except. Om en webbsida bråkar eller dör så loggas det bara, och skraparen traskar obrytt vidare till nästa artikel. Ingen sajt ska få sänka hela mitt bygge.

Spärrarna för LLM-anropen: Jag la in spärrar så att programmet inte ens ringer modellen om det finns för få artiklar (onödigt att bränna pengar på att analysera tre ynka rubriker). Likaså finns ett maxtak så den inte skickar med en hel bibel av misstag och spränger token-gränsen.

Testerna: Fick ihop fem olika testfiler med pytest där allting mockas bort i conftest.py. Testerna springer igenom blixtsnabbt utan att man behöver ha internet igång eller bränna API-krediter.

Vad jag lärt mig
Web scraping är ett träsk: Man tror i sin naivitet att en RSS-feed bara är att läsa in, men tji fick jag. Ena sajten har tomma rader först, andra ljuger om teckenkodning och den tredje kastar ut dig för att du inte har rätt användaragent.

Trafilaturas hemliga inställningar: Att bara sätta timeout i trafilatura räckte inte. Man var tvungen att läsa in deras medföljande settings.cfg först och sen lägga sina egna värden ovanpå. Gjorde man inte det försvann bibliotekets dolda standardvärden och allt blev pannkaka. Det stod inte i manualen direkt, utan krävde en rejäl dos trial-and-error.

Byta modell-leverantör är busenkelt: Att använda ett OpenAI-kompatibelt API gör det hur smidigt som helst att bara byta base_url till evroc utan att behöva skriva om koden.

Separation of concerns på riktigt: Jag fattar grejen nu. Att separera på saker och inte kasta in 500 rader spaghettikod i en enda main.py gör underverk för både psyket och testbarheten.

Svagheter och problem
Den komiska elefanten i rummet (Fulltexten som aldrig används!):
Här kommer projektets största ironi: jag sitter alltså och sliter med trafilatura, bygger timeouts och spoofar webbläsare för att tanka ner hela artiklarnas brödtext... och sen gör jag absolut ingenting med den! Min ursprungliga idé var faktiskt riktigt smart: först göra en snabb trendanalys på bara rubrikerna, och därefter göra ett till LLM-anrop där pipelinen skickar med fulltexten för de relevanta artiklarna för att summera vad nyheterna faktiskt handlade om. Men när jag sedan inser att jag använder TVÅ AI anrop, och det känns helt ärligt talat inte jättekul att använda två anrop bara för en trendanalys, så valde jag att istället bara skicka med titlarna. Men så tänkte jag inte på att jag aldrig använde nyhetstexten xD Men men, så kan det gå. Hade jag gjort om hade jag nog ändå kört två ai anrop, just för att få en så bra summering som möjligt, och även skickat med summaries i första anropet för trend-analysen.

Hängande flöden och McKinsey-kaoset:
I början hade jag inga timeouts. När jag testade McKinseys flöde stod hela programmet och snurrade i flera minuter och bara glodde ut i tomma intet innan det gav upp. Lösningen blev att sätta hårda tidsgränser i settings.json för både feed och nedladdning.

Hårdkodningsfällan:
I början tog jag mycket hjälp av AI, och plötsligt var allt hårdkodat rakt in i Python-filerna: modellnamn, temperatur, gränsvärden och själva promptarna. Ville jag ändra ett ord fick jag gå in och rota i källkoden. Jag rev ut allt och flyttade över det till config/settings.json så man slipper öppna koden för att byta modell eller justera system-prompten.

Ingen snygg felöversikt:
Om trafilatura misslyckas med en artikel loggas det i stunden och summeras som typ "10 av 12 lyckades". Men jag har ingen sparad tabell över vilka adresser som faktiskt sket sig. Man borde haft en tracker-tabell i SQLite som sparar länk och felorsak och spottar ut en pandas DataFrame, så man enkelt ser vilka källor som är ruttna och borde sparkas ut ur sources.json. Hinner dock inte bygga det nu.

Svårt att veta om modellen yrar (hallucinationer):
Eftersom bara rubriker skickas in just nu kan modellen mycket väl sitta och fantisera ihop trender som knappt existerar. En bootstrap-inspirerad lösning hade varit att köra flera LLM-anrop på olika slumpmässiga delar av rubrikerna och kräva att modellen listar exakt vilka rubriker den baserar varje trend på. Då kan man räkna ut hur många svar som pekar på samma källor och få ett kvitto på om trenden är på riktigt eller bara fria fantasier.

Småskavanker i koden:
load_sources() kraschar rakt av vid trasig JSON eftersom den saknar try/except (till skillnad från load_settings()). SQLite sparar datum utan tidszoner (funkar så länge man håller sig till UTC, men inte helt vattentätt). Dessutom körs allt sekventiellt istället för asynkront med asyncio, vilket gör att det tar sin lilla tid om man skulle smacka in 50 källor till.

Koppling till yrkesrollen
Hela den här resan (hämta data, tvätta bort skit, rensa dubbletter, lagra i databas, välja ut russinen ur kakan, mata in i en modell och spotta ut resultatet) är i princip vardagsmat för en Data Scientist som bygger ETL- och maskininlärnings-pipeliner.

Datavård är A och O: Att rensa dubbletter med databasbegränsningar och automatiskt kasta gammal data är precis sånt man måste göra i riktiga produktionssystem för att modeller inte ska matas med gammalt skräp.

Använd LLM där det passar: Att fatta vad en språkmodell är bra på (sammanfatta stora textmassor) och vad den suger på (faktakoll utan källhänvisning, hålla nere token-kostnader) är sjukt viktigt om man ska bygga vettiga AI-lösningar idag.

Struktur före fulhack: Att separera konfiguration från logik och bygga testbar kod gör att man faktiskt kan schemalägga grejerna och sova gott om natten utan att allt brakar ihop.

Betygsreflektion (G eller VG?)
Jag tycker definitivt att det här arbetet landar på ett VG.

Jag har inte bara snickrat ihop ett enkelt engångsskript, utan byggt en hel liten arkitektur som är testad, modulär och välpaketerad med src-layout och pyproject.toml. Jag kan förklara exakt varför koden ser ut som den gör: varför Feedly-bluffen behövdes mot sajtspärrarna, varför trafilatura behövde seedas från sin egen config-fil, och varför isolerad felhantering per artikel räddar hela körningen. Dessutom är jag fullt medveten om projektets svagheter (som det lite fåniga faktumet att jag skrapar fulltext utan att använda den än, och avsaknaden av aggregerad felstatistik). Projektet visar att jag har stenkoll på helheten, varför valen gjordes och vad som krävs för att bygga en robust pipeline i praktiken.

Källor
trafilatura - https://trafilatura.readthedocs.io
Och pappret bakom: Barbaresi, A. (2021). "Trafilatura: A Web Scraping Library for Text Discovery and Extraction." ACM. Det var här jag till slut förstod varför ConfigParser måste seedas från bibliotekets egna settings.cfg.

OpenAI Python SDK - https://platform.openai.com/docs/api-reference och https://github.com/openai/openai-python
Grunden för hur jag byggde LLMClient. Att SDK:n funkar mot valfri OpenAI-kompatibel endpoint via base_url.

evroc (modell-slutpunkt) - https://models.think.evroc.com
Den svenska provider jag pekar om base_url till i settings.json.

pytest - https://docs.pytest.org
Fixtures, monkeypatch och tmp_path - allt jag behövde för att mocka bort nätverk, databas och API-krediter i testerna.

Python sqlite3 - https://docs.python.org/3/library/sqlite3.html
UNIQUE-constraint, IntegrityError och tidshantering i UTC.

Stack Overflow-trådar om feedparser encoding-problem och trafilatura User-Agent-hantering - för de specifika problemen där dokumentationen inte räckte till och jag fick googla mig fram.

AI - både Gemini Pro och GLM-5.2, för att skriva kod, granska kod och förklara delar jag inte förstod