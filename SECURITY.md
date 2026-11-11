# Security policy

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use GitHub's private vulnerability reporting ("Report a vulnerability" on the repository's Security tab) once the repository is public. We aim to acknowledge reports within seven days.

## What counts

- Anything that lets a user's API keys or other secrets leak, in logs, recorded test fixtures, dossiers or the ledger.
- Anything that lets text fetched from a third-party source (news, filings, transcripts) act as instructions to a language model or a tool.
- Anything that lets a ledger record be edited, removed or back-dated without `foxcast verify-ledger` detecting it.
- A dependency compromise affecting a released version.

## Supply chain

Releases install from a hash-pinned lock file. New dependency releases are only taken after a seven-day waiting period.
