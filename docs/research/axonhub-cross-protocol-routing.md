# AxonHub cross-protocol routing evidence

## Question

Can AxonHub map a Claude/GPT request to an arbitrary target model across API
protocols, and does each candidate need a channel association?

## First-party findings

1. AxonHub explicitly supports any SDK calling models exposed through another
   provider protocol. Its official README describes OpenAI clients calling
   Claude and Anthropic clients calling GPT through automatic request/response
   translation. Source: [AxonHub README](https://github.com/looplj/axonhub#readme).

2. The official request pipeline is:

   ```text
   API Key Profile rename
   → Model Association selects channel/model
   → Channel-level rename
   → upstream request
   ```

   Sources: [Model Management](https://github.com/looplj/axonhub/blob/unstable/docs/en/guides/model-management.md),
   [Channel Management](https://github.com/looplj/axonhub/blob/unstable/docs/en/guides/channel-management.md).

3. A `model` association is a global specific-model match: it searches enabled
   channels that support the target model without binding the rule to one
   channel. A `channel_model` association is the precise form that binds both
   channel and upstream model. Source: [Model Association Types](https://github.com/looplj/axonhub/blob/unstable/docs/en/guides/model-management.md#model-association-types).

4. A channel accepts direct model IDs only when they are present in its
   `supportedModels`; channel mappings are evaluated later and their target
   must also be supported. Source: [Channel Model Mapping](https://github.com/looplj/axonhub/blob/unstable/docs/en/guides/channel-management.md#model-renaming).

5. A `type=model` association directly scans every channel's model entries for
   the target ID. The target does not need its own `channel_model` association;
   only `channel_model` rules bind a specific channel ID. First-party source:
   [`matchModel` and `matchChannelModel`](https://github.com/looplj/axonhub/blob/4483c2e4c685f27f37ddd666b3ff3bb48bebea50/internal/server/biz/model_association_matcher.go),
   [`GetModelEntries`](https://github.com/looplj/axonhub/blob/4483c2e4c685f27f37ddd666b3ff3bb48bebea50/internal/server/biz/channel_llm.go),
   and [`TestMatchAssociations_Deduplication`](https://github.com/looplj/axonhub/blob/4483c2e4c685f27f37ddd666b3ff3bb48bebea50/internal/server/biz/model_association_matcher_test.go).

## Read-only production evidence (2026-08-30)

The migrated SQLite history was queried as user `klein` without reading request
content or credentials. Completed executions prove inbound/outbound protocol
conversion is active:

| Requested | Executed | Channel | Inbound | Outbound | Completed |
|---|---|---|---|---|---:|
| `gpt-5.6-sol` | `glm-5.2` | `opencode-go` | OpenAI Responses | Chat Completions | 4 |
| `kimi-k3` | `kimi-k3` | `opencode-go` | Anthropic Messages | Chat Completions | 70 |
| `muse-spark-1.2-contributor` | same | `op-responses` | Anthropic Messages | OpenAI Responses | 63 |
| `qwen3.7-plus` | same | `op-anthropic` | OpenAI Responses | Anthropic Messages | 19 |

## Design consequence

Inbound request protocol does not constrain the target channel. The managed
channel still must use the upstream format required by that OpenCode model.
Therefore:

- request aliases can use one global `type=model` association to the chosen
  candidate;
- profile templates can rename the same request IDs to the same candidates;
- catalog sync should keep each candidate in the protocol-derived managed
  channel's `supportedModels`; it does not need to create candidate
  `channel_model` associations;
- request aliases use global `type=model` associations, while profile templates
  rely on the mapped candidate being a managed, enabled model. The current
  `fallback_to_channels_on_model_not_found` system setting is enabled and must
  be checked before applying template changes.

The source checkout used for verification was `looplj/axonhub` unstable commit
`4483c2e4c685f27f37ddd666b3ff3bb48bebea50`. The following first-party suites
passed:

```text
go test ./internal/server/biz ./internal/server/orchestrator
cd llm && go test ./transformer/openai/... ./transformer/anthropic/...
```
