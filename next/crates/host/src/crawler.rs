//! Deterministic source collection; no generated companies or CAPTCHA bypass.
use crate::{
    Host, db,
    error::{ApiError, Result},
    search::{self, Criteria},
    verification,
};
use axum::http::StatusCode;
use regex::Regex;
use scraper::{Html, Selector};
use serde_json::{Value, json};
use std::{collections::HashSet, sync::OnceLock, time::Duration};
use uuid::Uuid;
fn unavailable(message: &str) -> ApiError {
    ApiError(StatusCode::SERVICE_UNAVAILABLE, message.into())
}
fn sources() -> Vec<Value> {
    serde_json::from_str(include_str!("../../../data/sources.json")).expect("Источники")
}
pub async fn collect(host: &Host, job: Uuid, owner: Uuid, criteria: &Criteria) -> Result<()> {
    if !host.config.source_access {
        return Err(unavailable(
            "Внешние источники отключены; доступные компании из базы уже проверены",
        ));
    }
    let key = crate::secrets::load(&host.config)?;
    if key["checko_api_key"].as_str().is_none_or(|s| s.is_empty())
        && std::env::var("CHECKO_API_KEY")
            .ok()
            .is_none_or(|s| s.is_empty())
    {
        return Err(unavailable(
            "Настройте API Чекко на хосте. Непроверенные компании не выдаются сотрудникам.",
        ));
    }
    let mut issues = vec![];
    let mut visited = 0;
    for source in sources() {
        let mut tx = db::transaction(&host.pool, None, true).await?;
        sqlx::query("INSERT INTO scraper_pages(url,source) VALUES($1,$2) ON CONFLICT DO NOTHING")
            .bind(source["url"].as_str().unwrap())
            .bind(source["id"].as_str().unwrap())
            .execute(&mut *tx)
            .await?;
        tx.commit().await?;
    }
    for round in 0..12 {
        for source in sources() {
            if done(host, job).await? {
                return Ok(());
            }
            match if source["id"] == "egrul" {
                discover_fns(host, &source, criteria, round + 1).await
            } else {
                discover_page(host, &source).await
            } {
                Ok(()) => {}
                Err(e) => {
                    progress(host, job, &source, &e.1).await?;
                    if issues.len() < 8 {
                        issues.push(format!(
                            "{}: {}",
                            source["name"].as_str().unwrap_or("Источник"),
                            e.1
                        ));
                    }
                }
            }
            for _ in 0..50 {
                if done(host, job).await? {
                    return Ok(());
                }
                let mut tx = db::transaction(&host.pool, None, true).await?;
                let candidate:Option<Value>=sqlx::query_scalar("WITH next AS(SELECT url FROM scraper_candidates WHERE source=$1 AND state IN ('pending','error') AND attempts<3 AND (last_attempt IS DISTINCT FROM $2) ORDER BY attempts,url LIMIT 1 FOR UPDATE SKIP LOCKED) UPDATE scraper_candidates c SET state='processing',attempts=attempts+1,last_attempt=$2 FROM next WHERE c.url=next.url RETURNING to_jsonb(c)").bind(source["id"].as_str().unwrap()).bind(job).fetch_optional(&mut *tx).await?;
                tx.commit().await?;
                let Some(candidate) = candidate else { break };
                visited += 1;
                let url = candidate["url"].as_str().unwrap();
                let outcome = async {
                    let mut item = if candidate["payload"]["inn"].as_str().is_some() {
                        candidate["payload"].clone()
                    } else {
                        let html = fetch(host, &source, url).await?;
                        parse_company(&html, url, &source)?
                    };
                    let verified = verification::verify(
                        host,
                        item["inn"]
                            .as_str()
                            .ok_or_else(|| ApiError::invalid("В карточке источника нет ИНН"))?,
                        false,
                    )
                    .await?;
                    for field in ["phones", "emails"] {
                        let mut values = verified[field].as_array().cloned().unwrap_or_default();
                        for v in item[field].as_array().into_iter().flatten() {
                            if !values.contains(v) {
                                values.push(v.clone())
                            }
                        }
                        item[field] = values.into();
                    }
                    for (k, v) in verified.as_object().unwrap() {
                        if !["phones", "emails", "source", "source_url"].contains(&k.as_str())
                            && !v.is_null()
                        {
                            item[k] = v.clone();
                        }
                    }
                    let mut tx = db::transaction(&host.pool, None, true).await?;
                    sqlx::query(
                        "UPDATE jobs SET scanned=scanned+1,verified=verified+1 WHERE id=$1",
                    )
                    .bind(job)
                    .execute(&mut *tx)
                    .await?;
                    tx.commit().await?;
                    if !criteria.matches(&item) {
                        let mut tx = db::transaction(&host.pool, None, true).await?;
                        sqlx::query("UPDATE jobs SET rejected=rejected+1 WHERE id=$1")
                            .bind(job)
                            .execute(&mut *tx)
                            .await?;
                        tx.commit().await?;
                        return Ok(());
                    }
                    search::ingest(host, item, Some(owner), Some(job)).await?;
                    Ok(())
                }
                .await;
                let mut tx = db::transaction(&host.pool, None, true).await?;
                let error = outcome.as_ref().err().map(|e: &ApiError| e.1.clone());
                sqlx::query("UPDATE scraper_candidates SET state=$1,error=$2 WHERE url=$3 AND last_attempt=$4").bind(if error.is_some(){"error"}else{"done"}).bind(error.as_deref().unwrap_or("")).bind(url).bind(job).execute(&mut *tx).await?;
                tx.commit().await?;
                if let Some(error) = error {
                    progress(host, job, &source, &error).await?;
                    if error.contains("Чекко") {
                        return Err(unavailable(&error));
                    }
                }
            }
        }
    }
    if done(host, job).await? {
        Ok(())
    } else {
        Err(unavailable(&format!(
            "Проверено карточек: {visited}. Не удалось набрать заданное количество. {}",
            issues.join("; ")
        )))
    }
}
async fn done(host: &Host, job: Uuid) -> Result<bool> {
    let mut tx = db::transaction(&host.pool, None, true).await?;
    let v: bool = sqlx::query_scalar(
        "SELECT stop_requested OR status<>'running' OR added>=target FROM jobs WHERE id=$1",
    )
    .bind(job)
    .fetch_one(&mut *tx)
    .await?;
    tx.commit().await?;
    Ok(v)
}
async fn progress(host: &Host, job: Uuid, source: &Value, message: &str) -> Result<()> {
    let mut tx = db::transaction(&host.pool, None, true).await?;
    sqlx::query(
        "UPDATE jobs SET source_progress=jsonb_set(source_progress,ARRAY[$1],$2,true) WHERE id=$3",
    )
    .bind(source["id"].as_str().unwrap())
    .bind(json!({"name":source["name"],"state":"error","message":message}))
    .bind(job)
    .execute(&mut *tx)
    .await?;
    tx.commit().await?;
    Ok(())
}
async fn fetch(host: &Host, source: &Value, url: &str) -> Result<String> {
    let seed = url::Url::parse(source["url"].as_str().unwrap())
        .map_err(|_| unavailable("Некорректный источник"))?;
    let mut url = url::Url::parse(url).map_err(|_| unavailable("Некорректная ссылка"))?;
    for _ in 0..4 {
        if !matches!(url.scheme(), "http" | "https")
            || url.host_str() != seed.host_str()
            || !url.username().is_empty()
            || url.password().is_some()
        {
            return Err(unavailable("Источник перенаправляет на другой сайт"));
        }
        let bucket = source["id"]
            .as_str()
            .unwrap()
            .bytes()
            .fold(0usize, |a, b| (a + b as usize) % 64);
        {
            let mut gate = host.search.source_pacing[bucket].lock().await;
            let elapsed = gate.elapsed();
            if elapsed < Duration::from_secs(1) {
                tokio::time::sleep(Duration::from_secs(1) - elapsed).await
            }
            *gate = std::time::Instant::now();
        }
        let mut response = host
            .client
            .get(url.clone())
            .header("User-Agent", "NexSearch/2.0 company directory collector")
            .timeout(Duration::from_secs(15))
            .send()
            .await
            .map_err(|_| unavailable("Источник не отвечает"))?;
        if response.status().is_redirection() {
            let location = response
                .headers()
                .get(reqwest::header::LOCATION)
                .and_then(|v| v.to_str().ok())
                .ok_or_else(|| unavailable("Некорректное перенаправление"))?;
            url = url
                .join(location)
                .map_err(|_| unavailable("Некорректная ссылка"))?;
            continue;
        }
        if !response.status().is_success() {
            return Err(unavailable(&format!(
                "Источник вернул HTTP {}",
                response.status().as_u16()
            )));
        }
        let mut bytes = vec![];
        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(|_| unavailable("Источник прервал ответ"))?
        {
            if bytes.len() + chunk.len() > 5 * 1024 * 1024 {
                return Err(unavailable("Страница источника слишком велика"));
            }
            bytes.extend_from_slice(&chunk)
        }
        return Ok(String::from_utf8_lossy(&bytes).into_owned());
    }
    Err(unavailable("Слишком много перенаправлений"))
}
async fn discover_page(host: &Host, source: &Value) -> Result<()> {
    let url = {
        let _listing = host.search.listing_gate.lock().await;
        let mut tx = db::transaction(&host.pool, None, true).await?;
        let next:Option<String>=sqlx::query_scalar("SELECT url FROM scraper_pages WHERE source=$1 AND (last_scan IS NULL OR last_scan<now()-interval '1 day') ORDER BY last_scan NULLS FIRST,url LIMIT 1 FOR UPDATE SKIP LOCKED").bind(source["id"].as_str().unwrap()).fetch_optional(&mut *tx).await?;
        let Some(url) = next else { return Ok(()) };
        sqlx::query("UPDATE scraper_pages SET last_scan=now() WHERE url=$1")
            .bind(&url)
            .execute(&mut *tx)
            .await?;
        tx.commit().await?;
        url
    };
    let html = fetch(host, source, &url).await?;
    let (cards, pages) = {
        let document = Html::parse_document(&html);
        let base = url::Url::parse(&url).map_err(|_| unavailable("Некорректная страница"))?;
        let selector = Selector::parse("a[href]").unwrap();
        let mut cards = HashSet::new();
        let mut pages = HashSet::new();
        for node in document.select(&selector) {
            let Some(href) = node.value().attr("href") else {
                continue;
            };
            let Ok(mut link) = base.join(href) else {
                continue;
            };
            link.set_fragment(None);
            if link.host_str() != base.host_str() || !matches!(link.scheme(), "http" | "https") {
                continue;
            }
            let p = link.path();
            let card = match source["id"].as_str().unwrap() {
                "fabricators" => p.starts_with("/proizvoditel/"),
                "productcenter" => p
                    .strip_prefix("/producers/")
                    .is_some_and(|s| s.chars().next().is_some_and(|c| c.is_ascii_digit())),
                "promreg" => p.starts_with("/kompanii/") && p.matches('/').count() > 2,
                "zavody" => {
                    p.starts_with("/factory/")
                        || p.starts_with("/zavod/")
                        || p.starts_with("/factories/")
                }
                "manufacturers" => p.starts_with("/company/"),
                "texzakaz" => p.starts_with("/company/") || p.starts_with("/proizvoditel/"),
                "madeinrussia" => {
                    p.starts_with("/ru/manufacturers/")
                        && p.trim_matches('/').split('/').count() > 2
                }
                _ => false,
            };
            if card {
                cards.insert(link.to_string());
            } else if p.contains("/page/")
                || link
                    .query_pairs()
                    .any(|(k, _)| matches!(k.as_ref(), "page" | "p" | "page_num"))
            {
                pages.insert(link.to_string());
            }
        }
        (cards, pages)
    };
    let mut tx = db::transaction(&host.pool, None, true).await?;
    for card in cards.into_iter().take(1000) {
        sqlx::query(
            "INSERT INTO scraper_candidates(url,source) VALUES($1,$2) ON CONFLICT DO NOTHING",
        )
        .bind(card)
        .bind(source["id"].as_str().unwrap())
        .execute(&mut *tx)
        .await?;
    }
    for page in pages.into_iter().take(100) {
        sqlx::query("INSERT INTO scraper_pages(url,source) VALUES($1,$2) ON CONFLICT DO NOTHING")
            .bind(page)
            .bind(source["id"].as_str().unwrap())
            .execute(&mut *tx)
            .await?;
    }
    tx.commit().await?;
    Ok(())
}
fn parse_company(html: &str, url: &str, source: &Value) -> Result<Value> {
    static INN: OnceLock<Regex> = OnceLock::new();
    let regex = INN.get_or_init(|| {
        Regex::new(r#"(?i)(?:инн|taxid|"inn")\D{0,40}([0-9]{10})(?:\D|$)"#).unwrap()
    });
    let document = Html::parse_document(html);
    let text = document.root_element().text().collect::<Vec<_>>().join(" ");
    let inn = regex
        .captures_iter(&text)
        .filter_map(|c| c.get(1).map(|v| v.as_str().to_owned()))
        .find(|v| nexsearch_core::company::Inn::try_from(v.clone()).is_ok())
        .ok_or_else(|| ApiError::invalid("В карточке источника нет корректного ИНН"))?;
    let mut phones = vec![];
    let mut emails = vec![];
    for node in document.select(&Selector::parse("a[href]").unwrap()) {
        let value = node.value().attr("href").unwrap_or("");
        if let Some(value) = value.strip_prefix("tel:") {
            let v = value.trim().to_owned();
            if !phones.contains(&v) {
                phones.push(v)
            }
        }
        if let Some(value) = value.strip_prefix("mailto:") {
            let v = value.split('?').next().unwrap_or("").trim().to_owned();
            if !emails.contains(&v) {
                emails.push(v)
            }
        }
    }
    Ok(json!({"inn":inn,"phones":phones,"emails":emails,"source":source["name"],"source_url":url}))
}
async fn discover_fns(host: &Host, source: &Value, criteria: &Criteria, page: usize) -> Result<()> {
    let query = if !criteria.keywords.is_empty() {
        criteria.keywords.as_str()
    } else {
        ["завод", "фабрика", "комбинат", "производство"][(page - 1) % 4]
    };
    let page = ((page - 1) / 4 + 1).to_string();
    let response = host
        .client
        .post("https://egrul.nalog.ru/")
        .form(&[
            ("query", query),
            ("region", criteria.region.as_str()),
            ("page", page.as_str()),
            ("PreventChromeAutocomplete", ""),
        ])
        .timeout(Duration::from_secs(12))
        .send()
        .await
        .map_err(|_| unavailable("ЕГРЮЛ не отвечает"))?;
    let answer = verification::bounded_json(response, 1024 * 1024).await?;
    if answer["captchaRequired"] == true {
        return Err(unavailable(
            "ЕГРЮЛ запросил ручную капчу; автоматический запрос пропущен",
        ));
    }
    let ticket = answer["t"]
        .as_str()
        .filter(|v| {
            v.len() < 300
                && v.bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"-_".contains(&b))
        })
        .ok_or_else(|| unavailable("ЕГРЮЛ не вернул идентификатор поиска"))?;
    for _ in 0..8 {
        tokio::time::sleep(Duration::from_secs(1)).await;
        let response = host
            .client
            .get(format!("https://egrul.nalog.ru/search-result/{ticket}"))
            .timeout(Duration::from_secs(12))
            .send()
            .await
            .map_err(|_| unavailable("ЕГРЮЛ не отвечает"))?;
        let answer = verification::bounded_json(response, 5 * 1024 * 1024).await?;
        if answer["status"] == "wait" {
            continue;
        }
        if answer["status"] == "error" {
            return Err(unavailable("ЕГРЮЛ не завершил поиск"));
        }
        let mut tx = db::transaction(&host.pool, None, true).await?;
        for row in answer["rows"].as_array().into_iter().flatten() {
            let inn = row["i"].as_str().unwrap_or("");
            if row["k"] != "ul"
                || inn.len() != 10
                || nexsearch_core::company::Inn::try_from(inn.to_owned()).is_err()
            {
                continue;
            }
            let item = json!({"name":row["c"].as_str().or(row["n"].as_str()).unwrap_or(""),"inn":inn,"region":row["rn"].as_str().unwrap_or(""),"legal_address":row["a"].as_str().unwrap_or(""),"phones":[],"emails":[],"source":source["name"],"source_url":format!("https://egrul.nalog.ru/index.html?query={inn}")});
            sqlx::query("INSERT INTO scraper_candidates(url,source,payload) VALUES($1,'egrul',$2) ON CONFLICT(url) DO UPDATE SET payload=excluded.payload").bind(item["source_url"].as_str().unwrap()).bind(&item).execute(&mut *tx).await?;
        }
        tx.commit().await?;
        return Ok(());
    }
    Err(unavailable("ЕГРЮЛ не завершил поиск за отведённое время"))
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn extracts_only_labeled_valid_inn_not_random_phone_numbers() {
        let source = json!({"name":"Каталог"});
        let html = "<p>Телефон 7700000016</p><dl><dt>ИНН</dt><dd>7700000016</dd></dl><a href='mailto:a@example.org'>Почта</a>";
        let value = parse_company(html, "https://example.org/company", &source).unwrap();
        assert_eq!(value["inn"], "7700000016");
        assert_eq!(value["emails"][0], "a@example.org");
        assert!(
            parse_company("<p>Телефон 7700000016</p>", "https://example.org", &source).is_err()
        );
    }
}
