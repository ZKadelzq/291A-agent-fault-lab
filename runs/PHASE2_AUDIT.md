# Phase 2 complete-matrix audit

Model: `api-muse-glimmer-30b`. All three runs use six tasks, six methods, ample cash, and matching agent configuration.

| Scenario | Episodes | Safe success by method (none / restart / verify / selective / change_aware / reconcile) |
| --- | ---: | --- |
| committed | 300 | 20/48 / 36/48 / 42/48 / 36/48 / 38/48 / 38/48 |
| not-committed | 48 | 6/6 / 6/6 / 6/6 / 6/6 / 6/6 / 6/6 |
| unknown | 48 | 0/6 / 0/6 / 0/6 / 0/6 / 0/6 / 0/6 |

## Main matrix: paired recovery cost

Pairs are the same task and fault. Total tokens include the faulty run plus recovery work; a lower cost is useful only alongside safe success.

| Method | Safe success | Mean total tokens | Mean tokens saved vs restart | Reconstructed / fallback / stopped |
| --- | ---: | ---: | ---: | --- |
| none | 20/48 | 24846 | 24227 | 0 / 0 / 0 |
| restart | 36/48 | 49073 | 0 | 42 / 0 / 6 |
| verify | 42/48 | 35266 | 13807 | 0 / 0 / 0 |
| selective | 36/48 | 45845 | 3228 | 41 / 1 / 6 |
| change_aware | 38/48 | 38603 | 10470 | 42 / 4 / 2 |
| reconcile | 38/48 | 27975 | 21098 | 48 / 0 / 0 |

## Interpretation limits

- Each task/fault cell is one model run. Counts describe this run, not a confidence interval.
- `verify` is preventive and does not inspect executor effects; its run is separate from the shared faulty baseline.
- Irreversible external effects remain in `safe_success` after account reconstruction.
- `R-stale` and `R-incorrect` both flip the decision-relevant rating in this benchmark; the stale fault additionally changes the source date and price.
