//! Native headless host with transaction-local PostgreSQL employee scopes.
pub mod auth;
mod business;
mod collaboration;
mod company_time;
mod crawler;
mod crm;
pub mod db;
mod discovery;
pub mod error;
pub mod legacy_config;
mod mail;
mod mail_content;
mod mail_data;
mod mail_folders;
mod mail_ops;
mod mail_transport;
mod maintenance;
mod rates;
mod search;
mod secrets;
mod tasks;
mod updates;
mod verification;
use axum::{
    Json, Router,
    body::Bytes,
    extract::{ConnectInfo, DefaultBodyLimit, State},
    http::{HeaderMap, Method, StatusCode, Uri, header},
    response::{Html, IntoResponse, Response},
    routing::{any, get},
};
use error::{ApiError, Result};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sqlx::{PgPool, postgres::PgPoolOptions};
use std::{
    collections::HashMap,
    net::SocketAddr,
    path::PathBuf,
    sync::Arc,
    time::{Duration, Instant},
};
use tokio::sync::{Mutex, Semaphore};
use uuid::Uuid;
#[derive(Clone, Deserialize, Serialize)]
pub struct Config {
    pub database_url: String,
    pub host_id: Uuid,
    #[serde(default = "default_bind")]
    pub bind: SocketAddr,
    pub tls_cert: PathBuf,
    pub tls_key: PathBuf,
    pub secret_file: PathBuf,
    pub web_dir: PathBuf,
    pub pack_dir: PathBuf,
    pub update_dir: PathBuf,
    #[serde(default = "source_enabled")]
    pub source_access: bool,
}
fn source_enabled() -> bool {
    true
}
fn default_bind() -> SocketAddr {
    "0.0.0.0:8743".parse().expect("Адрес службы")
}
pub struct Host {
    pub config: Config,
    pub pool: PgPool,
    pub started: Instant,
    pub client: reqwest::Client,
    pub(crate) password_slots: Arc<Semaphore>,
    pub(crate) login_attempts: Mutex<HashMap<String, Vec<Instant>>>,
    pub(crate) dummy_hash: String,
    pub(crate) search: search::Runtime,
    pub(crate) mail: mail::Runtime,
    pub(crate) telemetry: Mutex<sysinfo::System>,
    _lease: Mutex<sqlx::PgConnection>,
}
impl Host {
    pub async fn connect(
        config: Config,
    ) -> std::result::Result<Arc<Self>, Box<dyn std::error::Error>> {
        let _ = rustls::crypto::ring::default_provider().install_default();
        let pool = PgPoolOptions::new()
            .min_connections(1)
            .max_connections(16)
            .acquire_timeout(Duration::from_secs(10))
            .connect(&config.database_url)
            .await?;
        let version: Option<i32> = sqlx::query_scalar("SELECT max(version) FROM schema_meta")
            .fetch_one(&pool)
            .await?;
        if version.unwrap_or_default() < 19 {
            return Err(
                "Требуется проверенная схема 19; автоматическая миграция рабочих данных запрещена"
                    .into(),
            );
        }
        let unsafe_role: bool = sqlx::query_scalar(
            "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user",
        )
        .fetch_one(&pool)
        .await?;
        if unsafe_role {
            return Err("Нужна роль PostgreSQL без SUPERUSER и BYPASSRLS".into());
        }
        use sqlx::Connection;
        let mut lease = sqlx::PgConnection::connect(&config.database_url).await?;
        let exclusive: bool = sqlx::query_scalar("SELECT pg_try_advisory_lock(73142020)")
            .fetch_one(&mut lease)
            .await?;
        if !exclusive {
            return Err("Для этой базы уже запущен другой Rust-хост".into());
        }
        // The installed 1.6.7 privileged updater checks this session lease before
        // draining searches. Keep its exact lock so live Rust jobs are never
        // mistaken for abandoned Python jobs during later updates.
        let worker: bool = sqlx::query_scalar("SELECT pg_try_advisory_lock(73142003)")
            .fetch_one(&mut lease)
            .await?;
        if !worker {
            return Err("Для этой базы уже работает служба поиска".into());
        }
        {
            let mut tx = db::transaction(&pool, None, true).await?;
            sqlx::query("UPDATE jobs SET status=CASE WHEN stop_requested THEN 'stopped' ELSE 'queued' END,message='Восстанавливаем поиск после перезапуска' WHERE status='running'").execute(&mut *tx).await?;
            sqlx::query("UPDATE scraper_candidates SET state='pending' WHERE state='processing'")
                .execute(&mut *tx)
                .await?;
            tx.commit().await?;
        }
        let dummy_hash = auth::hash_password(&Uuid::new_v4().to_string())
            .map_err(|_| "Не удалось подготовить защиту входа")?;
        let client = reqwest::Client::builder()
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .connect_timeout(Duration::from_secs(8))
            .timeout(Duration::from_secs(35))
            .build()?;
        Ok(Arc::new(Self {
            config,
            pool,
            started: Instant::now(),
            client,
            password_slots: Arc::new(Semaphore::new(8)),
            login_attempts: Mutex::new(HashMap::new()),
            dummy_hash,
            search: search::Runtime::default(),
            mail: mail::Runtime::default(),
            telemetry: Mutex::new(sysinfo::System::new_all()),
            _lease: Mutex::new(lease),
        }))
    }
}
pub struct RequestContext<'a> {
    pub host: &'a Host,
    pub actor: &'a db::Employee,
    pub method: &'a str,
    pub parts: Vec<&'a str>,
    pub query: HashMap<String, String>,
    pub body: Value,
    pub ip: &'a str,
}
pub fn json_response(value: Value) -> Response {
    Json(value).into_response()
}
pub fn start_background(host: Arc<Host>) {
    let owner = host.clone();
    tokio::spawn(async move {
        use sqlx::Connection;
        loop {
            tokio::time::sleep(Duration::from_secs(5)).await;
            if owner._lease.lock().await.ping().await.is_err() {
                tracing::error!(
                    "Потеряно исключительное соединение поиска; требуется перезапуск службы"
                );
                // systemd restarts the host and reacquires both locks before any
                // recovery. Continuing with a pool after losing a session lock
                // could overlap another worker after PostgreSQL restarts.
                std::process::exit(1);
            }
        }
    });
    tokio::spawn(discovery::run(host.clone()));
    tokio::spawn(mail::run_scheduler(host.clone()));
    tokio::spawn(search::run_scheduler(host));
}
pub fn router(host: Arc<Host>) -> Router {
    let web = host.config.web_dir.clone();
    let pack = host.config.pack_dir.clone();
    Router::new()
        .route("/api/{*path}", any(dispatch))
        .route("/health", any(dispatch))
        .route("/", get(index))
        .nest_service("/assets", tower_http::services::ServeDir::new(web))
        .nest_service("/pack", tower_http::services::ServeDir::new(pack))
        .layer(
            tower_http::set_header::SetResponseHeaderLayer::if_not_present(
                header::X_CONTENT_TYPE_OPTIONS,
                header::HeaderValue::from_static("nosniff"),
            ),
        )
        .layer(DefaultBodyLimit::max(5 * 1024 * 1024))
        .with_state(host)
}
async fn index(State(host): State<Arc<Host>>) -> Result<Html<String>> {
    let path = host.config.web_dir.join("index.html");
    let meta = tokio::fs::metadata(&path)
        .await
        .map_err(|_| ApiError::invalid("Файлы интерфейса хоста недоступны"))?;
    if meta.len() > 1024 * 1024 {
        return Err(ApiError::invalid("Некорректный файл интерфейса"));
    }
    let html = tokio::fs::read_to_string(path)
        .await
        .map_err(|_| ApiError::invalid("Файлы интерфейса хоста недоступны"))?;
    Ok(Html(html.replace("1.6.7", env!("CARGO_PKG_VERSION"))))
}
async fn dispatch(
    State(host): State<Arc<Host>>,
    ConnectInfo(peer): ConnectInfo<SocketAddr>,
    method: Method,
    uri: Uri,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let mut response = dispatch_inner(&host, peer, &method, &uri, &headers, body)
        .await
        .unwrap_or_else(IntoResponse::into_response);
    response.headers_mut().insert(
        header::CACHE_CONTROL,
        header::HeaderValue::from_static("no-store"),
    );
    response.headers_mut().insert(
        header::X_CONTENT_TYPE_OPTIONS,
        header::HeaderValue::from_static("nosniff"),
    );
    response
}
async fn dispatch_inner(
    host: &Host,
    peer: SocketAddr,
    method: &Method,
    uri: &Uri,
    headers: &HeaderMap,
    bytes: Bytes,
) -> Result<Response> {
    let path = uri.path();
    let ip = peer.ip().to_string();
    if method == Method::GET && path == "/api/host-info" {
        return Ok(json_response(
            json!({"app":"nexsearch-host","protocol":1,"version":env!("CARGO_PKG_VERSION"),"host_id":host.config.host_id,"runtime":"rust"}),
        ));
    }
    if method == Method::GET && path == "/health" {
        sqlx::query("SELECT 1").execute(&host.pool).await?;
        return Ok(json_response(
            json!({"version":env!("CARGO_PKG_VERSION"),"database_connected":true,"runtime":"rust"}),
        ));
    }
    let body = if bytes.is_empty() {
        Value::Null
    } else {
        serde_json::from_slice(&bytes).map_err(|_| ApiError::invalid("Проверьте формат запроса"))?
    };
    if method == Method::POST && matches!(path, "/api/auth/login" | "/api/auth/register") {
        return auth::enter(host, body, path.ends_with("register"), &ip)
            .await
            .map(json_response);
    }
    let token = headers
        .get(header::AUTHORIZATION)
        .and_then(|h| h.to_str().ok())
        .and_then(|h| h.strip_prefix("Bearer "))
        .unwrap_or("");
    let actor = auth::authenticate(host, token, &ip).await?;
    if method == Method::GET && matches!(path, "/api/me" | "/api/auth/status") {
        return Ok(json_response(
            json!({"employee":actor,"approved":actor.approved}),
        ));
    }
    if method == Method::POST && path == "/api/auth/logout" {
        let mut tx = db::transaction(&host.pool, Some(&actor), false).await?;
        sqlx::query("DELETE FROM sessions WHERE token_hash=$1 AND employee_id=$2")
            .bind(auth::digest(token))
            .bind(actor.id)
            .execute(&mut *tx)
            .await?;
        db::audit(&mut tx, actor.id, "account.logout", "", json!({}), &ip).await?;
        tx.commit().await?;
        return Ok(json_response(json!({"ok":true})));
    }
    if !actor.approved {
        return Err(ApiError(
            StatusCode::FORBIDDEN,
            "Учётная запись ожидает подтверждения директора".into(),
        ));
    }
    let query = url::form_urlencoded::parse(uri.query().unwrap_or("").as_bytes())
        .into_owned()
        .collect();
    let context = RequestContext {
        host,
        actor: &actor,
        method: method.as_str(),
        parts: path.trim_matches('/').split('/').collect(),
        query,
        body,
        ip: &ip,
    };
    if let Some(response) = crm::route(&context).await? {
        return Ok(response);
    }
    if let Some(response) = maintenance::route(&context).await? {
        return Ok(response);
    }
    if let Some(response) = rates::route(&context).await? {
        return Ok(response);
    }
    if let Some(response) = collaboration::route(&context).await? {
        return Ok(response);
    }
    if let Some(response) = tasks::route(&context).await? {
        return Ok(response);
    }
    if let Some(response) = business::route(&context).await? {
        return Ok(response);
    }
    if let Some(response) = search::route(&context).await? {
        return Ok(response);
    }
    if let Some(response) = mail::route(&context).await? {
        return Ok(response);
    }
    if let Some(response) = updates::route(&context).await? {
        return Ok(response);
    }
    Err(ApiError(
        StatusCode::NOT_IMPLEMENTED,
        "Операция ещё не прошла перенос и проверку на Rust-хосте".into(),
    ))
}
