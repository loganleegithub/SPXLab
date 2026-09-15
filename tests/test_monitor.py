import importlib.util
import unittest
from datetime import datetime,timezone

spec=importlib.util.spec_from_file_location('monitor_close','scripts/monitor_close.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
BASE=datetime(2026,9,15,16,tzinfo=timezone.utc).timestamp()


def cfg():
    return {'close_utc':'2026-09-15T20:00:00+00:00','reference_median':7604,
            'hysteresis_points':2,'confirmation_seconds':30,'levels':[{'value':7600,'label':'reference'}]}


def health(price,stamp=BASE):
    iso=datetime.fromtimestamp(stamp,timezone.utc).isoformat()
    return {'updated_at':iso,'connected':True,'generation':1,'types':{'1':1},
            'contracts':{'1':{'symbol':'SPX','sec_type':'IND'}},
            'fields':{'1':{'last':{'value':price,'utc':iso,'generation':1,'seq':int(stamp)}}}}


class MonitorTests(unittest.TestCase):
    def test_transition_requires_sustained_cross_and_no_repeated_alert(self):
        monitor=m.Monitor(cfg());monitor.step(health(7595),BASE,0)
        alerts=[]
        for t in range(1,40):
            _,a=monitor.step(health(7605,BASE+t),BASE+t,t);alerts+=a
        crosses=[a for a in alerts if a['kind']=='LEVEL_TRANSITION']
        self.assertEqual(len(crosses),1);self.assertEqual(crosses[0]['to'],'ABOVE')

    def test_brief_cross_and_neutral_zone_do_not_trigger(self):
        monitor=m.Monitor(cfg());monitor.step(health(7595),BASE,0)
        alerts=[]
        for t in range(1,60):
            _,a=monitor.step(health(7605 if t%20<10 else 7601,BASE+t),BASE+t,t);alerts+=a
        self.assertFalse(any(a['kind']=='LEVEL_TRANSITION' for a in alerts))

    def test_data_gap_invalidates_cross_and_alerts_once(self):
        monitor=m.Monitor(cfg());monitor.step(health(7595),BASE,0)
        alerts=[]
        for t in range(1,25):
            status,a=monitor.step({},BASE+t,t);alerts+=a
        self.assertIsNone(status['current_spx'])
        self.assertEqual(sum(a['kind']=='FEED_UNAVAILABLE' for a in alerts),1)
        _,alerts=monitor.step(health(7605,BASE+25),BASE+25,25)
        self.assertTrue(any(a['kind']=='FEED_RECOVERED' for a in alerts))
        self.assertFalse(any(a['kind']=='LEVEL_TRANSITION' for a in alerts))

    def test_polling_pause_does_not_fake_continuous_confirmation(self):
        monitor=m.Monitor(cfg());monitor.step(health(7595),BASE,0)
        monitor.step(health(7605,BASE+1),BASE+1,1)
        _,alerts=monitor.step(health(7605,BASE+40),BASE+40,40)
        self.assertTrue(any(a['kind']=='SUPERVISION_GAP' for a in alerts))
        self.assertFalse(any(a['kind']=='LEVEL_TRANSITION' for a in alerts))

    def test_close_is_not_a_feed_outage_or_official_settlement(self):
        monitor=m.Monitor(cfg());close=m.epoch(cfg()['close_utc'])
        status,alerts=monitor.step({},close,1)
        self.assertEqual(status['phase'],'CLOSE_PENDING')
        self.assertEqual(status['settlement_status'],'PENDING_OFFICIAL_PM_SETTLEMENT')
        self.assertIsNone(status['current_spx'])
        _,alerts=monitor.step({},close+20,21)
        self.assertFalse(any(a['kind']=='FEED_UNAVAILABLE' for a in alerts))


if __name__=='__main__':unittest.main()
