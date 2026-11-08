"""Day-1 spikes against the real AECA account: one subcommand per item in spec §11.

Throwaway evidence, excluded from ruff and mypy. It prints findings only: ids, label ids, header
names and the few header values a finding is about. Never a token, a secret or an email body.

Everything it sends has a subject starting "[AECA spike]" and the header X-AECA-Test: spike, and
every event it creates is tagged aeca_test, so `make reset-test` finds them. `cleanup` trashes the
spike's mail and cancels its events by hand.

Run: uv run --env-file .env python docs/research/spikes/day1.py <item> [options]
"""

import argparse
import base64
import os
import secrets
import sys
import time
from datetime import UTC, date, datetime, timedelta
from email.message import EmailMessage
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from aeca.adapters.google_auth import GOOGLE_SCOPES, credentials_from_settings
from aeca.settings import load_settings

SUBJECT_PREFIX = "[AECA spike]"
TEST_TAG = {"private": {"aeca_test": "true"}}
BASE32HEX = "0123456789abcdefghijklmnopqrstuv"


def services() -> tuple[Any, Any, str, Any]:
    settings = load_settings(os.environ, purpose="auth")
    creds = credentials_from_settings(settings)
    gmail = build("gmail", "v1", credentials=creds, cache_discovery=False)
    calendar = build("calendar", "v3", credentials=creds, cache_discovery=False)
    return gmail, calendar, settings.gmail_address, creds


def say(item: str, finding: str) -> None:
    print(f"[{item}] {finding}")


def http_status(error: HttpError) -> str:
    reason = error.error_details if hasattr(error, "error_details") else ""
    return f"HTTP {error.resp.status} {reason}"


def mime(to: str, subject: str, body: str, extra: dict[str, str]) -> EmailMessage:
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = f"{SUBJECT_PREFIX} {subject}"
    message["X-AECA-Test"] = "spike"
    for name, value in extra.items():
        message[name] = value
    message.set_content(body)
    return message


def raw(message: EmailMessage) -> str:
    return base64.urlsafe_b64encode(message.as_bytes()).decode()


def send_via_draft(gmail: Any, message: EmailMessage, thread_id: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"raw": raw(message)}
    if thread_id:
        body["threadId"] = thread_id
    draft = gmail.users().drafts().create(userId="me", body={"message": body}).execute()
    sent = gmail.users().drafts().send(userId="me", body={"id": draft["id"]}).execute()
    sent["draft_id"] = draft["id"]
    return sent


def headers_of(gmail: Any, message_id: str, names: list[str]) -> dict[str, list[str]]:
    message = (
        gmail.users()
        .messages()
        .get(userId="me", id=message_id, format="metadata", metadataHeaders=names)
        .execute()
    )
    found: dict[str, list[str]] = {}
    for header in message["payload"].get("headers", []):
        found.setdefault(header["name"], []).append(header["value"])
    found["_labels"] = message.get("labelIds", [])
    return found


def thread_summary(gmail: Any, thread_id: str, names: list[str]) -> list[dict[str, Any]]:
    thread = (
        gmail.users()
        .threads()
        .get(userId="me", id=thread_id, format="metadata", metadataHeaders=names)
        .execute()
    )
    rows = []
    for message in thread["messages"]:
        heads = {h["name"]: h["value"] for h in message["payload"].get("headers", [])}
        rows.append({"id": message["id"], "labels": message.get("labelIds", []), **heads})
    return rows


# --- item 2: scopes -------------------------------------------------------------------------


def item_scopes(_: argparse.Namespace) -> None:
    gmail, calendar, address, creds = services()
    say("2", f"requested scopes: {', '.join(s.rsplit('/', 1)[-1] for s in GOOGLE_SCOPES)}")
    checks = {
        "users.getProfile": lambda: gmail.users().getProfile(userId="me").execute()["emailAddress"],
        "labels.list": lambda: len(gmail.users().labels().list(userId="me").execute()["labels"]),
        "messages.list INBOX": lambda: gmail.users()
        .messages()
        .list(userId="me", labelIds=["INBOX"], maxResults=1)
        .execute()
        .get("resultSizeEstimate"),
        "drafts.list": lambda: len(gmail.users().drafts().list(userId="me").execute().get("drafts", [])),
        "calendar settings.get timezone": lambda: calendar.settings()
        .get(setting="timezone")
        .execute()["value"],
        "calendar events.list primary": lambda: len(
            calendar.events().list(calendarId="primary", maxResults=1).execute().get("items", [])
        ),
    }
    for name, check in checks.items():
        try:
            say("2", f"OK   {name}: {check()}")
        except HttpError as error:
            say("2", f"FAIL {name}: {http_status(error)}")
    say("2", f"profile matches GMAIL_ADDRESS (case-insensitive): "
        f"{gmail.users().getProfile(userId='me').execute()['emailAddress'].lower() == address.lower()}")
    granted = getattr(creds, "granted_scopes", None)
    say("2", f"granted scopes after refresh: {granted}")


# --- item 3: is a user label named SPAM reserved? -------------------------------------------


def item_spam_label(_: argparse.Namespace) -> None:
    gmail, _, _, _ = services()
    try:
        label = gmail.users().labels().create(userId="me", body={"name": "SPAM"}).execute()
        say("3", f"creating user label SPAM was ACCEPTED: id={label['id']} — deleting it again")
        gmail.users().labels().delete(userId="me", id=label["id"]).execute()
    except HttpError as error:
        say("3", f"creating user label SPAM was REFUSED: {http_status(error)}")


# --- items 5 and 7: self-sent mail, custom headers through drafts.send ----------------------


def item_self_send(_: argparse.Namespace) -> None:
    gmail, _, address, _ = services()
    aeca_id = "spike-" + secrets.token_hex(4)
    message = mime(address, "self-send check", "Spike: a message the account sends to itself.",
                   {"X-AECA-Kind": "spike", "X-AECA-Id": aeca_id})
    sent = send_via_draft(gmail, message)
    say("5", f"sent id={sent['id']} thread={sent['threadId']} aeca_id={aeca_id}")
    time.sleep(3)
    heads = headers_of(gmail, sent["id"], ["X-AECA-Kind", "X-AECA-Id", "X-AECA-Test", "Message-ID"])
    say("5", f"labels on the self-sent message: {heads['_labels']} "
        f"(SENT: {'SENT' in heads['_labels']}, INBOX: {'INBOX' in heads['_labels']})")
    say("7", f"messages.get metadata: X-AECA-Kind={heads.get('X-AECA-Kind')} "
        f"X-AECA-Id={heads.get('X-AECA-Id')} X-AECA-Test={heads.get('X-AECA-Test')}")
    rows = thread_summary(gmail, sent["threadId"], ["From", "X-AECA-Kind", "X-AECA-Id"])
    say("7", f"threads.get metadata returns: {[{k: v for k, v in r.items() if k != 'From'} for r in rows]}")
    try:
        gmail.users().drafts().get(userId="me", id=sent["draft_id"]).execute()
        say("7", "the sent draft STILL EXISTS")
    except HttpError as error:
        say("7", f"the sent draft is gone: {http_status(error)}")
    say("5", "next: reply APPROVE to this message from the AECA inbox, then run "
        f"`thread --thread {sent['threadId']}`")


# --- item 6: the inbox query that excludes AECA/processed -----------------------------------


def label_id(gmail: Any, name: str) -> str:
    for label in gmail.users().labels().list(userId="me").execute()["labels"]:
        if label["name"] == name:
            return str(label["id"])
    created = gmail.users().labels().create(userId="me", body={"name": name}).execute()
    return str(created["id"])


def listed_ids(gmail: Any, **query: Any) -> set[str]:
    response = gmail.users().messages().list(userId="me", maxResults=100, **query).execute()
    return {m["id"] for m in response.get("messages", [])}


def item_query(args: argparse.Namespace) -> None:
    gmail, _, address, _ = services()
    processed = label_id(gmail, "AECA/processed")
    message = mime(address, "query check", "Spike: labelled AECA/processed, then listed.", {})
    sent = send_via_draft(gmail, message)
    time.sleep(3)
    gmail.users().messages().modify(userId="me", id=sent["id"], body={"addLabelIds": [processed]}).execute()
    time.sleep(args.wait)
    inbox = listed_ids(gmail, labelIds=["INBOX"])
    for q in ("in:inbox -label:aeca-processed", "in:inbox -label:AECA/processed",
              "in:inbox -label:aeca/processed"):
        hits = listed_ids(gmail, q=q)
        say("6", f"q={q!r}: excludes the labelled message: {sent['id'] not in hits} "
            f"({len(hits)} hits)")
    say("6", f"labelIds=['INBOX'] lists the labelled message: {sent['id'] in inbox} ({len(inbox)} hits)")


# --- items 9 and 10: the calendar ------------------------------------------------------------


def spike_event_id() -> str:
    return "aecaspike" + "".join(secrets.choice(BASE32HEX) for _ in range(12))


def item_calendar(_: argparse.Namespace) -> None:
    _, calendar, _, _ = services()
    zone = calendar.settings().get(setting="timezone").execute()["value"]
    say("9", f"settings.get('timezone') = {zone}")
    day = date.today() + timedelta(days=20)
    event = calendar.events().insert(calendarId="primary", body={
        "summary": f"{SUBJECT_PREFIX} all-day", "start": {"date": day.isoformat()},
        "end": {"date": (day + timedelta(days=1)).isoformat()}, "extendedProperties": TEST_TAG,
    }).execute()
    say("9", f"inserted all-day event id={event['id']} transparency={event.get('transparency')}")
    start = datetime.combine(day, datetime.min.time(), UTC) - timedelta(days=1)
    listed = calendar.events().list(calendarId="primary", singleEvents=True,
                                    timeMin=start.isoformat(),
                                    timeMax=(start + timedelta(days=3)).isoformat()).execute()
    for item in listed.get("items", []):
        if item["id"] == event["id"]:
            say("9", f"events.list(singleEvents=True) returns it: start={item['start']} "
                f"transparency={item.get('transparency', '(absent = opaque)')}")
    calendar.events().delete(calendarId="primary", eventId=event["id"]).execute()
    say("9", "deleted the all-day spike event")


def item_event_ids(_: argparse.Namespace) -> None:
    _, calendar, _, _ = services()
    event_id = spike_event_id()
    events = calendar.events()

    def get(label: str) -> None:
        try:
            found = events.get(calendarId="primary", eventId=event_id).execute()
            say("10", f"get {label}: status={found.get('status')}")
        except HttpError as error:
            say("10", f"get {label}: {http_status(error)}")

    def insert(label: str) -> None:
        start = datetime.now(UTC).replace(microsecond=0) + timedelta(days=21)
        try:
            events.insert(calendarId="primary", body={
                "id": event_id, "summary": f"{SUBJECT_PREFIX} event id", "extendedProperties": TEST_TAG,
                "start": {"dateTime": start.isoformat()},
                "end": {"dateTime": (start + timedelta(minutes=30)).isoformat()},
            }).execute()
            say("10", f"insert {label}: accepted")
        except HttpError as error:
            say("10", f"insert {label}: {http_status(error)}")

    say("10", f"client-chosen id {event_id}")
    get("never created")
    insert("first")
    get("after insert")
    insert("again with the live id")
    events.delete(calendarId="primary", eventId=event_id).execute()
    get("after delete")
    insert("again with the cancelled id")
    get("after re-insert")
    try:
        events.delete(calendarId="primary", eventId=event_id).execute()
    except HttpError as error:
        say("10", f"final delete: {http_status(error)}")


# --- item 14: a draft updated in place --------------------------------------------------------


def item_draft_update(_: argparse.Namespace) -> None:
    gmail, _, address, _ = services()
    drafts = gmail.users().drafts()
    first = mime(address, "AECA lock", "copy-spike heartbeat 1", {})
    created = drafts.create(userId="me", body={"message": {"raw": raw(first)}}).execute()
    second = mime(address, "AECA lock", "copy-spike heartbeat 2", {})
    updated = drafts.update(userId="me", id=created["id"],
                            body={"id": created["id"], "message": {"raw": raw(second)}}).execute()
    say("14", f"draft id before={created['id']} after={updated['id']} "
        f"same={created['id'] == updated['id']}; message id before={created['message']['id']} "
        f"after={updated['message']['id']}")
    fetched = drafts.get(userId="me", id=created["id"], format="raw").execute()
    text = base64.urlsafe_b64decode(fetched["message"]["raw"]).decode(errors="replace")
    say("14", f"updated body readable: {'heartbeat 2' in text}")
    drafts.delete(userId="me", id=created["id"]).execute()
    say("14", "deleted the spike draft")


# --- items 4, 5 and 16: replies and threads ---------------------------------------------------


def latest_from(gmail: Any, sender: str) -> dict[str, Any]:
    found = gmail.users().messages().list(userId="me", q=f"from:{sender} in:inbox", maxResults=1).execute()
    if not found.get("messages"):
        sys.exit(f"no inbox message from {sender} yet")
    message_id = found["messages"][0]["id"]
    return gmail.users().messages().get(
        userId="me", id=message_id, format="metadata",
        metadataHeaders=["Subject", "Message-ID", "References"]).execute()


def item_reply(args: argparse.Namespace) -> None:
    gmail, _, _, _ = services()
    source = latest_from(gmail, args.sender)
    heads = {h["name"].lower(): h["value"] for h in source["payload"]["headers"]}
    references = " ".join(filter(None, [heads.get("references"), heads.get("message-id")]))
    message = mime(args.sender, f"Re: {heads.get('subject', '')}",
                   "Spike: an automatic reply in your thread. Please check it threads in Proton.",
                   {"In-Reply-To": heads.get("message-id", ""), "References": references,
                    "Auto-Submitted": "auto-replied", "X-AECA-Kind": "spike-reply",
                    "X-AECA-Id": "spike-" + secrets.token_hex(4)})
    sent = send_via_draft(gmail, message, thread_id=source["threadId"])
    say("4", f"replied to {source['id']} (thread {source['threadId']}): sent id={sent['id']} "
        f"thread={sent['threadId']} same thread={sent['threadId'] == source['threadId']}")


def item_request(args: argparse.Namespace) -> None:
    gmail, _, _, _ = services()
    aeca_id = "spike-" + secrets.token_hex(4)
    message = mime(args.to, "Approve: spike request",
                   "Spike approval request.\nTo answer, reply with APPROVE or REJECT as the first word.",
                   {"X-AECA-Kind": "approval-request", "X-AECA-Id": aeca_id})
    sent = send_via_draft(gmail, message)
    say("16", f"request to {args.to}: id={sent['id']} thread={sent['threadId']} aeca_id={aeca_id}")
    say("16", f"next: reply APPROVE, then run `thread --thread {sent['threadId']}`")


def item_thread(args: argparse.Namespace) -> None:
    gmail, _, _, _ = services()
    rows = thread_summary(gmail, args.thread, ["From", "X-AECA-Kind", "Authentication-Results"])
    say("5/16", f"thread {args.thread} has {len(rows)} message(s)")
    for row in rows:
        say("5/16", f"  id={row['id']} labels={row['labels']} From={row.get('From')} "
            f"X-AECA-Kind={row.get('X-AECA-Kind')}")
        heads = headers_of(gmail, row["id"], ["Authentication-Results"])
        for value in heads.get("Authentication-Results", []):
            say("5/16", f"    Authentication-Results: {value}")


# --- item 12: an invitation matched by its iCalUID ------------------------------------------


def item_invite(args: argparse.Namespace) -> None:
    gmail, calendar, _, _ = services()
    found = gmail.users().messages().list(userId="me", q="filename:ics OR filename:invite.ics", maxResults=5).execute()
    for ref in found.get("messages", []):
        message = gmail.users().messages().get(userId="me", id=ref["id"], format="raw").execute()
        text = base64.urlsafe_b64decode(message["raw"]).decode(errors="replace")
        uids = [line.split(":", 1)[1].strip() for line in text.splitlines() if line.startswith("UID:")]
        for uid in uids[:1]:
            events = calendar.events().list(calendarId="primary", iCalUID=uid).execute().get("items", [])
            say("12", f"message {ref['id']}: UID found; events.list(iCalUID) matches {len(events)} "
                f"event(s) {[e.get('status') for e in events]}")
    if not found.get("messages"):
        say("12", "no invitation email in the account yet")


# --- cleanup -----------------------------------------------------------------------------------


def item_cleanup(_: argparse.Namespace) -> None:
    gmail, calendar, _, _ = services()
    found = gmail.users().messages().list(userId="me", q=f'subject:"{SUBJECT_PREFIX}" -in:trash',
                                          maxResults=200).execute()
    for ref in found.get("messages", []):
        gmail.users().messages().trash(userId="me", id=ref["id"]).execute()
    say("cleanup", f"trashed {len(found.get('messages', []))} spike message(s)")
    tagged = calendar.events().list(calendarId="primary", privateExtendedProperty="aeca_test=true",
                                    showDeleted=False).execute().get("items", [])
    for event in tagged:
        if event.get("summary", "").startswith(SUBJECT_PREFIX):
            calendar.events().delete(calendarId="primary", eventId=event["id"]).execute()
    say("cleanup", f"cancelled {len(tagged)} tagged spike event(s)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="item", required=True)
    commands.add_parser("scopes").set_defaults(run=item_scopes)
    commands.add_parser("spam-label").set_defaults(run=item_spam_label)
    commands.add_parser("self-send").set_defaults(run=item_self_send)
    query = commands.add_parser("query")
    query.add_argument("--wait", type=int, default=10, help="seconds to let the search index catch up")
    query.set_defaults(run=item_query)
    commands.add_parser("calendar").set_defaults(run=item_calendar)
    commands.add_parser("event-ids").set_defaults(run=item_event_ids)
    commands.add_parser("draft-update").set_defaults(run=item_draft_update)
    reply = commands.add_parser("reply")
    reply.add_argument("--sender", required=True)
    reply.set_defaults(run=item_reply)
    request = commands.add_parser("request")
    request.add_argument("--to", required=True)
    request.set_defaults(run=item_request)
    thread = commands.add_parser("thread")
    thread.add_argument("--thread", required=True)
    thread.set_defaults(run=item_thread)
    commands.add_parser("invite").set_defaults(run=item_invite)
    commands.add_parser("cleanup").set_defaults(run=item_cleanup)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
