# TOOLS.md — Local Notes (Orion / market-intelligence-engine)

## Watchlist (starter — confirm + extend on first session)

### Direct / adjacent apps

- Calm, Headspace, Balance, Oak, Open, Waking Up, Aura
- Breathwrk, Othership, Wim Hof (app)
- Ten Percent Happier, Insight Timer
- Apple Health Mindfulness / Apple Fitness+
- Neurotech adjacent: Muse, Flow Neuroscience (MDD device), Apollo Neuro

### Adjacent wellness-tech

- Sleep: Loóna, Rise, Calm (overlap)
- HRV / wearables: WHOOP, Oura, Apple Watch SDK changes
- Mental-health therapy apps: BetterHelp, Talkspace (regulatory frame)
- Corporate wellness: Spring Health, Modern Health, Headspace for Work, Lyra

### Info surfaces

- App Store category rankings (Health & Fitness, Medical)
- Play Store category rankings
- Crunchbase / PitchBook (if access) for funding signal
- Sensor Tower / Appfigures (if subscribed) for app-revenue signal
- Google News / arXiv for breathwork research
- FDA digital therapeutics announcements
- Press: Wired, The Verge, TechCrunch, STAT News, The Atlantic (wellness angle)

## Obsidian vault (durable memory)

- **Competitor dossiers:** `Topics/Competitors/<Name>.md` — per-company: product, pricing, positioning, last material move, so-what
- **Funding log:** `Topics/Funding Log.md` — raises/M&A in adjacent space, with source links
- **Trend notes:** `Topics/Market Trends.md` — quarterly synthesis
- **Regulatory signal:** `Topics/Regulatory.md` — FDA / DTx / privacy law changes
- **Research signal:** `Topics/Breathwork Research.md` — papers that affect how we talk about the product
- **Narrative windows:** `Topics/Narrative Windows.md` — what the press is covering right now that Vayu could fit into
- **Weekly briefs:** `Sessions/YYYY-MM-DD-mie-weekly-brief.md`
- **Material-move alerts:** `Sessions/YYYY-MM-DD-mie-<slug>.md`
- **My daily memory:** `agents/market-intelligence-engine/memory/YYYY-MM-DD.md`
- **My durable lessons:** `agents/market-intelligence-engine/LEARNINGS.md`
- **My trajectories:** `logs/trajectories/market-intelligence-engine/<session_id>.jsonl`

## Discord

- **Guild:** `1469395433360195777` (Vayu Prana Labs)
- **My channel:** `1472864038122815602` (`#market-intelligence-engine`)
- **My Discord identity:** own bot (`bot_token_env: MIE_BOT_TOKEN`)
- **My webhook:** `MIE_WEBHOOK_URL`

## Cross-agent routes I use

| Who | When I route to them | Channel |
|---|---|---|
| `main` (Vayu) | material competitive move, regulatory signal, narrative window | `1469505325102006490` |
| `deepali` (CDO) | positioning language shift, brand voice implication | `1469503216545693766` |
| `marketing` (Mira) | angle worth testing in outbound | `1469778689322647800` |
| `ads` (Ember) | category trend affects channel choice | `1471218500969435156` |
| `social-media` (Echo) | narrative window worth posting into | `1471339567071101045` |
| `media` (Pixel) | a comparison / explainer worth producing | `1469500272802926653` |
| `project-manager` (Tempo) | task state | `1470690373667127420` |

## Cadence

- **Daily:** sweep watchlist surfaces. Only ping the channel if something material happened.
- **Monday 09:00:** weekly brief. Fixed format: new moves, rank changes, funding, press, regulatory, research, so-what.
- **Monthly:** update each competitor dossier.
- **Quarterly:** full `Topics/Market Trends.md` refresh.

## Runtime baseline

- Model: `claude-opus-4-6` (CLI default)
- Timezone: Pacific (Vancouver)
