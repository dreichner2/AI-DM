---
name: Campaign-pack authoring
about: Track campaign-pack content, import, checkpoint, or director issues
title: "[Campaign Pack]: "
labels: campaign-pack
---

## Pack identity

- Pack ID/title:
- Pack version/schema version:
- Source filename or installed-pack ID:
- AIDM commit/RC:

## Content area

- [ ] Schema/manifest
- [ ] Bundled example or Play Now catalog
- [ ] Import, dry run, or installed-pack reuse
- [ ] Checkpoints, progress, or shared-session propagation
- [ ] Hidden/player-visible content
- [ ] Bestiary, encounters, or combat outcomes
- [ ] Director rules, commentary, or off-track behavior
- [ ] Authoring tools, report, graph, or forge

## Problem

Describe the authored intent, actual behavior, and whether the issue blocks
import or appears as a linter warning.

## Reproduction and sanitized evidence

```bash
PACK=/absolute/path/to/pack.json
.venv/bin/python scripts/aidm_pack.py lint "$PACK"
.venv/bin/python scripts/aidm_pack.py report "$PACK"
```

- Command/API result:
- Stable error or warning code:
- Relevant record/checkpoint IDs:

## Acceptance criteria

- [ ] Schema and runtime import behavior agree
- [ ] Linter or fixture regression added
- [ ] Dry-run/import behavior verified
- [ ] Player visibility checked for hidden content
- [ ] Progress/event behavior checked when relevant
- [ ] `docs/campaign_packs.md` updated when the contract changed
