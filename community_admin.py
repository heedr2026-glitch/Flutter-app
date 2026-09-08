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

def moderation(c,route,method,data,q,page,error):
 part=route.split('/')[1:] or ['posts']; kind=part[0]
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
 return admin.paged(c,'SELECT t.*,u.name,u.organization_id','FROM community_'+kind+' t JOIN users u ON u.id=t.user_id',[],'t.id DESC',page)

def client(h,method,s):
 from urllib.parse import urlparse,parse_qs
 path=urlparse(h.path).path.rstrip('/')
 if not path.startswith('/api/community/'):return False
 route=path[len('/api/community/'):]
 with s.db() as c:
  user=h._user(c)
  if route=='posts' and method=='GET':
   try: page=max(1,int(parse_qs(urlparse(h.path).query).get('page',['1'])[0]))
   except ValueError:raise s.ApiError(400,'رقم الصفحة غير صحيح')
   result=admin.paged(c,'SELECT p.id,p.body,p.created_at,u.name,(SELECT COUNT(*) FROM community_likes l WHERE l.post_id=p.id) like_count','FROM community_posts p JOIN users u ON u.id=p.user_id WHERE p.hidden=0 AND u.organization_id=?',[user['organization_id']],'p.id DESC',page)
  elif route=='posts' and method=='POST':
   body=str(h._body().get('body','')).strip()
   if not 1<=len(body)<=3000:raise s.ApiError(400,'المنشور من 1 إلى 3000 حرف')
   item=c.execute('INSERT INTO community_posts(user_id,body,created_at) VALUES(?,?,?) RETURNING id',(user['id'],body,admin.stamp())).fetchone();result={'id':item['id']}
  elif re.fullmatch(r'posts/\d+/like',route) and method=='POST':
   ident=int(route.split('/')[1])
   if not c.execute('SELECT p.id FROM community_posts p JOIN users u ON u.id=p.user_id WHERE p.id=? AND p.hidden=0 AND u.organization_id=?',(ident,user['organization_id'])).fetchone(): raise s.ApiError(404,'المنشور غير موجود')
   c.execute('INSERT INTO community_likes(post_id,user_id,created_at) VALUES(?,?,?) ON CONFLICT(post_id,user_id) DO NOTHING',(ident,user['id'],admin.stamp()))
   count=c.execute('SELECT COUNT(*) total FROM community_likes WHERE post_id=?',(ident,)).fetchone()['total']
   if count == 10:
    c.execute("INSERT INTO community_rewards(post_id,user_id,like_threshold,months,created_at) SELECT ?,user_id,10,1,? FROM community_posts WHERE id=? ON CONFLICT(post_id) DO NOTHING",(ident,admin.stamp(),ident))
   result={'liked':True,'likeCount':count,'rewardEligible':count>=10}
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

