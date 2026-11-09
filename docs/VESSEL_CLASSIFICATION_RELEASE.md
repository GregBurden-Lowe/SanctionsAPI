# Vessel classification change

OFAC Vessel and UK Ship entries are preserved as Vessel instead of being imported as Organization. OFAC Aircraft entries are also preserved. The existing organization search still searches these records, and evidence identifies their source type. For person submissions only, a multi-word name no longer produces a potential match on a one-word vessel name or alias alone. Complete vessel names and one-word person searches remain eligible. Other organization cross-type matches are unchanged.

This changes screening outcomes and invalidates existing matching fingerprints. It does not alter historical results or automatically clear existing alerts. Have Compliance review the rule and validate representative labelled person, organization and vessel cases, including names and aliases, before release. Coordinate the API and screening worker release.

Existing published watchlists have already lost the original OFAC/UK vessel type and cannot be reliably corrected from their current schema. A verified fresh download and publication of both Parquet and PostgreSQL sources using the updated importers is mandatory before this change can fix existing records; merely deploying the code does not reclassify them. Plan a coordinated release so screening does not run with the new rule and old source data. Verify the actual source's vessel types, candidate evidence, and both source manifests before reopening traffic. Existing records and cached decisions require a separately reviewed re-screening plan; do not treat a fresh import or deployment as a historical re-screen.

Synthetic tests do not establish production recall or false-alert rates. Preserve the full watchlists, including vessels, and investigate unknown or missing source types separately.
