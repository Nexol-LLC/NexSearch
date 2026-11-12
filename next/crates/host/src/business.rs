//! Shared carrier directory, personal loading plans and exact VAT arithmetic.
use crate::{
    RequestContext,
    crm::path_id,
    db,
    error::{ApiError, Result},
    json_response, secrets,
};
use axum::response::Response;
use rust_decimal::{Decimal, RoundingStrategy};
use serde_json::{Value, json};
use std::str::FromStr;
use uuid::Uuid;
fn decimal(b: &Value, key: &str) -> Result<Decimal> {
    let s = b[key]
        .as_str()
        .map(str::to_owned)
        .unwrap_or_else(|| b[key].to_string());
    Decimal::from_str(&s).map_err(|_| ApiError::invalid(format!("Проверьте число: {key}")))
}
pub fn vat(b: &Value) -> Result<Value> {
    let amount = decimal(b, "amount")?;
    let rate = if b["rate"].is_null() {
        Decimal::from(22)
    } else {
        decimal(b, "rate")?
    };
    if amount < Decimal::ZERO
        || amount > Decimal::from(1_000_000_000_000u64)
        || ![0, 5, 7, 10, 20, 22]
            .into_iter()
            .any(|r| rate == Decimal::from(r))
    {
        return Err(ApiError::invalid("Проверьте сумму и ставку НДС"));
    }
    let round = |v: Decimal| v.round_dp_with_strategy(2, RoundingStrategy::MidpointAwayFromZero);
    let amount = round(amount);
    let mode = b["mode"].as_str().unwrap_or("add");
    let (net, tax, gross) = match mode {
        "without" => (amount, Decimal::ZERO, amount),
        "add" => {
            let tax = round(amount * rate / Decimal::from(100));
            (amount, tax, amount + tax)
        }
        "extract" => {
            let tax = round(amount * rate / (Decimal::from(100) + rate));
            (amount - tax, tax, amount)
        }
        _ => return Err(ApiError::invalid("Проверьте режим НДС")),
    };
    Ok(
        json!({"net":format!("{net:.2}"),"vat":format!("{tax:.2}"),"gross":format!("{gross:.2}"),"rate":rate.to_string(),"mode":mode}),
    )
}
fn vehicles(b: &Value) -> Result<Value> {
    let values = b["vehicles"]
        .as_array()
        .filter(|v| v.len() <= 100)
        .ok_or_else(|| ApiError::invalid("До 100 машин у перевозчика"))?;
    let kinds = [
        "tent",
        "refrigerator",
        "isotherm",
        "van",
        "flatbed",
        "lowbed",
        "container",
        "dump",
        "tank",
        "mega",
        "jumbo",
        "roadtrain",
        "other",
    ];
    let mut output = vec![];
    for v in values {
        let name = db::text(v, "name", 200, true)?;
        let kind = db::text(v, "kind", 50, true)?;
        if !kinds.contains(&kind.as_str()) {
            return Err(ApiError::invalid("Неизвестный тип машины"));
        }
        for key in ["payload_kg", "length_mm", "width_mm", "height_mm"] {
            if !v[key]
                .as_f64()
                .is_some_and(|n| n.is_finite() && n > 0. && n <= 1_000_000.)
            {
                return Err(ApiError::invalid(format!(
                    "Проверьте параметр машины: {key}"
                )));
            }
        }
        if !v["volume_m3"].is_null()
            && !v["volume_m3"]
                .as_f64()
                .is_some_and(|n| n > 0. && n <= 10000.)
        {
            return Err(ApiError::invalid("Проверьте объём"));
        }
        output.push(json!({"name":name,"kind":kind,"payload_kg":v["payload_kg"],"length_mm":v["length_mm"],"width_mm":v["width_mm"],"height_mm":v["height_mm"],"volume_m3":v["volume_m3"],"notes":db::text(v,"notes",2000,false)?}));
    }
    Ok(output.into())
}
pub async fn route(c: &RequestContext<'_>) -> Result<Option<Response>> {
    let u = c.actor;
    let b = &c.body;
    let mut tx = db::transaction(&c.host.pool, Some(u), false).await?;
    let response = match (c.method, c.parts.as_slice()) {
        ("POST", ["api", "vat"]) => vat(b)?,
        ("GET", ["api", "catalog"]) => {
            let mut v: Value =
                serde_json::from_str(include_str!("../../../data/catalog.json")).expect("Каталог");
            v["roles"] = json!({"logist":"Логист","manager":"Руководитель отдела","director":"Генеральный директор"});
            v["timezones"]=(2..=12).map(|h|json!({"offset":h,"label":if h==3{"UTC+3 · МСК".to_owned()}else{format!("UTC+{h} · МСК{:+}",h-3)}})).collect::<Vec<_>>().into();
            v
        }
        ("GET", ["api", "workspace", "catalog"]) => {
            json!({"order_statuses":{"new":"Новая","agreed":"Согласована","loading":"Погрузка","transit":"В пути","done":"Завершена","cancelled":"Отменена"},"vehicle_types":{"tent":"Тент","refrigerator":"Рефрижератор","flatbed":"Борт","lowbed":"Трал","other":"Другое"}})
        }
        ("GET", ["api", "carriers"]) => {
            let rows: Vec<Value> =
                sqlx::query_scalar("SELECT to_jsonb(c) FROM carriers c ORDER BY lower(name)")
                    .fetch_all(&mut *tx)
                    .await?;
            json!({"carriers":rows})
        }
        ("POST", ["api", "carriers", "save"]) => {
            let id = db::optional_id(b, "id")?;
            let name = db::text(b, "name", 500, true)?;
            let inn = db::text(b, "inn", 12, false)?;
            if !inn.is_empty() {
                nexsearch_core::company::Inn::try_from(inn.clone()).map_err(ApiError::invalid)?;
            }
            let fleet = vehicles(b)?;
            let terms = db::text(b, "work_terms", 5000, false)?;
            let phone = db::text(b, "phone", 200, false)?;
            let email = db::text(b, "email", 300, false)?;
            if !email.is_empty() && (!email.contains('@') || email.chars().any(char::is_whitespace))
            {
                return Err(ApiError::invalid("Проверьте почту перевозчика"));
            }
            let mode = b["vat_mode"].as_str().unwrap_or("unknown");
            if !["unknown", "included", "without"].contains(&mode) {
                return Err(ApiError::invalid("Проверьте условия НДС"));
            }
            let rate = b["vat_rate"].as_f64();
            if rate.is_some_and(|v| !v.is_finite() || !(0.0..=100.0).contains(&v)) {
                return Err(ApiError::invalid("Проверьте ставку НДС"));
            }
            let mut record = json!({"id":id.unwrap_or_else(Uuid::new_v4),"name":name,"inn":inn,"phone":phone,"email":email,"work_terms":terms,"vat_mode":mode,"vat_rate":rate,"vehicles":fleet,"contacts":db::text(b,"contacts",5000,false)?,"routes":db::text(b,"routes",5000,false)?,"regions":db::text(b,"regions",3000,false)?,"description":db::text(b,"description",10000,false)?});
            let row: Value = if let Some(id) = id {
                let old: Value =
                    sqlx::query_scalar("SELECT to_jsonb(c) FROM carriers c WHERE id=$1 FOR UPDATE")
                        .bind(id)
                        .fetch_one(&mut *tx)
                        .await?;
                if old["revision"].as_i64() != Some(db::revision(b)? as i64) {
                    return Err(ApiError::conflict(
                        "Перевозчик уже изменён другим сотрудником",
                    ));
                }
                if b.get("inn").is_none() {
                    record["inn"] = old["inn"].clone()
                }
                sqlx::query_scalar("UPDATE carriers c SET name=r.name,inn=r.inn,phone=r.phone,email=r.email,work_terms=r.work_terms,vat_mode=r.vat_mode,vat_rate=r.vat_rate,vehicles=r.vehicles,contacts=r.contacts,routes=r.routes,regions=r.regions,description=r.description,updated_by=$2,updated_at=now(),revision=c.revision+1 FROM jsonb_populate_record(NULL::carriers,$1) r WHERE c.id=r.id RETURNING to_jsonb(c)").bind(record).bind(u.id).fetch_one(&mut *tx).await?
            } else {
                // Populate a complete row from defaults first; caller-controlled ownership and timestamps are never copied.
                sqlx::query_scalar("INSERT INTO carriers(id,name,inn,phone,email,work_terms,vat_mode,vat_rate,vehicles,contacts,routes,regions,description,created_by,updated_by) SELECT r.id,r.name,r.inn,r.phone,r.email,r.work_terms,r.vat_mode,r.vat_rate,r.vehicles,r.contacts,r.routes,r.regions,r.description,$2,$2 FROM jsonb_populate_record(NULL::carriers,$1) r RETURNING to_jsonb(carriers)").bind(record).bind(u.id).fetch_one(&mut *tx).await?
            };
            db::audit(
                &mut tx,
                u.id,
                if id.is_some() {
                    "carrier.update"
                } else {
                    "carrier.create"
                },
                row["id"].as_str().unwrap_or(""),
                json!({"name":row["name"]}),
                c.ip,
            )
            .await?;
            row
        }
        ("PUT", ["api", "carriers", id, "fleet"]) => {
            let id = path_id(id)?;
            let fleet = vehicles(b)?;
            let row:Value=sqlx::query_scalar("UPDATE carriers SET vehicles=$1,regions=$2,updated_by=$3,revision=revision+1,updated_at=now() WHERE id=$4 AND revision=$5 RETURNING to_jsonb(carriers)").bind(fleet).bind(db::text(b,"regions",3000,false)?).bind(u.id).bind(id).bind(db::revision(b)?).fetch_optional(&mut *tx).await?.ok_or_else(||ApiError::conflict("Автопарк изменён"))?;
            db::audit(
                &mut tx,
                u.id,
                "carrier.fleet",
                &id.to_string(),
                json!({}),
                c.ip,
            )
            .await?;
            row
        }
        ("POST", ["api", "carriers", "feedback"]) => {
            let rating = b["rating"]
                .as_i64()
                .filter(|v| (1..=5).contains(v))
                .ok_or_else(|| ApiError::invalid("Оценка от 1 до 5"))?;
            let row:Value=sqlx::query_scalar("INSERT INTO carrier_feedback(id,employee_id,carrier_id,order_id,rating,description) VALUES($1,$2,$3,$4,$5,$6) RETURNING to_jsonb(carrier_feedback)").bind(Uuid::new_v4()).bind(u.id).bind(db::id(b,"carrier_id")?).bind(db::optional_id(b,"order_id")?).bind(rating as i32).bind(db::text(b,"description",5000,true)?).fetch_one(&mut *tx).await?;
            db::audit(
                &mut tx,
                u.id,
                "carrier.feedback",
                row["id"].as_str().unwrap_or(""),
                json!({"rating":rating}),
                c.ip,
            )
            .await?;
            row
        }
        ("GET", ["api", "plans"]) => {
            let rows:Vec<Value>=sqlx::query_scalar("SELECT to_jsonb(r) FROM (SELECT id,name,employee_id,revision,updated_at FROM loading_plans WHERE employee_id=$1 ORDER BY updated_at DESC) r").bind(u.id).fetch_all(&mut *tx).await?;
            json!({"plans":rows})
        }
        ("GET", ["api", "plans", id]) => {
            sqlx::query_scalar::<_, Value>(
                "SELECT to_jsonb(p) FROM loading_plans p WHERE id=$1 AND employee_id=$2",
            )
            .bind(path_id(id)?)
            .bind(u.id)
            .fetch_one(&mut *tx)
            .await?
        }
        ("POST", ["api", "plans"]) | ("PUT", ["api", "plans", _]) => {
            let id = if c.parts.len() == 3 {
                Some(path_id(c.parts[2])?)
            } else {
                None
            };
            let name = db::text(b, "name", 200, true)?;
            if !b["project"].is_object() {
                return Err(ApiError::invalid("Проверьте схему погрузки"));
            }
            let row: Value = if let Some(id) = id {
                sqlx::query_scalar("UPDATE loading_plans SET name=$1,project=$2,revision=revision+1,updated_at=now() WHERE id=$3 AND employee_id=$4 AND revision=$5 RETURNING to_jsonb(loading_plans)").bind(name).bind(&b["project"]).bind(id).bind(u.id).bind(db::revision(b)?).fetch_optional(&mut *tx).await?.ok_or_else(||ApiError::conflict("План изменён или недоступен"))?
            } else {
                sqlx::query_scalar("INSERT INTO loading_plans(id,employee_id,name,project) VALUES($1,$2,$3,$4) RETURNING to_jsonb(loading_plans)").bind(Uuid::new_v4()).bind(u.id).bind(name).bind(&b["project"]).fetch_one(&mut *tx).await?
            };
            db::audit(
                &mut tx,
                u.id,
                if id.is_some() {
                    "plan.update"
                } else {
                    "plan.create"
                },
                row["id"].as_str().unwrap_or(""),
                json!({"name":row["name"]}),
                c.ip,
            )
            .await?;
            row
        }
        ("GET", ["api", "settings"]) => {
            if u.role != "director" {
                return Err(ApiError::denied(
                    "Настройки доступны генеральному директору",
                ));
            }
            let saved = secrets::load(&c.host.config)?;
            json!({"checko_configured":saved["checko_api_key"].as_str().is_some_and(|s|!s.is_empty()),"checko_usage":{},"version":env!("CARGO_PKG_VERSION")})
        }
        ("PATCH", ["api", "settings"]) => {
            if u.role != "director" {
                return Err(ApiError::denied(
                    "Настройки доступны генеральному директору",
                ));
            }
            let mut saved = secrets::load(&c.host.config)?;
            if b.get("checko_api_key").is_some() {
                saved["checko_api_key"] = db::text(b, "checko_api_key", 200, false)?.into();
                secrets::save(&c.host.config, &saved)?
            }
            db::audit(
                &mut tx,
                u.id,
                "host.settings",
                "",
                json!({"checko_key_changed":b.get("checko_api_key").is_some()}),
                c.ip,
            )
            .await?;
            json!({"checko_configured":saved["checko_api_key"].as_str().is_some_and(|s|!s.is_empty())})
        }
        _ => return Ok(None),
    };
    tx.commit().await?;
    Ok(Some(json_response(response)))
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn vat_keeps_kopecks_and_half_up_rounding() {
        assert_eq!(
            vat(&json!({"amount":"100.00","rate":"22","mode":"add"})).unwrap()["gross"],
            "122.00"
        );
        let extracted = vat(&json!({"amount":"122.00","rate":"22","mode":"extract"})).unwrap();
        assert_eq!(extracted["net"], "100.00");
        assert_eq!(extracted["vat"], "22.00");
        assert_eq!(
            vat(&json!({"amount":"0.05","rate":"10","mode":"add"})).unwrap()["vat"],
            "0.01"
        );
        assert!(vat(&json!({"amount":-1})).is_err());
    }
}
