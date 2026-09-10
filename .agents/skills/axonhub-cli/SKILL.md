---
name: axonhub-cli
description: Ad-hoc AxonHub operations over its GraphQL API via graphql-cli — queries, mutations, schema discovery, and endpoint management for channels, API keys, users, projects, and system settings. Use for one-off CLI work and generic tool mechanics; this repository's governed writes (model-plan, mapping table, channel sync regex) belong to the axonhub-admin skill.
---

# AxonHub CLI

Operate AxonHub through its GraphQL API using `graphql-cli`.

## Prerequisites

- `curl` and `jq` available on the system
- AxonHub instance running (default: `http://localhost:8090`)

Run graphql-cli via npx — no installation required (`-y` skips the
first-run install prompt):

```bash
npx -y @axonhub/graphql-cli <command>
```

`go install github.com/looplj/graphql-cli@latest` also builds the binary
from source; that path is not verified in this repository — prefer npx.

## Workflows

### 1. Obtain a token

**Option A — Paste manually:**
```bash
AXONHUB_TOKEN="<paste-your-jwt-token>"
```

**Option B — Login via environment variables + curl:**
```bash
export AXONHUB_EMAIL="admin@example.com"
export AXONHUB_PASSWORD="your-password"
```

```bash
AXONHUB_URL="http://localhost:8090"
AXONHUB_TOKEN=$(curl -s -X POST "${AXONHUB_URL}/admin/auth/signin" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"${AXONHUB_EMAIL}\",\"password\":\"${AXONHUB_PASSWORD}\"}" \
  | jq -r '.token')
```

Verify:
```bash
[ "$AXONHUB_TOKEN" != "null" ] && [ -n "$AXONHUB_TOKEN" ] && echo "✅ Login successful" || echo "❌ Login failed"
```

Token expires after 7 days; re-run this workflow to refresh.

`AXONHUB_TOKEN` (this skill, signin-fetched) and `AXONHUB_JWT` (the
`axonhub-admin` skill's browser-session token for governed writes) are
different mechanisms and not interchangeable.

### 2. Configure endpoint

```bash
AXONHUB_URL="http://localhost:8090"
npx -y @axonhub/graphql-cli endpoint add axonhub --url "${AXONHUB_URL}/admin/graphql" --description "AxonHub GraphQL API"
npx -y @axonhub/graphql-cli endpoint login axonhub --type token --token "${AXONHUB_TOKEN}"
```

Verify:
```bash
npx -y @axonhub/graphql-cli endpoint list --detail
```

### 3. Explore the schema

```bash
# List all queries
npx -y @axonhub/graphql-cli find -e axonhub --query

# List all mutations
npx -y @axonhub/graphql-cli find -e axonhub --mutation

# Search by keyword (names only)
npx -y @axonhub/graphql-cli find channel -e axonhub

# Show full definitions with fields and arguments
npx -y @axonhub/graphql-cli find channel -e axonhub --detail

# Find input types needed for a mutation
npx -y @axonhub/graphql-cli find CreateChannel -e axonhub --input --detail
```

### 4. Execute a query

```bash
npx -y @axonhub/graphql-cli query '<graphql-query>' -e axonhub
npx -y @axonhub/graphql-cli query '{ me { id email firstName lastName isOwner scopes } }' -e axonhub
npx -y @axonhub/graphql-cli query '{ systemStatus { isInitialized } systemVersion { version commit uptime } }' -e axonhub
npx -y @axonhub/graphql-cli query '{ queryChannels(input: { first: 20 }) { edges { node { id name type status supportedModels } } } }' -e axonhub
npx -y @axonhub/graphql-cli query '{ models(input: {}) { id } }' -e axonhub
```

### 5. Execute a mutation

```bash
npx -y @axonhub/graphql-cli mutate '<graphql-mutation>' -e axonhub
npx -y @axonhub/graphql-cli mutate 'mutation { testChannel(input: { channelID: "1" }) { success latency message error } }' -e axonhub
npx -y @axonhub/graphql-cli mutate 'mutation { updateChannelStatus(id: "1", status: enabled) { id name status } }' -e axonhub
npx -y @axonhub/graphql-cli mutate 'mutation { createChannel(input: { type: openai, name: "my-channel", baseURL: "https://api.openai.com", credentials: { apiKey: "sk-xxx" }, supportedModels: ["gpt-4o"], defaultTestModel: "gpt-4o" }) { id name status } }' -e axonhub
```

Object-valued inputs: GraphQL input-object syntax inline is valid, but JSON
must go through variables — `-v '{"input": …}'` (verified from the CLI's
help: `-v, --variables` — mutation variables as JSON string). A JSON object
pasted inline as a string is invalid GraphQL (`Expected Name, found String`).

### 6. Handle authentication errors (401)

Re-authenticate by re-running Workflow 1 (Option B) for a fresh token, then:

```bash
# Update the endpoint with the new token
npx -y @axonhub/graphql-cli endpoint login axonhub --type token --token "${AXONHUB_TOKEN}"

# Verify the new token works
npx -y @axonhub/graphql-cli query '{ me { id email } }' -e axonhub
```

## Common patterns

### Pipe output to jq
```bash
npx -y @axonhub/graphql-cli query '{ queryChannels(input: { first: 100 }) { edges { node { id name status } } } }' -e axonhub 2>/dev/null | jq '.queryChannels.edges[].node'
```

### Update an endpoint
```bash
npx -y @axonhub/graphql-cli endpoint update axonhub --url "${AXONHUB_URL}/admin/graphql" --header "Authorization=Bearer ${AXONHUB_TOKEN}"
```

## Guidelines

- **Always use `find` without `--detail` first** to get an overview of matching names, then use `find --detail` on specific results to see full definitions with fields and arguments. This avoids overwhelming output when schemas are large.
- The GraphQL endpoint requires JWT authentication — always complete Workflows 1 and 2 before querying.
- For complex queries, use `npx -y @axonhub/graphql-cli find <type> -e axonhub --input --detail` to discover required input fields.
- Pass JSON object values as variables (`-v`), never as inline JSON string literals.
- Governed writes — this repository's model-plan, mapping-table, and channel sync regex writes — run only through `axonhub-admin`'s interactive program; this skill is for one-off operations.
