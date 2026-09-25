# IndexScout

**Ask your agent what to work on in search, and get answers backed by exact Google Search Console
evidence.**

IndexScout is a local, read-only [MCP](https://modelcontextprotocol.io) server for Google Search
Console (GSC). It does more than return API rows: it compares equal periods, splits traffic changes
by page, query, device, country, and search type, ranks opportunities on pages you already have,
classifies cannibalization, and audits indexing. Every answer carries the periods, metrics, formulas,
and caveats an agent needs to stay honest.

```text
You:    What should I work on in search this week?
Agent:  gsc_site_snapshot → gsc_diagnose_change → gsc_find_opportunities → gsc_page_analysis
        "1. /pricing lost 412 clicks (2026-08-29..2026-09-21 vs the prior 24 days) ..."
```

- **Read-only by construction** — only the `webmasters.readonly` scope; no write or Indexing API code.
- **Local** — stdio only, no network listener, no telemetry, tokens in your OS keyring.
- **Agent-first** — every response has `summary`, `evidence`, `results`, `warnings`,
  `limitations`, `recommended_next_calls`, and `provenance`.

> IndexScout is an independent open-source project. It is not affiliated with or endorsed by Google,
> OpenAI, or Anthropic.

## Contents

[What you can ask](#what-you-can-ask) · [Example conversations](#example-conversations) ·
[Install](#install) · [OAuth setup](#oauth-setup) · [Service accounts](#service-accounts) ·
[Codex](#codex-setup) · [Claude](#claude-setup) · [Other clients](#other-mcp-clients) ·
[Tools](#tool-reference) · [Security](#security-model) · [Limitations](#google-api-limitations) ·
[Troubleshooting](#troubleshooting) · [Contributing](#contributing)

## What you can ask

| Goal | Ask | Tools the agent uses |
| --- | --- | --- |
| Decide what to work on | "What should I work on in search this week?" | `gsc_site_snapshot`, `gsc_find_opportunities` |
| Diagnose a traffic drop | "Why did organic clicks fall last week?" | `gsc_diagnose_change` |
| Check a deploy | "What changed since we shipped the new nav on Sept 2?" | `gsc_diagnose_change(start_date=...)` |
| Analyze one page | "What is happening with /pricing?" | `gsc_page_analysis` |
| Analyze one search | "How do we perform for 'moving permit cost'?" | `gsc_query_analysis` |
| Find ranking opportunities | "Which queries rank 4–20 with real impressions?" | `gsc_find_opportunities` |
| Weak CTR | "Which high-impression pages have unusually weak CTR?" | `gsc_find_opportunities(category="low_ctr")` |
| Cannibalization | "Is one query split across competing pages?" | `gsc_find_cannibalization` |
| Indexing | "Which important URLs are not indexed?" | `gsc_indexing_audit` |
| Sitemap coverage | "Are the URLs in my sitemap indexed?" | `gsc_indexing_audit(source="sitemap")` |
| Devices | "Are mobile results behaving differently from desktop?" | `gsc_diagnose_change`, `gsc_site_snapshot` |
| Portfolio | "Which properties need attention?" | `gsc_list_properties`, `gsc_site_snapshot` |
| Evidence | "What evidence supports this recommendation?" | `gsc_search_analytics` |

## Example conversations

> **All data below is fabricated for illustration.** The domain and numbers are examples.

**Weekly review**

```text
You:   What should I work on in search this week for sc-domain:example.com?

Agent: (gsc_site_snapshot days=7 → gsc_find_opportunities → gsc_page_analysis)

       Web search 2026-09-15..2026-09-21 vs 2026-09-08..2026-09-14 (final data, PT):
       clicks 8,940 (−6.2%), impressions 412,300 (+1.8%), CTR 2.2% (−0.18 pp).

       1. Review the title and SERP intent for /boston-moving-permit: 3,420 impressions,
          0.8% CTR, average position 6.2; CTR down 31% vs the prior week. The property's
          own CTR at positions 6–10 is 2.9%. Check the live results page first — a map pack
          or ads could explain it. (score 71.8, category low_ctr)
       2. /guides/moving-checklist averages position 11.4 for "moving checklist"
          (5,100 impressions). It is just below page one; review coverage and internal links.
       3. "permit cost" is shown for both /pricing and /permit-cost, and impression share
          moved 38 points to /permit-cost. Classified possible_competition — worth a content
          and intent review, not an automatic merge.

       What the data does not show: why CTR fell. GSC reports associations, not causes.
```

**Traffic drop**

```text
You:   Organic traffic fell. What happened?

Agent: (gsc_diagnose_change metric=clicks)

       Clicks fell by 1,210 (−14.8%), 2026-08-25..2026-09-21 vs 2026-07-28..2026-08-24.
       - 62% of the loss is on /pricing (−750 clicks); its average position moved 1.4 → 2.3.
       - By query+page: "pricing" → /pricing −610 clicks.
       - Devices: desktop −980, mobile −230. Countries: loss concentrated in usa.
       - Returned query rows explain 81% of the change; the rest is anonymized or truncated.
       Next I will inspect /pricing's index status (gsc_indexing_audit source=losing_pages).
       I cannot tell from GSC alone whether this is a site change, competition, or demand.
```

## Install

Requires [uv](https://docs.astral.sh/uv/). IndexScout runs on Python 3.11+ (uv installs it if needed).

```bash
# Put the `indexscout` command on your PATH
uv tool install git+https://github.com/jpow18/indexscout@v0.1.0

# Or run without installing
uvx --from git+https://github.com/jpow18/indexscout@v0.1.0 indexscout doctor
```

IndexScout is not on PyPI yet, so `uvx indexscout` does not work until it is published; use the
`--from git+...` form above.

Development install:

```bash
git clone https://github.com/jpow18/indexscout && cd indexscout
uv sync
uv run indexscout doctor
```

Then set up OAuth (below), and run:

```bash
indexscout auth login        # opens a browser once
indexscout doctor            # checks deps, token storage, scope, API reachability, MCP
indexscout properties        # lists exact property identifiers
indexscout snapshot sc-domain:example.com --days 28
```

## OAuth setup

Desktop OAuth is the recommended default. You create your own OAuth client, so no third party ever
holds your token.

1. In the [Google Cloud console](https://console.cloud.google.com/), create or select a project.
2. Enable the **Google Search Console API** (APIs & Services → Library).
3. Configure the **OAuth consent screen** (Google Auth Platform): user type *External* is fine for a
   personal Google account. Add your Google account as a test user. Add the scope
   `https://www.googleapis.com/auth/webmasters.readonly`.
4. Create credentials: **OAuth client ID → Desktop app**. Download the JSON.
5. Save it as `client_secret.json` in the IndexScout config directory, or point
   `INDEXSCOUT_CLIENT_SECRETS` at it:
   - Linux: `~/.config/indexscout/client_secret.json`
   - macOS: `~/Library/Application Support/indexscout/client_secret.json`
   - Windows: `%LOCALAPPDATA%\indexscout\indexscout\client_secret.json`
6. Run `indexscout auth login` and approve **read-only** access in the browser.

The refresh token goes to your OS keyring (Secret Service, macOS Keychain, or Windows Credential
Locker). Without a usable keyring, IndexScout writes a `0600` token file and prints a warning.
Access tokens refresh automatically in memory.

> While the OAuth app is in *Testing*, Google expires refresh tokens after 7 days. Run
> `indexscout auth login` again, or publish the app to *In production* for your own use.

`indexscout auth logout` deletes the stored token. Revoke access at
<https://myaccount.google.com/permissions>.

## Service accounts

For unattended automation (CI, scheduled reports):

1. Create a service account in Google Cloud and download a JSON key. Keep the key outside any
   repository.
2. Enable the Search Console API in that project.
3. In Search Console, open each property → **Settings → Users and permissions → Add user**, enter
   the service account's email, and choose **Restricted** (read access is enough).
4. Set `INDEXSCOUT_SERVICE_ACCOUNT_FILE=/path/to/key.json`. IndexScout requests only the read-only
   scope. The service account sees only properties you added it to.

## Configuration

| Variable | Purpose |
| --- | --- |
| `INDEXSCOUT_ALLOWED_PROPERTIES` | Comma-separated exact identifiers (`sc-domain:example.com,https://www.example.com/`). Everything else is rejected, even if the account can access it. |
| `INDEXSCOUT_CLIENT_SECRETS` | Path to the Desktop OAuth client JSON. |
| `INDEXSCOUT_SERVICE_ACCOUNT_FILE` | Use a service account instead of OAuth. |
| `INDEXSCOUT_TOKEN_STORE` | `auto` (default), `keyring`, or `file`. |
| `INDEXSCOUT_CONFIG_DIR` | Override the config directory. |

See [`.env.example`](https://github.com/jpow18/indexscout/blob/main/.env.example). No `.env` file is required.

## Codex setup

**Plugin (MCP server + skill):**

```bash
codex plugin marketplace add jpow18/indexscout
codex plugin add indexscout@indexscout
```

**MCP server only:**

```bash
codex mcp add indexscout -- uvx --from git+https://github.com/jpow18/indexscout@v0.1.0 indexscout serve
```

Copy [`skills/indexscout`](https://github.com/jpow18/indexscout/blob/main/skills/indexscout) to `~/.codex/skills/` if you want the skill without
the plugin.

## Claude setup

**Claude Code plugin (MCP server + skill):**

```bash
claude plugin marketplace add jpow18/indexscout
claude plugin install indexscout@indexscout
```

**Claude Code MCP only:**

```bash
claude mcp add --scope user indexscout -- uvx --from git+https://github.com/jpow18/indexscout@v0.1.0 indexscout serve
```

**Claude Desktop:** download `indexscout.mcpb` from the
[latest release](https://github.com/jpow18/indexscout/releases/latest) and open it. Run
`indexscout auth login` in a terminal first. You can build the bundle yourself with
`scripts/build_mcpb.sh`.

## Other MCP clients

Any stdio MCP client works ([`examples/mcp.json`](https://github.com/jpow18/indexscout/blob/main/examples/mcp.json)):

```json
{
  "mcpServers": {
    "indexscout": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/jpow18/indexscout@v0.1.0", "indexscout", "serve"]
    }
  }
}
```

## Agent workflow

The server sends instructions at initialization, every tool description says when to use it, and
the bundled [`indexscout` skill](https://github.com/jpow18/indexscout/blob/main/skills/indexscout/SKILL.md) teaches the workflows: weekly review,
traffic-loss diagnosis, page evaluation, opportunity finding, cannibalization, indexing audits, and
how to avoid unsupported SEO conclusions.

A typical chain for "What should I work on?":

1. `gsc_capabilities` → finds the property (only if unknown)
2. `gsc_site_snapshot` → totals, gains, losses, device and search-type changes
3. `gsc_diagnose_change` → only if clicks or impressions fell ≥ 10%
4. `gsc_find_opportunities` → ranked, scored items with evidence
5. `gsc_page_analysis` → validates the top items

Each step returns `recommended_next_calls` with ready-to-use arguments.

## Tool reference

All tools are read-only and return the same envelope:

```jsonc
{
  "summary": "...",                 // one or two sentences with exact numbers and periods
  "evidence": ["..."],              // metric statements that support the summary
  "results": {},                    // structured, bounded data
  "warnings": ["..."],              // truncation, incomplete data, partial failures
  "limitations": ["..."],           // what this data cannot show
  "recommended_next_calls": [{"tool": "...", "arguments": {}, "reason": "..."}],
  "provenance": {}                  // property, inclusive PT date ranges, data state, dimensions,
                                    // filters, rows returned, truncation, formulas, units
}
```

| Tool | Answers | Notes |
| --- | --- | --- |
| `gsc_capabilities` | What can IndexScout do here? | Auth status, scopes, allowlist, properties, workflows, limits, first call. No secrets. |
| `gsc_list_properties` | Which properties exist? | Exact identifier, permission level, allowed by local policy. |
| `gsc_search_analytics` | Give me the rows. | Explicit dates, 6 dimensions, 6 search types, all filter operators, `final`/`all`, `startRow` pagination, ≤ 1,000 rows per call. |
| `gsc_site_snapshot` | How is this property doing? | Equal-period totals and deltas, top gaining/losing pages and queries, devices, search types, data-quality warnings. |
| `gsc_diagnose_change` | Why did clicks/impressions change? | Decomposes by page, query, query+page, device, country, search type. New and absent keys. Never claims cause. |
| `gsc_find_opportunities` | What should I work on? | Positions 4–20, low CTR, near page one / top 3, impressions rising without clicks. Documented score. |
| `gsc_page_analysis` | What is happening with this page? | Trend, exact-page queries, gains/losses, devices, appearances, position distribution, shared queries, optional inspection. |
| `gsc_query_analysis` | How do we do for this search? | Pages, trend, device/country, competing pages, new/growing/declining/stable label. |
| `gsc_find_cannibalization` | Is a query split across pages? | `possible_competition`, `likely_intent_split`, or `review`, with rules in provenance. |
| `gsc_inspect_url` | Is this URL indexed? | Verdict, coverage, crawl, canonicals, robots, fetch, referrers, sitemaps. Indexed version, not live. |
| `gsc_indexing_audit` | Which important URLs have problems? | ≤ 50 URLs from a list, top pages, losing pages, or live sitemap files (URLs with no impressions first); grouped; partial failures kept. |
| `gsc_list_sitemaps` | Are sitemaps healthy? | Submission/download dates, pending, errors, warnings, content counts. |

### Opportunity scoring

```text
potential_clicks = impressions × max(0, target_ctr − current_ctr)
score            = potential_clicks × (1 + trend_adjustment)
```

`target_ctr` is the property's own impression-weighted CTR for the target position bucket (1, 2, 3,
4–5, 6–10, 11–20, 21+). Striking-distance pairs target the next better bucket; low-CTR pairs target
their own bucket. If a bucket has fewer than 1,000 impressions, a conservative default curve is used
and labeled `default_curve`. `trend_adjustment` is +0.2 when impressions grew ≥ 20% versus the
baseline, −0.2 when they fell ≥ 20%, else 0. The score ranks work; it is not a traffic forecast.

### Data rules IndexScout applies

- Equal-length periods; unequal ones need `allow_unequal_periods=true`.
- Dates are inclusive calendar dates in America/Los_Angeles, as GSC defines them.
- Incomplete dates are excluded by default using Google's `firstIncompleteDate`.
- Aggregate CTR = clicks ÷ impressions; aggregate position is impression-weighted.
- Queries are attributed to pages only from query+page rows.
- Keys missing from returned rows are reported as absent, not zero.

## Security model

- Requests only `webmasters.readonly`. No site, sitemap, or URL submission code; no Indexing API.
- stdio transport only; no network listener (OAuth login uses a one-time localhost redirect).
- Refresh token in the OS keyring, or an atomic `0600` file with a warning; access tokens in memory.
- Tokens, codes, client secrets, API responses, queries, URLs, and property data are never logged.
- Optional exact property allowlist; URL Inspection targets must belong to the property.
- The only non-Google request is a GET of a sitemap file inside the property (for sitemap audits).
  Redirects must stay inside the property; files are size-limited; XML with DTDs is rejected.
- GSC strings are sanitized, quoted, and labeled untrusted; agents are told never to follow them.

Details: [SECURITY.md](https://github.com/jpow18/indexscout/blob/main/SECURITY.md) and [docs/threat-model.md](https://github.com/jpow18/indexscout/blob/main/docs/threat-model.md).

## Google API limitations

- Final data usually lags 2–3 days; the newest dates stay incomplete until finalized.
- About 16 months of history.
- Anonymized (rare) queries are never returned, so query rows do not sum to totals.
- At most 25,000 rows per request, sorted by clicks, and a limited number of rows per day;
  IndexScout fetches one bounded page per analysis and flags possible truncation.
- Discover and Google News have no query dimension and no position.
- URL Inspection shows Google's indexed version, not a live test, and allows about 2,000
  inspections per property per day (600 per minute).
- The API does not list the URLs inside a sitemap. IndexScout reads the live sitemap files from your
  site instead, so the audit uses the current file, which can differ from the version Google last read.
- Quota errors (HTTP 429) usually clear after about 15 minutes.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| "Not authenticated" | Run `indexscout auth login` in a terminal. MCP servers cannot open a browser for you. |
| "OAuth client file not found" | Save the Desktop client JSON to the path shown, or set `INDEXSCOUT_CLIENT_SECRETS`. |
| "Google rejected the stored refresh token" | The token expired (Testing apps: 7 days) or was revoked. Log in again. |
| 403 on a property | The account lacks access, or the identifier is wrong. Use `indexscout properties`. |
| "not in INDEXSCOUT_ALLOWED_PROPERTIES" | Add the exact identifier to the allowlist, or remove the variable. |
| "does not belong to property" | Inspect URLs covered by the property: same scheme/host/port for URL-prefix properties. |
| Keyring warning on Linux | Install and unlock a Secret Service provider (GNOME Keyring or KWallet), or accept the `0600` file. |
| Server not showing in a client | Run `indexscout doctor`, then check the client's MCP logs. `uv` must be on the client's PATH. |

## Contributing

See [CONTRIBUTING.md](https://github.com/jpow18/indexscout/blob/main/CONTRIBUTING.md). Tests use a fake Search Console, so no credentials are
needed. Please keep IndexScout read-only and evidence-first.

## Acknowledgements

The tool coverage was informed by [AminForou/mcp-gsc](https://github.com/AminForou/mcp-gsc) (MIT),
used only as a behavioral reference. IndexScout is an independent implementation and shares no code
with it.

## License

[MIT](https://github.com/jpow18/indexscout/blob/main/LICENSE) © 2026 James Pow
