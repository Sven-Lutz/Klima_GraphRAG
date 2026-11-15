"""Synthetic pipeline and budget contracts, never municipal Gold judgments."""
from research.experiments.ds006.continuity import run
from research.experiments.ds006.e0b import dry_run


def test_real_pipeline_loss_and_generic_unfiltered_control():
    result=run(repetitions=1)
    rows={(r['variant'],r['fixture']):r for r in result['matrix']}
    for name in ['short_heat','context','no_keyword_table']:
        assert rows['A',name]['stored']
        assert not rows['A',name]['admitted']
        assert rows['B',name]['chunk_present']
        assert rows['B',name]['lineage_preserved']
    assert rows['A','navigation']['stored']
    assert rows['A','navigation']['chunk_present'] is None
    assert rows['B','sports']['chunk_present']  # recall control also admits noise
    assert rows['B','foreign_seed_inheritance']['wrong_subject']
    assert rows['B','ambiguous_actor']['subject_unresolved']
    assert rows['B','foreign_correct']['subject_correct']
    assert not rows['B','foreign_correct']['target_scoped_hit']
    assert rows['B','foreign_seed_inheritance']['target_scoped_hit']


def test_held_out_failure_modes_are_not_hidden():
    result=run(repetitions=1,split='held_out')
    rows={(r['variant'],r['fixture']):r for r in result['matrix']}
    for name in ['held_compact','held_units','held_negative_keyword']:
        assert not rows['A',name]['chunk_present']
        assert rows['B',name]['lineage_preserved']
    # The later generic parser repair preserves short numeric fields. Keep this
    # as a continuity regression; historical DS-006 loss reports stay frozen.
    assert rows['B','held_short_parser']['first_loss'] is None
    assert rows['B','held_short_parser']['lineage_preserved']
    assert rows['B','held_large_table']['admitted']
    assert not rows['B','held_large_table']['chunk_present']
    assert rows['B','held_noise']['chunk_present']


def test_e0b_same_inputs_seeds_and_hard_request_budget():
    result=dry_run(budget=8)
    assert result['live_http_requests']==0
    assert len(result['results'])==4
    for i in (0,2):
        a,b=result['results'][i:i+2]
        assert a['seed']==b['seed']
        assert a['input_manifest']==b['input_manifest']
        assert a['budget']==b['budget']==8
        assert a['requests']<=8 and b['requests']<=8
        assert [t['task_id'] for t in a['tasks']]==[t['task_id'] for t in b['tasks']]
        assert all(p['raw_hash'] for p in a['provenance']+b['provenance'])


def test_tiny_budget_stops_both_strategies_without_hidden_fetches():
    result=dry_run(budget=1)
    assert all(r['requests']==1 for r in result['results'])
    assert all(r['fixture_document_recall']==0 for r in result['results'])


def test_unfiltered_mode_preserves_scores_and_limit(tmp_path):
    from crawler.core.engine import _segment_features
    from crawler.core.models import FetchResult, Segment
    from crawler.core.storage import Storage
    from crawler.scripts.chunk_documents import process_segments, setup_db

    with Storage(tmp_path/'db.sqlite', raw_dir=tmp_path/'raw', create=True) as store:
        doc=store.store_raw('SYN-A','https://fixture.test/',
                           FetchResult('https://fixture.test/',200,'text/html',b'fixture',{}))
        store.store_segments_scored(doc,[Segment(0,'paragraph','Kontakt Klima'),
                                         Segment(1,'paragraph','42 MWh')],_segment_features)
        setup_db(store.conn)
        before=[tuple(r) for r in store.conn.execute(
            'SELECT segment_id, impact_score, is_negative FROM segments ORDER BY segment_id')]
        assert process_segments(store.conn)==(0,0)
        assert process_segments(store.conn,retain_all=True,limit=1)==(1,1)
        assert before==[tuple(r) for r in store.conn.execute(
            'SELECT segment_id, impact_score, is_negative FROM segments ORDER BY segment_id')]
        assert store.conn.execute('SELECT count(*) FROM chunks').fetchone()[0]==1
        assert not store.conn.execute(
            'SELECT 1 FROM chunks c JOIN segments s ON c.segment_id=s.segment_id '
            'WHERE c.impact_score != s.impact_score').fetchone()
