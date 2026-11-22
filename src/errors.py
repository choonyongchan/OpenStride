"""Exceptions raised by openstride. Catch OpenStrideError to handle them all."""


class OpenStrideError(RuntimeError):
    """Base class: a Strava page could not be read."""


class SessionExpired(OpenStrideError):
    """No saved session, or Strava sent the request to the login page. Run `openstride login` again."""


class RateLimited(OpenStrideError):
    """Strava answered HTTP 429. Wait (15 minutes or more) before trying again."""


class MarkupChanged(OpenStrideError):
    """A page no longer looks the way openstride expects: Strava changed it, or blocked the request."""


class UnsupportedLocale(OpenStrideError):
    """The account shows Strava in a language other than English, so numbers can't be parsed safely."""
