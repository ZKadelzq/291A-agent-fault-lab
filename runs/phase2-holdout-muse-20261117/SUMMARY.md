# Independent evaluation and tracking replay

Muse Glimmer 30B on Nautilus. Four frozen within-family tasks, three fault types, five methods, three repetitions: 180 faulty and 24 clean rows. Recovery comparisons share a faulty trace. Verify is preventative and sampled separately.

| Method | Correct | Completed and safe | Mean E2E tokens | Paired savings vs restart |
| --- | ---: | ---: | ---: | ---: |
| none | 0/36 | 0/36 | 24,693 | — |
| verify | 24/36 | 24/36 | 36,879 | — |
| restart | 36/36 | 36/36 | 50,190 | 0.0% |
| selective | 36/36 | 36/36 | 40,953 | 18.4% |
| change_aware | 36/36 | 36/36 | 37,478 | 25.3% |

## Recovery by fault

| Fault | No recovery | Verify | Restart | Selective | Change-aware |
| --- | ---: | ---: | ---: | ---: | ---: |
| R-incorrect | 0/12 | 12/12 | 12/12 | 12/12 | 12/12 |
| P-stop | 0/12 | 12/12 | 12/12 | 12/12 | 12/12 |
| E-timeout | 0/12 | 0/12 | 12/12 | 12/12 | 12/12 |

Clean safe completion was none 12/12, verify 12/12.

Change-aware cost was lower on 36/36 pairs; it rescued 36 initially unsafe exposures. Savings in repeats 1–3 were 25.6%, 25.1%, 25.3%.

Six pilot observations with unregistered clients were retained separately and excluded before running the replacement split. H5–H8 share the development simulator and tool schema.

## Tracking contrast

Medians over 180 paired blocks per mode. Inputs are predecoded. The reference is the recorded runtime graph.

| Mode | CPU (µs) | Paired increment (µs) | P95 increment (µs) | Peak Python KiB | Missed of 62 edges |
| --- | ---: | ---: | ---: | ---: | ---: |
| off | 1.8 | 0.0 | 0.0 | 0.21 | 62 |
| message | 25.3 | 23.4 | 28.1 | 1.47 | 15 |
| raw | 171.6 | 169.9 | 285.4 | 2.68 | 0 |
| checkpoint | 326.9 | 325.1 | 532.2 | 3.35 | 0 |

[Frozen protocol](../../evaluation/PROTOCOL.md) · [Checked aggregates](AUDIT.json) · [Raw replay blocks](tracking.json) · [Artifact checksums](SHA256SUMS.json) · [Course PDF](../../docs/CSE291A-Project5-Final.pdf)
