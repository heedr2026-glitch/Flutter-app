import json, os, tempfile, threading, unittest, hashlib
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from contextlib import ExitStack
from datetime import datetime,timedelta,timezone
import server,owner_admin
from test_advertisements import test_db

class AdminConsoleTest(unittest.TestCase):
 def setUp(self):
  self.stack=ExitStack(); directory=self.stack.enter_context(tempfile.TemporaryDirectory()); self.stack.enter_context(patch.object(server,'DB_PATH',Path(directory)/'test.db'));self.stack.enter_context(patch.object(server,'DATABASE_URL',''));self.stack.enter_context(patch.object(server,'db',test_db));self.stack.enter_context(patch.dict(os.environ,{'KHDOOM_OWNER_KEY':'test-owner'}));server.init_db()
  with server.db() as c:
   for i in range(1,36):
    c.execute('INSERT INTO organizations(id,name,phone,created_at) VALUES(?,?,?,?)',(i,'Org '+str(i),'050'+str(i),server.now()));c.execute('INSERT INTO subscriptions(organization_id,package,starts_at) VALUES(?,?,?)',(i,'vip' if i%2 else 'free',server.now()))
   c.execute("INSERT INTO users(id,organization_id,name,username,password_hash,password_salt,role,created_at) VALUES(1,1,'User','user','x','x','admin',?)",(server.now(),))
   c.execute('INSERT INTO sessions(token_hash,user_id,created_at,expires_at) VALUES(?,?,?,?)',(hashlib.sha256(b'customer').hexdigest(),1,server.now(),(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()))
  self.http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler);self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
 def tearDown(self): self.http.shutdown();self.http.server_close();self.thread.join();self.stack.close()
 def call(self,path,method='GET',data=None,token=None,owner=True):
  headers={'Content-Type':'application/json'}
  if token:headers['X-Admin-Session']=token
  elif owner:headers['X-Owner-Key']='test-owner'
  req=Request('http://127.0.0.1:'+str(self.http.server_port)+path,method=method,headers=headers,data=None if data is None else json.dumps(data).encode())
  with urlopen(req,timeout=8) as r:return json.load(r)
 def staff(self):
  self.call('/owner/api/v2/admins','POST',{'name':'Support','username':'support','password':'Strong-test-123','role':'support','active':True})
  return self.call('/owner/api/v2/login','POST',{'username':'support','password':'Strong-test-123'},owner=False)['token']
 def test_staff_cannot_escalate_via_legacy_or_new_routes(self):
  token=self.staff();self.assertEqual(self.call('/owner/api/v2/me',token=token)['role'],'support')
  for p in ['/owner/api/v2/admins','/owner/api/security/overview','/owner/api/codes','/owner/api/v2/packages']:
   with self.assertRaises(HTTPError) as e:self.call(p,token=token)
   self.assertEqual(e.exception.code,403);e.exception.close()
  summary=self.call('/owner/api/v2/summary',token=token);self.assertNotIn('logins',summary);self.assertIn('organizations',summary)
 def test_pages_filters_and_profile(self):
  first=self.call('/owner/api/v2/organizations');second=self.call('/owner/api/v2/organizations?page=2');self.assertEqual(first['total'],35);self.assertEqual(len(first['items']),30);self.assertEqual(len(second['items']),5)
  self.assertFalse({x['id'] for x in first['items']}&{x['id'] for x in second['items']})
  self.assertTrue(all(x['package']=='vip' for x in self.call('/owner/api/v2/organizations?package=vip')['items']))
  profile=self.call('/owner/api/v2/organizations/1');self.assertEqual(len(profile['devices']),1);self.assertNotIn('password_hash',profile['users'][0])
 def test_staff_revocation_and_audit_actor(self):
  token=self.staff();item=self.call('/owner/api/v2/admins')['items'][0]
  self.call('/owner/api/v2/admins/'+str(item['id']),'PUT',{**item,'active':False,'permissions':['support'],'password':'Changed-test-123'})
  with self.assertRaises(HTTPError) as e:self.call('/owner/api/v2/me',token=token)
  self.assertEqual(e.exception.code,401);e.exception.close()
  self.assertTrue(self.call('/owner/api/v2/security?type=audit')['items'])
  self.assertEqual(self.call('/owner/api/v2/summary')['passwordResets'],1)
 def test_prices_and_offers_survive_restart_and_expire(self):
  basic=next(x for x in self.call('/owner/api/v2/packages') if x['package']=='basic');basic['monthly']=59;basic['ai_daily']=42;self.call('/owner/api/v2/packages','PUT',basic)
  self.call('/owner/api/v2/offers','POST',{'package':'basic','label':'Expired','paid_months':1,'bonus_months':2,'price_sar':10,'ends_at':'2020-01-01'})
  server.init_db()
  prices=self.call('/api/package-offers');self.assertFalse(any(x['label']=='Expired' for x in prices));self.assertEqual(next(x for x in prices if x['package']=='basic' and x['paid_months']==1)['price_sar'],59)
  with server.db() as c:self.assertEqual(owner_admin.daily_limit(c,2,'basic'),42)
 def test_admin_employee_can_create_platform_ad_with_image(self):
  self.call('/owner/api/v2/admins','POST',{'name':'Ads Employee','username':'ads_employee','password':'Strong-test-123','role':'employee','active':True})
  token=self.call('/owner/api/v2/login','POST',{'username':'ads_employee','password':'Strong-test-123'},owner=False)['token']
  created=self.call('/owner/api/platform-ads','POST',{'title':'صورة اختبار','message':'إعلان تجريبي','promoCode':'TEST','imageData':'data:image/png;base64,AAAA','durationDays':3},token=token)
  self.assertTrue(created.get('id'))
  self.assertEqual(self.call('/owner/api/platform-ads',token=token)[0]['image_data'],'data:image/png;base64,AAAA')
 def test_all_package_durations_save_as_numbers_without_swapping(self):
  payload={'package':'vip','price_1':'99','price_3':'279.50','price_6':'٤٩٩٫٧٥','price_12':'1٬٢٣٤٫٥٠'}
  saved=self.call('/owner/api/v2/packages','PUT',payload)
  self.assertTrue(saved['saved'])
  self.assertEqual(saved['prices'],{'1':99.0,'3':279.5,'6':499.75,'12':1234.5})
  with server.db() as c:
   rows=c.execute('SELECT duration_months,price_sar FROM package_prices WHERE package=? ORDER BY duration_months',('vip',)).fetchall()
   self.assertEqual([(int(r['duration_months']),float(r['price_sar'])) for r in rows],[(1,99.0),(3,279.5),(6,499.75),(12,1234.5)])
  public=self.call('/api/package-catalog',owner=False)
  vip=next(x for x in public if x['package']=='vip')
  self.assertEqual([(x['months'],x['price_sar']) for x in vip['prices']],[(1,99.0),(3,279.5),(6,499.75),(12,1234.5)])
 def test_package_settings_keep_prices_services_and_org_limits_separate(self):
  self.call('/owner/api/v2/packages','PUT',{'package':'vip','ai_daily':250,'ai_employees':8,'whatsapp_units':1000,'calls_units':600,'ads_units':12})
  vip=next(x for x in self.call('/owner/api/v2/packages') if x['package']=='vip')
  self.assertEqual(vip['service_limits'],{'ai_daily':250,'ai_employees':8,'whatsapp_units':1000,'calls_units':600,'ads_units':12})
  self.assertEqual([(x['months'],x['price_sar']) for x in vip['prices']],[(1,99.0),(3,279.0),(6,499.0),(12,899.0)])
  saved=self.call('/owner/api/package-limits','PUT',{'vip':{'users':20,'vehicles':30,'branches':10,'organization_notifications':40,'employee_notifications':50}})
  self.assertEqual(saved['vip']['users'],20);self.assertEqual(saved['vip']['vehicles'],30)
  vip=next(x for x in self.call('/owner/api/v2/packages') if x['package']=='vip')
  self.assertEqual(vip['service_limits']['ai_daily'],250)
 def test_discount_dates_scope_fixed_amount_and_duplicate(self):
  body={'code':'FIXED10','recipient_name':'A','max_uses':2,'discount_amount':10,'eligible_packages':'basic'};self.call('/owner/api/v2/codes','POST',body)
  d=self.call('/api/discount-code/preview','POST',{'code':'FIXED10','package':'basic'});self.assertEqual(d['discountedPrice'],39)
  with self.assertRaises(HTTPError):self.call('/api/discount-code/preview','POST',{'code':'FIXED10','package':'vip'})
  with self.assertRaises(HTTPError):self.call('/owner/api/v2/codes','POST',body)
  self.call('/owner/api/v2/codes','POST',{**body,'code':'FUTURE10','starts_at':'2099-01-01'})
  with self.assertRaises(HTTPError):self.call('/api/discount-code/preview','POST',{'code':'FUTURE10','package':'basic'})
 def test_support_closed_and_internal_notes(self):
  with server.db() as c:c.execute("INSERT INTO support_tickets(organization_id,user_id,category,message,status,owner_reply,created_at,updated_at) VALUES(1,1,'test','help','open','',?,?)",(server.now(),server.now()))
  token=self.staff();self.call('/owner/api/v2/support/1','PUT',{'status':'closed','owner_reply':'Resolved','note':'Internal only'},token=token)
  tickets=self.call('/owner/api/v2/support?status=closed',token=token);self.assertEqual(tickets['items'][0]['notes'][0]['actor'],'Support')
  audit=self.call('/owner/api/v2/security?type=audit');self.assertEqual(audit['items'][0]['actor'],'Support')
 def test_technical_employee_can_process_support_task_to_completion(self):
  with server.db() as c:
   c.execute("INSERT INTO support_tickets(id,organization_id,user_id,category,message,status,owner_reply,created_at,updated_at) VALUES(901,1,1,'login','Cannot sign in','in_progress','',?,?)",(server.now(),server.now()))
   c.execute("INSERT INTO technical_tasks(organization_id,user_id,support_ticket_id,service,problem,severity,status,diagnosis,proposal,started_at,created_by) VALUES(1,1,901,'support','طلب دعم #901: login — Cannot sign in','medium','queued','مهمة جديدة','فحص آمن',?, 'support-router')",(server.now(),))
  self.assertEqual(self.call('/owner/api/v2/technical-ai/tasks/1/start','POST')['action'],'start')
  self.assertEqual(self.call('/owner/api/v2/technical-ai/tasks/1/report','POST')['action'],'report')
  self.assertEqual(self.call('/owner/api/v2/technical-ai/tasks/1/complete','POST')['action'],'complete')
  with server.db() as c:
   task=c.execute('SELECT status,result FROM technical_tasks WHERE id=1').fetchone(); ticket=c.execute('SELECT status FROM support_tickets WHERE id=901').fetchone()
   self.assertEqual(task['status'],'completed'); self.assertIn('تم الإنجاز',task['result']); self.assertEqual(ticket['status'],'resolved')
 def test_suspend_revokes_and_rewards_apply_daily(self):
  self.call('/owner/api/v2/organizations/1/status','POST',{'suspended':True})
  with server.db() as c:self.assertTrue(owner_admin.suspended(c,1));self.assertEqual(owner_admin.scalar(c,'SELECT COUNT(*) n FROM sessions'),0)
  self.call('/owner/api/v2/organizations/1/reward','POST',{'kind':'ai','amount':10,'reason':'Manual reward'})
  with server.db() as c:self.assertEqual(owner_admin.daily_limit(c,1,'vip'),110)
 def test_ads_pagination_and_schedule(self):
  with server.db() as c:c.execute("INSERT INTO advertisements(organization_id,title,created_at) VALUES(1,'Ad',?)",(server.now(),))
  self.call('/owner/api/v2/ads/1','PUT',{'status':'scheduled','scheduled_at':'2099-01-01','expires_at':'2099-01-03'})
  self.assertEqual(self.call('/owner/api/v2/ads?status=scheduled')['total'],1)
  with urlopen(Request('http://127.0.0.1:'+str(self.http.server_port)+'/api/ads',headers={'Authorization':'Bearer customer'}),timeout=5) as response:self.assertEqual(json.load(response),[])
  self.call('/owner/api/v2/ads/1','DELETE');self.assertEqual(self.call('/owner/api/v2/ads')['total'],0)
 def test_community_moderation_is_separate_and_permission_checked(self):
  with server.db() as c:
   c.execute("INSERT INTO community_posts(user_id,body,created_at) VALUES(1,'Community post',?)",(server.now(),))
   c.execute("INSERT INTO community_comments(post_id,user_id,body,created_at) VALUES(1,1,'Comment',?)",(server.now(),))
  data=self.call('/owner/api/v2/community/posts');self.assertEqual(data['total'],1)
  self.call('/owner/api/v2/community/posts/1','PUT',{'hidden':True})
  with server.db() as c:self.assertEqual(c.execute('SELECT hidden FROM community_posts WHERE id=1').fetchone()['hidden'],1)
  token=self.staff()
  with self.assertRaises(HTTPError) as e:self.call('/owner/api/v2/community/posts',token=token)
  self.assertEqual(e.exception.code,403);e.exception.close()
 def test_unknown_sources_not_invented_and_login_throttle(self):
  self.assertIsNone(self.call('/owner/api/v2/summary')['calls'])
  self.assertIn('note',self.call('/owner/api/v2/usage?type=calls'))
  for i in range(11):
   with self.assertRaises(HTTPError) as e:self.call('/owner/api/v2/login','POST',{'username':'nobody','password':'bad'},owner=False)
   self.assertEqual(e.exception.code,429 if i==10 else 401);e.exception.close()
if __name__=='__main__':unittest.main()
