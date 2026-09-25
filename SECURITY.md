# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through
[GitHub security advisories](https://github.com/jpow18/indexscout/security/advisories/new).
Do not open a public issue. Do not include real tokens, client secrets, or Search Console data in a
report; describe the class of problem and a minimal reproduction with placeholder values.

Expect an acknowledgement within 7 days. Fixes ship as a patch release with a changelog entry.

## Supported versions

Only the latest release receives security fixes.

## Security model (summary)

- **Read-only by construction.** IndexScout requests only
  `https://www.googleapis.com/auth/webmasters.readonly`. It calls four read methods
  (`sites.list`, `searchanalytics.query`, `urlInspection.index.inspect`, `sitemaps.list`). There is
  no code for adding or deleting sites, submitting or deleting sitemaps, or the Indexing API. Tests
  enforce this.
- **Local only.** The MCP server speaks stdio. It opens no network listener. `indexscout auth login`
  binds a one-time loopback listener on `localhost` for the OAuth redirect and closes it.
- **Token storage.** Only the refresh token and OAuth client identity are stored, in the OS keyring
  when one is usable. Otherwise IndexScout writes an atomic `0600` file in a `0700` directory and
  warns. Access tokens stay in memory. A file lock guards writes.
- **No token deletion on transient failures.** A failed refresh reports an error; it never deletes
  the stored token.
- **Redaction.** Logs go to stderr at WARNING level through a redacting filter that removes
  token-like values and drops tracebacks. IndexScout never logs API responses, queries, URLs, or
  property data.
- **Property allowlist.** `INDEXSCOUT_ALLOWED_PROPERTIES` limits access to exact property identifiers,
  even when the Google account can access more. URL Inspection targets must belong to the selected
  property.
- **Untrusted data.** Search Console strings are sanitized (control and bidirectional characters
  removed, length bounded), quoted in generated prose, and labeled untrusted in every response.

See [docs/threat-model.md](docs/threat-model.md) for the full threat model.
