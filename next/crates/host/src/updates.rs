//! Read-only access to packages prepared by the privileged signed updater.
//! The application service never fetches GitHub credentials or installs code.
use crate::{
    RequestContext, db,
    error::{ApiError, Result},
    json_response,
};
use axum::{
    body::Body,
    http::{StatusCode, header},
    response::{IntoResponse, Response},
};
use serde_json::{Value, json};
use tokio::io::AsyncReadExt;
fn platform(c: &RequestContext<'_>) -> Result<&'static str> {
    let p = c
        .query
        .get("platform")
        .map(String::as_str)
        .unwrap_or("EmployeeWindows");
    match p {
        "EmployeeWindows" => Ok("EmployeeWindows"),
        "EmployeeMac" => Ok("EmployeeMac"),
        _ => Err(ApiError(
            StatusCode::BAD_REQUEST,
            "Неизвестная платформа сотрудника".into(),
        )),
    }
}
fn digest(s: &str) -> bool {
    s.len() == 64
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
async fn state(c: &RequestContext<'_>) -> Result<Value> {
    let path = c.host.config.update_dir.join("status.json");
    let meta = match tokio::fs::metadata(&path).await {
        Ok(v) => v,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            return Ok(json!({"packages":{},"status":"not_configured"}));
        }
        Err(_) => {
            return Err(ApiError(
                StatusCode::SERVICE_UNAVAILABLE,
                "Состояние обновлений недоступно".into(),
            ));
        }
    };
    if meta.len() > 1024 * 1024 {
        return Err(ApiError::invalid("Повреждено состояние обновлений"));
    }
    let bytes = tokio::fs::read(path)
        .await
        .map_err(|_| ApiError::invalid("Состояние обновлений недоступно"))?;
    serde_json::from_slice(&bytes).map_err(|_| ApiError::invalid("Повреждено состояние обновлений"))
}
pub async fn route(c: &RequestContext<'_>) -> Result<Option<Response>> {
    if c.method != "GET" || c.parts.get(1) != Some(&"update") {
        return Ok(None);
    }
    let role = platform(c)?;
    let state = state(c).await?;
    let package = &state["packages"][role];
    if c.parts.as_slice() == ["api", "update", "info"] {
        if package.is_null() {
            return Ok(Some(json_response(
                json!({"available":false,"branch":"employe","checked_at":state["checked_at"],"status":state["status"].as_str().unwrap_or("not_configured")}),
            )));
        }
        let mut result =
            json!({"available":true,"branch":"employe","checked_at":state["checked_at"]});
        for key in ["version", "source_commit", "sha256", "size_bytes"] {
            result[key] = package[key].clone()
        }
        return Ok(Some(json_response(result)));
    }
    if c.parts.as_slice() != ["api", "update", "package"] {
        return Ok(None);
    }
    let requested = c.query.get("sha256").map(String::as_str).unwrap_or("");
    if !digest(requested) || package["sha256"] != requested {
        return Err(ApiError::conflict(
            "Комплект изменился; проверьте обновления снова",
        ));
    }
    let root = c.host.config.update_dir.join("packages");
    let path = root.join(format!("{requested}.zip"));
    let unavailable = || {
        ApiError(
            StatusCode::SERVICE_UNAVAILABLE,
            "Комплект ещё не подготовлен на хосте".into(),
        )
    };
    let root = tokio::fs::canonicalize(root)
        .await
        .map_err(|_| unavailable())?;
    let resolved = tokio::fs::canonicalize(&path)
        .await
        .map_err(|_| unavailable())?;
    if resolved.parent() != Some(root.as_path())
        || tokio::fs::symlink_metadata(&path)
            .await
            .map_err(|_| unavailable())?
            .file_type()
            .is_symlink()
    {
        return Err(unavailable());
    }
    let file = tokio::fs::File::open(resolved)
        .await
        .map_err(|_| unavailable())?;
    let meta = file.metadata().await.map_err(|_| unavailable())?;
    if !meta.is_file()
        || meta.len() == 0
        || meta.len() > 256 * 1024 * 1024
        || package["size_bytes"].as_u64() != Some(meta.len())
    {
        return Err(unavailable());
    }
    let mut tx = db::transaction(&c.host.pool, Some(c.actor), false).await?;
    db::audit(&mut tx,c.actor.id,"update.employee_download",role,json!({"branch":"employe","version":package["version"],"source_commit":package["source_commit"]}),c.ip).await?;
    tx.commit().await?;
    let size = meta.len();
    let stream =
        futures_util::stream::try_unfold((file, size), |(mut file, remaining)| async move {
            if remaining == 0 {
                return Ok::<_, std::io::Error>(None);
            }
            let mut bytes = vec![0; remaining.min(64 * 1024) as usize];
            let count = file.read(&mut bytes).await?;
            if count == 0 {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::UnexpectedEof,
                    "Пакет обновления загружен не полностью",
                ));
            }
            bytes.truncate(count);
            Ok(Some((bytes, (file, remaining - count as u64))))
        });
    let mut response = Body::from_stream(stream).into_response();
    response.headers_mut().insert(
        header::CONTENT_TYPE,
        header::HeaderValue::from_static("application/zip"),
    );
    response.headers_mut().insert(
        header::CONTENT_DISPOSITION,
        header::HeaderValue::from_str(&format!("attachment; filename=\"{role}-update.zip\""))
            .map_err(|_| ApiError::invalid("Некорректное имя пакета"))?,
    );
    response.headers_mut().insert(
        header::CONTENT_LENGTH,
        header::HeaderValue::from_str(&size.to_string()).map_err(|_| unavailable())?,
    );
    Ok(Some(response))
}
