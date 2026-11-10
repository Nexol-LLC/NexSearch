//! Read-only compatibility check against an explicitly selected local host.
use base64::{Engine as _, engine::general_purpose::STANDARD};
use nexsearch_bridge::{Bridge, Trust};
use serde_json::{Value, json};
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let config: Value = serde_json::from_slice(&std::fs::read(
        std::env::args().nth(1).ok_or("Укажите локальный профиль")?,
    )?)?;
    let endpoint = config["endpoint"].as_str().ok_or("Не указан хост")?;
    if !endpoint.starts_with("https://127.0.0.1:") {
        return Err("Проверка разрешена только для явно выбранного локального сервера".into());
    }
    let trust: Trust = serde_json::from_value(config["trust"].clone())?;
    let bridge = Bridge::new(trust)?;
    bridge.connect(endpoint).await?;
    bridge
        .import_session(std::path::Path::new(
            config["import_session"].as_str().ok_or("Сеанс не указан")?,
        ))
        .await?;
    let mut results = vec![];
    for path in [
        "/api/me",
        "/api/state",
        "/api/leads",
        "/api/clients/overview",
        "/api/carriers",
        "/api/tasks",
        "/api/mail/accounts",
        "/api/notifications",
        "/api/clients/stages",
        "/api/catalog",
        "/api/search/preferences",
        "/api/host/status",
    ] {
        let reply = bridge.request(path, "GET", None).await;
        if reply.status != 200 {
            return Err(format!("{path}: {}", reply.status).into());
        }
        let bytes = STANDARD.decode(reply.body)?;
        let value: Value = serde_json::from_slice(&bytes)?;
        if value.get("token").is_some() {
            return Err("Секрет попал в ответ интерфейса".into());
        }
        results.push(json!({"path":path,"status":reply.status}));
    }
    println!("{}", json!({"authenticated":true,"tests":results}));
    Ok(())
}
