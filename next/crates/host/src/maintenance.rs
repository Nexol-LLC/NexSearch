//! Authenticated status and exports. No client-supplied commands or file paths.
use crate::{
    RequestContext, business, crm, db,
    error::{ApiError, Result},
    json_response, secrets,
};
use axum::{
    body::{Body, to_bytes},
    http::{StatusCode, header},
    response::{IntoResponse, Response},
};
use serde_json::{Value, json};
fn fail() -> ApiError {
    ApiError(
        StatusCode::SERVICE_UNAVAILABLE,
        "Не удалось подготовить выгрузку".into(),
    )
}
fn cell(value: &Value) -> String {
    match value {
        Value::Null => String::new(),
        Value::String(s) => s.clone(),
        Value::Array(items) => items.iter().map(cell).collect::<Vec<_>>().join("\n"),
        v => v.to_string(),
    }
}
async fn export(c: &RequestContext<'_>, path: Vec<&str>) -> Result<Response> {
    let request = RequestContext {
        host: c.host,
        actor: c.actor,
        method: "GET",
        parts: path,
        query: c.query.clone(),
        body: json!({}),
        ip: c.ip,
    };
    let response = if let Some(r) = crm::route(&request).await? {
        r
    } else {
        business::route(&request)
            .await?
            .ok_or_else(ApiError::missing)?
    };
    let bytes = to_bytes(response.into_body(), 64 * 1024 * 1024)
        .await
        .map_err(|_| fail())?;
    let data: Value = serde_json::from_slice(&bytes).map_err(|_| fail())?;
    let carriers = request.parts.get(1) == Some(&"carriers");
    let rows = data[if carriers { "carriers" } else { "companies" }]
        .as_array()
        .ok_or_else(fail)?
        .clone();
    let owner = c.actor.id;
    let kind = if carriers { "carriers" } else { "companies" };
    let q = c
        .query
        .get("q")
        .map(|s| s.to_lowercase())
        .unwrap_or_default();
    let status = c.query.get("status").cloned().unwrap_or_default();
    let bytes = tokio::task::spawn_blocking(move || -> std::result::Result<Vec<u8>, String> {
        let columns = if carriers {
            vec![
                ("Наименование", "name"),
                ("ИНН", "inn"),
                ("Телефон", "phone"),
                ("Почта", "email"),
                ("Другие контакты", "contacts"),
                ("Направления", "routes"),
                ("Регионы", "regions"),
                ("Условия работы", "work_terms"),
                ("НДС", "vat_mode"),
                ("Ставка НДС", "vat_rate"),
                ("Описание", "description"),
                ("Автомобили", "vehicles"),
            ]
        } else {
            vec![
                ("Наименование", "name"),
                ("ИНН", "inn"),
                ("ОГРН", "ogrn"),
                ("ОКВЭД", "okved"),
                ("Регион", "region"),
                ("Телефоны", "phones"),
                ("Почта", "emails"),
                ("Сайт", "website"),
                ("Источник", "source"),
                ("Статус работы", "lead_status"),
                ("Описание звонка", "call_note"),
                ("Этап", "stage"),
                ("Откуда", "origin"),
                ("Куда", "destination"),
                ("Ответственный", "owner_last_name"),
            ]
        };
        let mut book = rust_xlsxwriter::Workbook::new();
        let sheet = book.add_worksheet();
        sheet.set_name("База").map_err(|e| e.to_string())?;
        let heading = rust_xlsxwriter::Format::new()
            .set_bold()
            .set_background_color("28364A")
            .set_font_color("FFFFFF")
            .set_text_wrap();
        for (i, (title, _)) in columns.iter().enumerate() {
            sheet
                .write_string_with_format(0, i as u16, *title, &heading)
                .map_err(|e| e.to_string())?;
            sheet
                .set_column_width(i as u16, 28)
                .map_err(|e| e.to_string())?;
        }
        let mut row = 1;
        for item in rows {
            if (!q.is_empty() && !item.to_string().to_lowercase().contains(&q))
                || (!status.is_empty() && item["lead_status"].as_str() != Some(&status))
            {
                continue;
            }
            for (i, (_, key)) in columns.iter().enumerate() {
                let mut text = cell(&item[*key]);
                if text.chars().count() > 32767 {
                    text = text.chars().take(32767).collect();
                }
                sheet
                    .write_string(row, i as u16, text)
                    .map_err(|e| e.to_string())?;
            }
            row += 1;
        }
        sheet.set_freeze_panes(1, 1).map_err(|e| e.to_string())?;
        sheet
            .autofilter(0, 0, row.saturating_sub(1), columns.len() as u16 - 1)
            .map_err(|e| e.to_string())?;
        book.save_to_buffer().map_err(|e| e.to_string())
    })
    .await
    .map_err(|_| fail())?
    .map_err(|_| fail())?;
    let mut tx = db::transaction(&c.host.pool, Some(c.actor), false).await?;
    db::audit(
        &mut tx,
        owner,
        "data.export",
        kind,
        json!({"format":"xlsx"}),
        c.ip,
    )
    .await?;
    tx.commit().await?;
    let mut response = Body::from(bytes).into_response();
    response.headers_mut().insert(
        header::CONTENT_TYPE,
        header::HeaderValue::from_static(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
    );
    response.headers_mut().insert(
        header::CONTENT_DISPOSITION,
        header::HeaderValue::from_static("attachment; filename=\"NexSearch.xlsx\""),
    );
    Ok(response)
}
pub async fn route(c: &RequestContext<'_>) -> Result<Option<Response>> {
    if c.method != "GET" {
        return Ok(None);
    }
    if let ["api", "export", kind] = c.parts.as_slice() {
        let path = match *kind {
            "leads" => vec!["api", "leads"],
            "clients" => vec!["api", "clients"],
            "carriers" => vec!["api", "carriers"],
            _ => return Err(ApiError::missing()),
        };
        return export(c, path).await.map(Some);
    }
    if c.parts.as_slice() == ["api", "clients", "overview", "export"] {
        return export(c, vec!["api", "clients", "overview"])
            .await
            .map(Some);
    }
    let result = match c.parts.as_slice() {
        ["api", "host", "status"] => {
            let mut tx = db::transaction(&c.host.pool, Some(c.actor), false).await?;
            let queue:Value=sqlx::query_scalar("SELECT jsonb_build_object('running',count(*) FILTER(WHERE status='running'),'queued',count(*) FILTER(WHERE status='queued')) FROM jobs").fetch_one(&mut *tx).await?;
            tx.commit().await?;
            let mut system = c.host.telemetry.lock().await;
            system.refresh_memory();
            system.refresh_cpu_usage();
            let total = system.total_memory();
            let used = system.used_memory();
            let cpu = system.global_cpu_usage();
            let disks = sysinfo::Disks::new_with_refreshed_list();
            let free = disks
                .list()
                .iter()
                .filter(|d| c.host.config.secret_file.starts_with(d.mount_point()))
                .max_by_key(|d| d.mount_point().as_os_str().len())
                .map(|d| d.available_space())
                .unwrap_or(0);
            let updates = tokio::fs::read(c.host.config.update_dir.join("status.json"))
                .await
                .ok()
                .filter(|v| v.len() < 1024 * 1024)
                .and_then(|v| serde_json::from_slice::<Value>(&v).ok())
                .unwrap_or(json!({"status":"not_configured"}));
            json!({"status":"online","version":env!("CARGO_PKG_VERSION"),"runtime":"rust","hostname":sysinfo::System::host_name().unwrap_or_else(||"NexSearch".into()),"ip":c.host.config.bind.ip().to_string(),"time":chrono::Utc::now(),"cpu_percent":cpu,"memory_total_bytes":total,"memory_used_bytes":used,"memory_percent":if total>0 {used as f64*100.0/total as f64}else{0.0},"disk_free_bytes":free,"queue":queue,"updates":updates,"schedule":"10:00 Europe/Moscow"})
        }
        ["api", "diagnostics"] => {
            json!({"host_id":c.host.config.host_id,"version":env!("CARGO_PKG_VERSION"),"runtime":"rust","database":!c.host.pool.is_closed(),"checko_configured":secrets::load(&c.host.config)?["checko_api_key"].as_str().is_some_and(|s|!s.is_empty()),"protocol":1,"time":chrono::Utc::now(),"discovery":"UDP 8742","transport":"Pinned HTTPS TCP 8743"})
        }
        ["api", "admin", "backups"] => {
            if c.actor.role != "director" {
                return Err(ApiError::denied("Раздел доступен генеральному директору"));
            }
            let mut tx = db::transaction(&c.host.pool, Some(c.actor), false).await?;
            let rows:Vec<Value>=sqlx::query_scalar("SELECT to_jsonb(r) FROM(SELECT id,status,size_bytes,sha256,verified_at,error,created_at FROM backup_records ORDER BY created_at DESC LIMIT 100) r").fetch_all(&mut *tx).await?;
            tx.commit().await?;
            json!({"backups":rows,"automatic_available":false})
        }
        _ => return Ok(None),
    };
    Ok(Some(json_response(result)))
}
