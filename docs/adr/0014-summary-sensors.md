# 0014: Summary sensors for shares, an exception to "no energy sensors"

- Status: accepted
- Date: 2026-11-22
- Amends: ADR 0003 ("No sensor entities for the energy values")

## Context

- Core dashboard cards read statistics but can't divide one statistic by another. Shares such as "how much of the feed-in went to the communities" or "how much of the PV was used at home" are the questions members ask, and they can't be shown with the statistics alone (issue #51).
- Community data are complete only up to *Data complete until*, about two weeks back, while meter values and the user's live sources (#50) run ahead. A share only means something when numerator and denominator cover the same hours.
- ADR 0003 kept energy values out of entity states, because sensor states compiled by the recorder would double the external statistics and the values arrive late.

## Decision

- The integration adds **summary sensors** with a percentage state, computed from the recorder's hourly statistics (the `eg_austria:` statistics and the comparison sources) over the same complete hours.
- Two periods for every value: **month so far** (from the 1st of the current month up to *Data complete until*) and **last complete month** (the latest calendar month whose days are all complete).
- Values: share of the feed-in that went to the communities and share of the import covered by them (site and per community); with a PV source, the PV split (self-consumed, to communities, to the surplus buyer); with a feed-in or grid consumption source, the difference between the metered value and that source.
- The sensors have **no `state_class`**, so the recorder compiles no long-term statistics from them and nothing is counted twice. kWh values are attributes (`numerator_kwh`, `denominator_kwh`) next to `period_start` and `period_end`; there are still no kWh sensor states.
- A value is unknown when its period has no complete day or its denominator is 0 (ADR 0005: unknown is not zero).
- Values are recomputed at startup and after every run, once the recorder has written the run's statistics.
- A diagnostic **Meter data until** date sensor shows the last day with meter values for every hour, next to *Data complete until* (community side).

## Consequences

- Tile and gauge cards and automations can show shares; the generated dashboard (#52) uses them.
- More entities per entry (two per value and period); sensors for sources that aren't set are not created.
- kWh totals for cards keep coming from the statistics (statistic and statistics-graph cards), not from these sensors.
