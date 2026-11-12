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
pub mod session_store;
use session_store::{SavedSession, SessionStore};

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
    remembered_endpoint: Option<String>,
    pending_legacy: Option<Value>,
    legacy_attempted: bool,
    token: Option<String>,
    profile: Option<Value>,
    device_id: String,
}
pub struct Bridge {
    client: reqwest::Client,
    trust: Trust,
    session: RwLock<Session>,
    device_id: String,
    csrf: String,
    store: RwLock<Option<SessionStore>>,
}
impl Bridge {
    pub fn host_id(&self) -> &str {
        &self.trust.host_id
    }
    /// Only the signed-update endpoint, over the already pinned transport.
    pub async fn download_update(
        &self,
        role: &str,
        digest: &str,
        size: u64,
        destination: &std::path::Path,
        progress: impl Fn(u64),
    ) -> Result<(), String> {
        if !matches!(role, "EmployeeWindows" | "EmployeeMac")
            || digest.len() != 64
            || !digest
                .bytes()
                .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase())
            || size == 0
            || size > 256 * 1024 * 1024
        {
            return Err("Некорректные параметры пакета".into());
        }
        let (endpoint, token) = {
            let s = self.session.read().await;
            (
                s.endpoint.clone().ok_or("Хост недоступен")?,
                s.token.clone().ok_or("Не выполнен вход")?,
            )
        };
        let mut response = self
            .client
            .get(format!(
                "{endpoint}/api/update/package?platform={role}&sha256={digest}"
            ))
            .bearer_auth(token)
            .timeout(Duration::from_secs(600))
            .send()
            .await
            .map_err(|_| "Не удалось скачать обновление")?;
        if !response.status().is_success() || response.content_length().is_some_and(|n| n != size) {
            return Err("Хост не подтвердил пакет обновления".into());
        }
        let parent = destination.parent().ok_or("Недоступна папка обновления")?;
        std::fs::create_dir_all(parent).map_err(|_| "Недоступна папка обновления")?;
        let temp = tempfile::NamedTempFile::new_in(parent)
            .map_err(|_| "Не удалось создать файл обновления")?;
        let mut file = tokio::fs::File::from_std(
            temp.as_file()
                .try_clone()
                .map_err(|_| "Не удалось открыть файл обновления")?,
        );
        use tokio::io::AsyncWriteExt;
        let mut count = 0u64;
        let mut hash = Sha256::new();
        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(|_| "Загрузка обновления прервана")?
        {
            count += chunk.len() as u64;
            if count > size {
                return Err("Пакет больше заявленного размера".into());
            }
            file.write_all(&chunk)
                .await
                .map_err(|_| "Не удалось записать обновление")?;
            hash.update(&chunk);
            progress(count);
        }
        if count != size || format!("{:x}", hash.finalize()) != digest {
            return Err("Контрольная сумма пакета не совпала".into());
        }
        file.sync_all()
            .await
            .map_err(|_| "Не удалось сохранить обновление")?;
        drop(file);
        temp.persist(destination)
            .map_err(|_| "Не удалось сохранить обновление")?;
        Ok(())
    }
    pub fn storage_scope(&self) -> String {
        format!(
            "{:x}",
            Sha256::digest(format!("{}:{}", self.trust.host_id, self.trust.fingerprint).as_bytes())
        )
    }
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
            store: RwLock::new(None),
        })
    }
    pub async fn configure_store(&self, path: std::path::PathBuf) -> Result<(), &'static str> {
        let store = SessionStore::new(path, &self.trust.host_id);
        if let Some(saved) = store.load()? {
            if saved.host_id != self.trust.host_id || saved.fingerprint != self.trust.fingerprint {
                return Err("Сохранённый сеанс относится к другому хосту");
            }
            let mut state = self.session.write().await;
            state.token = saved.token;
            state.profile = saved.profile;
            state.device_id = saved.device_id;
            state.remembered_endpoint = saved.endpoint;
        }
        *self.store.write().await = Some(store);
        Ok(())
    }
    async fn save_session(&self) -> Result<(), &'static str> {
        let s = self.session.read().await;
        if let Some(store) = self.store.read().await.as_ref() {
            store.save(&SavedSession {
                host_id: self.trust.host_id.clone(),
                fingerprint: self.trust.fingerprint.clone(),
                device_id: if s.device_id.is_empty() {
                    self.device_id.clone()
                } else {
                    s.device_id.clone()
                },
                token: s.token.clone(),
                profile: s.profile.clone(),
                endpoint: s.endpoint.clone().or_else(|| s.remembered_endpoint.clone()),
            })?;
        }
        Ok(())
    }
    pub async fn import_session(&self, path: &std::path::Path) -> Result<(), &'static str> {
        let saved = session_store::read_legacy(path)?;
        self.import_session_value(saved).await
    }
    async fn import_session_value(&self, saved: Value) -> Result<(), &'static str> {
        let token = saved["token"]
            .as_str()
            .filter(|s| !s.is_empty() && s.len() <= 256)
            .ok_or("Сеанс входа не найден")?;
        let endpoint = self
            .session
            .read()
            .await
            .endpoint
            .clone()
            .ok_or("Сначала подключите проверенный хост")?;
        let response = self
            .client
            .get(format!("{endpoint}/api/me"))
            .bearer_auth(token)
            .send()
            .await
            .map_err(|_| "Хост не подтвердил сеанс")?;
        if !response.status().is_success()
            || response.content_length().is_some_and(|n| n > 64 * 1024)
        {
            return Err("Сохранённый вход больше не действует");
        }
        let value: Value = response
            .json()
            .await
            .map_err(|_| "Некорректный ответ хоста")?;
        if !value["employee"].is_object() {
            return Err("Хост не подтвердил профиль сотрудника");
        }
        {
            let mut s = self.session.write().await;
            s.token = Some(token.into());
            s.profile = Some(value["employee"].clone());
            s.device_id = saved["device_id"]
                .as_str()
                .unwrap_or(&self.device_id)
                .into();
        }
        self.save_session().await
    }
    pub async fn has_session(&self) -> bool {
        self.session.read().await.token.is_some()
    }
    /// Prepare the legacy address without authorizing a cached token/profile.
    /// Trust is checked afresh when discovery reconnects to the address.
    pub async fn prepare_legacy_session(&self) {
        if self.has_session().await {
            return;
        }
        let Some(path) = session_store::legacy_session_path(&self.trust.host_id) else {
            return;
        };
        if !path.is_file() {
            return;
        }
        let Ok(saved) = session_store::read_legacy(&path) else {
            return;
        };
        let hint = saved["active_host_url"]
            .as_str()
            .or_else(|| saved["host_url"].as_str())
            .filter(|v| valid_endpoint(v))
            .map(str::to_owned);
        let mut session = self.session.write().await;
        if session.token.is_none() {
            if session.remembered_endpoint.is_none() {
                session.remembered_endpoint = hint;
            }
            session.pending_legacy = Some(saved);
        }
    }
    pub async fn reconnect_saved(&self) -> Result<(), &'static str> {
        let endpoint = self.session.read().await.remembered_endpoint.clone();
        if let Some(endpoint) = endpoint {
            self.connect(&endpoint).await
        } else {
            Err("Сохранённый адрес хоста не найден")
        }
    }
    async fn restore_legacy_if_needed(&self) {
        if self.has_session().await {
            return;
        }
        let pending = {
            let mut session = self.session.write().await;
            if session.legacy_attempted {
                return;
            }
            session.legacy_attempted = true;
            session.pending_legacy.take()
        };
        if let Some(pending) = pending {
            let _ = self.import_session_value(pending).await;
        }
    }
    pub async fn connect(&self, endpoint: &str) -> Result<(), &'static str> {
        if !valid_endpoint(endpoint) {
            return Err("Требуется защищённый адрес хоста");
        }
        let endpoint = endpoint.trim_end_matches('/');
        let response = self
            .client
            .get(format!("{endpoint}/api/host-info"))
            .timeout(Duration::from_secs(5))
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
        {
            let mut state = self.session.write().await;
            state.endpoint = Some(endpoint.into());
            state.remembered_endpoint = Some(endpoint.into());
        }
        self.save_session().await?;
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
                json!({"error":"Хост недоступен. Программа восстанавливает соединение."}),
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
            let device = self.session.read().await.device_id.clone();
            value["device_id"] = json!(if device.is_empty() {
                &self.device_id
            } else {
                &device
            });
            value["computer_name"] = json!("NexSearch · Rust");
            body = Some(value.to_string());
        }
        if let Some(body) = body
            && !matches!(method, "GET" | "HEAD")
        {
            request = request.body(body);
        }
        let Ok(mut response) = request.send().await else {
            let mut current = self.session.write().await;
            if current.endpoint.as_deref() == Some(&endpoint) {
                current.endpoint = None;
            }
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
            drop(s);
            let _ = self.save_session().await;
            return Reply::json(status, value);
        }
        if path == "/api/auth/logout" && status < 300 {
            let mut s = self.session.write().await;
            s.token = None;
            s.profile = None;
            drop(s);
            let _ = self.save_session().await;
        }
        Reply {
            status,
            headers,
            body: STANDARD.encode(bytes),
        }
    }
    pub async fn discover(self: Arc<Self>) {
        loop {
            let active = self.session.read().await.endpoint.clone();
            if let Some(endpoint) = active {
                self.restore_legacy_if_needed().await;
                if self.connect(&endpoint).await.is_err() {
                    let mut current = self.session.write().await;
                    if current.endpoint.as_deref() == Some(&endpoint) {
                        current.endpoint = None;
                    }
                }
                tokio::time::sleep(Duration::from_secs(10)).await;
                continue;
            }
            if self.reconnect_saved().await.is_ok() {
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
pub fn valid_endpoint(endpoint: &str) -> bool {
    endpoint.len() <= 2048
        && !endpoint
            .chars()
            .any(|c| c.is_control() || c.is_whitespace() || c == '\\')
        && url::Url::parse(endpoint).is_ok_and(|url| {
            url.scheme() == "https"
                && url.host_str().is_some()
                && url.username().is_empty()
                && url.password().is_none()
                && url.path() == "/"
                && url.query().is_none()
                && url.fragment().is_none()
        })
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
