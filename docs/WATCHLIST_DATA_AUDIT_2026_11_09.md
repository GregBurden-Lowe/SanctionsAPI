# PostgreSQL watchlist audit — 9 November 2026

Read-only production inspection of 33,652 sanctions rows and comparison with the source XML. Counts are a point-in-time diagnostic, not a completeness or accuracy certification.

- 152 EU person rows had raw XML embedded in their DOB field. The revised EU parser distinguishes location-only entries from dates, retains the location, and preserves genuinely ambiguous dates as unknown. It also handles EU month and bounded year-range attributes. Publication requires a new source refresh.
- The UK source included `00/00/1975`, which was imported as invalid `1975-00-00`; the corrected importer keeps the known year. An incomplete year such as `15/08/19yy` remains unknown.
- The UN source contained 32 `BETWEEN` DOB entries: 27 had both year bounds and no qualifying note. The revised parser retains those as ranges; five qualified or incomplete entries remain unknown. Empty DOB elements also remain unknown.
- OFAC's current SDN XML contained 4,431 alias entries marked `weak` out of 24,663 aliases. The current importer retains the alias names but drops this source quality classification. This needs a separate provenance-preserving design, labelled tests and Compliance review; do not simply remove weak aliases from screening.
- Three UN DOB entries with date/year values also had notes; the present import drops those notes. Examples include a date derived from a false passport and a month-only note. Preserve and present source notes in a follow-up rather than inferring unsupported dates.
- Some people have no source DOB or country. In this snapshot that included 591 EU, 707 UK, 71 OFAC SDN and 2 UN person entries with no DOB. Missing source data is not evidence of a conflicting identity.
- No malformed alias cells, duplicate source IDs, blank names or NULL raw evidence were found. 1,602 EU entries have non-Latin primary names that normalise to empty, but all retained at least one searchable indexed name. These checks do not prove retrieval completeness.

After the release, verify source manifests and representative rows in PostgreSQL and Parquet. Existing run snapshots remain historical; they only change through a new re-screen. Automatic reconciliation is held until an admin resumes it.
