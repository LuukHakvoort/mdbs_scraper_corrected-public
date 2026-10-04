"""African Development Bank IATI Registry adapter.

AfDB's official MapAfrica CSV export sits behind Cloudflare bot-challenge
protection that also blocks headless-browser automation (see
docs/PROBLEMS.md). AfDB separately publishes its full project portfolio as
per-country IATI activity files through the IATI Registry (CKAN publisher
"afdb"), hosted without any bot-protection -- a fully official, purpose-built
open-data channel, used here instead of the protected download.
"""

from .common import CkanOrganizationScraper


class AfDBScraper(CkanOrganizationScraper):
    pass
