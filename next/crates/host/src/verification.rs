use crate::{
    Host, db,
    error::{ApiError, Result},
    secrets,
};
use axum::http::StatusCode;
use serde_json::{Value, json};
use std::time::{Duration, Instant};
fn unavailable(message: &str) -> ApiError {
    ApiError(StatusCode::SERVICE_UNAVAILABLE, message.into())
}
pub async fn verify(host: &Host, inn: &str, force: bool) -> Result<Value> {
    if inn.len() != 10 {
        return Err(ApiError::invalid("Нужен ИНН организации из 10 цифр"));
    }
    nexsearch_core::company::Inn::try_from(inn.to_owned()).map_err(ApiError::invalid)?;
    let index = inn.bytes().fold(0usize, |a, b| (a * 31 + b as usize) % 64);
    let _lock = host.search.inn_locks[index].lock().await;
    let mut tx = db::transaction(&host.pool, None, true).await?;
    let cached: Option<Value> =
        sqlx::query_scalar("SELECT payload FROM company_checks WHERE inn=$1 AND expires_at>now()")
            .bind(inn)
            .fetch_optional(&mut *tx)
            .await?;
    tx.commit().await?;
    if !force && let Some(value) = cached {
        return Ok(value);
    }
    let company = request(host, "company", inn).await?;
    let mut finances = json!({});
    let mut warning = None;
    if company["data"]["Статус"]["Код"] == "001" {
        match request(host, "finances", inn).await {
            Ok(v) => finances = v,
            Err(error) => warning = Some(error.1),
        }
    }
    let mut result = normalize(&company, &finances, inn)?;
    if let Some(warning) = warning {
        result["financial"]["availability"] = "unavailable".into();
        result["financial"]["message"] = warning.into();
    }
    let mut tx = db::transaction(&host.pool, None, true).await?;
    sqlx::query("INSERT INTO company_checks(inn,payload,expires_at) VALUES($1,$2,now()+interval '24 hours') ON CONFLICT(inn) DO UPDATE SET payload=excluded.payload,checked_at=now(),expires_at=excluded.expires_at").bind(inn).bind(&result).execute(&mut *tx).await?;
    tx.commit().await?;
    Ok(result)
}
async fn request(host: &Host, method: &str, inn: &str) -> Result<Value> {
    if !host.config.source_access {
        return Err(unavailable(
            "Внешние источники отключены в настройках этого стенда",
        ));
    }
    let key = std::env::var("CHECKO_API_KEY")
        .ok()
        .filter(|s| !s.is_empty())
        .or_else(|| {
            secrets::load(&host.config)
                .ok()
                .and_then(|v| v["checko_api_key"].as_str().map(str::to_owned))
        })
        .ok_or_else(|| unavailable("На хосте не настроен API-ключ Чекко"))?;
    let _slot = host
        .search
        .checko_slots
        .acquire()
        .await
        .map_err(|_| unavailable("Проверка временно недоступна"))?;
    {
        let mut gate = host.search.checko_gate.lock().await;
        let elapsed = gate.elapsed();
        if elapsed < Duration::from_millis(350) {
            tokio::time::sleep(Duration::from_millis(350) - elapsed).await
        }
        *gate = Instant::now();
    }
    let response = host
        .client
        .get(format!("https://api.checko.ru/v2/{method}"))
        .query(&[("key", key.as_str()), ("inn", inn)])
        .timeout(Duration::from_secs(12))
        .send()
        .await
        .map_err(|_| unavailable("Чекко временно недоступен"))?;
    let status = response.status();
    let answer = bounded_json(response, 5 * 1024 * 1024).await?;
    *host.search.metadata.lock().await = json!({"today_request_count":answer["meta"]["today_request_count"],"balance":answer["meta"]["balance"]});
    if !status.is_success() || answer["meta"]["status"] != "ok" {
        return Err(unavailable(
            "Чекко не подтвердил запрос. Проверьте тариф, баланс и доступность сервиса в настройках хоста.",
        ));
    }
    Ok(answer)
}
pub async fn bounded_json(mut response: reqwest::Response, limit: usize) -> Result<Value> {
    if response.content_length().is_some_and(|n| n > limit as u64) {
        return Err(unavailable("Слишком большой ответ источника"));
    }
    let mut bytes = vec![];
    while let Some(chunk) = response
        .chunk()
        .await
        .map_err(|_| unavailable("Источник прервал ответ"))?
    {
        if bytes.len() + chunk.len() > limit {
            return Err(unavailable("Слишком большой ответ источника"));
        }
        bytes.extend_from_slice(&chunk)
    }
    serde_json::from_slice(&bytes).map_err(|_| unavailable("Источник вернул нечитаемый ответ"))
}
pub fn normalize(company: &Value, finances: &Value, inn: &str) -> Result<Value> {
    let d = &company["data"];
    if d["ИНН"] != inn {
        return Err(unavailable("Источник вернул другую организацию"));
    }
    let mut reports = json!({});
    if let Some(values) = finances["data"].as_object() {
        for (year, value) in values {
            if year
                .parse::<u32>()
                .is_ok_and(|y| (2011..=2100).contains(&y))
                && value.is_object()
            {
                reports[year] = value.clone();
            }
        }
    }
    let year = reports
        .as_object()
        .and_then(|r| r.keys().filter_map(|k| k.parse::<u32>().ok()).max());
    let latest = year
        .map(|y| reports[y.to_string()].clone())
        .unwrap_or_else(|| json!({}));
    let money = |code: &str| {
        let v = &latest[code];
        if v.is_object() {
            v["СумОтч"].clone()
        } else {
            v.clone()
        }
    };
    let list = |value: &Value| match value {
        Value::Array(v) => Value::Array(v.clone()),
        Value::String(v) if !v.is_empty() => json!([v]),
        _ => json!([]),
    };
    let transport = d["Налоги"]["СведУпл"]
        .as_array()
        .and_then(|a| {
            a.iter().find(|v| {
                v["Наим"]
                    .as_str()
                    .unwrap_or("")
                    .to_lowercase()
                    .contains("транспорт")
            })
        })
        .map(|v| v["Сумма"].clone())
        .unwrap_or(Value::Null);
    let source = format!(
        "https://checko.ru/company/{}",
        d["ОГРН"].as_str().unwrap_or(inn)
    );
    let name = d["НаимСокр"]
        .as_str()
        .or(d["НаимПолн"].as_str())
        .ok_or_else(|| unavailable("Источник не вернул название компании"))?;
    let liquid = d["Ликвид"]["Дата"].as_str().is_some_and(|s| !s.is_empty());
    Ok(
        json!({"inn":inn,"name":name,"ogrn":d["ОГРН"],"kpp":d["КПП"],"region":d["Регион"]["Наим"].as_str().unwrap_or(""),"legal_address":d["ЮрАдрес"]["АдресРФ"].as_str().unwrap_or(""),"registration_date":d["ДатаРег"],"status":d["Статус"]["Наим"].as_str().unwrap_or("Не проверен"),"segment":d["ОКВЭД"]["Наим"].as_str().unwrap_or(""),"phones":list(&d["Контакты"]["Тел"]),"emails":list(&d["Контакты"]["Емэйл"]),"website":d["Контакты"]["ВебСайт"].as_str().unwrap_or(""),"source":"Чекко / ФНС","source_url":source,"status_source":source,"verification":{"active":d["Статус"]["Код"]=="001"&&!liquid,"status_code":d["Статус"]["Код"],"okved":d["ОКВЭД"]["Код"].as_str().unwrap_or(""),"additional_okved":d["ОКВЭДДоп"].as_array().into_iter().flatten().filter_map(|v|v["Код"].as_str()).collect::<Vec<_>>(),"source":"Чекко / ФНС","source_url":source,"checked_at":chrono::Utc::now()},"financial":{"year":year,"revenue_rub":money("2110"),"profit_rub":money("2400"),"assets_rub":money("1600"),"capital_rub":d["УстКап"]["Сумма"],"transport_tax_rub":transport,"tax_year":d["Налоги"]["СведУплГод"],"reports":reports,"report_links":finances["bo.nalog.ru"]["Отчет"],"source":"Чекко / ФНС","currency":"RUB","availability":if year.is_some(){"complete"}else{"not_published"},"message":if year.is_some(){""}else{"Финансовая отчётность не опубликована или недоступна по тарифу"}},"checko_company":d}),
    )
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn provider_never_substitutes_organization_or_treats_unknown_as_active() {
        let d = json!({"data":{"ИНН":"7700000016","НаимСокр":"Компания","Статус":{"Код":"001"},"Налоги":{"СведУпл":[{"Наим":"Транспортный налог","Сумма":100}]}}});
        assert!(normalize(&d, &json!({}), "7700000023").is_err());
        let result = normalize(
            &d,
            &json!({"data":{"2025":{"2110":10_000_000}}}),
            "7700000016",
        )
        .unwrap();
        assert_eq!(result["financial"]["revenue_rub"], 10_000_000);
        assert_eq!(result["financial"]["transport_tax_rub"], 100);
        assert_eq!(result["verification"]["active"], true);
    }
}
