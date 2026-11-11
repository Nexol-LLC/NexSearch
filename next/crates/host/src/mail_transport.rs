//! TLS-only mail sockets. DNS is resolved once and every returned address must
//! be public; the connection then uses that IP and verifies the original name.
use crate::error::{ApiError, Result};
use axum::http::StatusCode;
use std::{
    net::{IpAddr, SocketAddr},
    sync::{Arc, OnceLock},
    time::Duration,
};
use tokio::{
    io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader},
    net::TcpStream,
};
use tokio_rustls::{TlsConnector, client::TlsStream};
pub fn error(message: &str) -> ApiError {
    ApiError(StatusCode::BAD_GATEWAY, message.into())
}
pub fn public_ip(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(v) => {
            let a = v.octets();
            !(v.is_private()
                || v.is_loopback()
                || v.is_link_local()
                || v.is_broadcast()
                || v.is_documentation()
                || v.is_unspecified()
                || v.is_multicast()
                || a[0] == 0
                || a[0] >= 224
                || (a[0] == 100 && (64..=127).contains(&a[1]))
                || (a[0] == 192 && a[1] == 0 && a[2] == 0)
                || (a[0] == 198 && (a[1] == 18 || a[1] == 19)))
        }
        IpAddr::V6(v) => {
            if let Some(mapped) = v.to_ipv4_mapped() {
                return public_ip(mapped.into());
            }
            let s = v.segments();
            !(v.is_loopback()
                || v.is_unspecified()
                || v.is_multicast()
                || (s[0] & 0xfe00) == 0xfc00
                || (s[0] & 0xffc0) == 0xfe80
                || (s[0] & 0xffc0) == 0xfec0
                || s[0] == 0
                || (s[0] == 0x2001 && s[1] == 0x0db8)
                || (s[0] & 0xfff0) == 0x3ff0
                || s[0] == 0x2002
                || (s[0] == 0x64 && s[1] == 0xff9b))
        }
    }
}
pub async fn addresses(host: &str, port: u16) -> Result<Vec<SocketAddr>> {
    let mut addrs: Vec<_> = tokio::time::timeout(
        Duration::from_secs(5),
        tokio::net::lookup_host((host, port)),
    )
    .await
    .map_err(|_| error("Не удалось разрешить адрес сервера"))?
    .map_err(|_| error("Сервер не найден"))?
    .collect();
    if addrs.is_empty() || addrs.iter().any(|a| !public_ip(a.ip())) {
        return Err(ApiError::invalid(
            "Нужен публичный сервер. Локальные, служебные и смешанные DNS-адреса запрещены.",
        ));
    }
    addrs.sort_by_key(|a| a.is_ipv6());
    addrs.truncate(8);
    Ok(addrs)
}
fn tls_config() -> Result<Arc<rustls::ClientConfig>> {
    static CONFIG: OnceLock<std::result::Result<Arc<rustls::ClientConfig>, ()>> = OnceLock::new();
    CONFIG
        .get_or_init(|| {
            let mut roots = rustls::RootCertStore::empty();
            for cert in rustls_native_certs::load_native_certs().certs {
                let _ = roots.add(cert);
            }
            if roots.is_empty() {
                return Err(());
            }
            rustls::ClientConfig::builder_with_provider(Arc::new(
                rustls::crypto::ring::default_provider(),
            ))
            .with_safe_default_protocol_versions()
            .map(|b| Arc::new(b.with_root_certificates(roots).with_no_client_auth()))
            .map_err(|_| ())
        })
        .clone()
        .map_err(|_| error("Системные доверенные сертификаты недоступны"))
}
pub struct Socket {
    pub reader: BufReader<TlsStream<TcpStream>>,
    tag: u32,
}
pub struct Part {
    pub line: String,
    pub literal: Option<Vec<u8>>,
}
pub fn quoted(value: &str) -> Result<String> {
    if value.len() > 2048 || value.chars().any(char::is_control) {
        return Err(ApiError::invalid("Недопустимые символы в почтовой команде"));
    }
    Ok(format!(
        "\"{}\"",
        value.replace('\\', "\\\\").replace('"', "\\\"")
    ))
}
impl Socket {
    pub async fn connect(host: &str, port: u16) -> Result<Self> {
        let dns = url::Host::parse(host)
            .map_err(|_| ApiError::invalid("Некорректный почтовый сервер"))?
            .to_string();
        let addrs = addresses(&dns, port).await?;
        let connector = TlsConnector::from(tls_config()?);
        for address in addrs {
            let Ok(Ok(stream)) =
                tokio::time::timeout(Duration::from_secs(3), TcpStream::connect(address)).await
            else {
                continue;
            };
            let name = rustls::pki_types::ServerName::try_from(dns.clone())
                .map_err(|_| ApiError::invalid("Некорректный сервер TLS"))?;
            let stream =
                tokio::time::timeout(Duration::from_secs(5), connector.connect(name, stream))
                    .await
                    .map_err(|_| error("Сервер не завершил TLS-подключение"))?
                    .map_err(|_| error("Не подтверждён TLS-сертификат почтового сервера"))?;
            return Ok(Self {
                reader: BufReader::new(stream),
                tag: 0,
            });
        }
        Err(error("Почтовый сервер не отвечает"))
    }
    pub async fn line(&mut self) -> Result<String> {
        let mut bytes = vec![];
        let mut limited = (&mut self.reader).take(2 * 1024 * 1024 + 1);
        let n = limited
            .read_until(b'\n', &mut bytes)
            .await
            .map_err(|_| error("Почтовое соединение прервано"))?;
        if n == 0 || n > 2 * 1024 * 1024 || bytes.last() != Some(&b'\n') {
            return Err(error("Почтовый сервер вернул некорректную строку"));
        }
        Ok(String::from_utf8_lossy(&bytes).into_owned())
    }
    pub async fn write(&mut self, bytes: &[u8]) -> Result<()> {
        self.reader
            .get_mut()
            .write_all(bytes)
            .await
            .map_err(|_| error("Не удалось передать почтовую команду"))?;
        self.reader
            .get_mut()
            .flush()
            .await
            .map_err(|_| error("Почтовое соединение прервано"))
    }
    pub async fn imap_login(&mut self, login: &str, password: &str) -> Result<()> {
        if !self.line().await?.starts_with("* OK") {
            return Err(error("IMAP-сервер не подтвердил готовность"));
        }
        self.command(
            &format!("LOGIN {} {}", quoted(login)?, quoted(password)?),
            256 * 1024,
        )
        .await
        .map_err(|_| {
            error("IMAP не подтвердил вход. Проверьте пароль приложения и доступ IMAP.")
        })?;
        Ok(())
    }
    pub async fn command(&mut self, command: &str, limit: usize) -> Result<Vec<Part>> {
        if command.contains(['\r', '\n']) {
            return Err(ApiError::invalid("Некорректная почтовая команда"));
        }
        self.tag += 1;
        let tag = format!("NXS{:06}", self.tag);
        self.write(format!("{tag} {command}\r\n").as_bytes())
            .await?;
        self.response(&tag, limit).await
    }
    async fn response(&mut self, tag: &str, limit: usize) -> Result<Vec<Part>> {
        let mut rows = vec![];
        let mut used = 0;
        loop {
            let line = self.line().await?;
            used += line.len();
            if used > limit {
                return Err(error("Почтовый ответ превышает допустимый размер"));
            }
            if line.starts_with(&format!("{tag} ")) {
                if line.starts_with(&format!("{tag} OK")) {
                    return Ok(rows);
                }
                return Err(error("Почтовый сервер отклонил операцию"));
            }
            if line.starts_with("* BYE") {
                return Err(error("IMAP завершил соединение; повторите чтение позже"));
            }
            let literal = if let Some(length) = literal_size(&line)? {
                if length > limit.saturating_sub(used) {
                    return Err(error("Письмо превышает допустимый размер"));
                }
                let mut bytes = vec![0; length];
                self.reader
                    .read_exact(&mut bytes)
                    .await
                    .map_err(|_| error("Письмо загружено не полностью"))?;
                used += length;
                Some(bytes)
            } else {
                None
            };
            rows.push(Part { line, literal });
        }
    }
    pub async fn append(&mut self, folder: &str, raw: &[u8]) -> Result<()> {
        self.tag += 1;
        let tag = format!("NXS{:06}", self.tag);
        self.write(
            format!(
                "{tag} APPEND {} (\\Seen) {{{}}}\r\n",
                quoted(folder)?,
                raw.len()
            )
            .as_bytes(),
        )
        .await?;
        if !self.line().await?.starts_with('+') {
            return Err(error("Сервер не разрешил сохранить отправленное письмо"));
        }
        self.write(raw).await?;
        self.write(b"\r\n").await?;
        self.response(&tag, 256 * 1024).await?;
        Ok(())
    }
    pub async fn smtp_reply(&mut self) -> Result<u16> {
        for _ in 0..100 {
            let line = self.line().await?;
            let code = line
                .get(..3)
                .and_then(|s| s.parse::<u16>().ok())
                .ok_or_else(|| error("Некорректный ответ SMTP"))?;
            if line.as_bytes().get(3) != Some(&b'-') {
                return Ok(code);
            }
        }
        Err(error("Слишком большой ответ SMTP"))
    }
    pub async fn smtp(&mut self, command: &str, expected: &[u16]) -> Result<u16> {
        if command.contains(['\r', '\n']) {
            return Err(ApiError::invalid("Некорректная SMTP-команда"));
        }
        self.write(format!("{command}\r\n").as_bytes()).await?;
        let code = self.smtp_reply().await?;
        if !expected.contains(&code) {
            return Err(error(
                "SMTP не подтвердил операцию. Проверьте отправленные перед повторной отправкой.",
            ));
        }
        Ok(code)
    }
}
fn literal_size(line: &str) -> Result<Option<usize>> {
    let Some(end) = line.trim_end_matches(['\r', '\n']).strip_suffix('}') else {
        return Ok(None);
    };
    let Some((_, digits)) = end.rsplit_once('{') else {
        return Ok(None);
    };
    let digits = digits.strip_suffix('+').unwrap_or(digits);
    if digits.is_empty() || !digits.bytes().all(|v| v.is_ascii_digit()) {
        return Ok(None);
    }
    digits
        .parse()
        .map(Some)
        .map_err(|_| error("Некорректный размер почтового ответа"))
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejects_all_local_and_ambiguous_socket_targets() {
        for value in [
            "127.0.0.1",
            "10.0.0.1",
            "192.168.0.102",
            "169.254.169.254",
            "100.64.0.1",
            "0.0.0.0",
            "198.18.0.1",
            "224.0.0.1",
            "::1",
            "::ffff:127.0.0.1",
            "fc00::1",
            "fe80::1",
            "2001:db8::1",
            "64:ff9b::7f00:1",
        ] {
            assert!(!public_ip(value.parse().unwrap()), "{value}")
        }
        for value in ["77.88.21.158", "8.8.8.8", "2606:4700:4700::1111"] {
            assert!(public_ip(value.parse().unwrap()), "{value}")
        }
        assert!(quoted("bad\r\nLOGIN secret").is_err());
    }
}
