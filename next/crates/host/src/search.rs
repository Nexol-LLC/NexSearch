//! Persistent job queue, cancellation and globally unique company reservations.
use crate::{
    Host, RequestContext, company_time, crm, db,
    error::{ApiError, Result},
    json_response,
};
use axum::response::Response;
use chrono::{DateTime, Utc};
use nexsearch_jobs::{Cancellation, Coordinator, JobError};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sqlx::QueryBuilder;
use std::{
    collections::HashMap,
    sync::Arc,
    time::{Duration, Instant},
};
use tokio::sync::{Mutex, Semaphore};
use uuid::Uuid;
#[derive(Clone, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub struct Criteria {
    pub sectors: Vec<String>,
    pub revenue_segments: Vec<String>,
    pub turnover_segments: Vec<String>,
    pub primary_activity_only: bool,
    pub region: String,
    pub timezones: Vec<i32>,
    pub keywords: String,
    pub require_contacts: bool,
    pub company_count: u8,
    pub report_year: Option<u32>,
}
impl Default for Criteria {
    fn default() -> Self {
        Self {
            sectors: vec![],
            revenue_segments: vec![],
            turnover_segments: vec![],
            primary_activity_only: true,
            region: String::new(),
            timezones: vec![],
            keywords: String::new(),
            require_contacts: false,
            company_count: 10,
            report_year: None,
        }
    }
}
impl Criteria {
    pub fn parse(value: Value) -> Result<Self> {
        let value: Self = serde_json::from_value(value)
            .map_err(|_| ApiError::invalid("Проверьте критерии поиска"))?;
        let catalog: Value =
            serde_json::from_str(include_str!("../../../data/catalog.json")).expect("Каталог");
        if !(1..=50).contains(&value.company_count)
            || value.region.chars().count() > 150
            || value.keywords.chars().count() > 200
            || value.sectors.len() > 90
            || value.timezones.len() > 11
            || value.timezones.iter().any(|h| !(2..=12).contains(h))
            || value
                .report_year
                .is_some_and(|y| !(2011..=2100).contains(&y))
            || value.sectors.iter().any(|s| {
                !catalog["sectors"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .any(|v| v["code"] == *s)
            })
        {
            return Err(ApiError::invalid("Проверьте диапазоны критериев"));
        }
        for group in [&value.revenue_segments, &value.turnover_segments] {
            if group.len() > 4
                || group
                    .iter()
                    .any(|s| !matches!(s.as_str(), "10-100" | "100-500" | "500-1000" | "1000+"))
            {
                return Err(ApiError::invalid("Неизвестный сегмент выручки"));
            }
        }
        Ok(value)
    }
    pub fn matches(&self, v: &Value) -> bool {
        if v["verification"]["active"] != true {
            return false;
        }
        if !self.region.is_empty()
            && !v["region"]
                .as_str()
                .unwrap_or("")
                .to_lowercase()
                .contains(&self.region.to_lowercase())
        {
            return false;
        }
        if !self.timezones.is_empty()
            && !company_time::offset(v).is_some_and(|h| self.timezones.contains(&h))
        {
            return false;
        }
        if !self.keywords.is_empty()
            && !format!("{} {} {}", v["name"], v["description"], v["segment"])
                .to_lowercase()
                .contains(&self.keywords.to_lowercase())
        {
            return false;
        }
        if self.require_contacts
            && v["phones"].as_array().is_none_or(|a| a.is_empty())
            && v["emails"].as_array().is_none_or(|a| a.is_empty())
        {
            return false;
        }
        if !self.sectors.is_empty() {
            let mut codes = vec![v["verification"]["okved"].as_str().unwrap_or("")];
            if !self.primary_activity_only
                && let Some(extra) = v["verification"]["additional_okved"].as_array()
            {
                codes.extend(extra.iter().filter_map(Value::as_str))
            }
            if !codes
                .iter()
                .any(|code| self.sectors.iter().any(|s| code.starts_with(s)))
            {
                return false;
            }
        }
        let revenue = if let Some(year) = self.report_year {
            amount(&v["financial"]["reports"][year.to_string()]["2110"])
        } else {
            amount(&v["financial"]["revenue_rub"])
        };
        for group in [&self.revenue_segments, &self.turnover_segments] {
            if !group.is_empty()
                && !revenue.is_some_and(|n| {
                    group.iter().any(|s| {
                        let (min, max) = range(s);
                        n >= min && max.is_none_or(|m| n < m)
                    })
                })
            {
                return false;
            }
        }
        true
    }
}
fn amount(v: &Value) -> Option<f64> {
    v.as_f64()
        .or_else(|| v.as_str().and_then(|s| s.parse().ok()))
        .or_else(|| v["СумОтч"].as_f64())
}
fn range(s: &str) -> (f64, Option<f64>) {
    match s {
        "10-100" => (10e6, Some(100e6)),
        "100-500" => (100e6, Some(500e6)),
        "500-1000" => (500e6, Some(1e9)),
        _ => (1e9, None),
    }
}
pub struct Runtime {
    pub coordinator: Coordinator,
    pub cancellations: Mutex<HashMap<Uuid, Cancellation>>,
    pub checko_gate: Mutex<Instant>,
    pub checko_slots: Semaphore,
    pub inn_locks: Vec<Mutex<()>>,
    pub metadata: Mutex<Value>,
    pub listing_gate: Mutex<()>,
    pub source_pacing: Vec<Mutex<Instant>>,
}
impl Default for Runtime {
    fn default() -> Self {
        Self {
            coordinator: Coordinator::new(4).expect("Параллелизм поиска"),
            cancellations: Mutex::new(HashMap::new()),
            checko_gate: Mutex::new(Instant::now() - Duration::from_secs(1)),
            checko_slots: Semaphore::new(4),
            inn_locks: (0..64).map(|_| Mutex::new(())).collect(),
            metadata: Mutex::new(json!({})),
            listing_gate: Mutex::new(()),
            source_pacing: (0..64)
                .map(|_| Mutex::new(Instant::now() - Duration::from_secs(1)))
                .collect(),
        }
    }
}
pub async fn route(c: &RequestContext<'_>) -> Result<Option<Response>> {
    let u = c.actor;
    let b = &c.body;
    let mut tx = db::transaction(&c.host.pool, Some(u), false).await?;
    let response = match (c.method, c.parts.as_slice()) {
        ("GET", ["api", "search", "preferences"]) => {
            let v: Option<Value> =
                sqlx::query_scalar("SELECT criteria FROM search_preferences WHERE employee_id=$1")
                    .bind(u.id)
                    .fetch_optional(&mut *tx)
                    .await?;
            json!({"criteria":v.unwrap_or_else(||json!(Criteria::default()))})
        }
        ("PUT", ["api", "search", "preferences"]) => {
            let criteria = Criteria::parse(b.clone())?;
            let v = json!(criteria);
            sqlx::query("INSERT INTO search_preferences(employee_id,criteria) VALUES($1,$2) ON CONFLICT(employee_id) DO UPDATE SET criteria=excluded.criteria").bind(u.id).bind(&v).execute(&mut *tx).await?;
            db::audit(&mut tx, u.id, "search.criteria", "", v.clone(), c.ip).await?;
            json!({"criteria":v})
        }
        ("POST", ["api", "search"]) => {
            let criteria =
                Criteria::parse(b.get("criteria").cloned().unwrap_or_else(|| json!({})))?;
            let target = b["target"]
                .as_i64()
                .unwrap_or(criteria.company_count as i64);
            if !(1..=50).contains(&target) {
                return Err(ApiError::invalid(
                    "За поиск можно найти от 1 до 50 компаний",
                ));
            }
            let row:Value=sqlx::query_scalar("INSERT INTO jobs(id,employee_id,target,criteria,message) VALUES($1,$2,$3,$4,'В очереди хоста') RETURNING to_jsonb(jobs)").bind(Uuid::new_v4()).bind(u.id).bind(target as i32).bind(json!(criteria)).fetch_one(&mut *tx).await?;
            db::audit(
                &mut tx,
                u.id,
                "search.start",
                row["id"].as_str().unwrap_or(""),
                b.clone(),
                c.ip,
            )
            .await?;
            row
        }
        ("POST", ["api", "search", id, "stop"]) => {
            let id = crm::path_id(id)?;
            sqlx::query_scalar::<_,Uuid>("UPDATE jobs SET stop_requested=true,status='stopped',finished_at=now(),message='Поиск остановлен; найденные компании сохранены' WHERE id=$1 AND employee_id=$2 AND status IN ('queued','running') RETURNING id").bind(id).bind(u.id).fetch_one(&mut *tx).await?;
            if let Some(cancel) = c.host.search.cancellations.lock().await.get(&id) {
                cancel.cancel()
            }
            db::audit(
                &mut tx,
                u.id,
                "search.stop",
                &id.to_string(),
                json!({}),
                c.ip,
            )
            .await?;
            json!({"ok":true})
        }
        ("GET", ["api", "search", "schedules"]) => {
            let rows:Vec<Value>=sqlx::query_scalar("SELECT to_jsonb(r) FROM (SELECT s.*,j.status AS job_status,j.added,j.message FROM search_schedules s LEFT JOIN jobs j ON j.id=s.job_id WHERE s.employee_id=$1 ORDER BY s.run_at DESC LIMIT 100) r").bind(u.id).fetch_all(&mut *tx).await?;
            json!({"schedules":rows})
        }
        ("POST", ["api", "search", "schedules"]) => {
            let at = DateTime::parse_from_rfc3339(b["run_at"].as_str().unwrap_or(""))
                .map_err(|_| ApiError::invalid("Нужно будущее время с часовым поясом"))?
                .with_timezone(&Utc);
            if at <= Utc::now() {
                return Err(ApiError::invalid("Укажите будущее время"));
            }
            let target = b["target"]
                .as_i64()
                .filter(|n| (1..=50).contains(n))
                .ok_or_else(|| ApiError::invalid("От 1 до 50 компаний"))?;
            let criteria = Criteria::parse(b["criteria"].clone())?;
            sqlx::query("SELECT pg_advisory_xact_lock(hashtext($1))")
                .bind(u.id.to_string())
                .execute(&mut *tx)
                .await?;
            let count: i64 = sqlx::query_scalar(
                "SELECT count(*) FROM search_schedules WHERE employee_id=$1 AND status='pending'",
            )
            .bind(u.id)
            .fetch_one(&mut *tx)
            .await?;
            if count >= 20 {
                return Err(ApiError::conflict("Можно запланировать до 20 поисков"));
            }
            let row:Value=sqlx::query_scalar("INSERT INTO search_schedules(id,employee_id,run_at,target,criteria) VALUES($1,$2,$3,$4,$5) RETURNING to_jsonb(search_schedules)").bind(Uuid::new_v4()).bind(u.id).bind(at).bind(target as i32).bind(json!(criteria)).fetch_one(&mut *tx).await?;
            db::audit(
                &mut tx,
                u.id,
                "search.schedule",
                row["id"].as_str().unwrap_or(""),
                b.clone(),
                c.ip,
            )
            .await?;
            row
        }
        ("DELETE", ["api", "search", "schedules", id]) => {
            let id = crm::path_id(id)?;
            sqlx::query_scalar::<_,Uuid>("UPDATE search_schedules SET status='cancelled' WHERE id=$1 AND employee_id=$2 AND status='pending' RETURNING id").bind(id).bind(u.id).fetch_optional(&mut *tx).await?.ok_or_else(||ApiError::conflict("Поиск уже запущен или отменён"))?;
            db::audit(
                &mut tx,
                u.id,
                "search.schedule_cancel",
                &id.to_string(),
                json!({}),
                c.ip,
            )
            .await?;
            json!({"ok":true})
        }
        ("GET", ["api", "search", "presets"]) => {
            let rows:Vec<Value>=sqlx::query_scalar("SELECT to_jsonb(p) FROM search_presets p WHERE employee_id=$1 ORDER BY created_at DESC").bind(u.id).fetch_all(&mut *tx).await?;
            json!({"presets":rows})
        }
        ("POST", ["api", "search", "presets"]) => {
            let criteria = Criteria::parse(b["criteria"].clone())?;
            sqlx::query_scalar::<_,Value>("INSERT INTO search_presets(id,employee_id,name,criteria) VALUES($1,$2,$3,$4) RETURNING to_jsonb(search_presets)").bind(Uuid::new_v4()).bind(u.id).bind(db::text(b,"name",200,true)?).bind(json!(criteria)).fetch_one(&mut *tx).await?
        }
        ("DELETE", ["api", "search", "presets", id]) => {
            sqlx::query_scalar::<_, Uuid>(
                "DELETE FROM search_presets WHERE id=$1 AND employee_id=$2 RETURNING id",
            )
            .bind(crm::path_id(id)?)
            .bind(u.id)
            .fetch_one(&mut *tx)
            .await?;
            json!({"ok":true})
        }
        ("GET", ["api", "company", id, "financial"]) => {
            let id = crm::path_id(id)?;
            db::owned(&mut tx, u, id, false).await?;
            let v: Value =
                sqlx::query_scalar("SELECT payload->'financial' FROM companies WHERE id=$1")
                    .bind(id)
                    .fetch_one(&mut *tx)
                    .await?;
            json!({"financial":v})
        }
        ("POST", ["api", "search", "inn"]) => {
            let inn = db::text(b, "inn", 10, true)?;
            nexsearch_core::company::Inn::try_from(inn.clone()).map_err(ApiError::invalid)?;
            tx.commit().await?;
            let item = crate::verification::verify(c.host, &inn, false).await?;
            if item["verification"]["active"] != true {
                return Err(ApiError::invalid("Компания недействующая"));
            }
            let (ident, added) = ingest(c.host, item, Some(u.id), None).await?;
            let mut read = db::transaction(&c.host.pool, Some(u), false).await?;
            let owner: Uuid =
                sqlx::query_scalar("SELECT employee_id FROM employee_leads WHERE company_id=$1")
                    .bind(ident)
                    .fetch_one(&mut *read)
                    .await?;
            if owner != u.id {
                return Err(ApiError::conflict(
                    "Компания уже закреплена за другим сотрудником",
                ));
            }
            let item = crm::company(&mut read, ident).await?;
            db::audit(
                &mut read,
                u.id,
                "search.inn",
                &ident.to_string(),
                json!({"inn":inn,"added":added}),
                c.ip,
            )
            .await?;
            read.commit().await?;
            return Ok(Some(json_response(json!({"company":item,"added":added}))));
        }
        _ => return Ok(None),
    };
    tx.commit().await?;
    Ok(Some(json_response(response)))
}
pub async fn ingest(
    host: &Host,
    mut item: Value,
    owner: Option<Uuid>,
    job: Option<Uuid>,
) -> Result<(Uuid, bool)> {
    let inn = db::text(&item, "inn", 10, true)?;
    nexsearch_core::company::Inn::try_from(inn.clone()).map_err(ApiError::invalid)?;
    if item["verification"]["active"] != true {
        return Err(ApiError::invalid(
            "Компания не подтверждена как действующая",
        ));
    }
    item.as_object_mut()
        .ok_or_else(|| ApiError::invalid("Некорректная компания"))?
        .remove("contact_overrides");
    item["timezone_utc_offset"] = json!(company_time::offset(&item));
    let mut tx = db::transaction(&host.pool, None, true).await?;
    sqlx::query("SELECT pg_advisory_xact_lock(73142001)")
        .execute(&mut *tx)
        .await?;
    if let Some(job) = job {
        let available:bool=sqlx::query_scalar("SELECT status='running' AND NOT stop_requested AND added<target FROM jobs WHERE id=$1 FOR UPDATE").bind(job).fetch_one(&mut *tx).await?;
        if !available {
            return Err(ApiError::conflict("Поиск остановлен или выполнен"));
        }
    }
    let existing: Option<Value> =
        sqlx::query_scalar("SELECT to_jsonb(c) FROM companies c WHERE inn=$1 FOR UPDATE")
            .bind(&inn)
            .fetch_optional(&mut *tx)
            .await?;
    let id = existing
        .as_ref()
        .and_then(|v| v["id"].as_str())
        .and_then(|s| Uuid::parse_str(s).ok())
        .unwrap_or_else(Uuid::new_v4);
    if let Some(old) = existing {
        let mut merged = old["payload"].clone();
        let obj = merged
            .as_object_mut()
            .ok_or_else(|| ApiError::invalid("Повреждена компания"))?;
        for (k, v) in item.as_object().unwrap() {
            if k == "contact_overrides" {
                continue;
            }
            if ["phones", "emails", "notes", "contact_sources", "categories"].contains(&k.as_str())
            {
                let mut values = obj[k].as_array().cloned().unwrap_or_default();
                for value in v.as_array().into_iter().flatten() {
                    if !values.contains(value) {
                        values.push(value.clone())
                    }
                }
                obj.insert(k.clone(), values.into());
            } else if !v.is_null() {
                obj.insert(k.clone(), v.clone());
            }
        }
        item = merged;
        sqlx::query("UPDATE companies SET payload=$1,active_verified=true,verified_at=now(),updated_at=now() WHERE id=$2").bind(&item).bind(id).execute(&mut *tx).await?;
    } else {
        sqlx::query("INSERT INTO companies(id,name,inn,payload,active_verified,verified_at) VALUES($1,$2,$3,$4,true,now())").bind(id).bind(db::text(&item,"name",2000,true)?).bind(&inn).bind(&item).execute(&mut *tx).await?;
    }
    sqlx::query(
        "INSERT INTO company_identities(identity,company_id) VALUES($1,$2) ON CONFLICT DO NOTHING",
    )
    .bind(format!("inn:{inn}"))
    .bind(id)
    .execute(&mut *tx)
    .await?;
    let assigned = if let Some(owner) = owner {
        sqlx::query_scalar::<_,Uuid>("INSERT INTO employee_leads(company_id,employee_id,job_id) VALUES($1,$2,$3) ON CONFLICT(company_id) DO NOTHING RETURNING company_id").bind(id).bind(owner).bind(job).fetch_optional(&mut *tx).await?.is_some()
    } else {
        false
    };
    if let Some(job) = job {
        sqlx::query("UPDATE jobs SET added=added+$1,duplicates=duplicates+$2 WHERE id=$3")
            .bind(i32::from(assigned))
            .bind(i32::from(!assigned))
            .bind(job)
            .execute(&mut *tx)
            .await?;
    }
    tx.commit().await?;
    Ok((id, assigned))
}
async fn claim_cache(host: &Host, owner: Uuid, job: Uuid, criteria: &Criteria) -> Result<usize> {
    let mut tx = db::transaction(&host.pool, None, true).await?;
    let remaining:i32=sqlx::query_scalar("SELECT GREATEST(target-added,0) FROM jobs WHERE id=$1 AND status='running' AND NOT stop_requested FOR UPDATE").bind(job).fetch_optional(&mut *tx).await?.unwrap_or(0);
    if remaining == 0 {
        return Ok(0);
    }
    let mut query = QueryBuilder::new(
        "SELECT id,payload FROM companies c WHERE state='ready' AND active_verified AND inn IS NOT NULL AND verified_at>now()-interval '24 hours' AND NOT EXISTS(SELECT 1 FROM employee_leads l WHERE l.company_id=c.id)",
    );
    if !criteria.region.is_empty() {
        query
            .push(" AND payload->>'region' ILIKE ")
            .push_bind(format!("%{}%", criteria.region));
    }
    if !criteria.timezones.is_empty() {
        query
            .push(" AND (payload->>'timezone_utc_offset')::int=ANY(")
            .push_bind(&criteria.timezones)
            .push(")");
    }
    if !criteria.keywords.is_empty() {
        query
            .push(" AND concat_ws(' ',name,payload->>'segment',payload->>'description') ILIKE ")
            .push_bind(format!("%{}%", criteria.keywords));
    }
    if !criteria.sectors.is_empty() {
        query
            .push(" AND (left(payload#>>'{verification,okved}',2)=ANY(")
            .push_bind(&criteria.sectors)
            .push(")");
        if !criteria.primary_activity_only {
            query.push(" OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(COALESCE(payload#>'{verification,additional_okved}','[]')) a WHERE left(a.value,2)=ANY(").push_bind(&criteria.sectors).push("))");
        }
        query.push(")");
    }
    // Reservation is the UNIQUE(company_id) insert below. Locking an entire
    // candidate page here made other employees see an empty cache and finish
    // with zero leads while the first search held all company row locks.
    // A common order also prevents conflicting reservations from forming cycles.
    query.push(" ORDER BY created_at,id LIMIT 1000");
    let rows: Vec<(Uuid, Value)> = query.build_query_as().fetch_all(&mut *tx).await?;
    let mut count = 0;
    for (id, item) in rows {
        if count >= remaining as usize {
            break;
        }
        if !criteria.matches(&item) {
            continue;
        }
        if sqlx::query_scalar::<_,Uuid>("INSERT INTO employee_leads(company_id,employee_id,job_id) VALUES($1,$2,$3) ON CONFLICT(company_id) DO NOTHING RETURNING company_id").bind(id).bind(owner).bind(job).fetch_optional(&mut *tx).await?.is_some(){count+=1}
    }
    sqlx::query("UPDATE jobs SET added=added+$1 WHERE id=$2")
        .bind(count as i32)
        .bind(job)
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;
    Ok(count)
}
pub async fn run_scheduler(host: Arc<Host>) {
    loop {
        if tick(&host).await.is_err() {
            tracing::warn!("Очередь поиска временно недоступна; повторим проверку");
        }
        tokio::time::sleep(Duration::from_secs(1)).await;
    }
}
async fn paused(host: &Host) -> bool {
    if let Ok(bytes) = tokio::fs::read(host.config.update_dir.join("maintenance.json")).await
        && bytes.len() < 16384
        && let Ok(value) = serde_json::from_slice::<Value>(&bytes)
        && value["expires_at"]
            .as_f64()
            .is_some_and(|t| t > chrono::Utc::now().timestamp() as f64)
    {
        return true;
    }
    false
}
async fn tick(host: &Arc<Host>) -> Result<()> {
    if paused(host).await {
        return Ok(());
    }
    let mut tx = db::transaction(&host.pool, None, true).await?;
    sqlx::query("SELECT pg_advisory_xact_lock(73142004)")
        .execute(&mut *tx)
        .await?;
    if paused(host).await {
        return Ok(());
    }
    sqlx::query("WITH due AS (SELECT s.* FROM search_schedules s JOIN employees e ON e.id=s.employee_id WHERE s.status='pending' AND s.run_at<=now() AND e.active AND e.approved AND NOT EXISTS(SELECT 1 FROM jobs j WHERE j.employee_id=s.employee_id AND j.status IN ('queued','running')) ORDER BY s.run_at LIMIT 1 FOR UPDATE OF s SKIP LOCKED), inserted AS (INSERT INTO jobs(id,employee_id,target,criteria,message) SELECT gen_random_uuid(),employee_id,target,criteria,'По расписанию' FROM due RETURNING *) UPDATE search_schedules s SET status='queued',job_id=j.id FROM inserted j WHERE s.id IN(SELECT id FROM due) AND s.employee_id=j.employee_id").execute(&mut *tx).await?;
    let slots = 4usize.saturating_sub(host.search.cancellations.lock().await.len());
    if slots == 0 {
        tx.commit().await?;
        return Ok(());
    }
    let jobs:Vec<Value>=sqlx::query_scalar("WITH next AS(SELECT j.id FROM jobs j JOIN employees e ON e.id=j.employee_id WHERE j.status='queued' AND NOT j.stop_requested AND e.active AND e.approved ORDER BY j.created_at LIMIT $1 FOR UPDATE OF j SKIP LOCKED) UPDATE jobs j SET status='running',message='Поиск компаний' FROM next WHERE j.id=next.id RETURNING to_jsonb(j)").bind(slots as i64).fetch_all(&mut *tx).await?;
    tx.commit().await?;
    for job in jobs {
        let id = db::id(&job, "id")?;
        let owner = db::id(&job, "employee_id")?;
        let cancel = Cancellation::default();
        host.search
            .cancellations
            .lock()
            .await
            .insert(id, cancel.clone());
        let host = host.clone();
        tokio::spawn(async move {
            let result = host
                .search
                .coordinator
                .run(owner, Duration::from_secs(900), cancel, async {
                    process(&host, id, owner, &job)
                        .await
                        .map_err(|e| JobError::Failed(e.1))
                })
                .await;
            let message = match result {
                Ok(()) => "Поиск завершён".to_owned(),
                Err(JobError::Cancelled) => "Поиск остановлен".into(),
                Err(JobError::TimedOut) => {
                    "Истекло время поиска; найденные компании сохранены".into()
                }
                Err(JobError::Failed(message)) => message,
                _ => "Поиск не завершён".into(),
            };
            if let Ok(mut tx) = db::transaction(&host.pool, None, true).await {
                let _=sqlx::query("UPDATE jobs SET status=CASE WHEN added>=target THEN 'completed' WHEN stop_requested THEN 'stopped' ELSE 'partial' END,finished_at=now(),message=$1 WHERE id=$2 AND status='running'").bind(message).bind(id).execute(&mut *tx).await;
                let _=sqlx::query("UPDATE scraper_candidates SET state='pending' WHERE last_attempt=$1 AND state='processing'").bind(id).execute(&mut *tx).await;
                let _ = tx.commit().await;
            }
            host.search.cancellations.lock().await.remove(&id);
        });
    }
    Ok(())
}
async fn process(host: &Host, id: Uuid, owner: Uuid, job: &Value) -> Result<()> {
    let criteria = Criteria::parse(job["criteria"].clone())?;
    claim_cache(host, owner, id, &criteria).await?;
    let mut tx = db::transaction(&host.pool, None, true).await?;
    let remaining: i32 = sqlx::query_scalar("SELECT target-added FROM jobs WHERE id=$1")
        .bind(id)
        .fetch_one(&mut *tx)
        .await?;
    tx.commit().await?;
    if remaining <= 0 {
        return Ok(());
    }
    crate::crawler::collect(host, id, owner, &criteria).await
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn criteria_match_exact_revenue_boundaries_and_known_status() {
        let c = Criteria::parse(json!({"revenue_segments":["10-100"],"timezones":[3]})).unwrap();
        let mut item = json!({"region":"Москва","verification":{"active":true},"financial":{"revenue_rub":10_000_000}});
        assert!(c.matches(&item));
        item["financial"]["revenue_rub"] = 100_000_000.into();
        assert!(!c.matches(&item));
        item["verification"]["active"] = false.into();
        assert!(!c.matches(&item));
        assert!(Criteria::parse(json!({"company_count":51})).is_err());
    }
}
