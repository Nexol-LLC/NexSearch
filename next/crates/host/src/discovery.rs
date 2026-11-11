//! Discovery is an address hint, never authentication. Employees still check
//! the certificate fingerprint and host id over TLS before using a session.
use crate::{Config, Host};
use base64::{Engine as _, engine::general_purpose::STANDARD};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::HashMap,
    net::{IpAddr, SocketAddr},
    sync::Arc,
    time::{Duration, Instant},
};
pub fn fingerprint(config: &Config) -> Result<String, &'static str> {
    let pem =
        std::fs::read_to_string(&config.tls_cert).map_err(|_| "Сертификат хоста недоступен")?;
    let encoded = pem
        .split_once("-----BEGIN CERTIFICATE-----")
        .and_then(|(_, v)| v.split_once("-----END CERTIFICATE-----"))
        .map(|(v, _)| {
            v.chars()
                .filter(|c| !c.is_ascii_whitespace())
                .collect::<String>()
        })
        .ok_or("Сертификат хоста повреждён")?;
    let bytes = STANDARD
        .decode(encoded)
        .map_err(|_| "Сертификат хоста повреждён")?;
    Ok(format!("{:x}", Sha256::digest(bytes)))
}
pub fn reply(data: &[u8], host_id: uuid::Uuid, fingerprint: &str, port: u16) -> Option<Vec<u8>> {
    if data.len() > 2048 {
        return None;
    }
    let value: Value = serde_json::from_slice(data).ok()?;
    if value["protocol"] != "nexsearch-discovery-1" {
        return None;
    }
    let nonce = value["nonce"]
        .as_str()
        .filter(|s| s.len() <= 64 && uuid::Uuid::parse_str(s).is_ok())?;
    serde_json::to_vec(
        &json!({"host_id":host_id,"fingerprint":fingerprint,"port":port,"nonce":nonce}),
    )
    .ok()
}
pub async fn run(host: Arc<Host>) {
    // Isolated loopback test hosts must not compete with a running office host.
    if host.config.bind.ip().is_loopback() {
        return;
    }
    let Ok(fingerprint) = fingerprint(&host.config) else {
        tracing::warn!("Автообнаружение: не удалось прочитать сертификат");
        return;
    };
    let socket = match tokio::net::UdpSocket::bind("0.0.0.0:8742").await {
        Ok(v) => v,
        Err(_) => {
            tracing::warn!("Порт автообнаружения 8742/UDP занят или недоступен");
            return;
        }
    };
    let mut recent: HashMap<IpAddr, Instant> = HashMap::new();
    let mut data = [0; 2049];
    loop {
        let Ok((size, peer)) = socket.recv_from(&mut data).await else {
            tokio::time::sleep(Duration::from_secs(1)).await;
            continue;
        };
        if peer.port() < 1024
            || !matches!(peer.ip(),IpAddr::V4(ip) if ip.is_private()||ip.is_loopback())
        {
            continue;
        }
        let now = Instant::now();
        recent.retain(|_, at| now.duration_since(*at) < Duration::from_secs(10));
        if recent
            .get(&peer.ip())
            .is_some_and(|at| now.duration_since(*at) < Duration::from_secs(1))
            || recent.len() >= 1024
        {
            continue;
        }
        if let Some(response) = reply(
            &data[..size],
            host.config.host_id,
            &fingerprint,
            host.config.bind.port(),
        ) {
            recent.insert(peer.ip(), now);
            let _ = socket
                .send_to(&response, SocketAddr::new(peer.ip(), peer.port()))
                .await;
        }
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn only_current_nonce_is_echoed_without_accepting_identity_from_a_packet() {
        let id = uuid::Uuid::new_v4();
        let nonce = uuid::Uuid::new_v4();
        let packet=json!({"protocol":"nexsearch-discovery-1","nonce":nonce,"host_id":"forged","fingerprint":"forged"}).to_string();
        let actual: Value =
            serde_json::from_slice(&reply(packet.as_bytes(), id, "public-pin", 8743).unwrap())
                .unwrap();
        assert_eq!(actual["nonce"], nonce.to_string());
        assert_eq!(actual["host_id"], id.to_string());
        assert_eq!(actual["fingerprint"], "public-pin");
        assert!(reply(b"{\"protocol\":\"wrong\"}", id, "pin", 8743).is_none());
        assert!(reply(&vec![b' '; 2049], id, "pin", 8743).is_none());
    }
}
