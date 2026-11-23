# Library developer guide

This guide explains how the `openstride` package (`src/`) works, and how to change or extend it. For how
to use the package, see the [library README](README.md).

**Contents:** [Set up](#set-up) · [Layout](#layout) · [How one call works](#how-one-call-works) ·
[Design rules](#design-rules) · [Add a new method](#add-a-new-method) ·
[When Strava changes its site](#when-strava-changes-its-site) · [Tests](#tests) ·
[Packaging and release](#packaging-and-release)

---

## Set up

```bash
git clone https://github.com/choonyongchan/OpenStride && cd OpenStride
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[test]" -r requirements-dashboard.txt httpx
python -m playwright install chromium
python -m pytest -q tests/library tests/e2e
```

The source folder is `src/`, but the installed package is called `openstride`. Hatch maps one to the
other (see [Packaging](#packaging-and-release)). With an editable install, `import openstride` loads
`src/` directly.

## Layout

| File | Role | Uses Playwright? |
|---|---|---|
| `src/__init__.py` | Public API: what `from openstride import ...` exposes (`__all__`), plus `__version__` | no |
| `src/errors.py` | `OpenStrideError` and its four subclasses | no |
| `src/_parse.py` | **Pure parsers.** Strava's display text and HTML/JSON turned into numbers and dicts. No I/O. | no |
| `src/models.py` | pydantic return types. The club models subclass stravalib's. | no |
| `src/_browser.py` | `Browser`: one Chromium plus the saved session. `goto`, `fetch`, `pause`, `close`, and `login()`. | yes |
| `src/scraper.py` | `ClubScraper`: one method per Strava page. It orchestrates `Browser` and `_parse`. | through `Browser` |
| `src/client.py` | `Client(stravalib.Client)`: sends the three removed methods to a lazy `ClubScraper` | through `ClubScraper` |
| `src/cli.py`, `src/__main__.py` | The `openstride` command (argparse) | through `ClubScraper` |

The dependencies only point one way:

```text
cli.py ─┐
client.py ─┴─> scraper.py ─┬─> _browser.py ──> errors.py
                           ├─> _parse.py  ──> errors.py
                           └─> models.py  ──> _parse.py (split_name)
```

Modules that start with `_` are private. Anything a user may import must be added to `__init__.py` and
`__all__`.

## How one call works

This is what `ClubScraper.get_club_activities(123456)` does:

1. **`Browser.goto("/clubs/123456/recent_activity")`.** On the first call, `_open()` starts
   Playwright and launches Chromium through `_launch()`. `_launch()` loads `auth_state.json` and sets a
   normal desktop user agent and viewport. If Strava redirects to a login page, `goto` raises
   `SessionExpired`. Otherwise it waits a random 0.4 to 0.9 seconds (`pause()`).
2. **`Browser.fetch("/clubs/123456/feed?...")`.** This runs `FETCH_JS`, a `fetch()` inside the
   logged-in page, so the request carries the real cookies and is same-origin. The status maps to an
   error:
   - 401, or a redirect to login: `SessionExpired`;
   - 429: `RateLimited`;
   - any other non-2xx: `MarkupChanged`.
3. **`json.loads`**, then **`_parse.feed_page(entries)`**. This flattens `Activity` and `GroupActivity`
   cards into dicts. It checks the stat labels are English (`check_english`) and turns display text
   such as `"5.23 km"` and `"1h 5m"` into metres and seconds.
4. **`ClubActivity.from_feed(row)`** builds the pydantic model. It maps the sport to stravalib's enums
   and keeps `raw_type`. If validation fails, `_activities()` raises `MarkupChanged`.
5. **Paging.** `cursorData.updated_at` and `rank` from the last entry form the next URL. The loop stops
   when `pagination.hasMore` is false, or after `FEED_PAGES_MAX` pages as a circuit breaker.

The other methods follow the same pattern with different pages:

| Method | Strava page or endpoint | Parser |
|---|---|---|
| `get_club_activities` | `/clubs/{id}/feed?feed_type=club` (JSON) | `feed_page` |
| `get_club_roster` / `members` / `admins` / `member_count` | `/clubs/{id}/members?page=N` (HTML, 30 per page) | `parse_roster`, `parse_member_count` |
| `get_club_leaderboard` | `/clubs/{id}/leaderboard`, read through `LEADERBOARD_JS` in the DOM | `leaderboard_rows` |
| `get_athlete_weeks` | `/athletes/{id}/interval?interval=YYYYWW...` (JS response), fetched in parallel by `WEEKS_JS` | `feed_page` on `preFetchedEntries` |

## Design rules

Keep these rules when you change the code. The tests and users depend on them.

1. **Return correct data or raise an error, never a guess.** If a page or response doesn't have the
   expected shape, raise `MarkupChanged` with a message that says what was missing. A value that is
   genuinely absent becomes `None`; never return `0` in its place.
2. **Parsing is pure.** Anything that interprets Strava's output goes in `_parse.py`, as a function that
   takes strings, dicts or lists and has no I/O. That keeps it unit-testable without a browser.
   `scraper.py` only fetches and puts the pieces together.
3. **Stay compatible with stravalib.** The three club models subclass stravalib's and only add fields.
   Never rename, remove or change the unit of an inherited field. Units are metres and seconds.
4. **Be polite to Strava.** Space out requests (`Browser.pause()`), cap loops (`*_PAGES_MAX`) and stop
   at the first 429. Never add retries for 429.
5. **The session is a secret.** Don't log it, print it or copy it. `login()` writes it owner-only.
6. **One browser per thread.** Don't add shared state that assumes `ClubScraper` is thread-safe.
7. **Constants are named** at the top of the module, such as `FEED_PAGE_SIZE` and `WEEKS_WORKERS`.
   Avoid magic numbers inside functions.
8. **Docstrings are Google style and short**: a summary line, then `Args`, `Returns` and `Raises`.

## Add a new method

The example adds `ClubScraper.get_club_posts(club_id)`. Use the same steps for any new Strava page.

1. **Capture a real response.** Open the page logged in, save the HTML or JSON, then **remove every
   name, id and other personal detail**, replacing them with invented ones. Never commit real member
   data.
2. **Write the parser test first** in `tests/library/test_parse.py`. Cover a normal page, an empty page,
   a page with the expected markup missing (expect `None` or `MarkupChanged`) and, if it has numbers,
   a non-English page (expect `UnsupportedLocale`).
3. **Write the parser** in `_parse.py` as a pure function. Reuse `to_meters`, `to_seconds`, `to_int`,
   `strip_markup`, `parse_stats` and `check_english` instead of new regexes for units.
4. **Add a model** in `models.py`. Subclass the stravalib model if one exists; otherwise use a plain
   `pydantic.BaseModel` with units in the field comments.
5. **Add the method** in `scraper.py`:

   ```python
   def get_club_posts(self, club_id: int, limit: int | None = None) -> list[ClubPost]:
       """The club's posts, newest first.

       Args:
           club_id: Strava club id.
           limit: Stop after this many; None for all.

       Returns:
           The posts.

       Raises:
           MarkupChanged: The page doesn't have the expected shape.
       """
       self._browser.goto(f"/clubs/{club_id}/posts")
       return [ClubPost(**r) for r in _parse.club_posts(self._browser.fetch(...))][:limit]
   ```

   Use `goto` for the first navigation, which catches a login redirect. Use `fetch` for extra requests.
   Call `self._browser.pause()` between pages, and cap any loop with a named constant.
6. **Export it** by adding the model to `src/__init__.py` and `__all__`.
7. **Optional: add a CLI command** in `cli.py`. Add a subparser, then a branch that prints
   `model_dump(mode="json", exclude_none=True)`.
8. **Optional: add it to `Client`**, only if stravalib had a matching method that Strava removed.
   Keep stravalib's name and signature.
9. **Test it end to end.** Add a route for the page to `FakeStrava` in `tests/e2e/conftest.py`, and a
   test in `tests/e2e/test_e2e.py`. If it can be checked against real data, add an assertion to
   `tests/live/test_live.py`.
10. **Document it** in [README.md](README.md), in the method reference and the models table.

## When Strava changes its site

The symptom is `MarkupChanged`, or for numbers, `UnsupportedLocale` on an English account.

1. Reproduce it against the live site: `OPENSTRIDE_TEST_CLUB_ID=<id> python -m pytest tests/live --live`.
2. Save the new response and anonymise it as in step 1 above.
3. Add it as a new test case in `tests/library/test_parse.py`. **Keep the old case** while Strava may
   still serve both versions.
4. Fix the parser in `_parse.py`, or the JS constant in `scraper.py` (`LEADERBOARD_JS` or `WEEKS_JS`),
   until both cases pass.
5. Update the fake pages in `tests/e2e/conftest.py` if their shape changed.

Where each kind of change lands:

| Strava change | Where to fix it |
|---|---|
| A new stat label or unit on activity cards | `ENGLISH_STAT_LABELS`, `_LENGTH_UNITS`, `_figures()` in `_parse.py` |
| Feed JSON keys renamed | `_ACTIVITY_KEYS`, `feed_entry_activities()` in `_parse.py` |
| Members page HTML | `_COUNT_RE`, `_LIST_RE`, `_ROW_RE`, `_HEADING_RE`, `parse_roster()` |
| Leaderboard table | `LEADERBOARD_JS` in `scraper.py`, `leaderboard_rows()` in `_parse.py` |
| Profile-week response | `WEEKS_JS` (`parse`) and `WEEK_URL` in `scraper.py` |
| Login flow or URLs | `LOGIN_PATHS`, `LOGIN_DONE_GLOB` in `_browser.py` |
| Bot detection | `LAUNCH_ARGS`, user agent, `VIEWPORT`, `DEFAULT_DELAY_S` in `_browser.py` |

## Tests

| Folder | Kind | Needs | CI coverage gate |
|---|---|---|---|
| `tests/library` | Unit tests: parsers, models, the scraper with a fake `Browser`, the CLI | nothing | 90% of `openstride` |
| `tests/e2e` | A real headless Chromium against `FakeStrava`, a fake strava.com served by Playwright request routing. Nothing leaves the machine. | `playwright install chromium` | 75% |
| `tests/live` | The real strava.com. Checks that the roster tallies and that profile weeks match the leaderboard. | `--live`, `OPENSTRIDE_TEST_CLUB_ID`, a session file | not run in CI |

```bash
python -m pytest -q tests/library --cov=openstride        # fast, no browser
python -m pytest -q tests/e2e                              # headless Chromium, offline
OPENSTRIDE_TEST_CLUB_ID=123456 OPENSTRIDE_SESSION_FILE=auth_state.json \
  python -m pytest tests/live --live                       # real Strava; use sparingly
```

CI (`.github/workflows/ci.yml`) runs unit, integration and e2e tests on Python 3.11 and 3.14. Live
tests are opt-in. `tests/conftest.py` skips them unless you pass `--live`.

**Test data must be invented.** Use made-up names and ids such as `Alice Anon` and `Bob Bogus`, like the
existing fixtures.

## Packaging and release

`pyproject.toml` builds with hatchling. `[tool.hatch.build.targets.wheel.force-include]` maps `src` to
`openstride`, so the wheel only contains the library. The dashboard is not published to PyPI. The
`openstride` console script points to `openstride.cli:main`.

Dependencies are `stravalib>=2.6,<3`, `pydantic>=2,<3` and `playwright>=1.45`. Raise the
stravalib upper bound only after the model subclass tests pass against the new major version.

To release:

1. Bump `version` in `pyproject.toml` **and** `__version__` in `src/__init__.py`. They must match.
2. Run all tests, including one `--live` run.
3. Build and check the package:

   ```bash
   python -m build
   twine check dist/*
   ```

4. `twine upload dist/*`, then tag the commit `vX.Y.Z`.

The root `README.md` is the PyPI project page (`readme = "README.md"`). Use absolute GitHub URLs for its
links and images.
