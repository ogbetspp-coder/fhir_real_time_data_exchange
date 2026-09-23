## What and why

<!-- One or two sentences. Link the roadmap item, foundation item or ADR this serves. -->

## Checklist (AGENTS.md)

- [ ] Synthetic data only: no client, confidential or real product text (the one exception is an
      authority's published ePI for roadmap item 3a, kept out of the fixture directories).
- [ ] Tests added or updated for every mapping or validation change.
- [ ] A change to a published contract, mapping or runtime bumps its version and adds a
      change-control record under `docs/validation/changes/`.
- [ ] Generated files regenerated and committed (`npm run contracts:check` passes).
- [ ] If the Plan check shows a destroy or replace, it is intended and the `allow-replace` label
      is applied deliberately.
- [ ] Docs updated wherever behaviour changed.
- [ ] `npm run check` passes locally (`scripts/check-all.sh` if `zone-a/` or `agent/` changed).
