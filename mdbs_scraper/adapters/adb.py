"""Asian Development Bank IATI Registry adapter.

ADB's official CSV download (data.adb.org) sits behind Cloudflare
bot-challenge protection that also blocks headless-browser automation (see
docs/PROBLEMS.md). ADB separately publishes its full project portfolio as
per-country IATI activity files through the IATI Registry (CKAN publisher
"asdb"), hosted without any bot-protection -- a fully official, purpose-built
open-data channel, used here instead of the protected download.
"""

from .common import CkanOrganizationScraper


class ADBScraper(CkanOrganizationScraper):
    pass
