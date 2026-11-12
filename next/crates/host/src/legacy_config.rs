//! One-time compatibility with the existing systemd EnvironmentFile.
//! The old database, TLS identity and encrypted mailbox credentials are reused.
use crate::Config;
use std::{collections::BTreeMap, path::PathBuf};

fn keywords(value: &str) -> Result<BTreeMap<String, String>, String> {
    let mut values = BTreeMap::new();
    let mut chars = value.chars().peekable();
    while chars.peek().is_some() {
        while chars.peek().is_some_and(|c| c.is_whitespace()) {
            chars.next();
        }
        if chars.peek().is_none() {
            break;
        }
        let mut key = String::new();
        while chars
            .peek()
            .is_some_and(|c| c.is_ascii_alphanumeric() || *c == '_')
        {
            key.push(chars.next().unwrap());
        }
        while chars.peek().is_some_and(|c| c.is_whitespace()) {
            chars.next();
        }
        if key.is_empty() || chars.next() != Some('=') {
            return Err("Некорректные настройки PostgreSQL".into());
        }
        while chars.peek().is_some_and(|c| c.is_whitespace()) {
            chars.next();
        }
        let quoted = chars.peek() == Some(&'\'');
        if quoted {
            chars.next();
        }
        let mut item = String::new();
        let mut closed = !quoted;
        while let Some(c) = chars.next() {
            match c {
                '\\' => item.push(
                    chars
                        .next()
                        .ok_or("Некорректная escape-последовательность PostgreSQL")?,
                ),
                '\'' if quoted => {
                    closed = true;
                    break;
                }
                c if !quoted && c.is_whitespace() => break,
                c => item.push(c),
            }
        }
        if !closed {
            return Err("Незакрытое значение PostgreSQL".into());
        }
        values.insert(key, item);
    }
    Ok(values)
}
pub fn database_url(value: &str) -> Result<String, String> {
    if value.starts_with("postgres://") || value.starts_with("postgresql://") {
        return Ok(value.into());
    }
    let items = keywords(value)?;
    let mut url =
        url::Url::parse("postgres://127.0.0.1/nexsearch").map_err(|_| "Некорректный адрес базы")?;
    for key in items.keys() {
        if !matches!(
            key.as_str(),
            "host"
                | "port"
                | "dbname"
                | "user"
                | "password"
                | "sslmode"
                | "connect_timeout"
                | "application_name"
        ) {
            return Err(
                "Неподдерживаемый параметр PostgreSQL; требуется явный файл настроек".into(),
            );
        }
    }
    if let Some(host) = items.get("host") {
        url.set_host(Some(host))
            .map_err(|_| "Неподдерживаемый адрес PostgreSQL")?;
    }
    if let Some(port) = items.get("port") {
        url.set_port(Some(
            port.parse().map_err(|_| "Некорректный порт PostgreSQL")?,
        ))
        .map_err(|_| "Некорректный порт PostgreSQL")?;
    }
    if let Some(name) = items.get("dbname") {
        url.path_segments_mut()
            .map_err(|_| "Некорректная база PostgreSQL")?
            .clear()
            .push(name);
    }
    if let Some(user) = items.get("user") {
        url.set_username(user)
            .map_err(|_| "Некорректный пользователь PostgreSQL")?;
    }
    if let Some(password) = items.get("password") {
        url.set_password(Some(password))
            .map_err(|_| "Некорректные настройки PostgreSQL")?;
    }
    for key in ["sslmode", "connect_timeout", "application_name"] {
        if let Some(v) = items.get(key) {
            url.query_pairs_mut().append_pair(key, v);
        }
    }
    Ok(url.into())
}
pub fn from_environment() -> Result<Config, String> {
    let home =
        PathBuf::from(std::env::var_os("NEXOL_HOME").ok_or("Не указана существующая папка хоста")?);
    let identity: serde_json::Value = serde_json::from_slice(
        &std::fs::read(home.join("identity/host-identity.json"))
            .map_err(|_| "Не найдена существующая идентичность хоста")?,
    )
    .map_err(|_| "Повреждена идентичность хоста")?;
    let program = std::env::current_exe()
        .map_err(|_| "Не найдена папка программы")?
        .parent()
        .ok_or("Не найдена папка программы")?
        .to_owned();
    let port = std::env::var("NEXOL_HOST_PORT")
        .unwrap_or_else(|_| "8743".into())
        .parse::<u16>()
        .map_err(|_| "Некорректный порт хоста")?;
    Ok(Config {
        database_url: database_url(
            &std::env::var("NEXOL_DATABASE_URL")
                .map_err(|_| "Не указана существующая PostgreSQL")?,
        )?,
        host_id: identity["host_id"]
            .as_str()
            .ok_or("Нет идентификатора хоста")?
            .parse()
            .map_err(|_| "Некорректный идентификатор хоста")?,
        bind: std::net::SocketAddr::from(([0, 0, 0, 0], port)),
        tls_cert: home.join("identity/host-cert.pem"),
        tls_key: home.join("identity/host-key.pem"),
        secret_file: home.join("host-secrets.enc"),
        web_dir: program.join("network/web"),
        pack_dir: program.join("pack"),
        update_dir: std::env::var_os("NEXSEARCH_UPDATE_STATE")
            .map(PathBuf::from)
            .and_then(|p| p.parent().map(std::path::Path::to_owned))
            .unwrap_or_else(|| PathBuf::from("/var/lib/nexsearch-updates")),
        source_access: true,
    })
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn legacy_libpq_password_quotes_are_not_changed_or_executed() {
        let url=database_url(r"host=127.0.0.1 port=5432 dbname=nexsearch user=app password='a b\'c\\d$()' sslmode=disable").unwrap();
        let parsed = url::Url::parse(&url).unwrap();
        assert_eq!(
            percent_encoding::percent_decode_str(parsed.password().unwrap())
                .decode_utf8()
                .unwrap(),
            "a b'c\\d$()"
        );
        assert_eq!(parsed.port(), Some(5432));
        assert_eq!(parsed.query(), Some("sslmode=disable"));
        assert!(database_url("user=bad password='missing").is_err());
    }
}
