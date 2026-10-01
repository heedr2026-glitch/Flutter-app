"""Platform account directory and ownership changes; deletion is recoverable archival."""
import json
import re


def migrate(c, postgres=False):
    for table in ('organizations', 'users'):
        columns = set() if postgres else {r['name'] for r in c.execute('PRAGMA table_info('+table+')')}
        if postgres or 'archived_at' not in columns:
            c.execute('ALTER TABLE '+table+' ADD COLUMN '+('IF NOT EXISTS ' if postgres else '')+'archived_at TEXT')


def owner_for(c, organization_id):
    return c.execute('SELECT u.id,u.name FROM account_organizations ao JOIN owner_accounts a ON a.id=ao.account_id JOIN users u ON u.id=a.owner_user_id WHERE ao.organization_id=?', (organization_id,)).fetchone()


def handle(c, route, method, data, query, page, actor, admin, server):
    counts = lambda: {'organizations': admin.scalar(c,'SELECT COUNT(*) n FROM organizations WHERE archived_at IS NULL'), 'users':admin.scalar(c,'SELECT COUNT(*) n FROM users u JOIN organizations o ON o.id=u.organization_id WHERE u.archived_at IS NULL AND o.archived_at IS NULL')}
    if route == 'directory/counts' and method == 'GET':
        return counts()
    if route == 'directory/organizations' and method == 'GET':
        where="FROM organizations o LEFT JOIN account_organizations ao ON ao.organization_id=o.id LEFT JOIN owner_accounts a ON a.id=ao.account_id LEFT JOIN users u ON u.id=a.owner_user_id LEFT JOIN subscriptions s ON s.organization_id=o.id WHERE o.archived_at IS NULL"
        args=[]
        if query.get('search'):
            term='%'+query['search'].lower()[:100]+'%'
            where+=' AND (LOWER(o.name) LIKE ? OR LOWER(u.name) LIKE ? OR LOWER(u.username) LIKE ?)';args=[term]*3
        if query.get('package') in ('free','basic','vip'):
            where+=" AND COALESCE(s.package,'free')=?";args.append(query['package'])
        if query.get('since') and re.fullmatch(r'\d{4}-\d{2}-\d{2}',query['since']):
            where+=' AND o.created_at>=?';args.append(query['since'])
        out=admin.paged(c,"SELECT o.id,o.name,o.entity_type,o.phone,o.created_at,u.id owner_id,u.name owner_name,u.username owner_username,COALESCE(s.package,'free') package",where,args,'o.id DESC',page)
        out['counts']=counts();return out
    if route == 'directory/users' and method == 'GET':
        where='FROM users u JOIN organizations o ON o.id=u.organization_id WHERE u.archived_at IS NULL AND o.archived_at IS NULL';args=[]
        if query.get('organization'):
            where+=' AND u.organization_id=?';args.append(int(query['organization']))
        if query.get('search'):
            term='%'+query['search'].lower()[:100]+'%';where+=' AND (LOWER(u.name) LIKE ? OR LOWER(u.username) LIKE ? OR LOWER(o.name) LIKE ?)';args.extend([term]*3)
        out=admin.paged(c,'SELECT u.id,u.name,u.username,u.phone,u.role,u.active,u.organization_id,o.name organization_name',where,args,'u.id DESC',page)
        out['counts']=counts();return out
    match=re.fullmatch(r'directory/(organizations|users)/(\d+)/(name|password|archive|transfer)',route)
    if not match or method != 'POST': raise ValueError('مسار إدارة الحسابات غير صحيح')
    kind,ident,action=match.groups();ident=int(ident)
    table='organizations' if kind=='organizations' else 'users'
    record=c.execute('SELECT * FROM '+table+' WHERE id=? AND archived_at IS NULL',(ident,)).fetchone()
    if not record: raise server.ApiError(404,'السجل غير موجود')
    if action in ('archive','transfer') and actor.get('role')!='owner': raise server.ApiError(403,'الحذف ونقل الملكية متاحان لمالك منصة خدووم فقط')
    if action=='name':
        name=str(data.get('name','')).strip()
        if not 2<=len(name)<=120: raise ValueError('اكتب الاسم بين حرفين و120 حرفًا')
        c.execute('UPDATE '+table+' SET name=? WHERE id=?',(name,ident))
        admin.audit(c,actor['name'],'directory_name',json.dumps({'kind':kind,'id':ident,'before':record['name'],'after':name},ensure_ascii=False))
    elif action=='password':
        user_id=ident if kind=='users' else (owner_for(c,ident) or {'id':None})['id']
        if not user_id: raise ValueError('لا يوجد مالك مرتبط؛ انقل الملكية أولًا')
        password=str(data.get('password',''))
        if len(password)<8 or len(password)>200: raise ValueError('كلمة المرور من 8 إلى 200 حرف')
        hashed,salt=server.hash_password(password)
        c.execute('UPDATE users SET password_hash=?,password_salt=? WHERE id=?',(hashed,salt,user_id))
        c.execute('DELETE FROM sessions WHERE user_id=?',(user_id,))
        admin.audit(c,actor['name'],'directory_password_reset','user='+str(user_id)+';sessions revoked')
    elif action=='archive':
        if data.get('confirmation')!=record['name']: raise ValueError('اكتب الاسم كما يظهر لتأكيد الحذف')
        if kind=='users':
            owns=admin.scalar(c,'SELECT COUNT(*) n FROM account_organizations ao JOIN owner_accounts a ON a.id=ao.account_id JOIN organizations o ON o.id=ao.organization_id WHERE a.owner_user_id=? AND o.archived_at IS NULL',(ident,))
            if owns: raise ValueError('انقل ملكية جميع المؤسسات المرتبطة قبل حذف حساب مالكها')
            c.execute('UPDATE users SET archived_at=?,active=0 WHERE id=?',(admin.stamp(),ident))
            c.execute('DELETE FROM sessions WHERE user_id=?',(ident,))
        else:
            children=admin.scalar(c,'SELECT COUNT(*) n FROM account_organizations child JOIN account_organizations root ON root.account_id=child.account_id JOIN organizations o ON o.id=child.organization_id WHERE root.organization_id=? AND root.is_primary=1 AND child.organization_id<>? AND child.independent_package=0 AND o.archived_at IS NULL',(ident,ident))
            if children: raise ValueError('توجد مؤسسات تعتمد على باقة هذه المؤسسة؛ انقلها أو افصل باقاتها أولًا')
            for owner in c.execute('SELECT u.id FROM users u JOIN owner_accounts a ON a.owner_user_id=u.id WHERE u.organization_id=?',(ident,)).fetchall():
                other=c.execute('SELECT ao.organization_id FROM account_organizations ao JOIN owner_accounts a ON a.id=ao.account_id JOIN organizations o ON o.id=ao.organization_id WHERE a.owner_user_id=? AND ao.organization_id<>? AND o.archived_at IS NULL ORDER BY ao.is_primary DESC,ao.organization_id LIMIT 1',(owner['id'],ident)).fetchone()
                if other: c.execute("UPDATE users SET organization_id=?,role='admin',permissions='{}' WHERE id=?",(other['organization_id'],owner['id']))
            c.execute('UPDATE organizations SET archived_at=? WHERE id=?',(admin.stamp(),ident))
            c.execute('INSERT INTO platform_org_state(organization_id,suspended) VALUES(?,1) ON CONFLICT(organization_id) DO UPDATE SET suspended=1',(ident,))
            c.execute('DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE organization_id=?) OR scoped_organization_id=?',(ident,ident))
        admin.audit(c,actor['name'],'directory_archived',kind+'/'+str(ident))
    elif action=='transfer':
        if kind!='organizations': raise ValueError('نقل الملكية خاص بالمؤسسات')
        new_id=int(data.get('newOwnerId',0));previous_mode=data.get('previousOwnerMode','remove')
        if previous_mode not in ('remove','keep'): raise ValueError('حدد وضع المالك السابق')
        c.execute('UPDATE account_organizations SET account_id=account_id WHERE organization_id=?',(ident,))
        old_owner=owner_for(c,ident)
        new=c.execute('SELECT id,name FROM users WHERE id=? AND organization_id=? AND active=1 AND archived_at IS NULL',(new_id,ident)).fetchone()
        if not new: raise ValueError('اختر مستخدمًا نشطًا داخل المؤسسة ليصبح المالك')
        if old_owner and new_id==old_owner['id']: raise ValueError('هذا المستخدم هو المالك الحالي')
        if data.get('confirmation')!=record['name']: raise ValueError('اكتب اسم المؤسسة لتأكيد نقل الملكية')
        ts=admin.stamp();c.execute('INSERT INTO owner_accounts(id,owner_user_id,created_at) VALUES(?,?,?) ON CONFLICT(owner_user_id) DO NOTHING',(new_id,new_id,ts))
        account=c.execute('SELECT id FROM owner_accounts WHERE owner_user_id=?',(new_id,)).fetchone()['id']
        primary=not bool(c.execute('SELECT 1 FROM account_organizations WHERE account_id=? AND is_primary=1',(account,)).fetchone())
        old_link=c.execute('SELECT account_id FROM account_organizations WHERE organization_id=?',(ident,)).fetchone()
        c.execute('INSERT INTO account_organizations(organization_id,account_id,is_primary,independent_package,verified_at,verified_by) VALUES(?,?,?,1,?,?) ON CONFLICT(organization_id) DO UPDATE SET account_id=excluded.account_id,is_primary=excluded.is_primary,independent_package=1,verified_at=excluded.verified_at,verified_by=excluded.verified_by',(ident,account,int(primary),ts,actor['name']))
        c.execute("UPDATE users SET role='admin',permissions='{}' WHERE id=?",(new_id,))
        if old_owner:
            remaining=c.execute('SELECT ao.organization_id FROM account_organizations ao JOIN owner_accounts a ON a.id=ao.account_id JOIN organizations o ON o.id=ao.organization_id WHERE a.owner_user_id=? AND o.archived_at IS NULL ORDER BY ao.is_primary DESC,ao.organization_id LIMIT 1',(old_owner['id'],)).fetchone()
            if previous_mode=='remove' and remaining:
                c.execute("UPDATE users SET organization_id=?,role='admin',permissions='{}' WHERE id=?",(remaining['organization_id'],old_owner['id']))
            else:
                c.execute("UPDATE users SET role='employee',permissions='{}',active=? WHERE id=?",(1 if previous_mode=='keep' else 0,old_owner['id']))
            c.execute('DELETE FROM sessions WHERE user_id=?',(old_owner['id'],))
            if old_link and remaining:
                c.execute('UPDATE account_organizations SET is_primary=1 WHERE account_id=? AND organization_id=?',(old_link['account_id'],remaining['organization_id']))
        c.execute('DELETE FROM sessions WHERE user_id=?',(new_id,))
        admin.audit(c,actor['name'],'directory_ownership_transfer',json.dumps({'organizationId':ident,'oldOwnerId':old_owner['id'] if old_owner else None,'newOwnerId':new_id,'previousOwnerMode':previous_mode},ensure_ascii=False))
    return {'saved':True,'message':'تم حفظ التغيير','recoverable':action=='archive'}
