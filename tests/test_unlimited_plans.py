import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database as db


class UnlimitedPlanTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db_patch = patch.object(db, 'DB_FILE', str(Path(temp.name) / 'test.db'))
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        db.init_db()

    def test_three_unlimited_plans(self):
        plans = db.get_vpn_plans()
        self.assertEqual([(p['gb'], p['price'], p.get('limit_ip')) for p in plans],
                         [(0, 150, 1), (0, 250, 2), (0, 350, 3)])

    def test_receipt_keeps_purchased_ip_limit_after_plan_edit(self):
        db.get_or_create_user(42, 'alice', 'Alice', '')
        key = db.get_vpn_plans()[1]['plan_key']
        payment_id = db.save_payment_request(42, 'Unlimited', 'receipt', amount=250,
                                            plan_key=key, plan_gb=0)
        with sqlite3.connect(db.DB_FILE) as conn:
            conn.execute('UPDATE vpn_plans SET limit_ip = 3 WHERE plan_key = ?', (key,))
        record = db.get_payment_record(payment_id)
        self.assertEqual(record['plan_limit_ip'], 2)
        self.assertEqual(record['plan_gb'], 0)

    def test_unlimited_renewal_updates_local_quota(self):
        db.get_or_create_user(42, 'alice', 'Alice', '')
        db.save_new_config(42, 'alice', 'uuid', 10)
        db.update_config_total_gb('alice', 42, 0, unlimited=True)
        self.assertEqual(db.get_user_configs(42)[0][3], 0)
