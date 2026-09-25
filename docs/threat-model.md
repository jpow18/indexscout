# Threat model

## Assets

| Asset | Where it lives | Impact if exposed |
| --- | --- | --- |
| OAuth refresh token + client identity | OS keyring, or `0600` file in the user config dir | Read access to the user's Search Console data until revoked |
| Service-account key | A file the user controls (`INDEXSCOUT_SERVICE_ACCOUNT_FILE`) | Read access to properties shared with that account |
| Search Console data | In memory, returned to the local MCP client | Business-sensitive traffic and URL data |

## Trust boundaries

1. **Google API** — trusted for authenticity (TLS via the official client), not for content.
2. **The property's own website** — sitemap files are fetched from it and treated as untrusted input.
3. **MCP client / agent** — trusted to call tools; may be steered by prompt injection.
4. **Search Console strings** — untrusted. Anyone can make Google record a query such as
   "ignore previous instructions". URLs and sitemap paths can also carry hostile text.
5. **Local filesystem and keyring** — trusted as far as the OS user account is trusted.

## Threats and mitigations

| Threat | Mitigation |
| --- | --- |
| Agent (or injected text) tries to modify a property, submit sitemaps, or request indexing | No write tools or write code paths exist; only the read-only scope is requested, so Google would refuse writes anyway. Tests assert both. |
| Prompt injection through queries, URLs, or property names | Strings are sanitized and bounded; prose quotes them; responses carry an untrusted-data notice; server instructions and the skill tell agents never to follow them; `recommended_next_calls` only name known tools. |
| Agent queries a property the user did not intend | Optional exact-match allowlist enforced before any API call. |
| URL Inspection used to probe URLs outside the property | Target URL must be covered by the property (scheme, host, port, path, subdomain rules). |
| Token theft from disk | Keyring preferred; file fallback is `0600` in a `0700` directory, written atomically, with a visible warning. |
| Token corruption from concurrent MCP clients | Only the refresh token is persisted (it does not change on refresh); writes use a file lock and atomic replace. |
| Secrets leaking into logs or MCP responses | Redacting log filter, tracebacks dropped, no response or data logging, `gsc_capabilities` returns no secrets or paths. |
| Credentials committed to the repo | `.gitignore` rules, a hygiene test, and gitleaks in CI. |
| Network exposure | stdio only; no HTTP/SSE transport is wired in. |
| Server-side request forgery through sitemap URLs (GSC entries, sitemap indexes, redirects) | Fetch only http(s) URLs inside the selected property; validate every redirect target; skip off-property entries; at most 10 files. |
| Hostile sitemap XML (entity expansion, huge files, gzip bombs) | Reject any DOCTYPE/ENTITY; 10 MB download and 50 MB decompressed caps; only direct `<loc>` values are read. |
| Context flooding / denial of service against the agent | All lists bounded; raw rows capped at 1,000 per call with pagination; analyses fetch one bounded page per period and flag truncation. |
| Quota exhaustion | Bounded concurrency (4 analytics, 5 inspections); inspection audits capped at 50 URLs. |
| Supply-chain drift (e.g. an MCP SDK major release) | Major versions pinned (`mcp>=2.2,<3`); lockfile in repo; Dependabot proposes updates for review. |

## Out of scope

- A compromised local OS account or a malicious MCP client binary.
- Google-side data accuracy.
- Hosted or multi-user deployment (IndexScout is a single-user local tool).
