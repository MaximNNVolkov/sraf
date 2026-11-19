import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from research.retrotest.backend import Replay, create_run
from research.retrotest.common import MSK, digest, file_hash, iso, timestamp, write_json
from research.retrotest.dataset import SCHEMA, Dataset, insert
from research.retrotest.versioning import model_snapshot

ORIGIN = '2025-01-06'


def fixture(path):
    path.mkdir()
    db=sqlite3.connect(path / 'candles.sqlite')
    db.executescript(SCHEMA)
    start=datetime(2023,11,1,tzinfo=MSK)
    end=datetime(2025,1,8,tzinfo=MSK)
    at=start
    while at < end:
        for tf in (1440,10080):
            if tf==1440 or at.weekday()==0:
                insert(db,tf,at,100,102,98,100,10,'fixture')
        for hour in range(24):
            t=at+timedelta(hours=hour)
            insert(db,60,t,100,102,98,100,10,'fixture')
            if hour%4==0:
                insert(db,240,t,100,102,98,100,10,'fixture')
        at+=timedelta(days=1)
    # Fine bars cover two complete simulated trading days; no intrabar fabrication.
    at=datetime(2025,1,6,tzinfo=MSK)
    while at < end:
        insert(db,1,at,100,101,99,100,1,'fixture')
        if at.minute%15==0:
            insert(db,15,at,100,101,99,100,15,'fixture')
        at+=timedelta(minutes=1)
    db.commit();db.close()
    write_json(path / 'quality.json',{'fixture':True})
    write_json(path / 'manifest.json',{
        'period_start':'2023-01-01','period_end_inclusive':'2026-06-01',
        'data_end':iso(end),'instrument_metadata':{'minPriceIncrement':{'units':0,'nano':10000000}},
        'database_sha256':file_hash(path/'candles.sqlite'),'quality_sha256':file_hash(path/'quality.json'),
        'quality_status':'SYNTHETIC_TEST_FIXTURE'})


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.dataset=self.root/'dataset'
        fixture(self.dataset)
        self.results=self.root/'results'
        self.info=create_run(self.dataset,ORIGIN,results_root=self.results)
        self.path=Path(self.info['path'])
        self.replay=Replay(self.path)

    def tearDown(self):
        self.replay.close()
        self.tmp.cleanup()

    def submit(self, action='WAIT', **kw):
        context=self.replay.context()
        return self.replay.submit(dict(context_id=context['context_id'],action=action,reason='test decision',**kw))

    def plan(self):
        return self.submit('PLAN',direction='LONG',entry=100,stop=95,tp1=105,tp2=110,
                           entry_condition='H1 breakout and separate retest')

    def confirm(self):
        self.plan()
        self.replay.advance()
        ctx=self.replay.context()
        return self.submit('CONFIRM_ENTRY',evidence=[{'begin':ctx['h1']['last_closed_candle']['begin'],'reason':'agent verified breakout and retest'}])

    def test_context_contains_no_unfinished_or_future_bars(self):
        ctx=self.replay.context()
        cutoff=timestamp(ctx['source_cutoff'])
        self.assertEqual(cutoff.astimezone(MSK).hour,8)
        for rows in ctx['candles'].values():
            self.assertTrue(all(timestamp(r['end'])<=cutoff for r in rows))
        self.assertFalse(ctx['new_h1_candles'])
        self.assertTrue(ctx['simulation'])
        self.assertEqual(ctx['strategy_version'],'v0.1001')
        self.assertEqual(ctx['historical_trading_status'],'UNKNOWN')

    def test_dataset_is_read_only(self):
        with self.assertRaises(sqlite3.OperationalError):
            self.replay.dataset.db.execute('DELETE FROM candles')

    def test_plan_does_not_open_a_trade(self):
        self.plan()
        state=self.replay.advance()['state']
        self.assertIsNone(state['position'])
        self.assertIsNone(state['pending_entry'])
        self.assertTrue(state['plan'])

    def test_entry_is_pending_until_clock_reaches_lagged_close(self):
        response=self.confirm()
        state=response['state']
        self.assertIsNone(state['position'])
        self.assertEqual(timestamp(state['pending_entry']['entry_at']).astimezone(MSK).strftime('%H:%M'),'10:15')
        advanced=self.replay.advance()
        entry=next(e for e in advanced['events'] if e['type']=='ENTRY')
        self.assertEqual(entry['price'],100)
        self.assertEqual(timestamp(entry['at']).astimezone(MSK).strftime('%H:%M'),'10:15')
        self.assertEqual(advanced['state']['position']['entry_price'],100)

    def test_decision_required_before_advance(self):
        with self.assertRaises(ValueError):self.replay.advance()

    def test_context_and_decision_cannot_be_reused(self):
        ctx=self.replay.context()
        decision=dict(context_id=ctx['context_id'],action='WAIT',reason='test')
        self.replay.submit(decision)
        with self.assertRaises(ValueError):self.replay.submit(decision)
        self.replay.advance()
        with self.assertRaises(ValueError):self.replay.submit(decision)

    def test_confirm_at_morning_or_without_plan_is_rejected(self):
        with self.assertRaises(ValueError):self.submit('CONFIRM_ENTRY',evidence=[{'begin':ORIGIN,'reason':'test'}])

    def test_evidence_cannot_reference_future(self):
        self.plan();self.replay.advance()
        with self.assertRaises(ValueError):
            self.submit('CONFIRM_ENTRY',evidence=[{'begin':'2025-01-07T12:00:00+03:00','reason':'future'}])

    def test_no_trade_requires_review_condition(self):
        with self.assertRaises(ValueError):self.submit('NO_TRADE')
        self.submit('NO_TRADE',entry_condition='D1 closes above 105')
        self.assertIsNone(self.replay.state['plan'])

    def test_monitoring_adds_two_h1_bars(self):
        self.submit()
        response=self.replay.advance()
        self.assertEqual(len(response['context']['new_h1_candles']),2)

    def test_twenty_to_next_morning_keeps_pending_plan(self):
        self.plan()
        self.replay.advance()
        for _ in range(5):
            self.submit();self.replay.advance()
        self.assertEqual(timestamp(self.replay.state['cursor']).astimezone(MSK).hour,20)
        self.submit();response=self.replay.advance()
        self.assertEqual(timestamp(response['state']['cursor']).astimezone(MSK).strftime('%Y-%m-%d %H:%M'),'2025-01-07 08:00')
        self.assertIsNotNone(response['state']['plan'])

    def test_finish_open_position_is_unresolved_not_loss(self):
        self.confirm();self.replay.advance()
        result=self.replay.finish()
        self.assertEqual(result['phase'],'FINISHED')
        self.assertEqual(result['trades'][0]['status'],'OPEN_AT_DATA_END')
        self.assertIsNone(result['trades'][0]['gross_pct'])
        self.assertEqual(result['losses'],0)
        self.assertEqual(result['unresolved'],1)
        with self.assertRaises(ValueError):self.replay.advance()

    def test_state_tampering_detected(self):
        self.replay.db.execute("UPDATE state SET payload=replace(payload,'RUNNING','FINISHED')")
        self.replay.db.commit()
        with self.assertRaisesRegex(ValueError,'audit'):
            Replay(self.path)

    def test_audit_tampering_detected(self):
        self.replay.db.execute("UPDATE events SET payload='{}' WHERE kind='RUN_CREATED'")
        self.replay.db.commit()
        with self.assertRaisesRegex(ValueError,'chain'):
            Replay(self.path)

    def test_modified_dataset_rejected(self):
        ds=self.dataset/'candles.sqlite'
        with ds.open('ab') as f:f.write(b'tamper')
        with self.assertRaisesRegex(ValueError,'hash mismatch'):
            Dataset(self.dataset)

    def test_model_version_locks_results(self):
        lock=self.results/'v0.1001'/'model.json'
        value=json.loads(lock.read_text());value['sha256']='wrong';write_json(lock,value)
        with self.assertRaisesRegex(ValueError,'bump'):
            create_run(self.dataset,ORIGIN,results_root=self.results)

    def test_results_preserve_frozen_model_and_version_directory(self):
        self.assertEqual(self.path.parent.name,'v0.1001')
        manifest=json.loads((self.path/'manifest.json').read_text())
        self.assertEqual(manifest['model']['sha256'],model_snapshot('v0.1001')['sha256'])
        result=self.replay.results()
        self.assertIsNone(result['net_pct'])
        self.assertTrue((self.path/'results.json').exists())

    def test_missing_lag_bar_cancels_entry(self):
        self.confirm()
        self.replay.dataset.entry_bar=lambda _:None
        result=self.replay.advance()
        self.assertIn('CANCEL_ENTRY_NO_BAR',[e['type'] for e in result['events']])
        self.assertIsNone(result['state']['position'])

    def test_invalid_delayed_price_cancels_entry(self):
        self.confirm()
        original=self.replay.dataset.entry_bar
        self.replay.dataset.entry_bar=lambda at:dict(original(at),close=106)
        result=self.replay.advance()
        self.assertIn('CANCEL_ENTRY_INVALID_LEVELS',[e['type'] for e in result['events']])
        self.assertIsNone(result['state']['position'])

    def test_extremes_before_delayed_entry_do_not_stop_position(self):
        self.confirm()
        original=self.replay.dataset.execution
        def execution(after,until):
            early={'begin':'2025-01-06T07:14:00+00:00','end':'2025-01-06T07:15:00+00:00',
                   'open':100,'high':120,'low':90,'close':100}
            return [early,*original(after,until)]
        self.replay.dataset.execution=execution
        result=self.replay.advance()
        self.assertEqual(result['state']['position']['status'],'ACTIVE')
        self.assertFalse(result['state']['trades'])

    def test_minute_conflict_prevents_fabricated_result(self):
        self.confirm();self.replay.advance()
        self.replay.dataset.conflicts_between=lambda after,until:[{'reason':'test conflict'}]
        self.submit()
        result=self.replay.advance()
        self.assertEqual(result['state']['phase'],'UNRESOLVED')
        self.assertEqual(result['state']['trades'][0]['status'],'DATA_CONFLICT')
        self.assertIsNone(result['state']['trades'][0]['gross_pct'])
        self.assertIsNone(result['context'])

    def test_minute_replay_records_two_exits_and_weighted_result(self):
        self.confirm();self.replay.advance()
        def execution(after,until):
            return [dict(begin='2025-01-06T09:01:00+00:00',end='2025-01-06T09:02:00+00:00',open=101,high=106,low=101,close=105),
                    dict(begin='2025-01-06T09:02:00+00:00',end='2025-01-06T09:03:00+00:00',open=106,high=111,low=106,close=110)]
        self.replay.dataset.execution=execution
        self.submit();response=self.replay.advance()
        trade=response['state']['trades'][0]
        self.assertEqual(trade['outcome'],'WIN')
        self.assertEqual([e['fraction'] for e in trade['exits']],[0.5,0.5])
        self.assertAlmostEqual(trade['gross_pct'],7.5)
        self.assertEqual(trade['entry_price'],100)
        self.assertEqual(trade['exit_price'],110)
