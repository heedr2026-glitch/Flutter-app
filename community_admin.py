"""Shared community storage and moderation; deliberately separate from customer reception chats."""
import re
import owner_admin as admin

def migrate(c,postgres=False):
 identity='BIGSERIAL PRIMARY KEY' if postgres else 'INTEGER PRIMARY KEY AUTOINCREMENT'
 c.execute(f'''CREATE TABLE IF NOT EXISTS community_posts(id {identity},user_id BIGINT NOT NULL REFERENCES users(id),body TEXT NOT NULL,hidden INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL)''')
 c.execute(f'''CREATE TABLE IF NOT EXISTS community_comments(id {identity},post_id BIGINT NOT NULL REFERENCES community_posts(id),user_id BIGINT NOT NULL REFERENCES users(id),body TEXT NOT NULL,hidden INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL)''')
 c.execute(f'''CREATE TABLE IF NOT EXISTS community_reports(id {identity},post_id BIGINT NOT NULL REFERENCES community_posts(id),user_id BIGINT NOT NULL REFERENCES users(id),reason TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'open',note TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL)''')
 c.execute('CREATE INDEX IF NOT EXISTS community_post_user ON community_posts(user_id,id)')
 c.execute('CREATE INDEX IF NOT EXISTS community_comment_post ON community_comments(post_id,id)')
 c.execute('CREATE TABLE IF NOT EXISTS community_likes(post_id BIGINT NOT NULL,user_id BIGINT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(post_id,user_id))')
 c.execute('CREATE TABLE IF NOT EXISTS community_rewards(post_id BIGINT PRIMARY KEY,user_id BIGINT NOT NULL,like_threshold INTEGER NOT NULL,months INTEGER NOT NULL DEFAULT 1,status TEXT NOT NULL DEFAULT \'pending\',created_at TEXT NOT NULL,reviewed_at TEXT)')
 c.execute(f'''CREATE TABLE IF NOT EXISTS community_messages(id {identity},user_id BIGINT NOT NULL REFERENCES users(id),sender TEXT NOT NULL,body TEXT NOT NULL,created_at TEXT NOT NULL,read INTEGER NOT NULL DEFAULT 0)''')
 c.execute('CREATE INDEX IF NOT EXISTS community_message_user ON community_messages(user_id,id)')
 c.execute(f'''CREATE TABLE IF NOT EXISTS community_admin_posts(id {identity},admin_name TEXT NOT NULL,body TEXT NOT NULL,hidden INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL)''')
 c.execute('CREATE TABLE IF NOT EXISTS community_admin_likes(post_id BIGINT NOT NULL,admin_name TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(post_id,admin_name))')

def moderation(c,route,method,data,q,page,error,actor=None,postgres=False):
 # تأكد من وجود جداول المجتمع حتى تعمل النسخ التي بدأت قبل آخر ترحيل.
 migrate(c,postgres=postgres)
 part=route.split('/')[1:] or ['posts']; kind=part[0]
 if kind=='chat':
  if len(part)==1 and method=='GET':
   return {'items':admin.rows(c,'SELECT u.id user_id,u.name,u.organization_id,o.name organization_name,MAX(m.created_at) last_message,SUM(CASE WHEN m.sender=\'user\' AND m.read=0 THEN 1 ELSE 0 END) unread FROM community_messages m JOIN users u ON u.id=m.user_id JOIN organizations o ON o.id=u.organization_id GROUP BY u.id,u.name,u.organization_id,o.name ORDER BY MAX(m.id) DESC LIMIT 100')}
  if len(part)==2:
   user_id=int(part[1])
   if method=='GET':
    return {'items':admin.rows(c,'SELECT m.id,m.sender,m.body,m.created_at,u.name FROM community_messages m JOIN users u ON u.id=m.user_id WHERE m.user_id=? ORDER BY m.id ASC',(user_id,))}
   if method=='POST':
    body=str(data.get('body','')).strip()
    if not 1<=len(body)<=3000: raise error(400,'اكتب رسالة من 1 إلى 3000 حرف')
    c.execute('INSERT INTO community_messages(user_id,sender,body,created_at,read) VALUES(?,?,?,?,1)',(user_id,'admin',body,stamp()))
    return {'saved':True}
  raise error(404,'محادثة المجتمع غير موجودة')
 if kind=='admin-posts':
  if method=='GET': return {'items':admin.rows(c,'SELECT id,admin_name name,body,created_at,hidden FROM community_admin_posts ORDER BY id DESC LIMIT 100')}
  if method=='PUT' and len(part)==2:
   c.execute('UPDATE community_admin_posts SET hidden=? WHERE id=?',(int(bool(data.get('hidden'))),int(part[1]))); return {'saved':True}
  if method=='POST':
   body=str(data.get('body','')).strip()
   if not 1<=len(body)<=3000: raise error(400,'اكتب منشورًا من 1 إلى 3000 حرف')
   c.execute('INSERT INTO community_admin_posts(admin_name,body,created_at) VALUES(?,?,?)',(str((actor or {}).get('name','إدارة خدوم'))[:120],body,stamp()))
   return {'saved':True}
  raise error(405,'الإجراء غير متاح')
 if kind=='likes' and len(part)==2 and method=='POST':
  post_id=int(part[1]); name=str((actor or {}).get('name','إدارة خدوم'))[:120]
  c.execute('INSERT INTO community_admin_likes(post_id,admin_name,created_at) VALUES(?,?,?) ON CONFLICT(post_id,admin_name) DO NOTHING',(post_id,name,stamp()))
  return {'saved':True}
 if kind=='rewards' and len(part)==2 and method=='POST':
  post_id=int(part[1]); post=c.execute('SELECT p.user_id,u.organization_id FROM community_posts p JOIN users u ON u.id=p.user_id WHERE p.id=?',(post_id,)).fetchone()
  if not post: raise error(404,'المنشور غير موجود')
  reward=str(data.get('kind','days')); amount=int(data.get('amount',1) or 1)
  if reward not in ('days','ai') or amount<1 or amount>3650: raise error(400,'بيانات المكافأة غير صحيحة')
  c.execute('INSERT INTO platform_rewards(organization_id,kind,amount,reason,actor,created_at) VALUES(?,?,?,?,?,?)',(post['organization_id'],reward,amount,'مكافأة منشور مجتمع خدوم',str((actor or {}).get('name','إدارة خدوم')),stamp()))
  return {'saved':True}
 if kind not in ('posts','comments','reports','users','likes'): raise error(404,'القسم غير موجود')
 if len(part)==2:
  ident=int(part[1])
  if kind=='users' and method=='GET':
   row=c.execute("SELECT u.id,u.name,u.email,u.phone,u.organization_id,o.name organization_name,s.package FROM users u JOIN organizations o ON o.id=u.organization_id LEFT JOIN subscriptions s ON s.organization_id=o.id WHERE u.id=?",(ident,)).fetchone()
   if not row: raise error(404,'المستخدم غير موجود')
   return dict(row)
  if method=='PUT' and kind in ('posts','comments'):
   c.execute('UPDATE community_'+kind+' SET hidden=? WHERE id=?',(int(bool(data.get('hidden'))),ident));return {'saved':True}
  if method=='PUT' and kind=='reports':
   if data.get('status') not in ('open','closed'): raise error(400,'حالة غير صحيحة')
   c.execute('UPDATE community_reports SET status=?,note=? WHERE id=?',(data['status'],str(data.get('note',''))[:1000],ident));return {'saved':True}
  raise error(405,'الإجراء غير متاح')
 if method!='GET': raise error(405,'الإجراء غير متاح')
 if kind=='users':
  return admin.paged(c,'SELECT u.id,u.name,u.organization_id,o.name organization_name,(SELECT MAX(created_at) FROM community_posts WHERE user_id=u.id) last_post','FROM users u JOIN organizations o ON o.id=u.organization_id WHERE EXISTS(SELECT 1 FROM community_posts p WHERE p.user_id=u.id) OR EXISTS(SELECT 1 FROM community_comments co WHERE co.user_id=u.id)',[],'u.id DESC',page)
 if kind=='posts':
  return admin.paged(c,'SELECT t.*,u.name,u.organization_id,(SELECT COUNT(*) FROM community_likes l WHERE l.post_id=t.id) like_count','FROM community_posts t JOIN users u ON u.id=t.user_id',[],'like_count DESC,t.id DESC',page)
 return admin.paged(c,'SELECT t.*,u.name,u.organization_id','FROM community_'+kind+' t JOIN users u ON u.id=t.user_id',[],'t.id DESC',page)

def client(h,method,s):
 from urllib.parse import urlparse,parse_qs
 path=urlparse(h.path).path.rstrip('/')
 if not path.startswith('/api/community/'):return False
 route=path[len('/api/community/'):]
 with s.db() as c:
  user=h._user(c)
  # Startup migration already creates these tables on PostgreSQL. Avoid
  # running DDL inside every authenticated chat request; SQLite keeps the
  # lightweight safety migration for local/legacy instances.
  if not isinstance(c, s.PostgresConnection):
   migrate(c)
  if route=='posts' and method=='GET':
   try: page=max(1,int(parse_qs(urlparse(h.path).query).get('page',['1'])[0]))
   except ValueError:raise s.ApiError(400,'رقم الصفحة غير صحيح')
   result=admin.paged(c,'SELECT p.id,p.body,p.created_at,u.name,(SELECT COUNT(*) FROM community_likes l WHERE l.post_id=p.id) like_count','FROM community_posts p JOIN users u ON u.id=p.user_id WHERE p.hidden=0 AND u.organization_id=?',[user['organization_id']],'p.id DESC',page)
   admin_posts=admin.rows(c,'SELECT -p.id id,p.body,p.created_at,p.admin_name name,(SELECT COUNT(*) FROM community_likes l WHERE l.post_id=-p.id) like_count FROM community_admin_posts p WHERE p.hidden=0 ORDER BY p.id DESC LIMIT 100')
   result['items']=admin_posts+result['items']; result['total']+=len(admin_posts)
  elif route=='posts' and method=='POST':
   body=str(h._body().get('body','')).strip()
   if not 1<=len(body)<=3000:raise s.ApiError(400,'المنشور من 1 إلى 3000 حرف')
   item=c.execute('INSERT INTO community_posts(user_id,body,created_at) VALUES(?,?,?) RETURNING id',(user['id'],body,admin.stamp())).fetchone();result={'id':item['id']}
  elif re.fullmatch(r'posts/\d+/like',route) and method=='POST':
   ident=int(route.split('/')[1])
   if ident<0:
    if not c.execute('SELECT id FROM community_admin_posts WHERE id=? AND hidden=0',(-ident,)).fetchone(): raise s.ApiError(404,'المنشور غير موجود')
    c.execute('INSERT INTO community_likes(post_id,user_id,created_at) VALUES(?,?,?) ON CONFLICT(post_id,user_id) DO NOTHING',(ident,user['id'],admin.stamp()))
    count=c.execute('SELECT COUNT(*) total FROM community_likes WHERE post_id=?',(ident,)).fetchone()['total']; result={'liked':True,'likeCount':count,'rewardEligible':False}
   elif not c.execute('SELECT p.id FROM community_posts p JOIN users u ON u.id=p.user_id WHERE p.id=? AND p.hidden=0 AND u.organization_id=?',(ident,user['organization_id'])).fetchone(): raise s.ApiError(404,'المنشور غير موجود')
   else:
    c.execute('INSERT INTO community_likes(post_id,user_id,created_at) VALUES(?,?,?) ON CONFLICT(post_id,user_id) DO NOTHING',(ident,user['id'],admin.stamp()))
    count=c.execute('SELECT COUNT(*) total FROM community_likes WHERE post_id=?',(ident,)).fetchone()['total']
    if count == 10:
     c.execute("INSERT INTO community_rewards(post_id,user_id,like_threshold,months,created_at) SELECT ?,user_id,10,1,? FROM community_posts WHERE id=? ON CONFLICT(post_id) DO NOTHING",(ident,admin.stamp(),ident))
    result={'liked':True,'likeCount':count,'rewardEligible':count>=10}
  elif route=='chat' and method=='GET':
   try: after=max(0,int(parse_qs(urlparse(h.path).query).get('after',['0'])[0]))
   except ValueError: raise s.ApiError(400,'رقم الرسالة غير صحيح')
   result=admin.rows(c,'SELECT id,sender,body,created_at FROM community_messages WHERE user_id=? AND id>? ORDER BY id ASC LIMIT 100',(user['id'],))
   c.execute("UPDATE community_messages SET read=1 WHERE user_id=? AND sender='admin'",(user['id'],))
  elif route=='chat' and method=='POST':
   body=str(h._body().get('body','')).strip()
   if not 1<=len(body)<=3000: raise s.ApiError(400,'اكتب رسالة من 1 إلى 3000 حرف')
   item=c.execute('INSERT INTO community_messages(user_id,sender,body,created_at,read) VALUES(?,?,?,?,0) RETURNING id',(user['id'],'user',body,admin.stamp())).fetchone();result={'id':item['id']}
  elif re.fullmatch(r'posts/\d+/(comments|reports)',route):
   _,ident,kind=route.split('/');ident=int(ident)
   if not c.execute('SELECT p.id FROM community_posts p JOIN users u ON u.id=p.user_id WHERE p.id=? AND p.hidden=0 AND u.organization_id=?',(ident,user['organization_id'])).fetchone():raise s.ApiError(404,'المنشور غير موجود')
   if kind=='comments' and method=='GET':result=admin.rows(c,'SELECT c.id,c.body,c.created_at,u.name FROM community_comments c JOIN users u ON u.id=c.user_id WHERE c.post_id=? AND c.hidden=0 ORDER BY c.id DESC LIMIT 100',(ident,))
   elif method=='POST':
    key='body' if kind=='comments' else 'reason';text=str(h._body().get(key,'')).strip()
    if not 1<=len(text)<=1500:raise s.ApiError(400,'اكتب نصًا من 1 إلى 1500 حرف')
    result={'id':c.execute('INSERT INTO community_'+kind+'(post_id,user_id,'+key+',created_at) VALUES(?,?,?,?) RETURNING id',(ident,user['id'],text,admin.stamp())).fetchone()['id']}
   else:raise s.ApiError(405,'الإجراء غير متاح')
  else:raise s.ApiError(404,'المسار غير موجود')
  c.commit()
 h._send(200,result);return True
