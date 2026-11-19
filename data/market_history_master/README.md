# Master market-history index

This directory is a verified, de-duplicated index of history archives stored on the market-history-checkpoints and market-history-checkpoints-v3 branches.

- index.json: eligible unique ordinary shares and funds, excluding suffix-3 after-hours instruments.
- eligible_codes.json: successful instrument codes, suitable as the exclusion list for the next incremental fetch.
- after_hours_suffix3_excluded.json: suffix-3-only instruments excluded from analysis.
- duplicates.json: repeated instrument codes found across source batches/runs.
- failures.json: failed instruments retained for retry.
- summary.json: counts and archive integrity checks.

Raw and adjusted daily archives remain unchanged in their original checkpoint branches.
