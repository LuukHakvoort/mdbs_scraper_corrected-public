"""Compatibility entry point for running the scraper without installation."""

from mdbs_scraper.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
