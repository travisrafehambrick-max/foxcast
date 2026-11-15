# Source archive lives outside git

Raw fetched payloads are stored content-addressed under `archive_dir`, which must sit
outside every git work tree — `SourceArchive.outside_git` refuses to run otherwise.
Citations are verified against the exact bytes fetched at ingest time (row 1c), so
committing them would put megabytes of third-party filing text in repo history; "ship
code, never data" forbids it. Only the content-free hash index may be committed.
