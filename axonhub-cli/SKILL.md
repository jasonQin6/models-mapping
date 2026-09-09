---
name: axonhub-cli
description: "Manages AxonHub via its GraphQL API using graphql-cli. Use when asked to query, manage, or operate AxonHub resources (channels, API keys, users, projects, system settings) from the command line."
license: MIT
compatibility: Requires graphql-cli binary (Go) built and available in PATH
metadata:
  author: looplj
  version: "1.0"
---

# AxonHub CLI

Operate AxonHub through its GraphQL API using `graphql-cli`.

## Capabilities

- Query and manage AxonHub channels (AI model providers)
- Manage API keys and users
- Configure projects and system settings
- Test channel connectivity
- Monitor system status and version

## Prerequisites

- `curl` and `jq` available on the system
- AxonHub instance running (default: `http://localhost:8090`)

Run graphql-cli with npx (no installation required):

```bash
npx @axonhub/graphql-cli <command>
```

Or build from source:

```bash
go install github.com/looplj/graphql-cli@latest
```

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

Token expires after 7 days. Re-run this step to refresh.

### 2. Configure endpoint

```bash
AXONHUB_URL="http://localhost:8090"
npx @axonhub/graphql-cli endpoint add axonhub --url "${AXONHUB_URL}/admin/graphql" --description "AxonHub GraphQL API"
npx @axonhub/graphql-cli endpoint login axonhub --type token --token "${AXONHUB_TOKEN}"
```

Verify:
```bash
npx @axonhub/graphql-cli endpoint list --detail
```

### 3. Explore the schema

```bash
# List all queries
npx @axonhub/graphql-cli find -e axonhub --query

# List all mutations
npx @axonhub/graphql-cli find -e axonhub --mutation

# Search by keyword (names only)
npx @axonhub/graphql-cli find channel -e axonhub

# Show full definitions with fields and arguments
npx @axonhub/graphql-cli find channel -e axonhub --detail

# Find input types needed for a mutation
npx @axonhub/graphql-cli find CreateChannel -e axonhub --input --detail
```

### 4. Execute a query

```bash
npx @axonhub/graphql-cli query '<graphql-query>' -e axonhub
npx @axonhub/graphql-cli query '{ me { id email firstName lastName isOwner scopes } }' -e axonhub
npx @axonhub/graphql-cli query '{ systemStatus { isInitialized } systemVersion { version commit uptime } }' -e axonhub
npx @axonhub/graphql-cli query '{ queryChannels(input: { first: 20 }) { edges { node { id name type status supportedModels } } } }' -e axonhub
npx @axonhub/graphql-cli query '{ models(input: {}) { id } }' -e axonhub
```

### 5. Execute a mutation

```bash
npx @axonhub/graphql-cli mutate '<graphql-mutation>' -e axonhub
npx @axonhub/graphql-cli mutate 'mutation { testChannel(input: { channelID: "1" }) { success latency message error } }' -e axonhub
npx @axonhub/graphql-cli mutate 'mutation { updateChannelStatus(id: "1", status: enabled) { id name status } }' -e axonhub
```

### 6. Handle authentication errors (401)

If you encounter a 401 Unauthorized error, re-authenticate using your Email and Password:

```bash
# Set your credentials
export AXONHUB_EMAIL="admin@example.com"
export AXONHUB_PASSWORD="your-password"

# Obtain a new token
AXONHUB_URL="http://localhost:8090"
AXONHUB_TOKEN=$(curl -s -X POST "${AXONHUB_URL}/admin/auth/signin" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"${AXONHUB_EMAIL}\",\"password\":\"${AXONHUB_PASSWORD}\"}" \
  | jq -r '.token')

# Update the endpoint with the new token
npx @axonhub/graphql-cli endpoint login axonhub --type token --token "${AXONHUB_TOKEN}"

# Verify the new token works
npx @axonhub/graphql-cli query '{ me { id email } }' -e axonhub
```

## Common patterns

### Explore before querying
```bash
# Find what queries are available
npx @axonhub/graphql-cli find -e axonhub --query

# See full definition with fields and arguments
npx @axonhub/graphql-cli find channel -e axonhub --query --detail

# Find the input types needed
npx @axonhub/graphql-cli find CreateChannel -e axonhub --input --detail

# Then execute
npx @axonhub/graphql-cli mutate 'mutation { createChannel(input: { type: openai, name: "my-channel", baseURL: "https://api.openai.com", credentials: { apiKey: "sk-xxx" }, supportedModels: ["gpt-4o"], defaultTestModel: "gpt-4o" }) { id name status } }' -e axonhub
```

### Pipe output to jq
```bash
npx @axonhub/graphql-cli query '{ queryChannels(input: { first: 100 }) { edges { node { id name status } } } }' -e axonhub 2>/dev/null | jq '.queryChannels.edges[].node'
```

### Update an endpoint
```bash
npx @axonhub/graphql-cli endpoint update axonhub --url "${AXONHUB_URL}/admin/graphql" --header "Authorization=Bearer ${AXONHUB_TOKEN}"
```

## Guidelines

- **Always use `find` without `--detail` first** to get an overview of matching names, then use `find --detail` on specific results to see full definitions with fields and arguments. This avoids overwhelming output when schemas are large.
- The GraphQL endpoint requires JWT authentication — always complete workflow 1 and 2 before querying.
- For complex queries, use `npx @axonhub/graphql-cli find <type> -e axonhub --input --detail` to discover required input fields.
