//! Dated, employee-owned quotes. A calculator result is never a fabricated market rate.
use crate::{
    RequestContext, db,
    error::{ApiError, Result},
    json_response,
};
use axum::response::Response;
use chrono::{DateTime, Utc};
use rust_decimal::Decimal;
use serde_json::{Value, json};
use std::{collections::BTreeMap, str::FromStr};
fn date(v: &Value) -> Option<DateTime<Utc>> {
    v.as_str()
        .and_then(|s| DateTime::parse_from_rfc3339(s).ok())
        .map(|d| d.with_timezone(&Utc))
}
fn fresh(row: &Value) -> Value {
    let Some(at) = date(&row["as_of"]) else {
        return json!({"state":"undated","label":"Дата источника отсутствует","age_hours":null});
    };
    if row["date_precision"] == "unknown" {
        return json!({"state":"undated","label":"Дата источника отсутствует","age_hours":null});
    }
    let age = (Utc::now() - at).num_seconds() as f64 / 3600.0;
    let (state, label) = if age < -0.017 {
        ("invalid", "Дата источника в будущем")
    } else if date(&row["valid_until"]).is_some_and(|v| v <= Utc::now()) {
        ("expired", "Срок предложения истёк")
    } else if row["date_precision"] == "date" {
        match (Utc::now().date_naive() - at.date_naive()).num_days() {
            0 => ("fresh", "Данные за сегодня"),
            1 => ("previous_day", "Данные за предыдущий день"),
            _ => ("stale", "Устаревшие данные"),
        }
    } else if age <= 24.0 {
        ("fresh", "Актуальная ставка")
    } else {
        ("stale", "Устаревшая ставка")
    };
    json!({"state":state,"label":label,"age_hours":if row["date_precision"]=="date"{None}else{Some((age.max(0.0)*10.0).round()/10.0)}})
}
pub async fn route(c: &RequestContext<'_>) -> Result<Option<Response>> {
    if !matches!(c.parts.get(1), Some(&"routes" | &"rates")) {
        return Ok(None);
    }
    let mut tx = db::transaction(&c.host.pool, Some(c.actor), false).await?;
    let response = match (c.method, c.parts.as_slice()) {
        ("POST", ["api", "routes", "quotes"]) => {
            let b = &c.body;
            let origin = db::text(b, "origin", 200, true)?;
            let destination = db::text(b, "destination", 200, true)?;
            let source = db::text(b, "source", 100, true)?;
            if !["VIP калькулятор", "Сигнал", "Перевозчик", "Вручную"].contains(&source.as_str())
            {
                return Err(ApiError::invalid("Выберите источник ставки"));
            }
            let as_of = date(&b["as_of"])
                .ok_or_else(|| ApiError::invalid("Укажите дату ставки с часовым поясом"))?;
            let until = if b["valid_until"].is_null() || b["valid_until"] == "" {
                None
            } else {
                Some(
                    date(&b["valid_until"])
                        .ok_or_else(|| ApiError::invalid("Некорректный срок действия"))?,
                )
            };
            if as_of > Utc::now() + chrono::Duration::seconds(60)
                || until.is_some_and(|v| v < as_of)
            {
                return Err(ApiError::invalid("Проверьте дату и срок действия ставки"));
            }
            let raw = b["amount_rub"]
                .as_str()
                .map(str::to_owned)
                .unwrap_or_else(|| b["amount_rub"].to_string());
            let amount =
                Decimal::from_str(&raw).map_err(|_| ApiError::invalid("Проверьте сумму ставки"))?;
            if amount < Decimal::ZERO || amount > Decimal::from(1_000_000_000) {
                return Err(ApiError::invalid("Проверьте сумму ставки"));
            }
            let vat = b["vat"].as_str().unwrap_or("unknown");
            let basis = b["pricing_basis"].as_str().unwrap_or("trip");
            if !["unknown", "included", "without"].contains(&vat)
                || !["trip", "km"].contains(&basis)
            {
                return Err(ApiError::invalid("Проверьте условия ставки"));
            }
            let rate = if b["vat_rate"].is_null() {
                None
            } else {
                Some(
                    b["vat_rate"]
                        .as_f64()
                        .filter(|v| v.is_finite() && *v >= 0.0 && *v <= 100.0)
                        .ok_or_else(|| ApiError::invalid("Проверьте ставку НДС"))?,
                )
            };
            let url = db::text(b, "source_url", 1000, false)?;
            if !url.is_empty()
                && !url::Url::parse(&url).is_ok_and(|u| {
                    matches!(u.scheme(), "http" | "https")
                        && u.host_str().is_some()
                        && u.username().is_empty()
                        && u.password().is_none()
                })
            {
                return Err(ApiError::invalid("Некорректная ссылка на источник"));
            }
            let kind = db::text(b, "vehicle_type", 50, true)?;
            if ![
                "tent",
                "refrigerator",
                "flatbed",
                "lowbed",
                "van",
                "container",
                "other",
                "mega",
                "jumbo",
                "roadtrain",
                "dump",
                "tank",
            ]
            .contains(&kind.as_str())
            {
                return Err(ApiError::invalid("Выберите тип автомобиля"));
            }
            let row:Value=sqlx::query_scalar("INSERT INTO route_quotes(id,employee_id,origin,destination,vehicle,source,amount_rub,vat,note,as_of,valid_until,source_url,vat_rate,vehicle_type,pricing_basis) VALUES($1,$2,$3,$4,$5,$6,$7::numeric,$8,$9,$10,$11,$12,$13,$14,$15) RETURNING to_jsonb(route_quotes)")
    .bind(uuid::Uuid::new_v4()).bind(c.actor.id).bind(origin).bind(destination).bind(db::text(b,"vehicle",200,false)?).bind(source).bind(amount.to_string()).bind(vat).bind(db::text(b,"note",1000,false)?).bind(as_of).bind(until).bind(url).bind(rate).bind(kind).bind(basis).fetch_one(&mut *tx).await?;
            db::audit(
                &mut tx,
                c.actor.id,
                "quote.create",
                row["id"].as_str().unwrap_or(""),
                json!({"as_of":row["as_of"],"source":row["source"]}),
                c.ip,
            )
            .await?;
            row
        }
        ("GET", ["api", "rates", "history"]) => {
            let from = c.query.get("origin").cloned().unwrap_or_default();
            let to = c.query.get("destination").cloned().unwrap_or_default();
            let kind = c.query.get("vehicle_type").cloned().unwrap_or_default();
            let mut rows:Vec<Value>=sqlx::query_scalar("SELECT to_jsonb(q)||jsonb_build_object('description',q.note) FROM route_quotes q WHERE employee_id=$1 AND ($2='' OR origin ILIKE '%'||$2||'%') AND ($3='' OR destination ILIKE '%'||$3||'%') AND ($4='' OR vehicle_type=$4) ORDER BY as_of DESC LIMIT 200").bind(c.actor.id).bind(&from).bind(&to).bind(&kind).fetch_all(&mut *tx).await?;
            let historical:Vec<Value>=sqlx::query_scalar("SELECT to_jsonb(q)||jsonb_build_object('origin',o.origin,'destination',o.destination,'carrier_name',ca.name) FROM order_quotes q JOIN shipment_orders o ON o.id=q.order_id LEFT JOIN carriers ca ON ca.id=q.carrier_id WHERE q.employee_id=$1 AND ($2='' OR o.origin ILIKE '%'||$2||'%') AND ($3='' OR o.destination ILIKE '%'||$3||'%') AND ($4='' OR q.vehicle_type=$4) ORDER BY q.as_of DESC LIMIT 200").bind(c.actor.id).bind(&from).bind(&to).bind(&kind).fetch_all(&mut *tx).await?;
            rows.extend(historical);
            rows.sort_by_key(|v| std::cmp::Reverse(date(&v["as_of"])));
            rows.truncate(200);
            for row in &mut rows {
                row["freshness"] = fresh(row);
            }
            json!({"quotes":rows,"automatic_market_provider":false,"message":"Подтверждённые предложения с датой источника. Данные калькуляторов сохраняются сотрудником; дата сохранения не заменяет дату ставки."})
        }
        ("GET", ["api", "routes"]) => {
            let rows:Vec<Value>=sqlx::query_scalar("SELECT jsonb_build_object('origin',origin,'destination',destination,'uses',count(*)) FROM employee_clients WHERE employee_id=$1 AND active AND origin<>'' AND destination<>'' GROUP BY origin,destination").bind(c.actor.id).fetch_all(&mut *tx).await?;
            let mut quotes:Vec<Value>=sqlx::query_scalar("SELECT to_jsonb(q) FROM route_quotes q WHERE employee_id=$1 ORDER BY as_of DESC LIMIT 200").bind(c.actor.id).fetch_all(&mut *tx).await?;
            let mut combined: BTreeMap<(String, String), i64> = BTreeMap::new();
            for row in rows {
                combined.insert(
                    (
                        row["origin"].as_str().unwrap_or("").into(),
                        row["destination"].as_str().unwrap_or("").into(),
                    ),
                    row["uses"].as_i64().unwrap_or(0),
                );
            }
            for q in &mut quotes {
                q["freshness"] = fresh(q);
                *combined
                    .entry((
                        q["origin"].as_str().unwrap_or("").into(),
                        q["destination"].as_str().unwrap_or("").into(),
                    ))
                    .or_default() += 1;
            }
            let mut routes=combined.into_iter().map(|((from,to),count)|json!({"origin":from,"destination":to,"uses":count,"quotes":quotes.iter().filter(|q|q["origin"]==from&&q["destination"]==to).take(5).collect::<Vec<_>>()})).collect::<Vec<_>>();
            routes.sort_by_key(|r| std::cmp::Reverse(r["uses"].as_i64()));
            routes.truncate(20);
            json!({"routes":routes,"quotes":quotes})
        }
        _ => return Ok(None),
    };
    tx.commit().await?;
    Ok(Some(json_response(response)))
}
