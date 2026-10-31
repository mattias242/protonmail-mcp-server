# protonmail-mcp

MCP-server (MCP Python SDK 2.x, `MCPServer`, stdio; HTTP med `--http`) som ger en LLM tillgång till Proton Mail via ProtonMail Bridge på localhost: IMAP för
läsning och ändringar, SMTP för skick. 29 verktyg, tre resurser (med prenumerationer), fyra promptar och bevakning
av nya mejl med IMAP IDLE. README.md (engelska, publik) beskriver verktygen, inställningarna och säkerhetsmodellen
för användare; den här filen är för den som ändrar koden.

## Köra lokalt

```bash
uv sync
cp .env.example .env            # PROTONMAIL_USERNAME + Bridge-lösenordet
uv run protonmail-mcp           # stdio-transport
uv run protonmail-mcp --http    # HTTP på 127.0.0.1:8765/mcp, kräver PROTONMAIL_HTTP_TOKEN (Perplexity)
npx @modelcontextprotocol/inspector uv run protonmail-mcp   # testa interaktivt
```

`python -m protonmail_mcp` och `python -m protonmail_mcp.server` startar samma server med samma verktyg. `.env` läses från projektroten även när
servern startas från en annan katalog; en `.env` i arbetskatalogen vinner, miljövariabler vinner över båda.
Konfiguration för Claude Desktop och Claude Code (`uv --directory /path/to/… run protonmail-mcp`) står i README.md.

## Arkitektur och modulkarta

`server.main()` anropar `register_tools()`, som importerar verktygsmodulerna (importen registrerar verktygen) och
registrerar resurser, prenumerationer och promptar, och kör sedan stdio — eller, med `--http`, Streamable HTTP via
`http_server.py` (en lifespan och en `IMAPClient` för alla HTTP-sessioner). `lifespan` läser inställningarna, skapar
bilagekatalogen, en delad `IMAPClient` (ansluter lat, vid första anropet), en `SMTPClient` (ansluter per skick) och
startar `MailWatcher` (egna IDLE-anslutningar). Loggning går bara till stderr — stdout är MCP-protokollet.

| Modul | Ansvar |
|-------|--------|
| `__init__.py` | `__version__` ur paketets metadata; versionens enda källa är `version` i pyproject.toml (servern anger den i serverInfo) |
| `_sdk.py` | Det enda stället som importerar MCP-SDK:ns serverdelar (`MCPServer`, `Context`, `ToolError`, `Resource`, `ResourceTemplate`, `ToolAnnotations`); övriga moduler importerar härifrån |
| `__main__.py` | `python -m protonmail_mcp` startar `server.main()` |
| `http_server.py` | HTTP-läget (`protonmail-mcp --http`): Streamable HTTP på `/mcp` bakom API-nyckeln (`ApiKeyProtected`), Host-kontroll mot `PROTONMAIL_HTTP_ALLOWED_HOSTS`, start med uvicorn |
| `server.py` | Serverinstansen `mcp` (`_GuardedMCPServer` vägrar verktyg efter policy innan de körs och översätter tools/prompts/resources-listorna), `INSTRUCTIONS` till modellen, `apply_language` (språket ur inställningarna), `lifespan`, `register_tools()`, `main()` och lyssnaren som gör nya mejl till resursnotiser |
| `config.py` | `Settings` (pydantic-settings, prefix `PROTONMAIL_`), tolkning av `ALLOWED_RECIPIENTS` (IDNA2008) och `WATCH_MAILBOXES` |
| `i18n.py` | Språket i det klienten ser (`PROTONMAIL_LANGUAGE`): `_` översätter svenska texter med platshållare till det aktiva språket, `set_language`/`language`, `translate_lookup` för tabellerna i `texts_en.py` |
| `texts_en.py` | Engelska texter: `MESSAGES` (svensk text → engelsk), `TOOLS`/`PROMPTS`/`RESOURCES` (titel och beskrivning per namn), `PARAMS` (parameterbeskrivningar) och `INSTRUCTIONS` |
| `policy.py` | Skrivskydd, skickläge, mottagarlista, mottagar- och timgräns; `write_operation`-dekoratorn och verktygslistorna `WRITE_TOOLS`/`DRAFT_TOOLS`/`SEND_TOOLS` |
| `audit.py` | Granskningslogg: en JSON-rad per skick och skrivande IMAP-operation, maskerad på stderr, fullständig i `PROTONMAIL_AUDIT_LOG` |
| `imap_client.py` | `IMAPClient`: den delade anslutningen med lås (`_serialized`), `_BridgeProtocol`, validering och kvotering, läsning, sökning, flaggor, flytt, radering, mappar |
| `imap_bulk.py` | Massoperationer (1–100 UID:n i en mapp): flaggor, flytt, radering med svar per UID (`ok`/`failed`) |
| `imap_labels.py` | Etiketter (`add_label` med UID COPY, `remove_label` via X-Pm-Internal-Id i etikettvyn) och stjärna (`\Flagged`); identitetsjämförelsen `_find_in_label` som flytt, radering och svar också använder |
| `imap_drafts.py` | Utkast i Drafts: APPEND, ersättning (gamla till Trash) och borttagning efter skick |
| `imap_threads.py` | Trådar ur Message-ID/In-Reply-To/References-kedjan i All Mail, med tak på sökningar, ID:n och tid |
| `imap_idle.py` | `MailWatcher`: en egen IDLE-anslutning per bevakad mapp (`_WatchProtocol`), vattenmärken, händelsekö, lyssnare som egna uppgifter, återanslutning med växande väntetid, återhämtning efter UIDVALIDITY-byte |
| `mailbox_names.py` | Modified UTF-7 för mappnamn (RFC 3501 §5.1.3) och `describe_mailbox` (type, selectable, special_use) |
| `email_parser.py` | RFC 822 → dict för verktygen: avkodning, body_format, bilagelistor, sanering och `suspicious_content` |
| `html_text.py` | HTML → synlig text; hoppar över gömda element och rapporterar dem; fail-closed |
| `sanitize.py` | Rensning av osynliga och vilseledande tecken, adressvisning med `[U+XXXX]`, injektionsheuristik, `UNTRUSTED_CONTENT_NOTICE` |
| `attachments.py` | Bilagekatalogen: filnamnssanering, säker sparning, kontroll av filer som ska skickas, spärr mot hemligheter |
| `smtp_client.py` | `SMTPClient`: bygger meddelanden, validerar adresser, den enda leveransvägen `_deliver` |
| `resources.py` | Resurserna `mailboxes://`, `mailbox://{mailbox}`, `email://{mailbox}/{uid}` och URI-kodningen |
| `subscriptions.py` | `resources/subscribe`, register över prenumererande sessioner, `notify_resource_updated` och `TOOL_CHANGES` (vilka resurser varje skrivverktyg ändrar) |
| `prompts.py` | Promptarna `sammanfatta_olasta`, `triagera_inkorgen`, `svara_pa_mejl`, `rensa_nyhetsbrev` |
| `tools/__init__.py` | `tool(...)`-dekoratorn med obligatorisk klassning och förinställningarna `READ_ONLY`, `SETS_STATE`, `ADDS`, `DESTRUCTIVE`, `SENDS`; efter-anrop-kroken `notify_after` som ger resursnotiser |
| `tools/_errors.py` | `with_error_messages`/`describe_error`: undantag → begripliga fel på svenska, lösenordet maskat |
| `tools/_types.py` | Parametertyper `Mailbox`, `OptionalStr`, `AttachmentArg` |
| `tools/_uids.py` | Parametertyperna `Uid` och `UidArg` (ett UID eller en lista med 1–100) |
| `tools/mailboxes.py` | `list_mailboxes`, `get_mailbox_status` |
| `tools/messages.py` | `list_emails`, `get_email`, `get_email_headers` |
| `tools/search.py` | `search_emails` |
| `tools/threads.py` | `get_thread` |
| `tools/send.py` | `send_email` |
| `tools/reply.py` | `reply_to_email`, `forward_email` och byggarna `build_reply`/`build_forward` som utkasten återanvänder |
| `tools/drafts.py` | `create_draft`, `create_reply_draft`, `create_forward_draft`, `update_draft`, `send_draft` |
| `tools/attachments.py` | `get_attachment` |
| `tools/manage.py` | `mark_email_read`, `mark_email_unread`, `move_email`, `delete_email` |
| `tools/labels.py` | `add_label`, `remove_label`, `flag_email`, `unflag_email` |
| `tools/folders.py` | `create_folder`, `delete_folder`, `rename_folder` |
| `tools/new_mail.py` | `check_new_emails`, `wait_for_new_email` |

`tests/unit/test_dokumentation.py` och `tests/features/dokumentation.feature` kräver att tabellen ovan har exakt
modulerna i `src/protonmail_mcp/`, och att README:s tabeller har exakt de verktyg, resurser, promptar och
inställningar som koden har. Lägger du till något: uppdatera tabellerna i samma commit.

## Designprinciper (gäller alla framtida ändringar)

- **Fail-closed.** Kan något inte avgöras säkert: vägra, eller ge mindre — aldrig mer. Exempel: HTML som inte går
  att tolka ger mindre text, en tvetydig adressrubrik i ett utkast vägrar skicket, en bilagekatalog som inte går att
  verifiera används inte, ett nekat SELECT kastar anslutningen i stället för att köra vidare i fel mapp.
- **Läs alltid med `BODY.PEEK`.** `BODY[]` markerar mejlet som läst. Inget läsverktyg, ingen resurs och ingen
  bevakning får ändra flaggor.
- **IMAP via klientens hjälpare.** `await imap._select(mailbox)` (kontrollerar svaret; Bridge står kvar i förra
  mappen efter ett misslyckat SELECT, så anslutningen kastas; en `\Noselect`-behållare ger fel),
  `await imap._uid(...)` (UID-kommandon med timeout), `await imap._require_uid(mailbox, uid)` (UID FETCH före varje
  skrivning, så att ett UID från fel mapp ger fel i stället för att ändra fel mejl). Validera med `_validate_mailbox`,
  `_validate_uid` och `_imap_quoted`, kvotera mappnamn med `_quote_mailbox` (kodar även modified UTF-7) — innan
  `_ensure_connected`, så att ogiltiga värden aldrig når Bridge. Allt som använder den delade anslutningen körs under
  `_serialized`, som även kastar anslutningen vid timeout eller avbrott. Tidsgränser i IMAP-modulerna sätts med
  `_with_timeout` (`asyncio.timeout`): ett avbrott utifrån ska alltid nå fram, även när det kommer just när Bridge
  svarar (`tests/unit/test_efterfix_l7_avbrott*.py`).
- **Inga nya metoder i `IMAPClient` för nya funktioner.** Skriv dem som modulnivåfunktioner i en egen modul
  (`imap_drafts.py`, `imap_labels.py` …) med `IMAPClient` som första argument, dekorerade med `_serialized`.
- **Skrivande IMAP-funktioner dekoreras med `policy.write_operation(action)`** (närmast funktionen, under
  `_serialized`; `draft=True` för utkast). Den kontrollerar `READ_ONLY`/`SEND_MODE` innan något valideras eller
  skickas och skriver granskningsraden, även för vägrade anrop. Nya skrivande verktyg läggs dessutom i rätt lista i
  `policy.py` så att servern vägrar dem redan före körning; `tests/unit/test_llm_skick_skrivskydd.py` kräver att
  varje registrerat verktyg är klassat.
- **En enda skickväg.** Allt SMTP går genom `SMTPClient._deliver` (via `send_email` eller
  `send_prepared_message`), som kontrollerar skickpolicy, mottagarlista, mottagargräns och timgräns före
  `_smtp_send`. Anropa aldrig aiosmtplib någon annanstans.
- **Verktyg registreras med `@tool(titel, **KLASSNING)`** från `tools/__init__.py` — aldrig `mcp.tool` direkt.
  Klassningen (read_only, destructive, idempotent, open_world) måste anges; MCP:s egna standardvärden är
  destruktiv + öppen värld. Lägg verktyget i tabellen i `tests/unit/test_llm_kontrakt_annoteringar.py`, i README:s
  verktygstabell och importera modulen i `server.register_tools()`. Ett skrivande verktyg ska också klassas i
  `subscriptions.TOOL_CHANGES` (vilka resurser ett lyckat anrop ändrar) eller `TOOLS_WITHOUT_MAILBOX_CHANGES`;
  `tests/unit/test_efterfix_resurser_verktygsnotiser.py` fäller annars. Strikt typade parametrar (`Annotated`/`Field`
  med gränser, `Literal`, `Mailbox`, `OptionalStr`, `Uid`/`UidArg`); `mailbox` är obligatorisk för allt som utgår
  från ett UID. Docstringen (svenska) säger vad som händer i Proton, vad som inte händer och vilka fel som kan komma.
- **Allt som når klienten går genom `i18n._`.** Felmeddelanden, `note`-/`warning`-texter, promptarnas texter och
  annat som modellen eller användaren läser skrivs på svenska som mall med platshållare —
  `_("Inget meddelande med UID {uid} i {mailbox}.", uid=uid, mailbox=mailbox)` — och får sin engelska
  översättning i `texts_en.MESSAGES` med samma platshållare (ordföljden får skilja). Ett nytt verktyg får titel
  och beskrivning i `texts_en.TOOLS`, en ny prompt eller resurs i `PROMPTS`/`RESOURCES`, en ny
  parameterbeskrivning i `PARAMS`; `tests/unit/test_feat_sprak_en.py` fäller annars. Kod som känner igen ett
  fel på en fras i texten (`imap_labels.AMBIGUOUS`, `imap_bulk._UNCHECKED`, `_errors._UNREACHABLE_PREFIX`)
  jämför mot `_(KONSTANT)`. Loggrader (stderr) och fel som uppstår innan inställningarna lästs (`config.py`,
  `http_server.py`) är bara på svenska. Det registrerade (docstrings, titlar, `INSTRUCTIONS`) förblir svenskt;
  engelskan byts in när listorna skickas, så kontraktsfacit gäller oförändrat.
- **Fel är `ValueError` med en begriplig svensk text** som säger vad som hände och vad modellen kan göra.
  `with_error_messages` gör om nätverks-, inloggnings- och SMTP-fel; lösenordet maskas i allt som returneras och
  loggas. Ett skick som delvis levererats ska aldrig se ut som ett misslyckat (modellen skickar då om).
- **Sanera allt avsändarstyrt** (ämne, namn, brödtext, HTML-text, bilagenamn, rubriker) med `sanitize.py`
  innan det når modellen, och märk det (`untrusted_content_notice`, `sanitized`, `suspicious_content`). Ur
  adresser tas inget bort — osynliga tecken visas som `[U+XXXX]` och skick till sådana adresser vägras.
  Identifierare (Message-ID m.fl.) visas bara som ASCII-`<msg-id>`. Nya utdatavägar (resurser, promptar, nya
  verktyg) ska gå genom samma funktioner, helst genom att anropa befintliga verktygsfunktioner.
- **Linjär tid i allt som tolkar avsändarstyrd indata.** Inga reguljära uttryck med bakåtspårning, inga
  kvadratiska slingor, tak på antal och längd. Parsrarna har linjäritetstester (dubblad indata ≈ dubbel tid).
- **Serverinstruktionerna och promptarna är en del av kontraktet.** `INSTRUCTIONS` i `server.py` och `_SAFETY` i
  `prompts.py` säger: UID per mapp, utkast före skick, mejlinnehåll är opålitligt, radering till Trash, högst 100
  UID:n per massoperation. Ändras beteendet ska texterna följa med.

## Bridge-fakta (verifierade live, läs-endast)

- **Anslutning.** IMAP på 127.0.0.1:1143 är okrypterad TCP (STARTTLS erbjuds men krävs inte; `IMAP4_SSL` ger
  `SSL: RECORD_LAYER_FAILURE`). SMTP på 1026 är direkt TLS med självsignerat certifikat (`CERT_NONE` och
  `check_hostname=False` som standard).
- **Capabilities.** `IDLE`, `MOVE` och `UIDPLUS` annonseras. Flytt görs med `UID MOVE` (ingen reserv utan MOVE);
  bevakningen kräver IDLE. APPEND till Drafts ger normalt `APPENDUID`; annars söks utkastet upp via Message-ID.
- **Mappar och etiketter.** INBOX, Archive, Sent, Spam, Trash, Drafts och `Folders/*` är exklusiva mappar;
  `Labels/*` och Starred är etiketter. All Mail innehåller allt. `Folders` och `Labels` är `\Noselect`-behållare som
  ändå svarar OK med 0 mejl på SELECT/STATUS — därför frågar `_select` LIST när en mapp ser tom ut. CREATE/RENAME
  kräver prefixet `Folders/` eller `Labels/`; underetiketter finns inte; `Folders/A/B` kräver `Folders/A`. Ett `/` i
  ett etikettnamn är escapat som `\/` och ska skickas tillbaka så.
- **COPY/MOVE/EXPUNGE.** COPY till en mapp flyttar, till en etikett lägger till etiketten. MOVE tar bort källan om
  den är en etikett eller målet är en mapp. EXPUNGE från en etikett tar bort etiketten, från en mapp hamnar mejlet
  bara i All Mail, från Trash/Drafts raderas det permanent — men bara om Protons UnlabelMessages lyckas för hela
  omgången (se Code=2501 nedan). Ligger mejlet redan i målet tas det först bort — i Trash/Drafts blir det permanent
  radering. Allt till/från All Mail och INBOX ↔ Sent nekas. Därför: radering = `UID MOVE` till Trash; permanent bara
  `UID STORE \Deleted` + `UID EXPUNGE <uid>` i Trash/Drafts; aldrig EXPUNGE utan UID.
- **Ett mejl, flera vyer.** Ett mejl till sig själv är ETT Proton-mejl med både Inbox och Sent (inte två): det syns i
  INBOX och Sent med var sitt UID men samma Message-ID, tid och storlek, och en gång i All Mail. Skickas ett utkast
  till sig själv blir utkastet självt det skickade mejlet (samma Proton-id och Message-ID).
- **Mejlets identitet = `X-Pm-Internal-Id`.** Bridge sätter rubriken (Protons mejl-id, 88 tecken base64url) på varje
  mejl i alla vyer (INBOX, Sent, Trash, Spam, All Mail; aldrig upprepad), och samma Proton-mejl har samma värde i
  alla vyer. `SEARCH HEADER X-Pm-Internal-Id "<id>"` fungerar och ger exakt det mejlet. Message-ID + tid + storlek
  räcker INTE: live finns två olika mejl i Trash med samma tre värden. Allt som letar upp "samma mejl" i en annan
  vy (`imap_labels._find_in_label`: L7-kontrollen, `remove_label`, Starred, kontrollen i All Mail efter 2501,
  svar på egna mejl) jämför därför id:t; utan id används de tre värdena som reserv, och flera träffar är då
  tvetydiga och ger fel i stället för ok.
- **`X-Pm-Origin`.** `internal` på mejl skickade inom Proton (alla egna mejl i Sent, och mejl från andra
  Proton-konton), `external` på mejl utifrån, `import` på importerade. `reply_to_email` räknar ett mejl som eget
  skickat bara om From är kontot OCH (exakt en `X-Pm-Origin: internal` eller samma id finns i Sent) — From kan
  förfalskas.
- **Vyerna släpar 20–40 s.** Bridge hämtar händelser från Proton var 20:e–40:e sekund (`EventPeriod` 20 s +
  `EventJitter` 20 s i `internal/user/user.go`). Vyer som beror på Protons tillstånd — Starred, Sent, All Mail och en
  mapp som mejlet just flyttats ur via en annan vy — visar det gamla läget så länge. `-FLAGS \Flagged` tar bort
  stjärnan i Proton direkt men mejlet ligger kvar i Starred-vyn; `unflag_email` tar därför bort det därifrån med
  UID EXPUNGE i Starred (bästa försök: fel, även timeout, redovisas i `note`). EXPUNGE i Starred tar bort stjärnan i
  Proton, så bara UID:n med samma X-Pm-Internal-Id som redan saknar `\Flagged` tas bort.
- **Code=2501 ("Message does not exist").** Flyttas ett mejl till Trash som redan ligger där — typiskt Sent-kopian av
  ett mejl till sig själv inom 20–40 s efter att INBOX-kopian lagts i Trash — svarar Bridge `NO labeling messages:
  … (Code=2501)`, men mejlet är då raderat permanent. `move_email`/`delete_email` letar därför först upp mejlen i
  Trash/Drafts via X-Pm-Internal-Id (`imap_bulk.already_in_target`, en SEARCH per 20 id) och skickar ingenting för
  dem som redan ligger där; de räknas som ok med en förklaring i `note`. Går det inte att avgöra (inget id och
  flera likadana mejl i målet) flyttas mejlet inte och hamnar i `failed`. Vid permanent radering kan Bridge svara
  `NO failed to unlabel messages … (Code=2501)`; mejlen försvinner då ur mappen vid nästa händelsehämtning.
  `permanent_delete_found` väntar upp till 45 s (`_GONE_TIMEOUT`) — utan IMAP-låset mellan kontrollerna, så att
  andra verktyg inte blockeras — och räknar ett försvunnet mejl som raderat först när det inte heller finns i All
  Mail (se nästa punkt).
- **Föräldralösa mejl efter 2501.** Bridge (`connector.go`, `removeMessagesFromMailboxWithUnlabelCallOnFolders`) gör
  UnlabelMessages för hela omgången och avbryter vid felet FÖRE DeleteMessage. Mejlet som redan var raderat i Proton
  ger 2501; alla andra mejl i omgången — vanliga som utkast, inget i koden gäller bara utkast — tappar Trash men
  raderas inte, och blir kvar osynliga i All Mail utan mapp (sett live för ett utkast). `permanent_delete_found`
  hämtar därför identiteten medan mejlen syns, letar upp de försvunna i All Mail, lägger dem som finns kvar i Trash
  igen med UID COPY och raderar dem där med UID STORE `\Deleted` + UID EXPUNGE. Går det inte att avgöra (ingen
  identitet, sökningen nekas eller hänger, flera likadana) blir mejlet `failed` med förklaring, aldrig `ok`.
  `send_draft` tar bort sitt utkast utan att vänta.
- **Stjärna = `\Flagged`**, globalt i alla vyer. Flytt till Starred skulle ta bort mejlet ur källmappen.
- **Mappnamn i modified UTF-7.** LIST-namnen avkodas; `_quote_mailbox` kodar alltid. Verktygen tar alltså bara emot
  läsbara namn (`Labels/Näs Samfällighet`), och den kodade formen blir dubbelkodad.
- **UID:n gäller per vy.** STATUS räknar per vy och etiketterna överlappar. `UID FETCH` av ett obefintligt UID svarar
  OK utan data; `FETCH 1:n` utanför vyn nekas.
- **Sökning.** Bridge söker i okodade rubriker (RFC 2047-ord matchas inte) och textdelarna ligger quoted-printable,
  så ämne/avsändare/mottagare filtreras lokalt mot avkodade värden och åäö-ord i text/body söks även i QP-form.
  `HEADER Message-ID` och `HEADER X-Pm-Internal-Id` är delsträngsmatchning — kontrollera exakt lokalt. En SEARCH i
  All Mail tar 3–8 s (~30 000 mejl).
- **Köade uppdateringar.** Bridge (Gluon) skickar `* n EXISTS` i svaret på *nästa* kommando i vald mapp och aldrig
  igen; aioimaplib kastar sådana rader utanför IDLE. Bevakningen söker därför efter varje IDLE-omgång och igen om en
  EXISTS kom i ett svar. Bridge kopplar ner en IDLE efter 29 minuter; den förnyas var 25:e.
- **Tomma mappar** svarar `NO` (no such message) på `UID SEARCH UID n:*` i stället för ett tomt svar. Bevakningen
  och verktygen för nya mejl söker därför inte när SELECT visar 0 EXISTS och tolkar just det svaret som inga mejl.
- **Misslyckat SELECT** lämnar den förra mappen vald (tvärtemot RFC 3501); därför kastas anslutningen.
- **internalid-referenser.** Bridge lägger `<X-Pm-Internal-Id@protonmail.internalid>` i References. Den pekar på
  mejlet självt (`get_thread` hoppar över den). Vid SMTP slår Bridge upp sådana referenser: ett vanligt mejl blir
  parentID (tråd och besvarad-markering), ett utkast hamnar i draftsToDelete och raderas efter skicket. Därför tar
  `update_draft` bort det gamla utkastets egen referens men behåller övriga, och `send_draft` behåller References men
  tar bort `X-Pm-*`-rubrikerna.

## Teststrategi

- **BDD först, på svenska.** Scenarier (Given/When/Then i `.feature`-filer under `tests/features/`) beskriver önskat
  beteende i domänens språk; stegen i `test_*.py` bredvid. Sedan **TDD**: enhetstest i `tests/unit/` som faller
  (RED), minsta ändring (GREEN), städa. Ett fel som inte går att få att falla i ett test fixas inte.
- **Nya tester i nya filer** (döpta efter ändringen) för att undvika krockar; ändra befintliga tester bara vid ett
  avsiktligt beteendebyte och säg det i commit-meddelandet.
- **Testspärren.** `tests/conftest.py` blockerar anslutningar till port 1143 och 1026 i alla tester som inte är
  märkta `integration` (`ConnectionRefusedError "… blockerad i tester"`). Ser du det felet har en attrapp fallit
  igenom — rätta attrappen. En ren `AsyncMock()` som `_client` duger inte: den behöver `select`/`login` som svarar
  OK och `protocol.state` (se `_imap_mock()` i `tests/unit/test_imap_client.py`).
- **Fejk-servrar i `tests/`** i stället för mockar där semantiken spelar roll: `fix_flytta_bridge.py`
  (flytt/radering/mappar), `feat_etiketter_bridge.py` (etiketter; X-Pm-Internal-Id med `internal_ids = True`),
  `efterfix_live_bridge.py` (Code=2501 vid permanent radering som i connector.go: bara `phantoms` raderas, övriga
  blir kvar i All Mail; Starred som släpar), `efterfix_slut_bridge.py` (X-Pm-Internal-Id påslaget, tvillingmejl
  med samma Message-ID/tid/storlek), `efterfix_l7_bridge.py` (ett mejl i INBOX och Sent, flytt till
  Trash av ett mejl som redan ligger där raderar permanent), `feat_utkast_bridge.py` (utkast),
  `feat_sok_fakebridge.py` och `fix_sok_fakebox.py` (SEARCH/FETCH som Gluon), `fix_imapkarna_fakeimap.py` (hängande
  och nekande Bridge), `efterfix_imap_avbrott.py` och `ny_mejl_fakeidle.py` (riktiga TCP-servrar: avbrott, IDLE),
  `fix_svar_support.py` (fångar SMTP), `llm_kontrakt_support.py` och `mcp_resurser_support.py` (riktig MCP-session i
  minnet).
- **Köra.** `uv run pytest` kör enhets- och BDD-testerna (integration väljs bort i `pyproject.toml`).
  `uv run pytest -m integration` kör mot riktiga Bridge och bara läsande (kräver `.env`); SMTP-testet skickar ett
  riktigt mejl till den egna adressen och körs bara med `PROTONMAIL_ALLOW_SEND=1`. Gör inga skrivoperationer mot
  riktiga Bridge vid utveckling — läs-endast-kontroller (LIST/STATUS/SELECT/SEARCH/FETCH BODY.PEEK) går bra.
- **Python 3.12 eller nyare** (`requires-python = ">=3.12"`). CI kör enhets- och BDD-testerna på 3.12, 3.13 och
  3.14; kör lokalt både standardversionen och den lägsta:
  `uv run --python 3.12 --isolated --with pytest --with pytest-asyncio --with pytest-bdd --with pytest-mock --with-editable . pytest tests/unit tests/features -q`.

## Arbetssätt

- **Trunk-based.** Små commits direkt på `main`; commit och push efter varje grön ändring. Inga långlivade brancher —
  är något halvfärdigt, göm det bakom en inställning.
- **Conventional Commits på svenska**, t.ex. `fix(imap): kontrollera SELECT-svaret`, `feat(utkast): …`,
  `docs(readme): …`.
- **Stage filer med namn**, aldrig `git add -A`/`git add .`; kontrollera med `git diff --cached --name-only`.
- **Nya beroenden** bara när de verkligen behövs, via `uv add`.
- Uppdatera README.md och den här filen i samma commit som beteendet ändras; dokumentationstesterna fäller annars.

## Kända begränsningar

- Brödtextsökning (`text`/`body`) med åäö hittar ungefär 95 %: ord som quoted-printable radbryter mitt i, ord i
  base64-delar och HTML-entiteter (`&ouml;`) missas.
- pydantics valideringsfel (saknad eller feltypad parameter) är på engelska; de uppstår i SDK:n innan
  verktygsfunktionen och `with_error_messages` körs.
- Homoglyfer (kyrilliskt `а` för `a` m.fl.) rensas inte, och `suspicious_content` fångar inte alla formuleringar
  (`1gn0re`, "SYSTEM: you must" …). Gömda element via `details`, `aria-hidden`, `noscript`, svg-text och
  `transform:scale(0)` känns inte igen.
- Notiser (`notifications/resources/updated`) ges för nya mejl i bevakade mappar och efter lyckade skrivverktyg i
  den här servern. Andra klienters ändringar av flaggor, etiketter, flyttar och mappar syns inte (bara nya mejl i
  `PROTONMAIL_WATCH_MAILBOXES`), och `rename_folder` notifierar inte undermappar som byter namn med föräldern.
- Prenumerationer finns bara för klienter på protokoll före 2026-07-28 (`resources/subscribe`, som Claude Code
  och Claude Desktop använder). `subscriptions/listen` (2026-07-28) stöds inte än: servern skapas med
  `subscriptions=False`, så den annonserar inga prenumerationer till sådana klienter. Verktyg, resurser och
  promptar fungerar på båda protokollen.
- Vattenmärkena för nya mejl och timräknaren för skick ligger i minnet och nollställs vid omstart. Avbryts
  `check_new_emails` efter att märket flyttats men innan svaret nått klienten räknas de mejlen inte längre som nya.
- Privata API:er som kan gå sönder vid uppgradering av beroenden (testerna täcker dem): `MCPServer`:s
  `_resource_manager._templates` (resursmallar med egna fel) och `_lowlevel_server` (hanterare för
  `resources/subscribe`; `instructions` byts av `server.apply_language`), `ServerSession._connection` (en session per anslutning; dess publika `exit_stack` glömmer
  sessionen vid nedkoppling) och klassbytet till `_BridgeProtocol` på aioimaplib-anslutningen. Testerna når SDK:n
  bara via `tests/sdk_support.py` och servern bara via `_sdk.py`, så ett SDK-byte ändrar i första hand de två.
  `tests/snapshots/mcp_kontrakt.json` är facit för allt en klient ser (scheman, fel, förmågor); skriv om det med
  `PROTONMAIL_UPDATE_SNAPSHOT=1` bara medvetet och förklara varje ändrat fält i commit-meddelandet.
- aioimaplib: `uid("search", …)` stöds inte — använd `uid_search()` eller `search()` + FETCH av UID. `list_emails`
  använder EXISTS + sekvensintervall i stället för `SEARCH ALL`, och FETCH görs i delar om högst 200.
