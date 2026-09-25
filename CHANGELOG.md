# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/) and
[Semantic Versioning](https://semver.org/).

## [0.2.0] - 2026-09-25

### Added
- `gsc_indexing_audit(source="sitemap")` reads the live sitemap files for the property (submitted
  sitemaps or `sitemap_url`), follows sitemap indexes, and inspects URLs with no impressions first.
- Published on PyPI: `uvx indexscout`. Plugin and example MCP configs now use `uvx indexscout@0.2.0 serve`.
- PyPI trusted-publishing workflow.

### Security
- Sitemap fetches are limited to URLs and redirects inside the property, capped in size, and reject
  XML with DTDs.

## [0.1.0] - 2026-09-25

### Added
- Read-only MCP server over stdio with 12 tools: `gsc_capabilities`, `gsc_list_properties`,
  `gsc_search_analytics`, `gsc_site_snapshot`, `gsc_diagnose_change`, `gsc_find_opportunities`,
  `gsc_page_analysis`, `gsc_query_analysis`, `gsc_find_cannibalization`, `gsc_inspect_url`,
  `gsc_indexing_audit`, `gsc_list_sitemaps`.
- Uniform response envelope with evidence, warnings, limitations, provenance, and
  `recommended_next_calls`.
- Equal-period comparisons, incomplete-date exclusion, impression-weighted aggregation, and
  transparent opportunity scoring and cannibalization rules.
- Desktop OAuth with the `webmasters.readonly` scope, keyring token storage with a `0600` file
  fallback, service-account support, and a property allowlist.
- CLI: `auth login|status|logout`, `properties`, `doctor`, `serve`, `snapshot`.
- `indexscout` agent skill, Claude Code and Codex plugin manifests, and a Claude Desktop `.mcpb`
  bundle.
