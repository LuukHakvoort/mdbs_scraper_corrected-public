"""Typed exceptions used by collectors and the command-line interface."""


class ScraperError(RuntimeError):
    """Base class for an expected collection failure."""


class SourceError(ScraperError):
    """The configured source could not be retrieved or decoded."""


class SourceLayoutChanged(ScraperError):
    """The source responded, but no longer matches the supported layout."""


class OptionalDependencyMissing(ScraperError):
    """A collector needs an optional dependency that is not installed."""


class ValidationError(ScraperError):
    """A record violates a non-negotiable schema rule."""
