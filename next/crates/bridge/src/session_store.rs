//! OS-protected storage; never falls back to plaintext tokens.
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::path::{Path, PathBuf};

#[derive(Serialize, Deserialize, Default)]
pub struct SavedSession {
    pub host_id: String,
    pub fingerprint: String,
    pub device_id: String,
    pub token: Option<String>,
    pub profile: Option<Value>,
}
pub struct SessionStore {
    path: PathBuf,
    account: String,
}
impl SessionStore {
    pub fn new(path: PathBuf, host_id: &str) -> Self {
        Self {
            path,
            account: host_id.into(),
        }
    }
    pub fn load(&self) -> Result<Option<SavedSession>, &'static str> {
        #[cfg(windows)]
        {
            let _ = &self.account;
            if !self.path.exists() {
                return Ok(None);
            }
            let value = read_dpapi(&self.path)?;
            serde_json::from_value(value)
                .map(Some)
                .map_err(|_| "Повреждён защищённый сеанс")
        }
        #[cfg(not(windows))]
        {
            let _ = &self.path;
            match keyring::Entry::new("ru.nexol.nexsearch.session", &self.account)
                .map_err(|_| "Хранилище сеанса недоступно")?
                .get_password()
            {
                Ok(value) => serde_json::from_str(&value)
                    .map(Some)
                    .map_err(|_| "Повреждён защищённый сеанс"),
                Err(keyring::Error::NoEntry) => Ok(None),
                Err(_) => Err("Системное хранилище сеанса недоступно"),
            }
        }
    }
    pub fn save(&self, value: &SavedSession) -> Result<(), &'static str> {
        let bytes = serde_json::to_vec(value).map_err(|_| "Не удалось подготовить сеанс")?;
        #[cfg(windows)]
        {
            use std::io::Write;
            let encrypted = windows_dpapi::encrypt_data(&bytes, windows_dpapi::Scope::User, None)
                .map_err(|_| "Не удалось защитить сеанс Windows")?;
            let parent = self.path.parent().ok_or("Недоступна папка сеанса")?;
            std::fs::create_dir_all(parent).map_err(|_| "Недоступна папка сеанса")?;
            let mut temp = tempfile::NamedTempFile::new_in(parent)
                .map_err(|_| "Не удалось сохранить сеанс")?;
            temp.write_all(b"DPAPI")
                .and_then(|_| temp.write_all(&encrypted))
                .and_then(|_| temp.as_file().sync_all())
                .map_err(|_| "Не удалось сохранить сеанс")?;
            temp.persist(&self.path)
                .map_err(|_| "Не удалось сохранить сеанс")?;
            Ok(())
        }
        #[cfg(not(windows))]
        {
            keyring::Entry::new("ru.nexol.nexsearch.session", &self.account)
                .map_err(|_| "Хранилище сеанса недоступно")?
                .set_password(std::str::from_utf8(&bytes).map_err(|_| "Некорректный сеанс")?)
                .map_err(|_| "Не удалось защитить сеанс")
        }
    }
}
pub fn read_dpapi(path: &Path) -> Result<Value, &'static str> {
    #[cfg(windows)]
    {
        let bytes = std::fs::read(path).map_err(|_| "Не удалось открыть защищённый сеанс")?;
        if bytes.len() > 1024 * 1024 || !bytes.starts_with(b"DPAPI") {
            return Err("Неподдерживаемый защищённый сеанс");
        }
        let plain = windows_dpapi::decrypt_data(&bytes[5..], windows_dpapi::Scope::User, None)
            .map_err(|_| "Сеанс принадлежит другому пользователю")?;
        serde_json::from_slice(&plain).map_err(|_| "Повреждён защищённый сеанс")
    }
    #[cfg(not(windows))]
    {
        let _ = path;
        Err("Импорт сеанса Windows доступен только на Windows")
    }
}
#[cfg(all(test, windows))]
mod tests {
    use super::*;
    #[test]
    fn protected_roundtrip_never_writes_plaintext_token() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("session.sec");
        let store = SessionStore::new(path.clone(), "test");
        let value = SavedSession {
            host_id: "test".into(),
            fingerprint: "pin".into(),
            device_id: "device".into(),
            token: Some("private-local-test-token".into()),
            profile: None,
        };
        store.save(&value).unwrap();
        assert_eq!(store.load().unwrap().unwrap().token, value.token);
        assert!(
            !String::from_utf8_lossy(&std::fs::read(path).unwrap())
                .contains("private-local-test-token")
        );
    }
}
