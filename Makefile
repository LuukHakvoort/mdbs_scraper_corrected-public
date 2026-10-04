# Pipeline shortcuts. Every target is a thin wrapper around main.py; see README.md.
PYTHON ?= .venv/bin/python
OUTPUT ?= output

.PHONY: test scrape combine backstop dyad refresh-reference crs-profile aiib-extract

test:
	$(PYTHON) -m unittest discover -s tests -v

# Collect all banks (browser-driven ones in their own process), merge the
# official files, run the backstop, combine, and build the country-year files.
scrape:
	$(PYTHON) main.py --all-banks --combine --dyad --output-dir $(OUTPUT)

# Rebuild all_mdb_projects.csv (and the backstop and coverage reports) from the
# per-bank CSVs already in $(OUTPUT), without scraping.
combine:
	$(PYTHON) main.py --all-banks --combine-only --output-dir $(OUTPUT)

backstop:
	$(PYTHON) main.py --all-banks --combine-only --strict-backstop --output-dir $(OUTPUT)

dyad:
	$(PYTHON) main.py --dyad --output-dir $(OUTPUT)

refresh-reference:
	$(PYTHON) main.py --dyad --refresh-reference --output-dir $(OUTPUT)

# Needs the manually downloaded OECD CRS zips in data/external/oecd_crs/.
crs-profile:
	$(PYTHON) main.py --crs-profile --output-dir $(OUTPUT)

# Re-extract the AIIB project-list PDF into online pulled data/extracted/ (needs pypdf).
aiib-extract:
	$(PYTHON) -c "from mdbs_scraper import official as o; print(o.write_aiib_extract(o.default_directory(o.OFFICIAL_REGISTRY) / 'AIIB Project List.pdf'))"
