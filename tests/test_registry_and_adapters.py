import json
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from mdbs_scraper.base import ScrapeOptions
from mdbs_scraper.config import BANKS, _load_registry, get_bank
from mdbs_scraper.adapters.aiib import AIIBScraper
from mdbs_scraper.errors import SourceLayoutChanged
from mdbs_scraper.registry import SCRAPER_CLASSES, create_scraper
from mdbs_scraper.schema import ProjectRecord


FIXTURES = Path(__file__).parent / "fixtures"


class RegistryAndAdapterTests(unittest.TestCase):
    def test_aiib_scraper_enriches_listing_links_from_detail_pages(self):
        listing = Mock(
            html=(
                '<a href="https://www.aiib.org/en/projects/details/AIIB-001">'
                "Water project</a>"
            ),
            url="https://www.aiib.org/en/projects/list/index.html",
            json_payloads=[],
        )
        detail = Mock(
            html=(
                "<h1>Water project</h1>"
                "<div>STATUS</div><div>Approved</div>"
                "<div>MEMBER</div><div>Kenya</div>"
                "<div>SECTOR</div><div>Water</div>"
                "<div>PROJECT NUMBER</div><div>AIIB-001</div>"
                "<div>APPROVED FUNDING</div><div>USD125 million</div>"
                "<div>FINANCING TYPE</div><div>Sovereign</div>"
                "<div>FINANCING APPROVAL</div><div>March 10, 2024</div>"
            ),
            url="https://www.aiib.org/en/projects/details/AIIB-001",
            json_payloads=[],
        )
        scraper = AIIBScraper(get_bank("aiib"), ScrapeOptions())
        scraper.client.get = Mock(return_value=Mock(
            url=detail.url,
            text=Mock(return_value=detail.html),
        ))
        with patch("mdbs_scraper.adapters.common.render_page", return_value=listing):
            records = scraper.run()

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].project_id, "AIIB-001")
        self.assertEqual(records[0].country, "Kenya")
        self.assertEqual(str(records[0].loan_amount), "125000000")
        self.assertEqual(records[0].loan_currency, "USD")
        self.assertEqual(records[0].loan_type, "Sovereign")

    def test_aiib_scraper_records_source_total_from_deduplicated_links(self):
        # Regression: unlike eib.py, aiib.py never populated run_metadata
        # ["source_total"], so there was no automated "found N, scraped M"
        # completeness signal for AIIB runs.
        listing = Mock(
            html=(
                '<a href="https://www.aiib.org/en/projects/details/AIIB-001">Project A</a>'
                '<a href="https://www.aiib.org/en/projects/details/AIIB-002">Project B</a>'
                '<a href="https://www.aiib.org/en/projects/details/AIIB-001">Project A again</a>'
            ),
            url="https://www.aiib.org/en/projects/list/index.html",
            json_payloads=[],
        )
        detail_html = (
            "<h1>Some project</h1><div>STATUS</div><div>Approved</div>"
            "<div>MEMBER</div><div>Kenya</div>"
            "<div>PROJECT NUMBER</div><div>AIIB-001</div>"
        )
        scraper = AIIBScraper(get_bank("aiib"), ScrapeOptions())
        scraper.client.get = Mock(return_value=Mock(
            url="https://www.aiib.org/en/projects/details/AIIB-001",
            text=Mock(return_value=detail_html),
        ))
        # Both the listing render and this fallback-branch render must be
        # mocked -- aiib.py imports render_page separately from common.py,
        # and a truthy project_id (via "PROJECT NUMBER" above) is what keeps
        # _detail_records() from ever needing the fallback in the first place.
        with patch("mdbs_scraper.adapters.common.render_page", return_value=listing), \
             patch("mdbs_scraper.adapters.aiib.render_page", return_value=listing):
            scraper.run()

        # Two unique hrefs, even though one link appears twice on the page.
        self.assertEqual(scraper.run_metadata["source_total"], 2)

    def test_aiib_scraper_maps_proposed_funding_amount(self):
        detail_html = (
            "<h1>Proposed project</h1>"
            "<div>STATUS</div><div>Proposed</div>"
            "<div>MEMBER</div><div>Indonesia</div>"
            "<div>SECTOR</div><div>Energy</div>"
            "<div>PROJECT NUMBER</div><div>001234</div>"
            "<div>PROPOSED FUNDING AMOUNT</div><div>USD47.71 million</div>"
            "<div>FINANCING TYPE</div><div>Nonsovereign</div>"
            "<div>FINANCING APPROVAL</div><div>January 15, 2025</div>"
        )
        scraper = AIIBScraper(get_bank("aiib"), ScrapeOptions())
        scraper.client.get = Mock(return_value=Mock(
            url="https://www.aiib.org/en/projects/details/2025/proposed/example.html",
            text=Mock(return_value=detail_html),
        ))
        listing = Mock(
            html='<a href="https://www.aiib.org/en/projects/details/2025/proposed/example.html">'
                 "View details</a>",
            url="https://www.aiib.org/en/projects/list/index.html",
            json_payloads=[],
        )
        with patch("mdbs_scraper.adapters.common.render_page", return_value=listing):
            records = scraper.run()

        self.assertEqual(records[0].project_id, "001234")
        # Decimal multiplication preserves the disclosed figure's decimal
        # precision (2 places from "47.71"), consistent with e.g. the CAF
        # "31,5 millones" -> "31500000.0" fixture test.
        self.assertEqual(str(records[0].loan_amount), "47710000.00")
        self.assertEqual(records[0].loan_currency, "USD")
        # "FINANCING TYPE" is the sovereign/non-sovereign signal, not a loan
        # product/instrument type -- routed to loan_type, not
        # financing_instrument. "Nonsovereign" (no space, AIIB's own
        # spelling) must normalize to "Non-sovereign", not fall through to
        # the plain "sovereign" substring match.
        self.assertEqual(records[0].loan_type, "Non-sovereign")

    def test_every_enabled_bank_has_a_concrete_adapter(self):
        self.assertTrue(BANKS)
        self.assertTrue(set(BANKS).issubset(set(SCRAPER_CLASSES)))
        self.assertEqual(get_bank("cabi").id, "cabei")

    def test_disabled_banks_are_excluded_from_the_loaded_registry(self):
        payload = {
            "aliases": {},
            "banks": [
                {
                    "id": "example",
                    "name": "Example Bank",
                    "abbreviation": "EX",
                    "website": "https://example.org",
                    "source_url": "https://example.org/projects.csv",
                    "source_format": "csv",
                    "method": "official-download",
                    "coverage_notes": "Fixture only.",
                    "dynamic": False,
                    "enabled": False,
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "banks.json"
            config_path.write_text(json.dumps(payload), encoding="utf-8")
            all_banks, _ = _load_registry(config_path)
            enabled = {bank_id: bank for bank_id, bank in all_banks.items() if bank.enabled}
        self.assertIn("example", all_banks)
        self.assertNotIn("example", enabled)

    def test_all_non_world_bank_adapters_map_official_snapshot_shape(self):
        source = str(FIXTURES / "common_projects.csv")
        # ibrd/ida (bespoke JSON search-API shape) and eib (bespoke JSON
        # project-list-API shape, since 2026-09-03 -- see test_eib.py) don't
        # go through the generic CSV-shaped read_rows()/FIELD_ALIASES path
        # this fixture exercises.
        for bank_id in (bank for bank in BANKS if bank not in {"ibrd", "ida", "eib"}):
            with self.subTest(bank=bank_id):
                scraper = create_scraper(bank_id, ScrapeOptions(source_file=source))
                records = scraper.run()
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0].bank_id, bank_id)
                self.assertEqual(records[0].project_id, "TEST-001")
                self.assertEqual(records[0].commitment_year, 2021)
                self.assertEqual(records[0].loan_type, "Non-sovereign")
                self.assertEqual(records[0].first_disbursement_date, "2021-06-01")
                self.assertEqual(records[0].last_disbursement_date, "2024-09-15")
                self.assertEqual(
                    records[0].cofinancing_partners,
                    "Regional Development Fund; Example Bilateral Agency",
                )
                self.assertEqual(str(records[0].cofinancing_amount), "3000000")
                self.assertEqual(records[0].cofinancing_currency, "USD")

    def test_world_bank_arms_filter_and_preserve_bank_specific_commitments(self):
        source = str(FIXTURES / "worldbank_projects.json")
        ibrd = create_scraper("ibrd", ScrapeOptions(source_file=source)).run()
        ida = create_scraper("ida", ScrapeOptions(source_file=source)).run()
        self.assertEqual([record.project_id for record in ibrd], ["P000001"])
        self.assertEqual([record.project_id for record in ida], ["P000002"])
        self.assertEqual(str(ibrd[0].total_commitment_usd), "100000000")
        self.assertEqual(str(ida[0].total_commitment_usd), "45000000")
        self.assertIsNone(ibrd[0].total_disbursement)
        # lendprojectcost can exceed the bank's own commitment (co-financing
        # from other sources) -- kept distinct from loan_amount/total_commitment.
        self.assertEqual(str(ibrd[0].total_project_cost_usd), "140000000")
        # sector2's {"Name": "", "Percent": 0} (the API's own empty-sector
        # placeholder) must not leak in as a literal "0", and sector_namecode's
        # list-of-dicts must contribute names, not raw {"name":...} fragments.
        self.assertEqual(ibrd[0].sector, "Transport; Urban Transport")
        self.assertEqual(ibrd[0].sector_category, "Transport & Storage")
        # Fictional fixture country names ("Exampleland") don't resolve to a
        # real ISO-3 code -- must stay blank with an explanatory note rather
        # than being left silently unexplained.
        self.assertEqual(ibrd[0].country_code, "")
        self.assertIn("No ISO-3 country code could be determined", ibrd[0].data_quality_notes)

    def test_world_bank_derives_an_iso3_country_code_from_country_name_text(self):
        # World Bank builds ProjectRecord directly (worldbank.py's _record()),
        # bypassing generic_record_from_row() entirely -- this is a dedicated
        # regression test for that separate wiring, confirmed missing in a
        # live rescrape (ibrd/ida's country_code came back 0% populated).
        scraper = create_scraper("ibrd", ScrapeOptions())
        record = scraper._record(
            {
                "id": "P999999",
                "project_name": "Real Country Test",
                "countryshortname": "Peru",
                "ibrdcommamt": "1000000",
            },
            "https://search.worldbank.org/api/v2/projects",
        )
        self.assertEqual(record.country, "Peru")
        self.assertEqual(record.country_code, "PER")

    def test_world_bank_enriches_interest_rate_and_last_repayment_date_from_loan_terms(self):
        # interest_rate/last_repayment_date come from a separate Finances One
        # dataset (confirmed live: the Projects search API's own ~37-key
        # project dict has neither), joined in by project_id -- this is a
        # direct unit test of that join, not the live fetch itself.
        scraper = create_scraper("ibrd", ScrapeOptions())
        loan_lookup = {
            "P999999": [{"interest_rate": 4.25, "last_repayment_date": "1977-05-01T00:00:00"}],
        }
        record = scraper._record(
            {"id": "P999999", "project_name": "Real Country Test", "ibrdcommamt": "1000000"},
            "https://search.worldbank.org/api/v2/projects",
            loan_lookup,
        )
        self.assertEqual(record.interest_rate, "4.25")
        self.assertEqual(record.interest_rate_pct, Decimal("4.25"))
        self.assertEqual(record.last_repayment_date, "1977-05-01")

    def test_world_bank_lists_distinct_loan_terms_for_a_multi_loan_project(self):
        # A project can have more than one loan (confirmed live: 1,613 of
        # IBRD's 7,291 do) -- all distinct values are disclosed, "; "-joined,
        # per explicit choice, rather than picking one and discarding others.
        scraper = create_scraper("ibrd", ScrapeOptions())
        loan_lookup = {
            "P999999": [
                {"interest_rate": 4.25, "last_repayment_date": "1977-05-01T00:00:00"},
                {"interest_rate": 5.5, "last_repayment_date": "1983-02-01T00:00:00"},
            ],
        }
        record = scraper._record(
            {"id": "P999999", "project_name": "Multi-loan Test", "ibrdcommamt": "1000000"},
            "https://search.worldbank.org/api/v2/projects",
            loan_lookup,
        )
        self.assertEqual(record.interest_rate, "4.25; 5.5")
        self.assertEqual(record.last_repayment_date, "1977-05-01; 1983-02-01")
        # Genuinely distinct rates -- interest_rate_pct stays blank rather
        # than picking one, with a note explaining why.
        self.assertIsNone(record.interest_rate_pct)
        self.assertIn("more than one distinct rate", record.data_quality_notes)

    def test_world_bank_interest_rate_pct_populates_when_multiple_loans_share_one_rate(self):
        # interest_rate_pct keys off the count of *distinct* rate values, not
        # the number of loans -- two loans at the same rate should still
        # yield a usable numeric value.
        scraper = create_scraper("ibrd", ScrapeOptions())
        loan_lookup = {
            "P999999": [
                {"interest_rate": 4.25, "last_repayment_date": "1977-05-01T00:00:00"},
                {"interest_rate": 4.25, "last_repayment_date": "1983-02-01T00:00:00"},
            ],
        }
        record = scraper._record(
            {"id": "P999999", "project_name": "Same-rate Multi-loan Test", "ibrdcommamt": "1000000"},
            "https://search.worldbank.org/api/v2/projects",
            loan_lookup,
        )
        self.assertEqual(record.interest_rate, "4.25")
        self.assertEqual(record.interest_rate_pct, Decimal("4.25"))

    def test_world_bank_ida_service_charge_rate_gets_a_clarifying_note(self):
        # IDA's rate column is literally named service_charge_rate (World
        # Bank's own term for concessional lending's periodic charge, not a
        # market interest rate) -- it lands in the same interest_rate field
        # as IBRD's, but with a note preserving that distinction.
        scraper = create_scraper("ida", ScrapeOptions())
        loan_lookup = {
            "P999998": [{"service_charge_rate": 0.75, "last_repayment_date": "2011-06-01T00:00:00"}],
        }
        record = scraper._record(
            {"id": "P999998", "project_name": "IDA Credit Test", "idacommamt": "1000000"},
            "https://search.worldbank.org/api/v2/projects",
            loan_lookup,
        )
        self.assertEqual(record.interest_rate, "0.75")
        self.assertEqual(record.interest_rate_pct, Decimal("0.75"))
        self.assertIn("service_charge_rate", record.data_quality_notes)
        self.assertIn("not a market interest rate", record.data_quality_notes)

    def test_world_bank_loan_terms_fetch_failure_does_not_break_the_scrape(self):
        from mdbs_scraper.adapters.worldbank import _fetch_loan_terms

        scraper = create_scraper("ibrd", ScrapeOptions())
        scraper.client.post = Mock(side_effect=RuntimeError("network is down"))
        # A bonus-enrichment fetch failing must not raise -- the primary,
        # already-working Projects-search-API scrape shouldn't go down over it.
        self.assertEqual(_fetch_loan_terms(scraper.client, "DS00047"), {})

    def test_world_bank_excludes_a_project_whose_source_tag_does_not_match_its_real_financing(self):
        # A project tagged source=["IBRD"] with ibrdcommamt=0 but a real,
        # positive idacommamt is 100% IDA-financed in reality (confirmed live
        # against real API data -- "source" names an administering unit, not
        # reliably who financed it). It must be excluded from ibrd (no
        # genuine IBRD commitment to report) and included, correctly, in ida.
        source = str(FIXTURES / "worldbank_source_tag_mismatch.json")
        with self.assertRaises(SourceLayoutChanged):
            create_scraper("ibrd", ScrapeOptions(source_file=source)).run()
        ida = create_scraper("ida", ScrapeOptions(source_file=source)).run()
        self.assertEqual([record.project_id for record in ida], ["P000003"])
        self.assertEqual(str(ida[0].total_commitment_usd), "280000000")

    def test_tabular_scraper_records_the_stable_requested_url_not_a_redirect_target(self):
        from mdbs_scraper.base import LoadedSource

        # IDB's official download redirects through a signed, short-lived
        # token-download URL; record.source_url must stay the stable,
        # always-refetchable banks.json URL, not that resolved redirect
        # target (which has since expired by the time anyone opens it).
        scraper = create_scraper("idb", ScrapeOptions())
        source = LoadedSource(
            data=b"",
            url="https://data.iadb.org/file/token-download/expired-example-token",
            source_format="csv",
        )
        record = scraper.map_row({"oper_num": "X", "oper_nm": "Test project"}, source)
        self.assertEqual(
            record.source_url,
            "https://data.iadb.org/files/download/791d475c-0f61-411c-ae92-2242c913e73a",
        )

    def test_isdb_excludes_the_mislabeled_organisation_file_package(self):
        from mdbs_scraper.adapters.isdb import IsDBScraper

        scraper = IsDBScraper(get_bank("isdb"), ScrapeOptions())
        # "isdb-activity" (singular) has neither a "-org" suffix nor
        # "orgfile" in its name, but its one resource is nonetheless an
        # organisation file -- the resource-URL fallback check must catch it.
        mislabeled = {
            "name": "isdb-activity",
            "resources": [{"url": "https://example.org/files/isdb-organisation.xml"}],
        }
        real_activity_package = {
            "name": "isdb-activities-isdb-a",
            "resources": [{"url": "https://example.org/files/isdb_activities_a.xml"}],
        }
        self.assertFalse(scraper._is_relevant_package(mislabeled))
        self.assertTrue(scraper._is_relevant_package(real_activity_package))

    def test_isdb_tags_itfc_rows_with_a_disclosure_gap_note(self):
        from mdbs_scraper.adapters.isdb import IsDBScraper

        scraper = IsDBScraper(get_bank("isdb"), ScrapeOptions())
        self.assertIn("ITFC", scraper._package_note({"name": "isdb-itfc-activities"}))
        self.assertEqual(scraper._package_note({"name": "isdb-activities-isdb-a"}), "")
        from mdbs_scraper.base import LoadedSource
        source = LoadedSource(b"", "https://example.org", "xml")
        itfc = scraper.map_row({"project_name": "Trade line", "_source_note": scraper._ITFC_NOTE}, source)
        own = scraper.map_row({"project_name": "Irrigation project"}, source)
        self.assertEqual(itfc.instrument_category, "trade_finance")
        self.assertEqual(own.instrument_category, "")

    def test_a_package_note_reaches_the_mapped_record(self):
        # The note travels in the row under "_source_note"; row keys are
        # normalized for field lookups (which strips the underscore), so the
        # note must be read from the raw row.
        from mdbs_scraper.adapters.common import generic_record_from_row

        record = generic_record_from_row({"project_name": "Trade line", "_source_note": "ITFC package note."})
        self.assertIn("ITFC package note.", record.data_quality_notes)

    def test_isdb_excludes_itfc_as_a_cofinancing_self_reference(self):
        from mdbs_scraper.adapters.isdb import IsDBScraper
        from mdbs_scraper.base import LoadedSource
        from mdbs_scraper.tabular import xml_rows

        # Confirmed live: every one of IsDB's 1,287 ITFC-file activities
        # lists both "Islamic Development Bank" and "International Islamic
        # Trade Finance Corporation" (IsDB's own trade-finance arm, already
        # treated as part of this same "isdb" bank -- see the disclosure-gap
        # note test above) as role="1" Funding participants, with no other
        # variation and no shared org `ref` for the ITFC entry -- so it can't
        # be caught by generic_record_from_row's ref-based check and needs
        # this bank-specific override instead. A genuine external cofinancier
        # (here, "AECID", a real value confirmed live in IsDB's data) must
        # still survive.
        data = b"""<?xml version="1.0"?>
        <iati-activities>
        <iati-activity default-currency="USD">
          <iati-identifier>XM-DAC-46025-TEST0001</iati-identifier>
          <reporting-org ref="XM-DAC-46025">
            <narrative>Islamic Development Bank</narrative>
          </reporting-org>
          <participating-org ref="XM-DAC-46025" role="1" type="40">
            <narrative>Islamic Development Bank</narrative>
          </participating-org>
          <participating-org role="1">
            <narrative>International Islamic Trade Finance Corporation</narrative>
          </participating-org>
          <participating-org role="1">
            <narrative>AECID</narrative>
          </participating-org>
        </iati-activity>
        </iati-activities>
        """
        row = xml_rows(data)[0]
        source = LoadedSource(data=b"", url="https://www.isdb.org/example.xml", source_format="xml")
        scraper = IsDBScraper(get_bank("isdb"), ScrapeOptions())
        record = scraper.map_row(row, source)
        self.assertEqual(record.cofinancing_partners, "AECID")


def _afdb_activity(funders: str, finance_type: str = "", flow_type: str = "") -> dict:
    """One AfDB-shaped IATI activity, reduced to what concessionality reads."""
    from mdbs_scraper.tabular import xml_rows

    optional = ""
    if finance_type:
        optional += f'<default-finance-type code="{finance_type}"/>'
    if flow_type:
        optional += f'<default-flow-type code="{flow_type}"/>'
    data = f"""<?xml version="1.0"?>
    <iati-activities>
    <iati-activity default-currency="XDR">
      <iati-identifier>46002-P-TEST-001</iati-identifier>
      <reporting-org ref="XM-DAC-46002"><narrative>African Development Bank</narrative></reporting-org>
      <title><narrative>Test Project</narrative></title>
      {funders}
      {optional}
    </iati-activity>
    </iati-activities>
    """.encode()
    return xml_rows(data)[0]


_AFDF = '<participating-org ref="XM-DAC-46003" role="1"><narrative>African Development Fund</narrative></participating-org>'
_AFDB = '<participating-org ref="XM-DAC-46002" role="1"><narrative>African Development Bank</narrative></participating-org>'
_MIC = '<participating-org ref="XM-DAC-MIC Fund" role="1"><narrative>Middle Income Countries Fund</narrative></participating-org>'
_EU = '<participating-org ref="XM-DAC-EU" role="1"><narrative>European Union (EU)</narrative></participating-org>'


class WorldBankV3Tests(unittest.TestCase):
    """The v3 API shape (search.worldbank.org/api/v3/projects), live since 2026-09-16."""

    SOURCE = str(FIXTURES / "worldbank_projects_v3.json")

    def _run(self, bank_id, loan_lookup=None):
        scraper = create_scraper(bank_id, ScrapeOptions(source_file=self.SOURCE))
        if loan_lookup is None:
            return {record.project_id: record for record in scraper.run()}
        # A --source-file run skips the Finances One join; drive it directly.
        payload = json.loads(Path(self.SOURCE).read_text(encoding="utf-8"))
        records = [scraper._record(project, self.SOURCE, loan_lookup) for project in payload["projects"].values()]
        finalized = scraper.finalize([record for record in records if record.project_name])
        return {record.project_id: record for record in finalized}

    def test_each_arm_keeps_only_projects_with_its_own_commitment(self):
        self.assertEqual(sorted(self._run("ibrd")), ["P100001"])
        self.assertEqual(sorted(self._run("ida")), ["P100002", "P100003"])

    def test_a_fully_cancelled_loan_is_kept_when_finances_one_records_it(self):
        lookup = {"P100004": [{"original_principal_amount": 75000000.0, "disbursed_amount": 0.0}]}
        records = self._run("ibrd", lookup)
        self.assertIn("P100004", records)
        self.assertEqual(records["P100004"].loan_amount, Decimal("75000000.0"))
        self.assertEqual(records["P100004"].total_commitment, Decimal("0"))
        self.assertIn("original approved principal", records["P100004"].data_quality_notes)

    def test_loan_amount_falls_back_to_the_api_commitment_with_a_note(self):
        record = self._run("ibrd")["P100001"]
        self.assertEqual(record.loan_amount, Decimal("250000000"))
        self.assertIn("No IBRD loan/credit record in Finances One", record.data_quality_notes)

    def test_disbursement_comes_from_finances_one(self):
        lookup = {"P100001": [
            {"original_principal_amount": 200000000.0, "disbursed_amount": 12000000.0},
            {"original_principal_amount": 50000000.0, "disbursed_amount": 3000000.0},
        ]}
        record = self._run("ibrd", lookup)["P100001"]
        self.assertEqual(record.loan_amount, Decimal("250000000.0"))
        self.assertEqual(record.total_disbursement, Decimal("15000000.0"))
        self.assertEqual(record.disbursement_currency, "USD")

    def test_a_pipeline_project_is_kept_without_an_approval_year(self):
        record = self._run("ida")["P100003"]
        self.assertEqual(record.approval_date, "")
        self.assertIsNone(record.approval_year)
        self.assertIn("not (yet) approved", record.data_quality_notes)

    def test_a_dropped_project_is_excluded_unless_a_loan_was_actually_approved(self):
        payload = {"id": "P100009", "project_name": "Dropped", "status": "Dropped",
                   "boardapprovaldate": "2015-01-01T00:00:00Z", "curr_ibrd_commitment": "50000000"}
        scraper = create_scraper("ibrd", ScrapeOptions())
        self.assertEqual(scraper._record(payload, self.SOURCE, {}).project_name, "")
        terminated = scraper._record(payload, self.SOURCE, {"P100009": [
            {"original_principal_amount": 50000000.0, "board_approval_date": "2015-01-01T00:00:00"},
        ]})
        self.assertEqual(terminated.project_id, "P100009")
        self.assertEqual(terminated.approval_date, "2015-01-01")

    def test_a_closed_project_missing_the_apis_board_date_falls_back_to_finances_one(self):
        # Confirmed live against a real old project (P008921, "GRAIN
        # STORAGE", status Closed): the v3 API exposes no boardapprovaldate
        # at all, but Finances One's own loan record -- already fetched for
        # interest_rate/last_repayment_date -- carries a real
        # board_approval_date.
        payload = {"id": "P100010", "project_name": "Old Closed Project", "status": "Closed",
                   "curr_ibrd_commitment": "85000000"}
        scraper = create_scraper("ibrd", ScrapeOptions())
        record = scraper._record(payload, self.SOURCE, {"P100010": [
            {"original_principal_amount": 85000000.0, "board_approval_date": "1979-06-26T00:00:00"},
        ]})
        self.assertEqual(record.approval_date, "1979-06-26")
        self.assertIn("recovered from Finances One", record.data_quality_notes)
        finalized = scraper.finalize([record])[0]
        self.assertEqual(finalized.approval_year, 1979)

    def test_cofinanciers_exclude_the_bank_and_the_borrower(self):
        record = self._run("ibrd")["P100001"]
        self.assertEqual(record.cofinancing_partners, "Global Environment Facility (GEF)")
        self.assertEqual(record.cofinancing_amount, Decimal("10000000"))

    def test_instrument_comes_from_the_arms_own_financer_entries(self):
        ida = self._run("ida")["P100002"]
        self.assertEqual(ida.financing_instrument, "IDA Credit; IDA Grant")
        self.assertEqual(ida.instrument_category, "loan")
        ibrd = self._run("ibrd")["P100001"]
        self.assertEqual(ibrd.financing_instrument, "IBRD Loan")
        self.assertEqual(ibrd.instrument_category, "loan")

    def test_sectors_are_read_from_the_structured_list_without_the_taxonomy_prefix(self):
        record = self._run("ibrd")["P100001"]
        self.assertEqual(record.sector, "Urban Transport; Other - Industry, Trade, and Services")

    def test_world_bank_short_country_names_resolve_and_2026_approvals_are_present(self):
        record = self._run("ida")["P100002"]
        self.assertEqual(record.country_code, "COD")
        self.assertEqual(record.approval_year, 2026)
        self.assertEqual(record.project_url,
                         "https://projects.worldbank.org/en/projects-operations/project-detail/P100002")


class PipelineStageTests(unittest.TestCase):
    """BaseScraper.finalize()'s pipeline_stage classification, bank-agnostic."""

    def _finalize(self, status: str) -> ProjectRecord:
        scraper = create_scraper("ibrd", ScrapeOptions())
        record = ProjectRecord(project_name="Test Project", status=status)
        return scraper.finalize([record])[0]

    def test_known_not_yet_approved_statuses_are_flagged(self):
        for status in (
            "Proposed", "Pipeline", "Pipeline/identification", "Preparation",
            "En Preparación", "Under appraisal",
            # Case-insensitive.
            "proposed", "PIPELINE",
        ):
            with self.subTest(status=status):
                self.assertEqual(self._finalize(status).pipeline_stage, Decimal(1))

    def test_an_ordinary_approved_status_is_not_flagged(self):
        for status in ("Closed", "Approved", "Active", "Committed"):
            with self.subTest(status=status):
                self.assertEqual(self._finalize(status).pipeline_stage, Decimal(0))

    def test_aiib_on_hold_and_cancelled_are_not_treated_as_pipeline(self):
        # Confirmed live: AIIB's one On-Hold project was already approved
        # (has a real financing-approval date), and the large majority of
        # idb's/ndb's Cancelled rows already carry a real approval date --
        # neither status can be blanket-classified as "not yet approved".
        self.assertEqual(self._finalize("On Hold").pipeline_stage, Decimal(0))
        self.assertEqual(self._finalize("Cancelled").pipeline_stage, Decimal(0))

    def test_a_blank_status_leaves_pipeline_stage_blank(self):
        self.assertIsNone(self._finalize("").pipeline_stage)


class ConcessionalityTests(unittest.TestCase):
    def _record(self, *args, **kwargs):
        from mdbs_scraper.adapters.common import generic_record_from_row

        return generic_record_from_row(
            _afdb_activity(*args, **kwargs), bank_abbreviation="AfDB", day_first=True
        )

    def test_the_concessional_window_is_reported_by_name(self):
        record = self._record(_AFDF, finance_type="421")
        self.assertEqual(record.funding_window, "African Development Fund")
        self.assertEqual(record.concessional, "Yes")
        self.assertEqual(record.concessional_flag, Decimal(1))
        # The instrument comes from the same feed's bare finance-type code.
        self.assertEqual(record.financing_instrument, "Standard loan")

    def test_the_ordinary_capital_window_is_not_mistaken_for_the_bank_itself(self):
        # AfDB's <reporting-org> is XM-DAC-46002 with the very same name as
        # its ordinary-capital window, so a "is this just the bank listing
        # itself?" check keyed on ref or name would discard this entry and
        # silently reclassify every non-concessional AfDB project as unknown.
        record = self._record(_AFDB, finance_type="421", flow_type="10")
        self.assertEqual(record.funding_window, "African Development Bank")
        self.assertEqual(record.concessional, "No")
        self.assertEqual(record.concessional_flag, Decimal(0))

    def test_a_disclosed_window_outranks_a_contradictory_flow_type(self):
        # The activity above is tagged flow-type 10 (ODA), which taken alone
        # would read as concessional. AfDB mislabels flow type this way on
        # 799 of its 1,289 ordinary-capital activities (confirmed live
        # 2026-09-15), so the window has to win.
        record = self._record(_AFDB, flow_type="10")
        self.assertEqual(record.concessional, "No")

    def test_both_windows_together_report_as_blended(self):
        record = self._record(_AFDB + _AFDF)
        self.assertEqual(record.concessional, "Blended")
        # Blended is genuinely two-sided; the numeric companion stays blank
        # rather than forcing it onto one side.
        self.assertIsNone(record.concessional_flag)
        self.assertIn("concessional_flag is left blank", record.data_quality_notes)

    def test_window_order_does_not_change_the_reported_value(self):
        self.assertEqual(
            self._record(_AFDB + _AFDF).funding_window,
            self._record(_AFDF + _AFDB).funding_window,
        )

    def test_a_special_fund_grant_is_concessional(self):
        record = self._record(_MIC, finance_type="110")
        self.assertEqual(record.concessional, "Yes")
        self.assertIn("inferred from the financing being a grant", record.data_quality_notes)

    def test_a_special_fund_without_stated_terms_is_left_blank_with_a_reason(self):
        record = self._record(_MIC, finance_type="421")
        self.assertEqual(record.concessional, "")
        self.assertIsNone(record.concessional_flag)
        self.assertIn("Middle Income Countries Fund", record.data_quality_notes)

    def test_an_external_funder_is_a_cofinancier_not_a_window(self):
        record = self._record(_AFDF + _EU)
        self.assertEqual(record.funding_window, "African Development Fund")
        self.assertEqual(record.cofinancing_partners, "European Union (EU)")

    def test_flow_type_is_used_when_no_window_is_disclosed(self):
        # ADB's shape: it never names the window that financed an activity,
        # but does report ODA vs OOF, and that reporting is reliable.
        oda = self._record("", flow_type="10")
        self.assertEqual(oda.funding_window, "")
        self.assertEqual(oda.concessional, "Yes")
        self.assertIn("does not disclose which of its financing windows", oda.data_quality_notes)
        oof = self._record("", flow_type="21")
        self.assertEqual(oof.concessional, "No")
        self.assertEqual(oof.concessional_flag, Decimal(0))

    def test_neither_signal_leaves_concessionality_blank(self):
        record = self._record("")
        self.assertEqual(record.concessional, "")
        self.assertEqual(record.funding_window, "")

    def test_world_bank_windows_come_from_the_bank_identity(self):
        ibrd = create_scraper("ibrd", ScrapeOptions())._record(
            {"id": "P1", "project_name": "T", "ibrdcommamt": "1000000"}, "https://x", {}
        )
        self.assertEqual((ibrd.funding_window, ibrd.concessional), ("IBRD", "No"))
        ida = create_scraper("ida", ScrapeOptions())._record(
            {"id": "P2", "project_name": "T", "idacommamt": "1000000"}, "https://x", {}
        )
        self.assertEqual((ida.funding_window, ida.concessional), ("IDA", "Yes"))
        self.assertEqual(ida.concessional_flag, Decimal(1))


if __name__ == "__main__":
    unittest.main()
