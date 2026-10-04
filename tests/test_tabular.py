from pathlib import Path
import unittest

from mdbs_scraper.tabular import format_from_content_type, xml_rows


FIXTURES = Path(__file__).parent / "fixtures"


class XmlRowsNarrativeTests(unittest.TestCase):
    def test_narrative_is_keyed_by_its_wrapping_element(self):
        data = (FIXTURES / "iati_narrative_sample.xml").read_bytes()
        rows = xml_rows(data)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["title"], "Proyecto de Transporte Ferroviario")
        self.assertEqual(
            rows[0]["description"], "Financiamiento para un sistema de tren rapido de pasajeros."
        )
        self.assertEqual(rows[1]["title"], "Segundo Proyecto de Ejemplo")
        # The reporting-org narrative must not clobber title/description.
        self.assertNotEqual(rows[0]["title"], rows[0].get("narrative"))


class IatiTransactionFieldsTests(unittest.TestCase):
    def test_transaction_and_date_types_are_disambiguated(self):
        data = (FIXTURES / "iati_transactions_sample.xml").read_bytes()
        rows = xml_rows(data)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        # Commitment (transaction-type 2): a single transaction, taken as-is.
        self.assertEqual(row["iati_commitment_value"], "50000000")
        self.assertEqual(row["iati_commitment_currency"], "USD")
        self.assertEqual(row["iati_commitment_date"], "2020-03-01")
        # Disbursements (transaction-type 3): only the two positive
        # transactions are summed (10000000 + 15000000 = 25000000); the
        # negative one (-2000000, a correction/reversal) is excluded from
        # the total and its date excluded from first/last tracking, but its
        # magnitude is preserved separately; the type-4 Expenditure
        # transaction (9500000) must not be counted as a disbursement at all.
        self.assertEqual(row["iati_disbursement_value"], "25000000")
        self.assertEqual(row["iati_disbursement_currency"], "USD")
        self.assertEqual(row["iati_disbursement_correction"], "-2000000")
        self.assertEqual(row["iati_first_disbursement_date"], "2021-04-10")
        self.assertEqual(row["iati_last_disbursement_date"], "2022-09-05")
        # Activity dates: actual (type 2/4) preferred over planned (type 1/3).
        self.assertEqual(row["iati_start_date"], "2020-03-01")
        self.assertEqual(row["iati_end_date"], "2025-06-30")
        # Cofinancing: only a Funding-role (1) participant other than the
        # reporting-org itself counts; the Implementing-role (4) government
        # counterpart must not appear. Nor must "Example DB - Concessional
        # Window" -- same org `ref` (XM-DAC-99999) as the reporting-org
        # despite a different narrative, i.e. the reporting bank disclosing
        # one of its own internal windows/funds under a different label
        # rather than a real external cofinancier (confirmed live: IsDB's
        # "IsDB - Ordinary Capital Resources" et al. follow this exact shape).
        self.assertEqual(row["iati_cofinancing_partners"], "Regional Cofinancing Facility")
        # That window is captured rather than discarded: it says who actually
        # financed the project, which is not the same question as who
        # cofinanced it alongside the bank.
        self.assertEqual(row["iati_funding_window"], "Example DB - Concessional Window")

    def test_generic_record_from_row_maps_iati_fields(self):
        from mdbs_scraper.adapters.common import generic_record_from_row

        data = (FIXTURES / "iati_transactions_sample.xml").read_bytes()
        row = xml_rows(data)[0]
        record = generic_record_from_row(row, default_currency="USD", day_first=False)
        self.assertEqual(str(record.loan_amount), "50000000")
        self.assertEqual(record.loan_currency, "USD")
        self.assertEqual(record.commitment_date, "2020-03-01")
        self.assertEqual(record.completion_date, "2025-06-30")
        self.assertEqual(str(record.total_disbursement), "25000000")
        self.assertEqual(record.first_disbursement_date, "2021-04-10")
        self.assertEqual(record.last_disbursement_date, "2022-09-05")
        self.assertEqual(record.cofinancing_partners, "Regional Cofinancing Facility")
        self.assertEqual(record.funding_window, "Example DB - Concessional Window")
        # The excluded correction is disclosed via a note, not silently dropped.
        self.assertIn("2000000", record.data_quality_notes)
        self.assertIn("correction/reversal", record.data_quality_notes)
        # <activity-status code="2"/> with no status text disclosed ->
        # translated via IATI's own codelist.
        self.assertEqual(record.status, "Implementation")
        self.assertIn("activity-status code 2", record.data_quality_notes)

    def test_generic_record_from_row_strips_a_cofinancer_matching_the_banks_own_abbreviation(self):
        # Confirmed live: EIB discloses itself as a role="1" (Funding)
        # participant using its own abbreviation ("EIB"), not its full
        # reporting-org name ("European Investment Bank") -- so the
        # narrative-exact-match exclusion above misses it. Unlike IsDB's
        # internal-fund self-references, this entry's org `ref`
        # ("40-Multilateral") does *not* match the reporting-org's own ref,
        # so it can only be caught by comparing against the bank's known
        # abbreviation -- which xml_rows() alone has no access to.
        from mdbs_scraper.adapters.common import generic_record_from_row

        data = b"""<?xml version="1.0"?>
        <iati-activities>
        <iati-activity default-currency="EUR">
          <iati-identifier>TEST-SELF-ABBR</iati-identifier>
          <reporting-org ref="XM-DAC-918-3">
            <narrative>European Investment Bank</narrative>
          </reporting-org>
          <participating-org role="1" ref="40-Multilateral">
            <narrative>EDF</narrative>
          </participating-org>
          <participating-org role="1" ref="40-Multilateral">
            <narrative>EIB</narrative>
          </participating-org>
        </iati-activity>
        </iati-activities>
        """
        row = xml_rows(data)[0]
        # xml_rows() itself has no bank context, so both partners survive.
        self.assertEqual(row["iati_cofinancing_partners"], "EDF; EIB")
        record = generic_record_from_row(row, default_currency="EUR", bank_abbreviation="EIB")
        self.assertEqual(record.cofinancing_partners, "EDF")

    def _activity_with_implementing_org(self, org_type: str) -> bytes:
        return f"""<?xml version="1.0"?>
        <iati-activities>
        <iati-activity default-currency="USD">
          <iati-identifier>TEST-COUNTERPART-{org_type}</iati-identifier>
          <reporting-org ref="XM-DAC-1">
            <narrative>Example Development Bank</narrative>
          </reporting-org>
          <participating-org role="4" type="{org_type}">
            <narrative>Counterpart</narrative>
          </participating-org>
        </iati-activity>
        </iati-activities>
        """.encode()

    def test_generic_record_from_row_maps_private_sector_counterpart_to_non_sovereign(self):
        # Confirmed live (adb/afdb/eib): a role="4" (Implementing)
        # participating-org's `type` attribute uses IATI's official
        # OrganisationType codelist. type="70" is Private Sector.
        from mdbs_scraper.adapters.common import generic_record_from_row

        data = self._activity_with_implementing_org("70")
        row = xml_rows(data)[0]
        self.assertEqual(row["iati_counterpart_org_type"], "Private Sector")
        record = generic_record_from_row(row, default_currency="USD")
        self.assertEqual(record.loan_type, "Non-sovereign")

    def test_generic_record_from_row_maps_government_counterpart_to_sovereign(self):
        # type="10" is Government.
        from mdbs_scraper.adapters.common import generic_record_from_row

        data = self._activity_with_implementing_org("10")
        row = xml_rows(data)[0]
        self.assertEqual(row["iati_counterpart_org_type"], "Government")
        record = generic_record_from_row(row, default_currency="USD")
        self.assertEqual(record.loan_type, "Sovereign")

    def test_generic_record_from_row_leaves_loan_type_blank_for_an_ambiguous_counterpart_type(self):
        # type="40" is Multilateral -- neither clearly sovereign nor private;
        # never guessed.
        from mdbs_scraper.adapters.common import generic_record_from_row

        data = self._activity_with_implementing_org("40")
        row = xml_rows(data)[0]
        self.assertNotIn("iati_counterpart_org_type", row)
        record = generic_record_from_row(row, default_currency="USD")
        self.assertEqual(record.loan_type, "")

    def test_value_falls_back_to_the_activity_default_currency(self):
        # AfDB/IsDB: <value> omits its own currency attribute and relies on
        # the activity's default-currency (confirmed live: AfDB's own files
        # declare default-currency="XDR" and never repeat it per-value).
        data = b"""<?xml version="1.0"?>
        <iati-activities>
        <iati-activity default-currency="XDR">
          <iati-identifier>TEST-XDR</iati-identifier>
          <transaction>
            <transaction-type code="2"/>
            <transaction-date iso-date="2020-03-01"/>
            <value>50000000</value>
          </transaction>
        </iati-activity>
        </iati-activities>
        """
        row = xml_rows(data)[0]
        self.assertEqual(row["iati_commitment_currency"], "XDR")

    def test_value_currency_attribute_wins_over_the_activity_default(self):
        data = b"""<?xml version="1.0"?>
        <iati-activities>
        <iati-activity default-currency="XDR">
          <iati-identifier>TEST-USD</iati-identifier>
          <transaction>
            <transaction-type code="2"/>
            <transaction-date iso-date="2020-03-01"/>
            <value currency="USD">50000000</value>
          </transaction>
        </iati-activity>
        </iati-activities>
        """
        row = xml_rows(data)[0]
        self.assertEqual(row["iati_commitment_currency"], "USD")


class EibIatiSampleTests(unittest.TestCase):
    def test_real_eib_activity_shape_maps_correctly(self):
        from mdbs_scraper.adapters.common import generic_record_from_row

        data = (FIXTURES / "eib_iati_sample.xml").read_bytes()
        rows = xml_rows(data)
        self.assertEqual(len(rows), 2)
        record = generic_record_from_row(rows[0], default_currency="EUR", day_first=False)

        self.assertEqual(record.country, "Mozambique")
        self.assertEqual(record.country_code, "MOZ")
        # EIB's sector vocabulary is "99" (Eurostat NACE, not OECD DAC), so
        # this only resolves via the hand-curated SECTOR_TEXT_TO_CATEGORY
        # entry, not the disclosed-code tiers.
        self.assertEqual(record.sector, "RE : solar PV")
        self.assertEqual(record.sector_category, "Energy generation, renewable sources")
        self.assertEqual(record.sector_category_code, "232")
        self.assertEqual(record.status, "Finalisation")
        self.assertEqual(str(record.loan_amount), "11720706")
        self.assertEqual(record.loan_currency, "EUR")
        # role="4" (Implementing) participating-org type="70" (Private
        # Sector, IATI's official OrganisationType codelist) -- confirmed
        # live as EIB's real disclosure shape for a private-sector borrower.
        self.assertEqual(record.loan_type, "Non-sovereign")
        # project_url comes from <document-link url="..."/>, not a
        # dedicated project-URL field -- EIB discloses no other URL field.
        self.assertEqual(
            record.project_url, "http://www.eib.org/en/registers/all/index.htm?q=20010242"
        )

    def test_invalid_status_code_degrades_to_blank_without_crashing(self):
        from mdbs_scraper.adapters.common import generic_record_from_row

        data = (FIXTURES / "eib_iati_sample.xml").read_bytes()
        rows = xml_rows(data)
        # <activity-status code="0"/> is not a valid IATI ActivityStatus
        # code (range is 1-6) -- confirmed present in EIB's real data (4 of
        # 1,395 activities). Must not raise and must not fabricate a note.
        record = generic_record_from_row(rows[1], default_currency="EUR", day_first=False)

        self.assertEqual(record.status, "")
        self.assertNotIn("activity-status code", record.data_quality_notes)


class FormatFromContentTypeTests(unittest.TestCase):
    def test_known_mime_types_map_to_a_format(self):
        self.assertEqual(format_from_content_type("text/csv; charset=utf-8"), "csv")
        self.assertEqual(format_from_content_type("application/json"), "json")
        self.assertEqual(format_from_content_type("text/xml"), "xml")

    def test_unknown_or_generic_types_yield_empty_string(self):
        self.assertEqual(format_from_content_type("application/octet-stream"), "")
        self.assertEqual(format_from_content_type(""), "")


if __name__ == "__main__":
    unittest.main()
