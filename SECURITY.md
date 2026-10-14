# Security policy

## Supported versions

Fixes go into the latest release only; there are no backports.

| Version | Supported |
|---|---|
| 0.5.x | yes |
| older | no |

## Reporting a vulnerability

Report it privately through GitHub:
[Security > Report a vulnerability](https://github.com/Jezue/fitatu_library/security/advisories/new).
Do not open a public issue. Say which version you used, what an attacker can do and how to
reproduce it. This is a spare-time project: you get an answer when a maintainer has read it, and
a fix and an advisory once it is understood.

In scope is this library, including `fitatu-api` and `fitatu-mcp`: how it stores and sends
tokens (the session file, its 0600 mode, the hosts that get credentials), what it logs, what the
agent tools let a model do, and the release pipeline. A flaw in Fitatu's own service or API
belongs to Fitatu, not here.

## Never share your session

**Never paste `session.json`, a bearer or refresh token, a password or an `Authorization` header**
into an issue, a pull request, a report, a chat with an assistant or a log you share. The session
file is full access to your Fitatu account. Fitatu does not rotate refresh tokens, so one that
leaks keeps working; we have not checked whether changing the password revokes it. If you posted
one, delete it, change your Fitatu password and log in again with `fitatu-api login`.

Error results and DEBUG logs hold no tokens, but they show your user id, dates, search phrases
and food names. Replace those with neutral values before you post them.
