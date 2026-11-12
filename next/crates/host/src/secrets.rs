//! Byte-compatible with 1.6.7 host settings and encrypted mailbox passwords.
use crate::{
    Config,
    error::{ApiError, Result},
};
use aes_gcm::{
    Aes256Gcm, Nonce,
    aead::{Aead, KeyInit, Payload},
};
use rand::{RngCore, rngs::OsRng};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::io::Write;
fn cipher(config: &Config, suffix: &[u8]) -> Result<Aes256Gcm> {
    let mut key =
        std::fs::read(&config.tls_key).map_err(|_| ApiError::invalid("Ключ хоста недоступен"))?;
    key.extend_from_slice(suffix);
    Aes256Gcm::new_from_slice(&Sha256::digest(key))
        .map_err(|_| ApiError::invalid("Ключ хранилища недоступен"))
}
pub fn load(config: &Config) -> Result<Value> {
    if !config.secret_file.exists() {
        return Ok(serde_json::json!({}));
    }
    let raw = std::fs::read(&config.secret_file)
        .map_err(|_| ApiError::invalid("Настройки хоста недоступны"))?;
    if raw.len() < 36 || raw.len() > 1024 * 1024 || !raw.starts_with(b"NXSSEC13") {
        return Err(ApiError::invalid("Повреждены защищённые настройки хоста"));
    }
    let value = cipher(config, b"NexSearch host-only secrets 1.3")?
        .decrypt(
            Nonce::from_slice(&raw[8..20]),
            Payload {
                msg: &raw[20..],
                aad: b"NXSSEC13",
            },
        )
        .map_err(|_| {
            ApiError::invalid("Не удалось расшифровать настройки исходным ключом хоста")
        })?;
    serde_json::from_slice(&value).map_err(|_| ApiError::invalid("Повреждены настройки хоста"))
}
pub fn save(config: &Config, value: &Value) -> Result<()> {
    let mut nonce = [0u8; 12];
    OsRng.fill_bytes(&mut nonce);
    let plain = serde_json::to_vec(value)
        .map_err(|_| ApiError::invalid("Не удалось подготовить настройки"))?;
    let ciphertext = cipher(config, b"NexSearch host-only secrets 1.3")?
        .encrypt(
            Nonce::from_slice(&nonce),
            Payload {
                msg: &plain,
                aad: b"NXSSEC13",
            },
        )
        .map_err(|_| ApiError::invalid("Не удалось защитить настройки"))?;
    let parent = config
        .secret_file
        .parent()
        .ok_or_else(|| ApiError::invalid("Неверная папка настроек"))?;
    std::fs::create_dir_all(parent).map_err(|_| ApiError::invalid("Папка настроек недоступна"))?;
    let mut pending = tempfile::NamedTempFile::new_in(parent)
        .map_err(|_| ApiError::invalid("Не удалось записать настройки"))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        pending
            .as_file()
            .set_permissions(std::fs::Permissions::from_mode(0o600))
            .map_err(|_| ApiError::invalid("Не удалось защитить настройки"))?;
    }
    pending
        .write_all(b"NXSSEC13")
        .and_then(|_| pending.write_all(&nonce))
        .and_then(|_| pending.write_all(&ciphertext))
        .and_then(|_| pending.as_file().sync_all())
        .map_err(|_| ApiError::invalid("Не удалось записать настройки"))?;
    pending
        .persist(&config.secret_file)
        .map_err(|_| ApiError::invalid("Не удалось сохранить настройки"))?;
    Ok(())
}
pub fn mail_password(
    config: &Config,
    owner: uuid::Uuid,
    account: uuid::Uuid,
    raw: &[u8],
) -> Result<String> {
    if raw.len() < 28 || raw.len() > 16384 {
        return Err(ApiError::invalid("Повреждён пароль ящика"));
    }
    let aad = format!("{owner}:{account}");
    let plain = cipher(config, b"NexSearch personal mail 1.6")?
        .decrypt(
            Nonce::from_slice(&raw[..12]),
            Payload {
                msg: &raw[12..],
                aad: aad.as_bytes(),
            },
        )
        .map_err(|_| ApiError::invalid("Пароль ящика не расшифрован исходным ключом хоста"))?;
    String::from_utf8(plain).map_err(|_| ApiError::invalid("Повреждён пароль ящика"))
}
pub fn seal_mail_password(
    config: &Config,
    owner: uuid::Uuid,
    account: uuid::Uuid,
    password: &str,
) -> Result<Vec<u8>> {
    let mut nonce = [0u8; 12];
    OsRng.fill_bytes(&mut nonce);
    let aad = format!("{owner}:{account}");
    let encrypted = cipher(config, b"NexSearch personal mail 1.6")?
        .encrypt(
            Nonce::from_slice(&nonce),
            Payload {
                msg: password.as_bytes(),
                aad: aad.as_bytes(),
            },
        )
        .map_err(|_| ApiError::invalid("Не удалось защитить пароль"))?;
    Ok([nonce.to_vec(), encrypted].concat())
}
