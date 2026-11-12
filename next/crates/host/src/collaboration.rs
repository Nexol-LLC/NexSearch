//! Atomic client ownership changes and append-only CRM history.
use crate::{
    RequestContext, crm,
    db::{self, Tx},
    error::{ApiError, Result},
    json_response,
};
use axum::response::Response;
use serde_json::{Value, json};
use uuid::Uuid;
pub async fn notify(
    tx: &mut Tx,
    owner: Uuid,
    kind: &str,
    title: &str,
    description: &str,
    entity: &str,
    key: Option<String>,
) -> Result<()> {
    sqlx::query("INSERT INTO notifications(id,employee_id,kind,title,description,entity_id,dedupe_key) VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT(employee_id,dedupe_key) DO NOTHING")
 .bind(Uuid::new_v4()).bind(owner).bind(kind).bind(title.chars().take(300).collect::<String>()).bind(description.chars().take(2000).collect::<String>()).bind(entity).bind(key.unwrap_or_else(||Uuid::new_v4().to_string())).execute(&mut **tx).await?;
    Ok(())
}
pub async fn route(c: &RequestContext<'_>) -> Result<Option<Response>> {
    let u = c.actor;
    let b = &c.body;
    let result = match (c.method, c.parts.as_slice()) {
        ("GET", ["api", "companies", id, "transfer-targets"]) => {
            if !u.leader() {
                return Err(ApiError::denied(
                    "Передача доступна руководителю и генеральному директору",
                ));
            }
            let id = crm::path_id(id)?;
            let mut tx = db::transaction(&c.host.pool, Some(u), false).await?;
            let company = crm::company(&mut tx, id).await?;
            if company["is_client"] != true {
                return Err(ApiError::conflict(
                    "Передавать можно клиентов, взятых в работу",
                ));
            }
            let people:Vec<Value>=sqlx::query_scalar("SELECT to_jsonb(e)-'password_hash' FROM employees e WHERE active AND approved AND login<>'host' AND id<>$1 ORDER BY last_name,first_name").bind(db::id(&company,"employee_id")?).fetch_all(&mut *tx).await?;
            let people: Vec<_> = people
                .into_iter()
                .filter_map(|v| db::Employee::from_row(v).ok())
                .filter(|p| u.manages(p))
                .collect();
            tx.commit().await?;
            json!({"employees":people})
        }
        ("POST", ["api", "companies", id, "transfer"]) => transfer(c, crm::path_id(id)?).await?,
        ("POST", ["api", "companies", id, "interactions"]) => {
            let id = crm::path_id(id)?;
            let mut tx = history_scope(c, id).await?;
            let note = db::text(b, "description", 10000, true)?;
            db::audit(
                &mut tx,
                u.id,
                "company.note",
                &id.to_string(),
                json!({"description":note}),
                c.ip,
            )
            .await?;
            tx.commit().await?;
            json!({"saved":true})
        }
        ("GET", ["api", "companies", id, "interactions"]) => {
            let id = crm::path_id(id)?;
            let mut tx = history_scope(c, id).await?;
            let rows:Vec<Value>=sqlx::query_scalar("SELECT to_jsonb(r) FROM (SELECT 'call:'||cr.id::text AS id,'call' AS kind,cr.called_at AS at,cr.result AS title,cr.description,e.first_name||' '||e.last_name AS actor,cr.contact_name AS contact,cr.phone FROM call_records cr JOIN employees e ON e.id=cr.employee_id WHERE cr.company_id=$1 UNION ALL SELECT 'event:'||a.id::text,a.kind,a.created_at,CASE a.kind WHEN 'company.contacts' THEN 'Обновлены контакты' WHEN 'company.transfer' THEN 'Передан клиент' WHEN 'company.note' THEN 'Заметка' WHEN 'client.stage' THEN 'Изменён этап' WHEN 'contact.create' THEN 'Добавлено контактное лицо' ELSE 'Изменения компании' END,COALESCE(a.details->>'description',a.details->>'title',a.details->>'after',''),COALESCE(e.first_name||' '||e.last_name,'Хост'),'','' FROM audit_events a LEFT JOIN employees e ON e.id=a.employee_id WHERE (a.entity_id=$1::text AND (a.kind LIKE 'lead.%' OR a.kind IN ('client.stage','company.contacts','company.transfer','company.note'))) OR (a.details->>'company_id'=$1::text AND a.kind IN ('task.create','task.update','contact.create'))) r ORDER BY at DESC,id DESC LIMIT 500").bind(id).fetch_all(&mut *tx).await?;
            tx.commit().await?;
            json!({"interactions":rows,"limit":500})
        }
        ("GET", ["api", "admin", "employees"]) => {
            if !u.leader() {
                return Err(ApiError::denied("Раздел доступен руководителю"));
            }
            let mut tx = db::transaction(&c.host.pool, Some(u), false).await?;
            let people:Vec<Value>=sqlx::query_scalar("SELECT to_jsonb(r) FROM (SELECT e.id,e.login,e.first_name,e.last_name,e.active,e.is_admin,e.role,e.requested_role,e.department,e.approved,(SELECT max(d.last_seen) FROM devices d WHERE d.employee_id=e.id) AS last_seen,(SELECT count(*) FROM employee_leads l WHERE l.employee_id=e.id) AS found_count,(SELECT count(*) FROM employee_clients cl WHERE cl.employee_id=e.id AND cl.active) AS client_count FROM employees e WHERE $1 OR e.id=$2 OR (e.role='logist' AND e.department=$3) ORDER BY e.created_at) r").bind(u.role=="director").bind(u.id).bind(&u.department).fetch_all(&mut *tx).await?;
            let ids: Vec<_> = people
                .iter()
                .filter_map(|p| p["id"].as_str().and_then(|s| Uuid::parse_str(s).ok()))
                .collect();
            let devices: Vec<Value> =
                sqlx::query_scalar("SELECT to_jsonb(d) FROM devices d WHERE employee_id=ANY($1)")
                    .bind(ids)
                    .fetch_all(&mut *tx)
                    .await?;
            tx.commit().await?;
            json!({"employees":people,"devices":devices})
        }
        ("GET", ["api", "admin", "employees", id, "clients"]) => {
            if !u.leader() {
                return Err(ApiError::denied("Раздел доступен руководителю"));
            }
            let id = crm::path_id(id)?;
            let mut tx = db::transaction(&c.host.pool, Some(u), false).await?;
            let p = db::employee(&mut tx, id).await?;
            if !u.manages(&p) {
                return Err(ApiError::missing());
            }
            let rows:Vec<Value>=sqlx::query_scalar("SELECT to_jsonb(r) FROM (SELECT co.id,co.payload,l.employee_id,l.status AS lead_status,l.called,l.call_note,l.called_at,l.revision,cl.stage,cl.revision AS stage_revision,cl.origin,cl.destination,cl.cargo,cl.weight_kg FROM employee_clients cl JOIN employee_leads l ON l.company_id=cl.company_id JOIN companies co ON co.id=cl.company_id WHERE cl.employee_id=$1 AND cl.active ORDER BY cl.created_at DESC) r").bind(id).fetch_all(&mut *tx).await?;
            tx.commit().await?;
            json!({"companies":rows.into_iter().map(crm::present).collect::<Vec<_>>()})
        }
        ("PATCH", ["api", "admin", "employees", id, field])
            if ["role", "active", "approval"].contains(field) =>
        {
            if u.role != "director" {
                return Err(ApiError::denied(
                    "Изменять доступ может генеральный директор",
                ));
            }
            let id = crm::path_id(id)?;
            let mut tx = db::transaction(&c.host.pool, Some(u), false).await?;
            if *field == "role" {
                let role = db::text(b, "role", 20, true)?;
                if !["logist", "manager", "director"].contains(&role.as_str())
                    || (id == u.id && role != "director")
                {
                    return Err(ApiError::invalid("Недопустимое изменение должности"));
                }
                sqlx::query(
                    "UPDATE employees SET role=$1,requested_role=$1,is_admin=$2 WHERE id=$3",
                )
                .bind(&role)
                .bind(role == "director")
                .bind(id)
                .execute(&mut *tx)
                .await?;
            } else {
                let key = if *field == "approval" {
                    "approved"
                } else {
                    "active"
                };
                let value = b[key]
                    .as_bool()
                    .ok_or_else(|| ApiError::invalid("Укажите состояние доступа"))?;
                if id == u.id && !value {
                    return Err(ApiError::invalid(
                        "Нельзя отключить собственную учётную запись",
                    ));
                }
                sqlx::query(&format!("UPDATE employees SET {key}=$1 WHERE id=$2"))
                    .bind(value)
                    .bind(id)
                    .execute(&mut *tx)
                    .await?;
                if !value {
                    sqlx::query("DELETE FROM sessions WHERE employee_id=$1")
                        .bind(id)
                        .execute(&mut *tx)
                        .await?;
                }
            }
            let p = db::employee(&mut tx, id).await?;
            db::audit(
                &mut tx,
                u.id,
                &format!("employee.{field}"),
                &id.to_string(),
                b.clone(),
                c.ip,
            )
            .await?;
            tx.commit().await?;
            json!(p)
        }
        ("GET", ["api", "history"]) => {
            if !u.leader() {
                return Err(ApiError::denied(
                    "История сотрудников доступна руководителю",
                ));
            }
            let mut tx = db::transaction(&c.host.pool, Some(u), false).await?;
            let owner = c
                .query
                .get("employee_id")
                .map(|s| crm::path_id(s))
                .transpose()?;
            if let Some(owner) = owner {
                let p = db::employee(&mut tx, owner).await?;
                if !u.manages(&p) {
                    return Err(ApiError::missing());
                }
            }
            let before = c
                .query
                .get("before")
                .map(|v| {
                    v.parse::<i64>()
                        .map_err(|_| ApiError::invalid("Проверьте страницу истории"))
                })
                .transpose()?;
            let rows:Vec<Value>=sqlx::query_scalar("SELECT to_jsonb(r) FROM (SELECT a.*,e.first_name,e.last_name,e.login FROM audit_events a LEFT JOIN employees e ON e.id=a.employee_id WHERE ($1 OR nexsearch_can_read(a.employee_id)) AND ($2::uuid IS NULL OR a.employee_id=$2) AND ($3::bigint IS NULL OR a.id<$3) ORDER BY a.id DESC LIMIT 150) r").bind(u.role=="director").bind(owner).bind(before).fetch_all(&mut *tx).await?;
            let next = if rows.len() == 150 {
                rows.last().and_then(|r| r["id"].as_i64())
            } else {
                None
            };
            tx.commit().await?;
            json!({"events":rows,"next_before":next})
        }
        ("POST", ["api", "activity"]) => {
            let kind = db::text(b, "kind", 100, true)?;
            if !(kind.starts_with("view.")
                || kind.starts_with("company.")
                || kind.starts_with("service.")
                || kind.starts_with("employee."))
            {
                return Err(ApiError::invalid("Неизвестное событие интерфейса"));
            }
            let mut details = json!({});
            for key in ["origin", "destination", "service", "name", "vehicle"] {
                if let Some(value) = b["details"][key].as_str() {
                    if value.len() > 500 {
                        return Err(ApiError::invalid("Слишком подробное событие"));
                    }
                    details[key] = value.into();
                }
            }
            let mut tx = db::transaction(&c.host.pool, Some(u), false).await?;
            db::audit(
                &mut tx,
                u.id,
                &kind,
                &db::text(b, "entity", 200, false)?,
                details,
                c.ip,
            )
            .await?;
            tx.commit().await?;
            json!({"ok":true})
        }
        _ => return Ok(None),
    };
    Ok(Some(json_response(result)))
}
async fn history_scope(c: &RequestContext<'_>, company: Uuid) -> Result<Tx> {
    let mut tx = db::transaction(&c.host.pool, None, true).await?;
    let owner: Uuid =
        sqlx::query_scalar("SELECT employee_id FROM employee_leads WHERE company_id=$1 FOR SHARE")
            .bind(company)
            .fetch_one(&mut *tx)
            .await?;
    let person = db::employee(&mut tx, owner).await?;
    if !c.actor.manages(&person) {
        return Err(ApiError::missing());
    }
    Ok(tx)
}
async fn transfer(c: &RequestContext<'_>, id: Uuid) -> Result<Value> {
    let u = c.actor;
    if !u.leader() {
        return Err(ApiError::denied("Передача доступна руководителю"));
    }
    let b = &c.body;
    let target_id = db::id(b, "employee_id")?;
    let mut tx = db::transaction(&c.host.pool, None, true).await?;
    let lead: Value = sqlx::query_scalar(
        "SELECT to_jsonb(l) FROM employee_leads l WHERE company_id=$1 FOR UPDATE",
    )
    .bind(id)
    .fetch_one(&mut *tx)
    .await?;
    let source = db::employee(&mut tx, db::id(&lead, "employee_id")?).await?;
    let target = db::employee(&mut tx, target_id).await?;
    if !u.manages(&source)
        || !u.manages(&target)
        || !target.active
        || !target.approved
        || target.login == "host"
    {
        return Err(ApiError::missing());
    }
    if source.id == target.id {
        return Err(ApiError::conflict("Сотрудник уже отвечает за клиента"));
    }
    if lead["revision"].as_i64() != Some(db::revision(b)? as i64) {
        return Err(ApiError::conflict(
            "Карточка изменилась. Откройте её заново.",
        ));
    }
    if lead["status"] != "taken" {
        return Err(ApiError::conflict(
            "Передавать можно только клиентов в работе",
        ));
    }
    let client:Value=sqlx::query_scalar("DELETE FROM employee_clients WHERE company_id=$1 AND active RETURNING to_jsonb(employee_clients)").bind(id).fetch_one(&mut *tx).await?;
    sqlx::query("UPDATE employee_leads SET employee_id=$1,revision=revision+1,updated_at=now() WHERE company_id=$2").bind(target_id).bind(id).execute(&mut *tx).await?;
    let mut client = client;
    client["employee_id"] = target_id.to_string().into();
    client["revision"] = (client["revision"].as_i64().unwrap_or(0) + 1).into();
    sqlx::query("INSERT INTO employee_clients SELECT * FROM jsonb_populate_record(NULL::employee_clients,$1)").bind(client).execute(&mut *tx).await?;
    let tasks:Vec<Uuid>=sqlx::query_scalar("UPDATE followup_tasks SET employee_id=$1,assigned_by=$2,revision=revision+1 WHERE company_id=$3 AND status='open' RETURNING id").bind(target_id).bind(u.id).bind(id).fetch_all(&mut *tx).await?;
    sqlx::query("UPDATE company_contacts SET employee_id=$1 WHERE company_id=$2")
        .bind(target_id)
        .bind(id)
        .execute(&mut *tx)
        .await?;
    let task_ids: Vec<_> = tasks.iter().map(Uuid::to_string).collect();
    sqlx::query("UPDATE notifications SET read_at=COALESCE(read_at,now()) WHERE employee_id=$1 AND kind LIKE 'task.%' AND entity_id=ANY($2)").bind(source.id).bind(task_ids).execute(&mut *tx).await?;
    let from = format!("{} {}", source.first_name, source.last_name);
    let to = format!("{} {}", target.first_name, target.last_name);
    let note = db::text(b, "note", 3000, false)?;
    db::audit(&mut tx,u.id,"company.transfer",&id.to_string(),json!({"from_employee_id":source.id,"to_employee_id":target.id,"from_name":from,"to_name":to,"description":note,"open_tasks":tasks.len()}),c.ip).await?;
    let name: String = sqlx::query_scalar("SELECT name FROM companies WHERE id=$1")
        .bind(id)
        .fetch_one(&mut *tx)
        .await?;
    notify(
        &mut tx,
        target_id,
        "client.transferred",
        "Вам передан клиент",
        &format!("{name} · от {from}"),
        &id.to_string(),
        None,
    )
    .await?;
    notify(
        &mut tx,
        source.id,
        "client.transferred",
        "Клиент передан",
        &format!("{name} · ответственный: {to}"),
        &id.to_string(),
        None,
    )
    .await?;
    tx.commit().await?;
    Ok(json!({"transferred":true,"employee_id":target_id,"open_tasks":tasks.len()}))
}
