"""Reviewed source-record links. An empty registry never changes a decision.

Entries must be reviewed against a complete source snapshot. Each record is
bound to its source evidence hash; any source change disables the entire group.
See docs/LINKED_LISTINGS.md for the review and release procedure.
"""

REVIEWED_LINKS = ({
    'reviewed_by': 'Greg Burden-Lowe',
    'review_reference': 'CLEAR01',
    'records': {
        'eu-676': '270c4f4a953cbe3b304b3997a72feb64135693ce8b54b19f2b8ab2ef57be51db',
        'eu-659': '35670aafa8e9702f7e41e7a86e3042d9b9a9d6d753b2d4b1cde82c9fc202ca6c',
        'uk-AFG0026': '98f345e270dedc147c47e0dce8498b09df113c9cee8c72096a3b8e0e7e1bc292',
        'un-individual-TAi.026': '7f4c77bd32b864c0bb8063cf34f02a4b9005e9a0b3f34be60f23b600d9bd042e',
    },
},)


def apply_reviewed_links(evidence, query_dob, assess):
    """Apply only complete, uniquely identified, evidence-bound link groups."""
    counts = {}
    for link in REVIEWED_LINKS:
        for record_id in link.get('records', {}):
            counts[record_id] = counts.get(record_id, 0) + 1
    for link in REVIEWED_LINKS:
        records = link.get('records', {})
        if (not link.get('reviewed_by') or not link.get('review_reference')
                or len(records) < 2 or any(counts[key] != 1 for key in records)):
            continue
        members = [item for item in evidence if item.get('record_id') in records]
        if len(members) != len(records) or {m['record_id'] for m in members} != set(records):
            continue
        if any(m.get('entity_type') != 'Person' or m.get('source_evidence_hash') != records[m['record_id']]
               for m in members):
            continue
        values = [m['birth_date'] for m in members if m.get('birth_date')]
        assessment = assess(';'.join(values), query_dob) if values else 'unknown'
        for member in members:
            member['linked_listing_evidence'] = {
                'record_ids': sorted(records), 'review_reference': link['review_reference'],
                'reviewed_by': link['reviewed_by'], 'dob_assessment': assessment,
                'individual_dob_assessment': member['dob_assessment'],
                'birth_dates_by_record': {m['record_id']: m.get('birth_date') for m in members},
            }
            member['dob_assessment'] = assessment
            member['disposition'] = 'excluded' if assessment == 'conflicting' else 'potential_match'
            member['reasons'] = [reason for reason in member['reasons'] if not reason.startswith((
                'Known dates of birth', 'Date of birth', 'Full date of birth'))]
            member['reasons'].append('Reviewed linked listings were assessed together; see linked listing evidence for the source records and decision.')
