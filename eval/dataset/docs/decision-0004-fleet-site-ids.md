# Decision Record 0004: Fleet Site ID Scheme

Synthetic document, Larkspur Systems wiki (fictional).

Status: accepted. Date: 2025-09-08. Deciders: platform lead, hardware lead, data lead.

## Context

The fleet grew from 12 to 40 nodes in a year. Early nodes carried names chosen by the person who installed them, such as `hilltop` or `north-barn`. Names clashed, and some were reused after a node moved. Dashboards, alert labels and the flight tracker all key on the site name. A clash caused a wrong merge in one flight track.

We needed an identifier that is unique, stable when a node is replaced, and readable in an alert.

## Options considered

1. Keep free names and add a uniqueness check. Rejected, because names still carry no region and people kept inventing new styles.
2. Use random UUIDs. Rejected, because nobody can read a UUID in a page at 03:00.
3. Use a structured code with a region and a serial number.

## Decision

Site IDs use the format `LK-<region>-<nnn>`. The region is a two-letter code. The serial number is three digits, counted per region, starting at 001.

The regions are NE (northeast), SE (southeast), MW (midwest), SW (southwest) and NW (northwest). The serial number is never reused, even if the site closes. Replacing a node at a site keeps the site ID. The node serial number is a separate field.

## Consequences

- Alert labels such as `site="LK-SW-003"` tell the on-call engineer where to look without a table.
- The fleet table is the single source for the next free number per region.
- A site that moves to a new place gets a new ID, because the target bearing and the baseline SNR change.
- Old names were mapped to new IDs in a one-time migration on 2025-09-20. The tracker keeps an alias table for old flights.

## Limits

Three digits allow 999 sites per region. The fleet has 64 nodes at 58 sites today, so the scheme has room. A change to four digits would be a new decision.

## Review

We will revisit the scheme if the fleet passes 500 sites or if we add sites outside the continental region set.
