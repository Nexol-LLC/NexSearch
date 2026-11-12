use crate::{
    Host, RequestContext,
    crm::path_id,
    db,
    error::{ApiError, Result},
    json_response, mail_data, mail_ops,
};
use axum::{
    http::{StatusCode, header},
    response::{IntoResponse, Response},
};
use serde_json::{Value, json};
use std::{
    collections::HashMap,
    sync::Arc,
    time::{Duration, Instant},
};
use tokio::sync::{Mutex, OwnedMutexGuard, OwnedSemaphorePermit, Semaphore};
use uuid::Uuid;
type CachedImage = (Instant, String, Vec<u8>);
pub struct Runtime {
    gates: Mutex<HashMap<Uuid, Arc<Mutex<()>>>>,
    slots: Arc<Semaphore>,
    pub due: Mutex<HashMap<Uuid, Instant>>,
    pub images: Mutex<HashMap<String, CachedImage>>,
    pub image_slots: Semaphore,
}
impl Default for Runtime {
    fn default() -> Self {
        Self {
            gates: Mutex::new(HashMap::new()),
            slots: Arc::new(Semaphore::new(3)),
            due: Mutex::new(HashMap::new()),
            images: Mutex::new(HashMap::new()),
            image_slots: Semaphore::new(4),
        }
    }
}
impl Runtime {
    pub async fn guard(&self, id: Uuid) -> Result<(OwnedMutexGuard<()>, OwnedSemaphorePermit)> {
        let lock = self
            .gates
            .lock()
            .await
            .entry(id)
            .or_insert_with(|| Arc::new(Mutex::new(())))
            .clone();
        let guard = lock.try_lock_owned().map_err(|_| {
            ApiError::conflict("Почта уже обновляется. Повторите действие через несколько секунд.")
        })?;
        let slot =
            tokio::time::timeout(Duration::from_secs(10), self.slots.clone().acquire_owned())
                .await
                .map_err(|_| {
                    ApiError(
                        StatusCode::SERVICE_UNAVAILABLE,
                        "Почтовый сервер занят. Повторите позже.".into(),
                    )
                })?
                .map_err(|_| ApiError::invalid("Почтовый модуль завершает работу"))?;
        Ok((guard, slot))
    }
}
pub struct Account {
    pub info: Value,
    pub secret: Vec<u8>,
}
impl Account {
    pub fn id(&self) -> Result<Uuid> {
        db::id(&self.info, "id")
    }
    pub fn owner(&self) -> Result<Uuid> {
        db::id(&self.info, "employee_id")
    }
    pub fn text(&self, key: &str) -> &str {
        self.info[key].as_str().unwrap_or("")
    }
}
pub async fn route(c: &RequestContext<'_>) -> Result<Option<Response>> {
    if c.parts.get(1) != Some(&"mail") {
        return Ok(None);
    }
    let run = async {
        let value = match (c.method, c.parts.as_slice()) {
            ("GET", ["api", "mail", "accounts"]) => mail_data::accounts(c).await?,
            ("POST", ["api", "mail", "accounts"]) => mail_ops::connect(c).await?,
            ("DELETE", ["api", "mail", "accounts", id]) => {
                let mut tx = db::transaction(&c.host.pool, Some(c.actor), false).await?;
                let id = path_id(id)?;
                sqlx::query_scalar::<_, Uuid>(
                    "DELETE FROM mail_accounts WHERE id=$1 AND employee_id=$2 RETURNING id",
                )
                .bind(id)
                .bind(c.actor.id)
                .fetch_one(&mut *tx)
                .await?;
                db::audit(
                    &mut tx,
                    c.actor.id,
                    "mail.disconnect",
                    &id.to_string(),
                    json!({}),
                    c.ip,
                )
                .await?;
                tx.commit().await?;
                json!({"ok":true})
            }
            ("PATCH", ["api", "mail", "accounts", id, "settings"]) => {
                mail_ops::settings(c, path_id(id)?).await?
            }
            ("POST", ["api", "mail", "accounts", id, "sync"]) => {
                let id = path_id(id)?;
                let folder = c.body["folder"].as_str().unwrap_or("INBOX");
                match mail_ops::sync(
                    c.host,
                    c.actor.id,
                    id,
                    folder,
                    c.body["before_uid"].as_i64(),
                )
                .await
                {
                    Ok(v) => v,
                    Err(error) if error.0 == StatusCode::CONFLICT => {
                        json!({"syncing":true,"loaded":0,"folder":folder})
                    }
                    Err(error) => {
                        let mut tx = db::transaction(&c.host.pool, Some(c.actor), false).await?;
                        sqlx::query(
                            "UPDATE mail_accounts SET last_error=$1 WHERE id=$2 AND employee_id=$3",
                        )
                        .bind(&error.1)
                        .bind(id)
                        .bind(c.actor.id)
                        .execute(&mut *tx)
                        .await?;
                        tx.commit().await?;
                        return Err(error);
                    }
                }
            }
            ("POST", ["api", "mail", "accounts", id, "folders"]) => {
                mail_ops::create_folder(c, path_id(id)?).await?
            }
            ("POST", ["api", "mail", "accounts", id, "mark"]) => {
                let ids = c.body["ids"]
                    .as_array()
                    .filter(|v| !v.is_empty() && v.len() <= 500)
                    .ok_or_else(|| ApiError::invalid("Выберите до 500 писем"))?
                    .iter()
                    .map(|v| path_id(v.as_str().unwrap_or("")))
                    .collect::<Result<Vec<_>>>()?;
                mail_ops::mark(
                    c.host,
                    c.actor.id,
                    path_id(id)?,
                    &ids,
                    c.body["seen"].as_bool().unwrap_or(true),
                )
                .await?;
                json!({"ok":true})
            }
            ("GET", ["api", "mail", "messages"]) => mail_data::messages(c).await?,
            ("GET", ["api", "mail", "messages", id]) => {
                mail_ops::read(c.host, c.actor.id, path_id(id)?).await?
            }
            ("PATCH", ["api", "mail", "messages", id]) => {
                let id = path_id(id)?;
                let m = mail_data::message(c.host, c.actor.id, id).await?;
                mail_ops::mark(
                    c.host,
                    c.actor.id,
                    db::id(&m, "account_id")?,
                    &[id],
                    c.body["seen"].as_bool().unwrap_or(true),
                )
                .await?;
                json!({"ok":true})
            }
            ("POST", ["api", "mail", "messages", id, "move"]) => {
                mail_ops::move_message(c, path_id(id)?).await?
            }
            ("GET", ["api", "mail", "messages", id, "attachments", index]) => {
                let (name, bytes) = mail_ops::attachment(
                    c.host,
                    c.actor.id,
                    path_id(id)?,
                    index
                        .parse()
                        .map_err(|_| ApiError::invalid("Некорректный номер вложения"))?,
                )
                .await?;
                let filename = format!(
                    "attachment; filename*=UTF-8''{}",
                    percent_encoding::utf8_percent_encode(
                        &name,
                        percent_encoding::NON_ALPHANUMERIC
                    )
                );
                return Ok(Some(
                    (
                        [
                            (header::CONTENT_TYPE, "application/octet-stream"),
                            (header::CONTENT_DISPOSITION, filename.as_str()),
                        ],
                        bytes,
                    )
                        .into_response(),
                ));
            }
            ("GET", ["api", "mail", "messages", id, "images", index]) => {
                let (kind, bytes) = mail_ops::image(
                    c.host,
                    c.actor.id,
                    path_id(id)?,
                    index
                        .parse()
                        .map_err(|_| ApiError::invalid("Некорректный номер изображения"))?,
                )
                .await?;
                return Ok(Some(
                    (
                        [
                            (header::CONTENT_TYPE, kind.as_str()),
                            (
                                header::CONTENT_SECURITY_POLICY,
                                "sandbox; default-src 'none'",
                            ),
                        ],
                        bytes,
                    )
                        .into_response(),
                ));
            }
            ("POST", ["api", "mail", "send"]) => mail_ops::send(c).await?,
            _ => return Ok(None),
        };
        Ok(Some(json_response(value)))
    };
    tokio::time::timeout(Duration::from_secs(45),run).await.map_err(|_|ApiError(StatusCode::GATEWAY_TIMEOUT,"Почтовая операция превысила 45 секунд и остановлена. Для отправки проверьте папку отправленных перед повтором.".into()))?
}
pub async fn run_scheduler(host: Arc<Host>) {
    let mut workers = tokio::task::JoinSet::new();
    loop {
        while workers.try_join_next().is_some() {}
        if let Ok(mut tx) = db::transaction(&host.pool, None, true).await {
            let accounts=sqlx::query_scalar::<_,Value>("SELECT jsonb_build_object('id',a.id,'employee_id',a.employee_id,'settings',a.settings,'folders',a.folders) FROM mail_accounts a JOIN employees e ON e.id=a.employee_id WHERE a.enabled AND e.active AND e.approved ORDER BY a.last_sync_at NULLS FIRST").fetch_all(&mut *tx).await;
            let _ = tx.commit().await;
            if let Ok(accounts) = accounts {
                let active: Vec<_> = accounts
                    .iter()
                    .filter_map(|a| db::id(a, "id").ok())
                    .collect();
                host.mail
                    .due
                    .lock()
                    .await
                    .retain(|id, _| active.contains(id));
                host.mail
                    .gates
                    .lock()
                    .await
                    .retain(|id, gate| active.contains(id) || Arc::strong_count(gate) > 1);
                for a in accounts {
                    if workers.len() >= 3 {
                        break;
                    }
                    let (Ok(id), Ok(owner)) = (db::id(&a, "id"), db::id(&a, "employee_id")) else {
                        continue;
                    };
                    let mut due = host.mail.due.lock().await;
                    if due.get(&id).is_some_and(|t| *t > Instant::now()) {
                        continue;
                    }
                    due.insert(id, Instant::now() + Duration::from_secs(60));
                    drop(due);
                    let host = host.clone();
                    workers.spawn(async move{
      let mut selected=a["settings"]["sync_folders"].as_array().cloned().unwrap_or_else(||vec![json!("INBOX")]);
      for folder in a["folders"].as_array().into_iter().flatten().filter(|f|f["sent"]==true){if !selected.contains(&folder["id"]){selected.push(folder["id"].clone())}}
      let started=Instant::now();for folder in selected.iter().filter_map(Value::as_str).take(100){if started.elapsed()>Duration::from_secs(120){break}let result=tokio::time::timeout(Duration::from_secs(45),mail_ops::sync(&host,owner,id,folder,None)).await;
       let message=match result{Ok(Ok(_))=>continue,Ok(Err(e))if e.0==StatusCode::CONFLICT=>break,Ok(Err(e))=>e.1,Err(_)=>"Проверка ящика остановлена по таймауту; повтор через минуту".into()};
       if let Ok(mut tx)=db::transaction(&host.pool,None,true).await{let _=sqlx::query("UPDATE mail_accounts SET last_error=$1 WHERE id=$2 AND employee_id=$3").bind(message).bind(id).bind(owner).execute(&mut *tx).await;let _=tx.commit().await;}break;
      }
     });
                }
            }
        }
        tokio::time::sleep(Duration::from_secs(2)).await;
    }
}
