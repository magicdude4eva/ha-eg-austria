# 0001: Custom integration, not an add-on or REST sensors

- Status: accepted
- Date: 2026-11-07

## Context

Members want EG Austria portal data (metered consumption and generation, community shares, residual grid import/export, later money) in Home Assistant. Existing community approaches use REST sensors, curl scripts or external databases (forum 1140). Portal data arrives one or more days late and is corrected weeks or months later. The Energy dashboard and statistics cards read long-term statistics, and only an integration running inside Home Assistant can write statistics for past hours.

Options:

1. REST/command-line sensors: state is "now"; they can't backdate, so late data lands in the wrong hour, and corrections are lost.
2. Add-on: runs outside Core; can only reach statistics through the WebSocket/REST API or MQTT, adds a container and an install path most users don't have (Container/Core installs have no add-ons).
3. Custom integration (`custom_components/eg_austria`), installable through the community store by repository link.

## Decision

Build a custom integration `eg_austria` with a UI config flow. No YAML configuration. Target the Home Assistant version current at first release; follow the integration quality scale where practical (config flow, reauth, diagnostics, repairs, translations en/de, tests).

## Consequences

- Statistics can be written for the actual hour and rewritten on corrections (ADR 0003).
- Works on every installation type, not only Home Assistant OS.
- We maintain Python code against an unofficial API that changes (ADR 0006 for how breakage is contained).
- Publishing in the default store list is a later, separate step (ADR 0010).
