//! Compatibility with the actual 1.6.7 host. Tokens never enter the WebView.
use base64::{Engine as _, engine::general_purpose::STANDARD};
use rustls::{
    DigitallySignedStruct, SignatureScheme,
    client::danger::{HandshakeSignatureValid, ServerCertVerified, ServerCertVerifier},
    pki_types::{CertificateDer, ServerName, UnixTime},
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{sync::Arc, time::Duration};
use tokio::sync::RwLock;
use uuid::Uuid;

#[derive(Debug, Clone, Deserialize)]
pub struct Trust {
    pub host_id: String,
    pub fingerprint: String,
}
#[derive(Debug)]
struct PinnedCertificate {
    digest: [u8; 32],
    provider: Arc<rustls::crypto::CryptoProvider>,
}
impl ServerCertVerifier for PinnedCertificate {
    fn verify_server_cert(
        &self,
        cert: &CertificateDer<'_>,
        _: &[CertificateDer<'_>],
        _: &ServerName<'_>,
        _: &[u8],
        _: UnixTime,
    ) -> Result<ServerCertVerified, rustls::Error> {
        if Sha256::digest(cert.as_ref()).as_slice() == self.digest {
            Ok(ServerCertVerified::assertion())
        } else {
            Err(rustls::Error::General(
                "Не совпадает сертификат хоста".into(),
            ))
        }
    }
    fn verify_tls12_signature(
        &self,
        message: &[u8],
        cert: &CertificateDer<'_>,
        signature: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, rustls::Error> {
        rustls::crypto::verify_tls12_signature(
            message,
            cert,
            signature,
            &self.provider.signature_verification_algorithms,
        )
    }
    fn verify_tls13_signature(
        &self,
        message: &[u8],
        cert: &CertificateDer<'_>,
        signature: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, rustls::Error> {
        rustls::crypto::verify_tls13_signature(
            message,
            cert,
            signature,
            &self.provider.signature_verification_algorithms,
        )
    }
    fn supported_verify_schemes(&self) -> Vec<SignatureScheme> {
        self.provider
            .signature_verification_algorithms
            .supported_schemes()
    }
}

#[derive(Debug, Serialize)]
pub struct Reply {
    pub status: u16,
    pub headers: std::collections::BTreeMap<String, String>,
    pub body: String,
}
impl Reply {
    pub fn json(status: u16, value: Value) -> Self {
        Self {
            status,
            headers: [(
                "content-type".into(),
                "application/json; charset=utf-8".into(),
            )]
            .into(),
            body: STANDARD.encode(serde_json::to_vec(&value).unwrap()),
        }
    }
}
#[derive(Default)]
struct Session {
    endpoint: Option<String>,
    token: Option<String>,
    profile: Option<Value>,
}
pub struct Bridge {
    client: reqwest::Client,
    trust: Trust,
    session: RwLock<Session>,
    device_id: String,
    csrf: String,
}
impl Bridge {
    pub fn new(trust: Trust) -> Result<Self, &'static str> {
        if trust.fingerprint.len() != 64 {
            return Err("Некорректная идентичность хоста");
        }
        let mut digest = [0; 32];
        for (i, chunk) in trust
            .fingerprint
            .as_bytes()
            .as_chunks::<2>()
            .0
            .iter()
            .enumerate()
        {
            digest[i] = u8::from_str_radix(
                std::str::from_utf8(chunk).map_err(|_| "Некорректная идентичность хоста")?,
                16,
            )
            .map_err(|_| "Некорректная идентичность хоста")?;
        }
        let provider = Arc::new(rustls::crypto::ring::default_provider());
        let tls = rustls::ClientConfig::builder_with_provider(provider.clone())
            .with_safe_default_protocol_versions()
            .map_err(|_| "TLS недоступен")?
            .dangerous()
            .with_custom_certificate_verifier(Arc::new(PinnedCertificate { digest, provider }))
            .with_no_client_auth();
        let client = reqwest::Client::builder()
            .tls_backend_preconfigured(tls)
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .connect_timeout(Duration::from_secs(4))
            .timeout(Duration::from_secs(95))
            .build()
            .map_err(|_| "Не удалось создать защищённое подключение")?;
        Ok(Self {
            client,
            trust,
            session: RwLock::new(Session::default()),
            device_id: Uuid::new_v4().to_string(),
            csrf: Uuid::new_v4().to_string(),
        })
    }
    pub async fn connect(&self, endpoint: &str) -> Result<(), &'static str> {
        let url = url::Url::parse(endpoint).map_err(|_| "Некорректный адрес хоста")?;
        if url.scheme() != "https"
            || url.host_str().is_none()
            || !url.username().is_empty()
            || url.password().is_some()
            || url.path() != "/"
            || url.query().is_some()
            || url.fragment().is_some()
        {
            return Err("Требуется защищённый адрес хоста");
        }
        let endpoint = endpoint.trim_end_matches('/');
        let response = self
            .client
            .get(format!("{endpoint}/health"))
            .send()
            .await
            .map_err(|_| "Хост недоступен или не прошёл проверку сертификата")?;
        if !response.status().is_success() {
            return Err("Хост не подтвердил доступность");
        }
        let health: Value = response
            .json()
            .await
            .map_err(|_| "Хост не подтвердил идентичность")?;
        if health["host_id"].as_str() != Some(&self.trust.host_id) {
            return Err("Это другой хост");
        }
        self.session.write().await.endpoint = Some(endpoint.into());
        Ok(())
    }
    pub async fn request(&self, path: &str, method: &str, body: Option<String>) -> Reply {
        if !valid_path(path)
            || !matches!(method, "GET" | "HEAD" | "POST" | "PATCH" | "PUT" | "DELETE")
            || body.as_ref().is_some_and(|s| s.len() > 16 * 1024 * 1024)
        {
            return Reply::json(400, json!({"error":"Недопустимый запрос"}));
        }
        let (endpoint, token, profile) = {
            let s = self.session.read().await;
            (s.endpoint.clone(), s.token.clone(), s.profile.clone())
        };
        let transport = json!({"preference":"auto","transport":if endpoint.is_some(){Some("lan")}else{None},"host_url":endpoint,"host_found":endpoint.is_some(),"internet_configured":false,"internet_online":false,"internet_error":"","internet_checked_at":null});
        if path == "/api/connection" {
            let mut connection = transport;
            connection["csrf"] = json!(self.csrf);
            connection["profile"] = json!(profile);
            connection["computer"] = json!("NexSearch · Rust");
            return Reply::json(200, connection);
        }
        if path == "/api/client/transport" {
            return Reply::json(200, transport);
        }
        if path == "/api/client/exit" && method == "POST" {
            return Reply::json(200, json!({"ok":true}));
        }
        if path == "/api/client/update/status" {
            return Reply::json(
                200,
                json!({"current_version":"2.0.0-alpha.1","version":"2.0.0-alpha.1","status":"Предварительная версия перехода. Рабочее обновление ещё не опубликовано.","supports_restart":false}),
            );
        }
        if path.starts_with("/api/client/") {
            return Reply::json(
                409,
                json!({"error":"Эта системная операция ещё переносится на Rust. Рабочая 1.6.7 остаётся отдельно."}),
            );
        }
        let Some(endpoint) = endpoint else {
            return Reply::json(
                503,
                json!({"error":"Ожидаем проверенный хост. Рабочие данные не изменены."}),
            );
        };
        let mut request = self
            .client
            .request(
                reqwest::Method::from_bytes(method.as_bytes()).unwrap(),
                format!("{endpoint}{path}"),
            )
            .header("Content-Type", "application/json");
        if let Some(token) = token {
            request = request.bearer_auth(token);
        }
        let mut body = body;
        if matches!(path, "/api/auth/login" | "/api/auth/register") {
            let Ok(mut value) = serde_json::from_str::<Value>(body.as_deref().unwrap_or("")) else {
                return Reply::json(400, json!({"error":"Некорректный запрос входа"}));
            };
            if !value.is_object() {
                return Reply::json(400, json!({"error":"Некорректный запрос входа"}));
            }
            value["device_id"] = json!(self.device_id);
            value["computer_name"] = json!("NexSearch · Rust");
            body = Some(value.to_string());
        }
        if let Some(body) = body
            && !matches!(method, "GET" | "HEAD")
        {
            request = request.body(body);
        }
        let Ok(mut response) = request.send().await else {
            return Reply::json(
                503,
                json!({"error":"Соединение с хостом прервано. Запрос изменения не повторялся автоматически."}),
            );
        };
        let status = response.status().as_u16();
        let headers = response
            .headers()
            .iter()
            .filter_map(|(name, value)| {
                matches!(name.as_str(), "content-type" | "content-disposition")
                    .then(|| value.to_str().ok().map(|v| (name.to_string(), v.into())))
                    .flatten()
            })
            .collect();
        let mut bytes = vec![];
        loop {
            match response.chunk().await {
                Ok(Some(chunk)) if bytes.len() + chunk.len() <= 50 * 1024 * 1024 => {
                    bytes.extend_from_slice(&chunk)
                }
                Ok(None) => break,
                _ => {
                    return Reply::json(
                        502,
                        json!({"error":"Хост вернул неполный или слишком большой ответ"}),
                    );
                }
            }
        }
        if matches!(path, "/api/auth/login" | "/api/auth/register") && status < 300 {
            let Ok(mut value) = serde_json::from_slice::<Value>(&bytes) else {
                return Reply::json(502, json!({"error":"Некорректный ответ входа"}));
            };
            let token = value
                .as_object_mut()
                .and_then(|v| v.remove("token"))
                .and_then(|v| v.as_str().map(String::from));
            let Some(token) = token else {
                return Reply::json(502, json!({"error":"Хост не подтвердил сеанс"}));
            };
            let mut s = self.session.write().await;
            s.token = Some(token);
            s.profile = Some(value["employee"].clone());
            return Reply::json(status, value);
        }
        if path == "/api/auth/logout" && status < 300 {
            let mut s = self.session.write().await;
            s.token = None;
            s.profile = None;
        }
        Reply {
            status,
            headers,
            body: STANDARD.encode(bytes),
        }
    }
    pub async fn discover(self: Arc<Self>) {
        loop {
            if self.session.read().await.endpoint.is_some() {
                tokio::time::sleep(Duration::from_secs(15)).await;
                continue;
            }
            let _ = self.connect("https://127.0.0.1:8743").await;
            if let Ok(sock) = tokio::net::UdpSocket::bind("0.0.0.0:0").await {
                let _ = sock.set_broadcast(true);
                let nonce = Uuid::new_v4().to_string();
                let packet = json!({"protocol":"nexsearch-discovery-1","nonce":nonce}).to_string();
                let _ = sock
                    .send_to(packet.as_bytes(), "255.255.255.255:8742")
                    .await;
                let deadline = tokio::time::Instant::now() + Duration::from_secs(3);
                let mut data = [0u8; 2048];
                while let Ok(Ok((size, peer))) =
                    tokio::time::timeout_at(deadline, sock.recv_from(&mut data)).await
                {
                    let Ok(info) = serde_json::from_slice::<Value>(&data[..size]) else {
                        continue;
                    };
                    let private = matches!(peer.ip(),std::net::IpAddr::V4(ip) if ip.is_private()||ip.is_loopback());
                    if private
                        && info["nonce"].as_str() == Some(&nonce)
                        && info["host_id"].as_str() == Some(&self.trust.host_id)
                        && info["fingerprint"].as_str() == Some(&self.trust.fingerprint)
                        && info["port"] == 8743
                    {
                        let _ = self.connect(&format!("https://{}:8743", peer.ip())).await;
                    }
                }
            }
            tokio::time::sleep(Duration::from_secs(12)).await;
        }
    }
}
pub fn valid_path(path: &str) -> bool {
    let route = path.split('?').next().unwrap_or("");
    route.starts_with("/api/")
        && !path.contains(['\\', '\r', '\n', '#'])
        && !route.contains("..")
        && !route.contains("://")
        && !route.to_ascii_lowercase().contains("%2e")
        && !route.to_ascii_lowercase().contains("%2f")
        && !route.to_ascii_lowercase().contains("%5c")
}

#[cfg(test)]
mod tests {
    use super::*;
    fn trust() -> Trust {
        Trust {
            host_id: "test-host".into(),
            fingerprint: "00".repeat(32),
        }
    }
    #[test]
    fn arbitrary_network_or_filesystem_paths_are_rejected() {
        for p in [
            "https://evil/api/a",
            "//evil/api/a",
            "/api/../secret",
            "/api/%2e%2e/secret",
            "/api/x\r\nHost: evil",
            "/api/x#fragment",
        ] {
            assert!(!valid_path(p));
        }
        assert!(valid_path("/api/leads?q=hello&limit=10"));
    }
    #[tokio::test]
    async fn offline_client_keeps_connection_screen_and_errors_honest() {
        let b = Bridge::new(trust()).unwrap();
        let r = b.request("/api/connection", "GET", None).await;
        let v: Value = serde_json::from_slice(&STANDARD.decode(r.body).unwrap()).unwrap();
        assert_eq!(v["host_found"], false);
        assert_eq!(b.request("/api/leads", "GET", None).await.status, 503);
        assert_eq!(
            b.request("/api/client/update/install", "POST", None)
                .await
                .status,
            409
        );
    }
    #[tokio::test]
    async fn invalid_trust_and_plain_http_never_send_credentials() {
        assert!(
            Bridge::new(Trust {
                host_id: "x".into(),
                fingerprint: "x".repeat(64)
            })
            .is_err()
        );
        let b = Bridge::new(trust()).unwrap();
        assert!(b.connect("http://127.0.0.1:1").await.is_err());
        assert!(b.connect("https://user:pass@example.org").await.is_err());
    }
}
