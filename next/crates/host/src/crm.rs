use crate::{
    RequestContext,
    db::{self, Tx},
    error::{ApiError, Result},
    json_response,
};
use axum::response::Response;
use serde_json::{Value, json};
use uuid::Uuid;
pub fn path_id(value: &str) -> Result<Uuid> {
    Uuid::parse_str(value).map_err(|_| ApiError::invalid("Некорректный идентификатор"))
}
const COMPANY: &str = "SELECT to_jsonb(r) FROM (SELECT co.id,co.payload,l.employee_id,l.status AS lead_status,l.called,l.call_note,l.called_at,l.revision,cl.stage,cl.revision AS stage_revision,cl.origin,cl.destination,cl.cargo,cl.weight_kg,COALESCE(cl.active,false) AS is_client,e.first_name AS owner_first_name,e.last_name AS owner_last_name FROM companies co JOIN employee_leads l ON l.company_id=co.id JOIN employees e ON e.id=l.employee_id LEFT JOIN employee_clients cl ON cl.company_id=co.id";
pub fn present(mut row: Value) -> Value {
    let mut payload = row
        .as_object_mut()
        .and_then(|o| o.remove("payload"))
        .unwrap_or_else(|| json!({}));
    payload["company_time"] = crate::company_time::display(&payload);
    if let Some(financial) = payload["financial"].as_object_mut() {
        financial.remove("reports");
        financial.remove("report_links");
    }
    if let Some(o) = payload.as_object_mut() {
        o.remove("checko_company");
        o.remove("timezone_utc_offset");
    }
    if payload["contact_overrides"].is_object() {
        payload["source_contacts"] = json!({"phones":payload["phones"],"emails":payload["emails"]});
        for key in ["phones", "emails"] {
            payload[key] = payload["contact_overrides"][key]
                .as_array()
                .cloned()
                .unwrap_or_default()
                .into()
        }
    }
    payload["contacts_revision"] = payload["contact_overrides"]["revision"]
        .as_i64()
        .unwrap_or(0)
        .into();
    if let (Some(p), Some(r)) = (payload.as_object_mut(), row.as_object()) {
        for (k, v) in r {
            p.insert(k.clone(), v.clone());
        }
    }
    payload
}
pub async fn company(tx: &mut Tx, id: Uuid) -> Result<Value> {
    let raw: Value = sqlx::query_scalar(&format!("{COMPANY} WHERE co.id=$1) r"))
        .bind(id)
        .fetch_one(&mut **tx)
        .await?;
    Ok(present(raw))
}
pub async fn route(c: &RequestContext<'_>) -> Result<Option<Response>> {
    if c.parts.first() != Some(&"api") {
        return Ok(None);
    }
    let user = c.actor;
    let b = &c.body;
    let mut tx = db::transaction(&c.host.pool, Some(user), false).await?;
    let response = match (c.method, c.parts.as_slice()) {
        ("GET", ["api", "state"]) => {
            let jobs:Vec<Value>=sqlx::query_scalar("SELECT to_jsonb(j) FROM (SELECT * FROM jobs WHERE employee_id=$1 ORDER BY created_at DESC LIMIT 5) j").bind(user.id).fetch_all(&mut *tx).await?;
            let found: i64 = if user.role == "director" {
                sqlx::query_scalar("SELECT count(*) FROM companies")
                    .fetch_one(&mut *tx)
                    .await?
            } else {
                sqlx::query_scalar("SELECT count(*) FROM employee_leads WHERE employee_id=$1")
                    .bind(user.id)
                    .fetch_one(&mut *tx)
                    .await?
            };
            let clients: i64 = sqlx::query_scalar(
                "SELECT count(*) FROM employee_clients WHERE employee_id=$1 AND active",
            )
            .bind(user.id)
            .fetch_one(&mut *tx)
            .await?;
            let carriers: i64 = sqlx::query_scalar("SELECT count(*) FROM carriers")
                .fetch_one(&mut *tx)
                .await?;
            let version:i64=sqlx::query_scalar("SELECT COALESCE(max(id),0) FROM audit_events WHERE $1 OR employee_id=$2 OR kind LIKE 'carrier.%' OR ($3 AND nexsearch_can_read(employee_id))").bind(user.role=="director").bind(user.id).bind(user.role=="manager").fetch_one(&mut *tx).await?;
            let sources: Value = serde_json::from_str(include_str!("../../../data/sources.json"))
                .expect("Каталог источников");
            json!({"employee":user,"jobs":jobs,"stats":{"found":found,"clients":clients,"carriers":carriers},"sources":sources,"version":format!("{version}:{}",serde_json::to_string(&jobs).unwrap_or_default())})
        }
        ("GET", ["api", "leads"]) => {
            let values: Vec<Value> = if user.role == "director" {
                sqlx::query_scalar("SELECT to_jsonb(r) FROM (SELECT co.id,co.payload,l.employee_id,l.status AS lead_status,l.called,l.call_note,l.called_at,l.revision,e.login AS assigned_to FROM companies co LEFT JOIN employee_leads l ON l.company_id=co.id LEFT JOIN employees e ON e.id=l.employee_id ORDER BY co.created_at DESC) r").fetch_all(&mut *tx).await?
            } else {
                sqlx::query_scalar(&format!(
                    "{COMPANY} WHERE l.employee_id=$1 ORDER BY l.created_at DESC) r"
                ))
                .bind(user.id)
                .fetch_all(&mut *tx)
                .await?
            };
            json!({"companies":values.into_iter().map(present).collect::<Vec<_>>()})
        }
        ("GET", ["api", "companies", id]) => company(&mut tx, path_id(id)?).await?,
        ("GET", ["api", "clients"]) | ("GET", ["api", "clients", "overview"]) => {
            let overview = c.parts.len() == 3;
            if overview && !user.leader() {
                return Err(ApiError::denied("Общий канбан доступен руководителю"));
            }
            let filter = c.query.get("employee_id").map(|v| path_id(v)).transpose()?;
            if let Some(target) = filter {
                let p = db::employee(&mut tx, target).await?;
                if !user.manages(&p) {
                    return Err(ApiError::missing());
                }
            }
            let sort = match c.query.get("order").map(String::as_str).unwrap_or("recent") {
                "recent" => "cl.created_at DESC,co.id",
                "stage" => "lower(cl.stage),co.name,co.id",
                "name" => "co.name,co.id",
                _ => return Err(ApiError::invalid("Неизвестная сортировка")),
            };
            let owner = if overview { filter } else { Some(user.id) };
            let rows:Vec<Value>=sqlx::query_scalar(&format!("{COMPANY} WHERE cl.active AND ($1::uuid IS NULL OR l.employee_id=$1) AND ($2::text IS NULL OR cl.stage=$2) ORDER BY {sort}) r")).bind(owner).bind(c.query.get("stage")).fetch_all(&mut *tx).await?;
            let owners: Vec<Value> = if overview {
                sqlx::query_scalar("SELECT to_jsonb(r) FROM (SELECT e.id,e.first_name,e.last_name,count(*) AS client_count FROM employee_clients cl JOIN employees e ON e.id=cl.employee_id WHERE cl.active GROUP BY e.id ORDER BY lower(e.last_name),lower(e.first_name)) r").fetch_all(&mut *tx).await?
            } else {
                vec![]
            };
            let mut stages:Vec<String>=sqlx::query_scalar("SELECT DISTINCT stage FROM employee_clients WHERE active AND ($1::uuid IS NULL OR employee_id=$1) ORDER BY stage").bind(owner).fetch_all(&mut *tx).await?;
            if stages.is_empty() {
                stages.push("В работе".into())
            }
            json!({"companies":rows.into_iter().map(present).collect::<Vec<_>>(),"owners":owners,"stages":stages})
        }
        ("GET", ["api", "clients", "stages"]) => {
            let mut stages: Vec<String> = sqlx::query_scalar(
                "SELECT name FROM workspace_stages WHERE employee_id=$1 ORDER BY position",
            )
            .bind(user.id)
            .fetch_all(&mut *tx)
            .await?;
            if stages.is_empty() {
                stages = [
                    "В работе",
                    "Связаться",
                    "Предложение",
                    "Договор",
                    "Завершено",
                ]
                .map(str::to_owned)
                .into()
            }
            for name in sqlx::query_scalar::<_,String>("SELECT DISTINCT stage FROM employee_clients WHERE employee_id=$1 AND active ORDER BY stage").bind(user.id).fetch_all(&mut *tx).await?{if !stages.contains(&name){stages.push(name)}}
            json!({"stages":stages})
        }
        ("PUT", ["api", "clients", "stages"]) => {
            let items = b["stages"]
                .as_array()
                .filter(|a| a.len() <= 40)
                .ok_or_else(|| ApiError::invalid("До 40 этапов"))?;
            let mut names = vec![];
            for item in items {
                let name = item
                    .as_str()
                    .ok_or_else(|| ApiError::invalid("Нужен текст этапа"))?
                    .trim();
                if name.chars().count() > 500 {
                    return Err(ApiError::invalid("Длинное название этапа"));
                }
                if !name.is_empty() && !names.contains(&name) {
                    names.push(name)
                }
            }
            sqlx::query("DELETE FROM workspace_stages WHERE employee_id=$1")
                .bind(user.id)
                .execute(&mut *tx)
                .await?;
            for (position, name) in names.iter().enumerate() {
                sqlx::query(
                    "INSERT INTO workspace_stages(employee_id,name,position) VALUES($1,$2,$3)",
                )
                .bind(user.id)
                .bind(name)
                .bind(position as i32)
                .execute(&mut *tx)
                .await?;
            }
            db::audit(
                &mut tx,
                user.id,
                "client.stages",
                "",
                json!({"stages":names}),
                c.ip,
            )
            .await?;
            json!({"stages":names})
        }
        ("PATCH", ["api", "leads", id]) => update_lead(c, &mut tx, path_id(id)?).await?,
        ("PATCH", ["api", "clients", id, "stage"]) => {
            let id = path_id(id)?;
            let stage = db::text(b, "stage", 500, false)?;
            let row:Value=sqlx::query_scalar("UPDATE employee_clients SET stage=$1,revision=revision+1,updated_at=now() WHERE company_id=$2 AND employee_id=$3 AND active AND revision=$4 RETURNING to_jsonb(employee_clients)").bind(&stage).bind(id).bind(user.id).bind(db::revision(b)?).fetch_optional(&mut *tx).await?.ok_or_else(||ApiError::conflict("Клиент изменён или недоступен"))?;
            db::audit(
                &mut tx,
                user.id,
                "client.stage",
                &id.to_string(),
                json!({"after":stage}),
                c.ip,
            )
            .await?;
            row
        }
        ("PATCH", ["api", "clients", id, "route"]) => {
            let id = path_id(id)?;
            let weight = b["weight_kg"].as_f64();
            if weight.is_some_and(|v| !v.is_finite() || !(0.0..=1_000_000.0).contains(&v)) {
                return Err(ApiError::invalid("Проверьте массу"));
            }
            let row:Value=sqlx::query_scalar("UPDATE employee_clients SET origin=$1,destination=$2,cargo=$3,weight_kg=$4::float8,revision=revision+1,updated_at=now() WHERE company_id=$5 AND employee_id=$6 AND active AND revision=$7 RETURNING to_jsonb(employee_clients)").bind(db::text(b,"origin",200,false)?).bind(db::text(b,"destination",200,false)?).bind(db::text(b,"cargo",500,false)?).bind(weight).bind(id).bind(user.id).bind(db::revision(b)?).fetch_optional(&mut *tx).await?.ok_or_else(||ApiError::conflict("Клиент изменён или недоступен"))?;
            db::audit(
                &mut tx,
                user.id,
                "client.route",
                &id.to_string(),
                b.clone(),
                c.ip,
            )
            .await?;
            row
        }
        ("GET", ["api", "contacts"]) => {
            let id = path_id(
                c.query
                    .get("company_id")
                    .ok_or_else(|| ApiError::invalid("Укажите компанию"))?,
            )?;
            db::owned(&mut tx, user, id, false).await?;
            let rows:Vec<Value>=sqlx::query_scalar("SELECT to_jsonb(r) FROM (SELECT * FROM company_contacts WHERE company_id=$1 ORDER BY name) r").bind(id).fetch_all(&mut *tx).await?;
            json!({"contacts":rows})
        }
        ("POST", ["api", "contacts"]) => {
            let id = db::id(b, "company_id")?;
            db::owned(&mut tx, user, id, true).await?;
            let row:Value=sqlx::query_scalar("INSERT INTO company_contacts(id,employee_id,company_id,name,position,phones,email,description) VALUES($1,$2,$3,$4,$5,$6,$7,$8) RETURNING to_jsonb(company_contacts)").bind(Uuid::new_v4()).bind(user.id).bind(id).bind(db::text(b,"name",200,true)?).bind(db::text(b,"position",200,false)?).bind(db::text(b,"phones",1000,false)?).bind(db::text(b,"email",300,false)?).bind(db::text(b,"description",3000,false)?).fetch_one(&mut *tx).await?;
            db::audit(
                &mut tx,
                user.id,
                "contact.create",
                row["id"].as_str().unwrap_or(""),
                b.clone(),
                c.ip,
            )
            .await?;
            row
        }
        ("PATCH", ["api", "leads", id, "contacts"]) => {
            let id = path_id(id)?;
            db::owned(&mut tx, user, id, true).await?;
            let payload: Value =
                sqlx::query_scalar("SELECT payload FROM companies WHERE id=$1 FOR UPDATE")
                    .bind(id)
                    .fetch_one(&mut *tx)
                    .await?;
            if payload["contact_overrides"]["revision"]
                .as_i64()
                .unwrap_or(0)
                != db::revision(b)? as i64
            {
                return Err(ApiError::conflict(
                    "Контакты изменены в другом окне. Откройте карточку заново.",
                ));
            }
            let phones = contacts(b, "phones", false)?;
            let emails = contacts(b, "emails", true)?;
            let updated = json!({"phones":phones,"emails":emails,"revision":db::revision(b)?+1,"updated_at":chrono::Utc::now()});
            sqlx::query("UPDATE companies SET payload=jsonb_set(payload,'{contact_overrides}',$1,true),updated_at=now() WHERE id=$2").bind(&updated).bind(id).execute(&mut *tx).await?;
            db::audit(
                &mut tx,
                user.id,
                "company.contacts",
                &id.to_string(),
                json!({"before":payload["contact_overrides"],"after":updated}),
                c.ip,
            )
            .await?;
            json!({"phones":phones,"emails":emails,"contacts_revision":updated["revision"]})
        }
        _ => return Ok(None),
    };
    tx.commit().await?;
    Ok(Some(json_response(response)))
}
fn contacts(b: &Value, key: &str, email: bool) -> Result<Vec<String>> {
    let values = b[key]
        .as_array()
        .filter(|v| v.len() <= 20)
        .ok_or_else(|| ApiError::invalid("До 20 контактов каждого вида"))?;
    let mut output = vec![];
    for value in values {
        let s = value.as_str().unwrap_or("").trim();
        if s.is_empty() {
            continue;
        }
        if s.chars().count() > 200 || s.chars().any(char::is_control) {
            return Err(ApiError::invalid("Проверьте контакт"));
        }
        let valid = if email {
            let parts: Vec<_> = s.split('@').collect();
            parts.len() == 2
                && !parts[0].is_empty()
                && parts[1].contains('.')
                && !parts[1].starts_with('.')
                && !parts[1].ends_with('.')
                && !s.chars().any(char::is_whitespace)
        } else {
            (7..=20).contains(&s.chars().filter(char::is_ascii_digit).count())
        };
        if !valid {
            return Err(ApiError::invalid(if email {
                "Проверьте адрес почты"
            } else {
                "Проверьте номер телефона"
            }));
        }
        if !output.iter().any(|v: &String| v == s) {
            output.push(s.to_owned())
        }
    }
    Ok(output)
}
async fn update_lead(c: &RequestContext<'_>, tx: &mut Tx, id: Uuid) -> Result<Value> {
    let user = c.actor;
    let b = &c.body;
    let status = db::text(b, "status", 20, true)?;
    if !["new", "processing", "refused", "taken"].contains(&status.as_str()) {
        return Err(ApiError::invalid("Неизвестный статус"));
    }
    let called = b["called"]
        .as_bool()
        .ok_or_else(|| ApiError::invalid("Укажите результат звонка"))?;
    let note = db::text(b, "call_note", 10000, false)?;
    if status == "taken" && !called {
        return Err(ApiError::invalid(
            "Чтобы взять клиента в работу, отметьте звонок",
        ));
    }
    let old:Value=sqlx::query_scalar("SELECT to_jsonb(l) FROM employee_leads l WHERE company_id=$1 AND employee_id=$2 FOR UPDATE").bind(id).bind(user.id).fetch_one(&mut **tx).await?;
    if old["revision"].as_i64() != Some(db::revision(b)? as i64) {
        return Err(ApiError::conflict(
            "Запись изменена. Откройте карточку заново.",
        ));
    }
    if called && (old["called"] != true || old["call_note"] != note) {
        sqlx::query("INSERT INTO call_records(id,employee_id,company_id,result,description,called_at) VALUES($1,$2,$3,$4,$5,now())").bind(Uuid::new_v4()).bind(user.id).bind(id).bind(match status.as_str(){"taken"=>"Взят в работу","refused"=>"Отказ","processing"=>"В обработке",_=>"Звонок"}).bind(&note).execute(&mut **tx).await?;
    }
    let row:Value=sqlx::query_scalar("UPDATE employee_leads SET status=$1,called=$2,call_note=$3,called_at=CASE WHEN $2 THEN COALESCE(called_at,now()) ELSE NULL END,revision=revision+1,updated_at=now() WHERE company_id=$4 AND employee_id=$5 RETURNING to_jsonb(employee_leads)").bind(&status).bind(called).bind(&note).bind(id).bind(user.id).fetch_one(&mut **tx).await?;
    if status == "taken" {
        sqlx::query("INSERT INTO employee_clients(company_id,employee_id) VALUES($1,$2) ON CONFLICT(company_id) DO UPDATE SET active=true,updated_at=now()").bind(id).bind(user.id).execute(&mut **tx).await?;
    } else {
        sqlx::query("UPDATE employee_clients SET active=false,updated_at=now() WHERE company_id=$1 AND employee_id=$2").bind(id).bind(user.id).execute(&mut **tx).await?;
    }
    db::audit(
        tx,
        user.id,
        &format!("lead.{status}"),
        &id.to_string(),
        json!({"before":old,"after":row}),
        c.ip,
    )
    .await?;
    Ok(row)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn manual_contacts_keep_sources_and_reject_unsafe_values() {
        let p = present(
            json!({"id":"a","payload":{"phones":["source"],"emails":[],"contact_overrides":{"phones":["+7 900 1234567"],"emails":[],"revision":2}}}),
        );
        assert_eq!(p["source_contacts"]["phones"][0], "source");
        assert_eq!(p["phones"][0], "+7 900 1234567");
        assert_eq!(p["contacts_revision"], 2);
        assert!(contacts(&json!({"emails":["a@b.org\nHeader"]}), "emails", true).is_err());
    }
}
