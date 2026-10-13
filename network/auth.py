import hashlib
import secrets
from datetime import datetime,timedelta,timezone
from uuid import uuid4
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError,InvalidHashError

HASHER=PasswordHasher()
DUMMY=HASHER.hash(secrets.token_urlsafe(24))

def digest(token):return hashlib.sha256(token.encode()).hexdigest()

def public_employee(row):
    result={k:row[k] for k in ('id','login','first_name','last_name','is_admin','active','role','requested_role','department')}
    if result['is_admin']:result['role']='director'
    result['approved']=bool(row.get('approved',True) or result['is_admin'])
    return result

def login_slug(first,last):
    source='абвгдеёжзийклмнопрстуфхцчшщъыьэюя'
    target=['a','b','v','g','d','e','yo','zh','z','i','y','k','l','m','n','o','p','r','s','t','u','f','h','ts','ch','sh','sch','','y','','e','yu','ya']
    table=dict(zip(source,target))
    value=''.join(table.get(c,c) for c in (last+'.'+first).lower())
    return ''.join(c for c in value if c.isascii() and (c.isalnum() or c in '.-_'))[:55] or 'employee'

class Authentication:
    def __init__(self,db):self.db=db

    def register(self,first,last,password,device,computer,ip,login=None,requested_role='logist'):
        with self.db.transaction(admin=True) as db:
            base=login or login_slug(first,last);candidate=base
            db.execute('SELECT pg_advisory_xact_lock(73142002)')
            count=1
            while db.execute('SELECT 1 FROM employees WHERE login=%s',(candidate,)).fetchone():
                if login:raise ValueError('Этот логин уже занят')
                count+=1;candidate=f'{base}.{count}'
            ident=uuid4()
            row=db.execute('INSERT INTO employees(id,login,first_name,last_name,password_hash,requested_role) VALUES(%s,%s,%s,%s,%s,%s) RETURNING *',(ident,candidate,first,last,HASHER.hash(password),requested_role)).fetchone()
        self.db.audit(ident,'account.register',ident,{'requested_role':requested_role,'computer':computer},ip)
        return self.issue(row,device,computer,ip)

    def issue(self,row,device,computer,ip):
        token=secrets.token_urlsafe(32)
        with self.db.transaction(admin=True) as db:
            db.execute('INSERT INTO sessions(token_hash,employee_id,device_id,expires_at) VALUES(%s,%s,%s,%s)',(digest(token),row['id'],device,datetime.now(timezone.utc)+timedelta(days=30)))
            db.execute('''INSERT INTO devices(id,employee_id,computer_name,ip) VALUES(%s,%s,%s,%s)
              ON CONFLICT(id) DO UPDATE SET employee_id=excluded.employee_id,computer_name=excluded.computer_name,ip=excluded.ip,last_seen=now()''',(device,row['id'],computer,ip))
        return dict(employee=public_employee(row),token=token)

    def login(self,login,password,device,computer,ip):
        with self.db.transaction(admin=True) as db:row=db.execute('SELECT * FROM employees WHERE login=%s',(login,)).fetchone()
        try:valid=HASHER.verify(row['password_hash'] if row else DUMMY,password)
        except (VerifyMismatchError,InvalidHashError):valid=False
        if not row or not valid or not row['active']:raise PermissionError('Неверный логин или пароль')
        if HASHER.check_needs_rehash(row['password_hash']):
            with self.db.transaction(admin=True) as db:db.execute('UPDATE employees SET password_hash=%s WHERE id=%s',(HASHER.hash(password),row['id']))
        self.db.audit(row['id'],'account.login',row['id'],{'computer':computer},ip)
        return self.issue(row,device,computer,ip)

    def authenticate(self,token,ip=''):
        if not token or len(token)>256:raise PermissionError('Требуется вход')
        with self.db.transaction(admin=True) as db:
            row=db.execute('''SELECT e.*,s.device_id FROM sessions s JOIN employees e ON e.id=s.employee_id
               WHERE s.token_hash=%s AND s.expires_at>now() AND e.active''',(digest(token),)).fetchone()
            if not row:raise PermissionError('Сеанс завершён. Войдите снова.')
            if not row.get('approved',True) and not row['is_admin']:raise PermissionError('Учётная запись ожидает подтверждения директора')
            db.execute('UPDATE devices SET last_seen=now(),ip=CASE WHEN %s<>\'\' THEN %s ELSE ip END WHERE id=%s AND last_seen<now()-interval \'30 seconds\'',(ip,ip,row['device_id']))
        return public_employee(row)

    def logout(self,token):
        with self.db.transaction(admin=True) as db:
            row=db.execute('DELETE FROM sessions WHERE token_hash=%s RETURNING device_id',(digest(token),)).fetchone()
            if row:db.execute('UPDATE devices SET employee_id=NULL WHERE id=%s',(row['device_id'],))
