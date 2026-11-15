# Hash-chained ledger and content-derived IDs

The forecast record is designed so tampering is detectable and citations cannot point at
invented content. The ledger is an append-only JSONL file where each record's hash covers
its own contents and the previous record's hash (row 3a); appends are repeat-safe by
request ID, so a retried freeze returns the existing record and a rerun with different
contents is refused. Facts, passages and claims are named by content-derived canonical
IDs (`f_`, `p_`, `c_` over a SHA-256 digest of identifying fields), so the same inputs
always yield the same ID and the model can only cite an ID that code issued.
