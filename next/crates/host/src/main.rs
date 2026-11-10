use axum::{Json, Router, routing::get};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Isolated migration probe, not a replacement for the authenticated 1.6.7 host.
    let app=Router::new().route("/health",get(|| async {Json(serde_json::json!({"version":env!("CARGO_PKG_VERSION"),"mode":"migration-preview","production_ready":false,"legacy_schema":19,"database_connected":false}))}));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:18743").await?;
    println!("Проверочный сервер 2.0: http://127.0.0.1:18743/health; рабочая база не подключена");
    axum::serve(listener, app)
        .with_graceful_shutdown(async {
            let _ = tokio::signal::ctrl_c().await;
        })
        .await?;
    Ok(())
}
