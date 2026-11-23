# OpenStride library

`openstride` is a Python package and command line tool. It returns a Strava club's activities, members
and admins, which were the three club endpoints Strava removed from its API on 1 September 2026. It reads
them through your own logged-in Strava session in a headless Chromium.

It has two entry points:

- **`Client`**: a drop-in `stravalib.Client`. The three club methods work again. Every other method is
  stravalib's own.
- **`ClubScraper`**: the club data without an API token. It also gives you the member count, the weekly
  leaderboard and athletes' weekly activity history.

> **Unofficial.** OpenStride is not affiliated with Strava, Inc. Using it may get the account you log in
> with restricted. Only use it with clubs you belong to, keep the request rate low, and respect members'
> privacy.

**Contents:** [Prerequisites](#prerequisites-and-assumptions) ·
[Install](#install) · [The session file](#the-session-file) · [Command line](#command-line) ·
[Choosing an entry point](#choosing-client-or-clubscraper) · [Client](#client) ·
[ClubScraper](#clubscraper) · [Models](#models) · [Errors](#errors) · [Recipes](#recipes) ·
[Accuracy and limits](#accuracy-and-limits)

---

## Prerequisites and assumptions

| Requirement | Why |
|---|---|
| **Python 3.14 or newer** | The package requires it (`requires-python = ">=3.14"`). |
| **Playwright's Chromium** | Every request runs in a real browser. Run `python -m playwright install chromium` once. To use an installed browser instead, pass `--channel chrome` / `msedge` or `browser_channel=`. |
| **A Strava account that is in the club** | OpenStride sees exactly what that account sees on strava.com. |
| **Strava's display language set to English** | OpenStride parses the numbers and labels Strava displays. Another language raises `UnsupportedLocale` instead of returning wrong numbers. Change it at strava.com/settings/display. |
| **A screen, once** | `openstride login` opens a visible browser window so you can log in. After that, the saved session works headless, including on servers and in containers. |
| **An API token, only for stravalib's methods** | `Client(access_token=...)` is only needed for the official API. The club methods use the session. |

The following assumptions affect your results:

- **Visibility.** Private activities, and athletes whose profile is private to your account, are invisible.
  The full member list needs an account that is allowed to view the club's members.
- **Feed window.** Strava's club feed only keeps about the last 26 hours, whatever you ask for.
- **Units.** Metric and imperial accounts both work. All numbers come back in metres and seconds.
- **Time zone.** `ClubScraper(timezone=...)` sets when a week starts for the leaderboard and profile weeks.
  If you leave it out, the machine's local zone is used.

## Install

```bash
pip install openstride
python -m playwright install chromium
openstride login                 # log in in the window that opens (5 minutes max)
```

To check it works:

```bash
openstride count 123456          # prints the member count of club 123456
```

`123456` stands for your club id, the number in `strava.com/clubs/<id>`.

## The session file

`openstride login` saves your logged-in browser state to `auth_state.json` (a Playwright storage-state JSON).
Every later call loads it.

- **It is a credential.** Anyone holding it is logged in to Strava as you. It is written owner-only
  (`0600`, directory `0700`). Do not commit, share or put it in an image. Add it to `.gitignore`.
- **It expires.** Strava ends sessions after a while, or when you log out or change your password. When
  it expires, every call raises `SessionExpired`. Run `openstride login` again; nothing else changes.
- **It can live anywhere.** Use `--session-file PATH` on the CLI, or `session_file=` in Python.

## Command line

```text
openstride login       [--session-file PATH] [--channel chrome|msedge]
openstride activities  CLUB_ID [--limit N] [--session-file PATH]
openstride members     CLUB_ID [--limit N] [--session-file PATH]
openstride admins      CLUB_ID [--limit N] [--session-file PATH]
openstride count       CLUB_ID [--session-file PATH]
openstride --version
```

| Command | Output |
|---|---|
| `login` | Opens a browser window and waits up to 5 minutes for you to log in. Prints `Session saved to <path>`. |
| `activities` | A JSON array of [`ClubActivity`](#models) objects: the club feed, newest first. |
| `members` | A JSON array of [`ClubAthlete`](#models) objects, admins first. |
| `admins` | A JSON array of [`ClubAdmin`](#models) objects. |
| `count` | A single number: the member count Strava shows on the club page. |

| Option | Default | Meaning |
|---|---|---|
| `--session-file` | `auth_state.json` | Session file to read, or to write for `login` |
| `--limit` | all | Stop after N items |
| `--channel` | bundled Chromium | Use an installed `chrome` or `msedge` for `login` |

The JSON leaves out fields that are `null`. Dates are ISO 8601. Distances are in metres and times in seconds.

**Exit codes:** `0` means success. `1` means an `OpenStrideError`, with `error: <message>` printed on
stderr. `2` means a usage error from argparse.

```bash
openstride activities 123456 --limit 5
openstride members 123456 > members.json
openstride members 123456 | jq -r '.[] | "\(.id)\t\(.name)"'
```

`python -m openstride ...` is the same as `openstride ...`.

## Choosing `Client` or `ClubScraper`

| You want | Use |
|---|---|
| Existing stravalib code to keep working after the endpoints were removed | `Client` |
| Official API calls and club data in one object | `Client` |
| Club data only, with no Strava API application or token | `ClubScraper` |
| The member count, weekly leaderboard or athletes' past weeks | `ClubScraper`, or `Client(...).scraper` |

## `Client`

```python
from openstride import Client

with Client(access_token="...", session_file="auth_state.json") as client:
    me = client.get_athlete()                        # official API, via stravalib
    for a in client.get_club_activities(123456):     # restored
        print(a.athlete.firstname, a.sport_type, a.distance, a.moving_time)
    members = client.get_club_members(123456)
    admins = client.get_club_admins(123456)
```

`Client(access_token=None, *args, session_file="auth_state.json", scraper=None, **kwargs)`

| Argument | Meaning |
|---|---|
| `access_token` | OAuth token for stravalib's official API methods. Optional if you only use the club methods. |
| `session_file` | Session file for the club methods. Ignored when `scraper` is given. |
| `scraper` | An existing `ClubScraper` to use, for example one with a time zone or browser channel set |
| `*args, **kwargs` | Passed through to `stravalib.Client` |

| Method | Returns |
|---|---|
| `get_club_activities(club_id, limit=None)` | `list[ClubActivity]` |
| `get_club_members(club_id, limit=None)` | `list[ClubAthlete]` |
| `get_club_admins(club_id, limit=None)` | `list[ClubAdmin]` |
| `scraper` (property) | The `ClubScraper` behind the club methods, created on first use |
| `close()` | Closes the browser. `with Client(...)` calls it for you. |

Differences from stravalib's original club methods:

- They take the same arguments and return subclasses of the same models, so
  `isinstance(a, stravalib.model.ClubActivity)` is true. The models have extra fields.
- They return a `list`, not a lazy `BatchedResultsIterator`. Iterating works the same way.
- The browser opens on the first club call, not when the client is created.

## `ClubScraper`

```python
from datetime import date
from openstride import ClubScraper

with ClubScraper("auth_state.json", timezone="Asia/Singapore") as s:
    activities = s.get_club_activities(123456)
    roster = s.get_club_roster(123456)
    admins = s.get_club_admins(123456)
    board = s.get_club_leaderboard(123456, "last")
    result = s.get_athlete_weeks([(987654, date(2026, 9, 14))])
```

`ClubScraper(session_file="auth_state.json", *, headless=True, browser_channel=None, timezone=None, locale="en-US")`

| Argument | Default | Meaning |
|---|---|---|
| `session_file` | `auth_state.json` | Session from `openstride login` |
| `headless` | `True` | Run Chromium without a window. Set `False` to watch it. |
| `browser_channel` | `None` | `None` uses Playwright's Chromium. `"chrome"` or `"msedge"` uses an installed browser. |
| `timezone` | the machine's zone | IANA zone, such as `"Asia/Singapore"`. It sets "this week" and "last week". |
| `locale` | `"en-US"` | Browser locale. Strava's own language setting must be English either way. |

The browser opens on the first request and stays open until `close()`, or until the `with` block ends.
Requests are spaced 0.4 to 0.9 seconds apart, at random.

### Methods

#### `get_club_activities(club_id, limit=None) -> list[ClubActivity]`

This returns the club feed, newest upload first. It replaces `GET /clubs/{id}/activities`. Activities of
any sport are included, and each activity appears once, even when it is also part of a group activity
card. The feed only reaches back about 26 hours.

#### `get_club_roster(club_id) -> ClubRoster`

This reads every members page once and returns the headline count plus every member. If the two don't
match, for example because someone joined during the read, it reads the pages again once. Check
`roster.complete` before treating a missing member as having left.

#### `get_club_members(club_id, limit=None) -> list[ClubAthlete]`

This returns `get_club_roster(club_id).members`, admins first. It replaces `GET /clubs/{id}/members`.

#### `get_club_admins(club_id, limit=None) -> list[ClubAdmin]`

This returns the club's admins, read from the first members page. It replaces `GET /clubs/{id}/admins`.

#### `get_club_member_count(club_id) -> int`

This returns the member count shown on the club page. It needs one request.

#### `get_club_leaderboard(club_id, week="this") -> list[LeaderboardEntry]`

This returns Strava's weekly club leaderboard for `"this"` or `"last"` week, about the top 100 athletes.
It only covers the club's own sport. The figures are Strava's own weekly totals.

#### `get_athlete_weeks(requests, workers=4, batch_size=24) -> AthleteWeeks`

This fetches athletes' own activities, one Monday-to-Sunday week from each profile. It is the only way to
go further back than the feed.

- `requests` is an iterable of `(athlete_id, any_day_in_the_week)` pairs.
- `workers` is the number of requests sent in parallel. `batch_size` is the number of URLs sent per
  round trip to the browser.
- Each URL gets up to 4 attempts, with backoff, when Strava answers 5xx or the network fails.
- It **stops at the first HTTP 429**. It returns what it fetched with `rate_limited=True`, and the
  unsent pairs in `unfetched`, so you can resume later.
- A private profile returns `AthleteWeek(visible=False, activities=[])`.
- It raises `SessionExpired` right away if the session expires partway through.

#### `close()`

This closes the browser. It is safe to call twice, and the next call opens the browser again.

### Helpers

| Function | Does |
|---|---|
| `login(session_file="auth_state.json", *, channel=None, timeout_s=300) -> Path` | Does the same as `openstride login`, from Python |
| `monday_of(d) -> date` | Returns the Monday of the week containing `d` |
| `week_id(monday) -> str` | Returns Strava's six-digit week id, `YYYYWW` (ISO week) |

### Threads

One `ClubScraper` drives one Chromium through Playwright's sync API, which is tied to the thread that
started it. **A `ClubScraper` is not thread-safe.** Create one per thread, and don't share a `Client`'s
club methods across threads either.

## Models

All models are pydantic v2 models, so `model_dump(mode="json")` gives the JSON the CLI prints. Distances
are in **metres**, times in **seconds**, and dates are timezone-aware `datetime` values.

### `ClubActivity` (subclass of `stravalib.model.ClubActivity`)

| Field | Notes |
|---|---|
| `id`, `athlete_id` | The activity id and athlete id. The old API never returned these. |
| `athlete` | A `ClubAthlete` with `id`, full `name`, `firstname`, `lastname` |
| `name`, `distance`, `moving_time`, `elapsed_time`, `total_elevation_gain` | The same fields as the old API |
| `sport_type`, `type` | stravalib enums. `None` if stravalib doesn't know the sport. |
| `raw_type` | The sport type exactly as Strava sent it, always set |
| `start_date`, `start_date_local` | The old API never returned these |
| `workout_type`, `is_virtual`, `is_commute`, `visibility` | |
| `device_name`, `location`, `description`, `steps`, `pace` | `pace` is Strava's display text, such as `"5:12 /km"` |
| `kudos_count`, `comment_count` | |
| `entity` | `"Activity"` or `"GroupActivity"`: the kind of feed card it came from |

### `ClubAthlete` (subclass of `stravalib.strava_model.ClubAthlete`)

| Field | Notes |
|---|---|
| `id`, `name` | Athlete id and full display name |
| `firstname`, `lastname` | The **full** last name, not an initial as in the old API |
| `admin` | `True` or `False`, or `None` when Strava didn't say |
| `member` | Always `"accepted"` |
| `owner` | Always `None`, because Strava doesn't show the owner |

### Other models

| Model | Fields |
|---|---|
| `ClubAdmin` (subclass of `SummaryAthlete`) | `id`, `name`, `firstname`, `lastname` |
| `ClubRoster` | `member_count`, `members`, and `complete` (a property: whether the two agree) |
| `LeaderboardEntry` | `week` (`"this"`/`"last"`), `week_start`, `rank`, `athlete_id`, `athlete_name`, `distance`, `activities`, `total_elevation_gain`, `moving_time` |
| `AthleteWeek` | `athlete_id`, `week_start` (a Monday), `visible`, `activities` |
| `AthleteWeeks` | `weeks`, `errors`, `rate_limited`, `unfetched` |

## Errors

Every error OpenStride raises subclasses `OpenStrideError`, which is a `RuntimeError`.

| Error | Raised when | What to do |
|---|---|---|
| `SessionExpired` | There is no session file, Strava redirected to its login page, or it answered HTTP 401 | Run `openstride login` again |
| `RateLimited` | Strava answered HTTP 429 | Wait 15 minutes or more. Lower how often you poll. |
| `MarkupChanged` | A page or response no longer has the expected shape, or another non-2xx status came back | Strava changed its site, or blocked the request. Update OpenStride, or [report it](https://github.com/choonyongchan/OpenStride/issues). |
| `UnsupportedLocale` | The account shows Strava in a language other than English | Switch to English at strava.com/settings/display |

`ValueError` is raised for bad arguments, such as `week="next"`. Playwright errors, such as a missing
Chromium, are not wrapped.

## Recipes

### Handle errors

```python
from openstride import ClubScraper, SessionExpired, RateLimited, OpenStrideError

try:
    with ClubScraper("auth_state.json") as s:
        acts = s.get_club_activities(123456)
except SessionExpired:
    ...   # alert someone to run `openstride login`
except RateLimited:
    ...   # back off; try again later
except OpenStrideError as e:
    ...   # MarkupChanged / UnsupportedLocale: needs a human
```

### Keep every activity, not just the last 26 hours

Poll the feed at least every few hours and store new activity ids:

```python
seen: set[int] = load_ids()
with ClubScraper("auth_state.json") as s:
    new = [a for a in s.get_club_activities(123456) if a.id not in seen]
save(new)
```

### Backfill past weeks and resume after a rate limit

```python
from datetime import date, timedelta
from openstride import ClubScraper, monday_of

start = monday_of(date(2026, 9, 1))
with ClubScraper("auth_state.json", timezone="Asia/Singapore") as s:
    ids = [m.id for m in s.get_club_members(123456)]
    jobs = [(aid, start + timedelta(weeks=w)) for aid in ids for w in range(8)]
    result = s.get_athlete_weeks(jobs)
    store(result.weeks)
    if result.rate_limited:
        save_for_later(result.unfetched)   # retry these in an hour or more
```

### Detect members who left

```python
roster = s.get_club_roster(123456)
if roster.complete:                     # only trust absences when the roster tallies
    left = known_ids - {m.id for m in roster.members}
```

## Accuracy and limits

- **Numbers are Strava's display values**, so they are rounded the way Strava rounds them on screen:
  - distance to 0.01 km or 0.01 mi (about 10 m);
  - elevation to 1 m or 1 ft;
  - durations over an hour lose their seconds.
- **Missing numbers are left as `None`**, never guessed. When a page or response has an unexpected
  shape, OpenStride raises `MarkupChanged`.
- **Leaderboard and profile figures can differ from the feed.** The leaderboard only counts the club's
  sport, and it uses moving time.
- **Request volume:**
  - one feed call is 1 or 2 requests;
  - one roster read is one request per 30 members;
  - a profile backfill is one request per athlete per week.

  Keep backfills small, and spread them over time.
- **Strava can change its site at any time.** Pin a version of `openstride` in production, and watch for
  `MarkupChanged`.

---

To change or extend the library, see the [developer guide](DeveloperGuide.md).
