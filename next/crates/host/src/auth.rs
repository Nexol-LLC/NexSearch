use crate::{
    Host,
    db::{self, Employee},
    error::{ApiError, Result},
};
use argon2::{
    Algorithm, Argon2, Params, Version,
    password_hash::{PasswordHash, PasswordHasher, PasswordVerifier, SaltString},
};
use axum::http::StatusCode;
use base64::{Engine as _, engine::general_purpose::URL_SAFE_NO_PAD};
use rand::{RngCore, rngs::OsRng};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::time::{Duration, Instant};
use uuid::Uuid;

fn hasher() -> Argon2<'static> {
    Argon2::new(
        Algorithm::Argon2id,
        Version::V0x13,
        Params::new(65536, 3, 4, None).expect("Параметры Argon2id"),
    )
}
pub fn digest(token: &str) -> String {
    format!("{:x}", Sha256::digest(token.as_bytes()))
}
pub fn hash_password(password: &str) -> Result<String> {
    hasher()
        .hash_password(password.as_bytes(), &SaltString::generate(&mut OsRng))
        .map(|s| s.to_string())
        .map_err(|_| ApiError::invalid("Не удалось защитить пароль"))
}
pub async fn authenticate(host: &Host, token: &str, ip: &str) -> Result<Employee> {
    if token.is_empty() || token.len() > 256 {
        return Err(ApiError::unauthorized());
    }
    let mut tx = db::transaction(&host.pool, None, true).await?;
    let value:Option<Value>=sqlx::query_scalar("SELECT to_jsonb(e)||jsonb_build_object('device_id',s.device_id) FROM sessions s JOIN employees e ON e.id=s.employee_id WHERE s.token_hash=$1 AND s.expires_at>now() AND e.active")
        .bind(digest(token)).fetch_optional(&mut *tx).await?;
    let value = value.ok_or_else(ApiError::unauthorized)?;
    let person = Employee::from_row(value.clone())?;
    sqlx::query("UPDATE devices SET last_seen=now(),ip=$1 WHERE id=$2 AND last_seen<now()-interval '30 seconds'").bind(ip).bind(value["device_id"].as_str().unwrap_or("")).execute(&mut *tx).await?;
    tx.commit().await?;
    Ok(person)
}
async fn throttle(host: &Host, ip: &str, login: &str) -> Result<()> {
    let key = format!("{ip}:{}", login.to_lowercase());
    let now = Instant::now();
    let mut all = host.login_attempts.lock().await;
    all.retain(|_, v| {
        v.last()
            .is_some_and(|t| now.duration_since(*t) < Duration::from_secs(300))
    });
    if all.len() >= 4096 && !all.contains_key(&key) {
        return Err(ApiError(
            StatusCode::TOO_MANY_REQUESTS,
            "Слишком много запросов входа".into(),
        ));
    }
    let attempts = all.entry(key).or_default();
    attempts.retain(|t| now.duration_since(*t) < Duration::from_secs(300));
    if attempts.len() >= 12 {
        return Err(ApiError(
            StatusCode::TOO_MANY_REQUESTS,
            "Слишком много попыток. Повторите через 5 минут.".into(),
        ));
    }
    attempts.push(now);
    Ok(())
}
pub async fn enter(host: &Host, body: Value, register: bool, ip: &str) -> Result<Value> {
    // A password is opaque: trimming would lock out legacy accounts that use
    // leading or trailing spaces.
    let password = body["password"]
        .as_str()
        .filter(|s| !s.is_empty() && s.chars().count() <= 256)
        .ok_or_else(|| ApiError::invalid("Проверьте пароль"))?
        .to_owned();
    let login = db::text(&body, "login", 64, !register)?;
    if register && password.chars().count() < 8 {
        return Err(ApiError::invalid("Пароль: не меньше 8 символов"));
    }
    let device = db::text(&body, "device_id", 128, true)?;
    let computer = db::text(&body, "computer_name", 128, false)?;
    throttle(host, ip, if register { "register" } else { &login }).await?;
    // Bound both running and queued expensive password operations.
    let permit = host
        .password_slots
        .clone()
        .try_acquire_owned()
        .map_err(|_| {
            ApiError(
                StatusCode::TOO_MANY_REQUESTS,
                "Вход занят. Повторите через несколько секунд.".into(),
            )
        })?;
    let mut tx = db::transaction(&host.pool, None, true).await?;
    let person = if register {
        let first = db::text(&body, "first_name", 100, true)?;
        let last = db::text(&body, "last_name", 100, true)?;
        let role = db::text(&body, "requested_role", 20, false)?;
        let role = if role.is_empty() { "logist" } else { &role };
        if !["logist", "manager", "director"].contains(&role) {
            return Err(ApiError::invalid("Неизвестная должность"));
        }
        let hash = tokio::task::spawn_blocking(move || {
            let _permit = permit;
            hash_password(&password)
        })
        .await
        .map_err(|_| ApiError::invalid("Вход прерван"))??;
        sqlx::query("SELECT pg_advisory_xact_lock(73142002)")
            .execute(&mut *tx)
            .await?;
        let base = if login.is_empty() {
            login_slug(&first, &last)
        } else {
            login.clone()
        };
        if base.len() < 3
            || !base
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b))
        {
            return Err(ApiError::invalid("Проверьте логин"));
        }
        let mut candidate = base.clone();
        let mut counter = 1;
        while sqlx::query_scalar::<_, bool>("SELECT EXISTS(SELECT 1 FROM employees WHERE login=$1)")
            .bind(&candidate)
            .fetch_one(&mut *tx)
            .await?
        {
            if !login.is_empty() {
                return Err(ApiError::conflict("Этот логин уже занят"));
            }
            counter += 1;
            candidate = format!("{base}.{counter}");
        }
        let value:Value=sqlx::query_scalar("INSERT INTO employees(id,login,first_name,last_name,password_hash,requested_role) VALUES($1,$2,$3,$4,$5,$6) RETURNING to_jsonb(employees)")
            .bind(Uuid::new_v4()).bind(candidate).bind(first).bind(last).bind(hash).bind(role).fetch_one(&mut *tx).await?;
        Employee::from_row(value)?
    } else {
        let row: Option<Value> =
            sqlx::query_scalar("SELECT to_jsonb(e) FROM employees e WHERE login=$1")
                .bind(&login)
                .fetch_optional(&mut *tx)
                .await?;
        let hash = row
            .as_ref()
            .and_then(|r| r["password_hash"].as_str())
            .unwrap_or(&host.dummy_hash)
            .to_owned();
        let valid = tokio::task::spawn_blocking(move || {
            let _permit = permit;
            PasswordHash::new(&hash).is_ok_and(|h| {
                Argon2::default()
                    .verify_password(password.as_bytes(), &h)
                    .is_ok()
            })
        })
        .await
        .unwrap_or(false);
        let value = row
            .filter(|v| valid && v["active"] == true)
            .ok_or_else(|| {
                ApiError(StatusCode::UNAUTHORIZED, "Неверный логин или пароль".into())
            })?;
        Employee::from_row(value)?
    };
    let mut bytes = [0u8; 32];
    OsRng.fill_bytes(&mut bytes);
    let token = URL_SAFE_NO_PAD.encode(bytes);
    sqlx::query("INSERT INTO sessions(token_hash,employee_id,device_id,expires_at) VALUES($1,$2,$3,now()+interval '30 days')")
        .bind(digest(&token)).bind(person.id).bind(&device).execute(&mut *tx).await?;
    sqlx::query("INSERT INTO devices(id,employee_id,computer_name,ip) VALUES($1,$2,$3,$4) ON CONFLICT(id) DO UPDATE SET employee_id=excluded.employee_id,computer_name=excluded.computer_name,ip=excluded.ip,last_seen=now()")
        .bind(&device).bind(person.id).bind(&computer).bind(ip).execute(&mut *tx).await?;
    db::audit(
        &mut tx,
        person.id,
        if register {
            "account.register"
        } else {
            "account.login"
        },
        &person.id.to_string(),
        json!({"computer":computer}),
        ip,
    )
    .await?;
    tx.commit().await?;
    Ok(json!({"employee":person,"token":token}))
}
pub fn login_slug(first: &str, last: &str) -> String {
    let source = "абвгдеёжзийклмнопрстуфхцчшщъыьэюя";
    let target = [
        "a", "b", "v", "g", "d", "e", "yo", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r",
        "s", "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya",
    ];
    let mut output = String::new();
    for c in format!("{last}.{first}").to_lowercase().chars() {
        if let Some(i) = source.chars().position(|s| s == c) {
            output.push_str(target[i])
        } else if c.is_ascii_alphanumeric() || ".-_".contains(c) {
            output.push(c)
        }
    }
    output.truncate(output.len().min(55));
    if output == "." {
        "employee".into()
    } else {
        output
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn hash_and_login_match_existing_format() {
        assert_eq!(login_slug("Анна", "Первая"), "pervaya.anna");
        let hash = hash_password("пароль-для-проверки").unwrap();
        assert!(hash.starts_with("$argon2id$v=19$m=65536,t=3,p=4$"));
        assert!(
            Argon2::default()
                .verify_password(
                    "пароль-для-проверки".as_bytes(),
                    &PasswordHash::new(&hash).unwrap()
                )
                .is_ok()
        );
        assert!(!digest("секрет").contains("секрет"));
    }
}
