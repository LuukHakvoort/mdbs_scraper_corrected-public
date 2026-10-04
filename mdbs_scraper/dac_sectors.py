"""OECD DAC sector code lookups (5-digit purpose codes and 3-digit category codes).

Some IATI publishers disclose a project's sector only as a bare numeric DAC
code (`<sector code="31161" vocabulary="1"/>`), with no human-readable
`<narrative>` text -- unlike publishers that spell the sector name out
directly. These tables translate those codes to their official names so the
`sector` field is not left blank for those banks. IATI's `vocabulary` attribute
distinguishes which codelist a code belongs to: "1" (or absent, IATI's default)
is the 5-digit purpose codelist (e.g. ADB); "2" is the coarser 3-digit category
codelist (e.g. IsDB). Other vocabularies (e.g. "99", a reporting-org's own
custom list) are deliberately not covered here -- their codes are not DAC codes
and translating them would risk a wrong, coincidental match.

Source: IATI's replication of the OECD DAC codelists
(https://codelists.codeforiati.org/api/json/en/Sector.json and
.../SectorCategory.json). Includes withdrawn codes (still valid for
translating older project data) alongside active ones.
"""

from __future__ import annotations

import re
import unicodedata


DAC_PURPOSE_CODES: dict[str, str] = {
    "11110": "Education policy and administrative management",
    "11120": "Education facilities and training",
    "11130": "Teacher training",
    "11182": "Educational research",
    "11220": "Primary education",
    "11230": "Basic life skills for adults",
    "11231": "Basic life skills for youth",
    "11232": "Primary education equivalent for adults",
    "11240": "Early childhood education",
    "11250": "School feeding",
    "11260": "Lower secondary education",
    "11320": "Upper Secondary Education (modified and includes data from 11322)",
    "11321": "Lower secondary education",
    "11322": "Upper secondary education",
    "11330": "Vocational training",
    "11420": "Higher education",
    "11430": "Advanced technical and managerial training",
    "12110": "Health policy and administrative management",
    "12181": "Medical education/training",
    "12182": "Medical research",
    "12191": "Medical services",
    "12196": "Health statistics and data",
    "12220": "Basic health care",
    "12230": "Basic health infrastructure",
    "12240": "Basic nutrition",
    "12250": "Infectious disease control",
    "12261": "Health education",
    "12262": "Malaria control",
    "12263": "Tuberculosis control",
    "12264": "COVID-19 control",
    "12281": "Health personnel development",
    "12310": "NCDs control, general",
    "12320": "Tobacco use control",
    "12330": "Control of harmful use of alcohol and drugs",
    "12340": "Promotion of mental health and well-being",
    "12350": "Other prevention and treatment of NCDs",
    "12382": "Research for prevention and control of NCDs",
    "13010": "Population policy and administrative management",
    "13020": "Reproductive health care",
    "13030": "Family planning",
    "13040": "STD control including HIV/AIDS",
    "13081": "Personnel development for population and reproductive health",
    "13096": "Population statistics and data",
    "14010": "Water sector policy and administrative management",
    "14015": "Water resources conservation (including data collection)",
    "14020": "Water supply and sanitation - large systems",
    "14021": "Water supply - large systems",
    "14022": "Sanitation - large systems",
    "14030": "Basic drinking water supply and basic sanitation",
    "14031": "Basic drinking water supply",
    "14032": "Basic sanitation",
    "14040": "River basins development",
    "14050": "Waste management/disposal",
    "14081": "Education and training in water supply and sanitation",
    "15110": "Public sector policy and administrative management",
    "15111": "Public finance management (PFM)",
    "15112": "Decentralisation and support to subnational government",
    "15113": "Anti-corruption organisations and institutions",
    "15114": "Domestic revenue mobilisation",
    "15116": "Tax collection",
    "15117": "Budget planning",
    "15118": "National audit",
    "15119": "Debt and aid management",
    "15120": "Public sector financial management",
    "15121": "Foreign affairs",
    "15122": "Diplomatic missions",
    "15123": "Administration of developing countries' foreign aid",
    "15124": "General personnel services",
    "15125": "Public Procurement",
    "15126": "Other general public services",
    "15127": "National monitoring and evaluation",
    "15128": "Local government finance",
    "15129": "Other central transfers to institutions",
    "15130": "Legal and judicial development",
    "15131": "Justice, law and order policy, planning and administration",
    "15132": "Police",
    "15133": "Fire and rescue services",
    "15134": "Judicial affairs",
    "15135": "Ombudsman",
    "15136": "Immigration",
    "15137": "Prisons",
    "15140": "Government administration",
    "15142": "Macroeconomic policy",
    "15143": "Meteorological services",
    "15144": "National standards development",
    "15150": "Democratic participation and civil society",
    "15151": "Elections",
    "15152": "Legislatures and political parties",
    "15153": "Media and free flow of information",
    "15154": "Executive office",
    "15155": "Tax policy and administration support",
    "15156": "Other non-tax revenue mobilisation",
    "15160": "Human rights",
    "15161": "Elections",
    "15162": "Human rights",
    "15163": "Free flow of information",
    "15164": "Women's equality organisations and institutions",
    "15170": "Women's rights organisations and movements, and government institutions",
    "15180": "Ending violence against women and girls",
    "15185": "Local government administration",
    "15190": "Facilitation of orderly, safe, regular and responsible migration and mobility",
    "15196": "Government and civil society statistics and data",
    "15210": "Security system management and reform",
    "15220": "Civilian peace-building, conflict prevention and resolution",
    "15230": "Participation in international peacekeeping operations",
    "15240": "Reintegration and SALW control",
    "15250": "Removal of land mines and explosive remnants of war",
    "15261": "Child soldiers (prevention and demobilisation)",
    "16010": "Social Protection",
    "16011": "Social protection and welfare services policy, planning and administration",
    "16012": "Social security (excl pensions)",
    "16013": "General pensions",
    "16014": "Civil service pensions",
    "16015": "Social services (incl youth development and women+ children)",
    "16020": "Employment creation",
    "16030": "Housing policy and administrative management",
    "16040": "Low-cost housing",
    "16050": "Multisector aid for basic social services",
    "16061": "Culture and cultural diversity",
    "16062": "Statistical capacity building",
    "16063": "Narcotics control",
    "16064": "Social mitigation of HIV/AIDS",
    "16065": "Recreation and sport",
    "16066": "Culture",
    "16070": "Labour rights",
    "16080": "Social dialogue",
    "21010": "Transport policy and administrative management",
    "21011": "Transport policy, planning and administration",
    "21012": "Public transport services",
    "21013": "Transport regulation",
    "21020": "Road transport",
    "21021": "Feeder road construction",
    "21022": "Feeder road maintenance",
    "21023": "National road construction",
    "21024": "National road maintenance",
    "21030": "Rail transport",
    "21040": "Water transport",
    "21050": "Air transport",
    "21061": "Storage",
    "21081": "Education and training in transport and storage",
    "22010": "Communications policy and administrative management",
    "22011": "Communications policy, planning and administration",
    "22012": "Postal services",
    "22013": "Information services",
    "22020": "Telecommunications",
    "22030": "Radio, television, print and online media",
    "22040": "Information and communication technology (ICT)",
    "22081": "Education and training in ICT, telecommunications and media",
    "23010": "Energy policy and administrative management",
    "23020": "Power generation/non-renewable sources",
    "23030": "Power generation/renewable sources",
    "23040": "Electrical transmission/ distribution",
    "23050": "Gas distribution",
    "23061": "Oil-fired power plants",
    "23062": "Gas-fired power plants",
    "23063": "Coal-fired power plants",
    "23064": "Nuclear power plants",
    "23065": "Hydro-electric power plants",
    "23066": "Geothermal energy",
    "23067": "Solar energy",
    "23068": "Wind power",
    "23069": "Ocean power",
    "23070": "Biomass",
    "23081": "Energy education/training",
    "23082": "Energy research",
    "23110": "Energy policy and administrative management",
    "23111": "Energy sector policy, planning and administration",
    "23112": "Energy regulation",
    "23181": "Energy education/training",
    "23182": "Energy research",
    "23183": "Energy conservation and demand-side efficiency",
    "23210": "Energy generation, renewable sources - multiple technologies",
    "23220": "Hydro-electric power plants",
    "23230": "Solar energy for centralised grids",
    "23231": "Solar energy for isolated grids and standalone systems",
    "23232": "Solar energy - thermal applications",
    "23240": "Wind energy",
    "23250": "Marine energy",
    "23260": "Geothermal energy",
    "23270": "Biofuel-fired power plants",
    "23310": "Energy generation, non-renewable sources, unspecified",
    "23320": "Coal-fired electric power plants",
    "23330": "Oil-fired electric power plants",
    "23340": "Natural gas-fired electric power plants",
    "23350": "Fossil fuel electric power plants with carbon capture and storage (CCS)",
    "23360": "Non-renewable waste-fired electric power plants",
    "23410": "Hybrid energy electric power plants",
    "23510": "Nuclear energy electric power plants and nuclear safety",
    "23610": "Heat plants",
    "23620": "District heating and cooling",
    "23630": "Electric power transmission and distribution (centralised grids)",
    "23631": "Electric power transmission and distribution (isolated mini-grids)",
    "23640": "Retail gas distribution",
    "23641": "Retail distribution of liquid or solid fossil fuels",
    "23642": "Electric mobility infrastructures",
    "24010": "Financial policy and administrative management",
    "24020": "Monetary institutions",
    "24030": "Formal sector financial intermediaries",
    "24040": "Informal/semi-formal financial intermediaries",
    "24050": "Remittance facilitation, promotion and optimisation",
    "24081": "Education/training in banking and financial services",
    "25010": "Business policy and administration",
    "25020": "Privatisation",
    "25030": "Business development services",
    "25040": "Responsible business conduct",
    "31110": "Agricultural policy and administrative management",
    "31120": "Agricultural development",
    "31130": "Agricultural land resources",
    "31140": "Agricultural water resources",
    "31150": "Agricultural inputs",
    "31161": "Food crop production",
    "31162": "Industrial crops/export crops",
    "31163": "Livestock",
    "31164": "Agrarian reform",
    "31165": "Agricultural alternative development",
    "31166": "Agricultural extension",
    "31181": "Agricultural education/training",
    "31182": "Agricultural research",
    "31191": "Agricultural services",
    "31192": "Plant and post-harvest protection and pest control",
    "31193": "Agricultural financial services",
    "31194": "Agricultural co-operatives",
    "31195": "Livestock/veterinary services",
    "31210": "Forestry policy and administrative management",
    "31220": "Forestry development",
    "31261": "Fuelwood/charcoal",
    "31281": "Forestry education/training",
    "31282": "Forestry research",
    "31291": "Forestry services",
    "31310": "Fishing policy and administrative management",
    "31320": "Fishery development",
    "31381": "Fishery education/training",
    "31382": "Fishery research",
    "31391": "Fishery services",
    "32110": "Industrial policy and administrative management",
    "32120": "Industrial development",
    "32130": "Small and medium-sized enterprises (SME) development",
    "32140": "Cottage industries and handicraft",
    "32161": "Agro-industries",
    "32162": "Forest industries",
    "32163": "Textiles, leather and substitutes",
    "32164": "Chemicals",
    "32165": "Fertilizer plants",
    "32166": "Cement/lime/plaster",
    "32167": "Energy manufacturing (fossil fuels)",
    "32168": "Pharmaceutical production",
    "32169": "Basic metal industries",
    "32170": "Non-ferrous metal industries",
    "32171": "Engineering",
    "32172": "Transport equipment industry",
    "32173": "Modern biofuels manufacturing",
    "32174": "Clean cooking appliances manufacturing, market development and distribution",
    "32182": "Technological research and development",
    "32210": "Mineral/mining policy and administrative management",
    "32220": "Mineral prospection and exploration",
    "32261": "Coal",
    "32262": "Oil and gas (upstream)",
    "32263": "Ferrous metals",
    "32264": "Nonferrous metals",
    "32265": "Precious metals/materials",
    "32266": "Industrial minerals",
    "32267": "Fertilizer minerals",
    "32268": "Offshore minerals",
    "32310": "Construction policy and administrative management",
    "33110": "Trade policy and administrative management",
    "33120": "Trade facilitation",
    "33130": "Regional trade agreements (RTAs)",
    "33140": "Multilateral trade negotiations",
    "33150": "Trade-related adjustment",
    "33181": "Trade education/training",
    "33210": "Tourism policy and administrative management",
    "41010": "Environmental policy and administrative management",
    "41020": "Biosphere protection",
    "41030": "Biodiversity",
    "41040": "Site preservation",
    "41050": "Flood prevention/control",
    "41081": "Environmental education/training",
    "41082": "Environmental research",
    "43010": "Multisector aid",
    "43030": "Urban development and management",
    "43031": "Urban land policy and management",
    "43032": "Urban development",
    "43040": "Rural development",
    "43041": "Rural land policy and management",
    "43042": "Rural development",
    "43050": "Non-agricultural alternative development",
    "43060": "Disaster Risk Reduction",
    "43071": "Food security policy and administrative management",
    "43072": "Household food security programmes",
    "43073": "Food safety and quality",
    "43081": "Multisector education/training",
    "43082": "Research/scientific institutions",
    "51010": "General budget support-related aid",
    "52010": "Food assistance",
    "53030": "Import support (capital goods)",
    "53040": "Import support (commodities)",
    "60010": "Action relating to debt",
    "60020": "Debt forgiveness",
    "60030": "Relief of multilateral debt",
    "60040": "Rescheduling and refinancing",
    "60061": "Debt for development swap",
    "60062": "Other debt swap",
    "60063": "Debt buy-back",
    "72010": "Material relief assistance and services",
    "72011": "Basic Health Care Services in Emergencies",
    "72012": "Education in emergencies",
    "72040": "Emergency food assistance",
    "72050": "Relief co-ordination and support services",
    "73010": "Immediate post-emergency reconstruction and rehabilitation",
    "74010": "Disaster prevention and preparedness",
    "74020": "Multi-hazard response preparedness",
    "91010": "Administrative costs (non-sector allocable)",
    "92010": "Support to national NGOs",
    "92020": "Support to international NGOs",
    "92030": "Support to local and regional NGOs",
    "93010": "Refugees/asylum seekers in donor countries (non-sector allocable)",
    "93011": "Refugees/asylum seekers in donor countries - food and shelter",
    "93012": "Refugees/asylum seekers in donor countries - training",
    "93013": "Refugees/asylum seekers in donor countries - health",
    "93014": "Refugees/asylum seekers in donor countries - other temporary sustenance",
    "93015": "Refugees/asylum seekers in donor countries - voluntary repatriation",
    "93016": "Refugees/asylum seekers in donor countries - transport",
    "93017": "Refugees/asylum seekers in donor countries - rescue at sea",
    "93018": "Refugees/asylum seekers in donor countries - administrative costs",
    "99810": "Sectors not specified",
    "99820": "Promotion of development awareness (non-sector allocable)",
}
"""5-digit DAC CRS Purpose Code -> name (IATI sector vocabulary "1", or absent)."""


DAC_CATEGORY_CODES: dict[str, str] = {
    "111": "Education, Level Unspecified",
    "112": "Basic Education",
    "113": "Secondary Education",
    "114": "Post-Secondary Education",
    "121": "Health, General",
    "122": "Basic Health",
    "123": "Non-communicable diseases (NCDs)",
    "130": "Population Policies/Programmes & Reproductive Health",
    "140": "Water Supply & Sanitation",
    "151": "Government & Civil Society-general",
    "152": "Conflict, Peace & Security",
    "160": "Other Social Infrastructure & Services",
    "210": "Transport & Storage",
    "220": "Communications",
    "230": "ENERGY GENERATION AND SUPPLY",
    "231": "Energy Policy",
    "232": "Energy generation, renewable sources",
    "233": "Energy generation, non-renewable sources",
    "234": "Hybrid energy plants",
    "235": "Nuclear energy plants",
    "236": "Energy distribution",
    "240": "Banking & Financial Services",
    "250": "Business & Other Services",
    "311": "Agriculture",
    "312": "Forestry",
    "313": "Fishing",
    "321": "Industry",
    "322": "Mineral Resources & Mining",
    "323": "Construction",
    "331": "Trade Policies & Regulations",
    "332": "Tourism",
    "410": "General Environment Protection",
    "430": "Other Multisector",
    "510": "General Budget Support",
    "520": "Development Food Assistance",
    "530": "Other Commodity Assistance",
    "600": "Action Relating to Debt",
    "720": "Emergency Response",
    "730": "Reconstruction Relief & Rehabilitation",
    "740": "Disaster Prevention & Preparedness",
    "910": "Administrative Costs of Donors",
    "920": "SUPPORT TO NON- GOVERNMENTAL ORGANISATIONS (NGOs)",
    "930": "Refugees in Donor Countries",
    "998": "Unallocated / Unspecified",
}
"""3-digit DAC Sector Category code -> name (IATI sector vocabulary "2")."""


IATI_ACTIVITY_STATUS_CODES: dict[str, str] = {
    "1": "Pipeline/identification",
    "2": "Implementation",
    "3": "Finalisation",
    "4": "Closed",
    "5": "Cancelled",
    "6": "Suspended",
}
"""IATI ActivityStatus codelist (source: codelists.codeforiati.org), used
to translate <activity-status code="X"/> when a bank's own `status` text
is not disclosed (confirmed: adb/afdb/caf/isdb never disclose `status` as
text at all, only this code)."""


def status_name_from_code(code: str) -> str:
    """Return an IATI ActivityStatus code's official name, or "" if unrecognized."""
    if not code:
        return ""
    return IATI_ACTIVITY_STATUS_CODES.get(code.strip(), "")


IATI_FINANCE_TYPE_CODES: dict[str, str] = {
    "110": "Standard grant",
    "111": "Subsidies to national private investors",
    "210": "Interest subsidy",
    "310": "Capital subscription on deposit basis",
    "410": "Aid loan excluding debt reorganisation",
    "421": "Standard loan",
    "422": "Reimbursable grant",
    "423": "Bonds",
    "431": "Subordinated loan",
    "432": "Preferred equity",
    "433": "Other hybrid instruments",
    "510": "Common equity",
    "912": "Purchase of securities from issuing agencies",
    "1100": "Guarantees/insurance",
}
"""IATI FinanceType codelist (source: codelists.codeforiati.org), used to
translate <default-finance-type code="X"/> into `financing_instrument`.
Confirmed live (2026-09-15): adb/afdb/caf disclose only this bare code and no
instrument text at all, leaving `financing_instrument` 100% blank without it.
Only the codes those publishers actually use are listed."""


# IATI FlowType codelist (source: codelists.codeforiati.org). Only the
# ODA/OOF distinction is needed: for a multilateral development bank, ODA
# means the operation was concessional and OOF means it was not. The private
# and "other flows" codes (30-37, 40, 50) say nothing about concessionality
# and are deliberately absent so they resolve to blank rather than a guess.
IATI_CONCESSIONAL_FLOW_TYPE_CODES = {"10"}
IATI_NON_CONCESSIONAL_FLOW_TYPE_CODES = {"20", "21", "22"}


def finance_type_name_from_code(code: str) -> str:
    """Return an IATI FinanceType code's official name, or "" if unrecognized."""
    if not code:
        return ""
    return IATI_FINANCE_TYPE_CODES.get(code.strip(), "")


def sector_name_from_dac_code(code: str, vocabulary: str = "1") -> str:
    """Return a DAC sector code's official name, or "" if unrecognized.

    ``vocabulary`` selects which codelist to check: "1" (default, matching
    IATI's own default when the attribute is absent) for the 5-digit purpose
    codelist, "2" for the 3-digit category codelist. Any other vocabulary
    always returns "" -- never invents a name for an unrecognized code or an
    unsupported vocabulary, consistent with this project's conservative-parsing
    conventions elsewhere (see cleaning.py).
    """
    if not code:
        return ""
    code = code.strip()
    if vocabulary == "2":
        return DAC_CATEGORY_CODES.get(code, "")
    if vocabulary == "1":
        return DAC_PURPOSE_CODES.get(code, "")
    return ""


# --- Cross-bank sector standardization -------------------------------------
#
# sector/loan_type are disclosed in wildly different vocabularies across the
# 15 banks (OECD DAC 5-digit purpose names, Spanish free text, bank-internal
# jargon, ALL-CAPS labels...), which makes cross-bank aggregation impossible
# without a shared bucket. sector_category_from() derives one, in tiers that
# prefer authoritative/automatic derivation over hand-curation wherever the
# disclosed text allows it:
#   1. A disclosed DAC code (sector_code/sector_vocabulary) -> exact, no
#      curation needed.
#   2. sector text that's an exact OECD DAC purpose name (many IATI
#      publishers' narrative text already reads this way, e.g. AfDB) ->
#      derived from that purpose code's own category, no curation needed.
#   3. sector text that's an exact OECD DAC *category* name already (some
#      publishers, e.g. CABEI, disclose category-level text directly) -> used
#      as-is, no curation needed.
#   4. SECTOR_TEXT_TO_CATEGORY below: a hand-curated lookup for the
#      genuinely bank-specific vocabularies tiers 1-3 can't resolve (caf's
#      Spanish terms, aiib/cdb/idb/ndb/cabei's own short labels, etc).
# If nothing matches at any tier, the category is left blank -- never guess.


def _normalize_sector_text(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", folded.lower()).strip()


_PURPOSE_NAME_TO_CATEGORY_CODE: dict[str, str] = {
    _normalize_sector_text(name): purpose_code[:3] for purpose_code, name in DAC_PURPOSE_CODES.items()
}
_CATEGORY_NAME_TO_CODE: dict[str, str] = {
    _normalize_sector_text(name): category_code for category_code, name in DAC_CATEGORY_CODES.items()
}


# Bank-specific sector vocabularies that don't already read as OECD DAC
# purpose/category text (tiers 2-3 above) -- curated by hand against the
# real, complete distinct-value list for each bank (mdbs_scraper output,
# 2026-08-24/25: aiib 16, cabei 30, caf 12, cdb 11, idb 18, ndb 8, ebrd 13,
# badea 8, eib ~50 (of 162; the rest is a long tail of one-off NACE codes)
# distinct values). A source's own comma/semicolon-joined multi-sector text is matched
# on its first listed sector, not the full joined string -- see
# sector_category_from(). Deliberately not exhaustive: a handful of entries
# per bank (e.g. AIIB's "Other", CDB's "Youth"/"Infrastructure", IDB's
# "OTHER") are too vague to assign a category to without guessing, and are
# left unmapped on purpose.
SECTOR_TEXT_TO_CATEGORY: dict[str, str] = {
    # AIIB
    "energy": "230",
    "transport": "210",
    "multi sector": "430",
    "crf economic resilience pbf": "510",
    "water": "140",
    "urban": "160",
    "crf public health": "121",
    "crf finance liquidity": "240",
    "digital infrastructure and technology": "220",
    "health infrastructure": "121",
    "education infrastructure": "111",
    "erf policy based financing": "510",
    "economic resilience pbf": "510",
    "rural infrastructure and agriculture development": "311",
    # CAF (Spanish)
    "transporte": "210",
    "agua saneamiento preservacion de recursos hidricos y gestion de residuos": "140",
    "administracion publica": "151",
    "sector financiero": "240",
    "energia e industrias extractivas": "230",
    "multisectorial": "430",
    "salud": "121",
    "educacion cultura y deporte": "111",
    "proteccion y servicios sociales": "160",
    "tecnologias de la informacion y la comunicacion": "220",
    "agricultura ganaderia pesca y silvicultura": "311",
    "industria comercio y servicios": "321",
    # EBRD (13 distinct values; "Energy"/"Transport" already covered above)
    "depository credit banks": "240",
    "manufacturing services": "321",
    "food and agribusiness": "311",
    "equity funds": "240",
    "municipal env inf": "140",
    "non depository credit non bank": "240",
    "real estate": "250",
    "telecommunications media and technology": "220",
    "leasing finance": "240",
    "natural resources": "322",
    "insurance pension mutual funds": "240",
    # BADEA (8 distinct values; "Transport" already covered above)
    "social": "160",
    "capacity development": "151",
    "microfinance smes and entrepreneurship development": "240",
    "agriculture rural development and food security": "311",
    "utilities": "230",
    "industrialisation": "321",
    "women and youth development": "160",
    # EIB (~50 of 162 distinct NACE-vocabulary values, ~80% coverage --
    # EIB's IATI sector vocabulary is "99", Eurostat NACE Rev. 2 economic-
    # activity codes, not OECD DAC, so the disclosed-code tiers never fire
    # here; matched purely on narrative text. "global loans" is the
    # first-listed fragment of "Global Loans, Loans for SMEs, Loans for
    # SMEs and Mid-Caps, Loans for Mid-Caps" (478 of 1,395 activities, the
    # single largest value) -- mapping the fragment survives any future
    # variant of the trailing list, unlike hardcoding the full string.
    "global loans": "240",
    "financial service activities except insurance and pension funding": "240",
    "other financial service activities except insurance and pension funding": "240",
    "venture capital fund regional": "240",
    "venture capital fund": "240",
    "venture capital fund global": "240",
    "fund management activities": "240",
    "trusts funds and similar financial entities": "240",
    "special purpose vehicles": "240",
    "water collection treatment and supply": "140",
    "wastewater treatment": "140",
    "wastewater treatment and storage": "140",
    "wastewater collection": "140",
    "supply and sewerage": "140",
    "drinking water supply": "140",
    "drinking water treatment": "140",
    "desalination": "140",
    "sewerage": "140",
    "stormwater collection drainage": "140",
    "irrigation": "140",
    "flow control dykes protection against erosion etc": "140",
    "waste collection treatment and disposal activities materials recovery": "140",
    "roads": "210",
    "conventional railways": "210",
    "motorways": "210",
    "urbain railways": "210",
    "roads and motorways": "210",
    "conventional railways track signalling buildings": "210",
    "conventional underground lines": "210",
    "urban passenger transport": "210",
    "urban and suburban passenger land transport": "210",
    "sea port installations river development works": "210",
    "airports and airport installations": "210",
    "re solar pv": "232",
    "alternative and renewable sources of energy": "232",
    "hydropower run of the river": "232",
    "re wind onshore": "232",
    "re solar csp": "232",
    "hydropower conventional with storage": "232",
    "renewable energy transmission infrastructures": "232",
    "distribution of electricity": "236",
    "transmission of electricity": "236",
    "transmission networks incl submarine cables": "236",
    "high voltage transmission": "236",
    "electricity gas steam and air conditioning supply": "230",
    "electric power generation transmission and distribution": "230",
    "heat supply": "230",
    "gaslines": "230",
    "heat production plants": "230",
    "human health activities": "121",
    "hospital activities": "121",
    "support activities to agriculture and post harvest crop activities": "311",
    "silviculture and other forestry activities": "312",
    "manufacture of food products": "321",
    "public buildings": "323",
    "specialised construction activities": "323",
    "housing": "160",
    "urban infrastructure": "160",
    "provision of services to the community as a whole": "160",
    "mobile broadband networks": "220",
    # EIB's own website (eib.org's "page-provider/projects/list" API, used
    # from 2026-09-03 instead of the old IATI-mirror source) discloses a
    # separate, much cleaner ~13-value sector taxonomy rather than raw NACE
    # text -- most already resolve via the tiers above (e.g. "Energy",
    # "Transport", "Health"); these three are this taxonomy's own labels,
    # not covered by anything already here. "Services" and "Composite
    # infrastructure" are deliberately left unmapped -- too vague to assign
    # a single DAC category to, same convention as "Other"/"Infrastructure"
    # elsewhere in this table.
    "credit lines": "240",
    "telecom": "220",
    "solid waste": "140",
    # CDB
    "climate": "410",
    "education": "111",
    "transportation": "210",
    "water resource management": "140",
    "food security": "311",
    "financial services": "240",
    "poverty alleviation": "160",
    "institutions": "151",
    "private sector development": "250",
    # IDB (ALL CAPS in the source; matched after lowercasing)
    "reform modernization of the state": "151",
    "social investment": "160",
    "private firms and sme development": "250",
    "environment and natural disasters": "410",
    "water and sanitation": "140",
    "agriculture and rural development": "311",
    "financial markets": "240",
    "trade": "331",
    "health": "121",
    "urban development and housing": "160",
    "science and technology": "250",
    "sustainable tourism": "332",
    "regional integration": "151",
    "industry": "321",
    # IBRD/IDA (World Bank): its own sub-sector taxonomy, nominally CRS-aligned
    # but not exact-text-matching IATI's codelist wording, so tiers 2-3 above
    # never resolve it automatically despite the underlying concepts being
    # the same. Curated against the top 60 first-listed sub-sector fragments
    # in a live 1,500-project sample (2026-08-24), covering 99.9% of that
    # sample's sector-having rows -- the long tail beyond this is left
    # unmapped rather than guessed.
    "social protection": "160",
    # "health": already added above (IDB uses the same word).
    "central government central agencies": "151",
    "other public administration": "151",
    "sub national government": "151",
    "rural and inter urban roads": "210",
    "energy transmission and distribution": "236",
    "public administration agriculture": "311",
    "water supply": "140",
    "health facilities and construction": "121",
    "public administration health": "121",
    "banking institutions": "240",
    "ict infrastructure": "220",
    "capital markets": "240",
    "renewable energy solar": "232",
    "agricultural extension": "311",
    "other energy and extractives": "230",
    "other education": "111",
    "primary education": "112",
    "public administration education": "111",
    "other agriculture": "311",
    "other water supply": "140",
    "agricultural markets": "311",
    "ict services": "220",
    "irrigation and drainage": "311",
    "public administration water": "140",
    "fisheries": "313",
    "forestry": "312",
    "sanitation": "140",
    "urban transport": "210",
    "secondary education": "113",
    "tertiary education": "114",
    "workforce development and vocational education": "114",
    "public administration social protection": "160",
    "renewable energy hydro": "232",
    "renewable energy geothermal": "232",
    "other industry": "321",
    "livestock": "311",
    "public administration information and communications technologies": "220",
    "public administration industry": "321",
    "waste management": "140",
    "public administration transportation": "210",
    "tourism": "332",
    "crops": "311",
    "railways": "210",
    "ports waterways": "210",
    "public administration energy and extractives": "230",
    "public administration financial sector": "240",
    "housing construction": "160",
    "other transportation": "210",
    "early childhood education": "112",
    "other non bank financial institutions": "240",
    "aviation": "210",
    "law and justice": "151",
    "mining": "322",
    "other information and communications technologies": "220",
    "manufacturing": "321",
    "renewable energy wind": "232",
    "oil and gas": "233",
    "non renewable energy generation": "233",
    # "trade": already added above (IDB uses the same word).
    # AfDB residuals (its sector text is overwhelmingly exact DAC purpose-name
    # text, resolved automatically by tier 2 above -- these 3 are the only
    # values that don't exactly match the codelist's own wording).
    "electric power transmission and distribution": "230",
    "bio diversity": "410",
    "basic life skills for youth and adults": "112",
    # NDB
    "transport infrastructure": "210",
    "clean energy and energy efficiency": "232",
    "multiple areas": "430",
    # "water and sanitation": already added above (IDB uses the same phrase).
    "social infrastructure": "160",
    "covid 19 emergency assistance": "720",
    "environmental protection": "410",
    "digital infrastructure": "220",
}


def sector_category_from(sector_text: str, sector_code: str, sector_vocabulary: str) -> tuple[str, str]:
    """Derive a standardized (name, 3-digit code) OECD DAC category for a project.

    Tries, in order: the disclosed DAC code; an exact DAC purpose-name match;
    an exact DAC category-name match; the hand-curated table above (against
    both the full sector text and, for multi-sector text, its first listed
    entry). Returns ("", "") if nothing matches -- callers should treat that
    as genuinely unclassifiable, not an error.
    """

    if sector_code:
        vocabulary = sector_vocabulary or "1"
        code = sector_code.strip()
        if vocabulary == "2" and code in DAC_CATEGORY_CODES:
            return DAC_CATEGORY_CODES[code], code
        if vocabulary == "1" and code in DAC_PURPOSE_CODES:
            category_code = code[:3]
            if category_code in DAC_CATEGORY_CODES:
                return DAC_CATEGORY_CODES[category_code], category_code

    if not sector_text:
        return "", ""

    candidates = [sector_text]
    first_listed = re.split(r"[;,]", sector_text, maxsplit=1)[0].strip()
    if first_listed and first_listed != sector_text:
        candidates.append(first_listed)

    for candidate in candidates:
        key = _normalize_sector_text(candidate)
        if key in _PURPOSE_NAME_TO_CATEGORY_CODE:
            category_code = _PURPOSE_NAME_TO_CATEGORY_CODE[key]
            if category_code in DAC_CATEGORY_CODES:
                return DAC_CATEGORY_CODES[category_code], category_code
        if key in _CATEGORY_NAME_TO_CODE:
            category_code = _CATEGORY_NAME_TO_CODE[key]
            return DAC_CATEGORY_CODES[category_code], category_code
        if key in SECTOR_TEXT_TO_CATEGORY:
            category_code = SECTOR_TEXT_TO_CATEGORY[key]
            return DAC_CATEGORY_CODES[category_code], category_code

    return "", ""
