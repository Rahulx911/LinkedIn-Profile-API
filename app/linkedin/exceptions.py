class LinkedInClientError(Exception):
    """Base class for all errors raised by the LinkedIn client."""


class InvalidProfileURLError(LinkedInClientError):
    """The input string isn't a LinkedIn profile URL."""


class ProfileNotFoundError(LinkedInClientError):
    """LinkedIn returned 404, or the URN couldn't be resolved from the page."""


class ProfileNotAccessibleError(LinkedInClientError):
    """LinkedIn returned 403 — private profile, out of network, or restricted."""


class AuthenticationError(LinkedInClientError):
    """LinkedIn returned 401, or a checkpoint/login page instead of profile data —
    the li_at cookie is missing, expired, or the account hit a security checkpoint."""


class RateLimitedError(LinkedInClientError):
    """LinkedIn returned 429, or is throttling this account."""


class UpstreamError(LinkedInClientError):
    """The request to LinkedIn failed at the transport level (timeout,
    connection error), LinkedIn returned an unexpected non-success status we
    don't have a specific mapping for, or its response body wasn't valid
    JSON where JSON was expected. Distinct from the auth/rate/not-found
    errors above, which reflect a real, understood response from LinkedIn."""
