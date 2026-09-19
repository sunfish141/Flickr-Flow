CELL = 'naea-1km:x=0:y=0'
POLICY = {'max_age_days': {'NALCMS': 3650, 'MOD44B': 1000, 'MOD13Q1': 64}, 'min_valid_fraction': .25}


def source(product='NALCMS', identity='land', start='2020-01-01T00:00:00Z', end='2021-01-01T00:00:00Z', available='2025-01-01T00:00:00Z'):
    return {'source_id': identity, 'product': product, 'version': '2020 v2' if product == 'NALCMS' else '061',
            'observation_start': start, 'observation_end': end, 'available_at': available,
            'availability_basis': 'provider-publication', 'availability_evidence': 'fixture-publication-record',
            'retrieved_at': '2026-09-08T00:00:00Z', 'revision': '1', 'qa_policy': 'conservative-vegetation-QA/v1',
            'assets': [{'sha256': 'a' * 64}]}
