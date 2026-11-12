use crate::{
    RequestContext,
    collaboration::notify,
    crm::path_id,
    db::{self, Tx},
    error::{ApiError, Result},
    json_response,
};
use axum::response::Response;
use chrono::{DateTime, Utc};
use serde_json::{Value, json};
use uuid::Uuid;
fn datetime(b: &Value, key: &str) -> Result<DateTime<Utc>> {
    DateTime::parse_from_rfc3339(b[key].as_str().unwrap_or(""))
        .map(|t| t.with_timezone(&Utc))
        .map_err(|_| ApiError::invalid(format!("Дата {key} должна содержать часовой пояс")))
}
async fn owner(c: &RequestContext<'_>, tx: &mut Tx) -> Result<Uuid> {
    let id = c
        .query
        .get("employee_id")
        .map(|s| path_id(s))
        .transpose()?
        .unwrap_or(c.actor.id);
    let person = db::employee(tx, id).await?;
    if !c.actor.manages(&person) {
        return Err(ApiError::missing());
    }
    Ok(id)
}
pub async fn route(c: &RequestContext<'_>) -> Result<Option<Response>> {
    let u = c.actor;
    let result = match (c.method, c.parts.as_slice()) {
        ("GET", ["api", "tasks", "assignees"]) => {
            let mut tx = db::transaction(&c.host.pool, Some(u), false).await?;
            let rows:Vec<Value>=sqlx::query_scalar("SELECT to_jsonb(r) FROM (SELECT id,first_name,last_name,role FROM employees WHERE active AND approved AND nexsearch_can_assign(id) ORDER BY last_name,first_name) r").fetch_all(&mut *tx).await?;
            tx.commit().await?;
            json!({"employees":rows})
        }
        ("GET", ["api", "tasks", "companies"]) => {
            let mut tx = db::transaction(&c.host.pool, Some(u), false).await?;
            let target = owner(c, &mut tx).await?;
            let rows:Vec<Value>=sqlx::query_scalar("SELECT to_jsonb(r) FROM (SELECT co.id,co.name,co.inn,l.status AS lead_status,COALESCE(cl.active,false) AS is_client FROM employee_leads l JOIN companies co ON co.id=l.company_id LEFT JOIN employee_clients cl ON cl.company_id=co.id WHERE l.employee_id=$1 ORDER BY co.name) r").bind(target).fetch_all(&mut *tx).await?;
            tx.commit().await?;
            json!({"companies":rows})
        }
        ("GET", ["api", "tasks"]) => {
            let mut tx = db::transaction(&c.host.pool, Some(u), false).await?;
            let target = owner(c, &mut tx).await?;
            let rows:Vec<Value>=sqlx::query_scalar("SELECT to_jsonb(r) FROM (SELECT t.*,co.name AS company_name FROM followup_tasks t LEFT JOIN companies co ON co.id=t.company_id WHERE t.employee_id=$1 ORDER BY t.status,t.due_at LIMIT 1000) r").bind(target).fetch_all(&mut *tx).await?;
            tx.commit().await?;
            json!({"tasks":rows})
        }
        ("GET", ["api", "tasks", id]) => {
            let mut tx = db::transaction(&c.host.pool, Some(u), false).await?;
            let row:Value=sqlx::query_scalar("SELECT to_jsonb(r) FROM (SELECT t.*,co.name AS company_name FROM followup_tasks t LEFT JOIN companies co ON co.id=t.company_id WHERE t.id=$1) r").bind(path_id(id)?).fetch_one(&mut *tx).await?;
            tx.commit().await?;
            row
        }
        ("POST", ["api", "tasks"]) => save(c, None).await?,
        ("PUT", ["api", "tasks", id]) => save(c, Some(path_id(id)?)).await?,
        ("GET", ["api", "today"]) => {
            let mut tx = db::transaction(&c.host.pool, Some(u), false).await?;
            let target = owner(c, &mut tx).await?;
            let rows:Vec<Value>=sqlx::query_scalar("SELECT to_jsonb(r) FROM (SELECT t.*,co.name AS company_name FROM followup_tasks t LEFT JOIN companies co ON co.id=t.company_id WHERE t.employee_id=$1 AND t.status='open' AND t.due_at<(date_trunc('day',now() AT TIME ZONE 'Europe/Moscow')+interval '1 day') AT TIME ZONE 'Europe/Moscow' ORDER BY t.due_at LIMIT 100) r").bind(target).fetch_all(&mut *tx).await?;
            let orders:Value=sqlx::query_scalar("SELECT jsonb_build_object('active',count(*) FILTER(WHERE status NOT IN ('done','cancelled')),'done',count(*) FILTER(WHERE status='done')) FROM shipment_orders WHERE employee_id=$1").bind(target).fetch_one(&mut *tx).await?;
            tx.commit().await?;
            json!({"tasks":rows,"orders":orders,"upcoming_loadings":[],"time_zone":"Europe/Moscow","server_time":Utc::now()})
        }
        ("GET", ["api", "notifications"]) => {
            reminders(c).await?;
            let mut tx = db::transaction(&c.host.pool, Some(u), false).await?;
            let rows:Vec<Value>=sqlx::query_scalar("SELECT to_jsonb(r) FROM (SELECT * FROM notifications WHERE employee_id=$1 AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 100) r").bind(u.id).fetch_all(&mut *tx).await?;
            let count:i64=sqlx::query_scalar("SELECT count(*) FROM notifications WHERE employee_id=$1 AND read_at IS NULL AND deleted_at IS NULL").bind(u.id).fetch_one(&mut *tx).await?;
            tx.commit().await?;
            json!({"notifications":rows,"unread":count,"server_time":Utc::now()})
        }
        ("POST", ["api", "notifications", "clear"])
        | ("POST", ["api", "notifications", "read-all"]) => {
            if c.parts[2] == "clear" {
                reminders(c).await?
            }
            let mut tx = db::transaction(&c.host.pool, Some(u), false).await?;
            let sql = if c.parts[2] == "clear" {
                "UPDATE notifications SET deleted_at=now(),read_at=COALESCE(read_at,now()) WHERE employee_id=$1 AND deleted_at IS NULL"
            } else {
                "UPDATE notifications SET read_at=now() WHERE employee_id=$1 AND read_at IS NULL"
            };
            sqlx::query(sql).bind(u.id).execute(&mut *tx).await?;
            if c.parts[2] == "clear" {
                db::audit(&mut tx, u.id, "notifications.clear", "", json!({}), c.ip).await?
            }
            tx.commit().await?;
            json!({"ok":true})
        }
        ("DELETE", ["api", "notifications", id])
        | ("POST", ["api", "notifications", id, "read"]) => {
            let mut tx = db::transaction(&c.host.pool, Some(u), false).await?;
            let sql = if c.method == "DELETE" {
                "UPDATE notifications SET deleted_at=COALESCE(deleted_at,now()),read_at=COALESCE(read_at,now()) WHERE id=$1 AND employee_id=$2 RETURNING id"
            } else {
                "UPDATE notifications SET read_at=COALESCE(read_at,now()) WHERE id=$1 AND employee_id=$2 RETURNING id"
            };
            sqlx::query_scalar::<_, Uuid>(sql)
                .bind(path_id(id)?)
                .bind(u.id)
                .fetch_one(&mut *tx)
                .await?;
            tx.commit().await?;
            json!({"ok":true})
        }
        _ => return Ok(None),
    };
    Ok(Some(json_response(result)))
}
async fn save(c: &RequestContext<'_>, id: Option<Uuid>) -> Result<Value> {
    let u = c.actor;
    let b = &c.body;
    let mut tx = db::transaction(&c.host.pool, None, true).await?;
    let old: Option<Value> = if let Some(id) = id {
        Some(
            sqlx::query_scalar("SELECT to_jsonb(t) FROM followup_tasks t WHERE id=$1 FOR UPDATE")
                .bind(id)
                .fetch_one(&mut *tx)
                .await?,
        )
    } else {
        None
    };
    let target = db::optional_id(b, "employee_id")?
        .or_else(|| {
            old.as_ref()
                .and_then(|r| r["employee_id"].as_str())
                .and_then(|s| Uuid::parse_str(s).ok())
        })
        .unwrap_or(u.id);
    let person = db::employee(&mut tx, target).await?;
    if !u.manages(&person) || !person.active || !person.approved {
        return Err(ApiError::missing());
    }
    if let Some(old) = &old {
        if db::id(old, "employee_id")? != target {
            return Err(ApiError::invalid(
                "Для другого исполнителя создайте отдельную задачу",
            ));
        }
        if old["revision"].as_i64() != Some(db::revision(b)? as i64) {
            return Err(ApiError::conflict("Задача уже изменена"));
        }
    }
    let company = db::optional_id(b, "company_id")?;
    let order = db::optional_id(b, "order_id")?;
    if let Some(company) = company {
        let owner: Uuid =
            sqlx::query_scalar("SELECT employee_id FROM employee_leads WHERE company_id=$1")
                .bind(company)
                .fetch_one(&mut *tx)
                .await?;
        if owner != target {
            return Err(ApiError::missing());
        }
    }
    if let Some(order) = order {
        let owner: Uuid = sqlx::query_scalar("SELECT employee_id FROM shipment_orders WHERE id=$1")
            .bind(order)
            .fetch_one(&mut *tx)
            .await?;
        if owner != target {
            return Err(ApiError::missing());
        }
    }
    let title = db::text(b, "title", 300, true)?;
    let description = db::text(b, "description", 5000, false)?;
    let due = datetime(b, "due_at")?;
    let status = if b["status"].is_null() {
        "open".to_owned()
    } else {
        db::text(b, "status", 20, true)?
    };
    let priority = if b["priority"].is_null() {
        "normal".to_owned()
    } else {
        db::text(b, "priority", 20, true)?
    };
    if !["open", "done", "cancelled"].contains(&status.as_str())
        || !["normal", "high"].contains(&priority.as_str())
    {
        return Err(ApiError::invalid("Проверьте статус или приоритет задачи"));
    }
    let ident = id.unwrap_or_else(Uuid::new_v4);
    let row: Value = if id.is_some() {
        sqlx::query_scalar("UPDATE followup_tasks SET company_id=$1,order_id=$2,title=$3,description=$4,due_at=$5,status=$6,priority=$7,revision=revision+1,completed_at=CASE WHEN $6='done' THEN COALESCE(completed_at,now()) ELSE NULL END WHERE id=$8 RETURNING to_jsonb(followup_tasks)").bind(company).bind(order).bind(&title).bind(&description).bind(due).bind(&status).bind(&priority).bind(ident).fetch_one(&mut *tx).await?
    } else {
        sqlx::query_scalar("INSERT INTO followup_tasks(id,employee_id,assigned_by,company_id,order_id,title,description,due_at,status,priority) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) RETURNING to_jsonb(followup_tasks)").bind(ident).bind(target).bind(u.id).bind(company).bind(order).bind(&title).bind(&description).bind(due).bind(&status).bind(&priority).fetch_one(&mut *tx).await?
    };
    let key = format!(
        "task:{ident}:revision:{}",
        row["revision"].as_i64().unwrap_or(0)
    );
    if target != u.id {
        notify(
            &mut tx,
            target,
            if id.is_some() {
                "task.updated"
            } else {
                "task.assigned"
            },
            &title,
            &format!("{} {}: {description}", u.first_name, u.last_name),
            &ident.to_string(),
            Some(key.clone()),
        )
        .await?
    }
    if let Some(old) = &old
        && let Ok(assigned) = db::id(old, "assigned_by")
        && assigned != target
        && assigned != u.id
    {
        notify(
            &mut tx,
            assigned,
            "task.updated",
            &title,
            &format!("Исполнитель обновил задачу: {status}"),
            &ident.to_string(),
            Some(key),
        )
        .await?
    }
    db::audit(
        &mut tx,
        u.id,
        if id.is_some() {
            "task.update"
        } else {
            "task.create"
        },
        &ident.to_string(),
        b.clone(),
        c.ip,
    )
    .await?;
    tx.commit().await?;
    Ok(row)
}
pub async fn reminders(c: &RequestContext<'_>) -> Result<()> {
    let u = c.actor;
    let mut tx = db::transaction(&c.host.pool, None, true).await?;
    sqlx::query("INSERT INTO notifications(id,employee_id,kind,title,description,entity_id,dedupe_key) SELECT gen_random_uuid(),employee_id,'task.due',left(title,300),'Срок задачи: '||to_char(due_at AT TIME ZONE 'Europe/Moscow','DD.MM.YYYY HH24:MI')||' МСК',id::text,'task:'||id::text||':due:'||to_char(due_at AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS')||CASE WHEN extract(microseconds FROM due_at)::bigint%1000000=0 THEN '' ELSE '.'||to_char(due_at,'US') END||'+00:00' FROM followup_tasks WHERE employee_id=$1 AND status='open' AND due_at<=now()+interval '15 minutes' ON CONFLICT(employee_id,dedupe_key) DO NOTHING").bind(u.id).execute(&mut *tx).await?;
    sqlx::query("INSERT INTO notifications(id,employee_id,kind,title,description,entity_id,dedupe_key) SELECT gen_random_uuid(),employee_id,'search.finished',CASE status WHEN 'stopped' THEN 'Поиск остановлен' WHEN 'error' THEN 'Ошибка поиска' ELSE 'Поиск завершён' END,'Найдено компаний: '||added::text||'. '||message,id::text,'search:'||id::text||':finished' FROM jobs WHERE employee_id=$1 AND status IN ('completed','partial','error','stopped') AND finished_at>now()-interval '24 hours' ON CONFLICT(employee_id,dedupe_key) DO NOTHING").bind(u.id).execute(&mut *tx).await?;
    if u.leader() {
        sqlx::query("INSERT INTO notifications(id,employee_id,kind,title,description,entity_id,dedupe_key) SELECT gen_random_uuid(),boss.id,'task.overdue',left('Просрочена задача: '||t.title,300),left(e.first_name||' '||e.last_name||E'\\nСрок: '||to_char(t.due_at AT TIME ZONE 'Europe/Moscow','DD.MM.YYYY HH24:MI')||' МСК'||E'\\n'||t.description,2000),t.id::text,'overdue:'||t.id::text||':'||extract(epoch FROM t.due_at)::text FROM followup_tasks t JOIN employees e ON e.id=t.employee_id JOIN employees boss ON boss.active AND boss.approved AND (boss.role='director' OR (boss.role='manager' AND boss.department=e.department)) WHERE t.status='open' AND t.due_at<now() AND e.role='logist' AND e.active AND e.approved AND boss.id=$1 ON CONFLICT(employee_id,dedupe_key) DO NOTHING").bind(u.id).execute(&mut *tx).await?;
    }
    tx.commit().await?;
    Ok(())
}
