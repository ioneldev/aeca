# AECA privacy policy

AECA, the Agentic Email & Calendar Assistant, is a single-user assistant built as a hiring assessment. It runs only on the machine of the person who operates it, against the one Google account that person authorises.

## What it accesses

- **Gmail:** it reads new messages in the Inbox, applies labels, archives messages, moves them to Spam, stars them, and sends replies and approval requests from the account.
- **Google Calendar:** it reads events and the calendar's time zone, and creates events that the account's owner has approved by email.

## Where the data goes

- The text and headers of each new email are sent to Anthropic's API to be classified.
- AECA keeps an audit log, a trace and a small database on the operator's machine. Email bodies are not stored in the audit log.
- Nothing is sold, shared with anyone else, or used for advertising.

## Removing access

You can revoke AECA's access at any time at https://myaccount.google.com/permissions. Stopping AECA and deleting its Docker volume removes everything it stored.

## Contact

Write to the support email shown on AECA's consent screen.
