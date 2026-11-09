# CookQuest + Gastrolabe

> **Public release note:** This is a clean public copy of a private hackathon
> repository, created after credentials were found in its development history.
> Old commits and pull requests are excluded; configure your own local `.env`.

Turn fridge ingredients into a personal world of culinary discovery.

## Features and workflow

**Home → Fridge photos → Confirm ingredients → Recipes → Three questions → Map → Recipe instructions**

- Gemini recognizes fridge ingredients; users correct or add ingredients.
- Recommendations aim for 10–20 dishes, allowing at most two missing essential ingredients.
- One additional AI plausibility review excludes clearly incoherent dishes. This is not external verification or kitchen testing.
- The map uses cost/time coordinates, a personal Zenith and distance-based brightness.
- Ivory stars need no additional ingredients; blue stars need ingredients. Small display jitter never changes source coordinates.
- Recipe instructions are shown without cooking-photo uploads or scoring.

## Getting started — Windows

Requires Python 3.14 and two Gemini API keys. Open PowerShell in this project folder:

```powershell
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
.venv\Scripts\python.exe -B -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

Before starting the app, open the generated `.env` and provide both credentials:

```dotenv
FRIDGE_GEMINI_API_KEY=
FRIDGE_GEMINI_FREE_TIER_CONFIRMED=true
GASTROLABE_GEMINI_API_KEY=
GASTROLABE_GEMINI_FREE_TIER_CONFIRMED=true
```

The web interface can start without keys, but fridge recognition, recipe generation,
Zenith and Empathy Bridge require valid API keys. Keep `.env` local: it is ignored by
Git and must never be committed.

Open **http://127.0.0.1:8001/**.
Keep the terminal running; next time only run the last command.
`start.cmd` is an alternative launcher. Do not use Live Server or open HTML directly.

## Where to edit

```text
app/
  main.py                   FastAPI entry point, errors, Flask mounts, static files
  config.py                 Database and cache settings
  api/integration.py        Inventory, recommendation and recipe-method APIs
  fridge/
    api.py                  Photo upload and detection routes
    ai.py                   Shared fridge Gemini client
    detection.py            Image recognition and validation
    recommendations.py      Original standalone recipe logic and shared helpers
    dietary.py              Ingredient/dietary helpers
    images.py               Recipe photos + autocomplete ingredient catalogue
    demo_data.py            Original standalone demo fixtures
  recipes/
    discovery.py            Recommendation workflow, contracts and persistence
    generated.py            Generation, ingredient validation and AI review
    ingredients.py          Aliases, ownership and ingredient-role rules
    role_classifier.py      Retained legacy source-search classifier
  gastrolabe/
    app.py                  Flask routes + Zenith and Empathy Bridge AI
    store.py                Coordinates, recipe IDs and isolated map sessions
    templates/index.html    Gateway and map HTML
  frontend/
    index.html              Home, fridge, inventory and recipe screens
    css/style.css           Shared/main design
    css/gastrolabe.css      Map styles
    js/                     Journey, inventory, map, embedding and GIF playback
    assets/                 Active animation, preserved original GIF, placeholders
```

## API and configuration

Interactive API docs: **/docs**. Main endpoints:

- `POST /fridge/api/detect`
- `GET /api/v1/integration/ingredients`
- `POST /api/v1/integration/recipes/discover`
- `POST /api/v1/integration/recipes/{id}/check`
- `GET /api/v1/integration/recipes/{id}/method`
- `POST /gastrolabe/calculate_zenith?session=…`
- `POST /gastrolabe/empathy_bridge?session=…`
- `GET /gastrolabe/selection/{id}?session=…`

See `.env.example` for server-only keys and model settings. Fridge and Gastrolabe
keys remain separate. Do not commit real credentials or put them in browser code.

`COOKQUEST_CACHE_ENABLED=false` is the default: reusable AI/discovery and HTTP
caches are disabled. Repeat requests can use more API quota. Recipe records,
inventory and map sessions remain persisted for navigation and refresh.
`COOKQUEST_DATABASE_PATH` defaults to `data/cooking.db`; the legacy
`COOKING_DATABASE_PATH` alias is still accepted.

`fridge_recipes.json` is a compatibility export. Actual requests use isolated
session records and snapshots under `data/maps/`; these are user data, not disposable cache.

[TheMealDB](https://www.themealdb.com/api.php) supplies optional similar-dish
photos and ingredient suggestions, not proof of a generated recipe's existence.
Its test API is intended for development/education; check terms before public
release. `MEALDB_ENABLED=0` disables image/catalogue lookups. Manual entry works
without the catalogue. Legacy source-recipe sessions remain supported.

## Security

- `.env.example` contains variable names and safe defaults only.
- Real API keys, local databases, uploads, generated maps and recipe snapshots are excluded from Git.
- If a key is ever committed, revoke it in Google AI Studio and create a new one; deleting the file from a later commit does not remove it from Git history.

