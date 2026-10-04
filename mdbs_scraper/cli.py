"""Command-line interface for reproducible multi-bank collection runs."""

from __future__ import annotations

import argparse
import json
import logging
import pickle
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable

from . import http as http_module
from .base import ScrapeOptions
from .config import BANKS, DEFAULT_YEAR_FILTER, YearFilterDefault, canonical_bank_id
from .coverage import flag_summary, write_coverage_by_year
from .crs import DEFAULT_CRS_DIR, profile_crs
from .dyad import build_dyad_outputs
from .backstop import failing, rows_for, run_backstop, summarize
from .output import (
    combine_csv_files,
    read_csv_rows,
    coverage_summary,
    sha256,
    write_dict_rows,
    write_records,
    write_summary_csv,
)
from .reconcile import EXCLUDED_FIELDS, apply_official_sources
from .registry import create_scraper
from .report import write_bank_failure_report, write_bank_report
from .schema import ProjectRecord, utc_now


LOG = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _bank_override(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("Expected BANK=VALUE")
    bank, setting = value.split("=", 1)
    bank = canonical_bank_id(bank)
    if bank not in BANKS:
        raise argparse.ArgumentTypeError(f"Unknown bank: {bank}")
    if not setting:
        raise argparse.ArgumentTypeError("Override value cannot be empty")
    return bank, setting


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mdb-scrape",
        description="Collect project/arrangement data from 15 official MDB/institutional sources.",
    )
    selection = parser.add_argument_group("bank selection")
    selection.add_argument("--bank", action="append", default=[], help="Bank ID; repeat as needed")
    selection.add_argument("--banks", nargs="+", default=[], help="One or more bank IDs")
    selection.add_argument("--all-banks", action="store_true", help="Run all 15 adapters")
    selection.add_argument("--list-banks", action="store_true", help="Print the verified registry and exit")

    filters = parser.add_argument_group("filters")
    filters.add_argument(
        "--min-year", type=int, default=None,
        help=f"Default {DEFAULT_YEAR_FILTER.min_year} (see mdbs_scraper/year_filter.json)",
    )
    filters.add_argument(
        "--max-year", type=int, default=None,
        help=f"Default {DEFAULT_YEAR_FILTER.max_year} (see mdbs_scraper/year_filter.json)",
    )
    filters.add_argument("--max-projects", type=int, default=None, help="Per-bank cap for testing")

    source = parser.add_argument_group("source and browser")
    source.add_argument(
        "--source-file", action="append", type=_bank_override, default=[], metavar="BANK=PATH",
        help="Use an official downloaded/local snapshot for one bank",
    )
    source.add_argument(
        "--source-url", action="append", type=_bank_override, default=[], metavar="BANK=URL",
        help="Override one bank's source URL",
    )
    source.add_argument(
        "--source-format", action="append", type=_bank_override, default=[], metavar="BANK=FORMAT",
        help="Declare csv, tsv, json, xml, html, or xlsx when inference is impossible",
    )
    source.add_argument("--no-headless", action="store_true", help="Show Chromium for dynamic portals")
    source.add_argument("--timeout", type=float, default=45.0)
    source.add_argument("--request-delay", type=float, default=0.75)
    source.add_argument(
        "--no-isolation", action="store_true",
        help="Run browser-driven banks in this process even in a multi-bank run (not recommended)",
    )
    source.add_argument(
        "--save-raw", metavar="DIR", default="",
        help="Archive every fetched response body (with a JSON sidecar) under DIR",
    )

    official = parser.add_argument_group("official portfolio files (see mdbs_scraper/official_sources.json)")
    official.add_argument(
        "--official-dir", default="",
        help="Directory holding the registered official files (default: 'online pulled data/' in the repo)",
    )
    official.add_argument(
        "--no-official", action="store_true",
        help="Do not merge official files and do not run the backstop checks",
    )
    official.add_argument(
        "--crs-profile", nargs="?", const=str(DEFAULT_CRS_DIR), default="", metavar="DIR",
        help="Profile manually downloaded OECD CRS zip files (default dir: data/external/oecd_crs) "
             "against all_mdb_projects.csv; reports only, changes no output",
    )
    official.add_argument(
        "--strict-backstop", action="store_true",
        help="Exit with code 3 when any backstop check against an official file fails",
    )

    output = parser.add_argument_group("output")
    output.add_argument("--output-dir", default="output")
    output.add_argument("--format", choices=("csv", "json", "xlsx"), default="csv")
    output.add_argument("--combine", action="store_true")
    output.add_argument(
        "--combine-only", action="store_true",
        help="Rebuild all_mdb_projects.csv from the per-bank CSVs already in --output-dir, without scraping",
    )
    output.add_argument("--include-raw", action="store_true", help="Add JSON-encoded source fields")
    output.add_argument("--parallel", type=int, default=1, metavar="WORKERS")
    output.add_argument(
        "--allow-partial", action="store_true",
        help="Exit successfully when some requested banks fail (failures remain in manifest.json)",
    )
    output.add_argument(
        "--dyad", action="store_true",
        help="Build constant-2025-USD project amounts and bank-country-year totals from "
             "all_mdb_projects.csv (after --combine/--combine-only, or on its own)",
    )
    output.add_argument(
        "--refresh-reference", action="store_true",
        help="Download the IMF exchange rates, the FRED deflator and the World Bank PPP and "
             "exchange-rate series again before --dyad",
    )
    output.add_argument("--verbose", action="store_true")
    # Internal: a browser-driven bank re-invokes this CLI in a child process
    # and hands its results back through this file.
    output.add_argument("--child-result", default="", help=argparse.SUPPRESS)
    return parser


def _apply_default_year_filter(args: argparse.Namespace, default: YearFilterDefault) -> None:
    """Fill --min-year/--max-year from `default` when a run left them unset.

    An explicit --min-year/--max-year on the command line always wins; this
    only fills in whichever of the two was left as None.
    """

    if not default.enabled:
        return
    if args.min_year is None:
        args.min_year = default.min_year
    if args.max_year is None:
        args.max_year = default.max_year


def _selected_banks(args: argparse.Namespace) -> list[str]:
    if args.all_banks:
        requested = list(BANKS)
    else:
        requested = args.bank + args.banks
    canonical: list[str] = []
    for bank in requested:
        bank_id = canonical_bank_id(bank)
        if bank_id not in BANKS:
            raise ValueError(f"Unknown bank '{bank}'. Use --list-banks.")
        if bank_id not in canonical:
            canonical.append(bank_id)
    return canonical


def _options_for(args: argparse.Namespace, bank_id: str) -> ScrapeOptions:
    files = dict(args.source_file)
    urls = dict(args.source_url)
    formats = dict(args.source_format)
    return ScrapeOptions(
        min_year=args.min_year,
        max_year=args.max_year,
        max_projects=args.max_projects,
        source_file=files.get(bank_id, ""),
        source_url=urls.get(bank_id, ""),
        source_format=formats.get(bank_id, ""),
        headless=not args.no_headless,
        timeout=args.timeout,
        request_delay=args.request_delay,
        include_raw=args.include_raw,
    )


def _run_bank(args: argparse.Namespace, bank_id: str) -> tuple[list[ProjectRecord], dict]:
    LOG.info("Collecting %s", bank_id)
    try:
        scraper = create_scraper(bank_id, _options_for(args, bank_id))
        records = scraper.run()
    except Exception as exc:
        # Browser-driven (dynamic) banks occasionally hit a known macOS/
        # Playwright driver-lifecycle race -- confirmed transient: rerunning
        # the same bank alone always succeeds. One free retry recovers from
        # that automatically without masking a real failure, since a
        # re-scrape is always safe/idempotent. Multi-bank runs additionally
        # isolate these banks in a child process (see _run_bank_isolated).
        if not BANKS[bank_id].dynamic:
            raise
        LOG.warning("%s failed (%s: %s); retrying once", bank_id, type(exc).__name__, exc)
        scraper = create_scraper(bank_id, _options_for(args, bank_id))
        records = scraper.run()
    metadata = dict(scraper.run_metadata)
    if not args.no_official and not dict(args.source_file).get(bank_id):
        # An explicit --source-file snapshot is the whole source for that run.
        records, report = apply_official_sources(
            bank_id, records, _options_for(args, bank_id), _official_dir(args)
        )
        metadata["official"] = report["sources"]
        metadata["official_excluded"] = report["excluded"]
        metadata["rows_after_official_merge"] = len(records)
    LOG.info("Collected %s records for %s", len(records), bank_id)
    supplementary = getattr(scraper, "supplementary_outputs", None)
    if isinstance(supplementary, dict) and supplementary:
        metadata["supplementary_outputs"] = dict(supplementary)
    return records, metadata


def _needs_isolation(args: argparse.Namespace, bank_ids: list[str], bank_id: str) -> bool:
    """Whether a bank should run in its own process.

    Several Playwright launches in one process can crash on macOS (see
    docs/PROBLEMS.md), and an in-process retry cannot recover from that. The
    2026-09-15 refresh worked around it by running the three browser banks
    separately -- which is exactly how all_mdb_projects.csv ended up with 11
    of 14 banks. Isolating them here lets one --all-banks --combine run
    produce the complete file. A --source-file snapshot never opens a browser.
    """

    return (
        len(bank_ids) > 1
        and BANKS[bank_id].dynamic
        and not args.no_isolation
        and not dict(args.source_file).get(bank_id)
    )


def _child_argv(args: argparse.Namespace, bank_id: str, result_path: Path) -> list[str]:
    argv = [sys.executable, "-m", "mdbs_scraper", "--bank", bank_id, "--child-result", str(result_path)]
    for flag, value in (
        ("--min-year", args.min_year), ("--max-year", args.max_year),
        ("--max-projects", args.max_projects), ("--timeout", args.timeout),
        ("--request-delay", args.request_delay),
    ):
        if value is not None:
            argv += [flag, str(value)]
    for flag, pairs in (("--source-url", args.source_url), ("--source-format", args.source_format)):
        value = dict(pairs).get(bank_id)
        if value:
            argv += [flag, f"{bank_id}={value}"]
    for flag, enabled in (
        ("--no-headless", args.no_headless), ("--include-raw", args.include_raw),
        ("--verbose", args.verbose),
    ):
        if enabled:
            argv.append(flag)
    if args.save_raw:
        argv += ["--save-raw", args.save_raw]
    if args.official_dir:
        argv += ["--official-dir", args.official_dir]
    if args.no_official:
        argv.append("--no-official")
    return argv


def _run_bank_isolated(args: argparse.Namespace, bank_id: str) -> tuple[list[ProjectRecord], dict]:
    last_error = ""
    for attempt in range(2):
        with tempfile.TemporaryDirectory() as directory:
            result_path = Path(directory) / "result.pickle"
            LOG.info("Collecting %s in a separate process%s", bank_id, " (retry)" if attempt else "")
            # close_fds=False and no cwd let CPython use posix_spawn on macOS
            # instead of fork: a forked child segfaults (exit -11) once the
            # parent has used macOS networking frameworks with fork handlers,
            # which happens during ordinary scraping (confirmed 2026-09-16:
            # --banks adb badea crashed every BADEA child). Python opens its
            # own files non-inheritable, so nothing leaks into the child.
            completed = subprocess.run(_child_argv(args, bank_id, result_path), close_fds=False, check=False)
            if result_path.is_file():
                with result_path.open("rb") as handle:
                    result = pickle.load(handle)
                http_module.extend_fetch_log(result.get("fetches", []))
                if result.get("error") is None:
                    return result["records"], result["metadata"]
                last_error = str(result["error"])
            else:
                last_error = f"child process exited with code {completed.returncode} and no result"
        LOG.warning("%s failed in its child process: %s", bank_id, last_error)
    raise RuntimeError(last_error)


def _run_child(args: argparse.Namespace, bank_ids: list[str]) -> int:
    """Child-process mode: collect one bank and pickle the outcome for the parent."""

    result: dict[str, object] = {"error": None}
    try:
        records, metadata = _run_bank(args, bank_ids[0])
        result.update(records=records, metadata=metadata)
    except Exception as exc:  # noqa: BLE001 -- reported to the parent, which records the failure
        result["error"] = f"{type(exc).__name__}: {exc}"
    result["fetches"] = http_module.fetch_log()
    with Path(args.child_result).open("wb") as handle:
        pickle.dump(result, handle)
    return 0 if result["error"] is None else 1


def _official_dir(args: argparse.Namespace) -> Path | None:
    return Path(args.official_dir).expanduser().resolve() if args.official_dir else None


def _write_reconciliation(output_dir: Path, bank_id: str, metadata: dict, outputs: dict) -> None:
    excluded = metadata.pop("official_excluded", None)
    if excluded is None:
        return
    path = write_dict_rows(output_dir / "reports" / f"{bank_id}_reconciliation.csv", EXCLUDED_FIELDS, excluded)
    outputs[f"{bank_id}_reconciliation"] = {"path": str(path), "sha256": sha256(path)}


def _backstop(
    args: argparse.Namespace, rows_by_bank: dict[str, list[dict]], output_dir: Path,
    outputs: dict, manifest: dict,
) -> int:
    """Run the backstop; returns 3 when --strict-backstop is set and a check failed."""

    if args.no_official or not rows_by_bank:
        return 0
    path, results = run_backstop(
        rows_by_bank, output_dir, args.min_year or 2002, args.max_year or 2026, _official_dir(args)
    )
    outputs["backstop_summary"] = {"path": str(path), "sha256": sha256(path)}
    manifest["backstop"] = summarize(results)
    failures = failing(results)
    if failures:
        print(
            f"Backstop: {len(failures)} check(s) below the official files -- see {path}",
            file=sys.stderr,
        )
        return 3 if args.strict_backstop else 0
    print("Backstop: every check against the official files passed")
    return 0


def _dyad(args: argparse.Namespace, output_dir: Path) -> int:
    combined = output_dir / "all_mdb_projects.csv"
    if not combined.is_file():
        print(f"--dyad needs {combined}; run --combine or --combine-only first", file=sys.stderr)
        return 2
    entry = build_dyad_outputs(
        combined, output_dir, min_year=args.min_year or 2002, max_year=args.max_year or 2026,
        refresh_reference=args.refresh_reference,
    )
    print(
        f"Country-year files: {entry['rows']['dyad']} observed rows, {entry['rows']['dyad_balanced']} "
        f"balanced rows, {entry['rows']['dyad_unallocated']} unallocated bank-years "
        f"(amount conversions: {entry['amount_conversions']})"
    )
    return 0


def _crs_profile(args: argparse.Namespace, output_dir: Path) -> int:
    try:
        path, rows = profile_crs(
            Path(args.crs_profile).expanduser(), output_dir / "all_mdb_projects.csv", output_dir
        )
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"CRS profile: {len(rows)} bank-years written to {path}")
    return 0


def _list_banks() -> None:
    print(f"{'ID':<7} {'Abbreviation':<14} {'Method':<20} Name")
    for bank in BANKS.values():
        print(f"{bank.id:<7} {bank.abbreviation:<14} {bank.method:<20} {bank.name}")
    print("\nCompatibility alias: cabi -> cabei")


def _git_commit() -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10, check=True,
        )
        return completed.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _source_file_hashes(args: argparse.Namespace) -> dict[str, dict[str, str]]:
    hashes: dict[str, dict[str, str]] = {}
    for bank_id, path in args.source_file:
        candidate = Path(path).expanduser()
        if candidate.is_file():
            hashes[bank_id] = {"path": str(candidate.resolve()), "sha256": sha256(candidate)}
    return hashes


def _failure_summary(bank_id: str, error: str) -> dict[str, str]:
    return {
        "bank_id": bank_id,
        "status": "failed",
        "error": error,
        "project_count": "",
        "with_commitment_year": "",
        "with_loan_amount": "",
        "zero_loan_amount": "",
        "with_total_commitment": "",
        "with_total_disbursement": "",
        "with_sector": "",
        "with_sector_category": "",
        "with_subnational_location": "",
        "with_conditionality": "",
        "with_cofinancing": "",
        "with_disbursement_timing": "",
        "rows_seen": "",
        "rows_retained": "",
        "duplicates_removed": "",
        "source_total": "",
        "completeness": "failed",
    }


def _write_supplementary(output_dir: Path, metadata: dict, outputs: dict[str, dict[str, str]]) -> None:
    """Write any extra datasets a scraper handed over (e.g. CABEI's private-sector totals)."""

    for name, spec in (metadata.pop("supplementary_outputs", None) or {}).items():
        path = write_dict_rows(output_dir / f"{name}.csv", spec["fields"], spec["rows"])
        outputs[name] = {"path": str(path), "sha256": sha256(path)}
        metadata.setdefault("supplementary_rows", {})[name] = len(spec["rows"])


def _combine_only(args: argparse.Namespace, bank_ids: list[str], output_dir: Path) -> int:
    started_at = utc_now()
    paths: list[Path] = []
    missing: list[str] = []
    for bank_id in bank_ids:
        path = output_dir / f"{bank_id}_projects.csv"
        if path.is_file():
            paths.append(path)
        else:
            missing.append(bank_id)
    if missing and not args.allow_partial:
        print(
            f"Missing per-bank files for: {', '.join(missing)}. Collect them first, or pass "
            "--allow-partial to combine without them.",
            file=sys.stderr,
        )
        return 2
    combined_path = output_dir / "all_mdb_projects.csv"
    try:
        inputs = combine_csv_files(paths, combined_path, include_raw=args.include_raw)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    coverage_path, coverage_rows = write_coverage_by_year(
        output_dir, bank_ids, args.min_year or 2002, args.max_year or 2026
    )
    record_count = sum(int(entry["rows"]) for entry in inputs)
    outputs = {
        "combined": {"path": str(combined_path), "sha256": sha256(combined_path)},
        "coverage_by_year": {"path": str(coverage_path), "sha256": sha256(coverage_path)},
    }
    manifest = {
        "schema_version": "2.1",
        "mode": "combine-only",
        "started_at": started_at,
        "finished_at": utc_now(),
        "git_commit": _git_commit(),
        "requested_banks": bank_ids,
        "combined_banks": [Path(str(entry["path"])).name.split("_projects")[0] for entry in inputs],
        "missing_banks": missing,
        "record_count": record_count,
        "inputs": {"per_bank_files": inputs},
        "outputs": outputs,
        "filters": {"min_year": args.min_year, "max_year": args.max_year},
    }
    rows_by_bank = {
        Path(str(entry["path"])).name.split("_projects")[0]: read_csv_rows(Path(str(entry["path"])))[1]
        for entry in inputs
    }
    backstop_code = _backstop(args, rows_by_bank, output_dir, outputs, manifest)
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Combined {record_count} records from {len(inputs)} bank file(s) into {combined_path}")
    print(flag_summary(coverage_rows))
    if missing:
        print(f"Not included (no per-bank file): {', '.join(missing)}", file=sys.stderr)
    return backstop_code


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if args.list_banks:
        _list_banks()
        return 0
    _apply_default_year_filter(args, DEFAULT_YEAR_FILTER)
    try:
        bank_ids = _selected_banks(args)
    except ValueError as exc:
        parser.error(str(exc))
    if not bank_ids and not (args.dyad or args.crs_profile):
        parser.error("Select --bank, --banks, or --all-banks (or run --dyad / --crs-profile on its own)")
    if bank_ids and args.dyad and not (args.combine or args.combine_only):
        parser.error("--dyad with a bank selection needs --combine or --combine-only")
    if args.min_year and args.max_year and args.min_year > args.max_year:
        parser.error("--min-year cannot exceed --max-year")
    if args.parallel < 1:
        parser.error("--parallel must be at least 1")
    if args.max_projects is not None and args.max_projects < 1:
        parser.error("--max-projects must be at least 1")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.request_delay < 0:
        parser.error("--request-delay cannot be negative")
    if args.combine_only and args.format != "csv":
        parser.error("--combine-only works on the per-bank CSV files; use --format csv")
    if args.child_result and len(bank_ids) != 1:
        parser.error("--child-result takes exactly one bank")

    http_module.configure_raw_archive(args.save_raw or None)
    http_module.reset_fetch_log()
    if args.child_result:
        return _run_child(args, bank_ids)
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if not bank_ids:
        code = _dyad(args, output_dir) if args.dyad else 0
        return max(code, _crs_profile(args, output_dir)) if args.crs_profile else code
    if args.combine_only:
        code = _combine_only(args, bank_ids, output_dir)
        return code if code == 2 or not args.dyad else max(code, _dyad(args, output_dir))

    started_at = utc_now()
    collected: dict[str, tuple[list[ProjectRecord], dict]] = {}
    failures: dict[str, str] = {}

    def collect(bank_id: str) -> tuple[list[ProjectRecord], dict]:
        if _needs_isolation(args, bank_ids, bank_id):
            return _run_bank_isolated(args, bank_id)
        return _run_bank(args, bank_id)

    workers = min(args.parallel, len(bank_ids))
    if workers == 1:
        for bank_id in bank_ids:
            try:
                collected[bank_id] = collect(bank_id)
            except Exception as exc:  # manifest must record bank-specific failures
                LOG.error("%s failed: %s", bank_id, exc)
                failures[bank_id] = f"{type(exc).__name__}: {exc}"
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_bank = {executor.submit(collect, bank_id): bank_id for bank_id in bank_ids}
            for future in as_completed(future_to_bank):
                bank_id = future_to_bank[future]
                try:
                    collected[bank_id] = future.result()
                except Exception as exc:
                    LOG.error("%s failed: %s", bank_id, exc)
                    failures[bank_id] = f"{type(exc).__name__}: {exc}"

    outputs: dict[str, dict[str, str]] = {}
    summaries = []
    extension = args.format
    for bank_id in bank_ids:  # deterministic registry/request order
        if bank_id not in collected:
            continue
        records, metadata = collected[bank_id]
        path = output_dir / f"{bank_id}_projects.{extension}"
        write_records(path, records, args.format, include_raw=args.include_raw)
        outputs[bank_id] = {"path": str(path), "sha256": sha256(path)}
        _write_supplementary(output_dir, metadata, outputs)
        _write_reconciliation(output_dir, bank_id, metadata, outputs)
        summaries.append(coverage_summary(bank_id, records, metadata))
        write_bank_report(bank_id, records, output_dir, metadata)
    for bank_id in bank_ids:
        if bank_id in failures:
            summaries.append(_failure_summary(bank_id, failures[bank_id]))
            write_bank_failure_report(bank_id, failures[bank_id], output_dir)
    summary_path = write_summary_csv(output_dir / "project_counts_and_coverage.csv", summaries)
    combined_records = [
        record
        for bank_id in bank_ids
        for record in collected.get(bank_id, ([], {}))[0]
    ]
    if args.combine:
        combined_path = output_dir / f"all_mdb_projects.{extension}"
        write_records(combined_path, combined_records, args.format, include_raw=args.include_raw)
        outputs["combined"] = {"path": str(combined_path), "sha256": sha256(combined_path)}
        left_out = [bank_id for bank_id in BANKS if bank_id not in collected]
        if left_out:
            print(
                f"Warning: {combined_path.name} holds only {len(collected)} of {len(BANKS)} banks "
                f"(missing: {', '.join(left_out)}). Collect them, then run "
                "--all-banks --combine-only to rebuild the complete file.",
                file=sys.stderr,
            )
    coverage_rows: list[dict[str, object]] = []
    if args.format == "csv":
        coverage_path, coverage_rows = write_coverage_by_year(
            output_dir, list(BANKS), args.min_year or 2002, args.max_year or 2026
        )
        outputs["coverage_by_year"] = {"path": str(coverage_path), "sha256": sha256(coverage_path)}

    manifest = {
        "schema_version": "2.1",
        "mode": "collect",
        "started_at": started_at,
        "finished_at": utc_now(),
        "git_commit": _git_commit(),
        "requested_banks": bank_ids,
        "successful_banks": [bank_id for bank_id in bank_ids if bank_id in collected],
        "failures": failures,
        "record_count": len(combined_records),
        "bank_metadata": {
            bank_id: collected[bank_id][1] for bank_id in bank_ids if bank_id in collected
        },
        "coverage_summary_path": str(summary_path),
        "outputs": outputs,
        "inputs": {
            "fetched": http_module.fetch_log(),
            "source_files": _source_file_hashes(args),
        },
        "filters": {"min_year": args.min_year, "max_year": args.max_year},
        "source_overrides": {
            "files": dict(args.source_file), "urls": dict(args.source_url),
            "formats": dict(args.source_format),
        },
    }
    backstop_code = _backstop(
        args,
        {bank_id: rows_for(collected[bank_id][0]) for bank_id in bank_ids
         if bank_id in collected and not dict(args.source_file).get(bank_id)},
        output_dir, outputs, manifest,
    )
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(combined_records)} records from {len(collected)} bank(s) to {output_dir}")
    if coverage_rows:
        print(flag_summary([row for row in coverage_rows if row["bank_id"] in collected]))
    if args.dyad:
        dyad_code = _dyad(args, output_dir)
        backstop_code = max(backstop_code, dyad_code)
    if failures:
        print(f"Failures: {', '.join(failures)}. See {manifest_path}", file=sys.stderr)
        return 0 if args.allow_partial else 2
    return backstop_code


if __name__ == "__main__":
    raise SystemExit(main())
