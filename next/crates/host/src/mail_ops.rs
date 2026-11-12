//! Mail operations keep credentials and provider sockets outside the WebView.
//! Every cached lookup explicitly includes the mailbox owner, even for directors.
use crate::{
    Host, RequestContext, db,
    error::{ApiError, Result},
    mail::Account,
    mail_content, mail_data, mail_folders,
    mail_transport::{self, Socket, quoted},
    secrets,
};
use axum::http::StatusCode;
use base64::{Engine as _, engine::general_purpose::STANDARD};
use regex::Regex;
use serde_json::{Value, json};
use std::{
    collections::{HashMap, HashSet},
    sync::OnceLock,
    time::{Duration, Instant},
};
use uuid::Uuid;
const MAX_MESSAGE: usize = 15 * 1024 * 1024;
fn strings(value: &Value) -> Vec<String> {
    value
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_owned)
        .collect()
}
fn mail_error(message: &str) -> ApiError {
    mail_transport::error(message)
}
fn offline(host: &Host) -> Result<()> {
    if host.config.source_access {
        Ok(())
    } else {
        Err(ApiError(
            StatusCode::SERVICE_UNAVAILABLE,
            "Внешние подключения отключены на этом хосте".into(),
        ))
    }
}
fn email(value: &str) -> Result<String> {
    static EMAIL: OnceLock<Regex> = OnceLock::new();
    let value = value.trim().to_lowercase();
    if value.len() > 254
        || !value.is_ascii()
        || !EMAIL
            .get_or_init(|| {
                Regex::new(r#"^[^\s@<>(),;:\\\"]+@[^\s@<>(),;:\\\"]+\.[^\s@<>(),;:\\\"]+$"#)
                    .unwrap()
            })
            .is_match(&value)
    {
        return Err(ApiError::invalid("Проверьте адрес электронной почты"));
    }
    Ok(value)
}
fn credential(value: &Value, key: &str, max: usize) -> Result<String> {
    let value = value[key]
        .as_str()
        .filter(|v| !v.is_empty() && v.chars().count() <= max && !v.chars().any(char::is_control))
        .ok_or_else(|| ApiError::invalid("Проверьте логин и пароль приложения"))?;
    Ok(value.to_owned())
}
fn hostname(value: &Value, key: &str, default: &str) -> Result<String> {
    let name = value[key]
        .as_str()
        .unwrap_or(default)
        .trim()
        .to_ascii_lowercase();
    if name.is_empty()
        || name.len() > 253
        || name.split('.').any(|label| {
            label.is_empty()
                || label.len() > 63
                || label.starts_with('-')
                || label.ends_with('-')
                || !label
                    .bytes()
                    .all(|c| c.is_ascii_alphanumeric() || c == b'-')
        })
    {
        return Err(ApiError::invalid(
            "Укажите имя почтового сервера без протокола и пути",
        ));
    }
    Ok(name)
}
async fn connection(host: &Host, account: &Account) -> Result<Socket> {
    offline(host)?;
    let password = secrets::mail_password(
        &host.config,
        account.owner()?,
        account.id()?,
        &account.secret,
    )?;
    let port = account.info["imap_port"]
        .as_u64()
        .filter(|p| *p == 993)
        .ok_or_else(|| {
            ApiError::invalid("Поддерживается защищённое IMAP-подключение на порту 993")
        })?;
    let mut socket = Socket::connect(account.text("imap_host"), port as u16).await?;
    socket.imap_login(account.text("login"), &password).await?;
    Ok(socket)
}
fn ensure_folder(account: &Account, folder: &str) -> Result<()> {
    if folder.len() > 512
        || folder.chars().any(char::is_control)
        || !account.info["folders"]
            .as_array()
            .into_iter()
            .flatten()
            .any(|f| f["id"] == folder && f["selectable"] != false)
    {
        return Err(ApiError::invalid(
            "Папка недоступна. Обновите список папок.",
        ));
    }
    Ok(())
}
pub async fn connect(c: &RequestContext<'_>) -> Result<Value> {
    offline(c.host)?;
    let owner = c.actor.id;
    let id = Uuid::new_v4();
    let email = email(c.body["email"].as_str().unwrap_or(""))?;
    let login = credential(&c.body, "login", 254)?;
    let password = credential(&c.body, "password", 512)?;
    let imap = hostname(&c.body, "imap_host", "imap.yandex.ru")?;
    let smtp = hostname(&c.body, "smtp_host", "smtp.yandex.ru")?;
    if c.body.get("imap_port").is_some_and(|p| p != 993)
        || c.body.get("smtp_port").is_some_and(|p| p != 465)
    {
        return Err(ApiError::invalid("IMAP: порт 993, SMTP: порт 465 с TLS"));
    }
    let label = db::text(&c.body, "label", 100, false)?;
    let secret = secrets::seal_mail_password(&c.host.config, owner, id, &password)?;
    let account = Account {
        info: json!({"id":id,"employee_id":owner,"email":email,"login":login,"imap_host":imap,"imap_port":993}),
        secret,
    };
    let _guard = c.host.mail.guard(id).await?;
    let mut socket = connection(c.host, &account).await?;
    let folders = mail_folders::list(&mut socket).await?;
    let mut tx = db::transaction(&c.host.pool, Some(c.actor), false).await?;
    sqlx::query("SELECT pg_advisory_xact_lock(hashtextextended($1,73142003))")
        .bind(owner.to_string())
        .execute(&mut *tx)
        .await?;
    let count: i64 = sqlx::query_scalar("SELECT count(*) FROM mail_accounts WHERE employee_id=$1")
        .bind(owner)
        .fetch_one(&mut *tx)
        .await?;
    if count >= 10 {
        return Err(ApiError::invalid("Можно подключить до 10 ящиков"));
    }
    sqlx::query("INSERT INTO mail_accounts(id,employee_id,email,login,label,imap_host,imap_port,smtp_host,smtp_port,secret,folders) VALUES($1,$2,$3,$4,$5,$6,993,$7,465,$8,$9)").bind(id).bind(owner).bind(&email).bind(login).bind(label).bind(imap).bind(smtp).bind(&account.secret).bind(json!(folders)).execute(&mut *tx).await?;
    db::audit(
        &mut tx,
        owner,
        "mail.connect",
        &id.to_string(),
        json!({"email":email}),
        c.ip,
    )
    .await?;
    tx.commit().await?;
    Ok(json!({"id":id,"connected":true}))
}
pub async fn settings(c: &RequestContext<'_>, id: Uuid) -> Result<Value> {
    let old = mail_data::account(c.host, c.actor.id, id).await?;
    let _guard = c.host.mail.guard(id).await?;
    let mut updated = old.info.clone();
    let mut changed = false;
    for key in ["login", "imap_host", "smtp_host", "label"] {
        if c.body.get(key).is_some() {
            let value = match key {
                "login" => credential(&c.body, key, 254)?,
                "label" => db::text(&c.body, key, 100, false)?,
                _ => hostname(&c.body, key, old.text(key))?,
            };
            changed |= key != "label" && updated[key] != value;
            updated[key] = value.into();
        }
    }
    let mut secret = old.secret.clone();
    if c.body["password"].as_str().is_some_and(|v| !v.is_empty()) {
        let password = credential(&c.body, "password", 512)?;
        secret = secrets::seal_mail_password(&c.host.config, c.actor.id, id, &password)?;
        changed = true;
    }
    let mut account = Account {
        info: updated,
        secret,
    };
    if changed {
        let mut socket = connection(c.host, &account).await?;
        account.info["folders"] = json!(mail_folders::list(&mut socket).await?);
    }
    let mut settings = old.info["settings"]
        .as_object()
        .cloned()
        .unwrap_or_default();
    for key in ["read_mode", "signature", "notifications", "sync_folders"] {
        if let Some(v) = c.body.get(key) {
            match key {
                "read_mode" => {
                    if !["auto", "text", "html"].contains(&v.as_str().unwrap_or("")) {
                        return Err(ApiError::invalid("Проверьте режим отображения письма"));
                    }
                }
                "signature" => {
                    if v.as_str().is_none_or(|v| v.chars().count() > 10000) {
                        return Err(ApiError::invalid("Подпись: до 10 000 символов"));
                    }
                }
                "notifications" => {
                    if !v.is_boolean() {
                        return Err(ApiError::invalid("Проверьте уведомления"));
                    }
                }
                "sync_folders" => {
                    let values = v
                        .as_array()
                        .filter(|a| !a.is_empty() && a.len() <= 100)
                        .ok_or_else(|| ApiError::invalid("Выберите от 1 до 100 папок"))?;
                    for folder in values {
                        ensure_folder(
                            &account,
                            folder
                                .as_str()
                                .ok_or_else(|| ApiError::invalid("Проверьте название папки"))?,
                        )?;
                    }
                }
                _ => {}
            }
            settings.insert(key.into(), v.clone());
        }
    }
    let reset = account.text("imap_host") != old.text("imap_host")
        || account.text("login") != old.text("login");
    let mut tx = db::transaction(&c.host.pool, Some(c.actor), false).await?;
    sqlx::query_scalar::<_,Uuid>("UPDATE mail_accounts SET label=$1,login=$2,imap_host=$3,smtp_host=$4,secret=$5,settings=$6,folders=$7,last_error='' WHERE id=$8 AND employee_id=$9 RETURNING id").bind(account.text("label")).bind(account.text("login")).bind(account.text("imap_host")).bind(account.text("smtp_host")).bind(&account.secret).bind(json!(settings)).bind(&account.info["folders"]).bind(id).bind(c.actor.id).fetch_one(&mut *tx).await?;
    if reset {
        sqlx::query("DELETE FROM mail_messages WHERE account_id=$1 AND employee_id=$2")
            .bind(id)
            .bind(c.actor.id)
            .execute(&mut *tx)
            .await?;
        sqlx::query("UPDATE mail_accounts SET sync_state='{}',last_sync_at=NULL WHERE id=$1 AND employee_id=$2").bind(id).bind(c.actor.id).execute(&mut *tx).await?;
    }
    db::audit(
        &mut tx,
        c.actor.id,
        "mail.settings",
        &id.to_string(),
        json!({"credentials_changed":changed}),
        c.ip,
    )
    .await?;
    tx.commit().await?;
    Ok(json!({"saved":true,"settings":settings}))
}
pub fn search_uids(rows: &[mail_transport::Part]) -> Result<Vec<i64>> {
    let mut uids = vec![];
    let mut complete = false;
    for part in rows {
        if let Some(values) = part.line.strip_prefix("* SEARCH") {
            complete = true;
            for s in values.split_whitespace() {
                let id = s
                    .parse::<u32>()
                    .map_err(|_| mail_error("IMAP вернул некорректный список писем"))?;
                if id == 0 {
                    return Err(mail_error("IMAP вернул некорректный UID"));
                }
                uids.push(i64::from(id));
                if uids.len() > 200_000 {
                    return Err(mail_error(
                        "В папке больше 200 000 писем. Разделите её у провайдера.",
                    ));
                }
            }
        }
    }
    if !complete {
        return Err(mail_error(
            "Сервер не подтвердил список писем; сохранённый кэш оставлен без изменений",
        ));
    }
    uids.sort_unstable();
    uids.dedup();
    Ok(uids)
}
fn uid(metadata: &str) -> Option<i64> {
    static UID: OnceLock<Regex> = OnceLock::new();
    UID.get_or_init(|| Regex::new(r"(?i)\bUID\s+(\d+)").unwrap())
        .captures(metadata)
        .and_then(|c| c[1].parse::<u32>().ok())
        .filter(|v| *v > 0)
        .map(i64::from)
}
fn time(value: &Value) -> Option<chrono::DateTime<chrono::Utc>> {
    value
        .as_str()
        .and_then(|s| chrono::DateTime::parse_from_rfc3339(s).ok())
        .map(|v| v.with_timezone(&chrono::Utc))
}
pub async fn sync(
    host: &Host,
    owner: Uuid,
    id: Uuid,
    folder: &str,
    before: Option<i64>,
) -> Result<Value> {
    let account = mail_data::account(host, owner, id).await?;
    let _guard = host.mail.guard(id).await?;
    if before.is_some_and(|v| v < 1) {
        return Err(ApiError::invalid("Некорректная страница писем"));
    }
    let mut socket = connection(host, &account).await?;
    let folders = mail_folders::list(&mut socket).await?;
    let fresh = Account {
        info: json!({"folders":folders}),
        secret: vec![],
    };
    ensure_folder(&fresh, folder)?;
    let validity = mail_folders::select(&mut socket, folder, true).await?;
    let uids = search_uids(&socket.command("UID SEARCH ALL", 2 * 1024 * 1024).await?)?;
    let batch: Vec<_> = uids
        .iter()
        .rev()
        .copied()
        .filter(|u| before.is_none_or(|b| *u < b))
        .take(100)
        .collect();
    let mut fetched = vec![];
    if !batch.is_empty() {
        let set = batch
            .iter()
            .map(i64::to_string)
            .collect::<Vec<_>>()
            .join(",");
        let rows=socket.command(&format!("UID FETCH {set} (UID FLAGS RFC822.SIZE BODY.PEEK[HEADER.FIELDS (SUBJECT FROM TO CC DATE MESSAGE-ID IN-REPLY-TO REFERENCES)])"),4*1024*1024).await?;
        for part in rows {
            if let (Some(raw), Some(uid)) = (part.literal, uid(&part.line))
                && batch.contains(&uid)
            {
                fetched.push((uid, mail_content::header(&raw, &part.line)?));
            }
        }
    }
    drop(socket);
    let previous = &account.info["sync_state"][folder];
    let initial = previous["uidvalidity"] != validity;
    let highest = if initial {
        0
    } else {
        previous["last_uid"].as_i64().unwrap_or(0)
    };
    let notify = !initial
        && account.info["settings"]["notifications"] != false
        && !folders
            .iter()
            .any(|f| f["id"] == folder && f["quiet"] == true);
    let mut tx = db::transaction(&host.pool, None, true).await?;
    // Keep the account alive throughout cache writes and check ownership again.
    sqlx::query_scalar::<_, Uuid>(
        "SELECT id FROM mail_accounts WHERE id=$1 AND employee_id=$2 FOR UPDATE",
    )
    .bind(id)
    .bind(owner)
    .fetch_one(&mut *tx)
    .await?;
    sqlx::query("DELETE FROM mail_messages WHERE account_id=$1 AND employee_id=$2 AND folder=$3 AND (uidvalidity<>$4 OR NOT(uid=ANY($5)))").bind(id).bind(owner).bind(folder).bind(&validity).bind(&uids).execute(&mut *tx).await?;
    for (uid, item) in &fetched {
        let mid:Uuid=sqlx::query_scalar("INSERT INTO mail_messages(id,employee_id,account_id,folder,uidvalidity,uid,subject,sender,recipients,addresses,sent_at,seen,size_bytes,message_id,in_reply_to,reference_ids) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16) ON CONFLICT(account_id,folder,uidvalidity,uid) DO UPDATE SET seen=excluded.seen,in_reply_to=excluded.in_reply_to,reference_ids=excluded.reference_ids RETURNING id")
  .bind(Uuid::new_v4()).bind(owner).bind(id).bind(folder).bind(&validity).bind(uid).bind(item["subject"].as_str().unwrap_or("")).bind(item["sender"].as_str().unwrap_or("")).bind(&item["recipients"]).bind(strings(&item["addresses"])).bind(time(&item["sent_at"])).bind(item["seen"].as_bool().unwrap_or(false)).bind(item["size_bytes"].as_i64().unwrap_or(0)).bind(item["message_id"].as_str().unwrap_or("")).bind(item["in_reply_to"].as_str().unwrap_or("")).bind(strings(&item["reference_ids"])).fetch_one(&mut *tx).await?;
        if notify && *uid > highest && item["seen"] == false {
            sqlx::query("INSERT INTO notifications(id,employee_id,kind,title,description,entity_id,dedupe_key) VALUES($1,$2,'mail.new',$3,$4,$5,$6) ON CONFLICT(employee_id,dedupe_key) DO NOTHING").bind(Uuid::new_v4()).bind(owner).bind(item["subject"].as_str().unwrap_or("Новое письмо")).bind(item["sender"].as_str().unwrap_or("")).bind(mid.to_string()).bind(format!("mail:{id}:{folder}:{validity}:{uid}")).execute(&mut *tx).await?;
        }
    }
    let mut state = account.info["sync_state"]
        .as_object()
        .cloned()
        .unwrap_or_default();
    state.insert(
        folder.to_owned(),
        json!({"uidvalidity":validity,"last_uid":uids.last().copied().unwrap_or(highest)}),
    );
    sqlx::query("UPDATE mail_accounts SET folders=$1,sync_state=$2,last_sync_at=now(),last_error='' WHERE id=$3 AND employee_id=$4").bind(json!(folders)).bind(json!(state)).bind(id).bind(owner).execute(&mut *tx).await?;
    tx.commit().await?;
    Ok(json!({"loaded":fetched.len(),"folder":folder,"initial":initial}))
}
pub async fn create_folder(c: &RequestContext<'_>, id: Uuid) -> Result<Value> {
    let account = mail_data::account(c.host, c.actor.id, id).await?;
    let _guard = c.host.mail.guard(id).await?;
    let mut socket = connection(c.host, &account).await?;
    let folders = mail_folders::list(&mut socket).await?;
    let name = db::text(&c.body, "name", 100, true)?;
    let parent = db::text(&c.body, "parent", 512, false)?;
    let raw = mail_folders::child(&name, &parent, &folders)?;
    if folders.iter().any(|f| {
        f["id"]
            .as_str()
            .is_some_and(|s| s.eq_ignore_ascii_case(&raw))
    }) {
        return Err(ApiError::conflict("Такая папка уже есть"));
    }
    socket
        .command(&format!("CREATE {}", quoted(&raw)?), 256 * 1024)
        .await?;
    let _ = socket
        .command(&format!("SUBSCRIBE {}", quoted(&raw)?), 256 * 1024)
        .await;
    let folders = mail_folders::list(&mut socket).await?;
    let mut tx = db::transaction(&c.host.pool, Some(c.actor), false).await?;
    sqlx::query("UPDATE mail_accounts SET folders=$1 WHERE id=$2 AND employee_id=$3")
        .bind(json!(folders))
        .bind(id)
        .bind(c.actor.id)
        .execute(&mut *tx)
        .await?;
    db::audit(
        &mut tx,
        c.actor.id,
        "mail.folder_create",
        &id.to_string(),
        json!({"name":name}),
        c.ip,
    )
    .await?;
    tx.commit().await?;
    Ok(json!({"id":raw,"folders":folders}))
}
async fn raw_message(host: &Host, account: &Account, row: &Value) -> Result<Vec<u8>> {
    if row["size_bytes"]
        .as_u64()
        .is_some_and(|v| v > MAX_MESSAGE as u64)
    {
        return Err(ApiError::invalid(
            "Письмо больше 15 МБ. Откройте его у провайдера.",
        ));
    }
    let mut socket = connection(host, account).await?;
    if mail_folders::select(&mut socket, row["folder"].as_str().unwrap_or(""), true).await?
        != row["uidvalidity"].as_str().unwrap_or("")
    {
        return Err(ApiError::conflict("Папка изменилась. Обновите почту."));
    }
    let uid = row["uid"]
        .as_i64()
        .filter(|v| *v > 0)
        .ok_or_else(ApiError::missing)?;
    socket
        .command(
            &format!("UID FETCH {uid} (UID BODY.PEEK[])"),
            MAX_MESSAGE + 64 * 1024,
        )
        .await?
        .into_iter()
        .find_map(|part| {
            if self::uid(&part.line) == Some(uid) {
                part.literal
            } else {
                None
            }
        })
        .filter(|raw| raw.len() <= MAX_MESSAGE)
        .ok_or_else(ApiError::missing)
}
pub async fn read(host: &Host, owner: Uuid, id: Uuid) -> Result<Value> {
    let mut row = mail_data::message(host, owner, id).await?;
    let aid = db::id(&row, "account_id")?;
    let account = mail_data::account(host, owner, aid).await?;
    let mut warning = String::new();
    if row["body_text"].is_null() || row["content_version"].as_i64().unwrap_or(0) < 2 {
        let load = async {
            let _guard = host.mail.guard(aid).await?;
            let raw = raw_message(host, &account, &row).await?;
            tokio::task::spawn_blocking(move || mail_content::rich(&raw))
                .await
                .map_err(|_| mail_error("Разбор письма прерван"))?
        };
        match tokio::time::timeout(Duration::from_secs(25), load).await {
            Ok(Ok(content)) => {
                let mut tx = db::transaction(&host.pool, None, true).await?;
                sqlx::query("UPDATE mail_messages SET body_text=$1,body_html=$2,attachments=$3,content_version=3,in_reply_to=$4,reference_ids=$5,message_id=$6 WHERE id=$7 AND employee_id=$8").bind(content["body_text"].as_str().unwrap_or("")).bind(content["body_html"].as_str().unwrap_or("")).bind(&content["attachments"]).bind(content["in_reply_to"].as_str().unwrap_or("")).bind(strings(&content["reference_ids"])).bind(content["message_id"].as_str().unwrap_or("")).bind(id).bind(owner).execute(&mut *tx).await?;
                tx.commit().await?;
                for key in [
                    "body_text",
                    "body_html",
                    "attachments",
                    "in_reply_to",
                    "reference_ids",
                    "message_id",
                ] {
                    row[key] = content[key].clone()
                }
                row["content_version"] = 3.into();
            }
            result => {
                if row["body_text"].is_null() {
                    return Err(match result {
                        Ok(Err(e)) => e,
                        _ => mail_error("Загрузка письма остановлена по таймауту"),
                    });
                }
                warning = "Сервер почты недоступен. Показана сохранённая копия.".into()
            }
        }
    }
    if row["seen"] != true {
        match tokio::time::timeout(Duration::from_secs(10), mark(host, owner, aid, &[id], true))
            .await
        {
            Ok(Ok(())) => row["seen"] = true.into(),
            _ => {
                if warning.is_empty() {
                    warning =
                        "Письмо открыто; отметка прочтения пока не подтверждена сервером.".into()
                }
            }
        }
    }
    Ok(mail_data::response(row, &account, &warning))
}
pub async fn mark(host: &Host, owner: Uuid, aid: Uuid, ids: &[Uuid], seen: bool) -> Result<()> {
    let account = mail_data::account(host, owner, aid).await?;
    let _guard = host.mail.guard(aid).await?;
    let ids: Vec<_> = ids
        .iter()
        .copied()
        .collect::<HashSet<_>>()
        .into_iter()
        .collect();
    if ids.is_empty() || ids.len() > 500 {
        return Err(ApiError::invalid("Выберите до 500 писем"));
    }
    let mut tx = db::transaction(&host.pool, None, true).await?;
    let rows:Vec<Value>=sqlx::query_scalar("SELECT to_jsonb(m) FROM mail_messages m WHERE id=ANY($1) AND employee_id=$2 AND account_id=$3").bind(&ids).bind(owner).bind(aid).fetch_all(&mut *tx).await?;
    tx.commit().await?;
    if rows.len() != ids.len() {
        return Err(ApiError::missing());
    }
    let mut grouped: HashMap<String, Vec<Value>> = HashMap::new();
    for row in rows {
        grouped
            .entry(row["folder"].as_str().unwrap_or("").to_owned())
            .or_default()
            .push(row)
    }
    let mut socket = connection(host, &account).await?;
    for (folder, rows) in grouped {
        let validity = mail_folders::select(&mut socket, &folder, false).await?;
        if rows.iter().any(|r| r["uidvalidity"] != validity) {
            return Err(ApiError::conflict("Папка изменилась. Обновите почту."));
        }
        let set = rows
            .iter()
            .map(|r| r["uid"].as_i64().unwrap_or(0).to_string())
            .collect::<Vec<_>>()
            .join(",");
        socket
            .command(
                &format!(
                    "UID STORE {set} {}FLAGS.SILENT (\\Seen)",
                    if seen { "+" } else { "-" }
                ),
                1024 * 1024,
            )
            .await?;
        let selected: Vec<_> = rows.iter().filter_map(|r| db::id(r, "id").ok()).collect();
        let mut tx = db::transaction(&host.pool, None, true).await?;
        sqlx::query("UPDATE mail_messages SET seen=$1 WHERE id=ANY($2) AND employee_id=$3 AND account_id=$4").bind(seen).bind(&selected).bind(owner).bind(aid).execute(&mut *tx).await?;
        if seen {
            let ids: Vec<_> = selected.iter().map(Uuid::to_string).collect();
            sqlx::query("UPDATE notifications SET read_at=COALESCE(read_at,now()) WHERE employee_id=$1 AND kind='mail.new' AND entity_id=ANY($2)").bind(owner).bind(ids).execute(&mut *tx).await?;
        }
        tx.commit().await?;
    }
    Ok(())
}
pub async fn move_message(c: &RequestContext<'_>, id: Uuid) -> Result<Value> {
    let row = mail_data::message(c.host, c.actor.id, id).await?;
    let aid = db::id(&row, "account_id")?;
    let account = mail_data::account(c.host, c.actor.id, aid).await?;
    let folder = db::text(&c.body, "folder", 512, true)?;
    ensure_folder(&account, &folder)?;
    if row["folder"] == folder {
        return Ok(json!({"moved":false}));
    }
    let _guard = c.host.mail.guard(aid).await?;
    let mut socket = connection(c.host, &account).await?;
    let caps = socket.command("CAPABILITY", 256 * 1024).await?;
    if !caps.iter().any(|p| {
        p.line
            .split_whitespace()
            .any(|s| s.eq_ignore_ascii_case("MOVE") || s.eq_ignore_ascii_case("IMAP4rev2"))
    }) {
        return Err(ApiError::invalid(
            "Сервер не поддерживает безопасное перемещение писем",
        ));
    }
    if mail_folders::select(&mut socket, row["folder"].as_str().unwrap_or(""), false).await?
        != row["uidvalidity"].as_str().unwrap_or("")
    {
        return Err(ApiError::conflict("Папка изменилась. Обновите почту."));
    }
    let uid = row["uid"]
        .as_i64()
        .filter(|v| *v > 0)
        .ok_or_else(ApiError::missing)?;
    socket
        .command(&format!("UID MOVE {uid} {}", quoted(&folder)?), 256 * 1024)
        .await?;
    let mut tx = db::transaction(&c.host.pool, Some(c.actor), false).await?;
    sqlx::query("DELETE FROM mail_messages WHERE id=$1 AND employee_id=$2")
        .bind(id)
        .bind(c.actor.id)
        .execute(&mut *tx)
        .await?;
    sqlx::query("UPDATE notifications SET read_at=COALESCE(read_at,now()) WHERE employee_id=$1 AND kind='mail.new' AND entity_id=$2").bind(c.actor.id).bind(id.to_string()).execute(&mut *tx).await?;
    tx.commit().await?;
    Ok(json!({"moved":true}))
}
pub async fn attachment(
    host: &Host,
    owner: Uuid,
    id: Uuid,
    index: usize,
) -> Result<(String, Vec<u8>)> {
    let row = mail_data::message(host, owner, id).await?;
    let account = mail_data::account(host, owner, db::id(&row, "account_id")?).await?;
    let _guard = host.mail.guard(account.id()?).await?;
    let old_name = if row["content_version"].as_i64().unwrap_or(0) < 3 {
        row["attachments"]
            .as_array()
            .into_iter()
            .flatten()
            .find(|a| a["index"].as_u64() == Some(index as u64))
            .and_then(|a| a["name"].as_str())
            .map(str::to_owned)
    } else {
        None
    };
    let raw = raw_message(host, &account, &row).await?;
    tokio::task::spawn_blocking(move || mail_content::attachment(&raw, index, old_name.as_deref()))
        .await
        .map_err(|_| mail_error("Разбор вложения прерван"))?
}
pub async fn image(host: &Host, owner: Uuid, id: Uuid, index: usize) -> Result<(String, Vec<u8>)> {
    let row = mail_data::message(host, owner, id).await?;
    let urls = mail_content::image_urls(row["body_html"].as_str().unwrap_or(""));
    let url = urls.get(index).ok_or_else(ApiError::missing)?;
    let key = format!("{owner}:{url}");
    {
        let mut cache = host.mail.images.lock().await;
        cache.retain(|_, (at, _, _)| at.elapsed() < Duration::from_secs(600));
        if let Some((_, kind, bytes)) = cache.get(&key) {
            return Ok((kind.clone(), bytes.clone()));
        }
    }
    offline(host)?;
    let _slot = tokio::time::timeout(Duration::from_secs(3), host.mail.image_slots.acquire())
        .await
        .map_err(|_| mail_error("Изображения загружаются. Повторите позже."))?
        .map_err(|_| mail_error("Модуль изображений остановлен"))?;
    let result = tokio::time::timeout(Duration::from_secs(15), download_image(url))
        .await
        .map_err(|_| mail_error("Истекло время загрузки изображения"))??;
    let mut cache = host.mail.images.lock().await;
    while cache.values().map(|(_, _, v)| v.len()).sum::<usize>() + result.1.len() > 32 * 1024 * 1024
    {
        if let Some(old) = cache
            .iter()
            .min_by_key(|(_, v)| v.0)
            .map(|(k, _)| k.clone())
        {
            cache.remove(&old);
        } else {
            break;
        }
    }
    cache.insert(key, (Instant::now(), result.0.clone(), result.1.clone()));
    Ok(result)
}
async fn download_image(url: &str) -> Result<(String, Vec<u8>)> {
    let mut url = mail_content::remote_url(url)
        .ok_or_else(|| ApiError::invalid("Недопустимый адрес изображения"))?;
    for _ in 0..4 {
        let parsed = url::Url::parse(&url)
            .map_err(|_| ApiError::invalid("Некорректный адрес изображения"))?;
        let host = parsed.host_str().ok_or_else(ApiError::missing)?;
        let addresses =
            mail_transport::addresses(host, parsed.port_or_known_default().unwrap_or(443)).await?;
        let client = reqwest::Client::builder()
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .connect_timeout(Duration::from_secs(4))
            .timeout(Duration::from_secs(10))
            .resolve_to_addrs(host, &addresses)
            .build()
            .map_err(|_| mail_error("Не удалось открыть изображение"))?;
        let mut response = client
            .get(parsed.clone())
            .header(
                reqwest::header::ACCEPT,
                "image/png,image/jpeg,image/gif,image/webp",
            )
            .send()
            .await
            .map_err(|_| mail_error("Изображение временно недоступно"))?;
        if response.status().is_redirection() {
            let next = response
                .headers()
                .get(reqwest::header::LOCATION)
                .and_then(|h| h.to_str().ok())
                .and_then(|loc| parsed.join(loc).ok())
                .and_then(|u| mail_content::remote_url(u.as_str()))
                .ok_or_else(|| mail_error("Недопустимое перенаправление изображения"))?;
            url = next;
            continue;
        }
        if !response.status().is_success()
            || response
                .content_length()
                .is_some_and(|n| n > 2 * 1024 * 1024)
        {
            return Err(mail_error("Изображение недоступно или больше 2 МБ"));
        }
        let mut bytes = vec![];
        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(|_| mail_error("Изображение загружено не полностью"))?
        {
            if bytes.len() + chunk.len() > 2 * 1024 * 1024 {
                return Err(mail_error("Изображение больше 2 МБ"));
            }
            bytes.extend_from_slice(&chunk)
        }
        let kind = mail_content::raster(&bytes)
            .ok_or_else(|| mail_error("Разрешены только PNG, JPEG, GIF и WebP"))?;
        return Ok((kind.to_owned(), bytes));
    }
    Err(mail_error("Слишком много перенаправлений изображения"))
}
pub fn compose(
    from: &str,
    to: &[String],
    cc: &[String],
    subject: &str,
    text: &str,
    parent: &str,
    refs: &[String],
) -> Result<Vec<u8>> {
    email(from)?;
    for recipient in to.iter().chain(cc) {
        email(recipient)?;
    }
    if to.is_empty()
        || to.len() > 30
        || cc.len() > 30
        || subject.trim().is_empty()
        || subject.chars().count() > 500
        || subject.chars().any(char::is_control)
        || text.is_empty()
        || text.chars().count() > 200_000
    {
        return Err(ApiError::invalid(
            "Проверьте адресатов, тему и текст письма",
        ));
    }
    let encoded = subject
        .chars()
        .collect::<Vec<_>>()
        .chunks(12)
        .map(|v| {
            format!(
                "=?UTF-8?B?{}?=",
                STANDARD.encode(v.iter().collect::<String>())
            )
        })
        .collect::<Vec<_>>()
        .join("\r\n ");
    let mut message = format!("From: {from}\r\nTo: {}\r\n", to.join(", "));
    if !cc.is_empty() {
        message.push_str(&format!("Cc: {}\r\n", cc.join(", ")))
    }
    message.push_str(&format!(
        "Subject: {encoded}\r\nDate: {}\r\nMessage-ID: <{}@nexsearch.local>\r\n",
        chrono::Utc::now().to_rfc2822(),
        Uuid::new_v4()
    ));
    let parent = mail_content::message_id(parent);
    if !parent.is_empty() {
        let mut ids: Vec<_> = refs
            .iter()
            .map(|s| mail_content::message_id(s))
            .filter(|s| !s.is_empty())
            .collect();
        ids.push(parent.clone());
        let mut unique = HashSet::new();
        ids.retain(|s| unique.insert(s.clone()));
        let last = &ids[ids.len().saturating_sub(50)..];
        message.push_str(&format!(
            "In-Reply-To: {parent}\r\nReferences: {}\r\n",
            last.join("\r\n ")
        ))
    }
    message.push_str("MIME-Version: 1.0\r\nContent-Type: text/plain; charset=UTF-8\r\nContent-Transfer-Encoding: base64\r\n\r\n");
    let normalized = text
        .replace("\r\n", "\n")
        .replace('\r', "\n")
        .replace('\n', "\r\n");
    let body = STANDARD.encode(normalized);
    for chunk in body.as_bytes().chunks(76) {
        message.push_str(std::str::from_utf8(chunk).expect("Base64 is ASCII"));
        message.push_str("\r\n")
    }
    Ok(message.into_bytes())
}
pub async fn send(c: &RequestContext<'_>) -> Result<Value> {
    let owner = c.actor.id;
    let aid = db::id(&c.body, "account_id")?;
    let account = mail_data::account(c.host, owner, aid).await?;
    let _guard = c.host.mail.guard(aid).await?;
    offline(c.host)?;
    let to = strings(&c.body["to"])
        .iter()
        .map(|s| email(s))
        .collect::<Result<Vec<_>>>()?;
    let cc = strings(&c.body["cc"])
        .iter()
        .map(|s| email(s))
        .collect::<Result<Vec<_>>>()?;
    let subject = c.body["subject"].as_str().unwrap_or("");
    let text = c.body["text"].as_str().unwrap_or("");
    let (parent, refs) = if let Some(id) = db::optional_id(&c.body, "reply_to")? {
        let previous = mail_data::message(c.host, owner, id).await?;
        if previous["account_id"] != aid.to_string() {
            return Err(ApiError::invalid(
                "Ответ отправляется из ящика, в который пришло письмо",
            ));
        }
        (
            previous["message_id"].as_str().unwrap_or("").to_owned(),
            strings(&previous["reference_ids"]),
        )
    } else {
        (String::new(), vec![])
    };
    let raw = compose(
        account.text("email"),
        &to,
        &cc,
        subject,
        text,
        &parent,
        &refs,
    )?;
    if account.text("smtp_host").is_empty() || account.info["smtp_port"] != 465 {
        return Err(ApiError::invalid("Настройте SMTP с TLS на порту 465"));
    }
    let password = secrets::mail_password(&c.host.config, owner, aid, &account.secret)?;
    let mut socket = Socket::connect(account.text("smtp_host"), 465).await?;
    if socket.smtp_reply().await? != 220 {
        return Err(mail_error("SMTP не подтвердил готовность"));
    }
    socket.smtp("EHLO nexsearch.local", &[250]).await?;
    socket.smtp("AUTH LOGIN", &[334]).await?;
    socket
        .smtp(&STANDARD.encode(account.text("login")), &[334])
        .await?;
    socket.smtp(&STANDARD.encode(password), &[235]).await?;
    socket
        .smtp(
            &format!("MAIL FROM:<{}>", email(account.text("email"))?),
            &[250],
        )
        .await?;
    let mut accepted = 0;
    let mut refused = 0;
    for target in to.iter().chain(&cc) {
        let code = socket
            .smtp(
                &format!("RCPT TO:<{target}>"),
                &[250, 251, 450, 451, 452, 550, 551, 552, 553, 554],
            )
            .await?;
        if code == 250 || code == 251 {
            accepted += 1
        } else {
            refused += 1
        }
    }
    if accepted == 0 {
        return Err(mail_error(
            "SMTP отклонил всех адресатов. Письмо не отправлено.",
        ));
    }
    socket.smtp("DATA", &[354]).await?;
    socket.write(&raw).await?;
    socket.write(b".\r\n").await?;
    if socket.smtp_reply().await? != 250 {
        return Err(mail_error(
            "SMTP не подтвердил доставку. Проверьте отправленные перед повторной отправкой.",
        ));
    }
    drop(socket);
    // SMTP has accepted delivery. Failures below must never suggest resending.
    let mut warning = if refused > 0 {
        format!(
            "Письмо принято для {accepted} адресатов; {refused} отклонены. Не отправляйте повторно всем."
        )
    } else {
        String::new()
    };
    let saved = async {
        let sent = account.info["folders"]
            .as_array()
            .into_iter()
            .flatten()
            .find(|f| f["sent"] == true)
            .and_then(|f| f["id"].as_str())
            .ok_or_else(ApiError::missing)?;
        let mut socket = connection(c.host, &account).await?;
        socket.append(sent, &raw).await
    };
    if !matches!(
        tokio::time::timeout(Duration::from_secs(8), saved).await,
        Ok(Ok(()))
    ) {
        warning.push_str(" Письмо отправлено; сохранение копии в отправленных не подтверждено.")
    }
    let audited = async {
        let mut tx = db::transaction(&c.host.pool, Some(c.actor), false).await?;
        db::audit(
            &mut tx,
            owner,
            "mail.send",
            &aid.to_string(),
            json!({"recipients":accepted,"refused":refused}),
            c.ip,
        )
        .await?;
        tx.commit().await?;
        Result::<()>::Ok(())
    };
    if !matches!(
        tokio::time::timeout(Duration::from_secs(3), audited).await,
        Ok(Ok(()))
    ) {
        warning.push_str(" Письмо отправлено; запись в журнале пока не подтверждена.")
    }
    c.host.mail.due.lock().await.insert(aid, Instant::now());
    Ok(json!({"sent":true,"warning":warning.trim()}))
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn invalid_search_must_never_erase_cached_mail() {
        assert!(search_uids(&[]).is_err());
        assert!(
            search_uids(&[mail_transport::Part {
                line: "* SEARCH 1 bad\r\n".into(),
                literal: None
            }])
            .is_err()
        );
        assert_eq!(
            search_uids(&[mail_transport::Part {
                line: "* SEARCH 12 1 12\r\n".into(),
                literal: None
            }])
            .unwrap(),
            vec![1, 12]
        );
    }
    #[test]
    fn outgoing_mail_has_folded_safe_headers_and_utf8_body() {
        let raw = compose(
            "a@example.org",
            &["b@example.org".into()],
            &[],
            "Ответ клиенту",
            "Строка\n.\nЕщё",
            "<old@example.org>",
            &[],
        )
        .unwrap();
        let parsed = mail_parser::MessageParser::default().parse(&raw).unwrap();
        assert_eq!(parsed.subject(), Some("Ответ клиенту"));
        assert!(parsed.body_text(0).unwrap().contains("Строка"));
        assert_eq!(parsed.in_reply_to().as_text(), Some("old@example.org"));
        assert!(
            compose(
                "a@example.org",
                &["b@example.org".into()],
                &[],
                "bad\r\nBcc: leak",
                "body",
                "",
                &[]
            )
            .is_err()
        );
    }
}
