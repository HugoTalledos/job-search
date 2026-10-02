"""Run the local collector: python -m local_collector [--config config.yaml]."""

from __future__ import annotations

import argparse
import logging
import sys

from .bootstrap import build_collector
from .config import load_config, load_dotenv


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="local_collector")
    parser.add_argument("--config", help="Path to config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    load_dotenv()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        report = build_collector(load_config(args.config)).execute()
    except ValueError as exc:
        print(f"Error de configuración: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        logging.exception("Collector failed")
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for error in report.errors:
        print(f"error: {error}", file=sys.stderr)
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
