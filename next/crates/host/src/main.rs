use nexsearch_host::{Config, Host, router};
use std::{path::PathBuf, time::Duration};
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "nexsearch_host=info".into()),
        )
        .init();
    let config: Config =
        if let Some(file) = std::env::var_os("NEXSEARCH_HOST_CONFIG").map(PathBuf::from) {
            serde_json::from_slice(&std::fs::read(file)?)?
        } else {
            nexsearch_host::legacy_config::from_environment().map_err(std::io::Error::other)?
        };
    let host = Host::connect(config.clone()).await?;
    let handle = axum_server::Handle::new();
    let shutdown = handle.clone();
    tokio::spawn(async move {
        #[cfg(unix)]
        {
            let mut term =
                tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
                    .expect("SIGTERM");
            tokio::select! {_=tokio::signal::ctrl_c()=>{},_=term.recv()=>{}}
        }
        #[cfg(not(unix))]
        {
            let _ = tokio::signal::ctrl_c().await;
        }
        shutdown.graceful_shutdown(Some(Duration::from_secs(30)));
    });
    let tls =
        axum_server::tls_rustls::RustlsConfig::from_pem_file(&config.tls_cert, &config.tls_key)
            .await?;
    tracing::info!(address=%config.bind,host_id=%config.host_id,"Rust-хост подключён к PostgreSQL");
    let pool = host.pool.clone();
    nexsearch_host::start_background(host.clone());
    axum_server::bind_rustls(config.bind, tls)
        .handle(handle)
        .serve(router(host).into_make_service_with_connect_info::<std::net::SocketAddr>())
        .await?;
    pool.close().await;
    Ok(())
}
