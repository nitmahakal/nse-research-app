"""Bridge functions called from Android/Kotlin."""

from typing import Optional

import data_engine


def update_real_data_report(
    db_path: str,
    symbols_csv_path: str,
    progress_reporter=None,
):
    """Run raw market-data update and return a compact report."""

    def on_progress(done: int, total: int, phase: str):
        if progress_reporter is not None:
            try:
                progress_reporter.onProgress(
                    int(done),
                    int(total),
                    str(phase),
                )
            except Exception:
                # Progress reporting must never stop the actual update.
                pass

    try:
        if progress_reporter is not None:
            on_progress(
                0,
                0,
                "Loading NSE symbol list...",
            )

        symbols = data_engine.load_symbol_list(
            symbols_csv_path
        )

        total = len(symbols)

        if progress_reporter is not None:
            on_progress(
                0,
                total,
                f"Found {total} symbols",
            )

        report = data_engine.update_symbols(
            db_path=db_path,
            symbols=symbols,
            on_progress=on_progress,
        )

        return {
            "status": "SUCCESS",
            "total": report.get("total", total),
            "full": report.get("full", 0),
            "incremental": report.get(
                "incremental",
                0,
            ),
            "succeeded": report.get(
                "succeeded",
                0,
            ),
            "failed": report.get(
                "failed",
                0,
            ),
            "failed_symbols": report.get(
                "failed_symbols",
                [],
            ),
        }

    except Exception as exc:
        if progress_reporter is not None:
            try:
                progress_reporter.onProgress(
                    0,
                    0,
                    f"Update error: {exc}",
                )
            except Exception:
                pass

        return {
            "status": "ERROR",
            "error": str(exc),
        }"""Bridge functions called from Android/Kotlin."""

from typing import Optional

import data_engine


def update_real_data_report(
    db_path: str,
    symbols_csv_path: str,
    progress_reporter=None,
):
    """Run raw market-data update and return a compact report."""

    def on_progress(done: int, total: int, phase: str):
        if progress_reporter is not None:
            try:
                progress_reporter.onProgress(
                    int(done),
                    int(total),
                    str(phase),
                )
            except Exception:
                # Progress reporting must never stop the actual update.
                pass

    try:
        if progress_reporter is not None:
            on_progress(
                0,
                0,
                "Loading NSE symbol list...",
            )

        symbols = data_engine.load_symbol_list(
            symbols_csv_path
        )

        total = len(symbols)

        if progress_reporter is not None:
            on_progress(
                0,
                total,
                f"Found {total} symbols",
            )

        report = data_engine.update_symbols(
            db_path=db_path,
            symbols=symbols,
            on_progress=on_progress,
        )

        return {
            "status": "SUCCESS",
            "total": report.get("total", total),
            "full": report.get("full", 0),
            "incremental": report.get(
                "incremental",
                0,
            ),
            "succeeded": report.get(
                "succeeded",
                0,
            ),
            "failed": report.get(
                "failed",
                0,
            ),
            "failed_symbols": report.get(
                "failed_symbols",
                [],
            ),
        }

    except Exception as exc:
        if progress_reporter is not None:
            try:
                progress_reporter.onProgress(
                    0,
                    0,
                    f"Update error: {exc}",
                )
            except Exception:
                pass

        return {
            "status": "ERROR",
            "error": str(exc),
        }
