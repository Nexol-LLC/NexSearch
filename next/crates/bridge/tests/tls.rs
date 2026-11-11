use base64::{Engine as _, engine::general_purpose::STANDARD};
use nexsearch_bridge::{Bridge, Trust};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::sync::{
    Arc,
    atomic::{AtomicUsize, Ordering},
};
use tokio::io::{AsyncReadExt, AsyncWriteExt};

async fn fixture() -> (String, Trust, Arc<AtomicUsize>, tokio::task::JoinHandle<()>) {
    let key = rcgen::generate_simple_self_signed(vec!["localhost".into()]).unwrap();
    let fingerprint = format!("{:x}", Sha256::digest(key.cert.der()));
    let provider = Arc::new(rustls::crypto::ring::default_provider());
    let config = rustls::ServerConfig::builder_with_provider(provider)
        .with_safe_default_protocol_versions()
        .unwrap()
        .with_no_client_auth()
        .with_single_cert(
            vec![key.cert.der().clone()],
            rustls::pki_types::PrivateKeyDer::Pkcs8(key.signing_key.serialize_der().into()),
        )
        .unwrap();
    let tls = tokio_rustls::TlsAcceptor::from(Arc::new(config));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let count = Arc::new(AtomicUsize::new(0));
    let output = count.clone();
    let task = tokio::spawn(async move {
        while let Ok((socket, _)) = listener.accept().await {
            let tls = tls.clone();
            let count = count.clone();
            tokio::spawn(async move {
                let Ok(mut stream) = tls.accept(socket).await else {
                    return;
                };
                let mut buffer = vec![];
                let mut chunk = [0; 4096];
                loop {
                    let n = stream.read(&mut chunk).await.unwrap_or(0);
                    if n == 0 {
                        return;
                    }
                    buffer.extend_from_slice(&chunk[..n]);
                    if buffer.windows(4).any(|w| w == b"\r\n\r\n") {
                        break;
                    }
                }
                count.fetch_add(1, Ordering::SeqCst);
                let request = String::from_utf8_lossy(&buffer);
                let body=if request.starts_with("GET /api/host-info "){json!({"host_id":"fixture-host"})}
                else if request.starts_with("POST /api/auth/login "){json!({"token":"fixture-session-secret","employee":{"id":"employee","login":"fixture"}})}
                else {json!({"employee":{"id":"employee","login":"fixture"},"authenticated":request.to_ascii_lowercase().contains("authorization: bearer fixture-session-secret")})}.to_string();
                let response = format!(
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                );
                let _ = stream.write_all(response.as_bytes()).await;
            });
        }
    });
    (
        format!("https://{address}"),
        Trust {
            host_id: "fixture-host".into(),
            fingerprint,
        },
        output,
        task,
    )
}
#[tokio::test]
async fn certificate_is_checked_before_any_http_request_or_password() {
    let (endpoint, mut trust, count, server) = fixture().await;
    trust.fingerprint = "00".repeat(32);
    let bridge = Bridge::new(trust).unwrap();
    assert!(bridge.connect(&endpoint).await.is_err());
    assert_eq!(count.load(Ordering::SeqCst), 0);
    server.abort();
}
#[tokio::test]
async fn verified_host_keeps_session_secret_in_rust_only() {
    let (endpoint, trust, count, server) = fixture().await;
    let bridge = Bridge::new(trust).unwrap();
    bridge.connect(&endpoint).await.unwrap();
    let reply = bridge
        .request(
            "/api/auth/login",
            "POST",
            Some(json!({"login":"fixture","password":"local-test-only"}).to_string()),
        )
        .await;
    assert_eq!(reply.status, 200);
    let visible = STANDARD.decode(reply.body).unwrap();
    let value: Value = serde_json::from_slice(&visible).unwrap();
    assert!(value.get("token").is_none());
    assert!(!String::from_utf8_lossy(&visible).contains("fixture-session-secret"));
    let reply = bridge.request("/api/me", "GET", None).await;
    let value: Value = serde_json::from_slice(&STANDARD.decode(reply.body).unwrap()).unwrap();
    assert_eq!(value["authenticated"], true);
    assert_eq!(count.load(Ordering::SeqCst), 3);
    server.abort();
}

#[cfg(windows)]
#[tokio::test]
async fn native_restart_restores_protected_login_and_checks_host_again() {
    let (endpoint, trust, count, server) = fixture().await;
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("session.sec");
    let bridge = Bridge::new(trust.clone()).unwrap();
    bridge.configure_store(path.clone()).await.unwrap();
    bridge.connect(&endpoint).await.unwrap();
    assert_eq!(
        bridge
            .request(
                "/api/auth/login",
                "POST",
                Some(json!({"login":"fixture","password":"test-only"}).to_string())
            )
            .await
            .status,
        200
    );
    drop(bridge);
    let resumed = Bridge::new(trust).unwrap();
    resumed.configure_store(path.clone()).await.unwrap();
    assert!(resumed.has_session().await);
    resumed.reconnect_saved().await.unwrap();
    let reply = resumed.request("/api/me", "GET", None).await;
    let visible = STANDARD.decode(reply.body).unwrap();
    let value: Value = serde_json::from_slice(&visible).unwrap();
    assert_eq!(value["authenticated"], true);
    assert_eq!(count.load(Ordering::SeqCst), 4);
    assert!(
        !String::from_utf8_lossy(&std::fs::read(path).unwrap()).contains("fixture-session-secret")
    );
    server.abort();
}

#[cfg(windows)]
#[tokio::test]
async fn legacy_employee_login_import_requires_the_pinned_host_confirmation() {
    let (endpoint, trust, _, server) = fixture().await;
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("employee.sec");
    let raw=json!({"token":"fixture-session-secret","device_id":"legacy-device","profile":{"id":"unverified-cached-id"}}).to_string();
    let encrypted =
        windows_dpapi::encrypt_data(raw.as_bytes(), windows_dpapi::Scope::User, None).unwrap();
    std::fs::write(&path, [b"DPAPI".as_slice(), &encrypted].concat()).unwrap();
    let bridge = Bridge::new(trust).unwrap();
    assert!(bridge.import_session(&path).await.is_err());
    bridge.connect(&endpoint).await.unwrap();
    bridge.import_session(&path).await.unwrap();
    let connection = bridge.request("/api/connection", "GET", None).await;
    let visible = STANDARD.decode(connection.body).unwrap();
    let value: Value = serde_json::from_slice(&visible).unwrap();
    assert_eq!(value["profile"]["id"], "employee");
    assert!(!String::from_utf8_lossy(&visible).contains("fixture-session-secret"));
    server.abort();
}
