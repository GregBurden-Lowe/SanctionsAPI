"""Stand-in for the worker's PostgreSQL check, for tests about job handling rather than matching."""
from screening_policy import _merge_dual_type_results, check_plan
from source_assurance import assess


def postgres_job(matcher, manifest):
    def run(url, *, name, dob, country, entity_type, requestor=None, log_search=False, require_assurance=False):
        results = [matcher(**item, requestor=requestor, log_search=False) for item in check_plan(name, dob, country, entity_type)]
        merged = _merge_dual_type_results(*results, name=name, submitted_entity_type=entity_type)
        merged['Source Coverage'] = assess(manifest)
        return merged
    return run
