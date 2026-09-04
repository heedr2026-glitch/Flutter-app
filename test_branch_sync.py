import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
import branch_sync as sync


class SyncError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(':memory:')
        self.c.row_factory = sqlite3.Row
        self.c.execute('PRAGMA foreign_keys=ON')
        self.c.executescript('CREATE TABLE organizations(id INTEGER PRIMARY KEY); INSERT INTO organizations VALUES(1); INSERT INTO organizations VALUES(2);')
        sync.migrate(self.c)
        self.admin = {'organization_id':1,'role':'admin','permissions':'{}'}

    def tearDown(self):
        self.c.close()

    def save(self, base, data, deleted=False, user=None, branch='main', collection='vehicles'):
        with self.c:
            return sync.save_record(self.c, user or self.admin, branch, collection, 'v1',
                {'baseRevision':base, 'data':data,'deleted':deleted}, SyncError)

    def test_conflict_retry_delete_restore_history(self):
        first=self.save(0,{'name':'Original'})
        self.assertEqual(first['revision'],1)
        self.assertEqual(self.save(0,{'name':'Original'}),first)
        self.save(1,{'name':'Device A'})
        with self.assertRaises(SyncError) as caught:
            self.save(1,{'name':'Device B'})
        self.assertEqual(caught.exception.status,409)
        deleted=self.save(2,{},True)
        self.assertTrue(deleted['deleted'])
        self.assertTrue(sync.list_records(self.c,self.admin,'main','vehicles','',SyncError)['records'][0]['deleted'])
        old=sync.history(self.c,self.admin,'main','vehicles','v1',SyncError)
        self.assertEqual([r['revision'] for r in old],[3,2,1])
        restored=self.save(3,old[-1]['data'])
        self.assertEqual(restored['revision'],4)
        self.assertEqual(restored['data']['name'],'Original')

    def test_tenant_branch_and_employee_isolation(self):
        self.save(0,{'name':'Main'})
        self.save(0,{'name':'Branch'},branch='b1')
        other={**self.admin,'organization_id':2}
        self.assertEqual(sync.list_records(self.c,other,'main','vehicles','',SyncError)['records'],[])
        self.assertEqual(sync.history(self.c,other,'main','vehicles','v1',SyncError),[])
        employee={**self.admin,'role':'employee','permissions':json.dumps({'branch_id':'b1','viewVehicles':True})}
        rows=sync.list_records(self.c,employee,'b1','vehicles','',SyncError)['records']
        self.assertEqual(rows[0]['data']['name'],'Branch')
        for branch, write in [('main',False),('b1',True)]:
            with self.assertRaises(SyncError) as caught:
                sync.authorize(employee,branch,'vehicles',write,SyncError)
            self.assertEqual(caught.exception.status,403)

    def test_edit_permission_does_not_grant_delete_and_uses_existing_vehicle_permission(self):
        editor={**self.admin,'role':'employee','permissions':json.dumps({'branch_id':'main','editVehicles':True})}
        self.assertEqual(self.save(0,{'name':'Edited'},user=editor)['revision'],1)
        with self.assertRaises(SyncError) as caught:
            self.save(1,{},True,user=editor)
        self.assertEqual(caught.exception.status,403)
        deleter={**editor,'permissions':json.dumps({'branch_id':'main','deleteVehicles':True})}
        self.assertTrue(self.save(1,{},True,user=deleter)['deleted'])
        with self.assertRaises(SyncError):
            self.save(2,{'name':'Recreated'},user=deleter)

    def test_reject_credentials_paths_and_malformed_changes(self):
        for data in ({'password':'secret'},{'token':'secret'},{'imagePath':'C:/private.jpg'},
                     {'attachments':['/data/local.jpg']},{'name':{'password':'hidden'}},
                     {'name':'x'*8001}):
            with self.subTest(data=list(data)), self.assertRaises(SyncError):
                self.save(0,data)
        for revision in (-1,True,'1',None):
            with self.assertRaises(SyncError):
                self.save(revision,{'name':'test'})
        self.assertEqual(sync.list_records(self.c,self.admin,'main','vehicles','',SyncError)['records'],[])

    def test_tombstone_cannot_be_recreated_from_stale_device(self):
        self.save(0,{'name':'Old'})
        self.save(1,{},True)
        with self.assertRaises(SyncError): self.save(0,{'name':'Old'})
        with self.assertRaises(SyncError): self.save(1,{'name':'Changed offline'})

    def test_transaction_rollback_does_not_leave_partial_history(self):
        self.c.commit()
        with self.assertRaises(RuntimeError):
            with self.c:
                sync.save_record(self.c,self.admin,'main','vehicles','v1',{'baseRevision':0,'data':{'name':'test'}},SyncError)
                raise RuntimeError('simulated transaction failure')
        self.assertEqual(sync.list_records(self.c,self.admin,'main','vehicles','',SyncError)['records'],[])
        self.assertEqual(sync.history(self.c,self.admin,'main','vehicles','v1',SyncError),[])

    def test_concurrent_writers_do_not_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'sync.db'
            with sqlite3.connect(path) as c:
                c.executescript('CREATE TABLE organizations(id INTEGER PRIMARY KEY); INSERT INTO organizations VALUES(1);')
                sync.migrate(c)
            c.close()
            barrier=threading.Barrier(2); results=[]
            def writer(name):
                c=sqlite3.connect(path,timeout=5); c.row_factory=sqlite3.Row
                barrier.wait()
                try:
                    with c:
                        sync.save_record(c,self.admin,'main','vehicles','v1',{'baseRevision':0,'data':{'name':name}},SyncError)
                    results.append(200)
                except SyncError as e: results.append(e.status)
                finally: c.close()
            threads=[threading.Thread(target=writer,args=(name,)) for name in ('A','B')]
            for t in threads: t.start()
            for t in threads: t.join(timeout=10)
            self.assertEqual(sorted(results),[200,409])

    def test_pagination_migration_and_history_preservation(self):
        for i in range(202):
            sync.save_record(self.c,self.admin,'main','vehicles',f'v{i:03}',{'baseRevision':0,'data':{'name':'Test'}},SyncError)
        sync.migrate(self.c)
        first=sync.list_records(self.c,self.admin,'main','vehicles','',SyncError)
        second=sync.list_records(self.c,self.admin,'main','vehicles',first['nextCursor'],SyncError)
        self.assertEqual(len(first['records']),200)
        self.assertEqual(len(second['records']),2)
        self.assertIsNone(second['nextCursor'])
