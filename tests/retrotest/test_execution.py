import unittest

from research.retrotest.execution import new_position, process_bar


def bar(o=100,h=102,l=99,c=101):
    return {'begin':'2025-01-06T09:16:00+00:00','end':'2025-01-06T09:17:00+00:00',
            'open':o,'high':h,'low':l,'close':c}


def position(direction='LONG'):
    long = direction == 'LONG'
    return new_position({'direction':direction,'stop':95 if long else 105,
                         'tp1':105 if long else 95,'tp2':110 if long else 90},100,'2025-01-06T09:15:00+00:00')


class ExecutionTests(unittest.TestCase):
    def test_delayed_price_must_still_fit_levels(self):
        self.assertIsNone(new_position({'direction':'LONG','stop':95,'tp1':105,'tp2':110},106,'t'))

    def test_long_stop_touch_inside_minute(self):
        p = position()
        event = process_bar(p,bar(h=101,l=94))[0]
        self.assertEqual((event['type'],event['price']),('STOP',95))
        self.assertEqual(p['outcome'],'LOSS')
        self.assertAlmostEqual(p['gross_pct'],-5)
        self.assertEqual(event['time_precision'],'within_1m_bar')

    def test_short_stop(self):
        p = position('SHORT')
        process_bar(p,bar(h=106,l=99))
        self.assertEqual(p['exit_price'],105)
        self.assertAlmostEqual(p['gross_pct'],-5)

    def test_tp1_half_and_breakeven_stop(self):
        p = position()
        process_bar(p,bar(o=101,h=106,l=101))
        self.assertEqual(p['remaining'],0.5)
        self.assertEqual(p['stop'],100)
        self.assertEqual(p['status'],'ACTIVE')
        process_bar(p,bar(o=104,h=104,l=99))
        self.assertEqual(p['outcome'],'WIN')
        self.assertAlmostEqual(p['gross_pct'],2.5)
        self.assertEqual([e['price'] for e in p['exits']],[105,100])

    def test_short_tp1_then_tp2(self):
        p = position('SHORT')
        process_bar(p,bar(o=99,h=99,l=94,c=95))
        process_bar(p,bar(o=94,h=96,l=89,c=90))
        self.assertEqual(p['outcome'],'WIN')
        self.assertAlmostEqual(p['gross_pct'],7.5)
        self.assertEqual(p['weighted_exit_price'],92.5)

    def test_tp1_tp2_in_one_bar_without_be_stop_touch(self):
        p = position()
        process_bar(p,bar(o=101,h=111,l=101,c=110))
        self.assertEqual([e['type'] for e in p['exits']],['TP1','TP2'])
        self.assertAlmostEqual(p['gross_pct'],7.5)

    def test_original_stop_and_tp1_ambiguous(self):
        p = position()
        process_bar(p,bar(h=106,l=94))
        self.assertEqual(p['status'],'AMBIGUOUS')
        self.assertIsNone(p['gross_pct'])
        self.assertFalse(p['exits'])

    def test_new_stop_after_tp1_in_same_bar_ambiguous(self):
        p = position()
        process_bar(p,bar(h=106,l=99))
        self.assertEqual(p['status'],'AMBIGUOUS')
        self.assertEqual(p['exits'][0]['type'],'TP1')
        self.assertIsNone(p['gross_pct'])

    def test_gap_stop_fills_at_open(self):
        p = position()
        process_bar(p,bar(o=90,h=92,l=89))
        self.assertEqual(p['exit_price'],90)
        self.assertAlmostEqual(p['gross_pct'],-10)
        self.assertEqual(p['exits'][0]['time_precision'],'bar_open')

    def test_gap_over_both_targets(self):
        p = position()
        process_bar(p,bar(o=115,h=116,l=99))
        self.assertEqual(p['status'],'CLOSED')
        self.assertEqual([e['type'] for e in p['exits']],['TP1','TP2'])
        self.assertAlmostEqual(p['gross_pct'],7.5)

    def test_stop_tp2_ambiguous_after_partial(self):
        p = position()
        process_bar(p,bar(o=101,h=106,l=101))
        process_bar(p,bar(o=104,h=111,l=99))
        self.assertEqual(p['status'],'AMBIGUOUS')
        self.assertIsNone(p['gross_pct'])

    def test_closed_position_ignores_later_bars(self):
        p = position()
        process_bar(p,bar(h=101,l=94))
        self.assertEqual(process_bar(p,bar(h=120,l=90)),[])
        self.assertEqual(len(p['exits']),1)
