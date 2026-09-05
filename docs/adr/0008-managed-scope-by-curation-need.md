---
status: accepted
---

# Managed scope is defined by curation need, not by channel enumeration

Which channels this project manages changes over time, so no decision text
owns a fixed channel list. A channel is **managed** when its
`supportedModels` must be maintained by this project as an explicit
allowlist — because the upstream model list (e.g. `/v1/models`) reports the
full catalog, which is not the same as the models the account is entitled to
use. Mere write authority does not make a channel managed.

The entitlement fact source is per channel: the OpenCode `go.mdx` page for
the opencode channel, the GOAT plan page for the commandcode channel. The
current managed set is data, not vocabulary: it lives in
`config/model-decisions.json` as a provider→channel mapping plus the managed
templates. Scripts default to it, and the CLI may only select within it.

Under this definition the managed set is `opencode-go` and `commandcode`;
`op-responses` and `op-anthropic` leave the managed scope and are treated
like any other channel — read-only, used only for influence analysis. This
supersedes the managed-scope paragraphs of ADR 0005; the protocol partition
they served was already retired by ADR 0007.

`models-mapping`'s scoring responsibility is candidate selection for mapping
(`data/formula.md`). Channels carry no routing priority in this design, and
quota tags are display metadata. No `/v1/models` watch channel is added: the
models.dev `api.json` snapshot already provides the multi-provider catalog
keylessly, and the entitlement pages carry the new-model signal. Revisit only
if an entitlement page is found lagging the upstream `/v1/models`.

## Considered options

- **Enumerating managed channels in the glossary**: rejected — the list
  changes often and would force constant glossary churn.
- **Defining managed by write authority alone**: rejected — it conflates "we
  may write" with "someone must curate"; only the latter justifies the work.
- **A `/v1/models` collection channel**: deferred — useful only to detect
  upstream additions ahead of the entitlement pages.

## Consequences

- `config/model-decisions.json` `scope` gains the provider→channel mapping
  shape; scripts consume it as the managed boundary.
- README, SECURITY.md, and the skill docs stop naming `op-responses` /
  `op-anthropic` as owned channels.
- The glossary defines Managed channel and Entitlement generically; the
  concrete channel list lives only in config.
