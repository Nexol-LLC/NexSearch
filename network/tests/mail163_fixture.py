"""Opt-in synthetic sender groups, sent replies, nested folders and image fixtures."""
from datetime import datetime,timezone,timedelta
from uuid import uuid4
from email.message import EmailMessage
from psycopg.types.json import Jsonb
from network.mailbox import rich_content
from pathlib import Path
PNG=(Path(__file__).resolve().parents[1]/'web/assets/logo.png').read_bytes()

def seed(db,owner,account):
    folders=[{'id':'Archive/2025','name':'Архив/2025','delimiter':'/','sent':False},
      {'id':'Clients','name':'Клиенты','delimiter':'/','selectable':False,'sent':False},
      {'id':'Clients/Moscow','name':'Клиенты/Москва','delimiter':'/','sent':False},
      {'id':'Clients/Moscow/Contracts','name':'Клиенты/Москва/Договоры','delimiter':'/','sent':False},
      {'id':'Sent','name':'Отправленные','delimiter':'/','sent':True},
      {'id':'INBOX','name':'Входящие','delimiter':'/','sent':False}]
    ids=[];now=datetime.now(timezone.utc)
    with db.transaction(owner) as c:
        c.execute('UPDATE mail_accounts SET folders=%s WHERE id=%s',(Jsonb(folders),account))
        for i in range(8):
            ident=uuid4();ids.append(str(ident));outgoing=i>=6
            m=EmailMessage();m.set_content('Учебное письмо: ставка и доставка.')
            m.add_alternative('<h1>Доставка Ozon</h1><p>Это проверка HTML с тремя изображениями.</p><table style="width:100%;border-collapse:collapse"><tr><td style="padding:16px;background-color:#eeeaff">Маршрут Москва → Казань</td><td>12 тонн</td></tr></table><p>Встроенное CID:</p><img width="64" src="cid:logo%40demo" alt="Встроенный логотип"><p>Изображение в data URI:</p><img width="64" src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=" alt="Встроенное изображение"><p>Изображение отправителя:</p><img width="64" src="https://images.example.invalid/logo.png" alt="Логотип отправителя"><script>parent.mailAttack163=true</script>',subtype='html')
            m.get_payload()[-1].add_related(PNG,maintype='image',subtype='png',cid='<logo@demo>',filename='logo.png')
            text,html,attachments=rich_content(m)
            c.execute('''INSERT INTO mail_messages(id,employee_id,account_id,folder,uidvalidity,uid,subject,sender,recipients,addresses,sent_at,seen,body_text,body_html,content_version,attachments,message_id,in_reply_to,reference_ids)
              VALUES(%s,%s,%s,%s,'1',%s,%s,%s,%s,%s,%s,%s,%s,%s,2,%s,%s,%s,%s)''',
             (ident,owner,account,'Sent' if outgoing else 'INBOX',10+i,('Re: Уточнение ставки' if outgoing else 'Заказ Ozon № '+str(100+i)),
              'Сотрудник <demo@example.org>' if outgoing else 'Ozon <orders@ozon.example>',Jsonb(['orders@ozon.example'] if outgoing else ['demo@example.org']),['orders@ozon.example','demo@example.org'],now-timedelta(minutes=20-i*2),outgoing,text,html,Jsonb(attachments),f'<ozon-{i}@example.org>','<ozon-0@example.org>' if outgoing else '',['<ozon-0@example.org>'] if outgoing else []))
    return {'first_message':ids[0],'reply':ids[-1],'account':str(account),'folders':folders}
