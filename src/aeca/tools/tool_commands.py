"""The `aeca eval`, `aeca seed-calendar` and `aeca rehearse` commands, which the Make targets run.

They print what they did by id and count, never an email's contents.
"""

import argparse
import os
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from aeca.adapters.anthropic_client import AnthropicClient
from aeca.adapters.clock import SystemClock
from aeca.adapters.google_services import build_google_services
from aeca.adapters.models_file import load_models
from aeca.adapters.rotating_log import Rotation
from aeca.adapters.tracer import JsonlTracer
from aeca.application.budget import Budget
from aeca.application.decide import DecideDeps, decide
from aeca.domain.errors import ConfigError
from aeca.domain.secret import SecretStr
from aeca.settings import Settings, load_settings, require_rehearsal_senders
from aeca.tools.corpus import corpus_message_id, load_corpus
from aeca.tools.evaluate import (
    EvalReport,
    EvalStopped,
    ProgressLog,
    evaluate,
    format_report,
    write_report,
)
from aeca.tools.rehearse import MailAppSender, OsascriptRunner, plan_rehearsal, rehearse
from aeca.tools.seed_calendar import SeedWeek, next_monday, seed_calendar

DEFAULT_CORPUS = Path("tests/corpus/emails.json")
TRACE_ROTATION = Rotation(max_bytes=5_000_000, keep=3)
DATA_DIR_VARIABLE = "AECA_DATA_DIR"
LOCAL_DATA_DIR = Path("data")


def add_tool_commands(subparsers: Any) -> None:
    evaluation = subparsers.add_parser("eval", help="decide every corpus email for real (paid)")
    _add_corpus_option(evaluation)
    evaluation.add_argument("--out", type=Path, help="where to write the JSON report")
    evaluation.set_defaults(handler=_eval)
    seeding = subparsers.add_parser("seed-calendar", help="fill a test week in the real calendar")
    seeding.add_argument(
        "--week-start",
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="the Monday that starts the week (default: next Monday)",
    )
    seeding.set_defaults(handler=_seed_calendar)
    rehearsal = subparsers.add_parser("rehearse", help="send the corpus as real mail")
    _add_corpus_option(rehearsal)
    rehearsal.set_defaults(handler=_rehearse)


def _add_corpus_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)


def tool_data_dir(environ: Mapping[str, str], settings: Settings) -> Path:
    """The tools run on the host, where the container's /data does not exist."""
    return settings.data_dir if environ.get(DATA_DIR_VARIABLE, "").strip() else LOCAL_DATA_DIR


def _eval(arguments: argparse.Namespace) -> int:
    settings = load_settings(os.environ, purpose="tools")
    corpus = load_corpus(arguments.corpus)
    data_dir = _writable_directory(tool_data_dir(os.environ, settings))
    clock = SystemClock()
    budget = Budget(settings.max_usd_per_day)
    path = arguments.out or data_dir / f"eval-{_utc_stamp(clock.now())}.json"
    progress = ProgressLog(path.with_suffix(".progress.jsonl"))
    deps = _decide_deps(settings, data_dir=data_dir, clock=clock, budget=budget)
    try:
        report = evaluate(
            corpus,
            lambda facts: decide(facts, deps),
            budget=budget,
            clock=clock,
            on_decided=progress,
        )
    except EvalStopped as stopped:
        _publish(stopped.report, path)
        raise
    progress.discard()
    _publish(report, path)
    return 0


def _publish(report: EvalReport, path: Path) -> None:
    write_report(report, path)
    print(format_report(report))
    print(f"\nreport written to {path}")


def _decide_deps(
    settings: Settings, *, data_dir: Path, clock: SystemClock, budget: Budget
) -> DecideDeps:
    tracer = _tracer(settings, data_dir)
    choice = load_models().decision
    return DecideDeps(
        model=AnthropicClient.from_api_key(_anthropic_key(settings), tracer),
        model_name=choice.model,
        effort=choice.effort,
        clock=clock,
        zone=_eval_zone(settings),
        tracer=tracer,
        budget=budget,
    )


def _anthropic_key(settings: Settings) -> SecretStr:
    if settings.anthropic_api_key is None:
        raise ConfigError(
            "ANTHROPIC_API_KEY is not set.",
            next_action="Set ANTHROPIC_API_KEY in .env (see .env.example), then run again.",
        )
    return settings.anthropic_api_key


def _tracer(settings: Settings, data_dir: Path) -> JsonlTracer:
    secrets = (
        settings.google_client_secret,
        *(s for s in (settings.google_refresh_token, settings.anthropic_api_key) if s is not None),
    )
    return JsonlTracer(
        data_dir / "eval-trace",
        secrets=secrets,
        rotation=TRACE_ROTATION,
        debug_enabled=False,
        clock=SystemClock(),
    )


def _eval_zone(settings: Settings) -> ZoneInfo:
    """TIMEZONE when set, and then no Google credentials are needed for it."""
    if settings.timezone is not None:
        return ZoneInfo(settings.timezone)
    return ZoneInfo(build_google_services(settings).calendar.time_zone())


def _writable_directory(directory: Path) -> Path:
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ConfigError(
            f"AECA_DATA_DIR={directory} cannot be written: {error.strerror}.",
            next_action="Set AECA_DATA_DIR in .env to a writable folder, then run again.",
        ) from error
    return directory


def _utc_stamp(moment: datetime) -> str:
    return moment.strftime("%Y%m%dT%H%M%SZ")


def _seed_calendar(arguments: argparse.Namespace) -> int:
    settings = load_settings(os.environ, purpose="tools")
    requested: date | None = arguments.week_start
    if requested is not None and requested.weekday() != 0:
        raise ConfigError(
            f"--week-start {requested} is not a Monday.",
            next_action="Give the Monday that starts the week, as YYYY-MM-DD.",
        )
    services = build_google_services(settings)
    zone = ZoneInfo(services.calendar.time_zone())
    week_start = requested or next_monday(SystemClock().now(), zone)
    report = seed_calendar(services.seed_calendar, SeedWeek(week_start=week_start, zone=zone))
    print(
        f"Week of {week_start} ({zone.key}): created {len(report.created)} events, "
        f"skipped {len(report.skipped)} already there."
    )
    print("created event ids: " + (", ".join(report.created) or "none"))
    return 0


def _rehearse(arguments: argparse.Namespace) -> int:
    settings = load_settings(os.environ, purpose="tools")
    senders = require_rehearsal_senders(settings)
    print(f"internal: {senders.internal}\nexternal: {senders.external}", flush=True)
    corpus = load_corpus(arguments.corpus)
    plan = plan_rehearsal(corpus)
    print(f"Sending {len(plan.to_send)} of {len(corpus)} corpus emails through Mail.app.")
    print("left for the eval only: " + (", ".join(plan.eval_only_ids) or "none"))
    sender = MailAppSender(senders, recipient=settings.gmail_address, runner=OsascriptRunner())
    sent = rehearse(corpus, sender, on_sent=_announce_sent)
    print(f"Sent {len(sent)} of {len(corpus)} corpus emails to {settings.gmail_address}.")
    return 0


def _announce_sent(email_id: str) -> None:
    print(f"sent {corpus_message_id(email_id)}", flush=True)
